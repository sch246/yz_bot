"""The central agent loop: every request is assembled afresh from durable state.

WHY: 2026-10-19 the maintainer replaced "replay the agent's own transcript from
a fixed start" with a per-activation rebuild (see
docs/working/proposals/stateless-agent-loop.md).  The oplog still records every
output and result; it is no longer shown back wholesale.  What the model sees is
chosen by the selector each time, so unrelated old detail drops out simply by
not being chosen again, and comes back when something makes it relevant.
"""

from __future__ import annotations

import json
import time

from mods import context, image, llm, oplog, storage

import mods.chat as _chat_root
from . import actions as _actions, notes as _notes, reader as _reader, roles as _roles, screen as _screen, view as _view

_SEEN_KEY = "seen"
# Within one activation, results older than these stay readable by id only.
_CURRENT_FULL = 12
_CURRENT_CHARS = 1500
# A window never seen before shows this much even when the selector asked only for new lines.
_FIRST_LOOK = 20
_LEAD_IN = 3


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


def _woken(notice: dict | None, turn) -> tuple[list[tuple], list[str]]:
    windows: list[tuple] = []
    lines: list[str] = []
    if notice is not None:
        for item in notice.get("activations", []):
            window = tuple(item["window"])
            windows.append(window)
            stamp = _screen.clock(item["time"]) if isinstance(item.get("time"), (int, float)) else "?"
            lines.append(f"{_screen.label(window)} {item.get('kind')} 作者={item.get('user_id')} 时间={stamp}")
        for window in notice.get("windows", []):
            windows.append(tuple(window))
    windows.extend(window for window in turn.associated_windows if window[0] in ("group", "private"))
    return list(dict.fromkeys(windows)), lines


def _current_actions(turn) -> list[str]:
    found = _actions.pairs(ids=turn.action_ids) if turn.action_ids else []
    older, recent = found[:-_CURRENT_FULL], found[-_CURRENT_FULL:]
    lines = [_actions.title(output) for output, _result in older]
    lines.extend(_actions.render(output, result, chars=_CURRENT_CHARS) for output, result in recent)
    return lines


def _assemble(plan: dict, turn) -> tuple[list[dict], dict[str, str], dict[tuple, int | None]]:
    """Build the request body from the selector's plan; nothing else is replayed."""
    seen = _seen()
    messages: list[dict] = []
    note_parts = [text for scope in dict.fromkeys(plan["notes"]) if (text := _notes.render(scope))]
    if note_parts:
        messages.append({"role": "user", "content": "以下是你的笔记（图书管理员整理，可能不完整）：\n\n"
                         + "\n\n".join(note_parts)})
    situation = []
    if plan.get("situation"):
        situation.append("情景说明（前置选材给出）：" + plan["situation"])
    if plan.get("actions"):
        situation.append("相关的过去行动：\n" + "\n".join(
            _actions.render(output, result) for output, result in _actions.pairs(ids=plan["actions"])))
    current = _current_actions(turn)
    if current:
        situation.append("本次唤醒中你已经做过的行动（较早的只列标题，可用 read_actions 读全文）：\n"
                         + "\n".join(current))
    if situation:
        messages.append({"role": "user", "content": "\n\n".join(situation)})
    screens: dict[str, str] = {}
    newest: dict[tuple, int | None] = {}
    for window, count in plan["windows"]:
        mark = seen.get(_screen.label(window))
        items = _screen.records(window, max(count, 1) + 200)
        fresh = [item for item in items if mark is not None and item["time"] is not None and item["time"] > mark]
        # WHY: 新消息全部展示，选材给的条数只决定还附多少旧消息；至少带上新消息之前的
        # 几条接上话头。看到时间只精确到秒，同一秒里后到的那条也靠这几条不丢。
        older = [item for item in items if item not in fresh][-max(count, _LEAD_IN):]
        shown = older + fresh if mark is not None else items[-(count or _FIRST_LOOK):]
        if not shown:
            continue
        text = _screen.render(window, shown, mark)
        screens[_screen.label(window)] = text
        newest[window] = _screen.latest_time(shown)
        messages.append({"role": "user", "content": _view.msg_split(text)})
    return messages, screens, newest


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


def _mark_seen(newest: dict[tuple, int | None]) -> None:
    seen = _seen()
    for window, stamp in newest.items():
        if stamp is not None:
            seen[_screen.label(window)] = max(stamp, seen.get(_screen.label(window), 0))
        try:
            _reader.mark_window_read(window)
        except ValueError:
            # 离线补回仍在进行：未读前端没有固定，下次再标。
            pass
    storage.save()


