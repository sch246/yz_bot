"""The one context she works in until the next handoff, kept as a plain file.

WHY: 2026-10-22 维护者定：只要不交接，就一直是同一个上下文——每次唤醒只往后追加，
不重组、不截断、不投影。交接就是在这同一段末尾说一句"这段要被移除了，确认交接"，
她确认各类信息都已经在该在的地方，这段才归档、下一段从新的笔记重新开始。

``data/context/current.json`` 是当前这一段：系统提示每次重建（它在交接之间不变），
其余消息原样存着，包括原生的 assistant/tool 配对。归档的段在 ``archive/`` 下按开始
时刻命名，原样留着：那就是"她当时看到了什么、做了什么"，排查时最先要看的东西。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import time

ROOT = Path("data") / "context"
_CURRENT = ROOT / "current.json"
_ARCHIVE = ROOT / "archive"
_lock = threading.Lock()


def fresh() -> dict:
    return {"started": time.time(), "told": None, "windows": [], "last_shown": {}, "scopes": [], "messages": []}


def load() -> dict:
    """The current segment; a fresh empty one when there is none."""
    with _lock:
        if not _CURRENT.is_file():
            return fresh()
        try:
            data = json.loads(_CURRENT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 文件坏了：挪到一边留着排查，从新的一段开始，不让一次写坏卡住她。
            broken = _CURRENT.with_name(f"current.broken-{time.strftime('%Y%m%d-%H%M%S')}.json")
            os.replace(_CURRENT, broken)
            return fresh()
    segment = {**fresh(), **data}
    segment["messages"] = _repair(segment["messages"])
    return segment


def _repair(messages: list[dict]) -> list[dict]:
    """Give every tool call a result, so an interrupted round can still be sent.

    WHY: ^C 或进程退出可能停在一批工具调用中间：assistant 带着 tool_calls，后面的 tool
    结果不全。供应商会拒绝这样的历史，所以补一条"被打断"的结果，而不是删掉那次调用——
    调用本身发生过，行动记录里也有。
    """
    repaired: list[dict] = []
    waiting: list[str] = []
    for message in messages:
        if waiting and message.get("role") != "tool":
            repaired.extend({"role": "tool", "tool_call_id": call_id, "content": "（这次调用被打断，没有结果）"}
                            for call_id in waiting)
            waiting = []
        repaired.append(message)
        if message.get("role") == "assistant" and message.get("tool_calls"):
            waiting = [call.get("id") for call in message["tool_calls"]]
        elif message.get("role") == "tool" and message.get("tool_call_id") in waiting:
            waiting.remove(message["tool_call_id"])
    repaired.extend({"role": "tool", "tool_call_id": call_id, "content": "（这次调用被打断，没有结果）"}
                    for call_id in waiting)
    return repaired


def save(segment: dict) -> None:
    with _lock:
        ROOT.mkdir(parents=True, exist_ok=True)
        temporary = _CURRENT.with_name("current.json.tmp")
        temporary.write_text(json.dumps(segment, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, _CURRENT)


def archive(segment: dict, kind: str = "") -> str:
    """Move a finished segment under ``archive/``; returns its name."""
    with _lock:
        _ARCHIVE.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S", time.localtime(segment["started"])) + (f"-{kind}" if kind else "")
        target = _ARCHIVE / f"{name}.json"
        number = 1
        while target.exists():
            number += 1
            target = _ARCHIVE / f"{name}-{number}.json"
        segment = {**segment, "ended": time.time()}
        target.write_text(json.dumps(segment, ensure_ascii=False), encoding="utf-8")
        if kind == "" and _CURRENT.is_file():
            _CURRENT.unlink()
        return target.stem


def each_archived(since: float = 0):
    """``(name, data)`` for archived segments that ended after *since*, oldest first."""
    if not _ARCHIVE.is_dir():
        return
    for path in sorted(_ARCHIVE.glob("*.json")):
        # 归档文件写下后不再改，修改时间就是结束时刻：早于 since 的不用解析。
        if since and path.stat().st_mtime <= since:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("ended", 0) > since:
            yield path.stem, data


def archived(since: float = 0) -> list[dict]:
    """Archived segments that ended after *since*, oldest first: name, times, size, windows."""
    return [{"name": name, "started": data.get("started", 0), "ended": data.get("ended", 0),
             "messages": len(data.get("messages", [])), "windows": data.get("windows", [])}
            for name, data in each_archived(since)]


def read_archived(name: str) -> dict | None:
    if not re.fullmatch(r"[0-9A-Za-z_-]+", name or ""):
        return None
    path = _ARCHIVE / f"{name}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def render(messages: list[dict]) -> str:
    """A segment as plain text, the way a person reads it back."""
    lines = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "[图片]") if isinstance(part, dict) else str(part) for part in content)
        content = str(content or "")
        if role == "assistant":
            if content.strip():
                lines.append("【她】" + content)
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                lines.append(f"【她调用】{function.get('name')}({function.get('arguments')})")
        elif role == "tool":
            lines.append("【结果】" + content)
        elif content.startswith("<system-reminder>") and len(content) > 1500:
            # 开头那份完整工具状态很长，又和排查无关；中途的状态变化很短，照样显示。
            lines.append(f"【工具状态，{len(content)} 字，略】")
        else:
            lines.append(f"【{role}】" + content)
    return "\n".join(lines)
