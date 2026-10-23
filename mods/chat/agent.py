"""The central agent loop: one context, appended to on every wake, until a handoff.

WHY: 2026-10-22 维护者定：只要不交接，就一直是同一个上下文。交接之后第一次被叫醒时
从头组装一次——开头是 self、注意事项和工具状态，接着是笔记、一份只列名字的目录、
这次唤醒的画面；之后每次唤醒只往后追加新消息、第一次出现的人或群的笔记、谁叫醒的她，
前面一个字都不动。这一段存在 ``segment`` 的普通文件里，重启后接着用。

交接的驱动力是"这段上下文要被移除了，确认交接做好"：在同一段末尾追加一句交接指令，
她把各类信息确认到该在的地方（recent、人和群的笔记、global、todo、当天日记），
这段才归档。笔记都是改写成应有的样子，所以交接可以安全地跑很多次。

WHY: 交接由她自己在同一段里做，不另起一个只看到摘要的"图书管理员"会话：总结的人得
看到和她一样多，只是目的不同；同一段上下文还能整段命中前缀缓存。
"""

from __future__ import annotations

import json
import threading
import time

from mods import context, image, llm, oplog, py, storage

import mods.chat as _chat_root
from . import notes as _notes, reader as _reader, roles as _roles, screen as _screen, segment as _segment, view as _view

_SEEN_KEY = "seen"
# WHY: 什么时候交接是可以改的策略（2026-10-22 维护者："唤醒条件本身也配置化"）。
# 条件是 `data/notes/policy/tidy.py` 里的 Python，用 `.py` 那份共享环境求值，末行的值为真
# （或是一段说明原因的文字）就交接；文件不存在时用下面这份默认，和它写成文件后一模一样。
DEFAULT_TIDY = """# 什么时候交接。每次子请求结束（stage == "round"）和每次唤醒往这一段追加之前（stage == "assemble"）
# 各求值一次：前面的行照常执行，最后一行是一个表达式，它的值为真就交接，是字符串时就是交接的原因。
# 可用的名字：stage、used（这一段上下文的 token 估算）、grown（这次唤醒长了多少）、limit（#limit 设的整理阈值）、
# since_handoff（这一段开始了多少秒）、kind（唤醒／闹钟／反思）、hour。
"上下文太长" if used > limit else None
"""
# A window shows at least this many lines before its first unseen one.
_LEAD_IN = 3
_FRESH_MAX = 200
_KNOWN_PEOPLE = 6
_TYPING_EVERY = 5
# 一次唤醒里最多交接几次：防她每一段都调 tidy 时原地打转，不是常态限制。
_HANDOFFS_PER_WAKE = 3
_FORCE_AFTER_FAILURES = 2
_FORCE_OVER_THRESHOLD = 2


def _text_tokens(messages: list[dict]) -> int:
    def strip(value):
        if isinstance(value, list):
            return [strip(part) for part in value]
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                return "[image]"
            return {key: strip(part) for key, part in value.items()}
        return value

    return _chat_root.count_tokens(json.dumps(strip(messages), ensure_ascii=False))


def _seen() -> dict:
    data = storage.get("", "agent")
    value = data.get(_SEEN_KEY)
    if not isinstance(value, dict):
        value = data[_SEEN_KEY] = {}
    return value


def _woken(notice: dict | None, turn) -> tuple[list[tuple], list[str], list[int]]:
    windows: list[tuple] = []
    lines: list[str] = []
    authors: list[int] = []
    if notice is not None:
        for item in notice.get("activations", []):
            if isinstance(item.get("user_id"), int):
                authors.append(item["user_id"])
            window = tuple(item["window"])
            windows.append(window)
            stamp = _screen.clock(item["time"]) if isinstance(item.get("time"), (int, float)) else "?"
            lines.append(f"{_screen.label(window)} {item.get('kind')} 作者={item.get('user_id')} 时间={stamp}")
        for window in notice.get("windows", []):
            windows.append(tuple(window))
    windows.extend(window for window in turn.associated_windows if window[0] in ("group", "private"))
    return list(dict.fromkeys(windows)), lines, authors


