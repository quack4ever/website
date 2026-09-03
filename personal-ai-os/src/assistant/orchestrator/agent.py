"""The orchestrator: the manager who coordinates everything.

THE LOOP, IN PLAIN ENGLISH
--------------------------
    1. PERCEIVE  - work out the situation: what did you ask, what can I touch,
                   what do I already know about you?
    2. THINK     - send that to a model and get a reply back.
    3. ACT       - if the reply asks for tools, run each one THROUGH THE GUARD.
    4. REPEAT    - feed the results back and go again, up to a limit.
    5. STOP      - either the model is done, or something needs your approval.
    6. REFLECT   - afterwards, judge whether it actually worked.

THE MOST IMPORTANT LINE IN THIS FILE
------------------------------------
    result = registry.execute(call.name, call.arguments, ctx)

Everything the model wants to do goes through that one call, which asks the
policy engine first.  There is no second path.  If you want to verify this
system is safe, verify that claim - it is the whole architecture in one line.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, NamedTuple, Optional, Sequence

from .. import audit, config as config_module, db, tools
from ..ai import Router
from ..ai.provider import AIProvider, Message
from ..errors import AssistantError
from ..logging_setup import get
from ..memory import store as memory_store
from ..planner import plan as plan_module, superposition as sp
from ..planner.beam import Step
from ..security import policy
from ..tools import registry
from . import roles

log = get(__name__)


class AgentResult(NamedTuple):
    text: str
    session_id: str
    iterations: int = 0
    tools_used: Sequence[str] = ()
    pending_approvals: Sequence[Dict[str, Any]] = ()
    errors: Sequence[Dict[str, Any]] = ()
    plan_id: Optional[str] = None
    provider: str = ""
    model: str = ""

    @property
    def needs_approval(self) -> bool:
        return bool(self.pending_approvals)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text, "session_id": self.session_id,
            "iterations": self.iterations, "tools_used": list(self.tools_used),
            "pending_approvals": list(self.pending_approvals),
            "errors": list(self.errors), "plan_id": self.plan_id,
            "provider": self.provider, "model": self.model,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def extract_json(text: str) -> Optional[Any]:
    """Pull a JSON array or object out of a model reply.

    Models sometimes wrap JSON in prose or a ```json fence even when asked not
    to.  Rather than failing the whole task over formatting, we look for the
    first balanced block.  If there is genuinely no JSON we return None and
    the caller says so honestly - we never invent a fallback structure.
    """
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    # Try whichever bracket appears FIRST in the text, not a fixed order.
    # Preferring "[" unconditionally mis-parsed {"lessons": ["a"]} as the
    # inner array ["a"], silently losing the object it was wrapped in.
    candidates = []
    for opener, closer in (("[", "]"), ("{", "}")):
        position = text.find(opener)
        if position != -1:
            candidates.append((position, opener, closer))
    candidates.sort()

    for start, opener, closer in candidates:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:index + 1])
                    except json.JSONDecodeError:
                        break
    return None


class Agent:
    """One conversation with the assistant."""

    def __init__(self, cfg: Optional[config_module.Config] = None,
                 router: Optional[Router] = None,
                 session_id: Optional[str] = None) -> None:
        self.config = cfg or config_module.load()
        self.router = router or Router(self.config)
        self.session_id = session_id or self._new_session()
        tools.load_all()

    # -- session bookkeeping ----------------------------------------------
    def _new_session(self) -> str:
        session_id = uuid.uuid4().hex[:12]
        db.connect().execute(
            "INSERT INTO sessions(id,created_at,updated_at) VALUES(?,?,?)",
            (session_id, _now(), _now()))
        return session_id

    def _save_message(self, role: str, content: Any,
                      meta: Optional[Dict[str, Any]] = None) -> None:
        conn = db.connect()
        conn.execute(
            "INSERT INTO messages(session_id,ts,role,content,meta) VALUES(?,?,?,?,?)",
            (self.session_id, _now(), role,
             content if isinstance(content, str) else db.json_dump(content),
             db.json_dump(meta or {})))
        conn.execute("UPDATE sessions SET updated_at=? WHERE id=?",
                     (_now(), self.session_id))

    def history(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = db.query(
            db.connect(),
            "SELECT role, ts, content FROM messages WHERE session_id=? "
            "ORDER BY id ASC LIMIT ?", (self.session_id, int(limit)))
        return db.rows_to_dicts(rows)

    # -- perception --------------------------------------------------------
    def _context(self, question: str) -> Dict[str, Any]:
        engine = policy.PolicyEngine(self.config)
        memories = memory_store.recall(question, cfg=self.config)
        return {"engine_state": engine.explain(), "memories": memories}

    # -- the main loop -----------------------------------------------------
    def ask(self, question: str, role: str = "reasoning",
            max_iterations: Optional[int] = None,
            approvals: Optional[Dict[str, str]] = None) -> AgentResult:
        """Answer a question, using tools as needed.

        ``approvals`` maps tool name -> approval id, for re-running a step the
        user has just said yes to.
        """
        limit = int(max_iterations or self.config.get("ai.max_tool_iterations", 12))
        context = self._context(question)
        system = roles.orchestrator_prompt(context["engine_state"], context["memories"])
        provider = self.router.for_role(role)
        schemas = tools.provider_schemas()

        self._save_message("user", question)
        audit.record("agent.ask", actor="user", session_id=self.session_id,
                     arguments={"question": question[:500], "role": role})

        messages: List[Message] = [Message("user", question)]
        used: List[str] = []
        pending: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        final_text = ""
        iterations = 0

        for iterations in range(1, limit + 1):
            try:
                reply = provider.complete(system, messages, tools=schemas, role=role)
            except AssistantError as exc:
                self._save_message("assistant", exc.human())
                return AgentResult(exc.human(), self.session_id, iterations,
                                   used, pending, [exc.to_dict()],
                                   provider=provider.name)

            final_text = reply.text or final_text

            if not reply.wants_tools:
                break

            # Echo the assistant turn back verbatim where the provider gives us
            # native blocks (Anthropic needs its thinking blocks returned
            # unchanged); otherwise fall back to the plain text.
            assistant_content: Any = (
                reply.raw_content if isinstance(reply.raw_content, list)
                else (reply.text or "(requested tools)"))
            messages.append(Message("assistant", assistant_content))

            results_blocks: List[Dict[str, Any]] = []
            for call in reply.tool_calls:
                used.append(call.name)
                ctx = registry.ExecContext(
                    session_id=self.session_id, actor="assistant",
                    approval_id=(approvals or {}).get(call.name),
                    config=self.config)
                result = registry.execute(call.name, call.arguments, ctx)

                if result.needs_approval:
                    pending.append({
                        "approval_id": result.needs_approval,
                        "tool": call.name,
                        "arguments": call.arguments,
                        "summary": (result.error or {}).get("what", ""),
                        "why": (result.error or {}).get("why", ""),
                    })
                if not result.ok and result.error:
                    errors.append(dict(result.error, tool=call.name))

                results_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": self._render_result(result),
                    "is_error": not result.ok,
                })

            messages.append(Message("user", results_blocks))

            if pending:
                # Stop the whole loop: continuing would mean working around a
                # check the user has not answered yet.
                break

        if pending:
            final_text = self._approval_message(final_text, pending)

        self._save_message("assistant", final_text,
                           {"tools": used, "iterations": iterations})
        audit.record("agent.reply", actor="assistant", session_id=self.session_id,
                     outcome="pending_approval" if pending else "complete",
                     arguments={"tools": used, "iterations": iterations})

        return AgentResult(final_text, self.session_id, iterations, used,
                           pending, errors, provider=provider.name,
                           model=getattr(provider, "model", ""))

    @staticmethod
    def _render_result(result: registry.ToolResult) -> str:
        """Turn a tool result into text for the model.

        Where the tool produced untrusted content (file text, search hits,
        command output) we send the WRAPPED version, never the raw one.
        """
        if result.ok:
            data = result.data
            if isinstance(data, dict) and data.get("untrusted"):
                summary = {k: v for k, v in data.items()
                           if k not in ("untrusted", "text", "injection_signals")}
                return ("%s\n\n%s" % (json.dumps(summary, default=str)[:4000],
                                      data["untrusted"]))[:60_000]
            return json.dumps(data, default=str)[:60_000]
        error = result.error or {}
        return json.dumps({
            "error": True,
            "what": error.get("what"), "why": error.get("why"),
            "how_the_user_can_fix_it": error.get("fix"),
            "note": "This did not happen. Do not pretend it did.",
        }, default=str)

    @staticmethod
    def _approval_message(text: str, pending: Sequence[Dict[str, Any]]) -> str:
        lines = [text.strip()] if text.strip() else []
        lines.append("\nI need your permission before going further:")
        for item in pending:
            lines.append("\n  %s" % item["summary"])
            if item.get("why"):
                lines.append("  Why it is asking: %s" % item["why"])
            lines.append("  Approve:  assistant approve %s" % item["approval_id"])
            lines.append("  Refuse:   assistant deny %s" % item["approval_id"])
        return "\n".join(lines)

    # -- planning ----------------------------------------------------------
    def plan(self, goal: str, risk_ceiling: Optional[float] = None) -> Dict[str, Any]:
        """Generate strategies, score them, choose one, and save the plan."""
        context = self._context(goal)
        system = roles.planner_prompt(context["engine_state"], context["memories"])
        provider = self.router.for_role("planning")

        try:
            reply = provider.complete(
                system, [Message("user", "Goal: " + goal)], role="planning")
        except AssistantError as exc:
            return {"ok": False, "error": exc.to_dict(), "goal": goal}

        parsed = extract_json(reply.text)
        if not isinstance(parsed, list) or not parsed:
            return {
                "ok": False, "goal": goal,
                "error": {
                    "what": "I could not produce a set of strategies to compare.",
                    "why": ("The planning model did not return a usable list of "
                            "options." if reply.text else "The model returned nothing."),
                    "fix": ("Check a model is configured with 'assistant doctor'. "
                            "The model's reply is below so you can see what happened."),
                },
                "model_said": (reply.text or "")[:1500],
            }

        strategies = [s for s in (self._to_strategy(item, index)
                                  for index, item in enumerate(parsed)) if s]
        if not strategies:
            return {"ok": False, "goal": goal,
                    "error": {"what": "The strategies came back in an unusable shape.",
                              "why": "No entry had the required fields.",
                              "fix": "Try rephrasing the goal."}}

        constraints = self._constraints_from_rules()
        evaluations = sp.evaluate(
            strategies, constraints=constraints,
            temperature=float(self.config.get("planner.temperature", 0.35)),
            simulations=int(self.config.get("planner.simulations", 300)),
            seed=self.config.get("planner.seed"))
        choice = sp.collapse(
            evaluations,
            risk_ceiling=float(risk_ceiling if risk_ceiling is not None
                               else self.config.get("planner.risk_ceiling", 0.65)))

        steps = self._steps_for(choice)
        plan_id = plan_module.create(goal, choice, steps, self.session_id)
        audit.record("plan.created", actor="assistant", session_id=self.session_id,
                     arguments={"goal": goal[:300], "plan": plan_id},
                     outcome=choice.chosen.strategy.id if choice.chosen else "none")

        return {
            "ok": True, "goal": goal, "plan_id": plan_id,
            "choice": choice.chosen.to_dict() if choice.chosen else None,
            "ranked": [e.to_dict() for e in evaluations],
            "explanation": choice.explain(),
            "forced_by_risk": choice.forced_by_risk,
            "steps": [s.to_dict() for s in steps],
        }

    @staticmethod
    def _to_strategy(item: Any, index: int) -> Optional[sp.Strategy]:
        if not isinstance(item, dict) or not item.get("title"):
            return None
        scores = item.get("scores")
        return sp.Strategy(
            id=str(item.get("id") or "s%d" % index),
            title=str(item["title"])[:120],
            description=str(item.get("description", ""))[:600],
            actions=[str(a) for a in (item.get("actions") or [])][:20],
            scores={k: float(v) for k, v in (scores or {}).items()
                    if isinstance(v, (int, float))},
            step_success=max(0.05, min(1.0, float(item.get("step_success", 0.85)))),
            steps=max(1, min(20, int(item.get("steps", 3)))),
        )

    def _constraints_from_rules(self) -> List[sp.Constraint]:
        """Turn the user's DENY rules into hard planning constraints.

        This is what makes "you may organise Downloads but never delete
        anything" actually shape the plan, instead of only being caught later
        when a step is refused. A plan that was never going to be allowed
        should not be proposed in the first place.
        """
        mapping = {
            "files.delete": "delete_files", "files.bulk_delete": "delete_files",
            "files.trash": "trash_files", "files.move": "move_files",
            "files.write": "write_files", "files.create": "write_files",
            "shell.execute": "run_command", "mail.send": "email",
            "message.send": "message", "calendar.write": "make_calendar",
            "reminders.write": "reminders",
        }
        constraints: List[sp.Constraint] = []
        for rule in policy.list_rules():
            if rule.effect != policy.DENY:
                continue
            action = mapping.get(rule.capability)
            if not action:
                continue
            constraints.append(sp.Constraint(
                name=rule.note or ("your rule: never %s" % rule.capability),
                violated_by=(lambda tag: lambda s: tag in s.actions)(action),
                explanation="rule #%d" % rule.id))
        return constraints

    @staticmethod
    def _steps_for(choice: sp.Choice) -> List[Step]:
        """Break the chosen strategy into named steps.

        These are titles, not tool calls: the executor decides the exact tool
        at run time, because the right tool depends on what it finds. Being
        explicit about that is better than inventing tool arguments now and
        pretending they are final.
        """
        if not choice.chosen:
            return []
        strategy = choice.chosen.strategy
        titles = {
            "index_files": "Index the relevant folders so everything is searchable",
            "read_files": "Read the key documents",
            "search_files": "Search for the relevant files",
            "make_folders": "Create the folder structure",
            "move_files": "Move files into place",
            "copy_files": "Copy files",
            "trash_files": "Move unwanted files to the Trash",
            "write_files": "Write the new or updated documents",
            "make_calendar": "Add the dates to the calendar",
            "extract_dates": "Pull the deadlines out of the documents",
            "reminders": "Set the reminders",
            "notify": "Send a notification",
            "run_command": "Run the required command",
            "summarise": "Summarise what was found",
            "archive": "Archive the old material",
            "ask_user": "Check the details with the user",
        }
        steps = [Step(title=titles.get(action, action.replace("_", " ").capitalize()),
                      note=action)
                 for action in strategy.actions]
        if not steps:
            steps = [Step(title=strategy.title, note="single step")]
        return steps
