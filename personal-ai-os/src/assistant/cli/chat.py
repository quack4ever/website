"""Interactive mode: just talk, no command syntax.

WHY THIS EXISTS
---------------
Typing this every time is miserable:

    assistant ask "what is in my documents folder?"

Forget the quotes and the shell tries to run a program called `what`. Forget
`assistant ask` and the same thing happens. That is a papercut on every single
question, and papercuts are what stop people using a tool.

So: run `assistant` with no arguments and you get a conversation. Type
whatever you like, press Return. Commands still exist, but they start with a
slash - `/status`, `/approve` - which is the convention people already know
from chat apps.

Approvals appear inline. When the assistant needs permission you answer `y` or
`n` right there instead of copying an id into another command.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Any, Dict, List, Optional

from ..errors import AssistantError
from . import render

BANNER = r"""
   ___                      _        _
  /   |  ______ _____ _____(_)______/ /_____ _____  ____
 / /| | / ___/ // / _ \_  _/ / ___/ __/ __ `/ __ \/ __/
/ ___ |(__  )_  _/ __/ / / / (__  ) /_/ /_/ / / / / /_
\_/  |_/____/ /_/ \___/ /_/_/____/\__/\__,_/_/ /_/\__/
"""

HELP = """\
Just type what you want. No quotes, no command names.

  what's in my documents folder?
  find everything about my science project
  tidy up my downloads

Slash commands:
  /status              what it can do and has been doing
  /permissions         what folders it may touch
  /grant PATH          allow a folder        (add  rw  to allow changes)
  /revoke PATH         withdraw a folder
  /index               rebuild the file catalogue
  /search WORDS        search your files directly
  /plan GOAL           compare strategies for a goal
  /approvals           anything waiting for your yes
  /memory              what it remembers about you
  /logs                what it has done
  /doctor              check the installation
  /stop  /resume       emergency brake
  /help                this
  /quit                leave  (Ctrl-D also works)