def _note_block(scopes: list[str], title: str) -> dict | None:
    parts = [text for scope in scopes if (text := _notes.render(scope))]
    if not parts:
        return None
    return {"role": "user", "content": title + "\n\n" + "\n\n".join(parts)}


def _head(woken: list[tuple], segment: dict) -> list[dict]:
    """What a new segment starts with: her standing notes and a directory of the rest.

    WHY: 做法（global）、交接下来的 recent 和最近一篇日记在每一段开头；叫醒她的窗口和
    叫她的人的笔记跟着画面一起出现（见 `_people`）。待办每个子请求都挂在末尾。别的都由
    她自己召回，目录只列名字。
    """
    days = [key for key in _notes.diaries() if len(key) == 10]
    scopes = ["global", "recent", *(f"diary:{days[-1]}" for _ in days[-1:])]
    segment["scopes"] = list(scopes)
    messages = []
    block = _note_block(scopes, "以下是你的笔记（普通文件，你随时能改，可能不完整）：")
    if block:
        messages.append(block)
    messages.append({"role": "user", "content": "还能召回的东西（用 search 找，read_log、read_actions、read_note 读）：\n"
                     + _roles.directory(woken)})
    return messages


def _people(windows: list[tuple], authors: list[int], segment: dict) -> list[dict]:
    """Notes for windows and people appearing in this segment for the first time."""
    scopes = [_notes.scope_of(window) for window in windows]
    scopes.extend(f"person:{author}" for author in authors[:_KNOWN_PEOPLE])
    new = [scope for scope in dict.fromkeys(scopes) if scope not in segment["scopes"]]
    segment["scopes"].extend(new)
    block = _note_block(new, "这一段里第一次出现的人或群，你的笔记：")
    return [block] if block else []


def _screen_text(window: tuple, count: int, last: str | None) -> tuple[str, tuple, str | None]:
    """One window's chatlog lines for this wake, the (time, arrival) to mark seen, and its last record.

    *last* is the last record of this window already shown earlier in this segment;
    then only the records after it are new.
    """
    mark = _seen().get(_screen.label(window))
    # WHY: 标已读只标到读画面这一刻为止的未读（2026-10-22 审查发现：生成期间同窗口
    # 再来的 at 会在第一次输出时一起被标掉，收尾不再接续）。chatlog 写入与登记到达
    # 在同一把窗口锁里、先写后登记，所以先取边界再读文件，边界内的都在画面能读到的文件里。
    through = oplog.latest_pending_arrival(window)
    items = _screen.records(window, max(count, 1) + _FRESH_MAX)
    texts = [item["text"] for item in items]
    if last is not None and last in texts:
        # WHY: 这一段里已经给过这个窗口：按"上次给到哪一条"接着给，不按看到时间——看到
        # 时间只精确到秒，同一秒里后到的那条按时间会被当成看过。
        after = items[len(texts) - texts[::-1].index(last):]
        if not after:
            return "", (None, through), last
        shown = after[-_FRESH_MAX:]
        text = _screen.render(window, shown, mark)
        if len(after) > _FRESH_MAX:
            text = (f"（{_screen.label(window)} 又有 {len(after)} 条新消息，这里只展示最新的 {len(shown)} 条；"
                    f"更早的用 read_log(\"{_screen.label(window)}\", before=\"{_screen.clock(shown[0]['time'])}\") 往前翻）\n" + text)
        return text, (_screen.latest_time(shown), through), shown[-1]["text"]
    fresh = [item for item in items if mark is not None and item["time"] is not None and item["time"] > mark]
    # WHY: 新消息（_FRESH_MAX 条以内）全部展示，画面条数只决定还附多少旧消息；至少带上新消息之前的
    # 几条接上话头。看到时间只精确到秒，同一秒里后到的那条也靠这几条不丢。
    older = [item for item in items if item not in fresh][-max(count - len(fresh), _LEAD_IN):]
    shown = older + fresh if mark is not None else items[-max(count, _LEAD_IN):]
    skipped = 0
    if len(fresh) > _FRESH_MAX:
        # WHY: 维护者 2026-10-22 定：新消息太多（比如离线一晚）只展示最新的 _FRESH_MAX 条，
        # 但要明说跳过了多少、从哪里接着翻，看到位置照样推进；不静默丢，也不撑爆上下文。
        total = sum(1 for item in _screen.records(window, 1_000_000, since=mark)
                    if item["time"] is not None and item["time"] > mark)
        shown = fresh[-_FRESH_MAX:]
        skipped = total - len(shown)
    if not shown:
        return "", (None, through), last
    text = _screen.render(window, shown, mark)
    if skipped:
        text = (f"（{_screen.label(window)} 上次之后有 {skipped + len(shown)} 条新消息，这里只展示最新的 "
                f"{len(shown)} 条；更早的 {skipped} 条要看就用 read_log(\"{_screen.label(window)}\", "
                f"before=\"{_screen.clock(shown[0]['time'])}\") 往前翻）\n" + text)
    return text, (_screen.latest_time(shown), through), shown[-1]["text"]


