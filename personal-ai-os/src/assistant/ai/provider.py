"""The AI provider interface.

WHY AN INTERFACE AND NOT JUST "CALL THE API"?
---------------------------------------------
Because you should be able to change your mind.  Today you might use a cloud
model because it is the most capable; tomorrow you might want everything to
stay on your Mac for a private project.  Every provider below implements the
same three methods, so the rest of the system does not know or care which one
is answering.

ANALOGY
-------
It is like a power socket.  The kettle does not care whether the electricity
came from a wind turbine or a coal plant - it just needs 240 volts in the
right shape.  ``AIProvider`` is the shape of the socket.

AN IMPORTANT BOUNDARY
---------------------
A provider's only job is: text and tool definitions in, text and *requests to
use tools* out.  A provider NEVER executes a tool.  It cannot touch your
files.  Everything it asks for goes back to the orchestrator, which sends it
through the policy engine.
"""
from __future__ import annotations

import abc
from typing import Any, Dict, List, NamedTuple, Optional, Sequence


class Message(NamedTuple):
    """One turn of conversation."""
    role: str                 # "user" | "assistant"
    content: Any              # str, or a list of provider-native blocks


class ToolCall(NamedTuple):
    """The model asking to use a tool.  This is a *request*, not an action."""
    id: str
    name: str
    arguments: Dict[str, Any]


class Usage(NamedTuple):
    input_tokens: int = 0
    output_tokens: int = 0


class Reply(NamedTuple):
    text: str
    tool_calls: Sequence[ToolCall] = ()
    stop_reason: str = "end_turn"
    usage: Usage = Usage()
    model: str = ""
    provider: str = ""
    #: The provider's own message object, needed to continue the conversation
    #: faithfully (thinking blocks must be echoed back unchanged, for example).
    raw_content: Any = None
    #: True when the model declined for safety reasons.
    refused: bool = False

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class Availability(NamedTuple):
    ok: bool
    reason: str = ""
    fix: str = ""
    detail: Dict[str, Any] = {}


class AIProvider(abc.ABC):
    """What every model backend must be able to do."""

    #: Short name used in config and logs.
    name: str = "provider"
    #: True when no data leaves the Mac.
    is_local: bool = False

    def __init__(self, settings: Optional[Dict[str, Any]] = None) -> None:
        self.settings = dict(settings or {})

    @property
    def model(self) -> str:
        return str(self.settings.get("model", ""))

    @abc.abstractmethod
    def availability(self) -> Availability:
        """Can this provider be used right now?  Must never raise.

        When the answer is no, `reason` and `fix` are shown to the user
        verbatim - so write them as instructions, not as error codes.
        """

    @abc.abstractmethod
    def complete(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        role: Optional[str] = None,
    ) -> Reply:
        """Send a conversation, get one reply back."""

    # -- shared helpers ----------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        state = self.availability()
        return {
            "name": self.name,
            "model": self.model,
            "local": self.is_local,
            "available": state.ok,
            "reason": state.reason,
            "fix": state.fix,
        }


def record_usage(provider: str, model: str, role: Optional[str], usage: Usage,
                 session_id: Optional[str] = None) -> None:
    """Write token counts to the database so cloud spend is never invisible."""
    from .. import db
    try:
        conn = db.connect()
        conn.execute(
            "INSERT INTO usage(ts,provider,model,role,input_tokens,output_tokens,session_id) "
            "VALUES(datetime('now'),?,?,?,?,?,?)",
            (provider, model, role, usage.input_tokens, usage.output_tokens, session_id),
        )
    except Exception:  # usage accounting must never break a conversation
        pass