"""


class Spinner:
    """A 'thinking' indicator, so a long answer never looks like a freeze."""

    FRAMES = "|/-\\"

    def __init__(self, label: str = "thinking") -> None:
        self.label = label
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "Spinner":
        if sys.stdout.isatty():
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
            sys.stdout.write("\r" + " " * (len(self.label) + 6) + "\r")
            sys.stdout.flush()

    def _spin(self) -> None:
        index = 0
        while not self._stop.is_set():
            sys.stdout.write("\r  %s %s " % (self.FRAMES[index % 4],
                                             render.paint(self.label, "dim")))
            sys.stdout.flush()
            index += 1
            self._stop.wait(0.12)


def _setup_readline() -> None:
    """Arrow-key history and line editing, if the platform provides it."""
    try:
        import readline  # noqa: F401
    except ImportError:
        return
    try:
        import atexit
        import os
        from .. import paths
        history = paths.home() / "chat_history"
        history.parent.mkdir(parents=True, exist_ok=True)
        if history.exists():
            readline.read_history_file(str(history))
        readline.set_history_length(500)
        atexit.register(lambda: readline.write_history_file(str(history)))
    except Exception:
        pass  # history is a nicety, never a reason to fail


def _print_answer(text: str) -> None:
    print()
    for line in (text or "").rstrip().split("\n"):
        print("  " + line)
    print()


def _handle_approvals(call, pending: List[Dict[str, Any]]) -> bool:
    """Ask about each waiting action right here. Returns True if any approved."""
    approved_any = False
    for item in pending:
        print()
        print("  " + render.paint("Permission needed", "bold"))
        print("  " + (item.get("summary") or "").replace("Waiting for your approval: ", ""))
        if item.get("why"):
            print("  " + render.paint(item["why"], "dim"))
        try:
            answer = input("  Allow it?  [y]es / [n]o / [s]kip  › ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Left it pending.")
            return approved_any
        request_id = item.get("approval_id") or item.get("id")
        if answer.startswith("y"):
            call("approve", {"id": request_id})
            print("  " + render.paint("Approved. Ask again and it will go ahead.", "green"))
            approved_any = True
        elif answer.startswith("n"):
            call("deny", {"id": request_id})
            print("  " + render.paint("Refused.", "yellow"))
        else:
            print("  Left it pending.")
    return approved_any


def _slash(call, line: str) -> bool:
    """Run a slash command. Returns False when the user wants to leave."""
    parts = line[1:].split(None, 1)
    name = (parts[0] if parts else "").lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if name in ("quit", "exit", "q", "bye"):
        return False
    if name in ("help", "h", "?"):
        print(HELP)
        return True

    from .main import (_render_approvals, _render_logs, _render_memory,
                       _render_permissions, _render_plan, _render_search,
                       _render_status, _render_doctor, _render_index_report)

    simple = {
        "status": ("status", {}, _render_status),
        "permissions": ("permissions", {}, _render_permissions),
        "approvals": ("approvals", {}, _render_approvals),
        "memory": ("memory_list", {"limit": 50}, _render_memory),
        "logs": ("logs", {"limit": 25}, _render_logs),
        "doctor": ("doctor", {}, _render_doctor),
    }
    if name in simple:
        command, args, renderer = simple[name]
        with Spinner("checking"):
            data = call(command, args)
        print(renderer(data))
        return True

    if name == "index":
        with Spinner("scanning your files"):
            data = call("index_build", {})
        print(_render_index_report(data))
        return True
    if name == "search":
        if not rest:
            print("  What should I search for?  /search chemistry")
            return True
        print(_render_search(call("index_search", {"query": rest, "limit": 15})))
        return True
    if name == "plan":
        if not rest:
            print("  What should I plan for?  /plan organise my semester")
            return True
        with Spinner("comparing strategies"):
            data = call("plan", {"goal": rest})
        print(_render_plan(data))
        return True
    if name == "grant":
        if not rest:
            print("  Which folder?  /grant ~/Documents      (add  rw  to allow changes)")
            return True
        words = rest.split()
        mode = "readwrite" if len(words) > 1 and words[-1] in ("rw", "readwrite", "write") else "read"
        path = words[0]
        data = call("grant", {"path": path, "mode": mode})
        print("  " + render.paint("Granted %s access to %s" % (data["mode"], data["path"]), "green"))
        return True
    if name == "revoke":
        if not rest:
            print("  Which folder?  /revoke ~/Downloads")
            return True
        data = call("revoke", {"path": rest.split()[0]})
        print("  " + ("Revoked." if data["revoked"] else "That folder was not granted."))
        return True
    if name == "stop":
        call("stop", {"reason": "asked in chat"})
        print("  " + render.paint("STOPPED. Everything has halted. /resume to continue.", "red"))
        return True
    if name == "resume":
        data = call("resume", {})
        print("  " + ("Resumed." if data["resumed"] else "It was not stopped."))
        return True

    print("  Unknown command /%s. Type /help to see them all." % name)
    return True


def run(call, session_id: Optional[str] = None) -> int:
    """The conversation loop. `call` is the CLI's dispatcher."""
    _setup_readline()

    print(render.paint(BANNER, "cyan") if sys.stdout.isatty() else "Personal AI OS")
    try:
        status = call("status", {})
        scopes = status.get("scopes") or []
        models = (status.get("models") or {}).get("roles") or {}
        ready = [r for r, i in models.items() if i.get("available")]

        print("  " + render.paint("Personal AI OS", "bold") + "  ·  mode: %s"
              % status.get("autonomy_mode"))
        if scopes:
            print("  Folders: " + ", ".join(s["path"] for s in scopes[:3])
                  + (" +%d more" % (len(scopes) - 3) if len(scopes) > 3 else ""))
        else:
            print("  " + render.paint(
                "No folders granted yet - I can read nothing. Try:  /grant ~/Documents",
                "yellow"))
        if not ready:
            print("  " + render.paint(
                "No AI model reachable, so I cannot reason yet. /doctor explains how "
                "to fix it.", "yellow"))
        waiting = status.get("pending_approvals", 0)
        if waiting:
            print("  " + render.paint("%d action(s) waiting for you - /approvals" % waiting,
                                      "yellow"))
        if status.get("kill_switch"):
            print("  " + render.paint("EMERGENCY STOP IS ENGAGED. /resume to clear it.",
                                      "red"))
    except AssistantError as exc:
        print(render.error(exc))

    print(render.paint("  Type anything. /help for commands, /quit to leave.", "dim"))
    print()

    while True:
        try:
            line = input(render.paint("you › ", "cyan") if sys.stdout.isatty() else "you > ")
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return 0

        line = line.strip()
        if not line:
            continue
        if line.startswith("/"):
            try:
                if not _slash(call, line):
                    print("Bye.")
                    return 0
            except AssistantError as exc:
                print(render.error(exc))
            continue

        payload: Dict[str, Any] = {"question": line}
        if session_id:
            payload["session_id"] = session_id
        try:
            with Spinner("thinking"):
                result = call("ask", payload)
        except AssistantError as exc:
            print(render.error(exc))
            continue
        except KeyboardInterrupt:
            print("\n  Stopped that request.")
            continue

        session_id = result.get("session_id") or session_id
        _print_answer(result.get("text", ""))

        pending = result.get("pending_approvals") or []
        if pending and _handle_approvals(call, pending):
            # They said yes - carry straight on instead of making them retype.
            try:
                with Spinner("carrying on"):
                    result = call("ask", dict(payload, session_id=session_id))
                _print_answer(result.get("text", ""))
            except AssistantError as exc:
                print(render.error(exc))
