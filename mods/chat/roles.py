"""Recall tools for the main agent, and the librarian who lets things go.

WHY: 2026-10-22 the maintainer split memory this way: recall is hers -- she
reads chat logs, her own actions and notes when she judges she needs them --
and letting go is the librarian's, who turns what she has looked at into short
conclusions with a pointer back to the original.  An earlier version had a
separate selector deciding what she sees; it was removed because choosing what
to remember is part of being the one who remembers, and with one cheap model
for everything it saved nothing.
"""

from __future__ import annotations

import functools
import queue
import threading
import time
import traceback

from mods import chatlog, history, llm, log

from . import actions as _actions, notes as _notes, screen as _screen


_stream = log.stream("agent")
DEFAULT_HELPER_MODEL = "deepseek/deepseek-flash"
_LIBRARIAN_CALLS = 12
_SEARCH_DAYS = 7
_SEARCH_HITS = 30


def _root():
    import mods.chat as chat

    return chat


def helper_model(name: str) -> str:
    """WHY: 整理默认用便宜模型（2026-10-19 裁定，与 assign_tasks 的默认一致）；
    配置里没有这个供应商时退回主模型，而不是让整次唤醒失败。"""
    chat = _root()
    selection = chat.storage.get("", "agent").get(name) or DEFAULT_HELPER_MODEL
    try:
        llm.resolve_model(chat.llm_config, selection)
    except ValueError:
        return chat.get_model()
    return selection


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
    before: 只取这个时刻之前的记录，用来往前翻页或读 search_log 找到的那条的前文；写 YYYY-MM-DD HH:MM:SS 或 Unix 秒，留空表示到最新
    """
    target = _parse_window(window)
    items = _screen.records(target, max(1, min(int(count), 200)), _moment(before))
    return _screen.render(target, items) or "（没有记录）"


def read_actions(ids: list[str] | None = None, count: int = 10, keyword: str = "") -> str:
    """读自己过去的行动记录原文（输出、工具调用与结果）。

    @param
    ids: 行动号列表，如 20261019-12；留空则取最近 count 条
    count: 不给 ids 时取最近多少条，1 到 50
    keyword: 只取原文里含这个词的行动（不区分大小写）
    """
    rendered = [_actions.render(output, result, chars=4000) for output, result in _actions.pairs(ids=ids or None)]
    if keyword:
        rendered = [text for text in rendered if keyword.casefold() in text.casefold()]
    if not ids:
        rendered = rendered[-max(1, min(int(count), 50)):]
    return "\n".join(rendered) or "（没有记录）"


def search_log(keyword: str, window: str = "", days: int = _SEARCH_DAYS) -> str:
    """按线索在聊天记录里找：返回含这个词的记录原文，最新的在前。

    @param
    keyword: 要找的词（不区分大小写，按原文子串匹配，名字、QQ号、话题都行）
    window: 只找这个窗口（g<群号> 或 u<私聊对端号>）；留空找所有窗口
    days: 往前找多少天，1 到 90
    """
    needle = keyword.strip().casefold()
    if not needle:
        raise ValueError("keyword 不能为空")
    windows = [_parse_window(window)] if window else chatlog.known_windows()
    oldest = time.time() - max(1, min(int(days), 90)) * 86400
    hits = []
    for target in windows:
        for item in _screen.records(target, 100_000, since=oldest):
            if needle in item["text"].casefold():
                hits.append((item["time"] or 0, target, item))
    hits.sort(key=lambda hit: hit[0], reverse=True)
    lines = []
    for _stamp, target, item in hits[:_SEARCH_HITS]:
        day = "{:04d}-{:02d}-{:02d}".format(*item["day"])
        text = item["text"] if len(item["text"]) <= 400 else item["text"][:400] + "…"
        lines.append(f"# {_screen.label(target)} {day}\n{text}")
    more = f"\n（共 {len(hits)} 条，只列最新 {_SEARCH_HITS} 条；缩小 window 或 days 再找）" if len(hits) > _SEARCH_HITS else ""
    return "\n".join(lines) + more if lines else "（没有找到）"


def read_note(scope: str) -> str:
    """读一份笔记全文。

    @param
    scope: global、recent、person:<QQ号> 或 group:<群号>
    """
    return _notes.render(scope) or f"（{scope} 还没有笔记）"


def _counted(function, calls: list[int]):
    """Count a helper's reads so its few allowed requests cannot turn into a loop."""
    @functools.wraps(function)
    def wrapper(**arguments):
        calls[0] += 1
        return function(**arguments)
    return wrapper


def directory(woken: list[tuple], current: list[str] = ()) -> str:
    """What she could recall: other recent windows, the notes, her latest actions."""
    lines = ["## 最近有消息的其它窗口"]
    with history.lock():
        windows = [((kind, key), events[0]) for kind in ("group", "private")
                   for key, events in history.msgs[kind].items() if events]
    windows.sort(key=lambda item: item[1].get("time", 0), reverse=True)
    for window, latest in [item for item in windows if item[0] not in woken][:20]:
        body = " ".join(str(latest.get("message", "")).split())[:40]
        lines.append(f"{_screen.label(window)} 最后 {_screen.clock(latest.get('time'))} {body}")
    lines.append("## 笔记目录（scope / 首行）")
    lines.extend(f"{item['scope']} / {item['first']}" for item in _notes.listing())
    lines.append("## 最近的行动（行动号 / 时间 / 工具 / 摘要）")
    lines.extend([_actions.title(output) for output, _result in _actions.pairs()[-12 - len(current):]
                  if output["id"] not in current][-12:])
    return "\n".join(lines)


