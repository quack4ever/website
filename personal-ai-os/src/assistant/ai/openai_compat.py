"""Any provider that speaks the OpenAI chat-completions shape.

This is deliberately generic: it works with a self-hosted server, a local
LM Studio, a corporate gateway, or a third-party API - anything exposing
``POST {base_url}/chat/completions``.  You supply the base URL, the model
name, and the environment variable holding the key.

It is NOT used to reach Claude.  Claude is reached through Anthropic's own
SDK in ``anthropic_provider.py``, which is the supported path.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

from .http_json import post_json
from .provider import (AIProvider, Availability, Message, Reply, ToolCall,
                       Usage, record_usage)


class OpenAICompatProvider(AIProvider):
    name = "openai_compat"
    is_local = False

    @property
    def base_url(self) -> str:
        return str(self.settings.get("base_url") or "").rstrip("/")

    def _key(self) -> str:
        return os.environ.get(str(self.settings.get("api_key_env") or "OPENAI_API_KEY"), "")

    def availability(self) -> Availability:
        from .. import config as config_module
        if not config_module.load().allow_cloud and "127.0.0.1" not in self.base_url \
                and "localhost" not in self.base_url:
            return Availability(False, reason="Cloud AI is switched off in your settings.",
                                fix="Run:  assistant config set ai.allow_cloud true")
        if not self.base_url:
            return Availability(
                False, reason="No base_url is configured for this provider.",
                fix="Run:  assistant config set ai.providers.openai_compat.base_url "
                    "https://your-endpoint/v1")
        if not self.model:
            return Availability(
                False, reason="No model name is configured for this provider.",
                fix="Run:  assistant config set ai.providers.openai_compat.model <name>")
        if not self._key() and not self.base_url.startswith("http://127.0.0.1"):
            return Availability(
                False,
                reason="No API key found in $%s." % self.settings.get("api_key_env", "OPENAI_API_KEY"),
                fix="Run:  export %s=..." % self.settings.get("api_key_env", "OPENAI_API_KEY"))
        return Availability(True, reason="ready")

    def complete(self, system: str, messages: Sequence[Message],
                 tools: Optional[List[Dict[str, Any]]] = None,
                 max_tokens: Optional[int] = None,
                 role: Optional[str] = None) -> Reply:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [self._flatten(m) for m in messages],
            "max_tokens": int(max_tokens or self.settings.get("max_tokens", 8192)),
        }
        if tools:
            payload["tools"] = [
                {"type": "function",
                 "function": {"name": t["name"], "description": t["description"],
                              "parameters": t["input_schema"]}}
                for t in tools
            ]

        headers = {}
        if self._key():
            headers["Authorization"] = "Bearer " + self._key()

        data = post_json(self.base_url + "/chat/completions", payload,
                         headers=headers, provider=self.name)
        choices = data.get("choices") or [{}]
        message = (choices[0] or {}).get("message") or {}

        calls: List[ToolCall] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            calls.append(ToolCall(id=str(call.get("id", "call")),
                                  name=str(function.get("name", "")),
                                  arguments=dict(arguments or {})))

        raw_usage = data.get("usage") or {}
        usage = Usage(int(raw_usage.get("prompt_tokens", 0) or 0),
                      int(raw_usage.get("completion_tokens", 0) or 0))
        record_usage(self.name, self.model, role, usage)
        return Reply(text=str(message.get("content") or ""), tool_calls=calls,
                     stop_reason="tool_use" if calls else "end_turn",
                     usage=usage, model=self.model, provider=self.name,
                     raw_content=message)

    @staticmethod
    def _flatten(message: Message) -> Dict[str, Any]:
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
        return {"role": message.role, "content": "\n".join(parts)}
