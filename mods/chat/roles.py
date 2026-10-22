"""The two stateless helpers around the main agent: the selector and the librarian.

Neither keeps a conversation.  The selector looks at the current situation and
decides what the main agent sees this time; the librarian looks back at what one
activation saw and did, and edits the notes.  What the main agent keeps in view
across activations is therefore whatever the selector keeps choosing again -- no
task boundary, switch state or similarity rule is written down anywhere.
"""

from __future__ import annotations

import functools
import queue
import threading
import time
import traceback

from mods import chatlog, history, llm, log, oplog

from . import actions as _actions, notes as _notes, screen as _screen


_stream = log.stream("agent")
DEFAULT_HELPER_MODEL = "deepseek/deepseek-flash"
_SELECTOR_CALLS = 6
_LIBRARIAN_CALLS = 12
_PEEK_COUNT = 20


def _root():
    import mods.chat as chat

    return chat


def helper_model(name: str) -> str:
    """WHY: 选材和整理默认用便宜模型（2026-10-19 裁定，与 assign_tasks 的默认一致）；
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


def read_log(window: str, count: int = 30, before: int = 0) -> str:
    """读一个聊天窗口的 chatlog 原文（与画面同一格式），从最新往前取。

    @param
    window: g<群号> 或 u<私聊对端号>
    count: 取多少条记录，1 到 200
    before: 只取这个 Unix 秒之前的记录，用来往前翻页；0 表示到最新
    """
    target = _parse_window(window)
    items = _screen.records(target, max(1, min(int(count), 200)), int(before) or None)
    return _screen.render(target, items) or "（没有记录）"


def read_actions(ids: list[str] | None = None, count: int = 10) -> str:
    """读自己过去的行动记录原文（输出、工具调用与结果）。

    @param
    ids: 行动号列表，如 20261019-12；留空则取最近 count 条
    count: 不给 ids 时取最近多少条，1 到 50
    """
    found = _actions.pairs(ids=ids or None)
    if not ids:
        found = found[-max(1, min(int(count), 50)):]
    return "\n".join(_actions.render(output, result, chars=4000) for output, result in found) or "（没有记录）"


def read_note(scope: str) -> str:
    """读一份笔记全文。

    @param
    scope: global、person:<QQ号> 或 group:<群号>
    """
    return _notes.render(scope) or f"（{scope} 还没有笔记）"


def _counted(function, calls: list[int]):
    """Count a helper's reads so its few allowed requests cannot turn into a loop."""
    @functools.wraps(function)
    def wrapper(**arguments):
        calls[0] += 1
        return function(**arguments)
    return wrapper


def _directory(woken: list[tuple]) -> str:
    chat = _root()
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
    lines.extend(_actions.title(output) for output, _result in _actions.pairs()[-12:])
    hint = chat._agent_hint()
    if hint:
        lines.append("## " + hint)
    return "\n".join(lines)


def default_plan(woken: list[tuple], count: int) -> dict:
    scopes = ["global", *dict.fromkeys(_notes.scope_of(window) for window in woken)]
    return {"situation": "", "windows": [(window, count) for window in woken],
            "notes": scopes, "actions": []}


def select(woken: list[tuple], activations: list[str], current: list[str], count: int) -> dict:
    """Ask the selector what the main agent should see; fall back to the woken windows."""
    plan = default_plan(woken, count)
    chosen: dict = {}
    calls = [0]
    session = llm.Chat(model=helper_model("selector_model"), chat_client=llm.get_client())
    session.fail_fast = True

    def choose(windows: list[dict], notes: list[str], actions: list[str], situation: str) -> str:
        """提交这次给主 agent 看的材料；只调用一次。

        @param
        windows: 要展示的窗口画面，每项 {"window": "g<群号>或u<QQ号>", "count": 条数}；被唤醒窗口里的新消息无论如何都会展示，这里决定还要多少旧消息与其它窗口
        notes: 要附上的笔记 scope 列表
        actions: 要附上全文的过去行动号列表
        situation: 给主 agent 的几句情景说明：现在大概在发生什么、哪些旧事相关；不要复述材料本身
        """
        picked = []
        for item in windows or []:
            window = _parse_window(str(item.get("window", "")))
            picked.append((window, max(0, min(int(item.get("count", count)), 200))))
        chosen.update(windows=picked, notes=[str(scope) for scope in notes or []],
                      actions=[str(key) for key in actions or []], situation=str(situation)[:600])
        return "已提交"

    session.set_tools([_counted(read_log, calls), _counted(read_note, calls), _counted(read_actions, calls), choose])
    session.turn_done = lambda: bool(chosen) or calls[0] >= _SELECTOR_CALLS
    peeks = []
    for window in woken:
        items = _screen.records(window, _PEEK_COUNT)
        if items:
            peeks.append(_screen.render(window, items))
    session.set_messages([
        {"role": "system", "content":
            "你是柚子的前置选材助手，不和任何人说话。你的唯一产出是调用 choose，决定这次主 agent 看到哪些材料。"
            "原则：只给与眼下相关的东西；与当前无关的旧内容就不要选，它们没有丢，以后需要时还能再选回来；"
            "有关联的其它窗口、笔记或自己做过的事才附上。需要确认时可以先用 read_log / read_note / read_actions 看原文，"
            "但总共只有几次机会。画面条数宁少勿滥，被唤醒窗口的新消息系统会自动保留。"},
        {"role": "user", "content": "\n\n".join(part for part in [
            f"现在是 {_screen.clock()}。",
            "## 这次的唤醒\n" + ("\n".join(activations) or "（没有新唤醒，是续做或重新组装）"),
            "## 本次唤醒中已经做过的行动\n" + "\n".join(current) if current else "",
            "## 被唤醒窗口最近的消息\n" + "\n\n".join(peeks) if peeks else "",
            _directory(woken),
        ] if part)},
    ])
    try:
        session.chat(recall_func=_charge(session))
    except Exception:
        _stream.exception("前置选材失败，改用默认材料")
        return plan
    if not chosen:
        return plan
    shown = {window for window, _count in chosen["windows"]}
    # WHY: 唤醒她的那几条新消息是这次的输入本身，不是记忆；选材只能决定除此之外还看什么。
    for window in woken:
        if window not in shown:
            chosen["windows"].append((window, 0))
    return chosen


