"""Spotlight search, via `mdfind`.

WHAT IS SPOTLIGHT?
------------------
macOS already indexes your files continuously - that is what makes the search
in the top-right corner instant.  ``mdfind`` is the command-line door to that
same index.

WHY USE IT WHEN WE HAVE OUR OWN INDEX?
--------------------------------------
They complement each other:

  Spotlight    knows about EVERY file macOS has indexed, including formats we
               cannot read ourselves (Pages, Keynote, photos with recognised
               text), and it is always up to date.
  Our index    only covers folders you granted, but it stores the extracted
               text so we can rank, excerpt and compare documents.

So: Spotlight to find candidates fast, our index to reason about them.

IMPORTANT: results are still filtered through the path guard.  Spotlight will
happily tell us about files outside your granted folders; we drop those.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import paths
from ..errors import PlatformUnsupported
from ..security import pathguard


def available() -> bool:
    return paths.is_macos() and bool(shutil.which("mdfind"))


def search(query: str, only_in: Optional[str] = None, limit: int = 50,
           name_only: bool = False) -> Dict[str, Any]:
    """Ask Spotlight, then keep only what you have granted access to."""
    if not available():
        raise PlatformUnsupported(
            what="Spotlight search is not available.",
            why="'mdfind' is a macOS program and this is not macOS."
                if not paths.is_macos() else "'mdfind' was not found on PATH.",
            tried="Looking for mdfind",
            needs="macOS",
            fix="Use 'index_search' instead - it works everywhere and searches "
                "the assistant's own catalogue.",
        )

    command = ["mdfind"]
    if only_in:
        root = pathguard.require(only_in)
        command += ["-onlyin", str(root)]
    if name_only:
        command += ["-name", query]
    else:
        command.append(query)

    try:
        completed = subprocess.run(command, capture_output=True, timeout=30,
                                   check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"query": query, "results": [], "count": 0,
                "error": "Spotlight search failed: %s" % exc}

    allowed: List[Dict[str, Any]] = []
    dropped = 0
    for line in completed.stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        # Spotlight does not know about your scopes. We do.
        check = pathguard.check(line)
        if not check.ok or pathguard.forbidden_reason(Path(line)):
            dropped += 1
            continue
        path = Path(line)
        try:
            allowed.append({"path": line, "name": path.name,
                            "size": path.stat().st_size if path.exists() else 0})
        except OSError:
            continue
        if len(allowed) >= limit:
            break

    return {
        "query": query, "results": allowed, "count": len(allowed),
        "excluded_outside_your_scopes": dropped,
        "note": "Spotlight indexes your whole Mac; results outside the folders "
                "you granted were removed before you saw them.",
    }
