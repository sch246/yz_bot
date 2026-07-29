"""Minimal access to the process-level ``config.json``.

Configuration stays separate from ordinary feature storage during the
architecture cutover.  This module deliberately performs no I/O at import
time; identity and op initialization call it from their lifecycle hooks.
"""

from __future__ import annotations

import json
import os
import tempfile
from threading import RLock
from typing import Any


config_file = "config.json"
init: dict[str, Any] = {}
_lock = RLock()


class dicts:
    @staticmethod
    def get(mapping: dict[str, Any], *keys: str) -> Any:
        if not keys:
            return mapping
        value: Any = mapping
        for key in keys:
            value = value[key]
        return value

    @staticmethod
    def set(mapping: dict[str, Any], value: Any, *keys: str) -> None:
        if not keys:
            raise ValueError("至少需要一个配置键")
        target = mapping
        for key in keys[:-1]:
            child = target.get(key)
            if not isinstance(child, dict):
                child = {}
                target[key] = child
            target = child
        target[keys[-1]] = value

    @staticmethod
    def update(defaults: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """Recursively merge dictionaries, with ``override`` taking priority."""
        result = dict(defaults)
        for key, value in override.items():
            if isinstance(result.get(key), dict) and isinstance(value, dict):
                result[key] = dicts.update(result[key], value)
            else:
                result[key] = value
        return result


def _read() -> dict[str, Any]:
    with open(config_file, encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError("config.json 的根值必须是对象")
    return value


def _write(value: dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(config_file))
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=directory,
        prefix=".config.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, indent=4, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, config_file)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def init_config() -> None:
    with _lock:
        _write(init)


def load_config(*keys: str) -> Any:
    with _lock:
        return dicts.get(_read(), *keys)


def save_config(value: Any, *keys: str) -> None:
    with _lock:
        document = _read()
        dicts.set(document, value, *keys)
        _write(document)


def del_config(*keys: str) -> None:
    if not keys:
        raise ValueError("至少需要一个配置键")
    with _lock:
        document = _read()
        parent = dicts.get(document, *keys[:-1]) if len(keys) > 1 else document
        del parent[keys[-1]]
        _write(document)


def init_or_load_config(defaults: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        document = _read() if os.path.exists(config_file) else dict(init)
        merged = dicts.update(defaults, document)
        _write(merged)
        return merged


def dict_save_config(values: dict[str, Any]) -> None:
    with _lock:
        document = _read() if os.path.exists(config_file) else dict(init)
        _write(dicts.update(document, values))
