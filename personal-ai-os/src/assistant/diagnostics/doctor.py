"""Self-diagnosis: "why can't you do X?"

THE PRINCIPLE
-------------
When something does not work, the assistant should be able to tell you WHICH
part is broken, WHY, and WHAT TO TYPE to fix it.  Every check below returns
those three things.  A check that just says "FAILED" would be useless.

Checks are ordered from most fundamental to most optional, so the first
failure you see is usually the real cause of everything after it.
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
from typing import Any, Dict, List, NamedTuple

from .. import config as config_module, db, paths, tools
from ..ai import Router
from ..logging_setup import get
from ..security import killswitch, pathguard, policy
from ..version import __version__

log = get(__name__)

OK = "ok"
WARN = "warning"
FAIL = "problem"


class Check(NamedTuple):
    name: str
    status: str
    detail: str
    fix: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return self._asdict()


def _python_check() -> Check:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 9):
        return Check("Python version", FAIL,
                     "Python %d.%d is too old." % (major, minor),
                     "Install Python 3.9 or newer (macOS ships 3.9 at "
                     "/usr/bin/python3), then re-run ./install.sh")
    return Check("Python version", OK, "Python %s at %s"
                 % (platform.python_version(), sys.executable))


def _platform_check() -> Check:
    if paths.is_macos():
        return Check("Platform", OK, "macOS on %s" % platform.machine())
    return Check("Platform", WARN,
                 "This is %s, not macOS." % platform.system(),
                 "The core (files, index, memory, planning, permissions) works "
                 "here. Calendar, Reminders, Notifications, Spotlight and the "
                 "launchd service are macOS-only and are hidden.")


def _directories_check() -> Check:
    try:
        paths.ensure_dirs()
    except OSError as exc:
        return Check("Directories", FAIL, "Could not create %s: %s" % (paths.home(), exc),
                     "Check you have permission to write to your home folder.")
    missing = [str(p) for p in (paths.home(), paths.home() / "db",
                                paths.logs_dir(), paths.run_dir())
               if not p.exists()]
    if missing:
        return Check("Directories", FAIL, "Missing: %s" % ", ".join(missing),
                     "Re-run ./install.sh")
    return Check("Directories", OK, "All present under %s" % paths.home())


def _database_check() -> Check:
    try:
        conn = db.connect()
        version = db.schema_version(conn)
        conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        return Check("Database", FAIL, "Could not open the database: %s" % exc,
                     "Check %s is readable. If it is corrupt, move it aside and "
                     "re-run ./install.sh - you will lose memory and the index, "
                     "but not your own files." % paths.db_path())
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts_probe")
        fts = "with full-text search"
    except Exception:
        return Check("Database", FAIL,
                     "SQLite is missing the FTS5 extension.",
                     "Your Python's SQLite was built without FTS5. Install "
                     "Python from python.org or Homebrew and re-run ./install.sh")
    return Check("Database", OK, "schema v%d, %s" % (version, fts))


def _config_check() -> Check:
    try:
        cfg = config_module.load()
    except Exception as exc:
        return Check("Configuration", FAIL, str(exc),
                     "Fix or delete %s" % paths.config_path())
    problems = cfg.validate()
    if problems:
        return Check("Configuration", FAIL, "; ".join(problems),
                     "Run 'assistant config show' and correct the values.")
    return Check("Configuration", OK,
                 "valid; autonomy mode is '%s'" % cfg.autonomy_mode)


def _scopes_check() -> Check:
    scopes = pathguard.list_scopes()
    if not scopes:
        return Check("Folder access", WARN,
                     "No folders have been granted, so the assistant can read "
                     "nothing at all.",
                     "Grant one:  assistant permissions grant ~/Documents\n"
                     "        or:  assistant permissions grant ~/Downloads --mode readwrite")
    return Check("Folder access", OK,
                 "%d folder(s): %s" % (len(scopes),
                                       ", ".join("%s (%s)" % (s.path, s.mode)
                                                 for s in scopes[:5])))


def _model_check() -> Check:
    status = Router().status()
    available = [name for name, info in status["roles"].items() if info.get("available")]
    if available:
        return Check("AI model", OK,
                     "available for: %s" % ", ".join(sorted(available)))
    reasons = []
    for role, info in status["roles"].items():
        if info.get("fix"):
            reasons.append("%s -> %s" % (role, info["reason"]))
            break
    return Check("AI model", WARN,
                 "No model is reachable. %s" % ("; ".join(reasons) if reasons else ""),
                 "Either set a cloud key:  export ANTHROPIC_API_KEY=sk-ant-...\n"
                 "or install a local model: https://ollama.com then "
                 "'ollama pull llama3.1' and\n"
                 "  assistant config set ai.roles.reasoning ollama\n"
                 "Everything except the thinking part works without a model.")


def _tools_check() -> Check:
    try:
        tools.load_all()
    except Exception as exc:
        return Check("Tools", FAIL, "Could not load the tool modules: %s" % exc,
                     "This is a bug - run 'assistant logs --level error'.")
    specs = tools.list_specs()
    usable = tools.provider_schemas()
    return Check("Tools", OK, "%d registered, %d usable on this platform"
                 % (len(specs), len(usable)))


def _daemon_check() -> Check:
    from ..daemon import server
    if server.is_running():
        return Check("Background service", OK, "running, socket at %s"
                     % paths.socket_path())
    if paths.is_macos():
        from ..macos import launchd
        state = launchd.status()
        if state.get("plist_exists"):
            return Check("Background service", WARN,
                         "Installed but not running.",
                         "Start it:  assistant daemon start")
        return Check("Background service", WARN,
                     "Not installed as a login service.",
                     "Install it:  assistant daemon install\n"
                     "(Optional - the CLI works without it, just without "
                     "scheduled background work.)")
    return Check("Background service", WARN, "Not running.",
                 "Start it:  assistant daemon start   (optional)")


def _killswitch_check() -> Check:
    state = killswitch.status()
    if state:
        return Check("Emergency stop", WARN,
                     "ENGAGED since %s (%s). Nothing will run."
                     % (state.get("engaged_at"), state.get("reason")),
                     "Clear it:  assistant resume")
    return Check("Emergency stop", OK, "not engaged")


def _extraction_check() -> Check:
    have = []
    missing = []
    for name, what in (("pdftotext", "PDF text"), ("textutil", "RTF and DOC text")):
        (have if shutil.which(name) else missing).append("%s (%s)" % (name, what))
    try:
        import pypdf  # noqa: F401
        have.append("pypdf (PDF text)")
        missing = [m for m in missing if not m.startswith("pdftotext")]
    except ImportError:
        pass
    if not missing:
        return Check("Document readers", OK, "all present: " + ", ".join(have))
    return Check("Document readers", WARN,
                 "Not installed: %s" % ", ".join(missing),
                 "Optional. Plain text, Markdown, HTML and Office files "
                 "(.docx/.pptx/.xlsx) work without anything extra. For PDFs:  "
                 "brew install poppler")


def _macos_permissions_check() -> Check:
    if not paths.is_macos():
        return Check("macOS app permissions", OK, "not applicable off macOS")
    from ..macos import tcc
    report = tcc.check_all(timeout=5)
    granted, missing = report.get("granted", []), report.get("missing", [])
    if not missing:
        return Check("macOS app permissions", OK, "all granted: " + ", ".join(granted))
    return Check("macOS app permissions", WARN,
                 "Not granted: %s" % ", ".join(missing),
                 "Only needed for those specific features. To be asked now, "
                 "run FROM YOUR TERMINAL:  assistant doctor --request-permissions")


def run(request_permissions: bool = False) -> Dict[str, Any]:
    """Run every check.  Returns a structured report."""
    checks: List[Check] = [
        _python_check(), _platform_check(), _directories_check(),
        _database_check(), _config_check(), _tools_check(), _scopes_check(),
        _model_check(), _extraction_check(), _daemon_check(),
        _killswitch_check(),
    ]

    permission_report = None
    if request_permissions and paths.is_macos():
        from ..macos import tcc
        # Running the probes IS the request: macOS shows its consent dialog the
        # first time a program asks, and running from the terminal is what
        # makes that dialog appear somewhere you can click it.
        permission_report = tcc.check_all(timeout=30)
        checks.append(_macos_permissions_check())
    elif paths.is_macos():
        checks.append(_macos_permissions_check())

    problems = [c for c in checks if c.status == FAIL]
    warnings = [c for c in checks if c.status == WARN]
    return {
        "version": __version__,
        "healthy": not problems,
        "checks": [c.to_dict() for c in checks],
        "problems": len(problems),
        "warnings": len(warnings),
        "summary": ("Everything essential is working."
                    if not problems else
                    "%d problem(s) need fixing." % len(problems)),
        "permission_report": permission_report,
        "paths": {k: str(v) for k, v in paths.all_paths().items()},
    }