def _unread_hint(on_screen: set[tuple]) -> str:
    rows = []
    for detail in oplog.pending_details():
        window = tuple(detail["window"])
        if window[0] not in ("group", "private") or window in on_screen:
            continue
        rows.append(f"{_screen.label(window)} {detail['unread']} 条"
                    + (f"（@你 {detail['mentions']}）" if detail["mentions"] else ""))
    return ("其它窗口还有没看过的消息：" + "；".join(rows[:10])
            + "。要看就用 read_log 读原文。") if rows else ""


def _mark_seen(newest: dict[tuple, tuple]) -> None:
    seen = _seen()
    for window, (stamp, through) in newest.items():
        if stamp is not None:
            seen[_screen.label(window)] = max(stamp, seen.get(_screen.label(window), 0))
        try:
            _reader.mark_window_read(window, through=through)
        except ValueError:
            # 离线补回仍在进行：未读前端没有固定，下次再标。
            pass
    storage.save()


def _did_something(messages: list[dict]) -> bool:
    return any(message.get("role") == "assistant" for message in messages)


def _tidy_reason(turn, segment: dict, stage: str, used: int, grown: int = 0) -> str:
    """Evaluate the handoff condition; a non-empty string means hand off now, for that reason."""
    _max_events, threshold = _chat_root.limit()
    namespace = dict(py.loc)
    namespace.update(stage=stage, used=used, grown=grown, limit=threshold,
                     since_handoff=time.time() - segment["started"], kind=turn.log["kind"],
                     hour=time.localtime().tm_hour)
    code = _notes.read_policy("tidy").strip() or DEFAULT_TIDY
    try:
        result = py.eval_last(code, namespace)
    except Exception:
        # 她或维护者写坏了条件：这次按默认判断，终端里留下 traceback。
        _roles._stream.exception("交接条件求值失败，按默认判断")
        result = py.eval_last(DEFAULT_TIDY, namespace) if code != DEFAULT_TIDY else None
    if not result:
        return ""
    return result if isinstance(result, str) else "交接条件成立"


def diary_day(moment: float) -> str:
    """The day a handoff writes about: until 6 a.m. it is still the day before."""
    return time.strftime("%Y-%m-%d", time.localtime(moment - 6 * 3600))


