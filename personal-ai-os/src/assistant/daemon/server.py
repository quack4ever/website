"""The daemon: the little robot that stays awake.

WHAT A DAEMON IS
----------------
A program with no window that keeps running in the background.  Yours starts
when you log in (launchd does that) and waits for instructions.  The word is
old Unix jargon; it just means "background helper".

WHAT IT DOES WHILE IDLE
-----------------------
Almost nothing.  It sleeps on a socket waiting for a message, and once a
minute the scheduler wakes up to see whether any autonomous job is due.  It
does not scan your disk continuously and it does not phone home.

HOW IT IS PROTECTED
-------------------
  * The socket file is mode 0600 inside a 0700 directory, so only your user
    account can even open it.
  * We refuse to start if the socket directory is not owned by us.
  * Requests are size-limited, so a runaway client cannot exhaust memory.
  * Every request is logged.
  * The daemon runs as YOU, not root. It can never do anything you could not
    do yourself at the Terminal.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import socketserver
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .. import audit, config as config_module, db, paths
from ..errors import AssistantError, DaemonError
from ..logging_setup import get, setup
from ..version import __version__
from . import handlers, protocol
from .scheduler import Scheduler

log = get(__name__)

#: The live server, so it can be stopped without sending a signal - which is
#: what `assistant daemon stop` and the test suite both need.
_SERVER: Optional[socketserver.BaseServer] = None


def stop_serving() -> bool:
    """Ask a running in-process daemon to shut down. Returns True if it was up."""
    if _SERVER is None:
        return False
    threading.Thread(target=_SERVER.shutdown, daemon=True).start()
    return True



class _RequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        connection: socket.socket = self.request
        connection.settimeout(600)
        try:
            raw = protocol.read_line_buffered(connection)
        except ValueError as exc:
            connection.sendall(protocol.encode(protocol.fail("?", {
                "what": "That request was too large.", "why": str(exc),
                "fix": "Send a smaller request."})))
            return
        if not raw:
            return

        request_id = "?"
        try:
            message = protocol.decode(raw)
            request_id = str(message.get("id", "?"))
            command = str(message.get("command", ""))
            args = message.get("args") or {}
            if not isinstance(args, dict):
                raise ValueError("args must be an object")

            started = time.time()
            data = handlers.dispatch(command, args)
            elapsed = int((time.time() - started) * 1000)
            log.info("handled %s in %dms", command, elapsed)
            connection.sendall(protocol.encode(protocol.ok(request_id, data)))

        except AssistantError as exc:
            connection.sendall(protocol.encode(protocol.fail(request_id, exc.to_dict())))
        except (json.JSONDecodeError, ValueError) as exc:
            connection.sendall(protocol.encode(protocol.fail(request_id, {
                "code": "protocol.invalid",
                "what": "The daemon could not understand that request.",
                "why": str(exc),
                "fix": "This is a bug in the client. Run 'assistant logs'."})))
        except Exception as exc:  # never let one bad request kill the daemon
            log.exception("unhandled error while serving a request")
            connection.sendall(protocol.encode(protocol.fail(request_id, {
                "code": "daemon.error",
                "what": "The daemon hit an unexpected problem.",
                "why": "%s: %s" % (type(exc).__name__, exc),
                "fix": "Run 'assistant logs --level error' and report it."})))


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True
    #: Bound so a flood of connections cannot spawn unbounded threads.
    request_queue_size = 32


def _check_socket_dir(directory: Path) -> None:
    """Refuse to start if someone else could tamper with our socket."""
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(paths.DIR_MODE)
    except OSError:
        pass
    try:
        info = directory.stat()
    except OSError as exc:
        raise DaemonError(
            what="Could not inspect the run directory.", why=str(exc),
            tried="Checking %s" % directory, needs="A readable directory.",
            fix="Check the permissions on %s" % directory)
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise DaemonError(
            what="The run directory belongs to a different user.",
            why="%s is owned by uid %d, but this process is uid %d. Starting "
                "anyway could let another account talk to your assistant."
                % (directory, info.st_uid, os.getuid()),
            tried="Checking ownership of the socket directory",
            needs="The directory to be owned by you.",
            fix="Remove %s and run 'assistant daemon start' again." % directory)


def is_running() -> bool:
    """Is a daemon already listening?  Cleans up a stale socket if not."""
    socket_path = paths.socket_path()
    if not socket_path.exists():
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(2.0)
    try:
        probe.connect(str(socket_path))
        probe.sendall(protocol.encode(protocol.make_request("ping")))
        return bool(protocol.read_line_buffered(probe))
    except (OSError, socket.timeout):
        # The file exists but nothing is listening: a previous daemon was
        # killed. Remove the corpse so we can bind again.
        try:
            socket_path.unlink()
        except OSError:
            pass
        return False
    finally:
        probe.close()


def serve(with_scheduler: bool = True) -> None:
    """Run the daemon.  Blocks until stopped."""
    setup(level="INFO")
    paths.ensure_dirs()
    db.connect()

    socket_path = paths.socket_path()
    _check_socket_dir(socket_path.parent)

    if is_running():
        raise DaemonError(
            what="The assistant daemon is already running.",
            why="Something is already listening on %s." % socket_path,
            tried="Connecting to the existing socket",
            needs="Only one daemon at a time.",
            fix="Run 'assistant daemon stop' first, or 'assistant daemon "
                "restart' to replace it.")

    if socket_path.exists():
        socket_path.unlink()

    server = _Server(str(socket_path), _RequestHandler)
    try:
        os.chmod(socket_path, paths.FILE_MODE)   # 0600: only you may connect
    except OSError:
        pass

    paths.pid_path().write_text(str(os.getpid()) + "\n", encoding="utf-8")
    log.info("daemon %s listening on %s (pid %d)", __version__, socket_path, os.getpid())
    audit.record("daemon.started", actor="system",
                 arguments={"pid": os.getpid(), "socket": str(socket_path)})

    scheduler: Optional[Scheduler] = None
    if with_scheduler and config_module.load().get("daemon.enabled", True):
        scheduler = Scheduler()
        scheduler.start()

    global _SERVER
    _SERVER = server

    def on_signal(signum: int, _frame: Any) -> None:
        log.info("received signal %d, shutting down", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    # signal.signal() raises ValueError anywhere but the main thread, so a
    # daemon started inside a worker thread (embedded, or under test) would
    # die right here.  Only install handlers where they are legal.
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)
    else:
        log.info("not the main thread: skipping signal handlers "
                 "(use stop_serving() or the 'shutdown' command)")

    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        _SERVER = None
        if scheduler:
            scheduler.stop()
        server.server_close()
        for path in (socket_path, paths.pid_path()):
            try:
                path.unlink()
            except OSError:
                pass
        audit.record("daemon.stopped", actor="system")
        log.info("daemon stopped")


class Client:
    """The CLI's end of the conversation."""

    def __init__(self, socket_path: Optional[Path] = None, timeout: float = 600.0) -> None:
        self.socket_path = socket_path or paths.socket_path()
        self.timeout = timeout

    def available(self) -> bool:
        return self.socket_path.exists()

    def call(self, command: str, args: Optional[Dict[str, Any]] = None) -> Any:
        """Send one request and return its data, raising on failure."""
        request = protocol.make_request(command, args)
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        try:
            connection.connect(str(self.socket_path))
            connection.sendall(protocol.encode(request))
            raw = protocol.read_line_buffered(connection)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise DaemonError(
                what="The assistant daemon is not running.",
                why=str(exc),
                tried="Connecting to %s" % self.socket_path,
                needs="A running daemon.",
                fix="Start it with 'assistant daemon start'. (Most commands "
                    "work without it - the CLI falls back to running the work "
                    "itself.)")
        except socket.timeout:
            raise DaemonError(
                what="The daemon did not answer in time.",
                why="No reply within %.0f seconds." % self.timeout,
                tried="Waiting for a response to '%s'" % command,
                needs="A responsive daemon.",
                fix="Run 'assistant daemon restart'.")
        finally:
            connection.close()

        if not raw:
            raise DaemonError(
                what="The daemon closed the connection without replying.",
                why="It may have crashed while handling '%s'." % command,
                tried="Reading the response",
                needs="A complete response.",
                fix="Run 'assistant logs --level error' to see what happened.")

        message = protocol.decode(raw)
        if message.get("ok"):
            return message.get("data")
        error = message.get("error") or {}
        raise AssistantError(
            what=error.get("what", "The daemon reported an error."),
            why=error.get("why", ""), tried=error.get("tried", ""),
            needs=error.get("needs", ""), fix=error.get("fix", ""),
            details=error.get("details", {}))
