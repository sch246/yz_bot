"""Her subconscious: a context of its own that tidies hers once she is done.

WHY: 维护者 2026-10-24（提案 attention-and-subconscious）：显意识留痕，潜意识不留痕；用一个
视角来管理上下文本身，是提高上限的方式。它有自己的一条上下文，只往后接：她忙完一阵，这阵的
经过接到它末尾，由它决定她那条的末尾怎么办——收成一句记号，或者放下（同 dismiss，回到没读），
不动就是留着。它的理由只在它这条里，她只看到结果。它记得自己做过什么，所以可以等几轮再从
第一轮起一起收：只动她那条的末尾，她的前缀照样命中。这条长了它自己交接：写下地图，换新的一条。

WHY: 怎么判断写在 data/notes/policy/subconscious.md，不写成代码里的规则——维护者说固定规则
改起来不方便；文件不存在时用 DEFAULT_GUIDANCE。代码只管格式和机制本身：只认最后一行决定、
只动她那条的末尾、记号跟着她那一段（不跨交接）、这一段第一次唤醒连着开头的笔记不收起、
follow 期间攒过 _FRESH_MAX 条的不放下（和她自己 dismiss 的兜底是同一条，维护者定的）。
"""

from __future__ import annotations

import json
import os
import re
import time

from mods import llm, storage

import mods.chat as _chat_root
from . import notes as _notes, roles as _roles, screen as _screen, segment as _segment

_FILE = _segment.ROOT / "subconscious.json"
# 它这条每次整段读一遍，长了每次都贵：过了这么多 token 就写下地图，换新的一条。
_LIMIT = 100_000
# 接给它的一阵经过最多这么多字，从后往前留；其中一次工具结果最多这么多字。
_ROUND_CHARS = 30_000
_RESULT_CHARS = 1_000
# 列给它的、还能从那儿起收起或放下的唤醒，最近这么多个。
_OFFERED = 10

DEFAULT_GUIDANCE = """你是她的潜意识：同一个脑子的另一条思路。你不说话、不行动，只整理她眼前的上下文。她看不到你，只看到你整理的结果。

她眼前的上下文就是她的注意力。被叫醒、看一眼、没她的事，这样的痕迹叠多了，她会照着这一路往下演，越来越累；该记着的被收掉，她又会接不上话。你的事是让她眼前只留值得留的。

- 没什么要她记着的（看了一眼、没她的事、也没想出什么），就放下：消息回到没读，下次和新的一起给她。
- 看过、想过，但不必每个字都在眼前的，就收起。记号写清楚是什么事、到哪一步、她还要不要管，让她看记号就够用。
- 几轮都是同一件事，可以先留着，等这件事告一段落再从第一轮起一起收。
- 她在那以后说过话、答应过什么、在等谁的回复，工具报了错或者结果和预期不符，有人点了她的名：这些留着，她得记得。
- 拿不准就留着。"""

_FORMAT = """每次最后单独写一行决定，只写一件：
收起 <时刻> <记号>　把她那条从这次唤醒起到最后换成一句记号，原样另存，她要看能读回来
放下 <时刻>　　　　 把她那条从这次唤醒起清掉，消息回到没读，下次和新的一起给她
留着　　　　　　　　什么都不动
时刻照"还能从这些唤醒起"后面列的写。决定之前可以先写几句理由，它们只留在你这条里。"""

_HANDOFF = ("这一条要换新的了。写下新一条开头要用的地图：她眼前现在有哪几摊事、各是什么、到哪一步；"
            "你收起过哪些（记号原文）；彼此什么关系；还要盯着什么。直接写正文，不写决定。")

_DECISION = re.compile(r"(收起|放下)\s*[（(「]?\s*((?:\d{4}-\d{2}-\d{2}\s+)?\d{1,2}:\d{2}:\d{2})\s*[）)」]?\s*[:：，,]?\s*(.*)")


def on() -> bool:
    return bool(storage.get("", "agent").get("subconscious"))


def _fresh(started: float | None = None) -> dict:
    return {"started": started or time.time(), "messages": [], "map": "", "her": {}, "tokens": 0, "result": ""}


