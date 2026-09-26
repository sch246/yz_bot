from __future__ import annotations

import ast
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import re
import threading
import time
import traceback
from typing import Callable

from mods import _source_pages, context, cq, history, identity, image, llm, log, message, msgs, op, oplog, py, storage, text, thread, tools as tool_modules
from mods.command import command
from mods.capture import capture
from mods.llm import pricing

import mods.chat as _chat_root
from . import view as _view, reader as _reader, agent as _agent, subcommands as _subcommands

def _stream_results(window, binding):
    def record(source: str, results: list[llm.ToolCallResult],
               native_results: list[dict]) -> None:
        for result in results:
            binding.touch(result.name)
        if window is None:
            return
        session = binding.session
        if not results:
            if window == _chat_root.AGENT_WINDOW and session.reads_window_mail and source in session.native_sources:
                found, missing = oplog.recall_events(window, [source])
                if missing:
                    raise RuntimeError("刚写入的模型输出无法反查")
                tag = _view._event_refs(found[0])
                session.messages.append(tag)
                _remember_stream(session, tag, source)
            return
        returns = [{"position": position, "name": result.name,
                    "arguments": result.arguments, "content": result.content,
                    "tool_call_id": result.tool_call_id}
                   for position, result in enumerate(results)]
        recorded = oplog.result(window, source, returns, "")
        if window == _chat_root.AGENT_WINDOW and binding.session.reads_window_mail:
            if source in session.native_sources and len(native_results) == len(results):
                for message, result in zip(native_results, returns):
                    _remember_stream(session, message, recorded["id"])
                    _credit_recall(session, result, message)
                found, missing = oplog.recall_events(window, [source])
                if missing:
                    raise RuntimeError("刚写入的模型输出无法反查")
                tag = _view._event_refs(found[0], recorded)
                session.messages.append(tag)
                _remember_stream(session, tag, source)
            else:
                projection = _view._result_projection(recorded, oplog.say_links(window))
                session.pending_results.append((recorded, projection))
    return record



def _trusted_stream_ids(session: llm.Chat) -> set[str]:
    _prune_stream_ids(session)
    return {event_id for message in session.messages
            if (event_id := _stream_id(session, message)) is not None}


def _remember_stream(session: llm.Chat, message: dict, event_id: str) -> None:
    session.stream_ids[id(message)] = (message, event_id)


def _stream_id(session: llm.Chat, message: dict) -> str | None:
    remembered = session.stream_ids.get(id(message))
    return remembered[1] if remembered is not None and remembered[0] is message else None


def _prune_stream_ids(session: llm.Chat) -> None:
    live = {id(message) for message in session.messages}
    # WHY: on_output 在 assistant 加入 messages 前登记其映射；同批工具中的
    # cover_events 需要看见当前 O，因此暂留 active_action 所属输出。
    active_source = str(getattr(session, "active_action", "") or "").partition("#")[0]
    session.stream_ids = {key: pair for key, pair in session.stream_ids.items()
                          if key in live or active_source and pair[1] == active_source}


def _cover_agent_projection(session: llm.Chat, members: set[str]) -> None:
    session.messages[:] = [message for message in session.messages
                          if _stream_id(session, message) not in members]
    _prune_stream_ids(session)


def _visible_stream_ids(messages: list[dict]) -> set[str]:
    visible = set()
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        first = content[0].get("text", "") if isinstance(content, list) and content and isinstance(content[0], dict) else content
        match = re.match(r"^\[(\d{8}-[1-9]\d*)\] ", first) if isinstance(first, str) else None
        if match:
            visible.add(match[1])
    return visible


def _pressure_hint(used_tokens: int, max_tokens: int, threshold: int) -> str:
    if used_tokens * 100 <= max_tokens * threshold:
        return ""
    return f"上下文占用 {used_tokens}/{max_tokens} token"


def _cover_projection(messages: list[dict], members: set[str]) -> None:
    messages[:] = [message for message in messages
                   if not (_visible_stream_ids([message]) & members)]


def _drive_agent(model: str | None, window: tuple) -> None:
    turn, owner = context.begin_turn(_chat_root.AGENT_WINDOW)
    if not owner:
        return
    turn._chat_usage_tokens = 0
    turn.requested_reads = []
    turn.associated_windows = {window}
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
            turn.associated_windows.intersection_update(
                {window for window, _count, active in oplog.pending_summary() if active}
                | {tuple(request["window"]) for request in turn.requested_reads})
            if not context.finish_turn(_chat_root.AGENT_WINDOW, turn,
                                       lambda: oplog.has_unnotified() or bool(turn.requested_reads)):
                return
    finally:
        image.end_conversation(image_ledger)
        context.end_turn(_chat_root.AGENT_WINDOW, turn)
        # WHY: 循环停下的唯一信号就在这里，见 _run_hint。
        if origin is not None:
            _chat_root._run_hint(window, turn)


