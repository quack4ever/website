"""The tool registry: one door, one guard, no side entrances.

HOW A TOOL CALL ACTUALLY WORKS
------------------------------
    1. The model says: "please run file_read with {path: ...}".
    2. We look the tool up.  Unknown name -> stop.
    3. We validate the arguments against the tool's schema.  Wrong shape -> stop.
    4. We work out which real paths this would touch.
    5. We ask the policy engine.  It says allow / ask / deny.
         deny  -> stop, and explain why.
         ask   -> stop, create an approval slip, and tell the user.
         allow -> run it.
    6. We record the outcome in the audit log either way.

Step 5 is not optional and cannot be skipped: ``execute()`` is the only public
way to run a handler, and it always calls the engine.  A handler is a plain
function that assumes it has already been authorised.

THE SWAPPED-ARGUMENT ATTACK
---------------------------
You approve "delete /tmp/junk.txt".  Between your yes and the execution,
something changes the path to "/home/you/thesis.docx".  We defend against this
by storing the exact arguments with the approval and comparing a hash of them
before running.  Approval is for a *specific action*, not a blank cheque.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence

from .. import audit, config as config_module
from ..errors import (AssistantError, PermissionDenied, ToolError,
                      ToolNotFound)
from ..logging_setup import get
from ..security import capabilities, consent, policy
from . import schema as schema_module

log = get(__name__)


class ExecContext(NamedTuple):
    """Who is asking, and under what authority."""
    session_id: Optional[str] = None
    actor: str = "assistant"
    #: Set when re-running a tool the user has just approved.
    approval_id: Optional[str] = None
    #: When true, everything is checked but the handler never runs.
    dry_run: bool = False
    config: Optional[Any] = None

    def engine(self) -> policy.PolicyEngine:
        return policy.PolicyEngine(self.config or config_module.load())


class ToolResult(NamedTuple):
    ok: bool
    tool: str
    data: Any = None
    error: Optional[Dict[str, Any]] = None
    decision: Optional[Dict[str, Any]] = None
    needs_approval: Optional[str] = None
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok, "tool": self.tool, "data": self.data,
            "error": self.error, "decision": self.decision,
            "needs_approval": self.needs_approval, "duration_ms": self.duration_ms,
        }


class ToolSpec(NamedTuple):
    name: str
    capability: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[[Dict[str, Any], ExecContext], Any]
    #: Argument names whose values are filesystem paths.
    path_args: Sequence[str] = ()
    #: Human sentence describing the pending action, shown in the approval slip.
    summarize: Optional[Callable[[Dict[str, Any]], str]] = None
    #: macOS-only tools are hidden (and refuse to run) elsewhere.
    macos_only: bool = False

    @property
    def risk(self) -> str:
        return capabilities.risk_of(self.capability)


_REGISTRY: Dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in _REGISTRY:
        raise RuntimeError("tool %r registered twice" % spec.name)
    if spec.capability not in capabilities.REGISTRY:
        raise RuntimeError(
            "tool %r declares unknown capability %r - add it to "
            "security/capabilities.py first" % (spec.name, spec.capability))
    _REGISTRY[spec.name] = spec
    return spec


def get(name: str) -> Optional[ToolSpec]:
    return _REGISTRY.get(name)


def list_specs(include_macos_only: bool = True) -> List[ToolSpec]:
    from .. import paths
    specs = sorted(_REGISTRY.values(), key=lambda s: s.name)
    if include_macos_only or paths.is_macos():
        return specs
    return [s for s in specs if not s.macos_only]


def clear() -> None:
    """Used by tests only."""
    _REGISTRY.clear()


def provider_schemas(include_macos_only: Optional[bool] = None) -> List[Dict[str, Any]]:
    """The tool list in the shape an AI provider expects."""
    from .. import paths
    if include_macos_only is None:
        include_macos_only = paths.is_macos()
    out = []
    for spec in list_specs(include_macos_only=True):
        if spec.macos_only and not include_macos_only:
            continue
        out.append({
            "name": spec.name,
            "description": "%s  [risk: %s, permission: %s]"
                           % (spec.description, spec.risk, spec.capability),
            "input_schema": spec.parameters,
        })
    return out


def _paths_for(spec: ToolSpec, arguments: Dict[str, Any]) -> List[str]:
    found: List[str] = []
    for key in spec.path_args:
        value = arguments.get(key)
        if isinstance(value, str) and value:
            found.append(value)
        elif isinstance(value, list):
            found.extend(str(v) for v in value if v)
    return found


def canonical_hash(arguments: Dict[str, Any]) -> str:
    """A stable fingerprint of a set of arguments.

    ``sort_keys`` means {"a":1,"b":2} and {"b":2,"a":1} produce the same hash,
    so re-ordering does not look like tampering - but changing a value does.
    """
    blob = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _summary(spec: ToolSpec, arguments: Dict[str, Any]) -> str:
    if spec.summarize:
        try:
            return spec.summarize(arguments)
        except Exception:  # a broken summariser must not block the check
            pass
    return "%s(%s)" % (spec.name, json.dumps(arguments, default=str)[:200])


def execute(name: str, arguments: Optional[Dict[str, Any]] = None,
            ctx: Optional[ExecContext] = None) -> ToolResult:
    """Run a tool, but only if the policy engine says so."""
    started = time.time()
    ctx = ctx or ExecContext()
    arguments = dict(arguments or {})

    spec = _REGISTRY.get(name)
    if spec is None:
        error = ToolNotFound(
            what="There is no tool called '%s'." % name,
            why="The assistant asked for a capability this system does not have.",
            tried="Looking up '%s' in the tool registry" % name,
            needs="One of: %s" % ", ".join(sorted(_REGISTRY)[:25]),
            fix="Nothing to do - the assistant will be told and should try "
                "another approach.",
        )
        audit.record("tool.unknown", actor=ctx.actor, tool=name,
                     outcome="not_found", error=error.what, session_id=ctx.session_id)
        return ToolResult(False, name, error=error.to_dict())

    from .. import paths as paths_module
    if spec.macos_only and not paths_module.is_macos():
        error = AssistantError(
            what="'%s' only works on macOS." % name,
            why="This tool talks to a macOS application, and this machine is "
                "not running macOS.",
            tried="Checking the platform before running the tool",
            needs="macOS",
            fix="Use this feature on your Mac.",
        )
        return ToolResult(False, name, error=error.to_dict())

    # --- validate ---------------------------------------------------------
    try:
        cleaned = schema_module.validate(arguments, spec.parameters, spec.name)
    except AssistantError as exc:
        audit.record("tool.invalid", actor=ctx.actor, tool=name,
                     capability=spec.capability, arguments=arguments,
                     outcome="invalid", error=exc.what, session_id=ctx.session_id)
        return ToolResult(False, name, error=exc.to_dict())

    involved = _paths_for(spec, cleaned)
    summary = _summary(spec, cleaned)

    # --- previously approved? ---------------------------------------------
    if ctx.approval_id:
        approval = consent.get(ctx.approval_id)
        if not approval or approval["status"] != "approved":
            error = AssistantError(
                what="That approval is not valid.",
                why="Approval %s is %s." % (ctx.approval_id,
                                            approval["status"] if approval else "unknown"),
                needs="An approved request id.",
                fix="Run 'assistant approvals' to see what is waiting.",
            )
            return ToolResult(False, name, error=error.to_dict())
        if approval["tool"] != name or \
                canonical_hash(approval["arguments"]) != canonical_hash(cleaned):
            audit.record("tool.approval_mismatch", actor=ctx.actor, tool=name,
                         capability=spec.capability, arguments=cleaned,
                         outcome="blocked", session_id=ctx.session_id,
                         error="arguments differ from what was approved")
            error = AssistantError(
                what="The action changed after you approved it.",
                why="You approved '%s' with different arguments than the ones "
                    "now being run. Approval is for one specific action, not a "
                    "blank cheque." % approval["tool"],
                tried="Comparing a fingerprint of the approved arguments with "
                      "the ones supplied now",
                needs="A fresh approval for the new action.",
                fix="Run the request again and approve the new slip if you "
                    "still want it.",
            )
            return ToolResult(False, name, error=error.to_dict())
        decision = policy.Decision(policy.ALLOW,
                                   "you approved this specific action (%s)" % ctx.approval_id,
                                   spec.risk, step="approved")
    else:
        # --- ask the guard -------------------------------------------------
        request = policy.Request(
            capability=spec.capability, tool=name, arguments=cleaned,
            summary=summary, paths=involved, session_id=ctx.session_id,
            actor=ctx.actor,
        )
        decision = ctx.engine().evaluate(request)

    if decision.outcome == policy.DENY:
        # PermissionDenied, not the generic base class: callers need to tell
        # "the guard said no" apart from "the tool broke".  They are very
        # different situations and only one of them is a bug.
        error = PermissionDenied(
            what="Not allowed: %s" % summary,
            why=decision.reason,
            tried="Checking this action against your permissions (rule %s)"
                  % (decision.step or "default"),
            needs="A permission change, if you actually want this.",
            fix="Run 'assistant permissions' to see the current rules.",
        )
        return ToolResult(False, name, error=error.to_dict(),
                          decision=decision.to_dict(),
                          duration_ms=int((time.time() - started) * 1000))

    if decision.outcome == policy.ASK:
        return ToolResult(
            False, name,
            error={"code": "policy.approval_required",
                   "what": "Waiting for your approval: %s" % summary,
                   "why": decision.reason,
                   "needs": "Your yes or no.",
                   "fix": "Run:  assistant approve %s    (or: assistant deny %s)"
                          % (decision.approval_id, decision.approval_id)},
            decision=decision.to_dict(), needs_approval=decision.approval_id,
            duration_ms=int((time.time() - started) * 1000))

    if ctx.dry_run:
        return ToolResult(True, name, data={"dry_run": True, "would_do": summary},
                          decision=decision.to_dict(),
                          duration_ms=int((time.time() - started) * 1000))

    # --- run it -----------------------------------------------------------
    try:
        data = spec.handler(cleaned, ctx)
        elapsed = int((time.time() - started) * 1000)
        audit.record("tool.executed", actor=ctx.actor, tool=name,
                     capability=spec.capability, decision=decision.outcome,
                     rule_id=decision.rule_id, arguments=cleaned,
                     resources=involved, outcome="ok",
                     session_id=ctx.session_id, duration_ms=elapsed)
        return ToolResult(True, name, data=data, decision=decision.to_dict(),
                          duration_ms=elapsed)
    except AssistantError as exc:
        elapsed = int((time.time() - started) * 1000)
        audit.record("tool.failed", actor=ctx.actor, tool=name,
                     capability=spec.capability, decision=decision.outcome,
                     arguments=cleaned, resources=involved, outcome="error",
                     error=exc.what, session_id=ctx.session_id, duration_ms=elapsed)
        return ToolResult(False, name, error=exc.to_dict(),
                          decision=decision.to_dict(), duration_ms=elapsed)
    except Exception as exc:  # unexpected - never let it escape silently
        elapsed = int((time.time() - started) * 1000)
        log.exception("tool %s crashed", name)
        wrapped = ToolError(
            what="The tool '%s' stopped unexpectedly." % name,
            why="%s: %s" % (type(exc).__name__, exc),
            tried=summary,
            needs="This is a bug in the assistant, not something you did wrong.",
            fix="Run 'assistant logs --level error' for the full trace, and "
                "report it.",
        )
        audit.record("tool.crashed", actor=ctx.actor, tool=name,
                     capability=spec.capability, arguments=cleaned,
                     resources=involved, outcome="crash", error=str(exc),
                     session_id=ctx.session_id, duration_ms=elapsed)
        return ToolResult(False, name, error=wrapped.to_dict(),
                          decision=decision.to_dict(), duration_ms=elapsed)
