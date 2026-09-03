"""Finding duplicate files.

HOW WE KNOW TWO FILES ARE THE SAME
----------------------------------
Not by name - "essay.docx" and "essay final REAL.docx" can be identical, and
two different photos can share a name.  We compare a **SHA-256 hash**: a short
fingerprint computed from every byte.  If two files have the same fingerprint
they are the same file, byte for byte.

We never delete anything here.  This only *reports*.  Deleting is a separate,
approval-gated action, and the report deliberately marks the oldest copy as
the one to keep so a careless "delete the rest" keeps the original.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .. import db


def find_duplicates(min_size: int = 1024, limit: int = 200) -> Dict[str, Any]:
    conn = db.connect()
    groups = db.query(
        conn,
        "SELECT content_hash, COUNT(*) AS copies, SUM(size) AS total_bytes "
        "FROM files WHERE content_hash IS NOT NULL AND size >= ? "
        "GROUP BY content_hash HAVING copies > 1 "
        "ORDER BY (COUNT(*) - 1) * MAX(size) DESC LIMIT ?",
        (int(min_size), int(limit)),
    )

    out: List[Dict[str, Any]] = []
    reclaimable = 0
    for group in groups:
        rows = db.query(
            conn,
            "SELECT path, name, size, mtime FROM files WHERE content_hash=? "
            "ORDER BY mtime ASC",
            (group["content_hash"],),
        )
        files = db.rows_to_dicts(rows)
        if len(files) < 2:
            continue
        wasted = int(files[0]["size"]) * (len(files) - 1)
        reclaimable += wasted
        out.append({
            "hash": group["content_hash"][:16],
            "copies": len(files),
            "size_each": int(files[0]["size"]),
            "wasted_bytes": wasted,
            # Oldest first: this is the original, and the safe one to keep.
            "keep": files[0]["path"],
            "duplicates": [f["path"] for f in files[1:]],
        })

    return {
        "groups": out,
        "group_count": len(out),
        "reclaimable_bytes": reclaimable,
        "reclaimable_mb": round(reclaimable / 1e6, 1),
        "note": "Nothing has been deleted. 'keep' is the oldest copy.",
    }