def _handoff(turn, reason: str, model: str | None = None) -> bool:
    """Confirm the handoff at the end of the current segment, then archive it.

    WHY: 交接没跑完（出错、被打断、没有输出）就不归档：这一段原样留着，下次再交接一遍。
    笔记都是改写成应有的样子、内容没变就不写，所以重跑不会重复。"跑完"指她最后一次回复
    不再调用工具：2026-10-23 审查发现，只看"有过输出"时，中途出错、被打断、被阈值停下
    都会被当成交接完成而归档，没来得及记下的东西就跟着这段一起离开了眼前。
    """
    segment = _segment.load()
    if not _did_something(segment["messages"]):
        return False
    if turn.handoffs >= _HANDOFFS_PER_WAKE:
        _roles._stream.warning(f"这次唤醒已经交接了 {turn.handoffs} 次，不再交接（原因：{reason}）")
        turn.handoff_failed = True
        return False
    _max_events, threshold = _chat_root.limit()
    forced = _forced_reason(segment, threshold)
    if forced:
        name = _archive(turn, segment, f"未交接，强制归档（{forced}；本来要交接的原因：{reason}）")
        _roles._stream.warning(f"这一段{forced}，没有交接就归档为 {name}")
        turn.handoff_note = (f"上一段上下文没能交接（{forced}），已经原样归档为 {name}。recent 和其它笔记可能没记到最后；"
                             f"需要时加载 memory_system 用 read_context(\"{name}\") 读那一段，把漏下的补进笔记。")
        return True
    turn.handoffs += 1
    messages = [*segment["messages"],
                {"role": "user", "content": _roles.handoff_prompt(reason, diary_day(time.time()))}]
    grew = []

    def stop_when(used: int, initial: int) -> bool:
        if used - initial > threshold:
            grew.append(used - initial)
            return True
        return False

    session, failure = None, ""
    try:
        session = _run_session(model, turn, segment, messages, lambda: None, stop_when,
                               quiet=True, persist=False, fail_fast=True, known=len(segment["messages"]))
    except Exception as error:
        _roles._stream.exception("交接出错")
        failure = f"出错：{type(error).__name__}: {error}"
    turn.release = ""
    if not failure:
        added = session.messages[_system_count(session) + len(messages):]
        last = session.messages[-1] if session.messages else {}
        if turn.cancelled:
            failure = "被打断"
        elif grew:
            failure = f"交接时又长了 {grew[0]} token，超过整理阈值，被停下"
        elif not any(message.get("role") == "assistant" and (str(message.get("content") or "").strip()
                                                             or message.get("tool_calls")) for message in added):
            failure = "没有输出"
        elif last.get("role") != "assistant" or last.get("tool_calls"):
            failure = "没有正常结束"
    if failure:
        turn.handoff_failed = True
        if failure != "被打断":
            # 被打断是有人 ^C，不算交接本身的毛病；其余的记在这一段里，连着失败就强制归档。
            segment["handoff_failures"] = segment.get("handoff_failures", 0) + 1
            _segment.save(segment)
        _roles._stream.warning(f"交接没完成（{failure}），这一段（{_screen.clock(segment['started'])} 开始）原样留着；"
                               f"已经连着失败 {segment.get('handoff_failures', 0)} 次")
        return False
    segment["messages"] = _body(session)
    name = _archive(turn, segment, reason)
    _roles._stream.info(f"交接完成（{reason}），归档为 {name}")
    turn.handoff_note = f"你刚把上一段上下文交接掉了（原因：{reason}）。接着做刚才的事，做到哪儿见 recent。"
    return True


def _forced_reason(segment: dict, threshold: int) -> str:
    """Why this segment must be archived without a handoff, or empty.

    WHY: 维护者 2026-10-23：整理阈值设在模型上下文的一半左右，正常不会走到这里；真走到了
    （交接连着失败，或者这一段已经是阈值的两倍、连交接请求本身都发不出去），就别卡死，
    原样归档、告诉她，由她自己去读那一段补笔记。
    """
    failures = segment.get("handoff_failures", 0)
    if failures >= _FORCE_AFTER_FAILURES:
        return f"交接连着失败了 {failures} 次"
    used = _text_tokens(segment["messages"])
    if used > threshold * _FORCE_OVER_THRESHOLD:
        return f"已经长到 {used} token，超过整理阈值的 {_FORCE_OVER_THRESHOLD} 倍"
    return ""


def _archive(turn, segment: dict, reason: str) -> str:
    segment["reason"] = reason
    name = _segment.archive(segment)
    turn.handed_off = reason
    return name


def _system_count(session: llm.Chat) -> int:
    return next((index for index, message in enumerate(session.messages) if message.get("role") != "system"),
                len(session.messages))


