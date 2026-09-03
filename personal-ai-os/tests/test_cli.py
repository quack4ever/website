"""CLI tests: the commands a person actually types."""
from __future__ import annotations

import json

import pytest

from assistant.cli import main as cli
from assistant.cli import render
from assistant.security import consent, killswitch, pathguard


def run(capsys, *argv):
    """Run a CLI command and capture what the user would see."""
    code = cli.main(["--local", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ===========================================================================
# Basics
# ===========================================================================
def test_no_command_prints_help(sandbox, capsys):
    code, out, _ = run(capsys)
    assert code == 0
    assert "Personal AI OS" in out
    assert "examples:" in out


def test_status_works_on_a_fresh_install(sandbox, capsys):
    code, out, _ = run(capsys, "status")
    assert code == 0
    assert "Autonomy mode" in out
    assert "none yet" in out


def test_json_output_is_machine_readable(sandbox, capsys):
    code, out, _ = run(capsys, "--json", "status")
    assert code == 0
    assert json.loads(out)["autonomy_mode"] == "assisted"


def test_errors_explain_themselves(sandbox, capsys):
    code, out, err = run(capsys, "permissions", "grant", "/nowhere/at/all")
    assert code == 1
    assert "Problem:" in err
    assert "What you can do:" in err


# ===========================================================================
# Permissions
# ===========================================================================
def test_grant_and_revoke(sandbox, capsys):
    code, out, _ = run(capsys, "permissions", "grant", str(sandbox.docs))
    assert code == 0 and "Granted read access" in out
    assert len(pathguard.list_scopes()) == 1

    code, out, _ = run(capsys, "permissions", "revoke", str(sandbox.docs))
    assert code == 0 and "Revoked" in out


def test_cannot_grant_a_protected_folder(sandbox, capsys):
    code, _, err = run(capsys, "permissions", "grant", str(sandbox.user / ".ssh"))
    assert code == 1
    assert "permanently blocked" in err.lower() or "protected" in err.lower()


def test_permissions_shows_every_column_even_with_long_paths(sandbox, capsys):
    """Regression: long paths used to push the MODE column off the table."""
    pathguard.grant(str(sandbox.docs), mode="readwrite", note="a note here")
    code, out, _ = run(capsys, "permissions")
    assert code == 0
    assert "MODE" in out and "NOTE" in out
    assert "readwrite" in out


def test_capabilities_lists_risk_tiers(sandbox, capsys):
    code, out, _ = run(capsys, "permissions", "capabilities")
    assert code == 0
    assert "files.delete" in out and "critical" in out
    assert "CRITICAL actions always ask" in out


def test_rule_add_and_remove(sandbox, capsys):
    code, out, _ = run(capsys, "permissions", "rule", "add",
                       "--effect", "deny", "--capability", "files.delete",
                       "--note", "never")
    assert code == 0 and "added" in out
    code, out, _ = run(capsys, "permissions", "rule", "list")
    assert "files.delete" in out and "never" in out
    code, out, _ = run(capsys, "permissions", "rule", "remove", "1")
    assert code == 0 and "Removed" in out


# ===========================================================================
# Index and search
# ===========================================================================
def test_index_build_and_search(sandbox, capsys):
    (sandbox.docs / "biology.md").write_text("Photosynthesis converts light energy.")
    pathguard.grant(str(sandbox.docs), mode="read")

    code, out, _ = run(capsys, "index", "build")
    assert code == 0 and "Index updated" in out

    code, out, _ = run(capsys, "index", "search", "photosynthesis")
    assert code == 0 and "biology.md" in out


def test_search_with_no_index_explains_what_to_do(sandbox, capsys):
    code, out, _ = run(capsys, "index", "search", "anything")
    assert code == 0
    assert "No matches" in out


# ===========================================================================
# Memory
# ===========================================================================
def test_memory_add_list_forget(sandbox, capsys):
    code, out, _ = run(capsys, "memory", "add", "I", "like", "tea",
                       "--kind", "preference")
    assert code == 0 and "Remembered" in out

    code, out, _ = run(capsys, "memory", "list")
    assert "I like tea" in out

    code, out, _ = run(capsys, "memory", "forget", "1")
    assert code == 0 and "Forgotten" in out


def test_memory_refuses_a_secret_from_the_command_line(sandbox, capsys):
    code, _, err = run(capsys, "memory", "add", "my", "password", "is", "swordfish99")
    assert code == 1
    assert "secret" in err.lower()


def test_forget_all_needs_confirmation(sandbox, capsys):
    run(capsys, "memory", "add", "something")
    code, _, err = run(capsys, "memory", "forget", "--all")
    assert code == 1
    assert "--yes" in err
    code, out, _ = run(capsys, "memory", "forget", "--all", "--yes")
    assert code == 0 and "Forgot 1" in out


# ===========================================================================
# Approvals and the emergency stop
# ===========================================================================
def test_approvals_flow_through_the_cli(sandbox, capsys):
    request_id = consent.create(capability="files.delete", tool="file_delete",
                                arguments={"path": "/tmp/x"}, risk="high",
                                summary="delete /tmp/x")
    code, out, _ = run(capsys, "approvals")
    assert code == 0 and request_id in out and "assistant approve" in out

    code, out, _ = run(capsys, "approve", request_id)
    assert code == 0 and "Approved" in out
    assert consent.is_approved(request_id) is True


def test_deny_records_a_refusal(sandbox, capsys):
    request_id = consent.create(capability="files.delete", tool="file_delete",
                                arguments={}, risk="high", summary="x")
    code, out, _ = run(capsys, "deny", request_id)
    assert code == 0 and "Refused" in out
    assert consent.get(request_id)["status"] == "denied"


def test_stop_and_resume(sandbox, capsys):
    code, out, _ = run(capsys, "stop", "--reason", "testing")
    assert code == 0 and "STOPPED" in out
    assert killswitch.is_engaged() is True

    code, out, _ = run(capsys, "resume")
    assert code == 0 and "Resumed" in out
    assert killswitch.is_engaged() is False


# ===========================================================================
# Config, logs, tools, doctor
# ===========================================================================
def test_config_set_and_show(sandbox, capsys):
    code, out, _ = run(capsys, "config", "set", "autonomy.mode", "supervised")
    assert code == 0 and "supervised" in out
    code, out, _ = run(capsys, "--json", "config", "show", "autonomy")
    assert json.loads(out)["config"]["mode"] == "supervised"


def test_config_set_rejects_an_invalid_value(sandbox, capsys):
    code, _, err = run(capsys, "config", "set", "autonomy.mode", "yolo")
    assert code == 1
    assert "invalid" in err.lower()


def test_config_set_parses_booleans_and_numbers(sandbox, capsys):
    run(capsys, "config", "set", "memory.enabled", "false")
    code, out, _ = run(capsys, "--json", "config", "show", "memory")
    assert json.loads(out)["config"]["enabled"] is False
    run(capsys, "config", "set", "planner.candidates", "7")
    code, out, _ = run(capsys, "--json", "config", "show", "planner")
    assert json.loads(out)["config"]["candidates"] == 7


def test_logs_show_what_happened(sandbox, capsys):
    pathguard.grant(str(sandbox.docs), mode="read")
    run(capsys, "permissions", "grant", str(sandbox.downloads))
    code, out, _ = run(capsys, "logs")
    assert code == 0
    assert "scope_granted" in out or "EVENT" in out


def test_tools_lists_every_tool_with_its_risk(sandbox, capsys):
    code, out, _ = run(capsys, "tools")
    assert code == 0
    assert "file_read" in out and "RISK" in out


def test_doctor_reports_health(sandbox, capsys):
    code, out, _ = run(capsys, "doctor")
    assert code == 0
    assert "Health check" in out
    assert "Database" in out


def test_daemon_status_without_a_daemon(sandbox, capsys):
    code, out, _ = run(capsys, "daemon", "status")
    assert code == 0
    assert "not running" in out


# ===========================================================================
# Rendering helpers
# ===========================================================================
def test_table_keeps_all_columns_when_content_is_too_wide(monkeypatch):
    monkeypatch.setattr(render, "width", lambda default=80: 40)
    out = render.table([["/a/very/long/path/that/will/not/fit/at/all.txt", "readwrite"]],
                       headers=["PATH", "MODE"])
    assert "MODE" in out
    assert "readwrite" in out


def test_table_elides_paths_in_the_middle(monkeypatch):
    monkeypatch.setattr(render, "width", lambda default=80: 40)
    out = render.table([["/start/middle/middle/middle/middle/middle/end.txt", "x"]])
    assert "…" in out
    assert "end.txt" in out


def test_empty_table_says_so():
    assert "nothing to show" in render.table([])


def test_colour_is_disabled_when_not_a_terminal(monkeypatch):
    monkeypatch.setattr(render.sys.stdout, "isatty", lambda: False, raising=False)
    assert render.paint("hello", "red") == "hello"
