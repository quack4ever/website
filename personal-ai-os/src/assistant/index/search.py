"""Searching the catalogue.

THREE WAYS TO LOOK
------------------
1. **By name** - "anything called *chemistry*".  Instant.
2. **By content** - "anything mentioning photosynthesis".  Uses SQLite's FTS5
   full-text engine with BM25 ranking, which is the same family of algorithm
   a search engine uses: rare words count for more than common ones.
3. **Related to this file** - takes the most distinctive words out of one
   document and looks for others that share them.

WHAT THIS IS NOT
----------------
This is keyword search, not embedding-based semantic search.  It will not
match "car" to "automobile".  Real semantic search needs an embedding model;
that is listed in ARCHITECTURE.md as future work and is deliberately NOT
claimed here.  Keyword search has the advantage of working offline, instantly,
with no model at all.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .. import db

_TOKEN = re.compile(r"[\w'-]{2,}", re.UNICODE)

#: Very common words carry no signal, so they are dropped from queries.
_STOPWORDS = frozenset("""
a an and are as at be but by for from has have how i if in is it its of on or
that the their there these this to was were what when where which who will with
your you my me do does did not can could should would about into over under
""".split())


def _tokens(text: str, keep_stopwords: bool = False) -> List[str]:
    found = [t.lower() for t in _TOKEN.findall(text or "")]
    if keep_stopwords:
        return found
    return [t for t in found if t not in _STOPWORDS]


def build_match(query: str, mode: str = "all") -> Optional[str]:
    """Turn user text into a safe FTS5 MATCH expression.

    Every token is wrapped in double quotes, which makes it a literal phrase.
    That matters for safety: without it, a query containing FTS5 operators
    (``*``, ``NEAR``, ``^``, or an unbalanced quote) would either crash the
    search or mean something the user did not intend.
    """
    tokens = _tokens(query)
    if not tokens:
        tokens = _tokens(query, keep_stopwords=True)
    if not tokens:
        return None
    joiner = " AND " if mode == "all" else " OR "
    return joiner.join('"%s"' % t.replace('"', '""') for t in tokens[:24])


def by_content(query: str, limit: int = 20, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """Full-text search with BM25 ranking, and a graceful widening step."""
    conn = db.connect()

    def run(match: str) -> List[Dict[str, Any]]:
        sql = (
            "SELECT f.id, f.path, f.name, f.ext, f.kind, f.size, f.indexed_at, "
            "       bm25(files_fts) AS score, "
            "       snippet(files_fts, 2, '<<', '>>', ' ... ', 14) AS excerpt "
            "FROM files_fts JOIN files f ON f.id = files_fts.file_id "
            "WHERE files_fts MATCH ?"
        )
        args: List[Any] = [match]
        if kind:
            sql += " AND f.kind = ?"
            args.append(kind)
        # bm25() returns a NEGATIVE number where more negative = better match,
        # so ascending order puts the best results first.
        sql += " ORDER BY score LIMIT ?"
        args.append(int(limit))
        try:
            return db.rows_to_dicts(db.query(conn, sql, args))
        except Exception:
            return []

    match = build_match(query, mode="all")
    if not match:
        return []
    results = run(match)
    if not results:
        # Nothing matched every word - try "any word" before giving up.
        loose = build_match(query, mode="any")
        if loose and loose != match:
            results = run(loose)
    for row in results:
        row["match_type"] = "content"
    return results


def by_name(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    conn = db.connect()
    like = "%" + query.strip().lower() + "%"
    rows = db.query(
        conn,
        "SELECT id, path, name, ext, kind, size, indexed_at FROM files "
        "WHERE lower(name) LIKE ? ORDER BY mtime DESC LIMIT ?",
        (like, int(limit)),
    )
    out = db.rows_to_dicts(rows)
    for row in out:
        row["match_type"] = "filename"
        row["excerpt"] = ""
    return out


def search(query: str, limit: int = 20, kind: Optional[str] = None) -> Dict[str, Any]:
    """Name matches first (they are usually what you meant), then content."""
    names = by_name(query, limit=limit)
    contents = by_content(query, limit=limit, kind=kind)

    seen = {row["path"] for row in names}
    merged = list(names)
    for row in contents:
        if row["path"] not in seen:
            merged.append(row)
            seen.add(row["path"])

    return {
        "query": query,
        "results": merged[:limit],
        "count": len(merged[:limit]),
        "name_matches": len(names),
        "content_matches": len(contents),
        "note": "Keyword search (BM25). Synonyms are not matched - this is not "
                "embedding-based semantic search.",
    }


def related(path: str, limit: int = 10) -> Dict[str, Any]:
    """Find documents sharing distinctive vocabulary with this one."""
    conn = db.connect()
    row = db.one(conn,
                 "SELECT f.id, f.name, files_fts.body AS body FROM files f "
                 "JOIN files_fts ON files_fts.file_id = f.id WHERE f.path=?",
                 (str(path),))
    if not row:
        return {"path": str(path), "related": [], "count": 0,
                "note": "That file is not in the index yet. Run: assistant index build"}

    counts: Dict[str, int] = {}
    for token in _tokens(row["body"] or "")[:5000]:
        counts[token] = counts.get(token, 0) + 1
    # Words that appear a few times are more distinctive than words that
    # appear once (a typo) or hundreds of times (boilerplate).
    ranked = sorted(((t, n) for t, n in counts.items() if 2 <= n <= 60),
                    key=lambda item: -item[1])[:12]
    if not ranked:
        return {"path": str(path), "related": [], "count": 0,
                "note": "This file has no extracted text to compare."}

    match = " OR ".join('"%s"' % t.replace('"', '""') for t, _ in ranked)
    try:
        rows = db.query(
            conn,
            "SELECT f.path, f.name, f.kind, bm25(files_fts) AS score "
            "FROM files_fts JOIN files f ON f.id = files_fts.file_id "
            "WHERE files_fts MATCH ? AND f.id != ? ORDER BY score LIMIT ?",
            (match, row["id"], int(limit)),
        )
    except Exception:
        rows = []
    return {
        "path": str(path),
        "keywords": [t for t, _ in ranked],
        "related": db.rows_to_dicts(rows),
        "count": len(rows),
        "note": "Similarity is shared distinctive vocabulary, not meaning.",
    }
