"""Her subconscious: one reminder when the segment has grown a lot, nothing else.

WHY: 维护者 2026-10-25 08:16 定的方向：不再自动跑一次、替她决定收哪段。每长 5 万 token 只插
一条提醒（这一段多长、能收哪几段、各多大）；收不收、收哪段、摘要怎么写都由她自己调 `compress`。
提醒和 hint 一样只挂在这一次请求末尾、不写进这一段（基准除外），下次收的时候自然不在；旧版那条
自动整理还带着自己的一条会话、自己的账本和一套截断，看的是转写不是原样，已经删了。

WHY: 提醒里的时刻必须是 `_fold_since` / `_fold_between` 真能用的记号：这一段第一次唤醒连着交接
留下的笔记，不列；最近一次刚做完、下次多半还要接着做，也不列。`compress` 那头再校验一次。
"""

from __future__ import annotations

import time

from mods import storage

import mods.chat as _chat_root
from . import screen as _screen, segment as _segment

# 这一段比上次提醒时又长这么多 token 才提醒一次（维护者 2026-10-25：5 万）。
_GROWTH_TOKENS = 50_000


def on() -> bool:
    # 维护者 2026-10-24：默认开。
    return bool(storage.get("", "agent").get("subconscious", True))


def nudge(segment: dict) -> str | None:
    """A one-off reminder once the segment has grown `_GROWTH_TOKENS` since the last one."""
    messages = segment.get("messages") or []
    if not messages:
        return None
    size = _size(segment)
    settled = segment.get("settled")
    if not isinstance(settled, int) or size < settled:
        # 第一次见到这一段（或她自己 dismiss 清短了）：记下现在的大小，这次不提醒。
        segment["settled"] = size
        _segment.save(segment)
        return None
    if size - settled < _GROWTH_TOKENS:
        return None
    offered = _offered(segment)
    if not offered:
        # 只有第一次唤醒和最近一次：没有能收的，先不提醒，等它们之间多出一次来。
        return None
    segment["settled"] = size
    segment["nudged_at"] = time.time()
    _segment.save(segment)
    counts = _chat_root._agent._text_tokens
    listing = "\n".join(f"{mark['at'][11:]}（之后约 {_amount(counts(messages[mark['index']:]))}）"
                        for mark in offered)
    return (f"上下文又长了，这一段现在约 {_amount(size)} token。省着点用。\n"
            f"能收的唤醒（括号里是收掉以后，后面要按原价重读多少）：\n{listing}\n"
            f"要收就用 compress：compress(from_time=\"{offered[0]['at'][11:]}\", to_time=\"\", summary=\"……\")\n"
            f"不收就不用管，这条提醒到下次收的时候会一起没。")


def _size(segment: dict) -> int:
    """How long the segment is, the same measure the handoff threshold uses."""
    return _chat_root._agent._context_size(segment.get("tokens"), segment["messages"])


def _offered(segment: dict) -> list[dict]:
    """Wakes `compress` may start at: not the first (it carries the handoff notes), not the latest."""
    marks = [mark for mark in segment.get("marks") or [] if mark.get("index", 0) > 0]
    return marks[:-1]


def _amount(tokens: int) -> str:
    return f"{tokens / 10000:.1f} 万" if tokens >= 10000 else str(tokens)


def report() -> str:
    segment = _segment.load()
    size = _size(segment) if segment["messages"] else 0
    settled = segment.get("settled")
    if isinstance(settled, int):
        grown = max(0, size - settled)
        left = max(0, _GROWTH_TOKENS - grown)
        gap = f"离下次提醒还差约 {_amount(left)} token"
    else:
        gap = "还没到过门槛"
    nudged = segment.get("nudged_at")
    lines = [f"subconscious: {'on' if on() else 'off'}",
             f"这一段约 {_amount(size)} token；{gap}（门槛 {_amount(_GROWTH_TOKENS)}）",
             "上次提醒：" + (_screen.clock(nudged) if isinstance(nudged, (int, float)) else "还没提醒过")]
    return "\n".join(lines)
