"""The central agent loop: every request is assembled afresh from durable state.

WHY: 2026-10-19 the maintainer replaced "replay the agent's own transcript from
a fixed start" with a per-activation rebuild (see
docs/working/proposals/stateless-agent-loop.md).  The oplog still records every
output and result; it is no longer shown back wholesale.  Each wake shows only
what a person would have in mind on being called: how she does things, what was
going on just now, what she has done today, the window that called her and who
is in it.  Everything else she recalls herself.

WHY: 2026-10-22 维护者定的极简做法：白天只有一份"今天做过的事"的列表，跟着每次唤醒
往下长；图书管理员不在每次唤醒后自动跑，只在她自己调用 `tidy`、上下文太长、这份列表
太长时把它收进笔记，夜里再统一写日记、整理笔记。
"""

from __future__ import annotations

import json
import time

from mods import context, image, llm, oplog, py, storage

import mods.chat as _chat_root
from . import actions as _actions, journal as _journal, notes as _notes, reader as _reader, roles as _roles, screen as _screen, view as _view

_SEEN_KEY = "seen"
# The day's list keeps this many latest actions in full; older ones keep their title.
_CURRENT_FULL = 12
_CURRENT_CHARS = 1500
_TIDIED_KEY = "tidied_at"
# WHY: 什么时候请图书管理员整理是可以改的策略（2026-10-22 维护者："唤醒条件本身也配置化"）。
# 条件是 `data/notes/policy/tidy.py` 里的 Python，用 `.py` 那份共享环境求值，末行的值为真
# （或是一段说明原因的文字）就整理；文件不存在时用下面这份默认，和它写成文件后一模一样。
DEFAULT_TIDY = """# 什么时候请图书管理员整理。每次子请求结束（stage == "round"）和每次组装前（stage == "assemble"）
# 各求值一次：前面的行照常执行，最后一行是一个表达式，它的值为真就整理，是字符串时就是交给图书管理员的原因。
# 可用的名字：stage、used（当前上下文 token）、grown（这次组装以来长了多少）、limit（max_token × 整理百分比）、
# day_tokens / day_count（上次整理以来的行动列表）、since_tidy（距上次整理的秒数）、kind（唤醒／闹钟／反思）、hour。
reason = None
if stage == "round" and used > limit and grown > limit // 3:
    reason = "上下文太长"
elif stage == "assemble" and day_tokens > limit // 3:
    reason = "今天的行动列表太长"
reason
"""
# A window shows at least this many lines before its first unseen one.
_LEAD_IN = 3
_FRESH_MAX = 200
_KNOWN_PEOPLE = 6


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


def _plan(woken: list[tuple], authors: list[int], count: int) -> dict:
    """What is in mind on waking, before she recalls anything herself.

    WHY: 做法（global）、短期记忆（recent）和最近一篇日记每次都在（待办在末尾 hint）；叫醒她的
    窗口和叫她的人的笔记也在——认出是谁在叫自己不是一次检索，是看到就想起来的。除此之外
    都由她自己召回。
    """
    days = [key for key in _notes.diaries() if len(key) == 10]
    scopes = ["global", "recent", *(f"diary:{days[-1]}" for _ in days[-1:]),
              *(_notes.scope_of(window) for window in woken)]
    scopes.extend(f"person:{author}" for author in authors[:_KNOWN_PEOPLE])
    return {"windows": [(window, count) for window in woken], "notes": list(dict.fromkeys(scopes))}


def tidied_at() -> float:
    """When the librarian last folded the day's actions into notes."""
    data = storage.get("", "agent")
    value = data.get(_TIDIED_KEY)
    if not isinstance(value, (int, float)):
        # 第一次运行新循环：从现在起算，不把旧日志里的行动一股脑算进今天。
        value = data[_TIDIED_KEY] = time.time()
        storage.save()
    return float(value)


def _day_actions() -> list[str]:
    """Everything she has done since the last tidy; older entries keep only their title."""
    found = _actions.pairs(after=tidied_at())
    older, recent = found[:-_CURRENT_FULL], found[-_CURRENT_FULL:]
    lines = [_actions.title(output) for output, _result in older]
    lines.extend(_actions.render(output, result, chars=_CURRENT_CHARS) for output, result in recent)
    return lines


