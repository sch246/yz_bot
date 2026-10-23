"""Her recall tools, the handoff instruction, and the diary roll-ups.

WHY: 2026-10-22 the maintainer split memory this way: recall is hers -- she
reads chat logs, her own actions and notes when she judges she needs them.
Letting go is a handoff: the context she has been working in is about to be
removed, so she confirms every kind of information is where it belongs.  An
earlier version had a separate librarian session with its own assembled
material; it was removed because it saw less than she had seen, and the
handoff only needs the same context with a different purpose.
"""

from __future__ import annotations

import time

from mods import chatlog, history, llm, log

from . import actions as _actions, notes as _notes, screen as _screen, segment as _segment


_stream = log.stream("agent")
_SEARCH_DAYS = 30
_SEARCH_HITS = 30
_NOTE_HITS = 20
_SEGMENT_HITS = 10
_SNIPPET = 300
_READ_CHARS = 8000


def _root():
    import mods.chat as chat

    return chat


def _parse_window(target: str) -> tuple:
    return _root().parse_target(target)


def _charge(session: llm.Chat):
    def handle(chunk: llm.LLMResponse) -> None:
        if chunk.total_tokens:
            _root().inc_call_cost(session.model, chunk.prompt_tokens, chunk.completion_tokens,
                                  chunk.cached_tokens, chunk.requested_at)
    return handle


def _moment(value) -> int | None:
    """Unix seconds, or a local ``YYYY-MM-DD HH:MM[:SS]`` as the chat heads print it."""
    text = str(value or "").strip()
    if not text or text == "0":
        return None
    if text.isdigit():
        return int(text)
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(time.mktime(time.strptime(text, pattern)))
        except ValueError:
            pass
    raise ValueError("before 写 Unix 秒，或 YYYY-MM-DD HH:MM:SS")


def read_log(window: str, count: int = 30, before: str = "") -> str:
    """读一个聊天窗口的 chatlog 原文（与画面同一格式），从最新往前取。

    @param
    window: g<群号> 或 u<私聊对端号>
    count: 取多少条记录，1 到 200
    before: 只取这个时刻之前的记录，用来往前翻页或读 search 找到的那条的前文；写 YYYY-MM-DD HH:MM:SS 或 Unix 秒，留空表示到最新
    """
    target = _parse_window(window)
    items = _screen.records(target, max(1, min(int(count), 200)), _moment(before))
    return _screen.render(target, items) or "（没有记录）"


def read_actions(ids: list[str] | None = None, count: int = 10, keyword: str = "", start: int = 0) -> str:
    """读自己过去的行动记录原文（输出、工具调用与结果），一次最多 8000 字，更长的按 start 往后翻。

    @param
    ids: 行动号列表，如 20261019-12；留空则取最近 count 条
    count: 不给 ids 时取最近多少条，1 到 50
    keyword: 只取原文里含这个词的行动（不区分大小写，在完整原文里找）
    start: 从第几个字开始读，用上一次结尾提示的位置接着读
    """
    # WHY: 先按完整原文筛选、再分页（2026-10-22 审查发现：先截 4000 字再搜，长工具结果的
    # 尾部既按行动号读不到、也搜不到；原文只在行动记录里，召回工具得能拿全）。
    found = _actions.pairs(ids=ids or None)
    if not ids and not keyword:
        # 只要最近几条时先截再渲染；按词筛只能在完整原文里找，没法先截。
        found = found[-max(1, min(int(count), 50)):]
    rendered = [_actions.render(output, result, chars=10**9) for output, result in found]
    if keyword:
        rendered = [text for text in rendered if keyword.casefold() in text.casefold()]
        if not ids:
            rendered = rendered[-max(1, min(int(count), 50)):]
    text = "\n".join(rendered)
    if not text:
        return "（没有记录）"
    start = max(0, int(start))
    if start >= len(text):
        return f"（共 {len(text)} 字，start 超出范围）"
    end = start + _READ_CHARS
    page = text[start:end]
    if start:
        page = f"（第 {start} 字起）\n" + page
    if end < len(text):
        page += f"\n（共 {len(text)} 字，还有 {len(text) - end} 字；用同样的参数加 start={end} 接着读）"
    return page


