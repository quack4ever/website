"""Long-term memory: the assistant's notebook.

ANALOGY
-------
A new colleague who forgets everything overnight is exhausting to work with.
One who writes down "she prefers bullet points" and "the science project is
due 14 March" gets better every week.  That notebook is this file.

THE THREE RULES
---------------
1. **You own it.**  List it, search it, edit it, delete it, or switch it off
   entirely.  Nothing is hidden from you.
2. **It never stores secrets.**  Passwords, API keys, card numbers and the
   like are detected and refused at the point of writing, with a log entry.
3. **It forgets.**  Confidence in an unused memory halves every so often, so
   stale beliefs fade instead of hardening into wrong facts.  Anything you
   *pin* is exempt.

PROVENANCE
----------
Every entry records where it came from.  When the assistant says "you prefer
X", you can ask why it thinks that and get a real answer.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import audit, config as config_module, db, redact
from ..errors import AssistantError
from ..index.search import build_match
from ..logging_setup import get

log = get(__name__)

KINDS = ("preference", "fact", "project", "goal", "decision", "workflow")


class MemoryRefused(AssistantError):
    code = "memory.refused"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _days_since(stamp: Optional[str]) -> float:
    if not stamp:
        return 0.0
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - moment).total_seconds() / 86400.0)


def effective_confidence(row: Dict[str, Any], half_life_days: float = 90.0) -> float:
    """Confidence, reduced by how long it has been since the memory was useful.

    ``0.5 ** (age / half_life)`` is exponential decay: after one half-life the
    number is halved, after two it is quartered.  It is the same maths used
    for radioactive decay, and it means old unused beliefs fade smoothly
    rather than vanishing at an arbitrary cut-off.
    """
    base = float(row.get("confidence", 0.7))
    if row.get("pinned"):
        return base
    age = _days_since(row.get("last_used") or row.get("updated_at") or row.get("created_at"))
    if half_life_days <= 0:
        return base
    return round(base * math.pow(0.5, age / half_life_days), 4)


def enabled(cfg: Optional[config_module.Config] = None) -> bool:
    return (cfg or config_module.load()).memory_enabled


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------
def remember(text: str, kind: str = "fact", *, scope: Optional[str] = None,
             confidence: float = 0.7, source: str = "conversation",
             pinned: bool = False, cfg: Optional[config_module.Config] = None) -> Dict[str, Any]:
    """Store one thing.  Refuses secrets and refuses to run when memory is off."""
    cfg = cfg or config_module.load()
    if not enabled(cfg):
        raise MemoryRefused(
            what="Memory is switched off, so nothing was stored.",
            why="memory.enabled is false in your settings.",
            tried="Storing a new memory",
            needs="Memory to be enabled.",
            fix="Run:  assistant config set memory.enabled true",
        )

    text = (text or "").strip()
    if not text:
        raise MemoryRefused(
            what="There was nothing to remember.",
            why="The text was empty.",
            needs="Some text.",
            fix="Say what you would like remembered.",
        )
    if len(text) > 2000:
        raise MemoryRefused(
            what="That memory is too long (%d characters)." % len(text),
            why="Memories are short facts, not documents. Long text belongs in "
                "a file, which the index can search.",
            needs="Under 2000 characters.",
            fix="Save the long version as a file and remember a one-line summary.",
        )

    found = redact.find_secrets(text)
    if found:
        audit.record("memory.refused_secret", actor="system",
                     outcome="refused", error="detected: " + ", ".join(found))
        raise MemoryRefused(
            what="That looks like a secret, so it was not stored.",
            why="It contains something shaped like: %s." % ", ".join(found),
            tried="Scanning the text before writing it to the database",
            needs="Text without credentials in it.",
            fix="Never store passwords or keys here. Use the macOS Keychain "
                "for credentials. If this was a false alarm, rephrase without "
                "the key-like string.",
        )

    if kind not in KINDS:
        raise MemoryRefused(
            what="'%s' is not a kind of memory." % kind,
            why="Valid kinds are: %s" % ", ".join(KINDS),
            needs="A valid kind.",
            fix="Use one of: %s" % ", ".join(KINDS),
        )

    conn = db.connect()
    existing = db.one(conn,
                      "SELECT id FROM memory WHERE deleted=0 AND kind=? AND text=?",
                      (kind, text))
    if existing:
        # Seeing the same thing again is evidence, so raise confidence a
        # little rather than storing a duplicate row.
        conn.execute(
            "UPDATE memory SET confidence=MIN(0.99, confidence + 0.1), "
            "updated_at=?, last_used=? WHERE id=?",
            (_now(), _now(), existing["id"]))
        return get(int(existing["id"])) or {}

    now = _now()
    cursor = conn.execute(
        "INSERT INTO memory(created_at,updated_at,kind,text,scope,confidence,"
        "source,pinned,use_count) VALUES(?,?,?,?,?,?,?,?,0)",
        (now, now, kind, text, scope, float(confidence), source, 1 if pinned else 0),
    )
    memory_id = int(cursor.lastrowid or 0)
    conn.execute("INSERT INTO memory_fts(mem_id, text) VALUES(?,?)", (memory_id, text))
    audit.record("memory.stored", actor="system", arguments={"kind": kind, "text": text[:120]})
    _enforce_limit(cfg)
    return get(memory_id) or {}


def _enforce_limit(cfg: config_module.Config) -> int:
    """Keep the notebook from growing without bound: drop the weakest first."""
    limit = int(cfg.get("memory.max_entries", 5000))
    conn = db.connect()
    total = db.one(conn, "SELECT COUNT(*) AS n FROM memory WHERE deleted=0")["n"]
    if total <= limit:
        return 0
    rows = db.query(
        conn,
        "SELECT id FROM memory WHERE deleted=0 AND pinned=0 "
        "ORDER BY confidence ASC, COALESCE(last_used, created_at) ASC LIMIT ?",
        (total - limit,))
    for row in rows:
        forget(int(row["id"]), reason="memory limit reached")
    return len(rows)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
def get(memory_id: int) -> Optional[Dict[str, Any]]:
    row = db.one(db.connect(),
                 "SELECT * FROM memory WHERE id=? AND deleted=0", (int(memory_id),))
    if not row:
        return None
    item = dict(row)
    item["effective_confidence"] = effective_confidence(item)
    return item


def list_all(kind: Optional[str] = None, limit: int = 100,
             include_deleted: bool = False) -> List[Dict[str, Any]]:
    conn = db.connect()
    sql = "SELECT * FROM memory WHERE 1=1"
    args: List[Any] = []
    if not include_deleted:
        sql += " AND deleted=0"
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    sql += " ORDER BY pinned DESC, confidence DESC, id DESC LIMIT ?"
    args.append(int(limit))
    items = db.rows_to_dicts(db.query(conn, sql, args))
    for item in items:
        item["effective_confidence"] = effective_confidence(item)
    return items


def search(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Keyword search over memories, same FTS5 engine as the file index."""
    conn = db.connect()
    match = build_match(query, mode="any")
    if not match:
        return []
    try:
        rows = db.query(
            conn,
            "SELECT m.* FROM memory_fts JOIN memory m ON m.id = memory_fts.mem_id "
            "WHERE memory_fts MATCH ? AND m.deleted=0 "
            "ORDER BY bm25(memory_fts) LIMIT ?",
            (match, int(limit)))
    except Exception:
        return []
    items = db.rows_to_dicts(rows)
    for item in items:
        item["effective_confidence"] = effective_confidence(item)
    return items