def _assemble(plan: dict, turn, woken: list[tuple]) -> tuple[list[dict], dict[str, str], dict[tuple, tuple], list[str]]:
    """Build the request body; nothing from earlier requests is replayed."""
    seen = _seen()
    messages: list[dict] = []
    note_parts = [text for scope in plan["notes"] if (text := _notes.render(scope))]
    summary = ["笔记 " + ("、".join(f"{part.split(chr(10), 1)[0].removeprefix('## 笔记 ')}({len(part)}字)"
                                   for part in note_parts) or "（无）")]
    if note_parts:
        messages.append({"role": "user", "content": "以下是你的笔记（普通文件，你和图书管理员都会写，可能不完整）：\n\n"
                         + "\n\n".join(note_parts)})
    messages.append({"role": "user", "content": "你可以召回的东西（用 read_log、search_log、read_actions、read_note 读原文）：\n"
                     + _roles.directory(woken, tidied_at())})
    today = _day_actions()
    summary.append(f"今天的行动 {len(today)} 条")
    if today:
        messages.append({"role": "user", "content": "你上次整理以来做过的事（较早的只列标题，可用 read_actions 读全文；"
                         "整理过的已经收进笔记）：\n" + "\n".join(today)})
    screens: dict[str, str] = {}
    newest: dict[tuple, tuple] = {}
    for window, count in plan["windows"]:
        mark = seen.get(_screen.label(window))
        # WHY: 标已读只标到读画面这一刻为止的未读（2026-10-22 审查发现：生成期间同窗口
        # 再来的 at 会在第一次输出时一起被标掉，收尾不再接续）。chatlog 写入与登记到达
        # 在同一把窗口锁里、先写后登记，所以先取边界再读文件，边界内的都在画面能读到的文件里。
        through = oplog.latest_pending_arrival(window)
        items = _screen.records(window, max(count, 1) + _FRESH_MAX)
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
            continue
        text = _screen.render(window, shown, mark)
        if skipped:
            text = (f"（{_screen.label(window)} 上次之后有 {skipped + len(shown)} 条新消息，这里只展示最新的 "
                    f"{len(shown)} 条；更早的 {skipped} 条要看就用 read_log(\"{_screen.label(window)}\", "
                    f"before=\"{_screen.clock(shown[0]['time'])}\") 往前翻）\n" + text)
        screens[_screen.label(window)] = text
        summary.append(f"画面 {_screen.label(window)} {len(shown)} 条（新 "
                       + (f"{skipped + len(shown)} 条，跳过较早的 {skipped} 条" if skipped
                          else f"{len(fresh) if mark is not None else len(shown)} 条") + "）")
        newest[window] = (_screen.latest_time(shown), through)
        messages.append({"role": "user", "content": _view.msg_split(text)})
    return messages, screens, newest, summary


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


def _tidy_reason(turn, stage: str, used: int = 0, grown: int = 0) -> str:
    """Evaluate the tidy condition; a non-empty string means tidy now, for that reason."""
    _max_events, max_tokens = _chat_root.limit()
    today = _day_actions()
    namespace = dict(py.loc)
    namespace.update(stage=stage, used=used, grown=grown,
                     limit=max_tokens * _chat_root.agent_setting("pressure_percent") // 100,
                     day_tokens=_chat_root.count_tokens("\n".join(today)), day_count=len(today),
                     since_tidy=time.time() - tidied_at(), kind=turn.log["kind"],
                     hour=time.localtime().tm_hour)
    code = _notes.read_policy("tidy").strip() or DEFAULT_TIDY
    try:
        result = py.eval_last(code, namespace)
    except Exception as error:
        # 她或维护者写坏了条件：这次按默认判断，并在日志里留一条，等反思时看到。
        _roles._stream.exception("整理条件求值失败")
        turn.log["warnings"].append(f"⚠ 整理条件出错，按默认判断：{type(error).__name__}: {error}")
        result = py.eval_last(DEFAULT_TIDY, namespace) if code != DEFAULT_TIDY else None
    if not result:
        return ""
    return result if isinstance(result, str) else "整理条件成立"


