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

from mods import log, storage

import mods.chat as _chat_root
from . import notes as _notes, screen as _screen, windows as _windows

ROOT = Path("data") / "context"
_CURRENT = ROOT / "current.json"
_BACKUP = ROOT / "current.bak.json"
_ARCHIVE = ROOT / "archive"
# 每一段交接时往这里添一行：什么时候、多长、当时为什么结束。下一段开头挂最近几行。
_YEARS = Path("data") / "notes" / "years.md"
_lock = threading.Lock()
_stream = log.stream("agent")
# dismiss 能清回的记号：每次唤醒追加之前，这一段里会被这次唤醒改动的那几项；最多留最近这么多个。
MARKED = ("last_shown", "windows", "scopes", "told", "tokens", "notice")
MARKS = 40
# WHY: 维护者 2026-10-25 08:16 定的方向：不再自动跑一次、替她决定收哪段。每长 5 万 token 只插
# 一条提醒（这一段多长、能收哪几段、各多大）；收不收、收哪段、摘要怎么写都由她自己调 `compress`。
# 提醒和 hint 一样只挂在这一次请求末尾、不写进这一段（基准除外），下次收的时候自然不在；旧版那条
# 自动整理还带着自己的一条会话、自己的账本和一套截断，看的是转写不是原样，已经删了。
#
# WHY: 提醒里的时刻必须是 `_fold_since` / `_fold_between` 真能用的记号：这一段第一次唤醒连着交接
# 留下的笔记，不列；最近一次刚做完、下次多半还要接着做，也不列。`compress` 那头再校验一次。
# 这一段比上次提醒时又长这么多 token 才提醒一次（维护者 2026-10-25：5 万）。
GROWTH_TOKENS = 50_000
RECENT_STALE_SECONDS = 2 * 3600


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
            # 文件坏了：挪到一边留着排查；若有最近备份则从备份恢复，不让一次写坏丢掉整段上下文。
            broken = _CURRENT.with_name(f"current.broken-{time.strftime('%Y%m%d-%H%M%S')}.json")
            os.replace(_CURRENT, broken)
            if _BACKUP.is_file():
                try:
                    data = json.loads(_BACKUP.read_text(encoding="utf-8"))
                    _CURRENT.write_bytes(_BACKUP.read_bytes())
                except (OSError, ValueError):
                    return fresh()
            else:
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
        if _CURRENT.is_file():
            try:
                need = not _BACKUP.is_file() or (time.time() - _BACKUP.stat().st_mtime >= 300)
                if not need:
                    old = json.loads(_CURRENT.read_text(encoding="utf-8"))
                    need = len(segment.get("messages") or []) < len(old.get("messages") or [])
                if need:
                    _BACKUP.write_bytes(_CURRENT.read_bytes())
            except (OSError, ValueError):
                pass
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
        _append_year(segment)
        if kind == "" and _CURRENT.is_file():
            _CURRENT.unlink()
        return target.stem


def _year_line(segment: dict) -> str:
    """One line for ``years.md``: when, how long, and why that segment ended."""
    started, ended = segment.get("started", 0), segment.get("ended") or time.time()
    if time.strftime("%m-%d", time.localtime(started)) == time.strftime("%m-%d", time.localtime(ended)):
        span = f"{time.strftime('%m-%d %H:%M', time.localtime(started))}–{time.strftime('%H:%M', time.localtime(ended))}"
    else:
        span = f"{time.strftime('%m-%d %H:%M', time.localtime(started))}–{time.strftime('%m-%d %H:%M', time.localtime(ended))}"
    # 交接时她自己写的 reason 是最好的摘要；强制归档没有 reason，就只记窗口。
    reason = (segment.get("reason") or "").strip().split("\n")[0].strip()
    tail = reason[:80] if reason else "窗口 " + "、".join(segment.get("windows") or ["无"])
    return f"- {span}（{len(segment.get('messages') or [])} 条）{tail}"


def _append_year(segment: dict) -> None:
    try:
        _YEARS.parent.mkdir(parents=True, exist_ok=True)
        with _YEARS.open("a", encoding="utf-8") as handle:
            handle.write(_year_line(segment) + "\n")
    except OSError:
        # 年鉴只是索引，写不进去不能妨碍归档本身。
        pass


def each_archived(since: float = 0, newest_first: bool = False):
    """``(name, data)`` for archived segments that ended after *since*, one file at a time."""
    if not _ARCHIVE.is_dir():
        return
    # 按段名排：同一秒开始的段名后缀 -2、-3，按文件名排会排到不带后缀的那个前面。
    paths = sorted(_ARCHIVE.glob("*.json"), key=lambda path: path.stem, reverse=newest_first)
    for path in paths:
        # 归档文件写下后不再改，修改时间就是结束时刻：早于 since 的不用解析。
        if since and path.stat().st_mtime <= since:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("ended", 0) > since:
            yield path.stem, data


