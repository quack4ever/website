"""The emergency stop.

ANALOGY
-------
The big red button by the factory door.  Pressing it does not politely ask the
machines to wind down - it cuts power immediately.

HOW IT WORKS
------------
``assistant stop`` creates a file called ``STOP``.  Every single tool checks
for that file before doing anything.  We use a *file* rather than a message to
the daemon on purpose: if the daemon has crashed, hung, or gone rogue, a
message might never be read - but a file on disk is checked by every new
action regardless of what the daemon is doing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .. import paths


def engage(reason: str = "user requested stop", by: str = "user") -> None:
    """Stop all autonomous activity, now."""
    path = paths.killswitch_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "engaged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": reason,
        "by": by,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(paths.FILE_MODE)
    except OSError:
        pass


def release() -> bool:
    """Resume normal operation.  Returns True if it had been engaged."""
    path = paths.killswitch_path()
    if path.exists():
        path.unlink()
        return True
    return False


def is_engaged() -> bool:
    return paths.killswitch_path().exists()


def status() -> Optional[Dict[str, Any]]:
    """Details of why we are stopped, or None if running normally."""
    path = paths.killswitch_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # The file exists but is unreadable.  Still stopped - fail closed.
        return {"engaged_at": "unknown", "reason": "STOP file present but unreadable", "by": "unknown"}