def _job(turn, phase: str, note: str = "") -> dict:
    """What the librarian gets: the screens she saw this wake and everything since the last tidy."""
    return {
        "time": time.time(),
        "phase": phase,
        "note": note,
        "wake": _screen.clock(turn.log["start"]),
        "screens": list(turn.screens.values()),
        "actions": [_actions.render(output, result) for output, result in _actions.pairs(after=tidied_at())],
        "scopes": _roles.involved_scopes(
            [_reader.parse_target(label) for label in turn.screens], list(turn.screens.values())),
    }


def _tidy(turn, phase: str, note: str) -> None:
    """Let the librarian fold the day's list into notes; the list restarts from now."""
    turn.log["lines"].append(f"整理（{note}）：上下文约 {turn._chat_usage_tokens} token")
    started = time.time()
    try:
        _roles.organize(_job(turn, phase, note))
    except Exception:
        # 整理失败也照样继续：列表还在，只是没有收进笔记。
        _roles._stream.exception("整理失败")
        turn.log["warnings"].append("⚠ 整理失败，见终端日志")
        return
    turn.tidied.update(turn.action_ids)
    storage.get("", "agent")[_TIDIED_KEY] = started
    storage.save()


_RECALL = ("read_log", "search_log", "read_actions", "read_note")


def _begin(turn, window: tuple | None, kind: str) -> None:
    turn._chat_usage_tokens = 0
    turn.requested_reads = []
    turn.associated_windows = {window} if window is not None else set()
    turn.action_ids = []
    turn.screens = {}
    turn.authors = []
    turn.release = ""
    turn.tidied = set()
    turn.alarm = None
    turn.log = {"kind": kind, "start": time.time(), "lines": [], "warnings": [], "peak": 0,
                "assemblies": 0, "said": {}, "other": {}}


def _note_results(turn, results) -> None:
    """Journal lines for one result batch: what she recalled, said and used."""
    log = turn.log
    released = turn.tidied
    for result in results:
        name = result.name.rsplit("__", 1)[-1]
        arguments = _actions.parse_arguments(result.arguments)
        content = str(result.content)
        if name == "say":
            target = str(arguments.get("target", "?"))
            log["said"][target] = log["said"].get(target, 0) + 1
            continue
        if name not in _RECALL:
            log["other"][name] = log["other"].get(name, 0) + 1
            continue
        shown = json.dumps(arguments, ensure_ascii=False)
        outcome = "没有找到" if content.startswith("（没有") else f"{len(content)} 字"
        log["lines"].append(f"召回：{name}({shown[:160]}) → {outcome}")
        if name == "search_log":
            keyword = str(arguments.get("keyword", "")).strip()
            if outcome == "没有找到":
                log["warnings"].append(f"⚠ search_log 没找到：{keyword}")
            holders = [scope for scope in ["global", "recent", *(item["scope"] for item in _notes.listing())]
                       if keyword and keyword.casefold() in _notes.read(scope).casefold()]
            if holders:
                log["warnings"].append(f"⚠ 去聊天记录里找的「{keyword}」，笔记 {'、'.join(dict.fromkeys(holders))} 里已经有")
        if name == "read_actions":
            again = released & set(arguments.get("ids") or [])
            if again:
                log["warnings"].append(f"⚠ 读回了刚放下的行动 {'、'.join(sorted(again))}：放下太早，或结论没写够")


def _write_journal(turn, cancelled: bool) -> None:
    try:
        _compose_journal(turn, cancelled)
    except Exception:
        _roles._stream.exception("唤醒记录写入失败")


def _compose_journal(turn, cancelled: bool) -> None:
    log = turn.log
    if not log["assemblies"] and not turn.action_ids and not log["lines"]:
        return
    ids = turn.action_ids
    head = f"{log['kind']} {_screen.clock(log['start'])[11:]} 起" + (
        f"（行动 {ids[0]} 至 {ids[-1]}，共 {len(ids)} 次）" if ids else "（没有行动）")
    body = list(log["lines"])
    if log["said"]:
        body.append("说话：" + "；".join(f"{target} {count} 次" for target, count in log["said"].items()))
    elif log["kind"] in ("唤醒", "闹钟"):
        body.append("说话：没有开口")
    if log["other"]:
        body.append("其它工具：" + "、".join(f"{name}×{count}" for name, count in log["other"].items()))
    body.append(f"结束：最高约 {log['peak']} token" + ("；被取消" if cancelled else ""))
    body.extend(f"⚠ 画面里可能有人在说她忘了：{line}" for line in _journal.forgot_lines(list(turn.screens.values())))
    body.extend(dict.fromkeys(log["warnings"]))
    _journal.append(head, body)


