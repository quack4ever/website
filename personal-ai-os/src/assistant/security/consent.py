"""The approval queue: where actions wait for your yes or no.

ANALOGY
-------
An in-tray on your desk.  When the robot wants to do something that needs
your signature, it puts a slip in the tray and stops.  It does not proceed
"just this once".  It does not assume silence means yes.  If you never answer,
the slip expires and the action never happens.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .. import audit, db


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def create(
    *,
    capability: str,
    tool: str,
    arguments: Dict[str, Any],
    risk: str,
    summary: str,
    resources: Optional[List[str]] = None,
    session_id: Optional[str] = None,
    timeout_seconds: int = 900,
) -> str:
    """Add a pending request.  Returns its short id."""
    request_id = uuid.uuid4().hex[:12]
    conn = db.connect()
    conn.execute(
        "INSERT INTO approvals(id,created_at,status,capability,tool,arguments,"
        "risk,summary,resources,session_id,expires_at) "
        "VALUES(?,?,'pending',?,?,?,?,?,?,?,?)",
        (
            request_id, _iso(_now()), capability, tool,
            db.json_dump(arguments), risk, summary,
            db.json_dump(resources or []), session_id,
            _iso(_now() + timedelta(seconds=int(timeout_seconds))),
        ),
    )
    audit.record(
        "approval.requested", actor="assistant", tool=tool, capability=capability,
        decision="ask", arguments=arguments, resources=resources,
        session_id=session_id, outcome=request_id,
    )
    return request_id


def get(request_id: str) -> Optional[Dict[str, Any]]:
    row = db.one(db.connect(), "SELECT * FROM approvals WHERE id=?", (request_id,))
    if not row:
        return None
    item = dict(row)
    item["arguments"] = db.json_load(item.get("arguments"), {})
    item["resources"] = db.json_load(item.get("resources"), [])
    return item


def pending(limit: int = 50) -> List[Dict[str, Any]]:
    """Everything still waiting, oldest first.  Expires stale items first."""
    expire_stale()
    rows = db.query(
        db.connect(),
        "SELECT * FROM approvals WHERE status='pending' ORDER BY created_at ASC LIMIT ?",
        (int(limit),),
    )
    items = []
    for row in rows:
        item = dict(row)
        item["arguments"] = db.json_load(item.get("arguments"), {})
        item["resources"] = db.json_load(item.get("resources"), [])
        items.append(item)
    return items


def expire_stale() -> int:
    """Turn timed-out requests into 'expired'.  Silence is never consent."""
    conn = db.connect()
    now = _iso(_now())
    rows = db.query(
        conn,
        # '<=' not '<': an approval whose expiry time is exactly now HAS
        # expired.  With '<' a zero-second timeout would stay pending forever.
        "SELECT id FROM approvals WHERE status='pending' AND expires_at IS NOT NULL "
        "AND expires_at <= ?",
        (now,),
    )
    for row in rows:
        conn.execute(
            "UPDATE approvals SET status='expired', decided_at=?, decided_by='timeout' "
            "WHERE id=?",
            (now, row["id"]),
        )
        audit.record("approval.expired", actor="system", outcome=row["id"], decision="deny")
    return len(rows)


def decide(request_id: str, approved: bool, by: str = "user",
           remember: bool = False) -> Dict[str, Any]:
    """Record your answer.  Only ever called from the CLI, never from a model."""
    item = get(request_id)
    if not item:
        raise KeyError("no approval request with id %r" % request_id)
    if item["status"] != "pending":
        return item
    status = "approved" if approved else "denied"
    conn = db.connect()
    conn.execute(
        "UPDATE approvals SET status=?, decided_at=?, decided_by=?, remember=? WHERE id=?",
        (status, _iso(_now()), by, 1 if remember else 0, request_id),
    )
    audit.record(
        "approval." + status, actor=by, tool=item["tool"],
        capability=item["capability"], decision="allow" if approved else "deny",
        arguments=item["arguments"], resources=item["resources"],
        session_id=item.get("session_id"), outcome=request_id,
    )
    return get(request_id) or item


def is_approved(request_id: str) -> bool:
    item = get(request_id)
    return bool(item and item["status"] == "approved")


def purge(older_than_days: int = 30) -> int:
    """Housekeeping: drop settled requests older than N days."""
    conn = db.connect()
    cur = conn.execute(
        "DELETE FROM approvals WHERE status != 'pending' "
        "AND created_at < datetime('now', ?)",
        ("-%d days" % int(older_than_days),),
    )
    return cur.rowcount