def _drive_agent(model: str | None, window: tuple) -> None:
    turn, owner = context.begin_turn(_chat_root.AGENT_WINDOW)
    if not owner:
        return
    turn._chat_usage_tokens = 0
    turn.requested_reads = []
    turn.associated_windows = {window}
    turn.action_ids = []
    turn.screens = {}
    turn.reselect = False
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
                    return
            finally:
                context.set_agent_mode(False)
                context.set_current(origin)
            if turn.cancelled:
                return
            if turn.reselect:
                # 同一次唤醒里上下文涨过阈值：不等新消息，按当前情况重新选材再继续。
                turn.reselect = False
                continue
            turn.associated_windows.intersection_update(
                {window for window, _count, active in oplog.pending_summary() if active})
            if not context.finish_turn(_chat_root.AGENT_WINDOW, turn, oplog.has_unnotified):
                return
    finally:
        image.end_conversation(image_ledger)
        context.end_turn(_chat_root.AGENT_WINDOW, turn)
        if not turn.cancelled and (turn.screens or turn.action_ids) and _chat_root._offline_scope.get() is None:
            _roles.enqueue({
                "time": time.time(),
                "screens": list(turn.screens.values()),
                "actions": [_actions.render(output, result) for output, result in _actions.pairs(ids=turn.action_ids)],
                "scopes": _roles.involved_scopes(
                    [_reader.parse_target(label) for label in turn.screens], list(turn.screens.values())),
            })
        # WHY: 循环停下的唯一信号就在这里，见 _run_hint。
        if origin is not None:
            _chat_root._run_hint(window, turn)


def _run_agent(model: str | None, turn) -> bool:
    if _chat_root._offline_scope.get() is not None:
        raise RuntimeError("离线回放尚未按无状态主循环重写")
    session = llm.Chat(model=model or _chat_root.get_model(), chat_client=llm.get_client())
    session.requested_reads = turn.requested_reads
    session.associated_windows = turn.associated_windows
    max_events, max_tokens = _chat_root.limit()
    turn._chat_limit = (max_events, max_tokens)
    notice = oplog.deliver_notifications(_chat_root.AGENT_WINDOW)
    woken, activation_lines = _woken(notice, turn)
    # 续做时没有新唤醒，默认沿用刚才看着的窗口；选材仍可以换掉它们。
    focus = woken or [_reader.parse_target(label) for label in turn.screens]
    if notice is None and not focus and not turn.action_ids:
        return True
    current = _current_actions(turn)
    plan = _roles.select(focus, activation_lines, current, max_events)
    messages, screens, newest = _assemble(plan, turn)
    if not messages and notice is None:
        return True
    if notice is not None:
        messages.append({"role": "user", "content": "这次唤醒你的是：\n" + "\n".join(activation_lines)})
    binding = _chat_root._activate_chat(session, messages)
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

    def record_output(assistant, calls):
        source = oplog.output(_chat_root.AGENT_WINDOW, assistant, calls)
        turn.action_ids.append(source)
        if not session.output_recorded:
            if notice is not None:
                oplog.acknowledge_notification(notice["id"])
            _mark_seen(newest)
            turn.screens.update(screens)
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

    def over_budget() -> bool:
        # WHY: 不是"到 N 轮就截断"（见 llm.chat 的 WHY）：截下来的行动已经写进 oplog，
        # 重新组装时最近的行动原文仍在，较早的留标题、可按号读回；前提没有被砍掉，只是收起了。
        # 只算这次唤醒里长出来的部分：开局材料本身就大时，重选也不会变小，不能因此空转。
        used = _text_tokens(session.messages)
        turn._chat_usage_tokens = used
        threshold = max_tokens * _chat_root.agent_setting("pressure_percent") // 100
        if used > threshold and used - initial > threshold // 3:
            turn.reselect = True
            return True
        return False

    session.on_output = record_output
    session.on_results = record_results
    session.turn_done = over_budget
    session.add_context_provider(tool_changes)
    session.add_hint(lambda: _unread_hint(set(newest)))
    session.add_hint(lambda: f"现在是 {_screen.clock()}。")
    session.should_stop = lambda: turn.cancelled
    session.chat(recall_func=_chat_root.get_handler(session), description_cache=_chat_root.description_cache)
    return session.output_recorded