def _matching(lines, needle: str, most: int) -> tuple[int, list[str]]:
    """How many lines contain *needle*, and the first *most* of them, long ones cut around the hit."""
    count, picked = 0, []
    for line in lines:
        if needle in line.casefold():
            count += 1
            if len(picked) < most:
                picked.append(line)
    shown = []
    for line in picked:
        if len(line) > _SNIPPET:
            at = max(0, line.casefold().find(needle) - _SNIPPET // 3)
            line = ("…" if at else "") + line[at:at + _SNIPPET] + "…"
        shown.append(line)
    return count, shown


def _archive_lines(messages: list[dict]):
    """What only an archived segment holds: her thinking, output, calls and their results.

    WHY: 段里 user 消息是画面、笔记和提示，原文在聊天记录和笔记里，再搜一遍只会让每段
    都因为开头那份 global 命中；她当时想了什么、说了什么、调了什么，才是要靠段名找回的。
    """
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            for line in str(message.get("reasoning_content") or "").splitlines():
                yield "【她想】" + line
            for line in str(message.get("content") or "").splitlines():
                yield "【她】" + line
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                yield f"【她调用】{function.get('name')}({function.get('arguments')})"
        elif role == "tool":
            for line in str(message.get("content") or "").splitlines():
                yield "【结果】" + line


def search(keyword: str, window: str = "", days: int = _SEARCH_DAYS) -> str:
    """按线索一次找遍聊天记录、笔记、日记和归档的上下文，按出处分组，各自最新的在前。

    @param
    keyword: 要找的词（不区分大小写，按原文子串匹配，名字、QQ号、话题都行）
    window: 聊天记录只找这个窗口（g<群号> 或 u<私聊对端号>）；留空找所有窗口。笔记、日记和归档照样都找
    days: 聊天记录和归档往前找多少天，1 到 365；笔记和日记不限时间
    """
    # WHY: 2026-10-23 维护者同意把 search_log 扩成一处找遍：以前笔记和日记只能按名字整份读、
    # 归档只能按段名整段读，"谁说过喜欢什么""哪天聊过这事"只能先猜在哪。仍是子串匹配，
    # 不加索引和语义检索——这个规模下逐个文件扫一遍就够。
    needle = keyword.strip().casefold()
    if not needle:
        raise ValueError("keyword 不能为空")
    days = max(1, min(int(days), 365))
    oldest = time.time() - days * 86400
    sections = []

    windows = [_parse_window(window)] if window else chatlog.known_windows()
    hits = []
    for target in windows:
        for item in _screen.records(target, 100_000, since=oldest):
            if needle in item["text"].casefold():
                hits.append((item["time"] or 0, target, item))
    hits.sort(key=lambda hit: hit[0], reverse=True)
    if hits:
        lines = [f"## 聊天记录（{len(hits)} 条" + (f"，列最新 {_SEARCH_HITS} 条" if len(hits) > _SEARCH_HITS else "")
                 + "；用 read_log 的 before 读前后文）"]
        for _stamp, target, item in hits[:_SEARCH_HITS]:
            day = "{:04d}-{:02d}-{:02d}".format(*item["day"])
            text = item["text"] if len(item["text"]) <= 400 else item["text"][:400] + "…"
            lines.append(f"# {_screen.label(target)} {day}\n{text}")
        sections.append("\n".join(lines))

    for title, scopes in (("笔记", [item["scope"] for item in _notes.listing(limit=10**6)]),
                          ("日记", [f"diary:{key}" for key in reversed(_notes.diaries())])):
        found = []
        for scope in scopes:
            count, shown = _matching(_notes.read(scope).splitlines(), needle, 3)
            if count:
                found.append(f"# {scope}" + (f"（{count} 处）" if count > len(shown) else "") + "\n" + "\n".join(shown))
        if found:
            more = f"，列前 {_NOTE_HITS} 份" if len(found) > _NOTE_HITS else ""
            sections.append(f"## {title}（{len(found)} 份{more}；用 read_note 读全文）\n" + "\n".join(found[:_NOTE_HITS]))

    found, total = [], 0
    # 一次只解析一段：只留要列出来的摘要，其余只计数。
    for name, data in _segment.each_archived(oldest, newest_first=True):
        count, shown = _matching(_archive_lines(data.get("messages", [])), needle, 2)
        if count:
            total += 1
            if len(found) < _SEGMENT_HITS:
                found.append(f"# {name}（{count} 处）\n" + "\n".join(shown))
    if found:
        more = f"，列最新 {_SEGMENT_HITS} 段" if total > _SEGMENT_HITS else ""
        sections.append(f"## 归档的上下文（{total} 段{more}；只找你当时想的、说的、调用和结果；"
                        "加载 memory_system 后用 read_context 读那一段）\n" + "\n".join(found))

    return "\n\n".join(sections) if sections else f"（最近 {days} 天的聊天记录、笔记、日记和归档里都没有找到）"


def read_note(scope: str) -> str:
    """读一份笔记全文。

    @param
    scope: self、global、recent、person:<QQ号> 或 group:<群号>
    """
    return _notes.render(scope) or f"（{scope} 还没有笔记）"


def _recent_windows(exclude: list[tuple] = (), since: float = 0) -> list[tuple[tuple, dict]]:
    with history.lock():
        windows = [((kind, key), events[0]) for kind in ("group", "private")
                   for key, events in history.msgs[kind].items() if events]
    windows.sort(key=lambda item: item[1].get("time", 0), reverse=True)
    return [item for item in windows if item[0] not in exclude and item[1].get("time", 0) >= since]


def directory(woken: list[tuple]) -> str:
    """What else there is to recall, names only: other recent windows, notes, diaries."""
    lines = ["## 最近有消息的其它窗口"]
    lines.extend(f"{_screen.label(window)} 最后 {_screen.clock(latest.get('time'))[5:16]}"
                 for window, latest in _recent_windows(woken)[:20])
    lines.append("## 笔记")
    lines.append("、".join(item["scope"] for item in _notes.listing()) or "（无）")
    diaries = _notes.diaries()
    if diaries:
        days = [key for key in diaries if len(key) == 10][-7:]
        lines.append("## 日记（diary:<日期>，月和年的是汇总）")
        lines.append("、".join([key for key in diaries if len(key) < 10] + days))
    return "\n".join(lines)


_HANDOFF = """这段上下文要被移除了（原因：{reason}）。移除之前确认交接做好：对照这段上下文，逐类确认每种信息都已经在它该在的地方。

- 眼下在进行的事、还没回的话、刚看过的材料的结论与出处 → recent：改写成交接时的样子，做完的、和眼下无关的拿掉，不超过二十行。
- 关于某个人、某个群的认识和经历 → person:<QQ号>／group:<群号>："## 认识"写知道什么，变了就写成"某日起改为……"；"## 经历"一行一件事，带日期和出处（窗口和时刻，或行动号）。
- 做事的教训、被纠正的地方 → global：只留会改变你做法的条目。
- 答应了还没做的事 → todo。
- 今天发生了什么 → diary:{day}：改写成到目前为止的这一天——第一行一句话概括，之后按时间写发生了什么、做了什么决定、答应了什么，每条带出处。已经写过的保留，不要往后追加重复的段落。

每份先用 read_note 读现有内容：已经在了就算确认过，不用重写；没在的补上；过时的删掉。写用 write_note，text 是改写后的全文。不确定是不是事实的，标明"未确认"。
这次不对外说话。都确认好了，用一两句话说交接了什么，然后结束。"""


def handoff_prompt(reason: str, day: str) -> str:
    text = _HANDOFF.format(reason=reason, day=day)
    guidance = _notes.read_policy("handoff").strip()
    if guidance:
        text += "\n\n## 你在反思里给交接写的附加说明\n" + guidance
    return text


def summarize(period: str) -> None:
    """Roll a month's daily diaries, or a year's monthly ones, into ``diary:<period>``.

    Rewritten whole each time from the diaries it covers, so running it twice changes nothing.
    """
    parts = [key for key in _notes.diaries(period + "-") if len(key) == len(period) + 3]
    if len(period) == 4 and not parts:
        parts = [key for key in _notes.diaries(period + "-") if len(key) == 10]
    sources = "\n\n".join(f"### {key}\n{_notes.read('diary:' + key).strip()}" for key in parts)
    if not sources.strip():
        return
    chat = _root()
    session = llm.Chat(model=chat.get_model(), chat_client=llm.get_client())
    session.fail_fast = True
    session.set_messages([
        {"role": "system", "content": "你在替柚子写汇总，不和任何人说话。把下面的日记汇总成一篇"
                                      f"{'月' if len(period) == 7 else '年'}记：第一行一句话概括，之后二十行以内，"
                                      "按时间写要紧的事、认识的人、做过的决定和变化，每条带日期，方便以后按日期翻回日记。"
                                      "只写日记里有的事。直接输出正文。"},
        {"role": "user", "content": sources},
    ])
    replies = session.chat(recall_func=_charge(session))
    text = "".join(chunk.content for chunk in replies if chunk.role == "assistant" and chunk.content).strip()
    if text:
        _notes.write("diary:" + period, text)
