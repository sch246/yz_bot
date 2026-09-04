"""Per-window operation history: what the model actually did, across turns.

The chat context is rebuilt from ``chatlog`` at the start of every turn, and
``chatlog`` holds QQ messages only -- the ``assistant(tool_calls)`` messages and
their ``tool`` results live in one ``llm.Chat.messages`` list and die with it.
So without this module the model starts each turn unable to say which file it
edited, which module it loaded, or which request it sent last time; the only
trace that survived was whatever it happened to mention in chat.

This is that missing parallel track.  It is deliberately *not* merged into
``chatlog``: chatlog is the conversation between people and the bot, this is the
model's own record of its actions, and the two compress on completely different
schedules -- a chat message matters as long as the conversation does, while a
tool call stops mattering the moment its conclusion is drawn.

Which is the other half of the design: the track is not meant to be kept whole.
Tool calls serve a purpose, and once the conclusion is reached the intermediate
steps are noise.  Mainstream agents dodge this with sub-agents -- the sub-agent
runs the steps and returns only its conclusion, so the main context never sees
them.  Here the model does it itself, by calling ``meta.condense_ops`` when it
has an answer, which is why every entry carries a ``cid`` the model can name.
"""

from __future__ import annotations

from collections.abc import Iterable
from threading import RLock
from typing import Any

from mods import INFRA


PHASE = INFRA
LOAD_AFTER = ("storage",)

# WHY: 单条截断，只留够认出"干了什么"。完整输出模型在产出它的那一轮已经看过了；轨道是
# 索引不是副本。留长了每轮开头都要重付一次这些 token，而那正是这条轨道想省的东西。
MAX_FIELD = 400
# WHY: 这是**兜底**，不是压缩机制。压缩靠模型显式 condense；这个上限只防"从没收缩过的
# 窗口"把存储和上下文撑爆。设得比正常用量高一截，正常路径永远碰不到它。
# 不要把它调小当成自动压缩用：按条数截断会在结论产出之前砍掉前提，那正是这套设计要避免的。
MAX_ENTRIES = 200

_lock = RLock()


def _storage():
    from mods import storage

    return storage


def _key(window: tuple[str, Any] | None) -> str | None:
    """One storage file per chat window, named like the window tuple."""
    if not window:
        return None
    kind, value = window
    return f"{kind}_{value}"


def _state(window: tuple[str, Any] | None) -> dict | None:
    name = _key(window)
    if name is None:
        return None
    state = _storage().get("oplog", name)
    state.setdefault("entries", [])
    state.setdefault("next", 1)
    return state


def _clip(value: Any) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text if len(text) <= MAX_FIELD else text[:MAX_FIELD] + f"…(共 {len(text)} 字)"


def record(window: tuple[str, Any] | None, name: str, arguments: Any, content: Any, tool_call_id: str = "") -> str | None:
    """Append one finished tool call and return the cid the model can name it by."""
    with _lock:
        state = _state(window)
        if state is None:
            return None
        cid = f"op{state['next']}"
        state["next"] = state["next"] + 1
        state["entries"].append({
            "cid": cid,
            "kind": "call",
            "name": str(name),
            "arguments": _clip(arguments),
            "content": _clip(content),
            "tool_call_id": str(tool_call_id),
        })
        # 兜底裁剪，见 MAX_ENTRIES 的说明。
        if len(state["entries"]) > MAX_ENTRIES:
            del state["entries"][: len(state["entries"]) - MAX_ENTRIES]
        return cid


def entries(window: tuple[str, Any] | None) -> list[dict]:
    with _lock:
        state = _state(window)
        return list(state["entries"]) if state else []


def call_ids(window: tuple[str, Any] | None, cids: Iterable[str]) -> tuple[list[str], list[str]]:
    """Map cids to their tool_call ids; also report the cids nothing matches."""
    wanted = {str(value) for value in cids}
    found: list[str] = []
    seen: set[str] = set()
    for entry in entries(window):
        if entry.get("cid") in wanted:
            seen.add(entry["cid"])
            if entry.get("tool_call_id"):
                found.append(entry["tool_call_id"])
    return found, sorted(wanted - seen)


def condense(window: tuple[str, Any] | None, cids: Iterable[str], conclusion: str) -> int:
    """Replace the named entries with a single conclusion, keeping their place."""
    wanted = {str(value) for value in cids}
    with _lock:
        state = _state(window)
        if state is None:
            return 0
        kept: list[dict] = []
        replaced = 0
        inserted = False
        for entry in state["entries"]:
            if entry.get("cid") not in wanted:
                kept.append(entry)
                continue
            replaced += 1
            if not inserted:
                inserted = True
                kept.append({
                    "cid": entry["cid"],
                    "kind": "conclusion",
                    "name": "",
                    "arguments": "",
                    "content": _clip(conclusion),
                    "tool_call_id": "",
                    "covers": sorted(wanted),
                })
        if replaced:
            state["entries"] = kept
        return replaced


def clear(window: tuple[str, Any] | None) -> None:
    """Drop this window's track. The cid counter deliberately does not rewind.

    WHY: 清空只删条目，不把计数器归零。归零会让 cid 被重复使用，而旧的 cid 可能还印在
    某轮上下文的 tool result 里；下一次 condense_ops 就会指到一条完全不相干的操作上。
    """
    with _lock:
        state = _state(window)
        if state is not None:
            state["entries"] = []


def render(window: tuple[str, Any] | None) -> str | None:
    """Render the track as one context block, or None when there is nothing yet.

    WHY: 这条进 messages（由 chat.init_chat 放进去），不是 hint。它记的是"发生过一次"
    的事实，不是随时可重算的状态——文件已经改了、消息已经发了，重算不出来。判据见
    llm.Chat.hints 旁边那条。
    """
    listed = entries(window)
    if not listed:
        return None
    lines = ["## 本窗口的操作历史（你自己过去的工具调用，不是聊天内容）"]
    for entry in listed:
        if entry.get("kind") == "conclusion":
            covers = entry.get("covers") or []
            covered = f"（收缩自 {', '.join(covers)}）" if covers else ""
            lines.append(f"- [{entry['cid']}] 结论{covered}：{entry['content']}")
        else:
            lines.append(f"- [{entry['cid']}] {entry['name']}({entry['arguments']}) -> {entry['content']}")
    lines.append(
        "已经得出结论的调用用 condense_ops(cids, conclusion) 收成一条结论，"
        "既缩短这里，也缩短当前上下文。"
    )
    return "\n".join(lines)
