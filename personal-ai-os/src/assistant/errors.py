"""Error types that always explain themselves.

WHY THIS FILE EXISTS
--------------------
A normal program fails with something like ``PermissionError: [Errno 13]``.
That tells you almost nothing.  This project has a hard rule: never fail
silently, and never fail uselessly.  Every error carries five fields:

    what    - what failed, in plain words
    why     - the underlying reason
    tried   - what the system actually attempted
    needs   - what would be required to succeed
    fix     - what YOU can do about it, as a concrete step

Think of it as the difference between a car dashboard light that just says
"ERROR" and one that says "your left rear tyre is at 12 PSI, inflate it to 32".
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class AssistantError(Exception):
    """Base class for every error this system raises deliberately."""

    #: Short machine-readable code, e.g. "policy.denied".
    code = "assistant.error"

    def __init__(
        self,
        what: str,
        why: str = "",
        tried: str = "",
        needs: str = "",
        fix: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.what = what
        self.why = why
        self.tried = tried
        self.needs = needs
        self.fix = fix
        self.details = details or {}
        super().__init__(what)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "what": self.what,
            "why": self.why,
            "tried": self.tried,
            "needs": self.needs,
            "fix": self.fix,
            "details": self.details,
        }

    def human(self) -> str:
        """Render the error the way a helpful person would explain it."""
        lines = ["WHAT FAILED:  " + self.what]
        if self.why:
            lines.append("WHY:          " + self.why)
        if self.tried:
            lines.append("WHAT I TRIED: " + self.tried)
        if self.needs:
            lines.append("WHAT I NEED:  " + self.needs)
        if self.fix:
            lines.append("WHAT YOU CAN DO: " + self.fix)
        return "\n".join(lines)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.human()


class ConfigError(AssistantError):
    code = "config.invalid"


class PermissionDenied(AssistantError):
    """The policy engine said no.  This is a *feature*, not a malfunction."""

    code = "policy.denied"


class ApprovalRequired(AssistantError):
    """The action is allowed in principle but needs your explicit yes."""

    code = "policy.approval_required"

    def __init__(self, *args: Any, request_id: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.request_id = request_id


class ScopeError(AssistantError):
    """A path was outside every folder you granted access to."""

    code = "policy.out_of_scope"


class ForbiddenPath(AssistantError):
    """A path is on the non-overridable deny list (keys, keychains, TCC...)."""

    code = "policy.forbidden_path"


class KillSwitchEngaged(AssistantError):
    code = "policy.kill_switch"


class ToolError(AssistantError):
    code = "tool.failed"


class ToolNotFound(ToolError):
    code = "tool.not_found"


class ValidationError(ToolError):
    code = "tool.invalid_arguments"


class ProviderError(AssistantError):
    code = "provider.failed"


class ProviderUnavailable(ProviderError):
    """A model provider is not usable right now, and we say exactly why."""

    code = "provider.unavailable"


class PlatformUnsupported(AssistantError):
    """A macOS-only feature was called somewhere that is not macOS."""

    code = "platform.unsupported"


class DaemonError(AssistantError):
    code = "daemon.failed"
