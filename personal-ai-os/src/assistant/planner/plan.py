"""Plans: turning a goal into ordered, checkable steps - and saving them.

THE HIERARCHY THE PROMPT ASKED FOR
----------------------------------
    GOAL -> OBJECTIVES -> TASKS -> ACTIONS -> RESULTS -> FEEDBACK

In this code:
    goal        the sentence you typed
    strategy    one whole approach to the goal (from superposition.py)
    steps       the ordered actions of the chosen strategy
    status      what happened to each step when it ran
    reflection  what we learned afterwards (reflect.py)

A plan is stored in the database, so you can close your laptop, come back
tomorrow, and run `assistant plan show <id>` to see exactly what was proposed,
what was chosen, what was rejected, and how far it got.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from .. import db
from .beam import Step
from .superposition import Choice, Evaluation

STATUSES = ("draft", "awaiting_approval", "running", "done", "failed", "cancelled")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create(goal: str, choice: Choice, steps: Sequence[Step],
           session_id: Optional[str] = None) -> str:
    """Save a plan and its rejected alternatives.  Returns the plan id."""
    plan_id = uuid.uuid4().hex[:12]
    conn = db.connect()
    conn.execute(
        "INSERT INTO plans(id,created_at,updated_at,goal,status,chosen,"
        "alternatives,rationale,risk,session_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (plan_id, _now(), _now(), goal, "draft",
         db.json_dump(choice.chosen.to_dict() if choice.chosen else None),
         db.json_dump([e.to_dict() for e in choice.ranked
                       if not choice.chosen or e.strategy.id != choice.chosen.strategy.id]),
         choice.explain(),
         float(choice.chosen.risk) if choice.chosen else 1.0,
         session_id),
    )
    for index, step in enumerate(steps):
        conn.execute(
            "INSERT INTO plan_steps(plan_id,idx,title,tool,arguments,capability,status) "
            "VALUES(?,?,?,?,?,?, 'pending')",
            (plan_id, index, step.title, step.tool,
             db.json_dump(step.arguments or {}), step.capability),
        )
    return plan_id


def get(plan_id: str) -> Optional[Dict[str, Any]]:
    conn = db.connect()
    row = db.one(conn, "SELECT * FROM plans WHERE id=?", (plan_id,))
    if not row:
        return None
    plan = dict(row)
    plan["chosen"] = db.json_load(plan.get("chosen"))
    plan["alternatives"] = db.json_load(plan.get("alternatives"), [])
    steps = db.query(conn, "SELECT * FROM plan_steps WHERE plan_id=? ORDER BY idx",
                     (plan_id,))
    plan["steps"] = db.rows_to_dicts(steps)
    for step in plan["steps"]:
        step["arguments"] = db.json_load(step.get("arguments"), {})
    plan["progress"] = _progress(plan["steps"])
    return plan


def _progress(steps: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(steps)
    done = sum(1 for s in steps if s["status"] == "done")
    failed = sum(1 for s in steps if s["status"] == "failed")
    return {"total": total, "done": done, "failed": failed,
            "percent": round(100.0 * done / total, 1) if total else 0.0}


def list_plans(limit: int = 20, status: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = db.connect()
    sql = "SELECT id, created_at, goal, status, risk FROM plans"
    args: List[Any] = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(limit))
    return db.rows_to_dicts(db.query(conn, sql, args))


def set_status(plan_id: str, status: str) -> None:
    if status not in STATUSES:
        raise ValueError("unknown plan status %r" % status)
    db.connect().execute("UPDATE plans SET status=?, updated_at=? WHERE id=?",
                         (status, _now(), plan_id))


def start_step(plan_id: str, index: int) -> None:
    db.connect().execute(
        "UPDATE plan_steps SET status='running', started_at=? WHERE plan_id=? AND idx=?",
        (_now(), plan_id, int(index)))


def finish_step(plan_id: str, index: int, ok: bool,
                result: Optional[Any] = None, error: Optional[str] = None) -> None:
    db.connect().execute(
        "UPDATE plan_steps SET status=?, result=?, error=?, finished_at=? "
        "WHERE plan_id=? AND idx=?",
        ("done" if ok else "failed",
         db.json_dump(result) if result is not None else None,
         error, _now(), plan_id, int(index)))


def cancel(plan_id: str) -> bool:
    plan = get(plan_id)
    if not plan or plan["status"] in ("done", "cancelled"):
        return False
    set_status(plan_id, "cancelled")
    db.connect().execute(
        "UPDATE plan_steps SET status='cancelled' WHERE plan_id=? AND status='pending'",
        (plan_id,))
    return True


def render(plan: Dict[str, Any]) -> str:
    """A plan as readable text, for the terminal or for the model."""
    lines = ["Goal: %s" % plan["goal"],
             "Plan %s  [%s]  risk %.2f" % (plan["id"], plan["status"], plan.get("risk") or 0.0),
             ""]
    if plan.get("rationale"):
        lines.append(plan["rationale"])
        lines.append("")
    lines.append("Steps:")
    marks = {"done": "[x]", "failed": "[!]", "running": "[>]",
             "cancelled": "[-]", "pending": "[ ]"}
    for step in plan.get("steps", []):
        lines.append("  %s %d. %s%s"
                     % (marks.get(step["status"], "[ ]"), step["idx"] + 1,
                        step["title"],
                        "  (%s)" % step["tool"] if step.get("tool") else ""))
        if step.get("error"):
            lines.append("        error: %s" % step["error"])
    progress = plan.get("progress") or {}
    lines.append("")
    lines.append("Progress: %d/%d steps done (%.0f%%)"
                 % (progress.get("done", 0), progress.get("total", 0),
                    progress.get("percent", 0.0)))
    return "\n".join(lines)