def archived(since: float = 0, last: int | None = None) -> list[dict]:
    """Archived segments that ended after *since*, oldest first: name, times, size, windows, reason.

    With *last*, only the newest that many are read.
    """
    found = []
    for name, data in each_archived(since, newest_first=last is not None):
        found.append({"name": name, "started": data.get("started", 0), "ended": data.get("ended", 0),
                      "messages": len(data.get("messages", [])), "windows": data.get("windows", []),
                      "reason": data.get("reason", "")})
        if last is not None and len(found) >= last:
            break
    return found[::-1] if last is not None else found


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
        if role == "tool" and str(message.get("tool_call_id") or "").startswith("incoming_"):
            lines.append("【incoming】" + content)
        elif role == "assistant" and message.get("tool_calls") and all(
                call.get("function", {}).get("name") == "incoming" for call in message["tool_calls"]):
            # 系统替她打开窗口的那一半，不是她的调用。
            continue
        elif role == "assistant":
            if str(message.get("reasoning_content") or "").strip():
                lines.append("【她想】" + message["reasoning_content"])
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
        elif role == "system" and content.startswith(("以下是你的笔记", "还能召回的东西")) and len(content) > 400:
            # WHY: 2026-10-26 审查发现：每段开头的常驻笔记（global/recent/diary）和目录加起来常有上万字，
            # 不折叠的话 read_context 从 start=0 读整整第一页（8000 字）全在看静态笔记，根本看不到这一段
            # 发生了什么，而且用 match 搜词也会先撞上开头的 global。想看当时那一版笔记可调 note_history。
            lines.append(f"【开场笔记与目录，{len(content)} 字，略（查当时版本用 note_history）】")
        else:
            lines.append(f"【{role}】" + content)
    return "\n".join(lines)


def text_tokens(messages: list[dict]) -> int:
    def strip(value):
        if isinstance(value, list):
            return [strip(part) for part in value]
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                return "[image]"
            return {key: strip(part) for key, part in value.items()}
        return value

    return _chat_root.count_tokens(json.dumps(strip(messages), ensure_ascii=False))


def context_size(reported, body: list[dict]) -> int:
    """How long *body* is: the size the API last reported for its first messages, plus an estimate of the rest.

    *reported* is ``[tokens, count]``: the API counted ``tokens`` when ``body[:count]``
    was the whole request and its answer.

    WHY: 维护者 2026-10-23：以接口返回的为准。字数估算把图片当成几个字，DeepSeek 每张图
    却最多算 1024 token（官方图像理解文档），图多的群攒上几百张也不会交接，直到撞上单个
    请求 600 张图或 48 MiB 的上限；接口报的 prompt+completion 连系统提示、工具定义和图片都
    算在内。报数之后才追加的（工具结果、新画面）还没人报过，先按字数估。
    """
    if isinstance(reported, list) and len(reported) == 2 and 0 <= reported[1] <= len(body):
        return reported[0] + text_tokens(body[reported[1]:])
    return text_tokens(body)


def amount(tokens: int) -> str:
    return f"{tokens / 10000:.1f} 万" if tokens >= 10000 else str(tokens)


def next_number(segment: dict) -> int:
    """下一个唤醒编号：这一段里用过的最大值 + 1。

    WHY: 编号是 compress(first=…, last=…) 的地址。只看内存里的 marks 会在重启后从 1 重新
    数——`_MARKS` 会截掉旧的，磁盘里载入的又不一定带号；`last_number` 记住用过的最大值，
    重启也不会回绕。
    """
    highest = int(segment.get("last_number") or 0)
    for one in segment.get("marks") or []:
        highest = max(highest, int(one.get("number") or 0))
    return highest + 1


def numbers(segment: dict) -> list[dict]:
    """Give every wake mark a stable number (1, 2, 3 … oldest first); old segments get one on the spot."""
    marks = segment.get("marks") or []
    highest = int(segment.get("last_number") or 0)
    for one in marks:
        highest = max(highest, int(one.get("number") or 0))
    upcoming = highest + 1
    previous = 0
    for one in marks:
        number = int(one.get("number") or 0)
        if not number or number <= previous:
            # 缺号，或者重启后从 1 重新数过：往回荡平，否则 compress(first=…, last=…) 会切错唤醒
            one["number"] = upcoming
            upcoming += 1
        previous = max(previous, int(one["number"]))
    segment["last_number"] = previous or highest
    return marks


def _mark_at(marks: list[dict], at: str) -> dict | None:
    at = " ".join(str(at or "").split())
    found = [mark for mark in marks if at and at in (mark["at"], mark["at"][11:])]
    return found[-1] if found else None


def make_mark(segment: dict, now: str, boxes: dict, seen: dict) -> dict:
    return {
        "at": now,
        "time": time.time(),
        "index": len(segment["messages"]),
        "number": next_number(segment),
        **{key: json.loads(json.dumps(segment.get(key), ensure_ascii=False)) for key in MARKED},
        "boxes": {label: box["text"] for label, box in boxes.items()
                  if isinstance(box, dict) and box.get("text")},
        "seen": dict(seen),
    }


def record_mark(segment: dict, mark: dict) -> None:
    segment["marks"] = [*segment.get("marks", [])[-(MARKS - 1):], mark]
    segment["last_number"] = mark["number"]


