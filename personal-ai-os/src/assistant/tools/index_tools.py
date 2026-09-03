"""Tools that query the file catalogue.

A NOTE ON TRUST
---------------
Search results contain filenames and excerpts that came off your disk, so a
malicious document could put an injection attempt in its own text - or even in
its *filename* - and try to reach the model that way.  Results are therefore
wrapped as untrusted content, exactly like a file read.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from ..index import dedupe, indexer, search as search_module
from ..security import injection
from .registry import ExecContext, ToolSpec, register


def _wrap(payload: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Attach a model-safe rendering of anything that came from disk."""
    wrapped = injection.wrap(json.dumps(payload, indent=2, default=str)[:60_000],
                             source=source, content_id="index")
    payload = dict(payload)
    payload["untrusted"] = wrapped["wrapped"]
    payload["injection_signals"] = wrapped["signals"]
    payload["flagged"] = wrapped["flagged"]
    return payload


def _search(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    result = search_module.search(args["query"], limit=int(args.get("limit", 20)),
                                  kind=args.get("kind") or None)
    if result["count"] == 0:
        status = indexer.status()
        if status["files"] == 0:
            result["hint"] = ("Nothing is indexed yet. Run 'assistant index build' "
                              "after granting a folder with "
                              "'assistant permissions grant ~/Documents'.")
        else:
            result["hint"] = ("%d files are indexed but none matched. Try fewer "
                              "or different words - this is keyword search, so "
                              "it does not match synonyms." % status["files"])
    return _wrap(result, "file index search: " + str(args["query"]))


def _related(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    from ..security import pathguard
    path = pathguard.require(args["path"])
    return _wrap(search_module.related(str(path), limit=int(args.get("limit", 10))),
                 "related files for " + str(path))


def _status(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    return indexer.status()


def _build(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    report = indexer.index_scopes(only=args.get("folder") or None,
                                  full=bool(args.get("full", False)))
    return report.to_dict()


def _duplicates(args: Dict[str, Any], ctx: ExecContext) -> Dict[str, Any]:
    return dedupe.find_duplicates(min_size=int(args.get("min_size_bytes", 1024)),
                                  limit=int(args.get("limit", 100)))


register(ToolSpec(
    name="index_search", capability="index.query",
    description=("Search the catalogue of your indexed files by keyword, across "
                 "filenames and file contents. Fast, local, and works offline."),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "kind": {"type": "string",
                     "enum": ["document", "spreadsheet", "presentation", "image",
                              "media", "code", "archive", "other"]},
        },
        "required": ["query"], "additionalProperties": False,
    },
    handler=_search,
    summarize=lambda a: "search the file index for %r" % a.get("query"),
))

register(ToolSpec(
    name="index_related", capability="index.query",
    description="Find indexed files that share distinctive vocabulary with a given file.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "minLength": 1},
                       "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}},
        "required": ["path"], "additionalProperties": False,
    },
    handler=_related, path_args=("path",),
    summarize=lambda a: "find files related to %s" % a.get("path"),
))

register(ToolSpec(
    name="index_status", capability="index.query",
    description="How many files are catalogued, of what kinds, and when it was last updated.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    handler=_status,
    summarize=lambda a: "check the state of the file index",
))

register(ToolSpec(
    name="index_build", capability="index.build",
    description="Scan your granted folders and update the catalogue. Incremental by default.",
    parameters={
        "type": "object",
        "properties": {"folder": {"type": "string"},
                       "full": {"type": "boolean", "default": False}},
        "additionalProperties": False,
    },
    handler=_build, path_args=("folder",),
    summarize=lambda a: "rebuild the file index for %s" % (a.get("folder") or "all granted folders"),
))

register(ToolSpec(
    name="find_duplicates", capability="index.query",
    description="Report groups of byte-identical files. Reports only - deletes nothing.",
    parameters={
        "type": "object",
        "properties": {"min_size_bytes": {"type": "integer", "minimum": 0, "default": 1024},
                       "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}},
        "additionalProperties": False,
    },
    handler=_duplicates,
    summarize=lambda a: "look for duplicate files",
))
