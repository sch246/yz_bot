"""Her subconscious: one more request over her own context that folds wakes into a summary.

WHY: 维护者 2026-10-25：潜意识改成分叉（同一份请求末尾加指令，看完就散）；放下交还给 dismiss；
门槛 5 万。看到的是原样，少一套状态，整段走缓存——另起一条会话要把经过转写过去：截断、丢思考，
前缀和她那条对不上，还得记着读到哪、上次的决定做没做成，自己那条满了再写地图换新的。

WHY: 收起的位置以后全要按原价重读（缓存只认前面一样的部分），所以尽量收到靠后，只留最近一次
唤醒不动；收中间一小块、后面剩一大截，下一次请求就按原价读那一大截。

WHY: 怎么判断写在 data/notes/policy/subconscious.md（她也能改），盖掉 DEFAULT_GUIDANCE；代码只管
门槛、格式和机制：这一段第一次唤醒（连着交接留下的笔记）不收起，最近一次唤醒不列出来。
"""

from __future__ import annotations

import re

from mods import storage

import mods.chat as _chat_root
from . import notes as _notes, roles as _roles, segment as _segment

# 这一段比上次整理时又长这么多 token 才整理一次（维护者 2026-10-25：门槛 5 万）。
_GROWTH_TOKENS = 50_000
# 分叉请求的工具选择：先试 none（不调工具）。要是第一次分叉的缓存命中因此掉了，改回 "auto"；
# 那种回复里的工具调用也不执行，当作留着。
_TOOL_CHOICE = "none"

DEFAULT_GUIDANCE = """现在先停一下，整理你眼前这一段上下文。这次不对外说话、不调用工具，写完决定就结束；这一下你之后不会记得，只会看到收起后的摘要。

收的是已经了结、不必每个字都在眼前的唤醒。收起以后原文进归档，眼前只剩摘要，所以摘要要写到不看原文也接得上：
- 什么事、和谁、到哪一步、结果怎样；
- 要引别人的原话就带 mid，没有 mid 就写"大意"；
- 答应了谁、还没做的，单独一行"还欠着：……"；
- 当时的心情有就留一句；
- 查过、试过的只留结论，比如"查了 X，是 Y"；走过的弯路只留一句教训；
- 看了一眼、没你的事的画面，一句"看了几眼某某群，没我的事"就够。
写的是过去，不是现在要做的事。

还在进行中的不收：在等谁回话、刚有人点你的名、工具报错还没弄清。
从上次收起的地方接着往后收，收到能收的最后一次；别只收中间一小块，收的位置后面剩得越多，你下一次请求越贵。
没什么可收就写"留着"。"""

# 列表里那个"（之后约 N token）"它可能照抄过来，先摘掉再解析。
_AMOUNT = re.compile(r"[（(]\s*之后约[^）)]*[）)]")
_TIME = r"(?:\d{4}-\d{2}-\d{2}\s+)?\d{1,2}:\d{2}:\d{2}"
_HEAD = re.compile(rf"^收起\s*({_TIME})(?:\s*(?:~|～|到|至)\s*({_TIME}))?\s*[:：]?\s*(.*)$")


def on() -> bool:
    # 维护者 2026-10-24：默认开。
    return bool(storage.get("", "agent").get("subconscious", True))


def settle(model: str | None, turn) -> None:
    """One fork over her context; never raises. A failure is a warning, never a retry."""
    try:
        _settle(model, turn)
    except Exception as error:
        _roles._stream.warning(f"潜意识出错，这次没整理：{type(error).__name__}: {error}")
        _reset()


def _settle(model: str | None, turn) -> None:
    her = _segment.load()
    if not her["messages"]:
        # 交接后、新的一段存下之前读到的是空段：等她那条有东西再看。
        return
    size = _size(her)
    settled = her.get("settled")
    if not isinstance(settled, int) or size < settled:
        # 这一段第一次见到（或者她自己 dismiss 清短了）：记下现在的大小，这次不跑。
        _remember(her)
        return
    if size - settled < _GROWTH_TOKENS or turn.cancelled:
        return
    if not _offered(her):
        # 只有第一次唤醒和最近一次：没有能收的。
        return
    reply, hit = _fork(model, turn, her)
    if turn.cancelled:
        _roles._stream.warning("潜意识被打断，这次没整理")
        _remember(her)
        return
    applied = _apply(her, _decisions(reply or ""))
    after = _size(her)
    _remember(her)
    if applied:
        _roles._stream.info(f"潜意识收起 {applied} 段，约少了 {_amount(size - after)} token"
                            + (f"（分叉命中缓存 {_amount(hit)}）" if hit else ""))
    else:
        _roles._stream.info(f"潜意识留着（这一段约 {_amount(after)} token）"
                            + (f"，分叉命中缓存 {_amount(hit)}" if hit else ""))


def _size(her: dict) -> int:
    """How long her segment is, the same measure the handoff threshold uses."""
    return _chat_root._agent._context_size(her.get("tokens"), her["messages"])


def _remember(her: dict) -> None:
    her["settled"] = _size(her)
    _segment.save(her)


def _reset() -> None:
    """After a failure: move the baseline up so the next wake does not re-send the whole segment."""
    try:
        her = _segment.load()
        if her["messages"]:
            _remember(her)
    except Exception:
        _roles._stream.exception("潜意识没能重设门槛基准")


def _offered(her: dict) -> list[dict]:
    """The wakes it may start a fold at: not the first (it carries the handoff notes), not the latest."""
    marks = [mark for mark in her.get("marks") or [] if mark.get("index", 0) > 0]
    return marks[:-1]