_LIBRARIAN_PROMPT = """你是柚子的图书管理员，不和任何人说话。一次唤醒刚结束，下面是这次唤醒她实际看到的画面和她做过的行动。你的工作是维护笔记，让以后的她在需要时能找到该找的东西，平时又不被无关内容占满。

笔记有三种范围：global（跨场合有效的事）、person:<QQ号>（关于一个人，也是与此人私聊的笔记）、group:<群号>（关于一个群）。

做法（参照复盘的习惯）：
- 先读现有笔记再改，不重复已有内容；每条写进最合适的范围。
- 优先记：被人纠正的地方 > 她答应的事、做出的决定和理由 > 工具失败与返工 > 出乎意料的情况 > 一般事实。按部就班、符合预期的过程不用记。
- 她作出的决定和承诺本身就是经历：写明日期与来源；以后改变也要写成"某日改为……"，不要悄悄改写成从来如此。
- 删掉已经过时、已经解决、不再会改变她行为的条目。
- 笔记要短：一条一行，带日期；细节留在聊天记录和行动记录里，写"见 g群号 某日某时"或"见行动号"即可，需要时她会去读原文。
- 未完成的事项不写进笔记（她有自己的待办），只在相关笔记里留一句线索。
- 不确定是不是事实的，标明"她的理解"或"未确认"。
- 没有值得改的就直接结束，不必每次都写。

写入用 write_note(scope, text)，text 是这份笔记更新后的全文；传空串表示删除整份笔记。旧版本会自动备份。"""


_jobs: "queue.Queue[dict]" = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


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
    calls = [0]
    session = llm.Chat(model=helper_model("librarian_model"), chat_client=llm.get_client())
    session.fail_fast = True

    def write_note(scope: str, text: str) -> str:
        """用新全文替换一份笔记；空串删除整份笔记。旧版本自动备份。

        @param
        scope: global、person:<QQ号> 或 group:<群号>
        text: 这份笔记更新后的完整内容
        """
        calls[0] += 1
        _notes.write(scope, text)
        return f"已写入 {scope}" if text.strip() else f"已删除 {scope}"

    session.set_tools([write_note, _counted(read_note, calls), _counted(read_log, calls), _counted(read_actions, calls)])
    session.turn_done = lambda: calls[0] >= _LIBRARIAN_CALLS
    current = []
    for scope in job["scopes"]:
        text = _notes.read(scope).strip()
        name = _notes.custom_name(scope)
        current.append(f"### {scope}" + (f"（维护者设定的称呼：{name}）" if name else "")
                       + "\n" + (text or "（空）"))
    others = [item["scope"] for item in _notes.listing() if item["scope"] not in job["scopes"]]
    session.set_messages([
        {"role": "system", "content": _LIBRARIAN_PROMPT},
        {"role": "user", "content": "\n\n".join(part for part in [
            f"唤醒结束于 {_screen.clock(job['time'])}。",
            "## 她看到的画面\n" + "\n\n".join(job["screens"]) if job["screens"] else "",
            "## 她做过的行动\n" + "\n".join(job["actions"]) if job["actions"] else "## 她这次没有行动",
            "## 相关范围的现有笔记\n" + "\n\n".join(current),
            "## 其它已有笔记\n" + ", ".join(others[:60]) if others else "",
        ] if part)},
    ])
    session.chat(recall_func=_charge(session))
