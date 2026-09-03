"""The policy engine: the security guard.

THIS IS THE MOST IMPORTANT FILE IN THE PROJECT.

Every single action the assistant wants to take passes through
``PolicyEngine.evaluate()``.  There is no second route.  The engine reads
*only* from your configuration and your database - never from anything a
language model produced.  That is the structural reason a hijacked model
cannot escalate its own privileges: it can shout "you are allowed!" all it
likes, and this code will not be listening.

THE DECISION ORDER (first match wins)
-------------------------------------
     1. Kill switch engaged?            -> DENY   (always)
     2. Any path in the sealed vault?   -> DENY   (never overridable)
     3. An explicit DENY rule matches?  -> DENY   (your rules beat everything)
     4. Any path outside your scopes?   -> DENY
     5. Write asked on a read-only scope-> DENY
     6. Capability is CRITICAL?         -> ASK    (no setting can auto-allow)
     7. Mode is observe/suggest and the
        action is not read-only?        -> DENY
     8. An explicit ASK rule matches?   -> ASK
     9. An explicit ALLOW rule matches
        (supervised/autonomous only)?   -> ALLOW
    10. Risk within the mode's ceiling? -> ALLOW
    11. Otherwise                       -> ASK

Deny always beats allow.  The default answer is ASK, never ALLOW.
"""
from __future__ import annotations

import fnmatch
import os
from typing import Any, Dict, List, NamedTuple, Optional, Sequence

from .. import audit, config as config_module, db
from ..errors import ForbiddenPath
from . import capabilities, consent, killswitch, pathguard

ALLOW = "allow"
ASK = "ask"
DENY = "deny"

#: How much each autonomy mode may approve on its own.
#: None means "nothing above a plain read".
MODE_CEILING: Dict[str, Optional[str]] = {
    "observe": capabilities.LOW,
    "suggest": capabilities.LOW,
    "assisted": capabilities.LOW,
    "supervised": capabilities.MEDIUM,
    "autonomous": capabilities.MEDIUM,
}

#: Modes in which a user-written ALLOW rule can raise the ceiling to HIGH.
RULE_ALLOW_MODES = ("supervised", "autonomous")

#: Modes that refuse to act at all beyond reading.
READ_ONLY_MODES = ("observe", "suggest")


class Rule(NamedTuple):
    id: int
    effect: str
    capability: str
    path_glob: Optional[str]
    note: str
    priority: int


class Request(NamedTuple):
    capability: str
    tool: str
    arguments: Dict[str, Any]
    summary: str
    paths: Sequence[str] = ()
    session_id: Optional[str] = None
    actor: str = "assistant"


class Decision(NamedTuple):
    outcome: str            # allow | ask | deny
    reason: str
    risk: str
    rule_id: Optional[int] = None
    approval_id: Optional[str] = None
    step: str = ""          # which numbered rule above fired

    @property
    def allowed(self) -> bool:
        return self.outcome == ALLOW

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome, "reason": self.reason, "risk": self.risk,
            "rule_id": self.rule_id, "approval_id": self.approval_id,
            "step": self.step,
        }


# --------------------------------------------------------------------------
# Rule storage
# --------------------------------------------------------------------------
def list_rules(conn: Optional[Any] = None) -> List[Rule]:
    conn = conn or db.connect()
    rows = db.query(
        conn,
        "SELECT id,effect,capability,path_glob,COALESCE(note,'') AS note,priority "
        "FROM rules WHERE enabled=1 ORDER BY priority ASC, id ASC",
    )
    return [Rule(r["id"], r["effect"], r["capability"], r["path_glob"],
                 r["note"], r["priority"]) for r in rows]


def add_rule(effect: str, capability: str, path_glob: Optional[str] = None,
             note: str = "", priority: int = 100,
             conn: Optional[Any] = None) -> int:
    if effect not in (ALLOW, ASK, DENY):
        raise ValueError("effect must be allow, ask or deny")
    conn = conn or db.connect()
    cur = conn.execute(
        "INSERT INTO rules(effect,capability,path_glob,note,priority,created_at) "
        "VALUES(?,?,?,?,?,datetime('now'))",
        (effect, capability, path_glob, note, int(priority)),
    )
    audit.record("policy.rule_added", actor="user",
                 arguments={"effect": effect, "capability": capability,
                            "path_glob": path_glob, "note": note})
    return int(cur.lastrowid or 0)