def _begin(turn, window: tuple | None, kind: str) -> None:
    turn._chat_usage_tokens = 0
    turn.requested_reads = []
    turn.associated_windows = {window} if window is not None else set()
    turn.screens = {}
    turn.authors = []
    turn.release = ""
    turn.handed_off = ""
    turn.handoff_note = ""
    turn.handoff_failed = False
    turn.handoffs = 0
    turn.alarm = None
    turn.log = {"kind": kind, "start": time.time(), "peak": 0}


def _keep_typing(turn, window: tuple | None, stop: threading.Event) -> None:
    """Show "对方正在输入…" in the private chats she is working on until the wake ends.

    WHY: 维护者 2026-10-22 想要循环进行时有"正在输入"。NapCat 的 set_input_status 只支持
    私聊（C2C），QQ 群没有这个状态。没有明确的"取消"调用，所以隔几秒续一次，唤醒结束
    就不再续。失败只记一次，不影响她干活。
    """
    from mods import connect

    failed = False
    while not stop.is_set():
        try:
            targets = {window} if window is not None and window[0] == "private" else set()
            targets.update(_reader.parse_target(label) for label in tuple(turn.screens) if label.startswith("u"))
            for target in targets:
                reply = connect.call_api("set_input_status", user_id=str(target[1]), event_type=1)
                if reply.get("retcode") not in (0, None) and not failed:
                    failed = True
                    _roles._stream.warning(f"设置正在输入失败：{reply.get('wording') or reply.get('retcode')}")
        except Exception:
            if not failed:
                failed = True
                _roles._stream.exception("设置正在输入失败")
        stop.wait(_TYPING_EVERY)


def _drive_agent(model: str | None, window: tuple | None, alarm: tuple[str, str] | None = None) -> bool:
    """Hold the agent's turn until nothing is left to do; False when someone else holds it."""
    turn, owner = context.begin_turn(_chat_root.AGENT_WINDOW)
    if not owner:
        return False
    _begin(turn, window, "闹钟" if alarm else "唤醒")
    turn.alarm = alarm
    typing = threading.Event()
    threading.Thread(target=_keep_typing, args=(turn, window, typing), name="chat-typing", daemon=True).start()
    # WHY: 一次对话 = 这次持有的全过程（多轮 + 插话续写，直到 finally），图片检查台账就
    # 活在这段里：同一张图不重复下载/解析，对话结束即清掉，下次再聊重新检查一遍。
    image_ledger = image.begin_conversation()
    origin = context.current()
    try:
        while True:
            context.set_current(None)
            context.set_agent_mode(True)
            try:
                if not _run_agent(model, turn):
                    return True
                if turn.release and not turn.cancelled:
                    # 她叫了 tidy，或者这一段涨过阈值：先交接，再用新的一段接着干。
                    if not turn.handoff_failed:
                        _handoff(turn, turn.release, model)
                    turn.release = ""
                    continue
            finally:
                context.set_agent_mode(False)
                context.set_current(origin)
            if turn.cancelled:
                return True
            turn.associated_windows.intersection_update(
                {window for window, _count, active in oplog.pending_summary() if active})
            if not context.finish_turn(_chat_root.AGENT_WINDOW, turn, oplog.has_unnotified):
                return True
    finally:
        typing.set()
        image.end_conversation(image_ledger)
        context.end_turn(_chat_root.AGENT_WINDOW, turn)
        # WHY: 循环停下的唯一信号就在这里，见 _run_hint。
        if origin is not None and window is not None:
            _chat_root._run_hint(window, turn)


def alarm(reason: str, rule: str) -> bool:
    """A wake she or the maintainer scheduled in the awaker note; False when she is busy."""
    return _drive_agent(None, None, (reason, rule))


def request_tidy(note: str) -> bool:
    """Called by the `tidy` tool: hand off this segment once this round ends."""
    turn = context.get_turn(_chat_root.AGENT_WINDOW)
    if turn is None or not hasattr(turn, "release"):
        return False
    turn.release = note or "她自己要求交接"
    return True


