"""AI layer tests: the router must never silently fake a model."""
from __future__ import annotations

from assistant.ai import Router
from assistant.ai.offline_provider import NO_MODEL_MESSAGE, OfflineProvider
from assistant.ai.provider import Message, Reply


def test_offline_provider_says_it_has_no_model(sandbox):
    reply = OfflineProvider({}).complete("sys", [Message("user", "hello")])
    assert "do not have a language model configured" in reply.text
    assert reply.stop_reason == "no_model"


def test_offline_provider_replays_scripted_replies(sandbox):
    provider = OfflineProvider({})
    provider.push_text("first")
    provider.push_tool_call("file_read", {"path": "/tmp/x"})
    assert provider.complete("", []).text == "first"
    second = provider.complete("", [])
    assert second.tool_calls[0].name == "file_read"
    # Queue drained -> back to the honest default, not a repeat.
    assert provider.complete("", []).text == NO_MODEL_MESSAGE


def test_unavailable_provider_falls_back_to_an_explanation_not_a_guess(sandbox):
    """With no API key and no Ollama, we must get instructions - not an answer."""
    router = Router(sandbox.cfg())
    provider = router.for_role("reasoning")
    reply = provider.complete("sys", [Message("user", "what is 2+2?")])
    assert "4" not in reply.text, "an unavailable model must not answer anyway"
    assert "not installed" in reply.text or "could not use" in reply.text.lower()


def test_allow_cloud_false_disables_cloud_providers(sandbox):
    cfg = sandbox.cfg()
    cfg.set("ai.allow_cloud", False)
    cfg.save()
    from assistant.ai.anthropic_provider import AnthropicProvider
    state = AnthropicProvider({"model": "claude-opus-5"}).availability()
    assert state.ok is False
    assert "switched off" in state.reason


def test_router_status_lists_every_role(sandbox):
    status = Router(sandbox.cfg()).status()
    assert set(status["roles"]) == {"reasoning", "planning", "fast", "coding",
                                    "vision", "local"}
    for entry in status["roles"].values():
        # Every unavailable role must carry a fix the user can act on.
        if not entry["available"]:
            assert entry["fix"], entry


def test_forced_provider_is_used_for_every_role(sandbox):
    stub = OfflineProvider({})
    router = Router(sandbox.cfg())
    router.force(stub)
    assert router.for_role("planning") is stub
    assert router.for_role("vision") is stub


def test_ollama_is_marked_local(sandbox):
    router = Router(sandbox.cfg())
    assert router.build("ollama").is_local is True
    assert router.build("anthropic").is_local is False
    assert "ollama" in router.local_only()
