"""Talking to Mac applications through AppleScript - safely.

WHAT IS APPLESCRIPT?
--------------------
Apple's built-in way for one program to ask another to do something:
"tell application Calendar to make a new event".  It is how a script can add
a reminder or read your week without any private APIs.  ``osascript`` is the
command-line program that runs it.

THE DANGEROUS WAY (WHICH WE DO NOT DO)
--------------------------------------
The obvious approach is to paste the user's text into the script:

    script = 'tell app "Reminders" to make new reminder with name "%s"' % title

If ``title`` contains a quotation mark, everything after it becomes CODE.  A
file named:

    holiday" \\n do shell script "rm -rf ~" \\n --

would run a shell command.  This is exactly SQL injection, wearing a hat.  And
remember where these strings come from: filenames, document contents, calendar
entries - all of it potentially attacker-controlled.

THE SAFE WAY (WHICH WE DO)
--------------------------
AppleScript has a proper parameter mechanism.  A script can declare
``on run argv`` and receive arguments as DATA, passed after ``--``:

    osascript -e 'on run argv
                    set t to item 1 of argv
                    ...
                  end run' -- "holiday\\" do shell script..."

The text never touches the parser.  It is a string, and it stays a string, no
matter what is in it.  This is the single most important idea in this file.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Any, Dict, List, NamedTuple, Optional, Sequence

from .. import paths
from ..errors import PlatformUnsupported, ToolError
from ..logging_setup import get

log = get(__name__)

DEFAULT_TIMEOUT = 30

#: macOS error numbers we can explain in plain English.
ERROR_HINTS: Dict[int, str] = {
    -1743: "macOS has not been given permission to control that app yet.",
    -1728: "The item you asked for does not exist in that app.",
    -600: "That application is not running.",
    -609: "The connection to that application was lost.",
    -10004: "macOS refused the request (a privilege violation).",
}


class OSAResult(NamedTuple):
    ok: bool
    output: str
    error: str
    code: int


def available() -> bool:
    return paths.is_macos() and bool(shutil.which("osascript"))


def require_macos(feature: str) -> None:
    if not paths.is_macos():
        raise PlatformUnsupported(
            what="'%s' only works on macOS." % feature,
            why="It talks to a macOS application through AppleScript, and this "
                "machine is not running macOS.",
            tried="Checking the platform",
            needs="macOS",
            fix="Run this on your Mac.",
        )
    if not shutil.which("osascript"):
        raise PlatformUnsupported(
            what="The 'osascript' program was not found.",
            why="It ships with macOS, so something unusual has happened to "
                "this installation.",
            tried="Looking for osascript on PATH",
            needs="/usr/bin/osascript",
            fix="Check that /usr/bin is on your PATH.",
        )


def build_command(script: str, args: Sequence[str]) -> List[str]:
    """Assemble the argv for osascript.

    Kept separate from running it so the tests can prove that user text always
    lands after ``--`` as data, without needing a Mac to check.
    """
    command = ["osascript", "-e", script]
    if args:
        command.append("--")
        command.extend(str(a) for a in args)
    return command


def wrap_handler(body: str) -> str:
    """Wrap a script body in the ``on run argv`` handler that receives data."""
    if "on run" in body:
        return body
    return "on run argv\n%s\nend run" % body


def run(script: str, args: Optional[Sequence[str]] = None,
        timeout: int = DEFAULT_TIMEOUT, feature: str = "AppleScript") -> OSAResult:
    """Run an AppleScript, passing `args` as data rather than as code."""
    require_macos(feature)
    command = build_command(wrap_handler(script), args or [])
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout,
                                   check=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ToolError(
            what="'%s' took too long and was stopped." % feature,
            why="The app did not respond within %d seconds. This usually means "
                "a permission dialog is waiting for you on screen." % timeout,
            tried="Running an AppleScript",
            needs="The app to respond.",
            fix="Look for a permission dialog on your Mac and answer it, then "
                "try again.",
        )
    except OSError as exc:
        raise ToolError(
            what="Could not run AppleScript.",
            why=str(exc),
            tried=" ".join(command[:3]),
            needs="A working osascript.",
            fix="Try running 'osascript -e \"return 1\"' in Terminal to check.",
        )

    output = completed.stdout.decode("utf-8", errors="replace").strip()
    error = completed.stderr.decode("utf-8", errors="replace").strip()
    return OSAResult(completed.returncode == 0, output, error, completed.returncode)


def explain_failure(result: OSAResult, app: str = "the app") -> ToolError:
    """Turn an AppleScript error into something a person can act on."""
    number = None
    for code in ERROR_HINTS:
        if str(code) in result.error:
            number = code
            break

    if number == -1743:
        return ToolError(
            what="macOS has not allowed access to %s yet." % app,
            why="Apple Events permission has not been granted for this app. "
                "macOS asks once, from the program that made the request - and "
                "a background service has no window to show that dialog in.",
            tried="Sending an Apple Event to %s" % app,
            needs="Permission under System Settings > Privacy & Security > Automation.",
            fix="Run 'assistant doctor --request-permissions' FROM YOUR TERMINAL. "
                "The dialog will appear there, where you can click Allow. Then "
                "check System Settings > Privacy & Security > Automation and "
                "make sure the entry for your terminal has %s ticked." % app,
        )
    if number is not None:
        return ToolError(
            what="%s refused the request." % app,
            why="%s (macOS error %d)" % (ERROR_HINTS[number], number),
            tried="Sending an Apple Event to %s" % app,
            needs="The app to be available and permitted.",
            fix="Open %s once by hand, then try again." % app,
        )
    return ToolError(
        what="The request to %s failed." % app,
        why=result.error or "no error message was returned",
        tried="Sending an Apple Event to %s" % app,
        needs="A working connection to the app.",
        fix="Run 'assistant doctor' to check which macOS permissions are missing.",
    )