def _drive_agent(model: str | None, window: tuple | None, alarm: tuple[str, str] | None = None) -> bool:
    """Hold the agent's turn until nothing is left to do; False when someone else holds it."""
    turn, owner = context.begin_turn(_chat_root.AGENT_WINDOW)
    if not owner:
        return False
    _begin(turn, window, "闹钟" if alarm else "唤醒")
    turn.alarm = alarm
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
            finally:
                context.set_agent_mode(False)
                context.set_current(origin)
            if turn.cancelled:
                return True
            if turn.release:
                # 她叫了 tidy，或者这次唤醒的上下文涨过阈值：先让图书管理员把今天的事收进
                # 笔记，再重新组装接着干，不等新消息。
                note, turn.release = turn.release, ""
                _tidy(turn, "release", note)
                continue
            turn.associated_windows.intersection_update(
                {window for window, _count, active in oplog.pending_summary() if active})
            if not context.finish_turn(_chat_root.AGENT_WINDOW, turn, oplog.has_unnotified):
                return True
    finally:
        image.end_conversation(image_ledger)
        context.end_turn(_chat_root.AGENT_WINDOW, turn)
        _write_journal(turn, turn.cancelled)
        # WHY: 循环停下的唯一信号就在这里，见 _run_hint。
        if origin is not None and window is not None:
            _chat_root._run_hint(window, turn)


def alarm(reason: str, rule: str) -> bool:
    """A wake she or the maintainer scheduled in the awaker note; False when she is busy."""
    return _drive_agent(None, None, (reason, rule))


def request_tidy(note: str) -> bool:
    """Called by the `tidy` tool: fold the day's list into notes once this round ends."""
    turn = context.get_turn(_chat_root.AGENT_WINDOW)
    if turn is None or not hasattr(turn, "release"):
        return False
    turn.release = note or "她自己要求整理"
    return True


def _run_agent(model: str | None, turn) -> bool:
    max_events, max_tokens = _chat_root.limit()
    turn._chat_limit = (max_events, max_tokens)
    notice = oplog.deliver_notifications(_chat_root.AGENT_WINDOW)
    woken, activation_lines, authors = _woken(notice, turn)
    turn.authors = list(dict.fromkeys([*turn.authors, *authors]))
    # 续做时没有新唤醒，沿用刚才看着的窗口。
    focus = woken or [_reader.parse_target(label) for label in turn.screens]
    if notice is None and not focus and not turn.action_ids and not turn.alarm:
        return True
    before = _tidy_reason(turn, "assemble")
    if before:
        # 比如今天的列表本身已经太长：先收进笔记再组装，免得每次醒来都背着它。
        _tidy(turn, "release", before)
    reason = "醒来" if not turn.log["assemblies"] else "整理后" if turn.tidied else "续做"
    messages, screens, newest, summary = _assemble(_plan(focus, turn.authors, max_events), turn, focus)
    if not messages and notice is None:
        return True
    if notice is not None:
        messages.append({"role": "user", "content": "这次唤醒你的是：\n" + "\n".join(activation_lines)})
        turn.log["lines"].extend("叫醒：" + line for line in activation_lines)
    if turn.alarm:
        reason_text, rule = turn.alarm
        messages.append({"role": "user", "content": f"这次是 awaker 笔记里的闹钟叫醒你的（规则：{rule}）：{reason_text}\n"
                         "没有人在等你回话；看看要不要做点什么，没事就直接结束。"})
        turn.log["lines"].append(f"闹钟：{rule}")

    def first_output() -> None:
        turn.alarm = None
        if notice is not None:
            oplog.acknowledge_notification(notice["id"])
        _mark_seen(newest)
        turn.screens.update(screens)

    def over_budget(used: int, initial: int) -> bool:
        # WHY: 不是"到 N 轮就截断"（见 llm.chat 的 WHY）：整理掉的行动已经写进 oplog 和笔记，
        # 前提没有被砍掉，只是收起了。默认条件只算这次组装以来长出来的部分：开局材料本身就大
        # 时，整理也不会变小，不能因此空转。
        reason = _tidy_reason(turn, "round", used, used - initial)
        if reason:
            turn.release = reason
            return True
        return False

    return _run_session(model, turn, messages, first_output, lambda used, initial: bool(turn.release) or over_budget(used, initial),
                        hints=[lambda: _unread_hint(set(newest))],
                        assembly=f"组装 {turn.log['assemblies'] + 1}（{reason}）", summary=summary)


