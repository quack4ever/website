"""How the CLI and the daemon talk to each other.

THE CHANNEL
-----------
A Unix domain socket: a special file at
``~/Library/Application Support/PersonalAIOS/run/assistantd.sock``.
Writing to it sends a message to the daemon; nothing goes over the network.

WHY NOT A NETWORK PORT?
-----------------------
Three reasons, all of them security:
  1. A port on 127.0.0.1 is reachable by EVERY program and every user account
     on the machine. A socket file is protected by ordinary file permissions,
     so 0600 means "only me".
  2. Opening a listening port can trigger macOS network prompts and firewall
     rules for something that never needed the network.
  3. There is no risk of accidentally binding to a public interface.

THE FORMAT
----------
One JSON object per line ("newline-delimited JSON"). Simple to produce, simple
to parse, and easy to debug by hand - you can literally watch the traffic.

    -> {"id": "abc", "command": "status", "args": {}}
    <- {"id": "abc", "ok": true, "data": {...}}
"""
from __future__ import annotations

import json
import socket
import uuid
from typing import Any, Dict, Optional

#: Refuse anything larger, so a runaway client cannot exhaust memory.
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
ENCODING = "utf-8"


def make_request(command: str, args: Optional[Dict[str, Any]] = None,
                 request_id: Optional[str] = None) -> Dict[str, Any]:
    return {"id": request_id or uuid.uuid4().hex[:8],
            "command": command, "args": args or {}}


def ok(request_id: str, data: Any) -> Dict[str, Any]:
    return {"id": request_id, "ok": True, "data": data}


def fail(request_id: str, error: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": request_id, "ok": False, "error": error}


def encode(message: Dict[str, Any]) -> bytes:
    return (json.dumps(message, default=str) + "\n").encode(ENCODING)


def decode(line: bytes) -> Dict[str, Any]:
    return json.loads(line.decode(ENCODING))


def read_line(connection: socket.socket, limit: int = MAX_MESSAGE_BYTES) -> Optional[bytes]:
    """Read one newline-terminated message, refusing oversized ones."""
    chunks = bytearray()
    while True:
        try:
            byte = connection.recv(1)
        except (ConnectionResetError, OSError):
            return None
        if not byte:
            return bytes(chunks) if chunks else None
        if byte == b"\n":
            return bytes(chunks)
        chunks.extend(byte)
        if len(chunks) > limit:
            raise ValueError("message longer than %d bytes" % limit)


def read_line_buffered(connection: socket.socket,
                       limit: int = MAX_MESSAGE_BYTES) -> Optional[bytes]:
    """Same as read_line but reads in blocks - much faster for large payloads."""
    buffer = bytearray()
    while b"\n" not in buffer:
        try:
            chunk = connection.recv(65536)
        except (ConnectionResetError, OSError):
            return None
        if not chunk:
            return bytes(buffer) if buffer else None
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise ValueError("message longer than %d bytes" % limit)
    line, _, _rest = bytes(buffer).partition(b"\n")
    return line
