"""Lazy device-owned client for the historical encrypted ``%`` link."""

from __future__ import annotations

import json
from pathlib import Path
import threading

from mods import FEATURE, portfunc


PHASE = FEATURE
LOAD_AFTER = ("portfunc",)
CONFIG_PATH = Path("data/device/server.json")

_client: portfunc.Client | None = None
_lock = threading.RLock()


def _configuration() -> tuple[str, int, str, bool, float | None]:
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(
            "server 设备配置不存在: data/device/server.json"
        ) from error
    if not isinstance(value, dict):
        raise TypeError("server 设备配置必须是对象")
    host = value.get("host")
    port = value.get("port")
    private_key_file = value.get("private_key_file")
    is_str = value.get("is_str", True)
    timeout = value.get("timeout", 10)
    if not isinstance(host, str) or not host:
        raise TypeError("server.host 必须是非空字符串")
    if isinstance(port, bool) or not isinstance(port, int):
        raise TypeError("server.port 必须是整数")
    if not isinstance(private_key_file, str) or not private_key_file:
        raise TypeError("server.private_key_file 必须是非空字符串")
    if not isinstance(is_str, bool):
        raise TypeError("server.is_str 必须是布尔值")
    if timeout is not None and (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0
    ):
        raise TypeError("server.timeout 必须是正数或 null")
    return host, port, private_key_file, is_str, timeout


def _connect() -> portfunc.Client:
    host, port, private_key_file, is_str, timeout = _configuration()
    identity = portfunc.load_this_priv(private_key_file)
    return portfunc.Client(host, port, identity, is_str=is_str, timeout=timeout)


def call(value: str | bytes):
    """Call the configured encrypted remote function over one ordered socket."""
    global _client
    with _lock:
        if _client is None:
            _client = _connect()
        try:
            return _client.call(value)
        except (OSError, ConnectionError):
            _client.close()
            _client = None
            raise


def on_exit() -> None:
    global _client
    with _lock:
        client, _client = _client, None
        if client is not None:
            client.close()