def remove_rule(rule_id: int, conn: Optional[Any] = None) -> bool:
    conn = conn or db.connect()
    cur = conn.execute("DELETE FROM rules WHERE id=?", (int(rule_id),))
    if cur.rowcount:
        audit.record("policy.rule_removed", actor="user", arguments={"id": rule_id})
    return cur.rowcount > 0


def _path_matches(glob: Optional[str], paths: Sequence[str]) -> bool:
    """Does a rule's path pattern cover any of the paths in this request?

    A pattern with no wildcard is treated as a folder prefix, so
    ``~/Downloads`` covers everything inside it.  Patterns use fnmatch rules,
    where ``*`` also crosses ``/`` - so ``~/Downloads/*`` and ``~/Downloads/**``
    both mean "anything under Downloads".
    """
    if not glob:
        return True  # a rule with no path applies to every path
    if not paths:
        return False
    expanded = os.path.expanduser(glob)
    has_wildcard = any(ch in expanded for ch in "*?[")
    for raw in paths:
        try:
            resolved = str(pathguard.resolve_path(raw))
        except Exception:
            resolved = str(raw)
        if has_wildcard:
            if fnmatch.fnmatchcase(resolved, expanded):
                return True
        else:
            from pathlib import Path
            try:
                root = Path(expanded).resolve()
            except (OSError, RuntimeError):
                root = Path(expanded)
            if pathguard.is_within(Path(resolved), root):
                return True
    return False


