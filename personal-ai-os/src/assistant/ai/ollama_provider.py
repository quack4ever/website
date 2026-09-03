"""Ollama: running a model entirely on your own Mac.

WHY THIS MATTERS
----------------
This is the privacy escape hatch.  With Ollama installed, you can point any
role at a local model and that work never touches the internet.  It is slower
and less capable than a frontier cloud model, but for "summarise this file"
or "which of these folders look related" it is often plenty - and the file
never leaves your machine.

Ollama runs a small web server on your own computer at 127.0.0.1:11434.
"127.0.0.1" means "this computer, and only this computer".
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from ..logging_setup import get
from .http_json import get_json, post_json
from .provider import (AIProvider, Availability, Message, Reply, ToolCall,
                       Usage, record_usage)

log = get(__name__)


class OllamaProvider(AIProvider):
    name = "ollama"
    is_local = True

    @property
    def base_url(self) -> str:
        return str(self.settings.get("base_url") or "http://127.0.0.1:11434").rstrip("/")

    def availability(self) -> Availability:
        try:
            data = get_json(self.base_url + "/api/tags", timeout=3.0, provider="Ollama")
        except Exception:
            return Availability(
                False,
                reason="Ollama is not running on this Mac.",
                fix="Install it from https://ollama.com, then run:\n"
                    "  ollama serve\n  ollama pull %s" % (self.model or "llama3.1"),
            )
        names = [m.get("name", "") for m in (data.get("models") or [])]
        wanted = self.model
        if wanted and not any(n == wanted or n.startswith(wanted + ":") for n in names):
            return Availability(
                False,
                reason="Ollama is running, but the model '%s' is not downloaded." % wanted,
                fix="Run:  ollama pull %s\nInstalled models: %s"
                    % (wanted, ", ".join(names) or "none"),
                detail={"installed": names},
            )
        return Availability(True, reason="ready (fully local)",
                            detail={"installed": names})

    def complete(self, system: str, messages: Sequence[Message],
                 tools: Optional[List[Dict[str, Any]]] = None,
                 max_tokens: Optional[int] = None,
                 role: Optional[str] = None) -> Reply:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [self._flatten(m) for m in messages],
            "stream": False,
            "options": {"num_predict": int(max_tokens or self.settings.get("max_tokens", 4096))},
        }
        if tools:
            payload["tools"] = [
                {"type": "function",
                 "function": {"name": t["name"], "description": t["description"],
                              "parameters": t["input_schema"]}}
                for t in tools
            ]

        data = post_json(self.base_url + "/api/chat", payload, timeout=600.0,
                         provider="Ollama")
        message = data.get("message") or {}
        calls: List[ToolCall] = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            calls.append(ToolCall(id="ollama_%d" % index,
                                  name=str(function.get("name", "")),
                                  arguments=dict(arguments or {})))

        usage = Usage(int(data.get("prompt_eval_count", 0) or 0),
                      int(data.get("eval_count", 0) or 0))
        record_usage(self.name, self.model, role, usage)
        return Reply(
            text=str(message.get("content") or ""),
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            usage=usage, model=self.model, provider=self.name,
            raw_content=message,
        )

    @staticmethod
    def _flatten(message: Message) -> Dict[str, Any]:
        """Ollama expects plain strings, so collapse block lists into text."""
        content = message.content
        if isinstance(content, str):
            return {"role": message.role, "content": content}
        parts: List[str] = []
        for block in content or []:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_result":
                    inner = block.get("content")
                    parts.append("Tool result: %s" % (
                        inner if isinstance(inner, str) else json.dumps(inner, default=str)))
            else:
                parts.append(str(block))
        return {"role": message.role, "content": "\n".join(parts)}
