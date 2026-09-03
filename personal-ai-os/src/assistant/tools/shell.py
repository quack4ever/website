"""Running an allow-listed command.

Read ``security/shellguard.py`` first - it explains why this is the most
dangerous tool and what stops it being a skeleton key.  This module is only
the plumbing: validate, run without a shell, bound the time and output.
"""
from __future__ import annotations

import subprocess
from typing import Any, Dict

from .. import config as config_module
from ..errors import ToolError
from ..security import injection, shellguard
from .registry import ExecContext, ToolSpec, register


def _run(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    cfg = ctx.config or config_module.load()
    allowed = cfg.get("shell.allowed_binaries", []) or []
    timeout = int(args.get("timeout_seconds") or cfg.get("shell.timeout_seconds", 30))
    max_output = int(cfg.get("shell.max_output_bytes", 200_000))

    plan = shellguard.validate(args["command"], allowed)

    try:
        completed = subprocess.run(
            plan.argv,
            shell=False,            # THE critical line: no shell, ever.
            capture_output=True,
            timeout=timeout,
            env=shellguard.safe_environment(),
            cwd=str(args.get("working_directory") or "/"),
            stdin=subprocess.DEVNULL,   # never wait for typed input
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(
            what="The command took longer than %d seconds and was stopped." % timeout,
            why="It may be waiting for input, or scanning something very large.",
            tried=" ".join(plan.argv),
            needs="A command that finishes quickly.",
            fix="Narrow the command (for example add a folder to search in), "
                "or raise shell.timeout_seconds in your config.",
        )
    except FileNotFoundError:
        raise ToolError(
            what="'%s' is not installed." % plan.program,
            why="The program is allow-listed but not present on this Mac.",
            tried=" ".join(plan.argv),
            needs="The program to be installed.",
            fix="Install it, or use a different command.",
        )
    except PermissionError as exc:
        raise ToolError(
            what="macOS refused to run '%s'." % plan.program,
            why=str(exc),
            tried=" ".join(plan.argv),
            needs="Permission to execute that file.",
            fix="Check the file's permissions in Finder, or choose another command.",
        )

    stdout = completed.stdout[:max_output].decode("utf-8", errors="replace")
    stderr = completed.stderr[:20_000].decode("utf-8", errors="replace")
    truncated = len(completed.stdout) > max_output

    # Command output is untrusted: a file listing can contain a filename that
    # is itself an injection attempt.
    wrapped = injection.wrap(stdout, source="command output: " + plan.program,
                             content_id="shell")

    return {
        "command": " ".join(plan.argv),
        "exit_code": completed.returncode,
        "succeeded": completed.returncode == 0,
        "stdout": stdout,
        "stderr": stderr,
        "untrusted": wrapped["wrapped"],
        "injection_signals": wrapped["signals"],
        "truncated": truncated,
    }


register(ToolSpec(
    name="run_command", capability="shell.execute",
    description=("Run one allow-listed, read-only command (no shell, no pipes, "
                 "no redirection). Only programs in shell.allowed_binaries can run."),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "minLength": 1, "maxLength": 4000},
            "working_directory": {"type": "string"},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
        },
        "required": ["command"], "additionalProperties": False,
    },
    handler=_run, path_args=("working_directory",),
    summarize=lambda a: "run the command: %s" % a.get("command"),
))