def get_steps(segment: dict, messages: list[dict] | None = None) -> list[dict]:
    """Find all fine-grained action steps (assistant tool call + tool responses) across wakes.

    Each step has:
    - id: "2.1", "2.2" (wake_number.step_number)
    - wake: wake_number
    - sub: step_number
    - start: message index where assistant message starts
    - end: message index right after the last matching tool message
    - tools: list of tool names called in this step
    - tokens: estimated tokens taken by this step
    - at: timestamp of the wake
    - mark: the wake mark dict
    """
    msgs = segment.get("messages", []) if messages is None else messages
    marks = numbers(segment)
    if not marks:
        return []
    steps = []
    for i, mark in enumerate(marks):
        wake_num = int(mark.get("number") or (i + 1))
        start_idx = mark.get("index", 0)
        end_idx = marks[i + 1]["index"] if i + 1 < len(marks) else len(msgs)
        if start_idx >= len(msgs):
            continue
        sub_idx = 1
        pos = start_idx
        while pos < min(end_idx, len(msgs)):
            msg = msgs[pos]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                tool_start = pos
                pos += 1
                while pos < min(end_idx, len(msgs)) and msgs[pos].get("role") == "tool":
                    pos += 1
                tool_end = pos
                tool_names = [
                    call.get("function", {}).get("name") or "tool"
                    for call in msg.get("tool_calls", [])
                ]
                steps.append({
                    "id": f"{wake_num}.{sub_idx}",
                    "wake": wake_num,
                    "sub": sub_idx,
                    "start": tool_start,
                    "end": tool_end,
                    "tools": tool_names,
                    "tokens": text_tokens(msgs[tool_start:tool_end]),
                    "at": mark.get("at", ""),
                    "mark": mark,
                })
                sub_idx += 1
            else:
                pos += 1
    return steps


def subconscious_on() -> bool:
    # 维护者 2026-10-24：默认开。
    return bool(storage.get("", "agent").get("subconscious", True))


def _offered(segment: dict) -> list[dict]:
    """Wakes `compress` may start at: not the first (it carries the handoff notes), not the latest."""
    marks = [mark for mark in segment.get("marks") or [] if mark.get("index", 0) > 0]
    return marks[:-1]


