"""Her subconscious: a stateless fork of her own context that folds wakes into one line.

WHY: 维护者 2026-10-25（billion-context 式）：不再另起一条会话、把经过转写过去——那要截断
（整段 3 万字、工具结果 1000 字），前缀和她那条对不上，它还得自己记着读到哪、上次的决定做没
做成，满 10 万写下地图换新的一条。现在整理时把她那一整段上下文原样再发一次请求
（`agent._run_session`，persist=False，系统提示、工具定义、消息都和她那条一样），只在末尾补
一条整理指令；请求和回复都不存。它要是调工具，不执行，只认文字里的决定。

WHY: 按增长叫，不是每次唤醒都叫：这一段比上次整理时又长 `_GROWTH_TOKENS` 才叫一次。基线记在
这一段自己身上（键 `subconscious`），换段自然从头算；她自己 dismiss 清短了也把基线放下。

WHY: 只收不放。放下是退回没读，那是她自己 `dismiss` 的事；分叉只管把看过的唤醒收成一句。
怎么判断写在 data/notes/policy/subconscious.md（她也能改），拼进指令；文件不存在时用
DEFAULT_GUIDANCE。代码只管门槛、格式和机制：这一段第一次唤醒连着开头的笔记不收起。
"""

from __future__ import annotations

import re
import time

from mods import storage

import mods.chat as _chat_root
from . import notes as _notes, roles as _roles, screen as _screen, segment as _segment

# 这一段比上次整理时又长这么多 token 才整理一次（维护者 2026-10-25：几万 token，别每次唤醒都叫）。
_GROWTH_TOKENS = 30_000

DEFAULT_GUIDANCE = """你是她的潜意识：同一个脑子的另一条思路。上面这一段就是她眼前的上下文，原样发给你，不是新消息。你不说话、不行动，也不回答里面任何一个人；只整理她的注意力，她看不到你，只看到你收起来的那一句。

被叫醒、看一眼、没她的事，这样的痕迹叠多了，她会照着这一路往下演，越来越累；该记着的被收掉，她又会接不上话。你的事是让她眼前只留值得留的。

- 看过、想过，但不必每个字都在眼前的，就收起。摘要写清楚是什么事、到哪一步、她还要不要管，让她看这一句就够用。
- 只是一次次被叫醒、看一眼又没她的事的零碎唤醒（她自己忘了 dismiss），就合起来收成一句「看了几眼群，没我的事」。
- 几轮都是同一件事，等它告一段落再一起收；最近几次唤醒、她正在做的那件事留着别动。
- 她说过的话、答应过什么、在等谁的回复，工具报了错或者结果和预期不符，有人点了她的名：这些留着，她得记得。
- 收中间那几段以后，她那条从那里往后下一次要按原价重读（前缀缓存只认前面一样的部分），所以攒够了、值得时再收。
- 拿不准就留着。"""

# 列表里那个"（之后约 N token）"它可能照抄过来，先摘掉再解析。
_AMOUNT = re.compile(r"[（(]\s*之后约[^）)]*[）)]")
_TIME = r"(?:\d{4}-\d{2}-\d{2}\s+)?\d{1,2}:\d{2}:\d{2}"
_HEAD = re.compile(rf"^收起\s*({_TIME})(?:\s*(?:~|～|到|至)\s*({_TIME}))?\s*[:：]?\s*(.*)$")


def on() -> bool:
    # 维护者 2026-10-24：默认开。
    return bool(storage.get("", "agent").get("subconscious", True))


def settle(model: str | None, turn) -> None:
    """Resend her segment and fold what the fork decides; never raises."""
    try:
        _settle(model, turn)
    except Exception:
        _roles._stream.exception("潜意识出错，这次没整理")


def _settle(model: str | None, turn) -> None:
    her = _segment.load()
    if not her["messages"]:
        # 交接后、新的一段存下之前读到的是空段：等她那条有东西再看。
        return
    if not any(mark.get("index", 0) > 0 for mark in her.get("marks") or []):
        # 只有这一段第一次唤醒：连着开头的笔记，没有能收的。
        return
    size = _chat_root._agent._text_tokens(her["messages"])
    tidied = her.get("subconscious")
    last = tidied.get("tokens") if isinstance(tidied, dict) else None
    if not isinstance(last, int) or size < last:
        # 第一次见到这一段，或者她自己 dismiss 清短了：记下现在的长度，等它再长。
        _remember(her, size)
        return
    if size - last < _GROWTH_TOKENS or turn.cancelled:
        return
    reply = _fork(model, turn, her)
    if reply is None:
        return
    applied = _apply(her, _decisions(reply))
    _remember(her, _chat_root._agent._text_tokens(her["messages"]))
    if applied:
        _roles._stream.info(f"潜意识收起了 {applied} 段")


def _remember(her: dict, tokens: int) -> None:
    her["subconscious"] = {"tokens": tokens, "time": time.time()}
    _segment.save(her)


def _fork(model: str | None, turn, her: dict) -> str | None:
    """Her whole context plus one instruction at the end; nothing is stored or executed."""
    messages = [*her["messages"], {"role": "system", "content": _instruction(her)}]
    agent = _chat_root._agent
    session = agent._run_session(model, turn, her, messages, lambda: None,
                                 lambda used, initial: False,
                                 quiet=True, persist=False, fail_fast=True,
                                 known=len(her["messages"]), record=False, execute_tools=False, echo=False)
    if turn.cancelled:
        return None
    replies = session.messages[agent._system_count(session) + len(messages):]
    text = "\n".join(str(message.get("content") or "") for message in replies
                     if message.get("role") == "assistant").strip()
    return text or None


