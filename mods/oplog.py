"""Per-window operation history, stored whole and replayed as real tool records.

The chat context is rebuilt from ``chatlog`` at the start of every turn, and
``chatlog`` holds QQ messages only -- the ``assistant(tool_calls)`` messages and
their ``tool`` results live in one ``llm.Chat.messages`` list and die with it.
So without this module the model starts each turn unable to say which file it
edited, which module it loaded, or which request it sent last time.

This is that missing parallel track.  It is deliberately *not* merged into
``chatlog``: chatlog is the conversation between people and the bot, this is the
model's own record of its actions, and the two compress on completely different
schedules -- a chat message matters as long as the conversation does, while a
tool call stops mattering the moment its conclusion is drawn.

WHY: 重建成**真的**工具调用记录（assistant(tool_calls) + tool），不是摘要成一条 system。
同一件事有两种表示是有代价的：轮内模型看到的是自己的 tool_call 结构，跨轮忽然变成另一种
模态的条目列表。重建之后轮内轮外只有一个形状，收缩也就只有一套删除逻辑，不必在"消息手术"
和"条目删除"之间维护两份等价实现。

WHY: 因此写入时**存全量**，不截断。截断过的内容重建出来会冒充原文——那比明写的摘要更糟，
因为它看起来就是当时真实发生的事。上限只作为兜底存在，见下面两个常量。

WHY: cid 用跨轮稳定的 uid，不用每轮重排的序号。原因不是"稳定一点更好"，而是序号在这里会
自我否定：结论住在模型那条 condense_ops 调用的 arguments 里（见 meta.condense_ops），
而那条调用会被重建进以后每一轮的上下文。若编号每轮重排，模型下一轮读到自己写的
["op3","op4"] 时，这两个号已经指向别的调用了，而它无从察觉。唯一的补救是每轮改写模型自己
写下的文字，那更糟。能重算的（tool result 开头的 [opN] 前缀）才可以是视图。
cid 只需窗口内唯一——收缩和重建都在单个窗口里，不存在跨窗口引用。
"""

from __future__ import annotations

from collections.abc import Iterable
from threading import RLock
from typing import Any

from mods import INFRA


PHASE = INFRA
LOAD_AFTER = ("storage",)

# WHY: 两个上限都是**兜底**，不是压缩机制。压缩靠模型显式 condense_ops；这两个只防单次
# 巨量输出（比如读了个大文件）和"从没收缩过的窗口"把存储与上下文撑爆。设得比正常用量高
# 一截，正常路径碰不到。别把它们调小当成自动压缩用：按大小自动砍会在结论产出之前砍掉前提。
MAX_ENTRY_CHARS = 20000
MAX_TOTAL_CHARS = 200000
# 人看的那份（#ops）才截断，模型那份不截断。
DISPLAY_CHARS = 200

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


def _cap(value: Any) -> str:
    text = "" if value is None else str(value)
    if len(text) <= MAX_ENTRY_CHARS:
        return text
    return text[:MAX_ENTRY_CHARS] + f"\n…（截断，原文共 {len(text)} 字）"


def _trim(state: dict) -> None:
    """Drop the oldest whole rounds once the window is over budget.

    WHY: 按**整轮**丢，不按条。丢掉一轮里的一部分，重建出来就是一条 assistant 的
    tool_calls 少了对应的 tool 消息，供应商直接拒——兜底裁剪不该把上下文变成非法的。
    """
    def size(entry: dict) -> int:
        return len(entry["content"]) + len(entry["arguments"])

    listed = state["entries"]
    total = sum(size(entry) for entry in listed)
    while total > MAX_TOTAL_CHARS and listed:
        oldest = listed[0]["round"]
        total -= sum(size(entry) for entry in listed if entry["round"] == oldest)
        listed = [entry for entry in listed if entry["round"] != oldest]
        state["entries"] = listed


def record(
    window: tuple[str, Any] | None,
    name: str,
    arguments: Any,
    content: Any,
    tool_call_id: str = "",
    round_id: str = "",
) -> str | None:
    """Store one finished tool call whole; return the cid the model names it by."""
    with _lock:
        state = _state(window)
        if state is None:
            return None
        cid = f"op{state['next']}"
        state["next"] = state["next"] + 1
        state["entries"].append({
            "cid": cid,
            # 同一条 assistant 消息里并发的调用共用一个 round，重建时据此还原分组。
            "round": str(round_id or tool_call_id),
            "name": str(name),
            "arguments": _cap(arguments),
            "content": _cap(content),
            "tool_call_id": str(tool_call_id),
        })
        _trim(state)
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