def _run_agent(model: str | None, turn) -> bool:
    max_events, _threshold = _chat_root.limit()
    notice = oplog.deliver_notifications(_chat_root.AGENT_WINDOW)
    woken, activation_lines, authors = _woken(notice, turn)
    turn.authors = list(dict.fromkeys([*turn.authors, *authors]))
    continuing = turn.handed_off
    if notice is None and not woken and not turn.alarm and not continuing:
        return True
    segment = _segment.load()
    if _did_something(segment["messages"]) and not turn.handoff_failed:
        reason = _tidy_reason(turn, segment, "assemble", _text_tokens(segment["messages"]))
        if reason:
            if _handoff(turn, reason, model):
                continuing = turn.handed_off
            # 没交接成功时文件里多了失败次数，也要重读，否则这次唤醒的保存会把它盖掉。
            segment = _segment.load()
    if _chat_root.get_reasoning_mode() == "drop":
        # 以前几次唤醒的思考不再带回；这次唤醒之内照旧原样保留（DeepSeek 带工具时要求）。
        for message in segment["messages"]:
            message.pop("reasoning_content", None)
    material: list[dict] = []
    if not segment["messages"]:
        material.extend(_head(woken, segment))
    # 交接后续做时没有新唤醒，沿用刚才看着的窗口。
    focus = woken or [_reader.parse_target(label) for label in turn.screens]
    material.extend(_people(focus, turn.authors, segment))
    screens: dict[str, str] = {}
    newest: dict[tuple, tuple] = {}
    shown_last: dict[str, str] = {}
    for window in focus:
        label = _screen.label(window)
        text, mark, last = _screen_text(window, max_events, segment.setdefault("last_shown", {}).get(label))
        newest[window] = mark
        if text:
            screens[label] = text
            shown_last[label] = last
            material.append({"role": "user", "content": _view.msg_split(text)})
    now = _screen.clock()
    if notice is not None:
        material.append({"role": "user", "content": f"这次唤醒你的是（{now}）：\n" + "\n".join(activation_lines)})
    if turn.alarm:
        reason_text, rule = turn.alarm
        material.append({"role": "user", "content": f"这次是 awaker 笔记里的闹钟叫醒你的（{now}，规则：{rule}）：{reason_text}\n"
                         "没有人在等你回话；看看要不要做点什么，没事就直接结束。"})
    if continuing:
        material.append({"role": "user", "content": turn.handoff_note})
        turn.handed_off = ""
    if not material:
        return True

    def first_output() -> None:
        turn.alarm = None
        if notice is not None:
            oplog.acknowledge_notification(notice["id"])
        _mark_seen(newest)
        turn.screens.update(screens)
        segment["windows"] = list(dict.fromkeys([*segment["windows"], *screens]))
        segment["last_shown"].update(shown_last)

    def stop_when(used: int, initial: int) -> bool:
        if turn.release:
            return True
        reason = _tidy_reason(turn, segment, "round", used, used - initial)
        if reason:
            turn.release = reason
            return True
        return False

    session = _run_session(model, turn, segment, [*segment["messages"], *material], first_output, stop_when,
                           hints=[lambda: _unread_hint(set(newest))], known=len(segment["messages"]))
    return session.output_recorded


def _body(session: llm.Chat) -> list[dict]:
    """Everything after the system prompt, which is rebuilt on every wake."""
    return session.messages[_system_count(session):]


