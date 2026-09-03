"""The offline provider.

WHAT THIS IS - AND WHAT IT IS NOT
---------------------------------
This is NOT an AI.  It does not reason, plan, or understand anything.  It
exists for exactly two reasons:

1. **Tests.**  The test suite must be able to run the whole system - the
   orchestrator, the tool loop, the planner - without a network connection,
   an API key, or a bill.  Tests push scripted replies onto a queue and this
   provider hands them back in order.

2. **Honesty when nothing is configured.**  If you have not set up a model,
   the assistant must say so plainly instead of pretending.  This provider
   returns a message explaining exactly what to do.

It never fabricates an answer to a real question.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, List, Optional, Sequence

from .provider import AIProvider, Availability, Message, Reply, Usage

NO_MODEL_MESSAGE = (
    "I do not have a language model configured, so I cannot reason about this "
    "request.\n\n"
    "The rest of the system is working - permissions, file indexing, memory "
    "and the audit log all run locally without a model. Only the thinking "
    "part needs one.\n\n"
    "To fix this, pick one:\n"
    "  * Cloud:  export ANTHROPIC_API_KEY=...   then  assistant doctor\n"
    "  * Local:  install Ollama (https://ollama.com), run 'ollama pull llama3.1',\n"
    "            then  assistant config set ai.roles.reasoning ollama\n"
)


class OfflineProvider(AIProvider):
    name = "offline"
    is_local = True

    def __init__(self, settings: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(settings)
        self._scripted: Deque[Reply] = deque()
        self.calls: List[Dict[str, Any]] = []

    # -- test helpers -----------------------------------------------------
    def push(self, reply: Reply) -> None:
        """Queue a reply for the next call (tests only)."""
        self._scripted.append(reply)

    def push_text(self, text: str) -> None:
        self.push(Reply(text=text, provider=self.name, model="scripted"))

    def push_tool_call(self, name: str, arguments: Dict[str, Any],
                       call_id: str = "call_1") -> None:
        from .provider import ToolCall
        self.push(Reply(text="", tool_calls=[ToolCall(call_id, name, arguments)],
                        stop_reason="tool_use", provider=self.name, model="scripted"))

    # -- interface --------------------------------------------------------
    def availability(self) -> Availability:
        return Availability(
            ok=True,
            reason="No model is configured. The offline stub will explain that "
                   "rather than guess.",
            fix="Set ANTHROPIC_API_KEY, or point a role at a local Ollama model.",
        )

    def complete(self, system: str, messages: Sequence[Message],
                 tools: Optional[List[Dict[str, Any]]] = None,
                 max_tokens: Optional[int] = None,
                 role: Optional[str] = None) -> Reply:
        self.calls.append({"system": system, "messages": list(messages),
                           "tools": [t["name"] for t in (tools or [])], "role": role})
        if self._scripted:
            return self._scripted.popleft()
        return Reply(text=NO_MODEL_MESSAGE, provider=self.name, model="none",
                     stop_reason="no_model", usage=Usage())