def recall(query: str = "", limit: Optional[int] = None,
           cfg: Optional[config_module.Config] = None) -> List[Dict[str, Any]]:
    """What the orchestrator injects into a conversation.

    Returns the strongest relevant memories, filtered by decayed confidence,
    and marks them used (which slows their decay).
    """
    cfg = cfg or config_module.load()
    if not enabled(cfg):
        return []
    limit = int(limit or cfg.get("memory.recall_limit", 12))
    floor = float(cfg.get("memory.min_confidence_to_recall", 0.25))
    half_life = float(cfg.get("memory.decay_half_life_days", 90))

    candidates = search(query, limit=limit * 3) if query.strip() else []
    if len(candidates) < limit:
        seen = {c["id"] for c in candidates}
        for item in list_all(limit=limit * 3):
            if item["id"] not in seen:
                candidates.append(item)

    strong = []
    for item in candidates:
        item["effective_confidence"] = effective_confidence(item, half_life)
        if item["effective_confidence"] >= floor:
            strong.append(item)

    strong.sort(key=lambda i: (not i["pinned"], -i["effective_confidence"]))
    chosen = strong[:limit]
    mark_used([int(i["id"]) for i in chosen])
    return chosen


def mark_used(ids: List[int]) -> None:
    if not ids:
        return
    conn = db.connect()
    now = _now()
    for memory_id in ids:
        conn.execute("UPDATE memory SET use_count=use_count+1, last_used=? WHERE id=?",
                     (now, int(memory_id)))


