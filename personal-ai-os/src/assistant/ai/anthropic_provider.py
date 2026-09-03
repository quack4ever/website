"""Talking to Claude through Anthropic's official Python SDK.

WHAT LEAVES YOUR MAC
--------------------
When this provider is used, the conversation - which can include excerpts of
files you asked about - is sent to Anthropic's API over HTTPS.  Nothing else
is sent: not your file listing, not your permissions, not your audit log.
If ``ai.allow_cloud`` is false, this provider refuses to run at all, and if
``ai.send_file_content_to_cloud`` is false the orchestrator strips file bodies
before they ever reach here.

WHY THE SDK AND NOT PLAIN HTTP
------------------------------
The SDK handles retries, timeouts, streaming and the exact request shape, and
it is the surface Anthropic actually documents.  Hand-rolling HTTP would mean
re-implementing all of that and getting it subtly wrong.  The cost is one
dependency, which the installer puts in a private virtual environment.

WHY WE DRIVE THE TOOL LOOP OURSELVES
------------------------------------
The SDK offers a "tool runner" that executes tools for you automatically.  We
deliberately do not use it: our tools must be able to *stop and ask you*
mid-loop, and an automatic runner has nowhere to pause.  The orchestrator
runs the loop manually so the policy engine sits between every step.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..logging_setup import get
from .provider import (AIProvider, Availability, Message, Reply, ToolCall,
                       Usage, record_usage)

log = get(__name__)

DEFAULT_MODEL = "claude-opus-5"
#: Enables server-side fallback routing if a request is refused by a safety
#: classifier, so a refusal does not simply dead-end the assistant.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(AIProvider):
    name = "anthropic"
    is_local = False

    def __init__(self, settings: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(settings)
        self._client: Any = None
        self._beta_ok = True   # flipped off if the installed SDK is older

    # -- setup -------------------------------------------------------------
    @property
    def model(self) -> str:
        return str(self.settings.get("model") or DEFAULT_MODEL)

    def _key_env(self) -> str:
        return str(self.settings.get("api_key_env") or "ANTHROPIC_API_KEY")

    def _has_credentials(self) -> bool:
        """An unset env var does not mean 'no credentials'.

        The SDK also reads ANTHROPIC_AUTH_TOKEN and a profile written by
        `ant auth login`, so we check for those too before telling the user
        anything is missing.
        """
        if os.environ.get(self._key_env()) or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return True
        profile = Path.home() / ".config" / "anthropic"
        return profile.exists()

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    # -- availability ------------------------------------------------------
    def availability(self) -> Availability:
        from .. import config as config_module
        cfg = config_module.load()
        if not cfg.allow_cloud:
            return Availability(
                False,
                reason="Cloud AI is switched off in your settings.",
                fix="Run:  assistant config set ai.allow_cloud true   "
                    "(or use a local model instead).",
            )
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return Availability(
                False,
                reason="The 'anthropic' Python package is not installed.",
                fix="Run:  assistant doctor --fix   (or install it yourself "
                    "into the assistant's environment with "
                    "'~/Library/Application Support/PersonalAIOS/venv/bin/pip "
                    "install anthropic')",
            )
        if not self._has_credentials():
            return Availability(
                False,
                reason="No Anthropic credentials were found.",
                fix="Get a key from https://console.anthropic.com and run:\n"
                    "  export %s=sk-ant-...\n"
                    "Add that line to ~/.zshrc so it survives a restart."
                    % self._key_env(),
            )
        return Availability(True, reason="ready", detail={"model": self.model})

    # -- the call ----------------------------------------------------------
    def complete(self, system: str, messages: Sequence[Message],
                 tools: Optional[List[Dict[str, Any]]] = None,
                 max_tokens: Optional[int] = None,
                 role: Optional[str] = None) -> Reply:
        from ..errors import ProviderError, ProviderUnavailable

        state = self.availability()
        if not state.ok:
            raise ProviderUnavailable(
                what="The Anthropic model is not usable right now.",
                why=state.reason,
                tried="Checking credentials and the installed SDK",
                needs="A working Anthropic setup, or a different provider.",
                fix=state.fix,
            )

        from .. import config as config_module
        cfg = config_module.load()
        if cfg.get("ai.disclose_cloud_calls", True):
            log.info("sending %d message(s) to Anthropic (%s) for role=%s "
                     "- this leaves your Mac", len(messages), self.model, role)

        params: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(max_tokens or self.settings.get("max_tokens", 16000)),
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if system:
            params["system"] = system
        if tools:
            params["tools"] = tools

        thinking = str(self.settings.get("thinking", "adaptive")).lower()
        if thinking == "adaptive":
            # Adaptive thinking: the model decides how much to think per turn.
            # (The old fixed `budget_tokens` form is rejected by this model.)
            params["thinking"] = {"type": "adaptive"}
        effort = self.settings.get("effort")
        if effort:
            params["output_config"] = {"effort": str(effort)}

        message = self._send(params)
        return self._parse(message, role)

    def _send(self, params: Dict[str, Any]) -> Any:
        """Make the request, degrading gracefully on older SDK versions.

        We always stream: a long tool-using turn with a large max_tokens can
        exceed the plain HTTP timeout, and streaming avoids that entirely.
        ``get_final_message()`` reassembles the whole reply for us, so the
        rest of the code never has to think about chunks.
        """
        client = self._get_client()

        if self._beta_ok:
            try:
                with client.beta.messages.stream(
                    betas=[FALLBACK_BETA], fallbacks="default", **params
                ) as stream:
                    return stream.get_final_message()
            except (TypeError, AttributeError) as exc:
                # Installed SDK predates the beta - fall back permanently.
                log.info("server-side refusal fallback unavailable in this SDK "
                         "version (%s); continuing without it", exc)
                self._beta_ok = False
            except Exception as exc:
                text = str(exc).lower()
                if "fallback" in text or "beta" in text or "unexpected keyword" in text:
                    log.info("server-side refusal fallback rejected by the API "
                             "(%s); continuing without it", exc)
                    self._beta_ok = False
                else:
                    raise self._wrap(exc)

        try:
            with client.messages.stream(**params) as stream:
                return stream.get_final_message()
        except Exception as exc:
            raise self._wrap(exc)

    def _wrap(self, exc: Exception) -> Exception:
        """Turn an SDK exception into an error that explains itself."""
        from ..errors import ProviderError
        name = type(exc).__name__
        text = str(exc)
        status = getattr(exc, "status_code", None)

        if name in ("AuthenticationError", "PermissionDeniedError") or status == 401:
            return ProviderError(
                what="Anthropic rejected your credentials.",
                why=text,
                tried="Sending a request to the Messages API",
                needs="A valid API key.",
                fix="Check that %s is set correctly, and that the key has not "
                    "been revoked at https://console.anthropic.com" % self._key_env(),
            )
        if name == "RateLimitError" or status == 429:
            return ProviderError(
                what="Anthropic is rate-limiting this account.",
                why=text,
                tried="Sending a request to the Messages API",
                needs="A short wait, or a higher rate limit.",
                fix="Wait a minute and try again. For heavy use, raise your "
                    "limits in the Anthropic console.",
            )
        if name in ("APIConnectionError", "APITimeoutError"):
            return ProviderError(
                what="Could not reach Anthropic.",
                why=text,
                tried="Opening an HTTPS connection to the API",
                needs="A working internet connection.",
                fix="Check your network. If you are offline, switch to a local "
                    "model:  assistant config set ai.roles.reasoning ollama",
            )
        if status == 400:
            return ProviderError(
                what="Anthropic rejected the request as malformed.",
                why=text,
                tried="Sending a request to the Messages API",
                needs="A valid request shape.",
                fix="This is a bug in the assistant. Run 'assistant logs "
                    "--level error' and report it.",
            )
        return ProviderError(
            what="The Anthropic request failed.",
            why="%s: %s" % (name, text),
            tried="Sending a request to the Messages API",
            needs="A working connection and a valid key.",
            fix="Run 'assistant doctor' to check the setup.",
        )

    def _parse(self, message: Any, role: Optional[str]) -> Reply:
        text_parts: List[str] = []
        calls: List[ToolCall] = []

        for block in getattr(message, "content", []) or []:
            kind = getattr(block, "type", None)
            if kind == "text":
                text_parts.append(getattr(block, "text", ""))
            elif kind == "tool_use":
                calls.append(ToolCall(
                    id=str(getattr(block, "id", "")),
                    name=str(getattr(block, "name", "")),
                    arguments=dict(getattr(block, "input", {}) or {}),
                ))
            # 'thinking' blocks are intentionally not shown to the user here,
            # but they stay in raw_content so they can be echoed back
            # unchanged on the next turn, which the model requires.

        stop_reason = str(getattr(message, "stop_reason", "") or "end_turn")
        refused = stop_reason == "refusal"
        if refused:
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            text_parts = [
                "I was not able to answer that request (the safety system "
                "declined it%s). Try rephrasing, or ask me something else."
                % (", category: %s" % category if category else "")
            ]

        raw_usage = getattr(message, "usage", None)
        usage = Usage(
            input_tokens=int(getattr(raw_usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(raw_usage, "output_tokens", 0) or 0),
        )
        record_usage(self.name, self.model, role, usage)

        return Reply(
            text="\n".join(p for p in text_parts if p).strip(),
            tool_calls=calls,
            stop_reason=stop_reason,
            usage=usage,
            model=str(getattr(message, "model", self.model)),
            provider=self.name,
            raw_content=getattr(message, "content", None),
            refused=refused,
        )