def _instruction(her: dict) -> str:
    size = _chat_root._agent._text_tokens
    offered = "\n".join(
        f"{mark['at'][11:]}（之后约 {_amount(size(her['messages'][mark['index']:]))} token）"
        for mark in her.get("marks") or [] if mark.get("index", 0) > 0) or "（没有）"
    guidance = _notes.read_policy("subconscious").strip() or DEFAULT_GUIDANCE
    return f"""这次只是整理：不要回答上面任何人，也不要调用工具（调了也不会执行）。

{guidance}

## 能下手的唤醒
这一段第一次唤醒连着开头交接留下的说明，不能收；其它都可以，时刻只写时分秒。括号里是收起这一块以后，她那条从那里往后要按原价重读多少 token。
{offered}

## 怎么写
一次可以收好几段。一段一行决定，摘要写在它下面（可以写几行）：
收起 07:12:03~07:40:25
<摘要>
收起 08:02:10~08:30:00
<摘要>
收到最后就只写开始时刻：收起 07:12:03
「~」后面那次唤醒不收，从它开始原样留着。

摘要照聊天记录那样写：谁说了什么，带上那条消息的 mid（记录行里「|」后面那串消息号），没有 mid 的写大意；做了什么、结果如何；到哪一步；最后单独一行「还欠着」，写还在等谁、答应过什么，没有就写「还欠着：没有」。

只有收起，没有放下——想退回没读是她自己 dismiss 的事。没什么可收、拿不准，就写一行：留着"""


def _amount(tokens: int) -> str:
    return f"{tokens / 10000:.1f} 万" if tokens >= 10000 else str(tokens)


def _decisions(reply: str) -> list[dict]:
    """The decisions in *reply*: one ``{"at", "until", "label"}`` per ``收起`` block, in written order."""
    found: list[dict] = []
    current: dict | None = None
    for raw in reply.splitlines():
        line = _AMOUNT.sub("", raw.strip()).strip().strip("*`>#-· ").strip()
        line = re.sub(r"^(?:决定|decision)\s*[:：]\s*", "", line, flags=re.I)
        if not line:
            continue
        if line.startswith("收起"):
            head = _HEAD.match(line)
            if head is None:
                current = None   # 认不出的决定不猜，跳过这一块。
                continue
            current = {"at": " ".join(head.group(1).split()),
                       "until": " ".join((head.group(2) or "").split()),
                       "lines": [head.group(3).strip()] if head.group(3).strip() else []}
            found.append(current)
        elif line.startswith(("留着", "放下")):
            current = None
        elif current is not None:
            current["lines"].append(raw.strip())
    return [{"at": item["at"], "until": item["until"], "label": "\n".join(item["lines"]).strip()}
            for item in found if "\n".join(item["lines"]).strip()]


def _find(marks: list[dict], at: str) -> dict | None:
    found = [mark for mark in marks if at in (mark["at"], mark["at"][11:])]
    return found[-1] if found else None


def _apply(her: dict, decisions: list[dict]) -> int:
    """Carry out the foldable decisions; returns how many took effect."""
    resolved = []
    for item in decisions:
        mark = _find(her.get("marks") or [], item["at"])
        if mark is None:
            continue
        after = _find(her.get("marks") or [], item["until"]) if item["until"] else None
        if item["until"] and (after is None or after["time"] <= mark["time"]):
            continue
        resolved.append((mark["time"], after["time"] if after is not None else None, item["label"]))
    applied = 0
    # 从早到晚收：先收的会把后面记号的下标往前挪，每次重新按时刻找，不拿着旧下标。
    for start, end, label in sorted(resolved, key=lambda item: item[0]):
        marks = her.get("marks") or []
        mark = next((other for other in marks if other["time"] == start), None)
        if mark is None or mark.get("index", 0) == 0:
            continue
        if end is None:
            _chat_root._agent._fold_since(her, mark, label)
        else:
            after = next((other for other in marks if other["time"] == end), None)
            if after is None:
                continue   # 前面的决定已经把它收掉了。
            _chat_root._agent._fold_between(her, mark, after, label)
        applied += 1
    return applied


def report() -> str:
    her = _segment.load()
    size = _chat_root._agent._text_tokens(her["messages"]) if her["messages"] else 0
    tidied = her.get("subconscious") if isinstance(her.get("subconscious"), dict) else {}
    lines = [f"subconscious: {'on' if on() else 'off'}",
             f"这一段 {_screen.clock(her['started'])} 开始，约 {_amount(size)} token；"
             f"比上次整理再长 {_GROWTH_TOKENS} token 才整理（subconscious._GROWTH_TOKENS）"]
    if tidied.get("time"):
        lines.append(f"上次整理 {_screen.clock(tidied['time'])}，之后又长了 {size - tidied.get('tokens', 0)} token")
    else:
        lines.append("这一段还没整理过")
    lines.append("怎么判断：data/notes/policy/subconscious.md" + ("" if _notes.read_policy("subconscious").strip()
                                                                else "（还没写，用的是默认）"))
    return "\n".join(lines)
