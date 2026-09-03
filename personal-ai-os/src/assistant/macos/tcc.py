"""Checking which macOS permissions we actually have.

WHAT IS TCC?
------------
"Transparency, Consent and Control" - the part of macOS that shows those
"X would like to access your Calendar" dialogs and remembers your answer.
It is the reason apps cannot silently read your contacts.

HOW WE CHECK - AND WHY IT LOOKS ODD
-----------------------------------
There is no supported API that says "do I have Calendar permission?".  The
only honest way to find out is to *try something harmless and see what
happens*.  Asking Calendar to count its calendars is read-only and trivial;
if TCC has not granted access, macOS returns error -1743 and we know.

We deliberately do NOT read the TCC database directly.  It is protected,
reading it would require Full Disk Access, and poking at it is exactly the
kind of thing this project promised not to do.

THE HARD PART YOU SHOULD KNOW ABOUT
-----------------------------------
TCC attributes a permission to the *program that asked*.  For a Python
script, that program is the Python interpreter - or, when launched from a
terminal, often the terminal itself.  Two consequences:

  1. Granting a permission may grant it to every Python script you ever run.
     That is why we never ask for Full Disk Access.
  2. A background service (launchd) has no window, so the consent dialog may
     never appear and the request just fails.  That is why `assistant doctor
     --request-permissions` deliberately triggers each prompt from YOUR
     TERMINAL, where the dialog can actually be shown and answered.
"""
from __future__ import annotations

from typing import Any, Dict, List, NamedTuple

from .. import paths
from . import osa


class Probe(NamedTuple):
    name: str
    app: str
    script: str
    why: str


#: Each probe is a read-only question that costs nothing if it succeeds.
PROBES: List[Probe] = [
    Probe("calendar", "Calendar",
          'tell application "Calendar" to return count of calendars',
          "so the assistant can see your deadlines and add events"),
    Probe("reminders", "Reminders",
          'tell application "Reminders" to return count of lists',
          "so the assistant can read and create reminders"),
    Probe("notes", "Notes",
          'tell application "Notes" to return count of folders',
          "so the assistant can read your notes"),
    Probe("finder", "Finder",
          'tell application "Finder" to return name of home',
          "so the assistant can move files to the Trash properly"),
    Probe("system_events", "System Events",
          'tell application "System Events" to return count of processes',
          "so the assistant can see which applications are running"),
]


def check_one(probe: Probe, timeout: int = 10) -> Dict[str, Any]:
    """Try the harmless operation and interpret the result."""
    if not osa.available():
        return {
            "name": probe.name, "app": probe.app, "granted": False,
            "status": "unavailable",
            "detail": "AppleScript is not available on this system "
                      "(it is macOS-only).",
            "why_needed": probe.why,
            "how_to_grant": "Run this on macOS.",
        }
    try:
        result = osa.run(probe.script, timeout=timeout, feature=probe.app)
    except Exception as exc:
        return {"name": probe.name, "app": probe.app, "granted": False,
                "status": "error", "detail": str(exc)[:300],
                "why_needed": probe.why,
                "how_to_grant": "See System Settings > Privacy & Security > Automation."}

    if result.ok:
        return {"name": probe.name, "app": probe.app, "granted": True,
                "status": "granted", "detail": "responded with: %s" % result.output[:80],
                "why_needed": probe.why, "how_to_grant": ""}

    denied = "-1743" in result.error or "not authoris" in result.error.lower() \
        or "not authoriz" in result.error.lower()
    return {
        "name": probe.name, "app": probe.app, "granted": False,
        "status": "denied" if denied else "failed",
        "detail": result.error[:300],
        "why_needed": probe.why,
        "how_to_grant": (
            "Open System Settings > Privacy & Security > Automation, find the "
            "entry for your Terminal (or whichever app you run 'assistant' "
            "from), and tick %s. If no entry is there yet, run 'assistant "
            "doctor --request-permissions' from Terminal to make macOS ask."
            % probe.app),
    }


def check_all(timeout: int = 10) -> Dict[str, Any]:
    """Report on every permission we might want.  Never raises."""
    if not paths.is_macos():
        return {
            "platform": "not macOS",
            "note": "macOS permission checks do not apply here. Files, index, "
                    "memory, planning and the policy engine all still work.",
            "permissions": [],
        }
    results = [check_one(probe, timeout=timeout) for probe in PROBES]
    return {
        "platform": "macOS",
        "permissions": results,
        "granted": [r["name"] for r in results if r["granted"]],
        "missing": [r["name"] for r in results if not r["granted"]],
        "note": "Nothing here is required. Each missing permission only "
                "disables the feature that needs it - everything else works.",
    }


def full_disk_access_note() -> str:
    """Our standing answer about Full Disk Access."""
    return (
        "This assistant deliberately does NOT ask for Full Disk Access.\n\n"
        "Why: macOS would attach that permission to the Python interpreter, "
        "not to this program. Granting it would give EVERY Python script on "
        "your Mac the ability to read your Mail, Messages, Safari history and "
        "more - forever, and invisibly.\n\n"
        "Instead the assistant uses per-folder grants ('assistant permissions "
        "grant ~/Documents'), which you can list and revoke at any time, and "
        "which cover only what you chose.\n\n"
        "The consequence: reading Mail and Messages history is NOT IMPLEMENTED "
        "and will not be. That is a deliberate trade, not a missing feature."
    )