def _run_session(model: str | None, turn, segment: dict, messages: list[dict], first_output, stop_when,
                 *, hints=(), load: tuple[str, ...] = (), quiet: bool = False, persist: bool = True,
                 fail_fast: bool = False, known: int = 0) -> llm.Chat:
    """One model session over the segment plus this wake's material; outputs and results go to the oplog.

    With *persist*, the segment file follows the session after every round, but only
    once she has produced output: a request that fails before that leaves the file
    as it was, so the same material is not appended twice.  The first *known* of
    *messages* are already in the segment, so the request log skips them.
    """
    session = llm.Chat(model=model or _chat_root.get_model(), chat_client=llm.get_client())
    session.requested_reads = turn.requested_reads
    session.associated_windows = turn.associated_windows
    session.quiet = quiet
    session.fail_fast = fail_fast
    binding = _chat_root._activate_chat(session, messages)
    if load:
        binding.load(list(load))
    binding.sync_registry()
    told = [segment.get("told")]
    if told[0] is None:
        # 新的一段：完整工具状态放在系统提示之后，这一段里不再变。
        state = binding.state_text(None)
        if state:
            position = next((index for index, message in enumerate(session.messages)
                             if message.get("role") != "system"), len(session.messages))
            session.messages.insert(position, {"role": "user", "content": state})
        told[0] = binding.state_snapshot()
    # WHY: 请求日志以前每次唤醒都从头打印，同一段越长、醒得越勤，llm.log 里同样的前缀就重复
    # 越多遍（2026-10-23 审查）。这一段已有的部分在 current.json 和归档里都有原样；新的一段
    # 从头打印，系统提示因此每段记一次。
    session.logged = _system_count(session) + known if known else 0

    def tool_changes() -> list[dict]:
        binding.sync_registry()
        current = binding.state_snapshot()
        if current == told[0]:
            return []
        text = binding.state_text(told[0])
        told[0] = current
        return [{"role": "user", "content": text}] if text else []
    # WHY: 原生 assistant/tool 配对原样留在 messages（含思考，DeepSeek 带工具时要求回传），
    # 并随这一段一起存下：下次唤醒看到的就是这一路下来的原样。
    session.preserve_native = True
    session.keep_reasoning = True
    session.output_recorded = False
    initial = _text_tokens(session.messages)
    turn._chat_usage_tokens = initial
    turn.log["peak"] = max(turn.log["peak"], initial)

    def save() -> None:
        if persist and session.output_recorded:
            segment["messages"] = _body(session)
            segment["told"] = told[0]
            _segment.save(segment)

    def record_output(assistant, calls):
        source = oplog.output(_chat_root.AGENT_WINDOW, assistant, calls)
        if not session.output_recorded:
            first_output()
        session.output_recorded = True
        return source

    def record_results(source, results, _native):
        for result in results:
            binding.touch(result.name)
        if results:
            oplog.result(_chat_root.AGENT_WINDOW, source, [
                {"position": position, "name": result.name, "arguments": result.arguments,
                 "content": result.content, "tool_call_id": result.tool_call_id}
                for position, result in enumerate(results)], "")

    def turn_done() -> bool:
        used = _text_tokens(session.messages)
        turn._chat_usage_tokens = used
        turn.log["peak"] = max(turn.log["peak"], used)
        save()
        return stop_when(used, initial)

    session.on_output = record_output
    session.on_results = record_results
    session.turn_done = turn_done
    session.add_context_provider(tool_changes)
    for hint in hints:
        session.add_hint(hint)
    session.add_hint(lambda: f"现在是 {_screen.clock()}。")
    session.should_stop = lambda: turn.cancelled
    session.chat(recall_func=_chat_root.get_handler(session), description_cache=_chat_root.description_cache)
    save()
    return session


_REFLECT_PROMPT = """现在是每晚反思的时间（{now}）。没有人叫你，这次不要对外说话。

像人排查你的问题那样排查自己：
1. 从症状出发：上面列出了上次反思以来归档的每一段上下文。有人说你忘了、你找东西没找到、你答错了、交接漏了什么——先找到这样的地方。没有线索时，挑一两段读一读。
2. 顺着原始记录查：read_context 读那一段当时的原样（你当时看到了什么、做了什么）；search 一次找遍聊天记录、笔记、日记和归档；read_log 读聊天原文；read_actions 读行动记录；note_history 看当时笔记里写了什么。弄清是没记下、记下了没想起来、还是交接漏了。
3. 先评估：changes 里还没有评估结论的改动，对照它之后的记录，问题少了吗？用 assess_change 写下结论；没用的就改回去。
4. 找到原因再改，一次最多一两处，用 edit_policy 写明原因和预期：交接的附加说明（handoff）、你的做法（global）、什么时候交接（tidy）、你对自己的认识（self，只有现在能改，大多数晚上不需要动它）。机制本身要改就写进 proposals 交给维护者。
5. 没发现问题就什么都不改，直接结束。"""


def _tail(text: str, lines: int) -> str:
    return "\n".join(text.strip().split("\n")[-lines:]) if text.strip() else ""


