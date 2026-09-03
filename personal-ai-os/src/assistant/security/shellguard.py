"""Making 'run a command' survivable.

WHY THIS IS THE SCARIEST TOOL
-----------------------------
"Run a shell command" is a skeleton key: with it, every other restriction can
be walked around.  So it gets the strictest treatment in the whole codebase.

FIVE RULES
----------
1. NEVER use a shell.  We call the program directly, so ``;`` ``|`` ``&&``
   ``$(...)`` and backticks are just ordinary characters, not operators.
   This single decision removes command-injection as a category.
2. ALLOW-LIST, not deny-list.  Only programs you explicitly listed may run.
   A deny-list can always be worked around; an allow-list cannot.
3. NO ESCAPE HATCHES.  Even allow-listed programs have dangerous flags
   (``find -exec`` runs arbitrary programs).  Those flags are blocked.
4. BOUNDED.  Time limit, output limit, no interactive input.
5. ALWAYS HIGH RISK.  Even a perfect command asks for approval unless you
   wrote a rule saying otherwise.
"""
from __future__ import annotations

import os
import shlex
import shutil
from typing import Any, Dict, List, NamedTuple, Optional, Sequence

from ..errors import ValidationError

#: Flags that turn a harmless program into an arbitrary-code-execution tool.
DANGEROUS_ARGS: Dict[str, Sequence[str]] = {
    "find": ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint",
             "-fprintf", "-fls"),
    "grep": ("--include-dir",),  # harmless, kept as an example hook
    "sort": ("-o", "--output"),
    "defaults": ("write", "delete", "import", "rename"),
    "networksetup": ("-setdnsservers", "-setmanual", "-setwebproxy",
                     "-setsecurewebproxy", "-setairportnetwork"),
    "system_profiler": ("-xml",),  # huge output; blocked to protect the budget
}

#: Never runnable, even if a user adds them to the allow-list by mistake.
ALWAYS_BLOCKED = frozenset({
    "sudo", "su", "doas", "sh", "bash", "zsh", "fish", "csh", "tcsh", "ksh",
    "dash", "env", "eval", "exec", "osascript", "python", "python2", "python3",
    "perl", "ruby", "node", "deno", "bun", "php", "swift", "awk", "gawk", "sed",
    "xargs", "nc", "ncat", "netcat", "telnet", "ssh", "scp", "sftp", "rsync",
    "curl", "wget", "ftp", "chmod", "chown", "chflags", "rm", "rmdir", "mv",
    "cp", "dd", "mkfs", "diskutil", "launchctl", "systemsetup", "csrutil",
    "spctl", "tccutil", "security", "kextload", "nvram", "pmset", "killall",
    "kill", "pkill", "open", "crontab", "at", "screen", "tmux", "expect",
})


#: Tokens that only ever make sense to a shell.  We never use a shell, so if
#: one of these shows up it is either a mistake or an attempt to exploit a
#: shell we do not have.  Rejecting them costs nothing and closes the door if
#: some future code path ever does introduce one.
_OPERATOR_TOKENS = frozenset({
    ";", "&", "&&", "|", "||", ">", ">>", "<", "<<", "<<<", "2>", "&>", "|&",
    "(", ")", "{", "}", "`",
})

#: Substrings that indicate shell expansion, wherever they appear in a token.
#: Note we do NOT reject a bare "&" or "(" inside a token, because real
#: filenames contain them ("Tom & Jerry.txt", "Photo (1).png").
_EXPANSION_MARKERS = ("$(", "${", "`", "\n", "\r")


def _reject_shell_operators(argv: Sequence[str]) -> None:
    for token in argv:
        if token in _OPERATOR_TOKENS:
            raise ValidationError(
                what="The command contains the shell operator %r." % token,
                why="Operators like ';' '&&' '|' and '>' chain or redirect "
                    "commands. This system never runs a shell, so the operator "
                    "would just be passed to the program as a meaningless "
                    "filename - which means the command does not do what it "
                    "looks like it does. Either way it is rejected.",
                tried="Scanning the parsed arguments for shell operators",
                needs="A single command with no chaining or redirection.",
                fix="Run one command at a time, with no ';', '&&', '|' or '>'.",
            )
        for marker in _EXPANSION_MARKERS:
            if marker in token:
                raise ValidationError(
                    what="The command contains the shell expansion %r." % marker,
                    why="Constructs like $(...) and backticks ask a shell to run "
                        "another command and paste its output in. No shell is "
                        "used here, so this cannot work - and if it ever could, "
                        "it would be a command-injection hole.",
                    tried="Scanning the parsed arguments for shell expansions",
                    needs="Literal arguments only.",
                    fix="Write out the value you want instead of computing it "
                        "with $(...).",
                )


class ShellPlan(NamedTuple):
    argv: List[str]
    program: str
    resolved: Optional[str]
    reason: str