def _recent_hint() -> str:
    mtime = _notes.mtime("recent")
    age = (time.time() - mtime) if mtime > 0 else 0.0
    if age >= RECENT_STALE_SECONDS:
        hours = int(age // 3600)
        return f"（顺便看一眼：recent 已约 {hours} 小时未更新，若眼下手头的事或进展变了，可顺手调 write_note(\"recent\", ...) 刷新）"
    return ""


def nudge(segment: dict) -> str | None:
    """A one-off reminder once the segment has grown `GROWTH_TOKENS` since the last one."""
    if not subconscious_on():
        return None
    messages = segment.get("messages") or []
    if not messages:
        return None
    size = context_size(segment.get("tokens"), messages)
    settled = segment.get("settled")
    if not isinstance(settled, int) or size < settled:
        # 第一次见到这一段（或她自己 dismiss 清短了）：记下现在的大小，这次不提醒。
        segment["settled"] = size
        save(segment)
        return None
    if size - settled < GROWTH_TOKENS:
        return None
    offered = _offered(segment)
    if not offered:
        # 只有第一次唤醒和最近一次：没有能收的，先不提醒，等它们之间多出一次来。
        return None
    segment["settled"] = size
    segment["nudged_at"] = time.time()
    save(segment)
    listing = "\n".join(f"- #{mark.get('number')}（{mark['at'][11:]} 醒来，之后约 {amount(text_tokens(messages[mark['index']:]))} token）"
                        for mark in numbers(segment) if mark in offered)
    steps = get_steps(segment, messages)
    heavy_steps = [s for s in steps if s["tokens"] >= 10_000]
    heavy_str = ""
    if heavy_steps:
        heavy_str = "\n耗量较大的具体步骤（子序号）：\n" + "\n".join(
            f"- {s['id']}: 调用了 {','.join(s['tools'])}（约 {amount(s['tokens'])} token）"
            for s in heavy_steps[:5]
        ) + "\n"
    recent_line = (_recent_hint() + "\n") if _recent_hint() else "收起旧轮次时，若眼下手头的事有进展或变动，也可顺手 write_note(\"recent\", ...) 刷新近况。\n"
    first_ex = offered[0].get("number", 1)
    last_ex = offered[-1].get("number", 1)
    return (f"上下文又长了，这一段现在约 {amount(size)} token。已经了结的事可以顺手收一收，腾点地方。\n"
            f"能收的唤醒（整轮）：\n{listing}\n"
            + heavy_str +
            f"要收整轮直接调 compress(first={first_ex}, last={last_ex}, summary=\"……\")；\n"
            f"要收具体某个步骤（如读错了一大堆日志）调子序号 compress(first=\"{first_ex}.1\", last=\"{first_ex}.1\", summary=\"……\")；\n"
            + recent_line +
            f"不收就不用管，这条提醒到下次收的时候会一起没。")


def subconscious_report() -> str:
    segment = load()
    size = context_size(segment.get("tokens"), segment["messages"]) if segment["messages"] else 0
    settled = segment.get("settled")
    if isinstance(settled, int):
        grown = max(0, size - settled)
        left = max(0, GROWTH_TOKENS - grown)
        gap = f"离下次提醒还差约 {amount(left)} token"
    else:
        gap = "还没到过门槛"
    nudged = segment.get("nudged_at")
    lines = [f"subconscious: {'on' if subconscious_on() else 'off'}",
             f"这一段约 {amount(size)} token；{gap}（门槛 {amount(GROWTH_TOKENS)}）",
             "上次提醒：" + (_screen.clock(nudged) if isinstance(nudged, (int, float)) else "还没提醒过")]
    return "\n".join(lines)


def request_dismiss(session, since: str = "") -> str:
    """Called by the `dismiss` tool: drop everything since a wake header once this round ends; returns why not, or "".

    WHY: 维护者 2026-10-23：follow 一个群她看起来很累。这一段里一遍遍叠着"被叫醒、看一眼、
    没我的事"，她照着这一路往下演，累的是这些痕迹，不是叫醒的次数（先试过攒一批再叫，晚了接不上话，
    痕迹照样留着，撤了）。现在没什么要做的就清掉：从她点名的那行「这次唤醒你的是」起（不写就是
    最近一行），画面、插话、思考、工具调用和结果都不留。赌的是她想的时候会有插话：插话照样叫醒她，
    画面从清掉之前接着给，被清掉的消息和新的连在一起，等于她想的那段时间替她攒了一批。

    WHY: 调过工具也信她：清掉的只是眼前的工作记忆，笔记是文件、行动记录和 llm.log 都还在，
    和交接时由她决定留什么是同一种信任。只拦两种：那以后她发过话（别人多半在接她的话，她得记得
    当时为什么这么说；清了只剩聊天记录里那句话）；输入框里有那以后打的、改过的字（本来就有的不拦，
    维护者说框里的字可能是早先打的）。不跨交接：记号跟着这一段，交接后从新的一段算。

    WHY: 维护者 2026-10-24：follow 期间到的消息画面不截，一直 dismiss 就会越攒越多。兜底是思考：
    一个跟着的窗口里，会被退回去的 follow 期间消息超过 _FRESH_MAX 条，就不让清，她把这批看完留下，
    画面位置跟着往前推；不想跟了就揭掉 follow。
    """
    if getattr(session, "quiet", False) or getattr(session, "segment", None) is None:
        return "只有醒着处理消息的时候能用；交接、反思和子代理里没有可清的"
    marks = session.segment.get("marks") or []
    since = " ".join(str(since).split())
    since_clean = since.lstrip("#")
    found = [mark for mark in marks if not since or since in (
        mark["at"], mark["at"][11:], str(mark.get("number")), f"#{mark.get('number')}"
    ) or since_clean == str(mark.get("number"))]
    if not found:
        recent = "、".join(f"#{mark.get('number', '?')}（{mark['at'][11:]}）" for mark in marks[-5:]) or "没有"
        return f"这一段里找不到「这次唤醒你的是（{since}）」那一行；能清回的最近几次是：{recent}"
    problem = _dismiss_problem(session, found[-1])
    if problem:
        return problem
    session.dismiss = found[-1]
    return ""


def apply_dismiss(session, segment: dict) -> str:
    """Apply a pending dismiss at the end of a round; returns why not, or "" when applied."""
    problem = _dismiss_problem(session, session.dismiss)
    if problem:
        return problem
    _drop_since(segment, session.dismiss)
    return ""


def _detect_fold_level(messages: list[dict]) -> int:
    """Determine the compression level for a slice of messages.
    If the slice contains only raw messages/tools, level is 1 (L1).
    If it already contains a previous folded message (e.g. 【收起 L1】 or 【步骤收起】),
    its level is max(existing_levels) + 1 (L2, L3, ...).
    """
    max_level = 0
    for msg in messages:
        content = str(msg.get("content") or "")
        if "【收起 L" in content or "【步骤收起 L" in content:
            for m in re.finditer(r"【(?:步骤)?收起 L(\d+)", content):
                try:
                    max_level = max(max_level, int(m.group(1)))
                except ValueError:
                    pass
        elif "【收起】" in content or "【步骤收起】" in content:
            max_level = max(max_level, 1)
    return max_level + 1


def request_compress(session, first: int | str = 0, last: int | str = 0, summary: str = "",
                     from_time: str = "", to_time: str = "", **kwargs) -> str:
    """Called by the `compress` tool: fold and archive wakes or sub-steps (this call included); returns what happened.

    WHY: 维护者 2026-10-25：收哪段、摘要怎么写由她定。2026-10-27 扩充子序号粒度（参考 billion_context）：
    支持按唤醒整轮收（first=1），也支持收具体某个动作步骤（first="2.1", last="2.2"）。
    2026-10-28 维护者：废除时刻参数（避免与实际消息时间混淆），全面改为纯序号；自动识别折叠层级（L1/L2）；
    保留调用记录与回执，只截短入参 summary 避免冗余。
    """
    segment = getattr(session, "segment", None)
    if getattr(session, "quiet", False) or segment is None:
        return "只有醒着处理消息的时候能用；交接、反思和子代理里没有可收的"

    # 如果还带着时间参数调用，给出明确指引
    if from_time or to_time or (isinstance(first, str) and ":" in first):
        offered = _offered(segment)
        usable = "、".join(f"#{mark.get('number')}（{mark['at'][11:]}）" for mark in offered) or "暂无"
        return f"compress 现已升级为按【序号】折叠，不再使用时刻传参（避免与截图/消息时间打架）。请传唤醒序号（如 first=26, last=27；只收一轮 last 填 0）或子步骤（如 first=\"28.1\", last=\"28.1\"）；当前可收的唤醒序号有：{usable}"

    summary = str(summary or "").strip()
    if not summary:
        return "先写摘要：收掉以后原文就归档了，摘要要不看原文也接得上（什么事、到哪一步、还欠着什么）"
    if getattr(session, "pending_compress", None) is not None:
        return "同一批工具调用里只能收一次"

    first_raw = str(first).strip() if first is not None else ""
    last_raw = str(last).strip() if last is not None else ""
    is_substep = ("." in first_raw) or ("." in last_raw)

    msgs = session.messages[session.prompt_count:] if hasattr(session, "messages") else segment.get("messages", [])

    if is_substep:
        steps = get_steps(segment, msgs)
        # 排除当前正在调用的 compress 这一步自身
        steps = [s for s in steps if not ("compress" in s["tools"] and s["end"] >= len(msgs) - 1)]
        usable_steps = "、".join(f"{s['id']}（{','.join(s['tools'])}）" for s in steps) or "暂无"
        if not last_raw or last_raw == "0":
            last_raw = first_raw
        s_first = next((s for s in steps if s["id"] == first_raw), None)
        s_last = next((s for s in steps if s["id"] == last_raw), None)
        if s_first is None:
            return f"找不到子步骤 {first_raw}；当前可收的子步骤有：{usable_steps}"
        if s_last is None:
            return f"找不到子步骤 {last_raw}；当前可收的子步骤有：{usable_steps}"
        if s_first["start"] > s_last["start"]:
            return "last 步骤必须在 first 步骤之后或相同"
        if s_first["wake"] != s_last["wake"]:
            # WHY: 2026-10-29 审查：跨唤醒的子步骤区间会把后一次唤醒开头的画面一起收走，`_fold_slice`
            # 又把落在区间里的那个记号直接丢掉，那次唤醒从编号里消失、它第一次给的笔记也不再补。
            # 跨唤醒本来就是整轮收的事。
            return (f"子步骤只能在同一次唤醒里收（{first_raw} 和 {last_raw} 不在同一轮）；"
                    f"跨唤醒请按整轮收，如 compress(first={s_first['wake']}, last={s_last['wake']}, summary=…)")
        start, end = s_first["start"], s_last["end"]
        removed = msgs[start:end]
        level = _detect_fold_level(removed)
        level_tag = f"L{level}"
        saved = text_tokens(removed) - text_tokens([{"role": "system", "content": summary}])
        current_size = context_size(segment.get("tokens"), msgs) - saved
        tools_summary = ", ".join(dict.fromkeys([t for s in steps if s_first["start"] <= s["start"] <= s_last["start"] for t in s["tools"]]))
        where = f"第 {first_raw} 到 {last_raw} 步" if first_raw != last_raw else f"第 {first_raw} 步（{tools_summary}）"
        session.pending_compress = {"mode": "substep", "start": start, "end": end, "summary": summary, "where": where}
        range_desc = f"子步骤 {first_raw}～{last_raw}" if first_raw != last_raw else f"子步骤 {first_raw}（{tools_summary}）"
        return f"收下了：这批工具调用都返回以后，{range_desc} 收成一条【步骤收起 {level_tag}】，预计少约 {amount(max(0, saved))} token、剩约 {amount(max(0, current_size))} token。{_recent_hint()}"

    marks = numbers(segment)
    offered = _offered(segment)
    usable = "、".join(f"#{mark.get('number')}（{mark['at'][11:]}）" for mark in offered) or "暂无"

    if not offered:
        return f"当前没有可收的历史唤醒（第一次唤醒连着交接笔记，最近一次刚做完不收）。能收的唤醒：{usable}"

    first_str = first_raw.lstrip("#")
    if not first_str or not first_str.isdigit() or int(first_str) == 0:
        return f"请指定有效的起始唤醒序号（如 first=26）；当前可收的唤醒序号有：{usable}"
    first_num = int(first_str)

    last_str = last_raw.lstrip("#").lower()
    if last_str in ("latest", "-1", "max", "all"):
        last_num = int(offered[-1].get("number") or first_num)
    elif not last_str or last_str == "0":
        last_num = first_num
    elif last_str.isdigit():
        last_num = int(last_str)
    else:
        return f"last 序号格式不认识（{last_raw}）；支持指定数字或填 0 / 'latest'"

    if first_num > last_num:
        return f"last 序号（#{last_num}）必须大于等于 first 序号（#{first_num}）"

    chosen = [mark for mark in offered if first_num <= int(mark.get("number") or 0) <= last_num]
    if not chosen:
        return f"在可收的唤醒里找不到 #{first_num} 到 #{last_num}；当前能收的是：{usable}"

    mark = chosen[0]
    after = next((m for m in marks if int(m.get("number") or 0) > last_num), None)

    start = mark["index"]
    end = after["index"] if after is not None else len(segment["messages"])
    removed = segment["messages"][start:end]
    level = _detect_fold_level(removed)
    level_tag = f"L{level}"
    saved = text_tokens(removed) - text_tokens([{"role": "system", "content": summary}])
    current_size = context_size(segment.get("tokens"), segment["messages"]) - saved
    lo, hi = int(chosen[0].get("number") or 0), int(chosen[-1].get("number") or 0)
    range_desc = f"唤醒 #{lo}～#{hi}" if lo != hi else f"唤醒 #{lo}"

    session.pending_compress = {"mode": "wake", "mark": mark, "after": after, "summary": summary}
    return f"收下了：这批工具调用都返回以后，{range_desc} 收成一条【收起 {level_tag}】，预计少约 {amount(max(0, saved))} token、剩约 {amount(max(0, current_size))} token。{_recent_hint()}"


def get_folded_blocks(messages: list[dict]) -> list[dict]:
    """Find all folded blocks currently present in messages."""
    blocks = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "system":
            continue
        content = str(msg.get("content") or "")
        match = re.search(r"\b(\d{8}-\d{6}-folded(?:-\d+)?)\b", content)
        if match:
            fold_name = match.group(1)
            first_line = content.strip().split("\n", 1)[0]
            blocks.append({
                "index": i,
                "name": fold_name,
                "preview": first_line,
                "content": content,
            })
    return blocks


def request_decompress(session, name: str = "") -> str:
    """Called by the `decompress` tool: unfold an archived folded block back into the active context.

    WHY: 2026-10-29 草籽需求：参考 billion_context，有 compress 就要有对应的 decompress；
    当摘要漏了关键细节或需要原话核对时，支持随时把收起块无损还原回上下文。
    """
    segment = getattr(session, "segment", None)
    if getattr(session, "quiet", False) or segment is None:
        return "只有醒着处理消息的时候能用；交接、反思和子代理里没有可解压的"
    if getattr(session, "pending_compress", None) is not None or getattr(session, "pending_decompress", None) is not None:
        return "同一批工具调用里只能执行一次压缩或解压操作"

    body = session.messages[session.prompt_count:] if hasattr(session, "messages") else segment.get("messages", [])
    blocks = get_folded_blocks(body)
    if not blocks:
        return "当前上下文中没有被折叠的收起块（无需解压）"

    target = str(name or "").strip()
    if not target:
        usable = "\n".join(f"{i + 1}. {b['name']}（{b['preview'][:60]}）" for i, b in enumerate(blocks))
        return (f"当前上下文中共有 {len(blocks)} 处收起块可解压：\n{usable}\n\n"
                f"请传入 name（如 '{blocks[-1]['name']}'、序号如 {len(blocks)}、或 'latest'）指定解压哪一段。")

    chosen = None
    if target.lower() in ("latest", "-1", "last", "最新"):
        chosen = blocks[-1]
    elif target.isdigit() and 1 <= int(target) <= len(blocks):
        chosen = blocks[int(target) - 1]
    else:
        for b in blocks:
            if target == b["name"] or target in b["name"]:
                chosen = b
                break

    if chosen is None:
        usable = "、".join(b["name"] for b in blocks)
        return f"找不到指定的收起块「{name}」；当前可解压的收起块有：{usable}"

    data = read_archived(chosen["name"])
    if not data or not data.get("messages"):
        return f"找不到归档段 {chosen['name']} 的原始消息，可能已被清理"

    original_messages = data.get("messages", [])
    fold_msg = body[chosen["index"]]
    saved_tokens = text_tokens([fold_msg])
    orig_tokens = text_tokens(original_messages)
    restored_tokens = max(0, orig_tokens - saved_tokens)
    current_tokens = context_size(segment.get("tokens"), body)
    new_size = current_tokens + restored_tokens

    session.pending_decompress = {
        "name": chosen["name"],
        "index": chosen["index"],
        "messages": original_messages,
        "restored_tokens": restored_tokens,
        "new_size": new_size,
    }
    return (f"收下了：这批工具调用都返回以后，收起块 {chosen['name']} 将被解压展开"
            f"（共还原 {len(original_messages)} 条消息，约增加 {amount(restored_tokens)} token、当前段变为约 {amount(new_size)} token）。")


def finish_decompress(session, results) -> None:
    """Unfold the compressed block once this batch's results are in."""
    pending = getattr(session, "pending_decompress", None)
    session.pending_decompress = None
    if not pending:
        return
    segment = session.segment
    body = session.messages[session.prompt_count:]
    name = pending["name"]
    original_messages = pending["messages"]

    # 动态定位包含该 name 的折叠系统消息
    target_idx = None
    for i, msg in enumerate(body):
        if msg.get("role") == "system" and name in str(msg.get("content") or ""):
            target_idx = i
            break
    if target_idx is None:
        _stream.warning(f"decompress 展开时未在上下文中找到 {name}")
        session.messages.append({"role": "system", "content": (
            f"解压 {name} 失败：在上下文中未找到对应的收起行，上下文原样没动。")})
        return

    # 替换消息：把那一行【收起】替换为原来的所有消息
    body = [*body[:target_idx], *original_messages, *body[target_idx + 1:]]
    shift = len(original_messages) - 1

    # 调整 marks 下标
    marks = []
    for mark in segment.get("marks", []):
        idx = mark.get("index", 0)
        if idx <= target_idx:
            marks.append(mark)
        else:
            marks.append({**mark, "index": idx + shift})
    segment["marks"] = marks

    # 调整 tokens
    if isinstance(segment.get("tokens"), list) and len(segment["tokens"]) == 2:
        rep_tokens, rep_idx = segment["tokens"]
        if rep_idx > target_idx:
            segment["tokens"] = [rep_tokens + pending["restored_tokens"], rep_idx + shift]

    segment["messages"] = list(body)
    session.messages[session.prompt_count:] = list(body)
    save(segment)


def finish_compress(session, results) -> None:
    """Fold once this batch's results are in, so the call and its result go together.

    WHY: 2026-10-29 审查：`compress` 当场只是收下，真正折叠在这里。这里出错时她已经读到"收下了"，
    所以不能只在终端留 traceback：在这批结果后面补一句没收成，上下文照原样留着。
    """
    pending = session.pending_compress
    session.pending_compress = None
    segment = session.segment
    body = session.messages[session.prompt_count:]
    try:
        segment["messages"] = list(body)
        if pending.get("mode") == "substep":
            _fold_slice(segment, pending["start"], pending["end"], pending["summary"], pending.get("where", "子步骤"))
        elif pending["after"] is None:
            _fold_since(segment, pending["mark"], pending["summary"])
        else:
            _fold_between(segment, pending["mark"], pending["after"], pending["summary"])
    except Exception as error:
        _stream.exception("compress 收下以后没能折叠")
        segment["messages"] = body
        session.messages.append({"role": "system", "content": (
            f"刚才那次 compress 没收成（{type(error).__name__}: {error}），上下文原样没动；可以换个范围再收一次。")})
        return
    _truncate_compress_call(segment, results)
    session.messages[session.prompt_count:] = segment["messages"]


def _truncate_compress_call(segment: dict, results, max_len: int = 60) -> None:
    """Keep the compress call and tool response in context, but truncate the summary
    argument in the assistant call so it does not redundantly balloon the context.

    WHY: 2026-10-28 维护者：不要从上下文直接抹掉 compress 调用；抹掉会让模型在后续轮次
    发生认知断层（不知道上一轮刚压缩成功、重复调用或不知道自己做了什么）。
    保留调用和回执，只把入参里的 summary 截短到前 N 个字存照，完整的已由【收起】行承载。
    """
    ids = {result.tool_call_id for result in results if result.name == "compress"}
    if not ids:
        return
    body = segment["messages"]
    for message in reversed(body):
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        modified = False
        for call in message["tool_calls"]:
            if call.get("id") in ids and call.get("function", {}).get("name") == "compress":
                raw_args = call["function"].get("arguments", "")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    summary = args.get("summary", "")
                    if len(summary) > max_len:
                        args["summary"] = summary[:max_len] + f"……（共 {len(summary)} 字，完整见收起行）"
                        call["function"]["arguments"] = json.dumps(args, ensure_ascii=False)
                        modified = True
                except Exception:
                    pass
        if modified:
            save(segment)
            break


def _shown_since(segment: dict, mark: dict) -> list[str]:
    """Labels of the windows shown after *mark*: their screen position moved on since."""
    return [label for label, last in segment.get("last_shown", {}).items() if mark["last_shown"].get(label) != last]


def _dismiss_problem(session, mark: dict) -> str:
    if (getattr(session, "last_sent", 0) or 0) >= mark["time"]:
        return f"从 {mark['at']} 起你发过话，这段不能清：别人多半在接你的话，你得记得当时为什么这么说"
    for window in _windows.parse_all(_shown_since(session.segment, mark)):
        piled = _screen.piled(window, mark)
        if piled > _screen.FRESH_MAX:
            label = _screen.label(window)
            return (f"{label} 从 {mark['at']} 起 follow 期间已经攒了 {piled} 条，超过 {_screen.FRESH_MAX} 条就不能再清："
                    f"这次留着，把它们看完；不想跟了就 wake_me(\"follow {label} off\")")
    typed = [label for label, box in session.boxes.items()
             if isinstance(box, dict) and box.get("text") and box["text"] != mark["boxes"].get(label)]
    if typed:
        return (f"{'、'.join(typed)} 的输入框里有 {mark['at']} 之后打的或改过的字，清掉以后你会不记得打过；"
                "先 send，或者用 type_word 改回原样或清空，再 dismiss")
    return ""


def _restore(segment: dict, mark: dict, keys) -> None:
    for key in keys:
        if key not in mark:
            continue  # 早先记的记号没有 scopes
        if mark[key] is None:
            segment.pop(key, None)
        else:
            segment[key] = mark[key]
    segment["marks"] = [kept for kept in segment.get("marks", []) if kept["time"] < mark["time"]]


def _drop_since(segment: dict, mark: dict) -> None:
    """Put the segment back as it was before *mark*'s wake; what was shown since comes back on the next screen."""
    shown = _shown_since(segment, mark)
    segment["messages"] = segment["messages"][:mark["index"]]
    _restore(segment, mark, MARKED)
    save(segment)
    # 画面上的"看到哪"也退回去：清掉的消息下次排在"以下是上次之后的新消息"下面，当没读过的给。
    # oplog 的已读不退（日志里没有"标回未读"这种行），画面外那个窗口的未读条数因此不算它们。
    _screen.restore_seen(shown, mark.get("seen") or {})


def _fold_since(segment: dict, mark: dict, label: str) -> str:
    """Replace the stretch since *mark* with one line naming where it was archived whole; returns that name.

    Unlike a dismiss, what was shown stays read: the screen position does not move back.
    """
    shown = _shown_since(segment, mark)
    removed = segment["messages"][mark["index"]:]
    level = _detect_fold_level(removed)
    level_tag = f"L{level}"
    name = archive({"started": mark["time"], "windows": shown, "reason": label, "level": level,
                    "messages": removed}, "folded")
    where = f"{'、'.join(shown)} 的画面和" if shown else ""
    header_title = f"【收起 {level_tag}】" if level == 1 else f"【收起 {level_tag}·递归汇总】"
    segment["messages"] = [*segment["messages"][:mark["index"]], {"role": "system", "content": (
        f"{header_title}{label}\n（{mark['at'][5:]} 至 {_screen.clock()[5:]}，{where}你那时的想法、调用，后台整理时收成了上面这段；"
        f"原样存在归档段 {name}，加载 memory_system 用 read_context(\"{name}\") 读，search 也搜得到）")}]
    # 画面位置不退。第一次给的笔记、中途的工具状态跟着收走了，这几项退回去，下次照样给。
    _restore(segment, mark, ("scopes", "told", "tokens", "notice"))
    save(segment)
    return name


def _fold_slice(segment: dict, start: int, end: int, label: str, where: str) -> str:
    """Replace an arbitrary message slice (e.g. sub-steps) with one system line."""
    removed = segment["messages"][start:end]
    level = _detect_fold_level(removed)
    level_tag = f"L{level}"
    name = archive({"started": time.time(), "windows": [], "reason": label, "level": level, "messages": removed}, "folded")
    line = {"role": "system", "content": (
        f"【步骤收起 {level_tag}】{label}\n（{where}，中间思考与调用已收成上面这段；"
        f"原样存在归档段 {name}，加载 memory_system 用 read_context(\"{name}\") 读，search 也搜得到）")}
    segment["messages"] = [*segment["messages"][:start], line, *segment["messages"][end:]]
    shift = 1 - len(removed)
    saved = text_tokens(removed) - text_tokens([line])

    def moved(reported):
        if not (isinstance(reported, list) and len(reported) == 2) or reported[1] <= start:
            return reported
        return [reported[0] - saved, reported[1] + shift] if reported[1] >= end else None

    segment["tokens"] = moved(segment.get("tokens"))
    marks = []
    for other in segment.get("marks", []):
        idx = other.get("index", 0)
        if idx < start:
            marks.append(other)
        elif idx >= end:
            other = {**other, "index": idx + shift, "tokens": moved(other.get("tokens"))}
            marks.append(other)
    segment["marks"] = marks
    save(segment)
    return name


def _fold_between(segment: dict, mark: dict, after: dict, label: str) -> str:
    """Replace the wakes from *mark* up to (not including) *after* with one line; what follows stays as it was.

    WHY: 维护者 2026-10-24：潜意识动不了段中间不太好，本以为是像操作数据一样操作上下文。中间收掉一块，
    那之后她那条下一次请求要按原价重读（缓存只认前面一样的部分），偶尔一次只是几分钱，贵的是每轮都改
    中间；所以潜意识看得到每处之后有多少 token，值不值由它按说明判断，代码不设门槛。

    和收到最后不同，后面的唤醒还在：它们的记号下标往前挪，区间里第一次给的笔记从这一段和后面的记号里
    一起退掉（下次再给），工具状态退回区间开头那时的（下次把变化重新说一遍，重复无妨）。区间不含这一段
    第一次唤醒，开头那条交接留下的 notice 不会在里面。
    """
    start, end = mark["index"], after["index"]
    removed = segment["messages"][start:end]
    level = _detect_fold_level(removed)
    level_tag = f"L{level}"
    shown = [window for window, last in after["last_shown"].items() if mark["last_shown"].get(window) != last]
    name = archive({"started": mark["time"], "windows": shown, "reason": label, "level": level, "messages": removed}, "folded")
    where = f"{'、'.join(shown)} 的画面和" if shown else ""
    header_title = f"【收起 {level_tag}】" if level == 1 else f"【收起 {level_tag}·递归汇总】"
    line = {"role": "system", "content": (
        f"{header_title}{label}\n（{mark['at'][5:]} 起到 {after['at'][5:]} 之前，{where}你那时的想法、调用，后台整理时收成了"
        f"上面这段；原样存在归档段 {name}，加载 memory_system 用 read_context(\"{name}\") 读，search 也搜得到）")}
    segment["messages"] = [*segment["messages"][:start], line, *segment["messages"][end:]]
    shift = 1 - len(removed)
    saved = text_tokens(removed) - text_tokens([line])
    given = [scope for scope in after.get("scopes") or [] if scope not in (mark.get("scopes") or [])]

    def moved(reported):
        # 接口报的数若是在这一块之后报的，扣掉这一块的估算；报在这一块里面的已经对不上了，改回按字数估。
        if not (isinstance(reported, list) and len(reported) == 2) or reported[1] <= start:
            return reported
        return [reported[0] - saved, reported[1] + shift] if reported[1] >= end else None

    segment["scopes"] = [scope for scope in segment.get("scopes") or [] if scope not in given]
    segment["tokens"] = moved(segment.get("tokens"))
    if "told" in mark:
        segment["told"] = mark["told"]
    marks = []
    for other in segment.get("marks", []):
        if other["time"] < mark["time"]:
            marks.append(other)
        elif other["time"] >= after["time"]:
            other = {**other, "index": other["index"] + shift, "tokens": moved(other.get("tokens"))}
            if "scopes" in other:
                other["scopes"] = [scope for scope in other["scopes"] or [] if scope not in given]
            if "told" in mark:
                other["told"] = mark["told"]
            marks.append(other)
    segment["marks"] = marks
    save(segment)
    return name
