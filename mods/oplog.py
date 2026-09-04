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
因为它看起来就是当时真实发生的事。

WHY: 这个模块**没有自己的截断规则**——没有保留期，没有条数上限，没有字数上限。操作记录
依附于聊天消息的截断：聊天窗口留多久，操作就留多久，滚出窗口的由 chat.build_context 调
forget_before 清掉（见那里）。曾经这里有过一个独立的 7 天保留期，拆了：两套截断规则要各自
调参、各自解释，而它们描述的其实是同一件事——"多早以前的事情还算数"。答案只该有一个。

WHY: 条目只有两种消失方式——滚出聊天窗口，或被 clear。收缩不删除，只标记（见 condense），
标记过的离开上下文但仍可 recall_ops 取回。所以"这一轮上下文里有什么"和"存储里还有什么"
是两个不同的集合，读这个模块时别把它们当成一回事。

WHY: cid 用跨轮稳定的 uid，不用每轮重排的序号。原因不是"稳定一点更好"，而是序号在这里会
自我否定：结论住在模型那条 condense_ops 调用的 arguments 里（见 meta.condense_ops），
而那条调用会被重建进以后每一轮的上下文。若编号每轮重排，模型下一轮读到自己写的
["op3","op4"] 时，这两个号已经指向别的调用了，而它无从察觉。唯一的补救是每轮改写模型自己
写下的文字，那更糟。能重算的（tool result 开头的 [opN] 前缀）才可以是视图。
cid 只需窗口内唯一——收缩和重建都在单个窗口里，不存在跨窗口引用。
"""

from __future__ import annotations

from collections.abc import Iterable
import time
from threading import RLock
from typing import Any

from mods import INFRA


PHASE = INFRA
LOAD_AFTER = ("storage",)

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


def forget_before(window: tuple[str, Any] | None, at: float) -> int:
    """Drop rounds that started before ``at``; return how many entries went.

    WHY: 这是唯一的存储回收口，而且门槛由调用方给——chat 那边传的是聊天窗口能回溯到的
    最早时刻。比那更早的操作再也不可能被载入，留着只是磁盘上的死重量。回收规则不写在这
    个模块里，是因为"多早以前的事情还算数"是聊天截断的问题，不是操作记录自己的问题。

    WHY: 按**整轮**丢，不按条。丢掉一轮里的一部分，重建出来就是一条 assistant 的
    tool_calls 少了对应的 tool 消息，供应商直接拒——回收不该把上下文变成非法的。
    """
    with _lock:
        state = _state(window)
        if state is None:
            return 0
        listed = state["entries"]
        stale = {entry["round"] for entry in listed if float(entry.get("at") or 0.0) < at}
        if not stale:
            return 0
        kept = [entry for entry in listed if entry["round"] not in stale]
        state["entries"] = kept
        return len(listed) - len(kept)


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
            # WHY: cid 存在条目里，它是 recall/condense 的查找键。渲染时贴到 tool 结果开头的
            # "[opN] " 只是这个字段的一个视图——别反过来理解成"cid 是算出来的"。前缀不烤进
            # content 是因为 content 要保持工具当时真正输出的原文：recall_ops 取回的就是它，
            # 烤进去等于让原文里多一句工具从没说过的话。计数器 next 同样要存，否则重启后
            # 归零、cid 会指到别的调用上。
            "cid": cid,
            # 同一条 assistant 消息里并发的调用共用一个 round，重建时据此还原分组。
            "round": str(round_id or tool_call_id),
            # 重建时用来和聊天消息按时间归并，见 build_rounds。
            "at": time.time(),
            "name": str(name),
            "arguments": "" if arguments is None else str(arguments),
            "content": "" if content is None else str(content),
            "tool_call_id": str(tool_call_id),
        })
        return cid


def entries(window: tuple[str, Any] | None, include_condensed: bool = False) -> list[dict]:
    """Everything still on record for this window.

    WHY: 收缩过的条目默认不出现，但它们**还在**。收缩只让一次调用离开上下文，不参与重建；
    能不能取回是"有没有滚出聊天窗口"说了算。分开之后收缩才是安全的动作——模型收缩时不是
    在销毁证据，结论被推翻或需要核对当初到底看到了什么时，recall_ops 仍然能把原文捞回来。
    """
    with _lock:
        state = _state(window)
        if not state:
            return []
        return [
            entry for entry in state["entries"]
            if include_condensed or not entry.get("condensed")
        ]


def recall(window: tuple[str, Any] | None, cids: Iterable[str]) -> tuple[list[dict], list[str]]:
    """Look up stored calls by cid, whatever the context budget left out.

    WHY: 载入范围比存下来的窄（收缩过的、以及超出 token 预算的都不载入），所以"存着但这
    一轮没载入"是常态而不是异常。没有这个入口，那些记录就等于存了拿不到。

    WHY: 收缩过的也能取回，而且这才是主要用途。上下文里不会有一行清单去介绍那些没载入的
    调用（7 天的量列出来本身就是浪费），模型唯一能看见的 cid 来源是它自己那条
    condense_ops 调用的 arguments——那里面写的正是被收缩掉的 cid。所以这个入口如果够不着
    收缩过的条目，它实际上就没有可用的入参。
    """
    wanted = {str(value) for value in cids}
    found = [entry for entry in entries(window, include_condensed=True) if entry["cid"] in wanted]
    return found, sorted(wanted - {entry["cid"] for entry in found})


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
    """Mark the named calls condensed: out of the context, still on record.

    WHY: 标记而不删除。删除会让"收缩"变成不可逆的销毁，模型每次收缩都得先赌自己的结论没
    写错；标记之后收缩只是移出上下文，原文按保留期继续留着，recall_ops 随时能捞回来。
    唯一真正的删除是过期（见 _prune）。

    WHY: 不留结论条目。结论已经在模型那条 condense_ops 调用的 arguments 里，
    而那条调用本身也是一次被记录的工具调用，会跟着重建回来。再存一份就是第二个副本。

    WHY: 按整轮，理由与 _prune 相同：半轮会重建出配对不上的 tool_calls。

    WHY: 后来的收缩可以把更早那次 condense_ops 调用本身也收掉，于是旧结论从上下文里消失。
    这是刻意的——更高层的结论本就该取代下层的。别把它当成 bug 去"保护"结论条目。
    """
    wanted = {str(value) for value in cids}
    with _lock:
        state = _state(window)
        if state is None:
            return 0
        live = [entry for entry in state["entries"] if not entry.get("condensed")]
        rounds = {entry["round"] for entry in live if entry["cid"] in wanted}
        partial = [entry["cid"] for entry in live if entry["round"] in rounds and entry["cid"] not in wanted]
        if partial:
            raise ValueError("同一轮里的调用必须一起收缩，还差: " + ", ".join(sorted(partial)))
        marked = 0
        for entry in state["entries"]:
            if entry["round"] in rounds and not entry.get("condensed"):
                entry["condensed"] = True
                marked += 1
        return marked


def clear(window: tuple[str, Any] | None) -> None:
    """Drop this window's track. The cid counter deliberately does not rewind.

    WHY: 清空只删条目，不把计数器归零。归零会让 cid 被重复使用，而旧的 cid 可能还印在
    某轮上下文的 tool result 里；下一次 condense_ops 就会指到一条完全不相干的操作上。
    """
    with _lock:
        state = _state(window)
        if state is not None:
            state["entries"] = []


def build_rounds(window: tuple[str, Any] | None) -> list[tuple[float, list[dict]]]:
    """Rebuild the track as timestamped, atomic tool-call rounds.

    WHY: 归并的单位是**一轮**，不是一条消息。assistant(tool_calls) 和它的 tool 结果之间
    插进一条聊天消息就拆散了这一对，请求会被拒。所以每一轮带一个时间、整体落位。

    WHY: 时间取这一轮里第一条的完成时刻。一轮里几个并发调用各自完成时间不同，而且工具
    执行期间到达的聊天消息，其真实先后没法从这一个时间点还原——重建出的顺序因此不保证
    与当时完全一致。这是明知的近似：拿回"发生过什么、大致在什么时候"，不追求逐条复原。
    """
    listed = entries(window)
    if not listed:
        return []
    order: list[str] = []
    grouped: dict[str, list[dict]] = {}
    for entry in listed:
        round_id = entry["round"]
        if round_id not in grouped:
            grouped[round_id] = []
            order.append(round_id)
        grouped[round_id].append(entry)
    rounds: list[tuple[float, list[dict]]] = []
    for round_id in order:
        batch = grouped[round_id]
        messages: list[dict] = [{
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
        }]
        messages.extend(
            {
                "role": "tool",
                "tool_call_id": entry["tool_call_id"] or entry["cid"],
                "content": f"[{entry['cid']}] {entry['content']}",
            }
            for entry in batch
        )
        rounds.append((float(batch[0].get("at") or 0.0), messages))
    return rounds


def build_messages(window: tuple[str, Any] | None) -> list[dict]:
    """Rebuild the track as the real tool-call records it came from.

    WHY: assistant 消息的 content 一律留空。模型当时说的可见正文已经作为 QQ 消息发出去、
    进了 chatlog（见 message.py 的 _chatlog_write），下一轮 get_msgs 会重建它。这里再带一份
    就是同一句话在上下文里出现两遍。所以这条轨道只负责它独有的部分：调用与结果。

    WHY: 也不带 reasoning_content——轨道里本来就没有，而"assistant 不带 reasoning、
    请求停在 tool 上"这个形状实跑验证过被接受（deepseek-v4-flash, 2026-09-04）。同一次
    验证还覆盖了这些记录后面紧跟聊天消息（tool -> user）的形状。
    """
    return [message for _at, messages in build_rounds(window) for message in messages]


def render(window: tuple[str, Any] | None) -> str | None:
    """Render the track for a human (``#ops``); truncated, unlike the model's copy.

    WHY: 人这一份要连收缩过的一起显示（打上标记）。模型那边看不到它们是刻意的，人这边看
    不到就只剩困惑：条目会毫无征兆地从 #ops 里消失，而磁盘上其实还在。
    """
    listed = entries(window, include_condensed=True)
    if not listed:
        return None
    def short(value: str) -> str:
        text = " ".join(str(value).split())
        return text if len(text) <= DISPLAY_CHARS else text[:DISPLAY_CHARS] + f"…({len(text)}字)"
    return "\n".join([
        "本窗口的操作历史:",
        *(f"- [{entry['cid']}]{'(已收缩)' if entry.get('condensed') else ''} "
          f"{entry['name']}({short(entry['arguments'])}) -> {short(entry['content'])}"
          for entry in listed),
    ])
