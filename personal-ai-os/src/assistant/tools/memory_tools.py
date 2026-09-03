"""Tools the assistant uses to remember and recall.

Note the asymmetry: reading memory is LOW risk, writing is MEDIUM.  That is
deliberate - what the assistant believes about you shapes everything it does
later, so adding to it is a real change, not a free action.
"""
from __future__ import annotations

from typing import Any, Dict

from ..memory import store
from .registry import ExecContext, ToolSpec, register


def _remember(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    item = store.remember(
        args["text"], kind=args.get("kind", "fact"),
        confidence=float(args.get("confidence", 0.7)),
        source="conversation:%s" % (ctx.session_id or "cli"),
        cfg=ctx.config,
    )
    return {"stored": True, "id": item.get("id"), "kind": item.get("kind"),
            "text": item.get("text"),
            "note": "You can see or remove this with 'assistant memory list'."}


def _recall(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    if not store.enabled(ctx.config):
        return {"enabled": False, "memories": [], "count": 0,
                "note": "Memory is switched off, so there is nothing to recall."}
    query = args.get("query", "")
    items = store.search(query, limit=int(args.get("limit", 10))) if query \
        else store.list_all(limit=int(args.get("limit", 10)))
    return {
        "enabled": True,
        "count": len(items),
        "memories": [
            {"id": i["id"], "kind": i["kind"], "text": i["text"],
             "confidence": i["effective_confidence"], "source": i.get("source"),
             "pinned": bool(i.get("pinned"))}
            for i in items
        ],
    }


def _forget(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    ok = store.forget(int(args["id"]), reason="assistant requested")
    return {"forgotten": ok, "id": args["id"]}


register(ToolSpec(
    name="memory_remember", capability="memory.write",
    description=("Store one short fact or preference about the user for future "
                 "conversations. Never store passwords, keys or other secrets - "
                 "these are refused."),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 2000},
            "kind": {"type": "string", "enum": list(store.KINDS), "default": "fact"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.7},
        },
        "required": ["text"], "additionalProperties": False,
    },
    handler=_remember,
    summarize=lambda a: "remember: %r (as a %s)" % (a.get("text", "")[:100],
                                                    a.get("kind", "fact")),
))

register(ToolSpec(
    name="memory_recall", capability="memory.read",
    description="Look up what is remembered about the user, optionally filtered by keyword.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"},
                       "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}},
        "additionalProperties": False,
    },
    handler=_recall,
    summarize=lambda a: "recall memories about %r" % (a.get("query") or "anything"),
))

register(ToolSpec(
    name="memory_forget", capability="memory.write",
    description="Remove one stored memory by its id.",
    parameters={"type": "object", "properties": {"id": {"type": "integer", "minimum": 1}},
                "required": ["id"], "additionalProperties": False},
    handler=_forget,
    summarize=lambda a: "forget memory #%s" % a.get("id"),
))