def _run_session(model: str | None, turn, messages: list[dict], first_output, stop_when,
                 *, hints=(), assembly: str, summary: list[str], load: tuple[str, ...] = ()) -> bool:
    """One model session over freshly assembled messages; every output and result goes to the oplog."""
    session = llm.Chat(model=model or _chat_root.get_model(), chat_client=llm.get_client())
    session.requested_reads = turn.requested_reads
    session.associated_windows = turn.associated_windows
    binding = _chat_root._activate_chat(session, messages)
    if load:
        binding.load(list(load))
    # WHY: 已加载模块的正文（Skill）以前只在加载那一刻进经历、靠重放留在眼前；现在每次
    # 重建都给出完整工具状态，放在系统提示之后，跨唤醒不变的部分仍能命中前缀缓存。
    binding.sync_registry()
    told = [binding.state_snapshot()]
    state = binding.state_text(None)
    if state:
        position = next((index for index, message in enumerate(session.messages)
                         if message.get("role") != "system"), len(session.messages))
        session.messages.insert(position, {"role": "user", "content": state})

    def tool_changes() -> list[dict]:
        binding.sync_registry()
        current = binding.state_snapshot()
        if current == told[0]:
            return []
        text = binding.state_text(told[0])
        told[0] = current
        return [{"role": "user", "content": text}] if text else []
    # WHY: 一次唤醒之内原生 assistant/tool 配对原样留在 messages（含思考，DeepSeek 带工具时要求
    # 回传）；唤醒结束即丢弃，下次只从 oplog 渲染行动记录，不重放原生消息。
    session.preserve_native = True
    session.keep_reasoning = True
    session.output_recorded = False
    initial = _text_tokens(session.messages)
    turn.log["assemblies"] += 1
    turn.log["peak"] = max(turn.log["peak"], initial)
    turn.log["lines"].append(f"{assembly}：约 {initial} token；" + "；".join(summary))

    def record_output(assistant, calls):
        source = oplog.output(_chat_root.AGENT_WINDOW, assistant, calls)
        turn.action_ids.append(source)
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
            try:
                _note_results(turn, results)
            except Exception:
                # 日志只是观察；它出错不能打断她正在做的事。
                _roles._stream.exception("唤醒记录整理失败")

    def turn_done() -> bool:
        used = _text_tokens(session.messages)
        turn._chat_usage_tokens = used
        turn.log["peak"] = max(turn.log["peak"], used)
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
    return session.output_recorded


_REFLECT_CHARS = 24000
_REFLECT_PROMPT = """现在是每晚反思的时间（{now}）。没有人叫你，这次不要对外说话。

上面是上次反思以来的记忆系统日志、你的策略改动记录和给维护者的提案。memory_system 已经加载，它的说明里有你的上下文怎么来、你能改什么。

1. 先评估：changes 里还没有评估结论的改动，对照它之后的日志，问题少了吗？用 assess_change 写下结论；没用的就用 edit_policy 改回去。
2. 再找失败：从带 ⚠ 的行和被人指出忘了的地方出发，需要时用 read_log、read_actions、read_journal 看原文，弄清是没记下、记下了没想起来、还是放下太早。
3. 一次最多改一两处：edit_policy 改给图书管理员的附加说明或你的做法（global），写明原因和预期；醒来时带什么、阈值或代码要改，写进 proposals 交给维护者。
4. 没发现值得改的就什么都不改，直接结束。"""


