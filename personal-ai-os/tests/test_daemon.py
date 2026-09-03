"""Daemon tests: a real socket, a real server, real requests."""
from __future__ import annotations

import socket
import threading
import time

import pytest

from assistant import paths
from assistant.daemon import handlers, protocol, scheduler, server
from assistant.errors import AssistantError, DaemonError
from assistant.security import killswitch, pathguard


@pytest.fixture()
def running_daemon(sandbox):
    """Start a real daemon in a background thread and hand back a client."""
    thread = threading.Thread(target=server.serve, kwargs={"with_scheduler": False},
                              daemon=True)
    thread.start()
    for _ in range(100):                      # wait for the socket to appear
        if paths.socket_path().exists():
            break
        time.sleep(0.02)
    else:
        pytest.fail("the daemon never created its socket")

    client = server.Client(timeout=30)
    yield client

    # Stop it the same way `assistant daemon stop` does - never by signalling
    # the test runner's own process.
    try:
        client.call("shutdown")
    except Exception:
        server.stop_serving()
    thread.join(timeout=10)


# ===========================================================================
# Protocol
# ===========================================================================
def test_request_and_response_round_trip():
    request = protocol.make_request("status", {"a": 1})
    assert protocol.decode(protocol.encode(request)) == request
    assert protocol.decode(protocol.encode(protocol.ok("x", {"b": 2})))["ok"] is True
    assert protocol.decode(protocol.encode(protocol.fail("x", {"what": "no"})))["ok"] is False


def test_oversized_message_is_refused():
    left, right = socket.socketpair()
    try:
        left.sendall(b"x" * 5000)
        with pytest.raises(ValueError):
            protocol.read_line_buffered(right, limit=1000)
    finally:
        left.close()
        right.close()


# ===========================================================================
# Handlers work in-process (the CLI's fallback path)
# ===========================================================================
def test_handlers_dispatch_in_process(sandbox):
    result = handlers.dispatch("ping")
    assert result["pong"] is True


def test_unknown_command_lists_what_is_available(sandbox):
    with pytest.raises(AssistantError) as caught:
        handlers.dispatch("make_me_a_sandwich")
    assert "Unknown command" in caught.value.what
    assert "status" in caught.value.fix


def test_status_reports_the_whole_system(sandbox):
    status = handlers.dispatch("status")
    for key in ("version", "autonomy_mode", "scopes", "memory", "index", "models"):
        assert key in status


def test_grant_and_revoke_through_handlers(sandbox):
    result = handlers.dispatch("grant", {"path": str(sandbox.docs), "mode": "read"})
    assert result["granted"] is True
    assert len(pathguard.list_scopes()) == 1
    assert handlers.dispatch("revoke", {"path": str(sandbox.docs)})["revoked"] is True
    assert pathguard.list_scopes() == []


def test_config_set_refuses_an_invalid_value_and_saves_nothing(sandbox):
    with pytest.raises(AssistantError) as caught:
        handlers.dispatch("config_set", {"key": "autonomy.mode", "value": "yolo"})
    assert "invalid" in caught.value.what.lower()
    assert handlers.dispatch("config_show")["config"]["autonomy"]["mode"] == "assisted"


def test_stop_and_resume(sandbox):
    assert handlers.dispatch("stop", {"reason": "testing"})["stopped"] is True
    assert killswitch.is_engaged() is True
    assert handlers.dispatch("resume")["resumed"] is True
    assert killswitch.is_engaged() is False


# ===========================================================================
# Over a real socket
# ===========================================================================
def test_daemon_answers_over_the_socket(running_daemon):
    assert running_daemon.call("ping")["pong"] is True


def test_socket_is_owner_only(running_daemon):
    import stat
    mode = paths.socket_path().stat().st_mode
    assert not (mode & stat.S_IRGRP), "group can read the socket"
    assert not (mode & stat.S_IROTH), "everyone can read the socket"


def test_daemon_reports_errors_as_structured_failures(running_daemon):
    with pytest.raises(AssistantError) as caught:
        running_daemon.call("nonexistent_command")
    assert caught.value.what
    assert caught.value.fix


def test_daemon_survives_a_malformed_request(running_daemon):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(10)
    connection.connect(str(paths.socket_path()))
    connection.sendall(b"this is not json\n")
    reply = protocol.decode(protocol.read_line_buffered(connection))
    connection.close()
    assert reply["ok"] is False
    assert "could not understand" in reply["error"]["what"]
    # ...and it is still alive afterwards.
    assert running_daemon.call("ping")["pong"] is True


def test_daemon_handles_several_clients_at_once(running_daemon):
    results = []
    errors = []

    def worker():
        try:
            results.append(server.Client(timeout=20).call("status")["version"])
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert not errors, errors
    assert len(results) == 8


def test_is_running_detects_a_live_daemon(running_daemon):
    assert server.is_running() is True


def test_client_explains_a_missing_daemon(sandbox):
    with pytest.raises(DaemonError) as caught:
        server.Client(timeout=2).call("ping")
    assert "not running" in caught.value.what
    assert "daemon start" in caught.value.fix


def test_stale_socket_file_is_cleaned_up(sandbox):
    """A killed daemon leaves a socket file behind; it must not block a restart."""
    paths.run_dir().mkdir(parents=True, exist_ok=True)
    paths.socket_path().write_text("not really a socket")
    assert server.is_running() is False
    assert not paths.socket_path().exists()


# ===========================================================================
# Scheduler
# ===========================================================================
def test_default_jobs_are_created(sandbox):
    scheduler.ensure_default_jobs()
    names = {job["name"] for job in scheduler.list_jobs()}
    assert {"index_refresh", "memory_decay", "approval_expiry", "housekeeping"} <= names


def test_running_a_job_records_its_outcome(sandbox):
    scheduler.ensure_default_jobs()
    result = scheduler.run_job("approval_expiry")
    assert result["ok"] is True
    job = [j for j in scheduler.list_jobs() if j["name"] == "approval_expiry"][0]
    assert job["last_status"] == "ok"
    assert job["next_run"] > job["last_run"]


def test_a_failing_job_is_recorded_not_swallowed(sandbox, monkeypatch):
    scheduler.ensure_default_jobs()

    def explode():
        raise RuntimeError("job blew up")
    monkeypatch.setitem(scheduler.BUILTIN_JOBS, "approval_expiry", explode)

    result = scheduler.run_job("approval_expiry")
    assert result["ok"] is False
    assert "blew up" in result["error"]
    job = [j for j in scheduler.list_jobs() if j["name"] == "approval_expiry"][0]
    assert job["last_status"] == "failed"


def test_the_kill_switch_stops_scheduled_work(sandbox):
    scheduler.ensure_default_jobs()
    killswitch.engage("testing")
    try:
        result = scheduler.run_job("index_refresh")
        assert result["ok"] is False
        assert "emergency stop" in result["skipped"]
        assert all("skipped" in r for r in scheduler.Scheduler().tick_once())
    finally:
        killswitch.release()


def test_scheduled_jobs_obey_the_same_permissions(sandbox):
    """An 'autonomous' job gets no special powers: with no scopes granted,
    the index job has nothing it is allowed to look at."""
    result = scheduler.run_job("index_refresh")
    assert result["result"]["skipped"] == "no folders granted"


def test_a_job_can_be_disabled(sandbox):
    scheduler.ensure_default_jobs()
    assert scheduler.set_enabled("index_refresh", False) is True
    due = [j["name"] for j in scheduler.due_jobs()]
    assert "index_refresh" not in due
