"""Where everything lives on disk.

ANALOGY
-------
Before a company can operate it needs an address.  This file decides the
addresses: where the notebook (database) is kept, where the CCTV recordings
(logs) go, where the front door (socket) is.

On macOS the polite convention is to keep application data in
``~/Library/Application Support/<AppName>`` and logs in ``~/Library/Logs``.
We follow it.  On any other operating system (used for development and
automated testing) we fall back to the XDG convention so the test suite can
run anywhere.

Everything can be redirected with the ``PAIOS_HOME`` environment variable,
which is what the tests use so they never touch your real data.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "PersonalAIOS"
LAUNCH_LABEL = "com.personalaios.assistantd"

# Directory permissions: rwx for the owner only.  Nobody else on the machine
# gets to read your assistant's memory.
DIR_MODE = 0o700
FILE_MODE = 0o600


def is_macos() -> bool:
    return sys.platform == "darwin"


def home() -> Path:
    """The root folder for all Personal AI OS state."""
    override = os.environ.get("PAIOS_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if is_macos():
        return Path.home() / "Library" / "Application Support" / APP_NAME
    # Linux / CI fallback so the exact same code is testable off-Mac.
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "personal-ai-os"


def logs_dir() -> Path:
    override = os.environ.get("PAIOS_HOME")
    if not override and is_macos():
        return Path.home() / "Library" / "Logs" / APP_NAME
    return home() / "logs"


def config_path() -> Path:
    return home() / "config.json"


def db_path() -> Path:
    return home() / "db" / "assistant.db"


def run_dir() -> Path:
    return home() / "run"


def socket_path() -> Path:
    """The daemon's front door.

    A Unix domain socket is a special file.  Talking to it never touches the
    network, so no firewall rule and no macOS network permission is involved,
    and the file's 0600 permissions mean only your user account can connect.
    """
    override = os.environ.get("PAIOS_SOCKET")
    if override:
        return Path(override)
    return run_dir() / "assistantd.sock"


def pid_path() -> Path:
    return run_dir() / "assistantd.pid"


def killswitch_path() -> Path:
    """If this file exists, all autonomous action stops immediately."""
    return run_dir() / "STOP"


def audit_log_path() -> Path:
    return logs_dir() / "audit.jsonl"


def app_log_path() -> Path:
    return logs_dir() / "assistant.log"


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / (LAUNCH_LABEL + ".plist")


def ensure_dirs() -> None:
    """Create every directory we need, with tight permissions.

    Called by the installer and, defensively, at daemon start.  Creating a
    directory that already exists is not an error (``exist_ok=True``).
    """
    for path in (home(), home() / "db", logs_dir(), run_dir(), home() / "backups"):
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(DIR_MODE)
        except OSError:
            # Some filesystems (e.g. certain network mounts) refuse chmod.
            # Not fatal - we surface it in `assistant doctor` instead.
            pass


def all_paths() -> dict:
    """Used by `assistant doctor` and the tests to show the full layout."""
    return {
        "home": home(),
        "config": config_path(),
        "database": db_path(),
        "logs": logs_dir(),
        "audit_log": audit_log_path(),
        "socket": socket_path(),
        "kill_switch": killswitch_path(),
        "launch_agent": launch_agent_path(),
    }
