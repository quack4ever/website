"""Configuration: the system's settings file.

ANALOGY
-------
Config is the dial panel on a machine.  The machine ships with sensible dial
positions (the DEFAULTS below).  You can turn any dial, and your choices are
saved to ``config.json``.  If a future version adds a new dial, your file
still works - we merge your saved settings on top of the new defaults rather
than replacing them.

WHAT IS JSON?  It is just text that stores nested settings, using ``{}`` for
groups and ``"key": value`` for each setting.  You can open config.json in any
text editor.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths
from .errors import ConfigError

# --------------------------------------------------------------------------
# Defaults.  Every one of these is a deliberate, conservative choice.
# --------------------------------------------------------------------------
DEFAULTS: Dict[str, Any] = {
    "version": 1,
    "autonomy": {
        # Start in the safest useful mode: it may propose anything, but it
        # asks before every single action.
        "mode": "assisted",
        # Hard ceiling that no mode may exceed.  CRITICAL is never auto-run.
        "never_auto_approve": ["critical"],
        "approval_timeout_seconds": 900,
    },
    "memory": {
        "enabled": True,
        "decay_half_life_days": 90,
        "max_entries": 5000,
        "min_confidence_to_recall": 0.25,
        "recall_limit": 12,
    },
    "ai": {
        # Which provider each *role* uses.  This is the multi-model router.
        # Defaults keep everything on the most capable model; switch the
        # "fast" role to a cheaper model yourself if you want to save money
        # (see docs/CONFIG.md).
        "roles": {
            "reasoning": "anthropic",
            "planning": "anthropic",
            "fast": "anthropic",
            "coding": "anthropic",
            "vision": "anthropic",
            "local": "ollama",
        },
        "providers": {
            "anthropic": {
                "type": "anthropic",
                "model": "claude-opus-5",
                "api_key_env": "ANTHROPIC_API_KEY",
                "max_tokens": 16000,
                "effort": "high",
                "thinking": "adaptive",
            },
            "ollama": {
                "type": "ollama",
                "base_url": "http://127.0.0.1:11434",
                "model": "llama3.1",
                "max_tokens": 4096,
            },
            "openai_compat": {
                "type": "openai_compat",
                "base_url": "",
                "model": "",
                "api_key_env": "OPENAI_API_KEY",
                "max_tokens": 8192,
            },
            "offline": {"type": "offline"},
        },
        # If you set this to false, NOTHING is ever sent off your Mac.  Every
        # cloud provider immediately reports itself unavailable.
        "allow_cloud": True,
        # Print a one-line notice whenever data leaves the machine.
        "disclose_cloud_calls": True,
        # Never include raw file contents in a cloud request unless true.
        "send_file_content_to_cloud": True,
        "max_tool_iterations": 12,
    },
    "index": {
        "max_file_bytes": 5_000_000,
        "max_extract_chars": 200_000,
        "follow_symlinks": False,
        "exclude_globs": [
            "**/node_modules/**", "**/.git/**", "**/.venv/**", "**/venv/**",
            "**/__pycache__/**", "**/Library/**", "**/.Trash/**",
            "**/*.app/**", "**/DerivedData/**", "**/.cache/**",
        ],
        "text_extensions": [
            ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json",
            ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log", ".html", ".htm",
            ".xml", ".py", ".js", ".ts", ".tsx", ".jsx", ".swift", ".c", ".h",
            ".cpp", ".hpp", ".java", ".rb", ".go", ".rs", ".sh", ".sql",
            ".tex", ".bib", ".srt", ".vtt",
        ],
    },
    "shell": {
        # Only these binaries may ever be executed, and only after approval.
        # Nothing that writes, deletes, downloads, or elevates is here.
        "allowed_binaries": [
            "echo", "ls", "pwd", "date", "whoami", "uname", "sw_vers",
            "df", "du", "wc", "head", "tail", "cat", "file", "stat",
            "sort", "uniq", "grep", "find", "which", "mdfind", "sysctl",
            "system_profiler", "networksetup", "defaults", "hostname",
        ],
        "timeout_seconds": 30,
        "max_output_bytes": 200_000,
    },
    "privacy": {
        "redact_secrets_in_logs": True,
        "audit_arguments": True,
    },
    "planner": {
        "candidates": 5,
        "beam_width": 3,
        "max_depth": 4,
        "simulations": 300,
        "temperature": 0.35,
        "risk_ceiling": 0.65,
        "seed": None,
    },
    "daemon": {
        "enabled": True,
        "tick_seconds": 60,
        "idle_shutdown_seconds": 0,
    },
    "ui": {"color": True, "verbose_reasoning": True},
}

_VALID_MODES = ["observe", "suggest", "assisted", "supervised", "autonomous"]


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Copy `base`, then lay `override` on top, one nested key at a time.

    This is why upgrading never wipes your settings: new default keys appear,
    and your saved keys win wherever they exist.
    """
    out = copy.deepcopy(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    """Loaded settings, with dotted-path access: ``cfg.get("ai.roles.fast")``."""

    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None) -> None:
        self.data = data
        self.path = path

    # -- reading ----------------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            if part not in node or not isinstance(node[part], dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value

    # -- convenience ------------------------------------------------------
    @property
    def autonomy_mode(self) -> str:
        return str(self.get("autonomy.mode", "assisted"))

    @property
    def memory_enabled(self) -> bool:
        return bool(self.get("memory.enabled", True))

    @property
    def allow_cloud(self) -> bool:
        return bool(self.get("ai.allow_cloud", True))

    # -- validation -------------------------------------------------------
    def validate(self) -> List[str]:
        """Return a list of problems.  Empty list means the config is sane."""
        problems: List[str] = []
        mode = self.autonomy_mode
        if mode not in _VALID_MODES:
            problems.append(
                "autonomy.mode is '%s' but must be one of: %s"
                % (mode, ", ".join(_VALID_MODES))
            )
        roles = self.get("ai.roles", {}) or {}
        providers = self.get("ai.providers", {}) or {}
        for role, provider_name in roles.items():
            if provider_name not in providers:
                problems.append(
                    "ai.roles.%s points at provider '%s', which is not defined "
                    "in ai.providers" % (role, provider_name)
                )
        never = [str(x).lower() for x in (self.get("autonomy.never_auto_approve") or [])]
        if "critical" not in never:
            problems.append(
                "autonomy.never_auto_approve must contain 'critical' - "
                "irreversible actions always require your approval"
            )
        for key in ("planner.candidates", "planner.beam_width", "planner.simulations"):
            value = self.get(key)
            if not isinstance(value, int) or value < 1:
                problems.append("%s must be a positive whole number" % key)
        return problems

    # -- persistence ------------------------------------------------------
    def save(self, path: Optional[Path] = None) -> Path:
        target = path or self.path or paths.config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        os.replace(tmp, target)  # atomic: never leaves a half-written config
        try:
            target.chmod(paths.FILE_MODE)
        except OSError:
            pass
        self.path = target
        return target


def load(path: Optional[Path] = None) -> Config:
    """Read config.json (or start from defaults if it does not exist yet)."""
    target = path or paths.config_path()
    if not target.exists():
        return Config(copy.deepcopy(DEFAULTS), target)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            what="Your configuration file could not be read.",
            why="config.json is not valid JSON: %s" % exc,
            tried="Reading %s" % target,
            needs="A syntactically valid JSON file.",
            fix="Open %s in a text editor and fix the error on line %d, or "
                "delete the file to start again from defaults."
                % (target, getattr(exc, "lineno", 0)),
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigError(
            what="Your configuration file has the wrong shape.",
            why="The top level of config.json must be an object ({...}).",
            tried="Reading %s" % target,
            needs="A JSON object at the top level.",
            fix="Delete %s to regenerate defaults." % target,
        )
    return Config(_deep_merge(DEFAULTS, raw), target)


def init_default(path: Optional[Path] = None, force: bool = False) -> Path:
    """Write a fresh default config.  Used by the installer."""
    target = path or paths.config_path()
    if target.exists() and not force:
        return target
    return Config(copy.deepcopy(DEFAULTS), target).save(target)
