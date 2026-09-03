"""launchd: making the assistant start itself and stay running.

WHAT IS LAUNCHD?
----------------
The part of macOS that starts and supervises background programs.  It is what
makes your Mac's own services come back after a crash or a restart.  Apple
supports it; nothing here is a hack.

WHAT IS A LAUNCH AGENT?
-----------------------
A small settings file (a "plist") in ~/Library/LaunchAgents describing a
program to run.  An *Agent* runs as YOU, when you log in.  A *Daemon* runs as
root at boot.  We use an Agent on purpose: the assistant should never have
more power than you do.

THE MODERN COMMANDS
-------------------
    launchctl bootstrap gui/$UID <plist>   - load it   (replaces `load`)
    launchctl bootout    gui/$UID/<label>  - unload it (replaces `unload`)
    launchctl kickstart -k gui/$UID/<label> - restart it

`launchctl load`/`unload` still work but are deprecated and behave
inconsistently on Apple silicon, so we use the domain-target form.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import paths
from ..errors import PlatformUnsupported
from ..logging_setup import get

log = get(__name__)

LABEL = paths.LAUNCH_LABEL


def domain_target() -> str:
    """The 'address' of your login session, e.g. gui/501."""
    return "gui/%d" % os.getuid()


def service_target() -> str:
    return "%s/%s" % (domain_target(), LABEL)


def build_plist(python: Optional[str] = None,
                app_dir: Optional[Path] = None,
                extra_env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Construct the launchd settings.  Pure function, so it is testable."""
    python = python or sys.executable
    app_dir = Path(app_dir) if app_dir else Path(__file__).resolve().parents[3]

    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin",
        "PYTHONPATH": str(app_dir / "src"),
        "PYTHONUNBUFFERED": "1",
    }
    # Carry the API key through if the user exported one, so the daemon can
    # reach the model. We never write the key itself into the plist.
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "PAIOS_HOME"):
        if os.environ.get(name):
            environment[name] = os.environ[name]
    environment.update(extra_env or {})

    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "assistant.daemon", "--serve"],
        "WorkingDirectory": str(app_dir),
        "EnvironmentVariables": environment,
        "RunAtLoad": True,
        # Restart if it exits unexpectedly, but not if it exited cleanly
        # (otherwise `assistant daemon stop` would fight launchd forever).
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "StandardOutPath": str(paths.logs_dir() / "daemon.out.log"),
        "StandardErrorPath": str(paths.logs_dir() / "daemon.err.log"),
        "ThrottleInterval": 10,
    }


def write_plist(data: Optional[Dict[str, Any]] = None,
                target: Optional[Path] = None) -> Path:
    target = target or paths.launch_agent_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as handle:
        plistlib.dump(data or build_plist(), handle)
    try:
        target.chmod(0o644)   # launchd requires it to be readable
    except OSError:
        pass
    return target


def _launchctl(*args: str, timeout: int = 20) -> subprocess.CompletedProcess:
    if not paths.is_macos() or not shutil.which("launchctl"):
        raise PlatformUnsupported(
            what="launchctl is not available.",
            why="Background services are managed by launchd, which is macOS-only.",
            tried="Looking for launchctl",
            needs="macOS",
            fix="On other systems, start the daemon yourself with "
                "'assistant daemon start'.",
        )
    return subprocess.run(["launchctl", *args], capture_output=True,
                          timeout=timeout, check=False, stdin=subprocess.DEVNULL)


def install(python: Optional[str] = None, app_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Write the plist and load it into your login session."""
    plist_path = write_plist(build_plist(python, app_dir))
    # bootout first so re-installing over an existing service is not an error.
    _launchctl("bootout", service_target())
    result = _launchctl("bootstrap", domain_target(), str(plist_path))
    ok = result.returncode == 0
    error = result.stderr.decode("utf-8", "replace").strip()
    return {
        "installed": ok,
        "plist": str(plist_path),
        "label": LABEL,
        "target": service_target(),
        "error": error if not ok else "",
        "hint": ("Run 'launchctl print %s' to inspect it." % service_target())
                if ok else
                ("If this says 'Input/output error', the service may already be "
                 "loaded - run 'assistant daemon restart'. If it says "
                 "'Operation not permitted', your terminal may need Full Disk "
                 "Access to manage launch agents, or you can load it manually "
                 "with: launchctl bootstrap %s %s" % (domain_target(), plist_path)),
    }


def uninstall(remove_file: bool = True) -> Dict[str, Any]:
    result = _launchctl("bootout", service_target())
    removed = False
    plist_path = paths.launch_agent_path()
    if remove_file and plist_path.exists():
        plist_path.unlink()
        removed = True
    return {
        "unloaded": result.returncode == 0,
        "plist_removed": removed,
        "note": "The service is stopped. Your data and settings are untouched.",
    }


def restart() -> Dict[str, Any]:
    result = _launchctl("kickstart", "-k", service_target())
    return {"restarted": result.returncode == 0,
            "error": result.stderr.decode("utf-8", "replace").strip()}


def status() -> Dict[str, Any]:
    """Is the service installed, and is it running right now?"""
    plist_path = paths.launch_agent_path()
    info: Dict[str, Any] = {
        "plist_exists": plist_path.exists(),
        "plist": str(plist_path),
        "label": LABEL,
        "loaded": False,
        "pid": None,
        "last_exit_code": None,
    }
    if not paths.is_macos():
        info["note"] = ("launchd is macOS-only. On this system, start the "
                        "daemon manually with 'assistant daemon start'.")
        return info
    try:
        result = _launchctl("print", service_target())
    except PlatformUnsupported:
        return info
    if result.returncode != 0:
        return info
    text = result.stdout.decode("utf-8", "replace")
    info["loaded"] = True
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("pid = "):
            info["pid"] = int(stripped.split("=", 1)[1].strip())
        elif stripped.startswith("last exit code = "):
            value = stripped.split("=", 1)[1].strip()
            info["last_exit_code"] = value
    return info