def _tail(text: str, lines: int) -> str:
    return "\n".join(text.strip().split("\n")[-lines:]) if text.strip() else ""


def reflect(model: str | None = None) -> bool:
    """The nightly look back over how memory ran.  False means the agent was busy; try later.

    WHY: 维护者 2026-10-22：她要像人调试她那样调试自己，每晚观察记忆系统运行得怎么样。
    先让图书管理员把这一天收成日记、整理笔记，再准备反思材料并给她 memory_system；
    看什么、改不改由她决定。占住 AGENT_WINDOW，期间来的召唤照常登记，结束后由
    pick_up_pending 接上。
    """
    turn, owner = context.begin_turn(_chat_root.AGENT_WINDOW)
    if not owner:
        return False
    _begin(turn, None, "反思")
    data = storage.get("", "agent")
    last = data.get("reflected_at")
    image_ledger = image.begin_conversation()
    try:
        _night(turn)
        material = _journal.since(last if isinstance(last, (int, float)) else None, _REFLECT_CHARS)
        if not material.strip():
            turn.log["lines"].append("上次反思以来没有新的记录，跳过")
        else:
            context.set_current(None)
            context.set_agent_mode(True)
            try:
                _run_reflection(model, turn, material)
            finally:
                context.set_agent_mode(False)
        data["reflected_at"] = turn.log["start"]
        storage.save()
    finally:
        image.end_conversation(image_ledger)
        context.end_turn(_chat_root.AGENT_WINDOW, turn)
        _write_journal(turn, turn.cancelled)
    pick_up_pending()
    return True


def diary_day(moment: float) -> str:
    """The day a nightly run writes about: before noon it is still yesterday's night."""
    return time.strftime("%Y-%m-%d", time.localtime(moment - 12 * 3600))


def _night(turn) -> None:
    """The librarian's nightly pass: the day's diary, notes, and month/year roll-ups."""
    day = diary_day(turn.log["start"])
    started = time.time()
    try:
        _roles.organize({**_job(turn, "night"), "day": day,
                         "journal": _journal.since(tidied_at() - 1, 12000)})
    except Exception:
        _roles._stream.exception("夜间整理失败")
        turn.log["warnings"].append("⚠ 夜间整理失败，见终端日志")
        return
    storage.get("", "agent")[_TIDIED_KEY] = started
    storage.save()
    turn.log["lines"].append(f"夜间整理：日记 diary:{day}")
    # 一个月、一年的最后一夜各汇总一次；汇总只读日记，原文仍在 chatlog 里。
    following = time.localtime(time.mktime(time.strptime(day, "%Y-%m-%d")) + 36 * 3600)
    periods = []
    if following.tm_mday == 1:
        periods.append(day[:7])
        if following.tm_mon == 1:
            periods.append(day[:4])
    for period in periods:
        try:
            _roles.summarize(period)
            turn.log["lines"].append(f"汇总：diary:{period}")
        except Exception:
            _roles._stream.exception(f"汇总 {period} 失败")
            turn.log["warnings"].append(f"⚠ 汇总 diary:{period} 失败")


def _run_reflection(model: str | None, turn, material: str) -> None:
    _max_events, max_tokens = _chat_root.limit()
    notes = [text for scope in ("global", "recent") if (text := _notes.render(scope))]
    changes = _tail(_notes.read_policy("changes"), 40)
    proposals = _tail(_notes.read_policy("proposals"), 20)
    messages = [
        {"role": "user", "content": "以下是你的笔记：\n\n" + ("\n\n".join(notes) or "（还没有）")},
        {"role": "user", "content": "## 上次反思以来的记忆系统日志\n" + material},
        {"role": "user", "content": "## 策略改动记录（changes，最近的）\n" + (changes or "（还没有）")
         + "\n\n## 给维护者的提案（proposals，最近的）\n" + (proposals or "（还没有）")},
        {"role": "user", "content": _REFLECT_PROMPT.format(now=_screen.clock())},
    ]
    _run_session(model, turn, messages, lambda: None, lambda used, _initial: used > max_tokens,
                 assembly="组装（反思）", summary=[f"日志 {len(material)} 字", f"改动记录 {len(changes)} 字"],
                 load=("memory_system",))


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