def _first_matching(rules: Sequence[Rule], effect: str, request: Request) -> Optional[Rule]:
    for rule in rules:
        if rule.effect != effect:
            continue
        if not capabilities.matches(rule.capability, request.capability):
            continue
        if not _path_matches(rule.path_glob, request.paths):
            continue
        return rule
    return None


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------
class PolicyEngine:
    def __init__(self, cfg: Optional[config_module.Config] = None) -> None:
        self.config = cfg or config_module.load()

    # -- helpers ----------------------------------------------------------
    @property
    def mode(self) -> str:
        return self.config.autonomy_mode

    def ceiling(self) -> Optional[str]:
        return MODE_CEILING.get(self.mode, capabilities.LOW)

    # -- the decision -----------------------------------------------------
    def evaluate(self, request: Request, *, create_approval: bool = True) -> Decision:
        risk = capabilities.risk_of(request.capability)
        cap = capabilities.get(request.capability)
        needs_write = bool(cap and cap.writes)
        rules = list_rules()

        # 1 - kill switch --------------------------------------------------
        if killswitch.is_engaged():
            state = killswitch.status() or {}
            return self._finish(request, Decision(
                DENY,
                "the emergency stop is engaged (%s). Run 'assistant resume' to "
                "clear it." % state.get("reason", "unknown reason"),
                risk, step="1/kill-switch"))

        # 2 - sealed vault -------------------------------------------------
        for raw in request.paths:
            try:
                resolved = pathguard.resolve_path(raw)
                pathguard.assert_not_forbidden(resolved)
            except ForbiddenPath as exc:
                return self._finish(request, Decision(
                    DENY, exc.why or "the path is permanently protected",
                    risk, step="2/forbidden-path"))
            except Exception as exc:
                return self._finish(request, Decision(
                    DENY, "the path could not be validated: %s" % exc,
                    risk, step="2/unresolvable-path"))

        # 3 - explicit deny -------------------------------------------------
        rule = _first_matching(rules, DENY, request)
        if rule:
            return self._finish(request, Decision(
                DENY, "your rule #%d forbids this%s" % (
                    rule.id, (" (%s)" % rule.note) if rule.note else ""),
                risk, rule_id=rule.id, step="3/deny-rule"))

        # 4 & 5 - scopes ----------------------------------------------------
        for raw in request.paths:
            result = pathguard.check(raw, need_write=needs_write)
            if not result.ok:
                return self._finish(request, Decision(
                    DENY, result.reason, risk,
                    step="5/read-only-scope" if "read-only" in result.reason
                    else "4/out-of-scope"))

        # 6 - critical always asks -------------------------------------------
        if risk == capabilities.CRITICAL:
            return self._ask(request, risk,
                             "this is a CRITICAL action (irreversible or "
                             "visible to other people), which always needs "
                             "your explicit approval",
                             step="6/critical", create_approval=create_approval)

        # 7 - observe / suggest modes ----------------------------------------
        if self.mode in READ_ONLY_MODES and risk != capabilities.LOW:
            return self._finish(request, Decision(
                DENY,
                "you are in '%s' mode, where the assistant may look but not "
                "act. Switch with: assistant config set autonomy.mode assisted"
                % self.mode,
                risk, step="7/read-only-mode"))

        # 8 - explicit ask ---------------------------------------------------
        rule = _first_matching(rules, ASK, request)
        if rule:
            return self._ask(request, risk,
                             "your rule #%d says to ask first%s" % (
                                 rule.id, (" (%s)" % rule.note) if rule.note else ""),
                             rule_id=rule.id, step="8/ask-rule",
                             create_approval=create_approval)

        # 9 - explicit allow (only in the higher-trust modes) -----------------
        rule = _first_matching(rules, ALLOW, request)
        if rule:
            if self.mode in RULE_ALLOW_MODES:
                if risk == capabilities.CRITICAL:  # unreachable, kept for clarity
                    return self._ask(request, risk, "critical actions always ask",
                                     step="9/critical", create_approval=create_approval)
                return self._finish(request, Decision(
                    ALLOW, "your rule #%d permits this%s" % (
                        rule.id, (" (%s)" % rule.note) if rule.note else ""),
                    risk, rule_id=rule.id, step="9/allow-rule"))
            # The rule exists but the mode is too cautious to use it.
            return self._ask(request, risk,
                             "your rule #%d would permit this, but '%s' mode "
                             "asks before every action. Switch to 'supervised' "
                             "to let standing rules apply automatically."
                             % (rule.id, self.mode),
                             rule_id=rule.id, step="9/allow-rule-mode-gated",
                             create_approval=create_approval)

        # 10 - mode ceiling ---------------------------------------------------
        ceiling = self.ceiling()
        if ceiling and capabilities.tier_at_most(risk, ceiling):
            return self._finish(request, Decision(
                ALLOW,
                "'%s' risk is within what '%s' mode handles automatically"
                % (risk, self.mode),
                risk, step="10/mode-ceiling"))

        # 11 - default ---------------------------------------------------------
        return self._ask(request, risk,
                         "'%s' risk is above what '%s' mode does on its own"
                         % (risk, self.mode),
                         step="11/default-ask", create_approval=create_approval)

    # -- outcome helpers ---------------------------------------------------
    def _ask(self, request: Request, risk: str, reason: str, *,
             rule_id: Optional[int] = None, step: str = "",
             create_approval: bool = True) -> Decision:
        approval_id = None
        if create_approval:
            approval_id = consent.create(
                capability=request.capability, tool=request.tool,
                arguments=request.arguments, risk=risk, summary=request.summary,
                resources=[str(p) for p in request.paths],
                session_id=request.session_id,
                timeout_seconds=int(self.config.get("autonomy.approval_timeout_seconds", 900)),
            )
        return self._finish(request, Decision(ASK, reason, risk, rule_id,
                                              approval_id, step))

    def _finish(self, request: Request, decision: Decision) -> Decision:
        audit.record(
            "policy.decision", actor=request.actor, tool=request.tool,
            capability=request.capability, decision=decision.outcome,
            rule_id=decision.rule_id, arguments=request.arguments,
            resources=[str(p) for p in request.paths],
            outcome=decision.step, session_id=request.session_id,
            error=None if decision.outcome != DENY else decision.reason,
        )
        return decision

    # -- introspection ------------------------------------------------------
    def explain(self) -> Dict[str, Any]:
        """Used by `assistant permissions` to show the full current picture."""
        return {
            "mode": self.mode,
            "auto_approves_up_to": self.ceiling() or "nothing",
            "standing_rules_apply": self.mode in RULE_ALLOW_MODES,
            "kill_switch": killswitch.status(),
            "scopes": pathguard.describe()["scopes"],
            "rules": [r._asdict() for r in list_rules()],
            "pending_approvals": len(consent.pending()),
        }
