"""OneBot HTTP ingress and API calls."""

from __future__ import annotations

import json
import logging
import re
import socket
import sys
import threading
from urllib import request

from mods import INFRA


PHASE = INFRA
# This edge is primarily an exit guarantee: stop ingress before storage's
# final save, so no new event can race that save.
LOAD_AFTER = ("log", "scheduler", "storage")

REQUEST_TIMEOUT = 5
post_url = "http://127.0.0.1"
post_map: dict[str, str | int] = {}

_log = logging.getLogger(__name__)
_listen_socket: socket.socket | None = None
_socket_lock = threading.Lock()


def _argument(name: str, default: str) -> str:
    arguments = sys.argv[1:]
    try:
        return arguments[arguments.index(name) + 1]
    except (ValueError, IndexError):
        return default


post_port = _argument("-q", "5700")
listen_port = _argument("-p", "5701")
listen = ("127.0.0.1", int(listen_port))


def _target_port(params: dict) -> str | int:
    """Choose one window mapping; a missing ``user_id`` cannot erase a group map."""
    group_id = params.get("group_id")
    if group_id is not None:
        return post_map.get(f"g{group_id}", post_port)
    user_id = params.get("user_id")
    if user_id is not None:
        return post_map.get(f"u{user_id}", post_port)
    return post_port


def call_api(action: str, **params) -> dict:
    body = json.dumps(params).encode("utf-8")
    outgoing = request.Request(
        f"{post_url}:{_target_port(params)}/{action}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(outgoing, timeout=REQUEST_TIMEOUT) as response:
        payload = response.read()
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"retcode": 400, "wording": payload.decode("utf-8", "replace")}
    return value if isinstance(value, dict) else {"retcode": 400, "wording": repr(value)}


def send_msg(msg: str, user_id=None, group_id=None, **params) -> dict:
    if user_id is None and group_id is None:
        raise ValueError("user_id or group_id is required")
    return call_api("send_msg", message=msg, user_id=user_id, group_id=group_id, **params)


_content_length = re.compile(br"Content-Length:\s*(\d+)", re.IGNORECASE)


def _recv_request(client: socket.socket, buffer_size: int = 4096) -> bytes:
    client.settimeout(5)
    request = bytearray()
    while b"\r\n\r\n" not in request:
        chunk = client.recv(buffer_size)
        if not chunk:
            return bytes(request)
        request.extend(chunk)
    header, body = bytes(request).split(b"\r\n\r\n", 1)
    match = _content_length.search(header)
    if match:
        expected = int(match.group(1))
        while len(body) < expected:
            chunk = client.recv(min(buffer_size, expected - len(body)))
            if not chunk:
                break
            body += chunk
        body = body[:expected]
    else:
        client.settimeout(0.2)
        try:
            while True:
                chunk = client.recv(buffer_size)
                if not chunk:
                    break
                body += chunk
        except socket.timeout:
            pass
    return header + b"\r\n\r\n" + body


def request_to_json(request: str | bytes) -> dict | None:
    if isinstance(request, str):
        request = request.encode("utf-8")
    separator = request.find(b"\r\n\r\n")
    if separator < 0:
        return None
    body = request[separator + 4:].strip()
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Keep compatibility with the chunked requests accepted by the old
        # listener without implementing an HTTP framework in the Bot.
        start, end = body.find(b"{"), body.rfind(b"}")
        if start < 0 or end < start:
            return None
        try:
            value = json.loads(body[start:end + 1])
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
    return value if isinstance(value, dict) else None


_response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


def recv_msg() -> dict | None:
    server = _listen_socket
    if server is None:
        raise RuntimeError("OneBot listener is not loaded")
    try:
        client, _ = server.accept()
    except OSError:
        if _listen_socket is None:
            return None
        raise
    with client:
        try:
            event = request_to_json(_recv_request(client))
            client.sendall(_response)
            return event
        except socket.timeout:
            _log.warning("OneBot ingress timed out")
        except BrokenPipeError:
            _log.warning("OneBot ingress client closed before acknowledgement")
        except Exception:
            _log.exception("failed to receive OneBot event")
    return None


def on_load(ctx) -> None:
    global _listen_socket
    with _socket_lock:
        if _listen_socket is not None:
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(listen)
            server.listen(100)
        except Exception:
            server.close()
            raise
        _listen_socket = server


def on_exit() -> None:
    global _listen_socket
    with _socket_lock:
        server, _listen_socket = _listen_socket, None
    if server is not None:
        try:
            server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        server.close()
