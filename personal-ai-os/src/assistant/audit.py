"""The audit log: an unchangeable record of everything the assistant did.

ANALOGY
-------
This is the CCTV camera in the corridor.  It does not stop anyone - the
security guard (policy.py) does that.  Its job is to make sure that after the
fact you can always answer: *what happened, when, who allowed it, and to which
of my files?*

WRITTEN TWICE, DELIBERATELY
---------------------------
1. A row in the SQLite ``audit`` table  - so `assistant logs` can search it.
2. A line in ``audit.jsonl``            - a plain text file, one JSON object
   per line, opened in append mode only.  Even if the database is deleted,
   this survives; and it is trivially readable by any other tool.

We never expose an "edit" or "delete" function for audit records.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import db, paths, redact
from .logging_setup import get

log = get(__name__)


def now_iso() -> str:
    """Current time in UTC, ISO-8601.  Always UTC so logs from different
    timezones and daylight-saving changes still sort correctly."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(
    event: str,
    *,
    actor: str = "system",
    tool: Optional[str] = None,
    capability: Optional[str] = None,
    decision: Optional[str] = None,
    rule_id: Optional[int] = None,
    arguments: Optional[Any] = None,
    resources: Optional[List[str]] = None,
    outcome: Optional[str] = None,
    error: Optional[str] = None,
    session_id: Optional[str] = None,
    duration_ms: Optional[int] = None,
    redact_args: bool = True,
) -> int:
    """Write one audit entry.  Never raises: auditing must not break the app.

    Returns the row id (or -1 if the database write failed, in which case the
    JSONL line is still attempted).
    """
    entry: Dict[str, Any] = {
        "ts": now_iso(),
        "actor": actor,
        "event": event,
        "tool": tool,
        "capability": capability,
        "decision": decision,
        "rule_id": rule_id,
        "arguments": redact.redact_value(arguments) if (redact_args and arguments is not None) else arguments,
        "resources": resources or [],
        "outcome": outcome,
        "error": redact.redact_text(error) if error else None,
        "session_id": session_id,
        "duration_ms": duration_ms,
    }

    row_id = -1
    try:
        conn = db.connect()
        cur = conn.execute(
            "INSERT INTO audit(ts,actor,event,tool,capability,decision,rule_id,"
            "arguments,resources,outcome,error,session_id,duration_ms) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                entry["ts"], entry["actor"], entry["event"], entry["tool"],
                entry["capability"], entry["decision"], entry["rule_id"],
                db.json_dump(entry["arguments"]) if entry["arguments"] is not None else None,
                db.json_dump(entry["resources"]),
                entry["outcome"], entry["error"], entry["session_id"],
                entry["duration_ms"],
            ),
        )
        row_id = int(cur.lastrowid or -1)
    except Exception as exc:  # pragma: no cover - defensive
        log.error("audit database write failed: %s", exc)

    entry["id"] = row_id
    try:
        path = paths.audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # "a" = append only.  We never open the audit file for writing.
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        try:
            os.chmod(path, paths.FILE_MODE)
        except OSError:
            pass
    except Exception as exc:  # pragma: no cover - defensive
        log.error("audit file write failed: %s", exc)

    return row_id


def tail(limit: int = 50, event: Optional[str] = None, since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read the most recent audit entries (newest first)."""
    conn = db.connect()
    sql = "SELECT * FROM audit"
    clauses: List[str] = []
    args: List[Any] = []
    if event:
        clauses.append("event LIKE ?")
        args.append(event.replace("*", "%"))
    if since:
        clauses.append("ts >= ?")
        args.append(since)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    rows = db.rows_to_dicts(db.query(conn, sql, args))
    for row in rows:
        row["arguments"] = db.json_load(row.get("arguments"))
        row["resources"] = db.json_load(row.get("resources"), [])
    return rows


def summary(days: int = 7) -> Dict[str, Any]:
    """Counts by event and decision, for `assistant status`."""
    conn = db.connect()
    rows = db.query(
        conn,
        "SELECT event, decision, COUNT(*) AS n FROM audit "
        "WHERE ts >= datetime('now', ?) GROUP BY event, decision",
        ("-%d days" % int(days),),
    )
    by_event: Dict[str, int] = {}
    by_decision: Dict[str, int] = {}
    for row in rows:
        by_event[row["event"]] = by_event.get(row["event"], 0) + row["n"]
        if row["decision"]:
            by_decision[row["decision"]] = by_decision.get(row["decision"], 0) + row["n"]
    total = sum(by_event.values())
    return {"days": days, "total": total, "by_event": by_event, "by_decision": by_decision}