def condense(window: tuple[str, Any] | None, cids: Iterable[str]) -> int:
    """Delete the named calls from the track. Nothing is written in their place.

    WHY: 纯删除，不留结论条目。结论已经在模型那条 condense_ops 调用的 arguments 里，
    而那条调用本身也是一次被记录的工具调用，会跟着重建回来。再存一份就是第二个副本。

    WHY: 按整轮删，理由与 _trim 相同：半轮会重建出配对不上的 tool_calls。

    WHY: 后来的收缩可以把更早那次 condense_ops 调用本身也收掉，于是旧结论消失。这是
    刻意的——更高层的结论本就该取代下层的。别把它当成 bug 去"保护"结论条目。
    """
    wanted = {str(value) for value in cids}
    with _lock:
        state = _state(window)
        if state is None:
            return 0
        listed = state["entries"]
        rounds = {entry["round"] for entry in listed if entry["cid"] in wanted}
        partial = [entry["cid"] for entry in listed if entry["round"] in rounds and entry["cid"] not in wanted]
        if partial:
            raise ValueError("同一轮里的调用必须一起收缩，还差: " + ", ".join(sorted(partial)))
        kept = [entry for entry in listed if entry["round"] not in rounds]
        removed = len(listed) - len(kept)
        if removed:
            state["entries"] = kept
        return removed


def clear(window: tuple[str, Any] | None) -> None:
    """Drop this window's track. The cid counter deliberately does not rewind.

    WHY: 清空只删条目，不把计数器归零。归零会让 cid 被重复使用，而旧的 cid 可能还印在
    某轮上下文的 tool result 里；下一次 condense_ops 就会指到一条完全不相干的操作上。
    """
    with _lock:
        state = _state(window)
        if state is not None:
            state["entries"] = []


def build_messages(window: tuple[str, Any] | None) -> list[dict]:
    """Rebuild the track as the real tool-call records it came from.

    WHY: assistant 消息的 content 一律留空。模型当时说的可见正文已经作为 QQ 消息发出去、
    进了 chatlog（见 message.py 的 _chatlog_write），下一轮 get_msgs 会重建它。这里再带一份
    就是同一句话在上下文里出现两遍。所以这条轨道只负责它独有的部分：调用与结果。

    WHY: 也不带 reasoning_content——轨道里本来就没有，而"assistant 不带 reasoning、
    请求停在 tool 上"这个形状实跑验证过被接受（deepseek-v4-flash, 2026-09-04）。同一次
    验证还覆盖了这些记录后面紧跟聊天消息（tool -> user）的形状。
    """
    listed = entries(window)
    if not listed:
        return []
    messages: list[dict] = []
    order: list[str] = []
    grouped: dict[str, list[dict]] = {}
    for entry in listed:
        round_id = entry["round"]
        if round_id not in grouped:
            grouped[round_id] = []
            order.append(round_id)
        grouped[round_id].append(entry)
    for round_id in order:
        batch = grouped[round_id]
        messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": entry["tool_call_id"] or entry["cid"],
                    "type": "function",
                    "function": {"name": entry["name"], "arguments": entry["arguments"]},
                }
                for entry in batch
            ],
        })
        messages.extend(
            {
                "role": "tool",
                "tool_call_id": entry["tool_call_id"] or entry["cid"],
                "content": f"[{entry['cid']}] {entry['content']}",
            }
            for entry in batch
        )
    return messages


def render(window: tuple[str, Any] | None) -> str | None:
    """Render the track for a human (``#ops``); truncated, unlike the model's copy."""
    listed = entries(window)
    if not listed:
        return None
    def short(value: str) -> str:
        text = " ".join(str(value).split())
        return text if len(text) <= DISPLAY_CHARS else text[:DISPLAY_CHARS] + f"…({len(text)}字)"
    return "\n".join([
        "本窗口的操作历史:",
        *(f"- [{entry['cid']}] {entry['name']}({short(entry['arguments'])}) -> {short(entry['content'])}"
          for entry in listed),
    ])
