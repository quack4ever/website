"""Path containment: which parts of your disk exist, as far as the assistant
is concerned.

ANALOGY
-------
Two separate locks on every door.

  Lock 1 - THE FLOOR PLAN ("scopes").  You hand the robot keys to specific
           rooms: "you may read ~/Documents, you may read *and write*
           ~/Downloads".  A fresh install has NO keys at all.  Anything not
           in a granted room simply does not exist to it.

  Lock 2 - THE SEALED VAULT ("forbidden paths").  Some rooms are welded shut
           and no key ever opens them: your keychains, SSH keys, browser
           cookie stores, the assistant's own rulebook.  This list cannot be
           overridden by a scope, by a rule, or by you in a moment of
           enthusiasm.  It fails closed.

THE TRICKY PART: SYMLINKS
-------------------------
A symlink is a signpost: a file in ~/Documents can secretly point at ~/.ssh.
If we only checked the text of the path we would be fooled.  So we always
*resolve* the path first (follow every signpost to the real destination) and
then check where we actually ended up.

THE OTHER TRICKY PART: PREFIX MATCHING
--------------------------------------
"/Users/you/Documents-secret" starts with the text "/Users/you/Documents",
but it is a completely different folder.  Comparing strings would wrongly
allow it.  We compare *path components* instead - ["Users","you","Documents"]
is not a prefix of ["Users","you","Documents-secret"].
"""
from __future__ import annotations

import fnmatch
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence

from .. import db
from ..errors import ForbiddenPath, ScopeError

# --------------------------------------------------------------------------
# The sealed vault.  Nothing unlocks these.
# --------------------------------------------------------------------------
FORBIDDEN_PREFIXES: Sequence[str] = (
    # Credentials and keys
    "~/.ssh", "~/.gnupg", "~/.aws", "~/.azure", "~/.kube", "~/.docker",
    "~/.config/gcloud", "~/.netrc",
    "~/Library/Keychains", "/Library/Keychains", "/Network/Library/Keychains",
    # Apple privacy databases - reading these is exactly the kind of
    # TCC-adjacent snooping we refuse to do.
    "~/Library/Application Support/com.apple.TCC",
    "/Library/Application Support/com.apple.TCC",
    # Private message / mail / browser stores
    "~/Library/Messages", "~/Library/Mail", "~/Library/Safari",
    "~/Library/Cookies", "~/Library/HTTPStorages",
    "~/Library/Application Support/Google/Chrome",
    "~/Library/Application Support/Firefox",
    "~/Library/Application Support/BraveSoftware",
    # System integrity
    "/System", "/usr/libexec", "/private/var/db", "/var/db",
    "/Library/Security", "/etc/sudoers", "/private/etc/sudoers",
    "/etc/master.passwd", "/private/etc/master.passwd",
)

#: Filename patterns that are forbidden wherever they appear.
FORBIDDEN_NAMES: Sequence[str] = (
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "*.pem", "*.p12", "*.pfx",
    "*.keychain", "*.keychain-db", "*.mobileprovision",
    ".env", ".env.*", ".netrc", ".pgpass", ".htpasswd",
    "credentials", "authorized_keys", "known_hosts",
    "shadow", "master.passwd",
)


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(str(path)))


def resolve_path(raw: str) -> Path:
    """Turn whatever the caller gave us into one true absolute path.

    Handles ``~``, relative paths, ``..``, and symlinks.  Never raises for a
    file that does not exist yet - creating a new file is a legitimate action,
    and we still need to know *where* it would be created.
    """
    if raw is None:
        raise ScopeError(
            what="No path was given.",
            why="A file operation was requested without a path.",
            needs="A file or folder path.",
            fix="Say which file you mean.",
        )
    text = str(raw)
    if "\x00" in text:
        raise ForbiddenPath(
            what="That path contains a null byte.",
            why="Null bytes in paths are a classic way to trick programs into "
                "opening a different file than the one they checked.",
            tried="Validating %r" % text[:64],
            needs="A path made of ordinary characters.",
            fix="Remove the null byte from the path.",
        )
    path = _expand(text)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        # strict=False: resolve every symlink in the part that exists, and
        # normalise the rest.  This is what defeats the signpost trick.
        return path.resolve()
    except (OSError, RuntimeError) as exc:
        # RuntimeError = symlink loop.  Treat as hostile.
        raise ForbiddenPath(
            what="That path could not be safely resolved.",
            why=str(exc),
            tried="Resolving %s" % text,
            needs="A path without symlink loops.",
            fix="Check the path for a symlink that points at itself.",
        ) from exc


def is_within(child: Path, parent: Path) -> bool:
    """True when `child` is `parent` or lives inside it.

    Component-wise, so 'Documents-secret' never matches scope 'Documents'.
    """
    cparts, pparts = child.parts, parent.parts
    return len(cparts) >= len(pparts) and cparts[: len(pparts)] == pparts


def forbidden_reason(path: Path) -> Optional[str]:
    """Return why this path is sealed, or None if it is not."""
    for prefix in FORBIDDEN_PREFIXES:
        try:
            root = _expand(prefix).resolve()
        except (OSError, RuntimeError):
            root = _expand(prefix)
        if is_within(path, root):
            return "it is inside a protected location (%s)" % prefix

    # The assistant's own brain: it must never be able to rewrite its own
    # rules, memory or audit log through ordinary file tools.
    from .. import paths as _paths
    try:
        own = _paths.home().resolve()
        if is_within(path, own):
            return ("it is inside the assistant's own data directory - the "
                    "assistant is not allowed to edit its own rules, memory "
                    "or audit log through file tools")
    except (OSError, RuntimeError):
        pass

    name = path.name
    for pattern in FORBIDDEN_NAMES:
        if fnmatch.fnmatch(name, pattern):
            return "files named like '%s' are always protected" % pattern
    return None


