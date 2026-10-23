"""Read the ``.backfill.jsonl`` sidecars that older offline backfill wrote beside the day files.

WHY: 2026-10-23 起补回的消息直接插进普通日档（``chatlog.insert_recovered``），不再写这种文件。
已有的旁档留在原处、照常可读，因为它们记着当时补回的原文；删掉这个模块之前要先确认设备上
已经没有 ``*.backfill.jsonl``，或者把它们并进日档（那是改生产数据，要维护者授权）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Iterator
from uuid import uuid4


_lock = threading.Lock()


def _base(root: str | os.PathLike | None) -> Path:
    return Path("chatlog" if root is None else root)


def _message_id(value: Any) -> str:
    if isinstance(value, bool) or not str(value).lstrip("-").isdigit():
        raise ValueError("回填消息缺少 QQ message_id")
    return str(int(value))


def _sequence(value: Any) -> int:
    if isinstance(value, bool) or not str(value).lstrip("-").isdigit():
        raise ValueError("回填消息缺少 NapCat message_seq")
    return int(value)


def _time(value: Any) -> int:
    if isinstance(value, bool) or not str(value).isdigit():
        raise ValueError("回填消息缺少秒级时间")
    return int(value)


def _validate(message: Any) -> tuple[str, int, int]:
    if not isinstance(message, dict) or "message" not in message:
        raise ValueError("回填消息缺少原始正文")
    sender = message.get("sender")
    if not isinstance(sender, dict) or "user_id" not in sender:
        raise ValueError("回填消息缺少原始作者")
    _message_id(sender["user_id"])
    return _message_id(message.get("message_id")), _time(message.get("time")), _sequence(message.get("message_seq"))


def _validate_window(message: dict[str, Any], kind: str, target: int | str) -> None:
    if message.get("message_type", kind) != kind:
        raise ValueError("回填消息类型与档案路径不一致")
    field = "group_id" if kind == "group" else "target_id"
    value = message.get(field)
    if value is not None and _message_id(value) != _message_id(target):
        raise ValueError("回填消息窗口与档案路径不一致")


def _origin(path: Path, line: int, root: str | os.PathLike | None) -> str:
    return f"{path.relative_to(_base(root))}:{line}"


def _iter_sidecar(path: Path, root: str | os.PathLike | None, *, offset: int = 0,
                  line: int = 0) -> Iterator[tuple[int, int, dict[str, Any]]]:
    """Isolate an uncommitted final fragment; reject a bad complete line."""
    if not path.exists():
        return
    with path.open("r+b") as file:
        file.seek(offset)
        while True:
            offset = file.tell()
            chunk = file.readline()
            if not chunk:
                break
            if not chunk.endswith(b"\n"):
                complete = file.tell() - len(chunk)
                backup = path.with_name(path.name + ".incomplete-" + uuid4().hex)
                with backup.open("xb") as isolated:
                    isolated.write(chunk)
                    isolated.flush()
                    os.fsync(isolated.fileno())
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                file.seek(complete)
                if file.read() != chunk:
                    raise RuntimeError("回填档案在残尾修复期间被外部修改")
                file.truncate(complete)
                file.flush()
                os.fsync(file.fileno())
                break
            line += 1
            try:
                message = json.loads(chunk)
                _validate(message)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                raise ValueError(f"回填档案损坏：{_origin(path, line, root)}") from error
            yield line, offset, message


def _day_file(path: str | os.PathLike, root: str | os.PathLike | None) -> tuple[Path, tuple[str, ...]]:
    base = _base(root)
    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.is_relative_to(base):
        candidate = base / candidate
    try:
        relative = candidate.relative_to(base)
    except ValueError as error:
        raise ValueError("回填路径不在档案目录中") from error
    parts = relative.parts
    if (len(parts) != 4 or parts[0] not in ("group", "private") or not parts[1].isdigit()
            or re.fullmatch(r"\d{4}-\d{2}", parts[2]) is None
            or re.fullmatch(r"\d{2}\.backfill\.jsonl", parts[3]) is None
            or not candidate.resolve().is_relative_to(base.resolve())):
        raise ValueError("回填路径不是窗口日期文件")
    return candidate, parts


def _project(message: dict[str, Any], candidate: Path, line: int, parts: tuple[str, ...],
             root: str | os.PathLike | None) -> dict[str, Any]:
    _validate_window(message, parts[0], parts[1])
    record = dict(message)
    record["_source"] = "napcat_backfill"
    record["_version"] = "raw"
    record["_derived"] = []
    record["_missing"] = [] if "post_type" in record else ["post_type"]
    record["_guessed"] = []
    record["_log_origin"] = _origin(candidate, line, root)
    if "message_type" not in record:
        record["message_type"] = parts[0]
        record["_derived"].append("message_type")
    if "user_id" not in record:
        record["user_id"] = int(message["sender"]["user_id"])
        record["_derived"].append("user_id")
    if parts[0] == "group":
        if "group_id" not in record:
            record["group_id"] = int(parts[1])
            record["_derived"].append("group_id")
    elif "target_id" not in record:
        record["target_id"] = int(parts[1])
        record["_derived"].append("target_id")
    return record


def iter_day(path: str | os.PathLike, *, root: str | os.PathLike | None = None) -> Iterator[dict[str, Any]]:
    """Stream originals and stable origins from one sidecar day file."""
    candidate, parts = _day_file(path, root)
    with _lock:
        for line, _, message in _iter_sidecar(candidate, root):
            yield _project(message, candidate, line, parts, root)


def read_day(path: str | os.PathLike, *, root: str | os.PathLike | None = None) -> list[dict[str, Any]]:
    """Materialize one sidecar day for existing range-query callers."""
    return list(iter_day(path, root=root))