# --------------------------------------------------------------------------
# changing and removing
# --------------------------------------------------------------------------
def edit(memory_id: int, text: Optional[str] = None, kind: Optional[str] = None,
         confidence: Optional[float] = None, pinned: Optional[bool] = None) -> Dict[str, Any]:
    item = get(memory_id)
    if not item:
        raise MemoryRefused(
            what="There is no memory #%s." % memory_id,
            why="It may already have been deleted.",
            needs="A valid memory id.",
            fix="Run 'assistant memory list' to see the ids.",
        )
    if text is not None and redact.find_secrets(text):
        raise MemoryRefused(
            what="The new text looks like a secret, so it was not saved.",
            why="Memory never stores credentials.",
            needs="Text without credentials.",
            fix="Rephrase without the key-like string.",
        )
    conn = db.connect()
    if text is not None:
        conn.execute("UPDATE memory SET text=?, updated_at=? WHERE id=?",
                     (text, _now(), memory_id))
        conn.execute("DELETE FROM memory_fts WHERE mem_id=?", (memory_id,))
        conn.execute("INSERT INTO memory_fts(mem_id,text) VALUES(?,?)", (memory_id, text))
    if kind is not None:
        if kind not in KINDS:
            raise MemoryRefused(
                what="'%s' is not a kind of memory." % kind,
                why="Valid kinds: %s" % ", ".join(KINDS),
                needs="A valid kind.", fix="Use one of: %s" % ", ".join(KINDS))
        conn.execute("UPDATE memory SET kind=?, updated_at=? WHERE id=?",
                     (kind, _now(), memory_id))
    if confidence is not None:
        conn.execute("UPDATE memory SET confidence=?, updated_at=? WHERE id=?",
                     (max(0.0, min(1.0, float(confidence))), _now(), memory_id))
    if pinned is not None:
        conn.execute("UPDATE memory SET pinned=?, updated_at=? WHERE id=?",
                     (1 if pinned else 0, _now(), memory_id))
    audit.record("memory.edited", actor="user", arguments={"id": memory_id})
    return get(memory_id) or {}


def forget(memory_id: int, reason: str = "user request") -> bool:
    """Soft-delete, then hard-delete from the search index.

    The row is kept (marked deleted) so `assistant memory list --deleted` can
    show you what was removed and when - deleting your own audit trail would
    be its own kind of dishonesty.
    """
    conn = db.connect()
    item = db.one(conn, "SELECT id FROM memory WHERE id=? AND deleted=0", (int(memory_id),))
    if not item:
        return False
    conn.execute("UPDATE memory SET deleted=1, updated_at=? WHERE id=?",
                 (_now(), int(memory_id)))
    conn.execute("DELETE FROM memory_fts WHERE mem_id=?", (int(memory_id),))
    audit.record("memory.forgotten", actor="user",
                 arguments={"id": memory_id, "reason": reason})
    return True


def forget_all(confirm: bool = False) -> int:
    if not confirm:
        raise MemoryRefused(
            what="Nothing was deleted.",
            why="Erasing all memory needs explicit confirmation.",
            needs="The --yes flag.",
            fix="Run:  assistant memory forget --all --yes",
        )
    conn = db.connect()
    count = db.one(conn, "SELECT COUNT(*) AS n FROM memory WHERE deleted=0")["n"]
    conn.execute("UPDATE memory SET deleted=1, updated_at=? WHERE deleted=0", (_now(),))
    conn.execute("DELETE FROM memory_fts")
    audit.record("memory.wiped", actor="user", arguments={"count": count})
    return int(count)


def export() -> List[Dict[str, Any]]:
    """Everything, in a plain structure you can save or inspect."""
    return list_all(limit=100_000, include_deleted=True)


def stats() -> Dict[str, Any]:
    conn = db.connect()
    rows = db.query(conn, "SELECT kind, COUNT(*) AS n FROM memory WHERE deleted=0 "
                          "GROUP BY kind ORDER BY n DESC")
    total = db.one(conn, "SELECT COUNT(*) AS n FROM memory WHERE deleted=0")["n"]
    pinned = db.one(conn, "SELECT COUNT(*) AS n FROM memory WHERE deleted=0 AND pinned=1")["n"]
    deleted = db.one(conn, "SELECT COUNT(*) AS n FROM memory WHERE deleted=1")["n"]
    return {
        "enabled": enabled(),
        "total": int(total),
        "pinned": int(pinned),
        "deleted": int(deleted),
        "by_kind": {r["kind"]: r["n"] for r in rows},
    }
