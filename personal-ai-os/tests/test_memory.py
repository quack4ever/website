"""Memory tests: user control, secret refusal, decay."""
from __future__ import annotations

import pytest

from assistant import tools
from assistant.memory import store
from assistant.tools import registry


# ===========================================================================
# Secret refusal - the most important behaviour here
# ===========================================================================
@pytest.mark.parametrize("secret", [
    "my api key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345",
    "password = hunter2please",
    "card number 4111 1111 1111 1111",
    "ssn 123-45-6789",
    "github token ghp_abcdefghijklmnopqrstuvwxyz0123456789",
])
def test_secrets_are_never_stored(sandbox, secret):
    with pytest.raises(store.MemoryRefused) as caught:
        store.remember(secret)
    assert "secret" in caught.value.what.lower()
    assert store.stats()["total"] == 0


def test_ordinary_preference_is_stored(sandbox):
    item = store.remember("I prefer short bullet-point answers", kind="preference")
    assert item["id"]
    assert store.stats()["total"] == 1


def test_refusal_is_written_to_the_audit_log(sandbox):
    from assistant import audit
    with pytest.raises(store.MemoryRefused):
        store.remember("password: correcthorsebattery")
    events = [row["event"] for row in audit.tail(10)]
    assert "memory.refused_secret" in events


# ===========================================================================
# User control
# ===========================================================================
def test_memory_can_be_switched_off_entirely(sandbox):
    cfg = sandbox.cfg()
    cfg.set("memory.enabled", False)
    cfg.save()
    with pytest.raises(store.MemoryRefused):
        store.remember("something", cfg=sandbox.cfg())
    assert store.recall("anything", cfg=sandbox.cfg()) == []


def test_list_search_edit_forget(sandbox):
    first = store.remember("The science project is due 14 March", kind="project")
    store.remember("I like working in the morning", kind="preference")

    assert len(store.list_all()) == 2
    assert len(store.list_all(kind="project")) == 1
    assert any("science" in i["text"] for i in store.search("science"))

    store.edit(first["id"], text="The science project is due 21 March")
    assert "21 March" in store.get(first["id"])["text"]
    assert store.search("science")[0]["text"].endswith("21 March")

    assert store.forget(first["id"]) is True
    assert store.get(first["id"]) is None
    assert store.search("science") == []


def test_forget_all_requires_confirmation(sandbox):
    store.remember("something")
    with pytest.raises(store.MemoryRefused):
        store.forget_all(confirm=False)
    assert store.stats()["total"] == 1
    assert store.forget_all(confirm=True) == 1
    assert store.stats()["total"] == 0


def test_deleted_memories_remain_visible_in_the_export(sandbox):
    item = store.remember("temporary belief")
    store.forget(item["id"])
    exported = store.export()
    assert any(e["id"] == item["id"] and e["deleted"] == 1 for e in exported)


def test_provenance_is_recorded(sandbox):
    item = store.remember("fact", source="conversation:abc123")
    assert store.get(item["id"])["source"] == "conversation:abc123"


# ===========================================================================
# Decay and confidence
# ===========================================================================
def test_confidence_decays_with_age(sandbox):
    row = {"confidence": 0.8, "pinned": 0, "last_used": None,
           "created_at": "2020-01-01T00:00:00+00:00", "updated_at": None}
    decayed = store.effective_confidence(row, half_life_days=90)
    assert decayed < 0.05, "a five-year-old unused memory should have faded"


def test_pinned_memories_do_not_decay(sandbox):
    row = {"confidence": 0.8, "pinned": 1, "last_used": None,
           "created_at": "2020-01-01T00:00:00+00:00", "updated_at": None}
    assert store.effective_confidence(row, half_life_days=90) == 0.8


def test_repeating_a_memory_strengthens_it_without_duplicating(sandbox):
    first = store.remember("I prefer dark mode", kind="preference", confidence=0.5)
    second = store.remember("I prefer dark mode", kind="preference", confidence=0.5)
    assert first["id"] == second["id"]
    assert store.stats()["total"] == 1
    assert second["confidence"] > first["confidence"]


def test_recall_filters_out_faded_memories(sandbox):
    from assistant import db
    strong = store.remember("current project is chemistry", kind="project", confidence=0.9)
    weak = store.remember("old belief about something", kind="fact", confidence=0.3)
    db.connect().execute(
        "UPDATE memory SET created_at='2015-01-01T00:00:00+00:00', "
        "updated_at='2015-01-01T00:00:00+00:00', last_used=NULL WHERE id=?",
        (weak["id"],))
    recalled = [i["id"] for i in store.recall("", cfg=sandbox.cfg())]
    assert strong["id"] in recalled
    assert weak["id"] not in recalled


def test_max_entries_evicts_the_weakest_not_the_pinned(sandbox):
    cfg = sandbox.cfg()
    cfg.set("memory.max_entries", 3)
    cfg.save()
    pinned = store.remember("keep me", confidence=0.1, pinned=True, cfg=sandbox.cfg())
    for n in range(6):
        store.remember("filler %d" % n, confidence=0.5 + n / 100.0, cfg=sandbox.cfg())
    assert store.stats()["total"] <= 3
    assert store.get(pinned["id"]) is not None


# ===========================================================================
# Tools
# ===========================================================================
def test_memory_write_tool_needs_approval_by_default(sandbox):
    tools.load_all()
    result = registry.execute("memory_remember", {"text": "I like tea"},
                              registry.ExecContext())
    assert result.ok is False
    assert result.needs_approval
    assert store.stats()["total"] == 0


def test_memory_read_tool_is_low_risk_and_runs(sandbox):
    tools.load_all()
    store.remember("I like tea", kind="preference")
    result = registry.execute("memory_recall", {}, registry.ExecContext())
    assert result.ok is True
    assert result.data["count"] == 1


def test_memory_tool_refuses_secrets_through_the_tool_path_too(sandbox):
    tools.load_all()
    cfg = sandbox.cfg(); cfg.set("autonomy.mode", "supervised")
    result = registry.execute(
        "memory_remember",
        {"text": "my password is supersecret123", "kind": "fact"},
        registry.ExecContext(config=cfg))
    assert result.ok is False
    assert "secret" in result.error["what"].lower()


@pytest.mark.parametrize("text,should_flag", [
    ("my password is supersecret123", True),
    ("my password is correcthorsebatterystaple", True),
    ("the api key is sk_live_abcdefgh12345", True),
    ("my password is stored in 1Password", False),
    ("I forgot my password again", False),
    ("the password field is required on that form", False),
    ("Remember I prefer bullet points", False),
])
def test_secret_detection_balances_recall_against_false_alarms(text, should_flag):
    """Catching real secrets matters; so does not refusing ordinary notes."""
    from assistant import redact
    assert bool(redact.find_secrets(text)) is should_flag