def _run_agent(model: str | None, turn) -> bool:
    offline = _chat_root._offline_scope.get()
    if offline is not None and model != offline["model"]:
        raise RuntimeError("offline replay model changed")
    session = llm.Chat(model=model or _chat_root.get_model(), chat_client=llm.get_client())
    if offline is not None:
        session.fail_fast = True
    session.stream_ids = {}
    session.recalled_ids = set()
    session.pending_results = []
    session.native_sources = set()
    session.requested_reads = turn.requested_reads
    session.associated_windows = turn.associated_windows
    session.notification_ids = []
    max_events, max_tokens = _chat_root.limit()
    turn._chat_limit = (max_events, max_tokens)
    show_thought = _chat_root.get_reasoning_mode() == "keep"
    # WHY: 离线回放从空种子开始，正式读到的内容只能由模型 cover；若每次重建 Chat
    # 仍取近期后缀，实验会被滑窗偷偷救活，测到的就不是自主整理。生产路径继续使用两个
    # 上限，只有显式 offline scope 投影全部仍可见事件，直到模型整理或真实请求失败。
    rows, _used, _blocked = _view._stream_rows(
        _chat_root.AGENT_WINDOW,
        None if offline is not None else max_tokens,
        None if offline is not None else max_events,
        native_model=session.model if show_thought else None,
        show_thought=show_thought,
    )
    rows.extend(_reader._drain_legacy_results(turn.mail))
    notice = oplog.deliver_notifications(_chat_root.AGENT_WINDOW)
    if notice is not None:
        session.notification_ids.append(notice["id"])
        turn.associated_windows.update(map(tuple, notice["windows"]))
        # WHY: notice 是这一请求末尾交付的当前通知；失败重投时它也可能沿用较早的正式号。
        # 不能按事件号重排每条投影，那会拆开原生 assistant/tool 块：event_refs 与
        # assistant 共用 O 的编号，会被排到 R 对应的 tool 消息前面，供应商随即以配对
        # 不完整拒绝请求。保留已构造历史的块顺序，再把本次通知放到末尾。
        rows.append((notice, _view._notification_projection(notice)))
    messages = []
    for entry, projection in rows:
        messages.append(projection)
        _remember_stream(session, projection, entry["id"])
    if not messages and not turn.requested_reads and not turn.mail.unread():
        return True
    _chat_root._activate_chat(session, _view._close_with_user(messages), read_mail=True)
    for entry, projection in rows:
        if entry["kind"] == "result":
            for result in entry["returns"]:
                _credit_recall(session, result, projection)
    session.output_recorded = False

    def record_output(assistant, calls):
        recorded = _chat_root._record_output(_chat_root.AGENT_WINDOW, assistant, calls, session)
        for event_id in session.notification_ids:
            oplog.acknowledge_notification(event_id)
        session.notification_ids.clear()
        session.output_recorded = True
        return recorded

    session.on_output = record_output
    session.add_context_provider(_agent_provider(turn, session))
    session.add_hint(_view._pending_hint)
    session.add_hint(lambda: _pressure_hint(turn._chat_usage_tokens, _chat_root.limit()[1],
                                            _chat_root.window_setting("pressure_percent")))
    session.should_stop = lambda: turn.cancelled
    session.chat(recall_func=_chat_root.get_handler(session), description_cache=_chat_root.description_cache)
    return session.output_recorded


def _credit_recall(session: llm.Chat, result: dict, projection: dict) -> None:
    if result["name"] != "recall_events" or result["content"] not in projection["content"]:
        return
    try:
        payload = json.loads(result["content"])
        ids = payload.get("resolved_ids")
        if not isinstance(ids, list):
            return
        found, _missing = oplog.recall_events(_chat_root.AGENT_WINDOW, map(str, ids))
        session.recalled_ids.update(item["id"] for item in found)
    except (AttributeError, KeyError, TypeError, ValueError):
        return


def _agent_provider(turn, session: llm.Chat):
    def provide() -> list[dict]:
        try:
            # WHY: This is only a live projection of already durable R events. A
            # cancelled session drops it; later activations use ordinary history.
            pending = session.pending_results
            produced = [projection for _entry, projection in pending]
            for entry, projection in pending:
                _remember_stream(session, projection, entry["id"])
                for result in entry["returns"]:
                    _credit_recall(session, result, projection)
            session.pending_results = []
            notice = oplog.deliver_notifications(_chat_root.AGENT_WINDOW)
            new_notice = notice is not None and notice["id"] not in session.notification_ids
            if new_notice:
                session.notification_ids.append(notice["id"])
                turn.associated_windows.update(map(tuple, notice["windows"]))
                projection = _view._notification_projection(notice)
                produced.append(projection)
                _remember_stream(session, projection, notice["id"])
            # WHY: 已完成结果不会饿死显式 take；同一边界先交付结果与通知，
            # 再按登记顺序兑现至多一个 take。
            if turn.requested_reads:
                request = turn.requested_reads.pop(0)
                window = tuple(request["window"])
                turn.associated_windows.add(window)
                if "records" in request:
                    pulled, error = _reader._take_archive(session, request, window)
                else:
                    pulled, error = _reader._take_members(session, request)
                produced.extend(pulled)
                if error is None and (request.get("members") or request.get("records")):
                    turn.requested_reads.insert(0, request)
                if error:
                    if _chat_root._offline_scope.get() is not None:
                        raise llm.RequiredContextError(f"离线回放读取失败：{error}")
                    remaining = request.get("members") or request.get("records") or []
                    produced.append({"role": "user", "content":
                                     f"读取请求 read_by={request.get('read_by') or 'unknown'} "
                                     f"{window} 正式阅读部分失败："
                                     f"本次已兑现 {len(pulled)} 条，"
                                     f"尚未兑现 {len(remaining)} 条；{error}。"
                                     "原计划停止，未兑现成员仍未读；请重新调用 take 选择较窄范围，"
                                     "或用 read_messages 查档案。"})
            turn._chat_usage_tokens = sum(
                _view._message_cost(message) for message in [*session.messages, *produced]
                if _stream_id(session, message) is not None
            )
            turn._chat_event_count = len({
                event_id for message in [*session.messages, *produced]
                if (event_id := _stream_id(session, message)) is not None
            })
            return produced
        except Exception as error:
            raise llm.RequiredContextError("读取中心信息流失败，已停止后续行动") from error
    return provide
