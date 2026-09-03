"""Tools that talk to macOS apps.

Every one of these passes user text as an ARGUMENT, never by pasting it into
a script. See ``macos/osa.py`` for why that matters - it is the difference
between a calendar entry and a remote code execution bug.

Everything read back out of an app (event titles, reminder names, clipboard
contents) is treated as untrusted, because it is: anyone who can put text in
your calendar can put text in front of the model.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta
from typing import Any, Dict, List

from .. import paths
from ..errors import ToolError, ValidationError
from ..macos import osa, spotlight
from ..security import injection
from .registry import ExecContext, ToolSpec, register

# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------
_READ_EVENTS = '''
set daysAhead to (item 1 of argv) as integer
set startDate to (current date)
set endDate to startDate + (daysAhead * days)
set out to ""
tell application "Calendar"
  repeat with cal in calendars
    set calName to name of cal
    try
      set matches to (every event of cal whose start date is greater than or equal to startDate and start date is less than or equal to endDate)
      repeat with ev in matches
        set out to out & calName & tab & (summary of ev) & tab & ((start date of ev) as string) & linefeed
      end repeat
    end try
  end repeat
end tell
return out
'''

# Set day to 1 BEFORE changing month: if today is the 31st and you set the
# month to February first, AppleScript rolls the date over into March.
_CREATE_EVENT = '''
set theTitle to item 1 of argv
set y to (item 2 of argv) as integer
set mo to (item 3 of argv) as integer
set d to (item 4 of argv) as integer
set h to (item 5 of argv) as integer
set mi to (item 6 of argv) as integer
set mins to (item 7 of argv) as integer
set calName to item 8 of argv
set theNotes to item 9 of argv

set theDate to (current date)
set day of theDate to 1
set year of theDate to y
set month of theDate to mo
set day of theDate to d
set time of theDate to (h * hours + mi * minutes)

tell application "Calendar"
  if calName is "" then
    set targetCal to first calendar whose writable is true
  else
    set targetCal to first calendar whose name is calName
  end if
  tell targetCal
    make new event with properties {summary:theTitle, start date:theDate, end date:(theDate + (mins * minutes)), description:theNotes}
  end tell
end tell
return "created"
'''

_LIST_CALENDARS = '''
set out to ""
tell application "Calendar"
  repeat with cal in calendars
    set out to out & (name of cal) & linefeed
  end repeat
end tell
return out
'''


def _calendar_read(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    days = int(args.get("days_ahead", 7))
    result = osa.run(_READ_EVENTS, [str(days)], timeout=60, feature="Calendar")
    if not result.ok:
        raise osa.explain_failure(result, "Calendar")

    events: List[Dict[str, str]] = []
    for line in result.output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            events.append({"calendar": parts[0], "title": parts[1], "starts": parts[2]})

    wrapped = injection.wrap(json.dumps(events, indent=2), source="your Calendar",
                             content_id="calendar")
    return {"days_ahead": days, "count": len(events), "events": events,
            "untrusted": wrapped["wrapped"], "flagged": wrapped["flagged"],
            "injection_signals": wrapped["signals"]}


def _calendar_create(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    when = _parse_datetime(args["start"])
    minutes = int(args.get("duration_minutes", 60))
    result = osa.run(_CREATE_EVENT, [
        args["title"], str(when.year), str(when.month), str(when.day),
        str(when.hour), str(when.minute), str(minutes),
        args.get("calendar", "") or "", args.get("notes", "") or "",
    ], timeout=45, feature="Calendar")
    if not result.ok:
        raise osa.explain_failure(result, "Calendar")
    return {"created": True, "title": args["title"],
            "start": when.isoformat(timespec="minutes"),
            "duration_minutes": minutes,
            "calendar": args.get("calendar") or "(your default writable calendar)"}


def _calendar_list(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    result = osa.run(_LIST_CALENDARS, timeout=30, feature="Calendar")
    if not result.ok:
        raise osa.explain_failure(result, "Calendar")
    names = [n for n in result.output.splitlines() if n.strip()]
    return {"calendars": names, "count": len(names)}


def _parse_datetime(text: str) -> datetime:
    """Accept ISO ('2026-03-14T15:00') and a few forgiving shorthands."""
    text = (text or "").strip()
    for pattern in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    lowered = text.lower()
    match = re.match(r"^(today|tomorrow)(?:\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?$",
                     lowered)
    if match:
        base = datetime.now().replace(second=0, microsecond=0)
        if match.group(1) == "tomorrow":
            base += timedelta(days=1)
        hour = int(match.group(2)) if match.group(2) else 9
        minute = int(match.group(3)) if match.group(3) else 0
        if match.group(4) == "pm" and hour < 12:
            hour += 12
        if match.group(4) == "am" and hour == 12:
            hour = 0
        return base.replace(hour=hour, minute=minute)
    raise ValidationError(
        what="I could not understand the date %r." % text,
        why="It is not in a format I recognise.",
        tried="Parsing %r as a date and time" % text,
        needs="Something like '2026-03-14T15:00', or 'tomorrow at 3pm'.",
        fix="Write the date as YYYY-MM-DDTHH:MM.",
    )


# --------------------------------------------------------------------------
# Reminders
# --------------------------------------------------------------------------
_CREATE_REMINDER = '''
set theName to item 1 of argv
set listName to item 2 of argv
set theBody to item 3 of argv
tell application "Reminders"
  if listName is "" then
    make new reminder with properties {name:theName, body:theBody}
  else
    tell list listName to make new reminder with properties {name:theName, body:theBody}
  end if
end tell
return "created"
'''

_READ_REMINDERS = '''
set out to ""
tell application "Reminders"
  repeat with l in lists
    set lName to name of l
    try
      repeat with r in (every reminder of l whose completed is false)
        set out to out & lName & tab & (name of r) & linefeed
      end repeat
    end try
  end repeat
end tell
return out
'''


def _reminder_create(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    result = osa.run(_CREATE_REMINDER,
                     [args["title"], args.get("list", "") or "",
                      args.get("notes", "") or ""],
                     timeout=30, feature="Reminders")
    if not result.ok:
        raise osa.explain_failure(result, "Reminders")
    return {"created": True, "title": args["title"],
            "list": args.get("list") or "(your default list)"}


def _reminders_read(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    result = osa.run(_READ_REMINDERS, timeout=45, feature="Reminders")
    if not result.ok:
        raise osa.explain_failure(result, "Reminders")
    items = []
    for line in result.output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            items.append({"list": parts[0], "title": parts[1]})
    wrapped = injection.wrap(json.dumps(items, indent=2), source="your Reminders",
                             content_id="reminders")
    return {"count": len(items), "reminders": items,
            "untrusted": wrapped["wrapped"], "flagged": wrapped["flagged"]}


# --------------------------------------------------------------------------
# Notifications, clipboard, apps
# --------------------------------------------------------------------------
_NOTIFY = '''
display notification (item 1 of argv) with title (item 2 of argv) subtitle (item 3 of argv)
return "shown"
'''


def _notify(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    result = osa.run(_NOTIFY, [args["message"], args.get("title", "Assistant"),
                               args.get("subtitle", "") or ""],
                     timeout=15, feature="Notifications")
    if not result.ok:
        raise osa.explain_failure(result, "Notification Center")
    return {"shown": True, "message": args["message"],
            "note": "If you did not see it, check System Settings > "
                    "Notifications and allow notifications for your terminal."}


def _clipboard_read(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    if not paths.is_macos():
        raise ToolError(
            what="Reading the clipboard only works on macOS.",
            why="It uses the 'pbpaste' program, which is macOS-only.",
            needs="macOS", fix="Run this on your Mac.")
    try:
        completed = subprocess.run(["pbpaste"], capture_output=True, timeout=10,
                                   check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolError(
            what="Could not read the clipboard.", why=str(exc),
            tried="Running pbpaste", needs="A working pbpaste.",
            fix="Try 'pbpaste' in Terminal to see if it works.")
    text = completed.stdout.decode("utf-8", errors="replace")
    wrapped = injection.wrap(text[:100_000], source="your clipboard",
                             content_id="clipboard")
    return {"characters": len(text), "text": text[:100_000],
            "untrusted": wrapped["wrapped"], "flagged": wrapped["flagged"],
            "injection_signals": wrapped["signals"]}


def _app_launch(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    if not paths.is_macos():
        raise ToolError(
            what="Opening applications only works on macOS.",
            why="It uses the 'open' command, which is macOS-only.",
            needs="macOS", fix="Run this on your Mac.")
    name = args["name"]
    if "/" in name or name.startswith("-"):
        raise ValidationError(
            what="%r is not a valid application name." % name,
            why="An app name is a plain name like 'Safari', not a path or a flag.",
            tried="Validating the application name",
            needs="A plain application name.",
            fix="Use the name as it appears in your Applications folder.")
    # argv form, no shell: the name can never be interpreted as an option or
    # a second command.
    completed = subprocess.run(["/usr/bin/open", "-a", name], capture_output=True,
                               timeout=20, check=False, stdin=subprocess.DEVNULL)
    if completed.returncode != 0:
        raise ToolError(
            what="Could not open '%s'." % name,
            why=completed.stderr.decode("utf-8", "replace").strip() or "unknown error",
            tried="open -a %s" % name,
            needs="An installed application with that exact name.",
            fix="Check the spelling against your Applications folder.")
    return {"opened": True, "application": name}


def _spotlight(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    result = spotlight.search(args["query"], only_in=args.get("folder"),
                              limit=int(args.get("limit", 30)),
                              name_only=bool(args.get("name_only", False)))
    wrapped = injection.wrap(json.dumps(result.get("results", []), indent=2),
                             source="Spotlight results", content_id="spotlight")
    result["untrusted"] = wrapped["wrapped"]
    result["flagged"] = wrapped["flagged"]
    return result


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------
register(ToolSpec(
    name="calendar_read", capability="calendar.read", macos_only=True,
    description="Read upcoming events from your macOS Calendar.",
    parameters={"type": "object",
                "properties": {"days_ahead": {"type": "integer", "minimum": 1,
                                              "maximum": 365, "default": 7}},
                "additionalProperties": False},
    handler=_calendar_read,
    summarize=lambda a: "read your calendar for the next %d days" % a.get("days_ahead", 7),
))

register(ToolSpec(
    name="calendar_list", capability="calendar.read", macos_only=True,
    description="List the names of your calendars.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    handler=_calendar_list, summarize=lambda a: "list your calendars",
))

register(ToolSpec(
    name="calendar_create_event", capability="calendar.write", macos_only=True,
    description="Create an event in your macOS Calendar.",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 500},
            "start": {"type": "string", "minLength": 1,
                      "description": "ISO like 2026-03-14T15:00, or 'tomorrow at 3pm'"},
            "duration_minutes": {"type": "integer", "minimum": 1, "maximum": 1440,
                                 "default": 60},
            "calendar": {"type": "string", "maxLength": 200},
            "notes": {"type": "string", "maxLength": 4000},
        },
        "required": ["title", "start"], "additionalProperties": False,
    },
    handler=_calendar_create,
    summarize=lambda a: "add '%s' to your calendar at %s" % (a.get("title"), a.get("start")),
))

register(ToolSpec(
    name="reminders_read", capability="reminders.read", macos_only=True,
    description="Read your outstanding reminders.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    handler=_reminders_read, summarize=lambda a: "read your reminders",
))

register(ToolSpec(
    name="reminder_create", capability="reminders.write", macos_only=True,
    description="Create a reminder in the macOS Reminders app.",
    parameters={
        "type": "object",
        "properties": {"title": {"type": "string", "minLength": 1, "maxLength": 500},
                       "list": {"type": "string", "maxLength": 200},
                       "notes": {"type": "string", "maxLength": 4000}},
        "required": ["title"], "additionalProperties": False,
    },
    handler=_reminder_create,
    summarize=lambda a: "create the reminder '%s'" % a.get("title"),
))

register(ToolSpec(
    name="send_notification", capability="notify.send", macos_only=True,
    description="Show a macOS notification.",
    parameters={
        "type": "object",
        "properties": {"message": {"type": "string", "minLength": 1, "maxLength": 500},
                       "title": {"type": "string", "maxLength": 200, "default": "Assistant"},
                       "subtitle": {"type": "string", "maxLength": 200}},
        "required": ["message"], "additionalProperties": False,
    },
    handler=_notify,
    summarize=lambda a: "show you a notification saying %r" % a.get("message"),
))

register(ToolSpec(
    name="clipboard_read", capability="clipboard.read", macos_only=True,
    description="Read the current contents of your clipboard.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    handler=_clipboard_read, summarize=lambda a: "read what is on your clipboard",
))

register(ToolSpec(
    name="open_application", capability="app.launch", macos_only=True,
    description="Open an application by name.",
    parameters={"type": "object",
                "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 200}},
                "required": ["name"], "additionalProperties": False},
    handler=_app_launch,
    summarize=lambda a: "open the application '%s'" % a.get("name"),
))

register(ToolSpec(
    name="spotlight_search", capability="files.search", macos_only=True,
    description=("Search with macOS Spotlight. Covers formats the assistant "
                 "cannot read itself, but results outside your granted folders "
                 "are removed."),
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1},
                       "folder": {"type": "string"},
                       "name_only": {"type": "boolean", "default": False},
                       "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                                 "default": 30}},
        "required": ["query"], "additionalProperties": False,
    },
    handler=_spotlight, path_args=("folder",),
    summarize=lambda a: "search Spotlight for %r" % a.get("query"),
))