_LIBRARIAN_PROMPT = """你是柚子的图书管理员，不和任何人说话。下面是她刚看过的画面和做过的行动。召回是她自己的事；你负责放下：把看过、做过的东西收成短短的结论和出处，原文留在聊天记录和行动记录里，她需要时会自己去读。目标是以后的她平时不被无关内容占满，又能顺着线索找回原文。

笔记分四种范围，对应三种记忆：
- recent（短期记忆）：眼下在发生什么、她手上的事和答应过的事、刚看过的材料的结论与出处。每次唤醒她都会看到它。总是改写成当前的样子：做完的、和眼下无关的就拿掉，以后还会有用的先移进对应的人或群的笔记。不超过二十行。
- global（做法，过程记忆）：她怎么做事——被人纠正过的地方、自己总结出的做法和禁忌。每次唤醒她也都会看到，所以要短，只留会改变她行为的条目。
- person:<QQ号>（关于一个人，也是与此人私聊的笔记）和 group:<群号>（关于一个群）：分两段。"## 认识"写她知道什么（语义记忆：这人是谁、喜好、关系），改了就改，写成"某日起改为……"；"## 经历"一行一件事，带日期和出处（"见 g群号 某日某时"或"见行动号"），是指向原文的索引（情景记忆），旧的、不再要紧的可以删。

做法（参照复盘的习惯）：
- 先读现有笔记再改，不重复已有内容；每条写进最合适的范围。
- 优先记：被人纠正的地方 > 她答应的事、做出的决定和理由 > 工具失败与返工 > 出乎意料的情况 > 一般事实。按部就班、符合预期的过程不用记。
- 她作出的决定和承诺本身就是经历：写明日期与来源；以后改变也要写成"某日改为……"，不要悄悄改写成从来如此。
- 删掉已经过时、已经解决、不再会改变她行为的条目。
- 不确定是不是事实的，标明"她的理解"或"未确认"。
- 除了 recent，没有值得改的就不必写。

写入用 write_note(scope, text)，text 是这份笔记更新后的全文；传空串表示删除整份笔记。旧版本会自动备份。"""

_PHASES = {
    "end": "这次唤醒刚结束。",
    "release": "这次唤醒还没结束，但她眼前的上下文太长了。你整理完之后，她的上下文会被重新组装："
               "下面这些行动只剩标题，原始画面重新给出。她接下来还要用到的结论和出处，务必写进 recent。",
}


_jobs: "queue.Queue[dict]" = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_organize_lock = threading.Lock()


def involved_scopes(windows: list[tuple], new_texts: list[str]) -> list[str]:
    scopes = ["global", *(_notes.scope_of(window) for window in windows)]
    bot = None
    try:
        from mods import identity
        bot = identity.bot_id()
    except Exception:
        pass
    for text in new_texts:
        for line in text.split("\n"):
            if line.startswith(("    ", "#", "—")):
                continue
            head = chatlog._split_head(line)
            if head and head["sender_id"] and head["sender_id"] != bot:
                scopes.append(f"person:{head['sender_id']}")
    return list(dict.fromkeys(scopes))[:12]


def enqueue(job: dict) -> None:
    """Queue one finished activation for the librarian; runs on its own thread."""
    global _worker
    _jobs.put(job)
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="chat-librarian", daemon=True)
            _worker.start()


def _work() -> None:
    while True:
        try:
            job = _jobs.get(timeout=60)
        except queue.Empty:
            return
        try:
            organize(job)
        except Exception:
            traceback.print_exc()
            _stream.exception("图书管理员整理失败")


def organize(job: dict) -> None:
    """One librarian pass.  WHY: 背景收尾和唤醒中途的放下都会改写 recent，串行跑，
    后一次读到的是前一次写完的笔记。"""
    with _organize_lock:
        _organize(job)


def _organize(job: dict) -> None:
    calls = [0]
    session = llm.Chat(model=helper_model("librarian_model"), chat_client=llm.get_client())
    session.fail_fast = True

    def write_note(scope: str, text: str) -> str:
        """用新全文替换一份笔记；空串删除整份笔记。旧版本自动备份。

        @param
        scope: global、recent、person:<QQ号> 或 group:<群号>
        text: 这份笔记更新后的完整内容
        """
        calls[0] += 1
        _notes.write(scope, text)
        return f"已写入 {scope}" if text.strip() else f"已删除 {scope}"

    session.set_tools([write_note, _counted(read_note, calls), _counted(read_log, calls),
                       _counted(search_log, calls), _counted(read_actions, calls)])
    session.turn_done = lambda: calls[0] >= _LIBRARIAN_CALLS
    current = []
    for scope in ["recent", *job["scopes"]]:
        text = _notes.read(scope).strip()
        name = _notes.custom_name(scope)
        current.append(f"### {scope}" + (f"（维护者设定的称呼：{name}）" if name else "")
                       + "\n" + (text or "（空）"))
    others = [item["scope"] for item in _notes.listing() if item["scope"] not in ("recent", *job["scopes"])]
    session.set_messages([
        {"role": "system", "content": _LIBRARIAN_PROMPT},
        {"role": "user", "content": "\n\n".join(part for part in [
            f"现在是 {_screen.clock(job['time'])}。" + _PHASES[job.get("phase", "end")],
            "## 她看到的画面\n" + "\n\n".join(job["screens"]) if job["screens"] else "",
            "## 她做过的行动\n" + "\n".join(job["actions"]) if job["actions"] else "## 她这次没有行动",
            "## 相关范围的现有笔记\n" + "\n\n".join(current),
            "## 其它已有笔记\n" + ", ".join(others[:60]) if others else "",
        ] if part)},
    ])
    session.chat(recall_func=_charge(session))
