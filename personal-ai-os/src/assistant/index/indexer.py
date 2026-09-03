"""Building the catalogue of your files.

ANALOGY
-------
Imagine a library with no card catalogue.  To find a book you would walk
every shelf.  The indexer walks the shelves *once*, writes a card for each
book (name, size, date, and the words inside), and files the cards.  After
that, finding something takes milliseconds instead of minutes.

INCREMENTAL BY DESIGN
---------------------
Re-running the index does not redo everything.  For each file we compare the
size and modification time against the card we already have.  Unchanged files
are skipped, so the second scan takes seconds.

IT ONLY EVER LOOKS INSIDE FOLDERS YOU GRANTED
---------------------------------------------
The walk starts from your scopes and nowhere else, and every candidate file is
re-checked against the forbidden list before being opened.
"""
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional

from .. import config as config_module, db
from ..logging_setup import get
from ..security import pathguard
from . import extract as extract_module

log = get(__name__)


class IndexReport(NamedTuple):
    scanned: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    removed: int = 0
    seconds: float = 0.0
    errors: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        data = self._asdict()
        data["errors"] = (self.errors or [])[:10]
        return data


def _hash_file(path: Path, limit: int = 20_000_000) -> Optional[str]:
    """SHA-256 of the contents, used to spot exact duplicates."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            read = 0
            for chunk in iter(lambda: handle.read(262_144), b""):
                digest.update(chunk)
                read += len(chunk)
                if read > limit:
                    return None  # too big to hash cheaply
    except OSError:
        return None
    return digest.hexdigest()


def _excluded(path: Path, patterns: List[str]) -> bool:
    text = str(path)
    return any(fnmatch(text, pattern) for pattern in patterns)


def index_scopes(
    only: Optional[str] = None,
    full: bool = False,
    progress: Optional[Callable[[str, int], None]] = None,
) -> IndexReport:
    """Scan every granted folder (or just `only`) and update the catalogue."""
    started = time.time()
    cfg = config_module.load()
    conn = db.connect()

    excludes = list(cfg.get("index.exclude_globs", []) or [])
    max_bytes = int(cfg.get("index.max_file_bytes", 5_000_000))
    max_chars = int(cfg.get("index.max_extract_chars", 200_000))
    follow_links = bool(cfg.get("index.follow_symlinks", False))

    if only:
        roots = [pathguard.require(only)]
    else:
        roots = [s.path for s in pathguard.list_scopes()]
    if not roots:
        return IndexReport(errors=["No folders have been granted. Run: "
                                   "assistant permissions grant ~/Documents"])

    scanned = added = updated = unchanged = skipped = failed = 0
    errors: List[str] = []
    seen_paths: List[str] = []

    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_links):
            here = Path(dirpath)
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".")
                           and not _excluded(here / d, excludes)]
            for filename in filenames:
                if filename.startswith("."):
                    continue
                path = here / filename
                scanned += 1
                if progress and scanned % 200 == 0:
                    progress(str(path), scanned)

                if _excluded(path, excludes) or pathguard.forbidden_reason(path):
                    skipped += 1
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    skipped += 1
                    continue
                if not path.is_file():
                    skipped += 1
                    continue

                seen_paths.append(str(path))
                existing = db.one(
                    conn, "SELECT id, size, mtime FROM files WHERE path=?", (str(path),))
                if existing and not full \
                        and int(existing["size"]) == stat.st_size \
                        and abs(float(existing["mtime"]) - stat.st_mtime) < 1.0:
                    unchanged += 1
                    continue

                if stat.st_size > max_bytes:
                    _store(conn, path, stat, None,
                           extract_module.Extraction(
                               "", "skipped", False,
                               "file is larger than the %d byte limit "
                               "(index.max_file_bytes)" % max_bytes))
                    skipped += 1
                    continue

                result = extract_module.extract(path, max_chars=max_chars)
                digest = _hash_file(path)
                try:
                    _store(conn, path, stat, digest, result)
                except Exception as exc:  # one bad file must not stop the scan
                    failed += 1
                    errors.append("%s: %s" % (path.name, exc))
                    continue

                if not result.ok and result.error:
                    failed += 1
                    if len(errors) < 50:
                        errors.append("%s: %s" % (path.name, result.error))
                if existing:
                    updated += 1
                else:
                    added += 1

    removed = _remove_missing(conn, roots, seen_paths)
    report = IndexReport(scanned, added, updated, unchanged, skipped, failed,
                         removed, round(time.time() - started, 2), errors)
    log.info("index complete: %s", report.to_dict())
    return report


def _store(conn: Any, path: Path, stat: os.stat_result, digest: Optional[str],
           result: extract_module.Extraction) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO files(path,name,ext,size,mtime,content_hash,kind,indexed_at,"
        "has_text,chars,error) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET name=excluded.name, ext=excluded.ext, "
        "size=excluded.size, mtime=excluded.mtime, content_hash=excluded.content_hash, "
        "kind=excluded.kind, indexed_at=excluded.indexed_at, "
        "has_text=excluded.has_text, chars=excluded.chars, error=excluded.error",
        (str(path), path.name, path.suffix.lower(), stat.st_size, stat.st_mtime,
         digest, extract_module.classify(path), now,
         1 if result.ok and result.text else 0, len(result.text),
         result.error),
    )
    row = db.one(conn, "SELECT id FROM files WHERE path=?", (str(path),))
    file_id = int(row["id"])
    # Replace rather than update: FTS5 external-content tables are fiddly, and
    # delete+insert is simple, correct, and fast enough at this scale.
    conn.execute("DELETE FROM files_fts WHERE file_id=?", (file_id,))
    conn.execute("INSERT INTO files_fts(file_id,name,body) VALUES(?,?,?)",
                 (file_id, path.name, result.text or ""))


def _remove_missing(conn: Any, roots: List[Path], seen: List[str]) -> int:
    """Drop cards for files that no longer exist under the scanned roots."""
    removed = 0
    seen_set = set(seen)
    for root in roots:
        rows = db.query(conn, "SELECT id, path FROM files WHERE path LIKE ?",
                        (str(root) + os.sep + "%",))
        for row in rows:
            if row["path"] in seen_set:
                continue
            if Path(row["path"]).exists():
                continue
            conn.execute("DELETE FROM files_fts WHERE file_id=?", (row["id"],))
            conn.execute("DELETE FROM files WHERE id=?", (row["id"],))
            removed += 1
    return removed


def status() -> Dict[str, Any]:
    """What is in the catalogue right now."""
    conn = db.connect()
    total = db.one(conn, "SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes FROM files")
    with_text = db.one(conn, "SELECT COUNT(*) AS n FROM files WHERE has_text=1")
    failed = db.one(conn, "SELECT COUNT(*) AS n FROM files WHERE error IS NOT NULL")
    by_kind = db.query(
        conn, "SELECT kind, COUNT(*) AS n FROM files GROUP BY kind ORDER BY n DESC")
    latest = db.one(conn, "SELECT MAX(indexed_at) AS last FROM files")
    return {
        "files": int(total["n"]),
        "total_bytes": int(total["bytes"]),
        "with_extracted_text": int(with_text["n"]),
        "unreadable": int(failed["n"]),
        "by_kind": {r["kind"]: r["n"] for r in by_kind},
        "last_indexed": latest["last"],
        "scopes": [str(s.path) for s in pathguard.list_scopes()],
    }


def clear() -> int:
    conn = db.connect()
    count = db.one(conn, "SELECT COUNT(*) AS n FROM files")["n"]
    conn.execute("DELETE FROM files_fts")
    conn.execute("DELETE FROM files")
    return int(count)