def reflect(model: str | None = None) -> bool:
    """The nightly pass: hand off the day's segment, roll up diaries, then look back.  False when busy.

    WHY: 维护者 2026-10-22：她要像人排查她那样排查自己——从症状出发，顺着原始记录
    （归档的上下文、聊天记录、行动记录、笔记的历史版本）查原因，再决定改不改。
    占住 AGENT_WINDOW，期间来的召唤照常登记，结束后由 pick_up_pending 接上。
    """
    turn, owner = context.begin_turn(_chat_root.AGENT_WINDOW)
    if not owner:
        return False
    _begin(turn, None, "反思")
    data = storage.get("", "agent")
    last = data.get("reflected_at")
    image_ledger = image.begin_conversation()
    try:
        context.set_current(None)
        context.set_agent_mode(True)
        try:
            _handoff(turn, "夜里了", model)
            _roll_up(diary_day(turn.log["start"]))
            segments = _segment.archived(last if isinstance(last, (int, float)) else 0)
            segments = [item for item in segments if not item["name"].endswith("-reflect")]
            if segments:
                _run_reflection(model, turn, segments)
        finally:
            context.set_agent_mode(False)
        data["reflected_at"] = turn.log["start"]
        storage.save()
    finally:
        image.end_conversation(image_ledger)
        context.end_turn(_chat_root.AGENT_WINDOW, turn)
    pick_up_pending()
    return True


def _roll_up(day: str) -> None:
    """On a month's or a year's last night, rewrite its summary from the diaries it covers."""
    following = time.localtime(time.mktime(time.strptime(day, "%Y-%m-%d")) + 36 * 3600)
    periods = []
    if following.tm_mday == 1:
        periods.append(day[:7])
        if following.tm_mon == 1:
            periods.append(day[:4])
    for period in periods:
        try:
            _roles.summarize(period)
        except Exception:
            _roles._stream.exception(f"汇总 {period} 失败")


def _run_reflection(model: str | None, turn, segments: list[dict]) -> None:
    _max_events, threshold = _chat_root.limit()
    notes = [text for scope in ("global", "recent") if (text := _notes.render(scope))]
    listing = "\n".join(
        f"{item['name']}  {_screen.clock(item['started'])[5:16]} 至 {_screen.clock(item['ended'])[5:16]}  "
        f"{item['messages']} 条消息  窗口 {'、'.join(item['windows']) or '无'}"
        + (f"  {item['reason']}" if item.get("reason") else "") for item in segments)
    changes = _tail(_notes.read_policy("changes"), 40)
    proposals = _tail(_notes.read_policy("proposals"), 20)
    messages = [
        {"role": "user", "content": "以下是你的笔记：\n\n" + ("\n\n".join(notes) or "（还没有）")},
        {"role": "user", "content": "## 上次反思以来归档的上下文（用 read_context 读原样）\n" + listing},
        {"role": "user", "content": "## 策略改动记录（changes，最近的）\n" + (changes or "（还没有）")
         + "\n\n## 给维护者的提案（proposals，最近的）\n" + (proposals or "（还没有）")},
        {"role": "user", "content": _REFLECT_PROMPT.format(now=_screen.clock())},
    ]
    reflection = _segment.fresh()
    # 反思材料本身可能就很大：只限制它之后长出来的部分。
    session = _run_session(model, turn, reflection, messages, lambda: None,
                           lambda used, initial: used - initial > threshold,
                           load=("memory_system",), quiet=True, persist=False)
    # 反思这一段也留下来，排查"她昨晚为什么改了这个"时要看。
    reflection["messages"] = _body(session)
    _segment.archive(reflection, "reflect")


def pick_up_pending() -> None:
    """Start a wake for calls that arrived while nobody was there to take them."""
    if not oplog.has_unnotified():
        return
    window = next(iter(oplog.unacknowledged_windows()), None)
    if window is None:
        window = next((window for window, _count, active in oplog.pending_summary()
                       if active and window[0] in ("group", "private")), None)
    if window is not None:
        _drive_agent(None, window)
