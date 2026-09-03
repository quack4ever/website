"""Reflection: asking "did that actually work?" after the fact.

WHY THIS EXISTS
---------------
An assistant that never checks its own work repeats the same mistake forever.
After a piece of work finishes, we ask a model to judge it honestly, write the
verdict to the database, and - crucially - only *propose* things to remember.

THE SAFETY BOUNDARY
-------------------
Reflection can suggest memories. It cannot write them.  Storing a memory goes
through the normal ``memory_remember`` tool, which goes through the policy
engine like everything else.  This matters: reflection reads tool output, tool
output can contain attacker-controlled text, and a self-improvement loop that
could silently rewrite its own beliefs would be a lovely place to hide a
prompt injection.

SELF-MODIFICATION IS NOT ALLOWED
--------------------------------
Reflection may suggest changes to plans and settings.  It may never modify the
assistant's own code.  There is no code path here that writes to the source
tree, and ``pathguard`` blocks the assistant's own directory anyway.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from .. import audit, config as config_module, db
from ..ai import Router
from ..ai.provider import Message
from ..errors import AssistantError
from ..logging_setup import get
from ..planner import superposition as sp
from . import roles

log = get(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reflect(goal: str, what_happened: str, *, session_id: Optional[str] = None,
            plan_id: Optional[str] = None,
            cfg: Optional[config_module.Config] = None,
            router: Optional[Router] = None) -> Dict[str, Any]:
    """Judge a finished piece of work.  Never raises."""
    cfg = cfg or config_module.load()
    router = router or Router(cfg)
    provider = router.for_role("fast")

    prompt = ("GOAL:\n%s\n\nWHAT HAPPENED:\n%s\n\nJudge it." %
              (goal[:2000], what_happened[:8000]))
    try:
        reply = provider.complete(roles.analyst_prompt(),
                                  [Message("user", prompt)], role="fast")
    except AssistantError as exc:
        return {"ok": False, "error": exc.to_dict(),
                "note": "Reflection was skipped because no model was available."}

    from .agent import extract_json
    parsed = extract_json(reply.text)
    if not isinstance(parsed, dict):
        return {"ok": False,
                "note": "The reflection model did not return a usable verdict.",
                "model_said": (reply.text or "")[:800]}

    worked = bool(parsed.get("worked"))
    summary = str(parsed.get("summary") or "")[:600]
    lessons = [str(x)[:300] for x in (parsed.get("lessons") or [])][:10]
    proposed = [str(x)[:300] for x in (parsed.get("remember") or [])][:10]

    db.connect().execute(
        "INSERT INTO reflections(ts,plan_id,session_id,worked,summary,lessons) "
        "VALUES(?,?,?,?,?,?)",
        (_now(), plan_id, session_id, 1 if worked else 0, summary,
         db.json_dump(lessons)))
    audit.record("agent.reflected", actor="assistant", session_id=session_id,
                 outcome="worked" if worked else "did_not_work",
                 arguments={"summary": summary})

    return {
        "ok": True,
        "worked": worked,
        "summary": summary,
        "what_went_wrong": parsed.get("what_went_wrong"),
        "lessons": lessons,
        # Suggestions only. Nothing is stored until it goes through the tool.
        "proposed_memories": proposed,
        "note": "Nothing was remembered automatically. Use "
                "'assistant memory add' to keep any of these.",
    }


def learn_from_outcome(strategy: sp.Strategy, succeeded: bool) -> sp.Strategy:
    """Fold one real result into a strategy's success estimate."""
    return sp.bayesian_update(strategy,
                              successes=1 if succeeded else 0,
                              failures=0 if succeeded else 1)


def recent(limit: int = 10, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM reflections"
    args: List[Any] = []
    if session_id:
        sql += " WHERE session_id=?"
        args.append(session_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    rows = db.rows_to_dicts(db.query(db.connect(), sql, args))
    for row in rows:
        row["lessons"] = db.json_load(row.get("lessons"), [])
    return rows


def success_rate(days: int = 30) -> Dict[str, Any]:
    """How often the assistant's work actually worked, over time."""
    row = db.one(
        db.connect(),
        "SELECT COUNT(*) AS total, COALESCE(SUM(worked),0) AS worked "
        "FROM reflections WHERE ts >= datetime('now', ?)",
        ("-%d days" % int(days),))
    total = int(row["total"]) if row else 0
    worked = int(row["worked"]) if row else 0
    return {
        "days": days, "reflections": total, "worked": worked,
        "rate": round(worked / total, 3) if total else None,
        "note": "Based on the assistant's own judgement of its work; it is a "
                "rough signal, not an objective score.",
    }