def assert_not_forbidden(path: Path) -> None:
    reason = forbidden_reason(path)
    if reason:
        raise ForbiddenPath(
            what="Access to %s is permanently blocked." % path,
            why=reason,
            tried="Checking the path against the non-overridable protected list",
            needs="Nothing - this restriction cannot be lifted.",
            fix="This is intentional. If you genuinely need to work with this "
                "file, do it yourself outside the assistant.",
        )


# --------------------------------------------------------------------------
# Scopes: the folders you granted
# --------------------------------------------------------------------------
class Scope(NamedTuple):
    id: int
    path: Path
    mode: str  # 'read' | 'readwrite'
    note: str


def list_scopes(conn: Optional[Any] = None) -> List[Scope]:
    conn = conn or db.connect()
    rows = db.query(conn, "SELECT id, path, mode, COALESCE(note,'') AS note FROM scopes ORDER BY path")
    return [Scope(r["id"], Path(r["path"]), r["mode"], r["note"]) for r in rows]


def grant(path: str, mode: str = "read", note: str = "", conn: Optional[Any] = None) -> Scope:
    """Give the assistant access to a folder.  Called only from the CLI - never
    from a model response."""
    if mode not in ("read", "readwrite"):
        raise ScopeError(
            what="Invalid scope mode %r." % mode,
            why="A scope is either 'read' or 'readwrite'.",
            needs="mode='read' or mode='readwrite'",
            fix="Re-run with --mode read or --mode readwrite.",
        )
    resolved = resolve_path(path)
    assert_not_forbidden(resolved)
    if not resolved.exists():
        raise ScopeError(
            what="That folder does not exist: %s" % resolved,
            why="You can only grant access to a folder that is really there.",
            tried="Looking for %s" % resolved,
            needs="An existing folder.",
            fix="Check the spelling, or create the folder first.",
        )
    if not resolved.is_dir():
        raise ScopeError(
            what="%s is a file, not a folder." % resolved,
            why="Scopes are granted per folder so the boundary is clear.",
            needs="A folder path.",
            fix="Grant its parent folder instead: %s" % resolved.parent,
        )
    conn = conn or db.connect()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO scopes(path,mode,note,created_at) VALUES(?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET mode=excluded.mode, note=excluded.note",
        (str(resolved), mode, note, now),
    )
    row = db.one(conn, "SELECT id FROM scopes WHERE path=?", (str(resolved),))
    return Scope(int(row["id"]), resolved, mode, note)


def revoke(path: str, conn: Optional[Any] = None) -> bool:
    conn = conn or db.connect()
    resolved = resolve_path(path)
    cur = conn.execute("DELETE FROM scopes WHERE path=?", (str(resolved),))
    return cur.rowcount > 0


class PathCheck(NamedTuple):
    ok: bool
    path: Path
    scope: Optional[Scope]
    reason: str


def check(raw: str, *, need_write: bool = False, conn: Optional[Any] = None) -> PathCheck:
    """The function every file tool must call before touching anything.

    Order matters: we check the sealed vault *first*, so that even a
    mistakenly granted scope cannot expose a protected file.
    """
    path = resolve_path(raw)
    assert_not_forbidden(path)  # raises ForbiddenPath

    scopes = list_scopes(conn)
    if not scopes:
        return PathCheck(
            False, path, None,
            "no folders have been granted yet - the assistant currently has "
            "access to nothing. Run: assistant permissions grant <folder>",
        )

    containing = [s for s in scopes if is_within(path, s.path)]
    if not containing:
        return PathCheck(
            False, path, None,
            "%s is outside every folder you granted. Granted: %s"
            % (path, ", ".join(str(s.path) for s in scopes)),
        )

    # Prefer the most specific (deepest) scope, and among equals prefer the
    # one with more permission - so a readwrite grant on a subfolder wins.
    containing.sort(key=lambda s: (len(s.path.parts), s.mode == "readwrite"), reverse=True)
    scope = containing[0]

    if need_write and not any(s.mode == "readwrite" for s in containing):
        return PathCheck(
            False, path, scope,
            "%s is inside a read-only folder (%s). Writing was requested."
            % (path, scope.path),
        )
    return PathCheck(True, path, scope, "inside granted scope %s (%s)" % (scope.path, scope.mode))


def require(raw: str, *, need_write: bool = False, conn: Optional[Any] = None) -> Path:
    """Like `check`, but raises instead of returning a result object."""
    result = check(raw, need_write=need_write, conn=conn)
    if not result.ok:
        raise ScopeError(
            what="The assistant is not allowed to touch %s." % result.path,
            why=result.reason,
            tried="Checking the path against your granted folders",
            needs="A folder grant covering that path"
                  + (" with write access" if need_write else ""),
            fix="If you want this, run:  assistant permissions grant %s%s"
                % (result.path.parent if result.path.suffix else result.path,
                   " --mode readwrite" if need_write else ""),
        )
    return result.path


def describe() -> Dict[str, Any]:
    """Used by `assistant permissions` and `assistant doctor`."""
    return {
        "scopes": [
            {"id": s.id, "path": str(s.path), "mode": s.mode, "note": s.note}
            for s in list_scopes()
        ],
        "forbidden_prefixes": list(FORBIDDEN_PREFIXES),
        "forbidden_names": list(FORBIDDEN_NAMES),
    }