def _fork(model: str | None, turn, her: dict) -> tuple[str | None, int]:
    """Her whole context plus one instruction at the end; one request, nothing executed or stored."""
    messages = [*her["messages"], {"role": "system", "content": _instruction(her)}]
    agent = _chat_root._agent
    session, binding = agent._open_session(model, turn, her, messages, quiet=True, fail_fast=True)
    binding.sync_registry()
    if her.get("told") is None:
        # 新的一段：完整工具状态放在系统提示之后，和她平常的请求一样。
        state = binding.state_text(None)
        if state:
            session.messages.insert(session.prompt_count, {"role": "system", "content": state})
    make_tools = session.get_tools
    session.get_tools = lambda: [agent._inert(tool) for tool in make_tools()]
    # 请求日志只记这次追加的指令和回复：前面那一段上一次已经记过。
    session.logged = session.prompt_count + len(her["messages"])
    session.should_stop = lambda: turn.cancelled
    # 万一供应商还是回了工具调用：工具不执行（_inert），这一批过后直接收工，不再发第二次请求。
    session.turn_done = lambda: True
    hit: list[int] = []
    charge = _roles._charge(session)

    def handle(chunk):
        if chunk.cached_tokens:
            hit.append(chunk.cached_tokens)
        charge(chunk)

    session.chat(recall_func=handle, tool_choice=_TOOL_CHOICE,
                 description_cache=_chat_root.description_cache)
    replies = session.messages[agent._system_count(session) + len(messages):]
    if any(message.get("tool_calls") for message in replies):
        return None, max(hit, default=0)
    text = "\n".join(str(message.get("content") or "") for message in replies
                     if message.get("role") == "assistant").strip()
    return (text or None), max(hit, default=0)


def _instruction(her: dict) -> str:
    counts = _chat_root._agent._text_tokens
    offered = "\n".join(
        f"{mark['at'][11:]}（之后约 {_amount(counts(her['messages'][mark['index']:]))} token）"
        for mark in _offered(her)) or "（没有）"
    guidance = _notes.read_policy("subconscious").strip() or DEFAULT_GUIDANCE
    return f"""{guidance}

## 能收的唤醒
只能从下面这些时刻起收；最近一次唤醒和这一段第一次唤醒都不在里面，写了也不生效。括号里是收起以后，你那条从那里往后要按原价重读多少 token。
{offered}

## 决定格式
一段一行，摘要写在它下面（可以几行），一次可以写好几段：
收起 10:02:11~10:40:05
……这一段的摘要，几行……
收起 11:05:00~11:52:30
……
「~」后面那次唤醒不收，从它开始原样留着；收到最后就只写开始时刻：收起 10:02:11
"""


def _amount(tokens: int) -> str:
    return f"{tokens / 10000:.1f} 万" if tokens >= 10000 else str(tokens)


def _decisions(reply: str) -> list[dict]:
    """The fold decisions in *reply*, one per ``收起`` block; anything else (留着/放下) means none."""
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
            current = None       # 放下不归它管；留着就是没有决定。
        elif current is not None:
            current["lines"].append(raw.strip())
    return [{"at": item["at"], "until": item["until"], "label": "\n".join(item["lines"]).strip()}
            for item in found if "\n".join(item["lines"]).strip()]


def _find(marks: list[dict], at: str) -> dict | None:
    found = [mark for mark in marks if at in (mark["at"], mark["at"][11:])]
    return found[-1] if found else None


def _overlaps(resolved: list[tuple[dict, dict | None, str]]) -> bool:
    ordered = sorted(resolved, key=lambda item: item[0]["time"])
    return any(after is None or after["time"] > next_mark["time"]
               for (_mark, after, _label), (next_mark, _next, _next_label) in zip(ordered, ordered[1:]))


def _apply(her: dict, decisions: list[dict]) -> int:
    """Fold the decided ranges, back to front; overlapping ranges mean nothing is done at all."""
    marks = her.get("marks") or []
    latest = max((mark["time"] for mark in marks), default=None)
    resolved = []
    for item in decisions:
        mark = _find(marks, item["at"])
        if mark is None or mark.get("index", 0) == 0 or mark["time"] == latest:
            continue
        after = _find(marks, item["until"]) if item["until"] else None
        if item["until"] and (after is None or after["time"] <= mark["time"]):
            continue
        resolved.append((mark, after, item["label"]))
    if _overlaps(resolved):
        _roles._stream.warning("潜意识这次的收起区间有重叠，整次没做")
        return 0
    applied = 0
    # 从后往前：_fold_between 会把后面记号的下标往前挪，先收后面的，前面的下标就不受影响。
    # 记号在收之前定下来（含后边界），相邻两块共用同一个边界时，后一块折出来的那行正好落在
    # 前一块的结束下标上。
    for mark, after, label in sorted(resolved, key=lambda item: item[0]["time"], reverse=True):
        if after is None:
            _chat_root._agent._fold_since(her, mark, label)
        else:
            _chat_root._agent._fold_between(her, mark, after, label)
        applied += 1
    return applied


def report() -> str:
    her = _segment.load()
    size = _size(her) if her["messages"] else 0
    settled = her.get("settled")
    lines = [f"subconscious: {'on' if on() else 'off'}",
             f"这一段约 {_amount(size)} token，门槛 {_amount(_GROWTH_TOKENS)}"
             + (f"；上次整理时约 {_amount(settled)}，之后又长了 {_amount(size - settled)}"
                if isinstance(settled, int) else "；这一段还没整理过")]
    lines.append("怎么判断：data/notes/policy/subconscious.md" + ("" if _notes.read_policy("subconscious").strip()
                                                                else "（还没写，用的是默认）"))
    return "\n".join(lines)
