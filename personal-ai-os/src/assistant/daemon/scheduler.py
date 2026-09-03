"""Autonomous work: doing things without being asked - inside your rules.

THE RULE THAT MAKES THIS SAFE
-----------------------------
A scheduled job does not get special powers.  It goes through exactly the same
policy engine as anything you type.  So a job that tries to delete files in
`assisted` mode will stop and ask, the same as if you had asked for it - which
means an "autonomous" job can never do something you have not permitted.

WHAT IT ACTUALLY RUNS
---------------------
Jobs are named internal operations, not arbitrary commands.  There is no
"run this shell string every hour" job type, deliberately: that would be a
persistent, invisible way to run anything.

Built-in jobs:
    index_refresh   - keep the file catalogue up to date
    memory_decay    - let unused memories fade
    approval_expiry - time out approvals you never answered
    audit_rotate    - keep the approval table from growing forever
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from .. import audit, config as config_module, db
from ..logging_setup import get
from ..security import consent, killswitch

log = get(__name__)

JobFunction = Callable[[], Dict[str, Any]]


def _job_index_refresh() -> Dict[str, Any]:
    from ..index import indexer
    from ..security import pathguard
    if not pathguard.list_scopes():
        return {"skipped": "no folders granted"}
    return indexer.index_scopes().to_dict()


def _job_memory_decay() -> Dict[str, Any]:
    """Decay is computed on read, so this only prunes what has fully faded."""
    from ..memory import store
    cfg = config_module.load()
    if not store.enabled(cfg):
        return {"skipped": "memory disabled"}
    floor = float(cfg.get("memory.min_confidence_to_recall", 0.25))
    half_life = float(cfg.get("memory.decay_half_life_days", 90))
    faded = 0
    for item in store.list_all(limit=5000):
        if item.get("pinned"):
            continue
        if store.effective_confidence(item, half_life) < floor * 0.25:
            store.forget(int(item["id"]), reason="faded below the recall floor")
            faded += 1
    return {"faded": faded}


def _job_approval_expiry() -> Dict[str, Any]:
    return {"expired": consent.expire_stale()}


def _job_housekeeping() -> Dict[str, Any]:
    return {"purged_approvals": consent.purge(older_than_days=30)}


BUILTIN_JOBS: Dict[str, JobFunction] = {
    "index_refresh": _job_index_refresh,
    "memory_decay": _job_memory_decay,
    "approval_expiry": _job_approval_expiry,
    "housekeeping": _job_housekeeping,
}

DEFAULT_SCHEDULE: Dict[str, int] = {
    "approval_expiry": 60,          # every minute
    "index_refresh": 3600,          # hourly
    "memory_decay": 86400,          # daily
    "housekeeping": 86400,          # daily
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def ensure_default_jobs() -> None:
    conn = db.connect()
    for name, seconds in DEFAULT_SCHEDULE.items():
        existing = db.one(conn, "SELECT id FROM jobs WHERE name=?", (name,))
        if existing:
            continue
        conn.execute(
            "INSERT INTO jobs(name,every_seconds,action,enabled,next_run,created_at) "
            "VALUES(?,?,?,1,?,?)",
            (name, seconds, name, _iso(_now() + timedelta(seconds=seconds)),
             _iso(_now())))


def due_jobs() -> List[Dict[str, Any]]:
    rows = db.query(
        db.connect(),
        "SELECT * FROM jobs WHERE enabled=1 AND (next_run IS NULL OR next_run <= ?)",
        (_iso(_now()),))
    return db.rows_to_dicts(rows)


def run_job(name: str) -> Dict[str, Any]:
    """Run one job now.  Records the outcome either way."""
    function = BUILTIN_JOBS.get(name)
    conn = db.connect()
    if function is None:
        conn.execute("UPDATE jobs SET last_status='unknown_job', last_run=? WHERE name=?",
                     (_iso(_now()), name))
        return {"ok": False, "error": "no such job: %s" % name}

    if killswitch.is_engaged():
        return {"ok": False, "skipped": "the emergency stop is engaged"}

    started = time.time()
    try:
        result = function()
        status, error = "ok", None
    except Exception as exc:            # a broken job must not kill the daemon
        log.exception("scheduled job %s failed", name)
        result, status, error = {}, "failed", "%s: %s" % (type(exc).__name__, exc)

    row = db.one(conn, "SELECT every_seconds FROM jobs WHERE name=?", (name,))
    interval = int(row["every_seconds"]) if row else 3600
    conn.execute(
        "UPDATE jobs SET last_run=?, next_run=?, last_status=?, last_error=? WHERE name=?",
        (_iso(_now()), _iso(_now() + timedelta(seconds=interval)), status, error, name))
    audit.record("job.ran", actor="scheduler", tool=name, outcome=status,
                 error=error, duration_ms=int((time.time() - started) * 1000))
    return {"ok": status == "ok", "job": name, "result": result, "error": error}


def list_jobs() -> List[Dict[str, Any]]:
    return db.rows_to_dicts(db.query(db.connect(), "SELECT * FROM jobs ORDER BY name"))


def set_enabled(name: str, enabled: bool) -> bool:
    cursor = db.connect().execute("UPDATE jobs SET enabled=? WHERE name=?",
                                  (1 if enabled else 0, name))
    return cursor.rowcount > 0


class Scheduler:
    """A background thread that runs due jobs once a minute."""

    def __init__(self, tick_seconds: Optional[int] = None) -> None:
        cfg = config_module.load()
        self.tick = int(tick_seconds or cfg.get("daemon.tick_seconds", 60))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        ensure_default_jobs()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()
        log.info("scheduler started (tick %ds)", self.tick)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("scheduler stopped")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not killswitch.is_engaged():
                    for job in due_jobs():
                        if self._stop.is_set():
                            break
                        run_job(job["name"])
            except Exception:
                log.exception("scheduler tick failed")
            self._stop.wait(self.tick)

    def tick_once(self) -> List[Dict[str, Any]]:
        """Run everything that is due, right now.  Used by tests and by
        'assistant daemon tick'."""
        ensure_default_jobs()
        if killswitch.is_engaged():
            return [{"skipped": "the emergency stop is engaged"}]
        return [run_job(job["name"]) for job in due_jobs()]