def _looks_like_assignment(token: str) -> bool:
    """``FOO=bar cmd`` is how you smuggle environment changes past a check."""
    if "=" not in token:
        return False
    head = token.split("=", 1)[0]
    return bool(head) and all(ch.isalnum() or ch == "_" for ch in head)


def validate(command: str, allowed: Sequence[str]) -> ShellPlan:
    """Parse and vet a command string.  Raises ValidationError if unsafe."""
    if not command or not command.strip():
        raise ValidationError(
            what="No command was given.",
            why="An empty command cannot be run.",
            needs="A command string.",
            fix="Provide a command, e.g. 'ls -la'.",
        )
    if "\x00" in command:
        raise ValidationError(
            what="The command contains a null byte.",
            why="Null bytes are used to truncate strings and fool validators.",
            needs="An ordinary text command.",
            fix="Remove the null byte.",
        )
    if len(command) > 4000:
        raise ValidationError(
            what="The command is too long (%d characters)." % len(command),
            why="Commands this long are almost always an attempt to hide something.",
            needs="A command under 4000 characters.",
            fix="Break the work into smaller steps.",
        )

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ValidationError(
            what="The command could not be parsed.",
            why="Unbalanced quotes: %s" % exc,
            tried="Splitting %r into arguments" % command[:80],
            needs="Balanced quotes.",
            fix="Check your quotation marks.",
        ) from exc

    if not argv:
        raise ValidationError(
            what="The command parsed to nothing.",
            why="After removing quotes there were no arguments left.",
            needs="A real command.",
            fix="Provide a command.",
        )

    if _looks_like_assignment(argv[0]):
        raise ValidationError(
            what="Environment-variable assignments are not allowed.",
            why="'%s' sets an environment variable before running a program, "
                "which can change how that program behaves (for example "
                "DYLD_INSERT_LIBRARIES can load arbitrary code)." % argv[0],
            tried="Parsing the first token of the command",
            needs="A plain program name.",
            fix="Remove the VAR=value prefix.",
        )

    _reject_shell_operators(argv)

    program = os.path.basename(argv[0])

    if program in ALWAYS_BLOCKED:
        raise ValidationError(
            what="'%s' can never be run by the assistant." % program,
            why="It is a shell, an interpreter, a network client, or a tool "
                "that modifies the system - any of which would let it do "
                "anything at all, bypassing every other protection.",
            tried="Checking '%s' against the permanent block list" % program,
            needs="A command from the allow-list.",
            fix="Allowed programs are: %s" % ", ".join(sorted(allowed)[:20]),
        )

    if program not in set(allowed):
        raise ValidationError(
            what="'%s' is not on your allow-list." % program,
            why="Only programs you have explicitly permitted may run.",
            tried="Checking '%s' against shell.allowed_binaries" % program,
            needs="'%s' added to shell.allowed_binaries in your config" % program,
            fix="If you really want this, run:  assistant config add "
                "shell.allowed_binaries %s   (think first: can this program "
                "write files, download things, or run other programs?)" % program,
        )

    # Absolute paths must point at the same program name we approved, and must
    # not sneak in via a relative path like ../../bin/evil.
    if os.path.sep in argv[0]:
        if ".." in argv[0].split(os.path.sep):
            raise ValidationError(
                what="The program path contains '..'.",
                why="Relative traversal in a program path is used to run "
                    "something other than what the name suggests.",
                needs="A plain program name.",
                fix="Use just the program name, e.g. 'ls' not '../../bin/ls'.",
            )

    for flag in DANGEROUS_ARGS.get(program, ()):
        for token in argv[1:]:
            if token == flag or token.startswith(flag + "="):
                raise ValidationError(
                    what="'%s %s' is blocked." % (program, flag),
                    why="That option lets %s run other programs or modify "
                        "files, which would bypass the allow-list." % program,
                    tried="Scanning the arguments for dangerous options",
                    needs="The same command without %s." % flag,
                    fix="Remove '%s' and try again." % flag,
                )

    resolved = shutil.which(argv[0]) if os.path.sep not in argv[0] else argv[0]
    if resolved is None:
        raise ValidationError(
            what="'%s' was not found on this system." % program,
            why="The program is allow-listed but is not installed, or is not "
                "on the PATH the assistant can see.",
            tried="Looking for '%s' on PATH" % argv[0],
            needs="The program to be installed.",
            fix="Install it, or choose a different command.",
        )

    return ShellPlan(argv=argv, program=program, resolved=resolved,
                     reason="allow-listed program with no dangerous options")


def safe_environment() -> Dict[str, str]:
    """A deliberately minimal environment for child processes.

    We do not pass your whole environment through: it may contain API keys,
    and variables like DYLD_INSERT_LIBRARIES can change what a program does.
    """
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin",
        "HOME": os.path.expanduser("~"),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