def load() -> dict:
    try:
        return {**_fresh(), **json.loads(_FILE.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return _fresh()


def _save(state: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = _FILE.with_name("subconscious.json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, _FILE)


def _system(state: dict) -> list[dict]:
    parts = [_notes.read_policy("subconscious").strip() or DEFAULT_GUIDANCE, _FORMAT]
    who = _notes.read("self").strip()
    if who:
        parts.append("她对自己的认识（笔记 self）：\n" + who)
    messages = [{"role": "system", "content": "\n\n".join(parts)}]
    if state["map"]:
        messages.append({"role": "system", "content": "你上一条交接时写下的地图：\n" + state["map"]})
    return messages


def _shown(messages: list[dict]) -> str:
    """Her stretch as text, long tool results cut, the newest part kept when it is too long."""
    trimmed = []
    for message in messages:
        content = message.get("content")
        if message.get("role") == "tool" and isinstance(content, str) and len(content) > _RESULT_CHARS:
            message = {**message, "content": content[:_RESULT_CHARS] + f"…（共 {len(content)} 字）"}
        trimmed.append(message)
    text = _segment.render(trimmed)
    if len(text) > _ROUND_CHARS:
        text = f"（前面略了 {len(text) - _ROUND_CHARS} 字）\n" + text[-_ROUND_CHARS:]
    return text


def _decision(reply: str) -> tuple[str, str, str] | None:
    """The last decision line: ``(kind, at, label)``, ``("?", line, "")`` when it cannot be read, None for 留着."""
    for line in reversed(reply.strip().splitlines()):
        stripped = line.strip().strip("*`>#-· ").strip()
        if stripped.startswith("留着"):
            return None
        if stripped.startswith(("收起", "放下")):
            found = _DECISION.match(stripped)
            if not found:
                return "?", stripped, ""
            return found.group(1), " ".join(found.group(2).split()), found.group(3).strip().strip("*` ")
    return None


def _apply(her: dict, decision: tuple[str, str, str] | None) -> str:
    """Carry out one decision on her segment; returns what to tell it next time."""
    agent = _chat_root._agent
    if decision is None:
        return ""
    kind, at, label = decision
    if kind == "?":
        return f"上次最后那行「{at}」没看懂，什么都没动。"
    found = [mark for mark in her.get("marks") or [] if at in (mark["at"], mark["at"][11:])]
    if not found:
        return f"上次说的「{kind} {at}」没做：她那条里找不到这次唤醒（可能已经被清掉，或者她交接了）。"
    mark = found[-1]
    if kind == "放下":
        for window in _chat_root._windows.parse_all(agent._shown_since(her, mark)):
            piled = agent._piled(window, mark)
            if piled > agent._FRESH_MAX:
                return (f"上次说的「放下 {at}」没做：{_screen.label(window)} follow 期间已经攒了 {piled} 条，"
                        f"超过 {agent._FRESH_MAX} 条就不放下了，留给她看完。")
        agent._drop_since(her, mark)
        return f"上次说的「放下 {at}」做了：她那条从 {at} 起清掉，那些消息回到没读。"
    if not label:
        return f"上次说的「收起 {at}」没做：收起要写记号。"
    if mark["index"] == 0:
        return f"上次说的「收起 {at}」没做：那是这一段第一次唤醒，连着开头的笔记，不能收起（可以放下）。"
    name = agent._fold_since(her, mark, label)
    return f"上次说的「收起 {at}」做了：她那条从 {at} 起换成了你写的记号，原样存成归档段 {name}。"


def settle(model: str | None, turn) -> None:
    """Look at what she did since last time and maybe tidy the end of her segment; never raises."""
    try:
        _settle(model, turn)
    except Exception:
        _roles._stream.exception("潜意识出错，这次没整理")


def _settle(model: str | None, turn) -> None:
    state = load()
    her = _segment.load()
    if not her["messages"]:
        # 交接后、新的一段存下之前，每次读到的都是一份开始时刻不同的空段：等她那条有了东西再看。
        return
    pointer = state["her"]
    notes = [state["result"]] if state["result"] else []
    if pointer.get("started") != her["started"]:
        start = 0
        notes.append("她交接了，下面是她新的一段，从头开始。" if pointer else "下面是她眼前的这一段，从头开始。")
    else:
        start = pointer.get("index", 0)
        later = [mark["index"] for mark in her.get("marks") or [] if mark["time"] > pointer.get("time", 0)]
        if start > len(her["messages"]) or later and later[0] < start:
            # 她自己 dismiss 回了它看过的地方，之后可能又接了新的：条数不可信，从它上次之后的第一次唤醒接着看。
            start = later[0] if later else len(her["messages"])
            notes.append("她自己用 dismiss 清掉了一段你看过的。")
    new = her["messages"][start:]
    if not new:
        moved = _pointer(her)
        if (pointer.get("started"), pointer.get("index")) != (moved["started"], moved["index"]):
            state["her"] = moved
            state["result"] = "\n".join(notes)
            _save(state)
        return
    offered = "、".join(mark["at"] for mark in (her.get("marks") or [])[-_OFFERED:]) or "（没有）"
    added = []
    if notes:
        added.append({"role": "system", "content": "\n".join(notes)})
    added.append({"role": "user", "content": f"她又忙完一阵（{_screen.clock()}）。经过：\n{_shown(new)}"
                                             f"\n\n还能从这些唤醒起收起或放下：{offered}"})
    reply, used = _ask(model, turn, state, added)
    if reply is None:
        return
    state["messages"].extend([*added, {"role": "assistant", "content": reply}])
    state["tokens"] = used
    state["result"] = _apply(her, _decision(reply))
    state["her"] = _pointer(her)
    _save(state)
    if used > _LIMIT:
        _hand_off(model, turn, state)


def _pointer(her: dict) -> dict:
    """How far into her segment it has read, and when."""
    return {"started": her["started"], "index": len(her["messages"]), "time": time.time()}


def _ask(model: str | None, turn, state: dict, added: list[dict]) -> tuple[str | None, int]:
    """One request over its own context plus *added*; ``(None, 0)`` when it fails or says nothing."""
    session = llm.Chat(model=model or _chat_root.get_model(), chat_client=llm.get_client())
    session.fail_fast = True
    session.set_messages([*_system(state), *state["messages"], *added])
    # 请求日志只记这次新接上的：前面的上一次已经记过。
    session.logged = len(session.messages) - len(added) if state["messages"] else 0
    session.should_stop = lambda: turn.cancelled
    replies = session.chat(recall_func=_roles._charge(session))
    reply = "".join(chunk.content for chunk in replies
                    if chunk.role == "assistant" and isinstance(chunk.content, str)).strip()
    used = max((chunk.total_tokens for chunk in replies), default=0)
    return (reply, used) if reply and not turn.cancelled else (None, 0)


def _hand_off(model: str | None, turn, state: dict) -> None:
    """Write the map, archive this context, start a new one from the map."""
    written, _used = _ask(model, turn, state, [{"role": "system", "content": _HANDOFF}])
    if written is None:
        return
    name = _segment.archive({**state, "messages": [*state["messages"], {"role": "system", "content": _HANDOFF},
                                                   {"role": "assistant", "content": written}]}, "subconscious")
    _roles._stream.info(f"潜意识换了新的一条，旧的归档为 {name}")
    _save({**_fresh(), "map": written, "her": state["her"], "result": state["result"]})


def report() -> str:
    state = load()
    lines = [f"subconscious: {'on' if on() else 'off'}"]
    if state["messages"] or state["map"]:
        lines.append(f"它这一条 {_screen.clock(state['started'])} 开始，{len(state['messages'])} 条消息，"
                     f"上次请求 {state['tokens']} token（过 {_LIMIT} 就自己交接）")
    if state["result"]:
        lines.append("上次的决定：" + state["result"])
    lines.append("怎么判断：data/notes/policy/subconscious.md" + ("" if _notes.read_policy("subconscious").strip()
                                                                    else "（还没写，用的是默认）"))
    return "\n".join(lines)
