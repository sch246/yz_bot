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


def _addressed(event: dict, value: str) -> bool:
    """这条消息是冲着 Bot 说的吗：at、`<名字>，`、或者 `柚子，`。"""
    return (_view.has_at(identity.bot_id())(event)
            or value.startswith(f"{identity.bot_name()}，")
            or value.startswith("柚子，"))



def parse_target(target: str) -> tuple[str, int]:
    """Accept only an explicit QQ group or private-peer destination."""
    matched = re.fullmatch(r"([gu])([1-9][0-9]*)", str(target).strip())
    if matched is None:
        raise ValueError("目标必须是 g<群号> 或 u<私聊对端号>")
    return ("group" if matched[1] == "g" else "private"), int(matched[2])


def _unread_detail_text(detail: dict, *, include_wakes: bool = True) -> str:
    window = detail["window"]
    target = ("g" if window[0] == "group" else "u") + str(window[1])
    sources = ""
    if include_wakes:
        # WHY: notification 会持久化当时的 unread 快照；旧快照没有 ordinal，
        # 而且重建历史通知时本就不展示这份已过期的唤醒位置。
        sources = ", ".join(
            f"{item['kind']}"
            + (f" 作者={item['user_id']}" if item.get("user_id") is not None else "")
            + f" 时间={item['time']}"
            + (f" 未读序号={item['ordinal']}" if item.get("ordinal") is not None else "")
            for item in detail["wake_sources"])
    recovery = detail.get("recovery")
    extra = ((f" 补回未读={recovery['remaining']} 补回状态={recovery['state']}"
              + (f" 缺口={recovery['gap']}" if recovery.get("gap") else ""))
             if recovery else "")
    return (f"{target} 未读={detail['unread']} 普通={detail['ordinary']} "
            f"@/提及={detail['mentions']} 其他唤醒={detail['other_wakes']}"
            + (f" 最近唤醒：{sources}" if include_wakes and sources else "") + extra)


def _activation_text(item: dict) -> str:
    window = item["window"]
    target = ("g" if window[0] == "group" else "u") + str(window[1])
    return f"{target} {item['kind']} 作者={item.get('user_id')} 时间={item.get('time')}"


def unread_details() -> list[dict]:
    details = {tuple(item["window"]): item for item in oplog.pending_details()
               if item["window"][0] in ("group", "private")}
    all_sources = oplog.sources()
    boot_sources = {tuple(source["window"]): source for source in all_sources
                    if source["source_type"] == "napcat_boot"}
    boot_remaining: dict[tuple, tuple[int, int]] = {}
    for source in all_sources:
        if source["source_type"] == "napcat_boot":
            window = tuple(source["window"])
            remaining, mentions = boot_remaining.get(window, (0, 0))
            boot_remaining[window] = (remaining + source["remaining"],
                                      mentions + source["mention_count"] - source["read_mention_count"])
    for window, source in boot_sources.items():
        if window[0] not in ("group", "private"):
            continue
        remaining, mentions = boot_remaining[window]
        if not remaining and source["state"] == "complete":
            continue
        detail = details.setdefault(window, {"window": list(window), "unread": 0,
                                             "ordinary": 0, "mentions": 0,
                                             "other_wakes": 0, "wake_sources": []})
        recovery = detail.setdefault("recovery", {"remaining": 0, "state": "complete", "gap": None})
        recovery["remaining"] = remaining
        detail["mentions"] += mentions
        if source["state"] == "fetching":
            recovery["state"] = "fetching"
        elif source["state"] != "complete" and recovery["state"] != "fetching":
            recovery["state"] = source["state"]
        if source["gap"]:
            recovery["gap"] = source["gap"]
    for window, detail in details.items():
        if detail.get("recovery", {}).get("state") == "fetching":
            detail["wake_sources"] = []
            continue
        target = ("g" if window[0] == "group" else "u") + str(window[1])
        pending = {entry["arrival"]: entry for entry in oplog.unread(window)}
        wakes = []
        for ordinal, (member, _event) in enumerate(_iter_unread_metadata(target), 1):
            live = pending.get(member.get("arrival"))
            kind = (live.get("activation_kind", "wake") if live and live.get("activated")
                    else "mention" if member.get("mentioned") else None)
            if kind:
                wakes.append({"kind": kind, "user_id": (live["event"].get("user_id")
                              if live else member.get("user_id")),
                              "time": (live["event"].get("time") if live else member.get("time")),
                              "ordinal": ordinal})
        detail["wake_sources"] = wakes
    return list(details.values())


def _drain_legacy_results(mail: context.Mailbox) -> list[tuple[dict, dict]]:
    # WHY: f4d3591 left completed tool batches in the agent mailbox. Drain only
    # those existing arrivals on the next activation; new code writes R directly.
    # Delete this bridge after deployment confirms no old pending arrivals remain.
    if not mail.unread():
        return []

    def project(entries: list[context.MailEntry]) -> list[tuple[dict, dict]]:
        rows = []
        for entry in entries:
            values = entry.event["_stream_results"]
            recorded = oplog.read(entry.arrival) or oplog.result(
                _chat_root.AGENT_WINDOW, values["source"], values["returns"], entry.arrival)
            rows.append((recorded, _view._result_projection(recorded, oplog.say_links(_chat_root.AGENT_WINDOW))))
        return rows

    return mail.pull(len(mail.unread()), project)


_boot_sources: list[str] = []


def prepare_recovery_sources() -> None:
    """Freeze archive anchors and unread insertion boundaries before live ingress opens."""
    from mods import chatlog

    if _boot_sources:
        return
    for window, anchor in chatlog.freeze_boot_anchors().items():
        anchor_time = None
        try:
            latest = chatlog.read_range(*window, limit=1) if anchor is not None else []
            anchor_time = (latest[0].get("time") if latest and
                           str(latest[0].get("message_id")) == str(anchor) and
                           type(latest[0].get("time")) is int else None)
        except Exception:
            traceback.print_exc()
        try:
            source = oplog.start_source("NapCat " + str(window), window, "napcat_boot",
                                        anchor=anchor, anchor_time=anchor_time)
            _boot_sources.append(source["key"])
        except Exception:
            traceback.print_exc()


def _recovery_sources(window: tuple) -> list[dict]:
    return [source for source in oplog.sources()
            if tuple(source["queue_window"]) == window
            and source["source_type"] == "napcat_boot"]


def fetch_remote_source(window: tuple, source_key: str | None = None) -> dict:
    """Extend an unread gap or open a separate historical source."""
    from mods import _backfill, connect

    candidates = [source for source in oplog.sources()
                  if tuple(source["window"]) == window
                  and source["source_type"] in ("napcat_boot", "napcat_history")]
    selected = (next((source for source in candidates if source["key"] == source_key), None)
                if source_key is not None else (candidates[-1] if candidates else None))
    if source_key is not None and selected is None:
        raise ValueError("信源不属于该窗口或不能从 NapCat 扩展")
    if selected is not None and selected["state"] == "fetching":
        return selected
    if selected is not None and selected["state"] in ("gap", "failed") and not selected["pulled"]:
        source = oplog.reopen_source(selected["key"], fetch_anchor=selected["fetch_anchor"])
    else:
        # WHY: 显式对一个旧 key 再 fetch 就从那个 key 的旧端新开信源，不偷偷改用同窗口
        # 最新 key，也不跨信源去重。两个信源即使含有重叠原文，也是两次可由 agent 选择的
        # 重放经历；隐藏重叠会让“这个信源实际保存了什么”失真。
        target = ("g" if window[0] == "group" else "u") + str(window[1])
        source = oplog.start_source("NapCat 历史 " + target, window, "napcat_history",
                                    queue_window="new",
                                    start_seq=selected["stop_cursor"] if selected else None)

    def fetch() -> None:
        try:
            _backfill.recover_source(source, connect.call_api, manual=True)
        except Exception:
            traceback.print_exc()
            try:
                state = oplog.resolve_source(source["key"])
                oplog.finish_source(source["key"], gap="本地归档或入列失败；可重试",
                                    stop_cursor=state["cursor"])
            except Exception:
                traceback.print_exc()

    threading.Thread(target=fetch, name="napcat-source-fetch", daemon=True).start()
    return source


def _take_source_events(window: tuple, source: dict, session: llm.Chat | None,
                        positions: list[tuple[int, int]], budget: int = _chat_root.MAIL_PULL_TOKENS,
                        *, read_by: str | None = None, read_via: str | None = None,
                        bridge: dict | None = None) -> tuple[list[dict], str | None]:
    from mods import chatlog

    output = []
    used = 0
    pages = {}
    for page_number, offset in positions:
        if oplog.source_position_read(source["key"], page_number, offset):
            continue
        members = pages.setdefault(page_number, _source_pages.read_page(
            oplog.source_page_root(), source["key"], page_number))
        member = members[offset]
        origin = member["origin"]
        event = chatlog.read_origin(*window, origin)
        if event is None:
            return output, f"补回档案位置已丢失：{origin}"
        event["_history_source"] = "napcat_backfill"
        event["_history_seq"] = member.get("message_seq")
        original = _view._model_event(event, window[0] == "group")
        converted = _view._read_projection(original, read_by, read_via)
        if converted is not None and bridge:
            converted = _with_bridge(converted, bridge)
        amount = _view._message_cost(converted) if converted is not None else 0
        if used + amount > budget - 300:
            if bridge:
                return output, "来源桥超过本次输入预算；未消费后续成员"
            if output:
                break
            if converted is None:
                return [], "补回记录无法投影"
            excerpt = _chat_root.bounded_excerpt(json.dumps(original["content"], ensure_ascii=False),
                                      max(100, budget - 600))
            target = ("g" if window[0] == "group" else "u") + str(window[1])
            converted = {"role": "user", "content":
                         f"来源窗口={window} origin={origin}；补回消息过长，先读片段：{excerpt}；"
                         f"完整正文可用 read_messages(window={target!r}, origin={origin!r}, "
                         "before=0, after=0) 查阅"}
            converted = _view._read_projection(converted, read_by, read_via)
            if _view._message_cost(converted) > budget - 100:
                return output, "补回记录超过本次输入预算；未消费"
        recorded = context.mailbox(window).commit_recovered(
            member["message_id"], member["time"],
            lambda arrival: oplog.input_source(_chat_root.AGENT_WINDOW, event, converted, source["key"],
                                               page_number, offset, origin, window, arrival=arrival,
                                               mentioned=member.get("mentioned", False),
                                               read_by=read_by, read_via=read_via),
            event_seq=member.get("message_seq"))
        if converted is not None:
            projected = _view._echo_relation(_view._numbered(converted, recorded["id"]), recorded,
                                       oplog.say_links(_chat_root.AGENT_WINDOW))
            output.append(projected)
            if session is not None:
                _agent._remember_stream(session, projected, recorded["id"])
        used += _view._message_cost(converted) if converted is not None else 0
    return output, None


def _source_unread_members(source: dict, count: int | None = None):
    from mods import _source_pages

    yielded = 0
    for page_number in range(source["pages"] - 1, -1, -1):
        members = _source_pages.read_page(oplog.source_page_root(), source["key"], page_number)
        for offset, member in enumerate(members):
            if oplog.source_position_read(source["key"], page_number, offset):
                continue
            yield page_number, offset, member
            yielded += 1
            if count is not None and yielded >= count:
                return


def _all_source_members(source: str):
    """Yield the source's original sequence, including consumed positions."""
    try:
        window = parse_target(source)
    except ValueError:
        state = oplog.resolve_source(source)
        if state is None or state["key"] != source or state["source_type"] == "napcat_boot":
            raise ValueError("找不到该窗口或信源 key")
        for page in range(state["pages"] - 1, -1, -1):
            for offset, member in enumerate(_source_pages.read_page(
                    oplog.source_page_root(), state["key"], page)):
                yield _source_member(state, tuple(state["window"]), page, offset, member)
        return
    arrivals = oplog.arrival_members(window)
    cursor = 0
    for state in _recovery_sources(window):
        boundary = state["arrival_boundary"]
        while cursor < len(arrivals) and arrivals[cursor]["order"] < boundary:
            yield {**arrivals[cursor], "key": arrivals[cursor]["arrival"]}
            cursor += 1
        for page in range(state["pages"] - 1, -1, -1):
            for offset, member in enumerate(_source_pages.read_page(
                    oplog.source_page_root(), state["key"], page)):
                yield _source_member(state, window, page, offset, member)
    for member in arrivals[cursor:]:
        yield {**member, "key": member["arrival"]}


def _bridge_member(member: dict) -> dict | None:
    if member.get("source"):
        return oplog.source_member_provenance(member["source"], member["page"], member["offset"])
    return oplog.arrival_member_provenance(member["arrival"])


def _source_bridge_plan(source: str, before_key: str, selected: list[dict]) -> dict[str, dict | None]:
    """Scan source order once for this provider batch; retain only fold endpoints."""
    keys = {member["key"] for member in selected}
    if len(keys) == 1 and not before_key:
        return {next(iter(keys)): None}
    plan = {}
    crossed = {"count": 0, "shown": []}
    active = not before_key
    first = True
    for member in _all_source_members(source):
        key = member["key"]
        if key == before_key:
            active = True
            continue
        if key in keys:
            plan[key] = crossed if active and (before_key or not first) and crossed["count"] else None
            crossed = {"count": 0, "shown": []}
            active = True
            first = False
            if len(plan) == len(keys):
                break
        elif active and (before_key or not first) and _bridge_member(member) is not None:
            crossed["count"] += 1
            if crossed["count"] <= 5:
                crossed["shown"].append(member)
            elif crossed["count"] == 6:
                crossed["shown"] = [crossed["shown"][0], member]
            else:
                crossed["shown"][-1] = member
    return plan


def _bridge_projection(member: dict) -> dict:
    from mods import chatlog

    provenance = _bridge_member(member)
    if provenance["kind"] == "read":
        recorded = provenance["input"]
        converted = recorded.get("projection")
        excerpt = (converted is not None and isinstance(converted["content"], str)
                   and ("正式读取片段" in converted["content"]
                        or "补回消息过长，先读片段" in converted["content"]))
        if converted is not None and not excerpt:
            content = converted["content"]
            if (isinstance(content, list) and content
                    and content[0].get("text") == "<source_bridge>\n"):
                end = next((index for index, part in enumerate(content)
                            if part.get("text") == "</source_bridge>\n"), None)
                if end is not None:
                    converted = {**converted, "content": content[end + 1:]}
            return _view._numbered(converted, recorded["id"])
        event = recorded["event"]
    else:
        event = (chatlog.read_origin(*member["window"], member["origin"])
                 if member.get("origin") else oplog.arrival_event(member["arrival"]))
    if event is None:
        raise ValueError(f"桥成员原文已丢失：{member['key']}")
    event = {**event, "_bridge_original": True}
    if member.get("source"):
        event["_history_source"] = "napcat_backfill"
        event["_history_seq"] = member.get("message_seq")
    else:
        event["_live"] = True
        event["_log_origin"] = member.get("origin")
    converted = _view._model_event(event, member["window"][0] == "group")
    if converted is None:
        raise ValueError(f"桥成员无法投影：{member['key']}")
    if provenance["kind"] == "skipped":
        return _view._provenance_projection(converted, provenance["by"] or "unknown",
                                      "mark_read", skipped=True)
    recorded = provenance["input"]
    return _view._numbered(_view._read_projection(converted, recorded.get("read_by"), recorded.get("read_via")),
                     provenance["input"]["id"])


def _with_bridge(converted: dict, bridge: dict | None) -> dict:
    if not bridge:
        return converted
    shown = bridge["shown"]
    parts = [{"type": "text", "text": "<source_bridge>\n"}]
    for index, member in enumerate(shown):
        if index and bridge["count"] > 5:
            parts.append({"type": "text", "text":
                          f"已折叠 {bridge['count'] - 2} 条已读/跳过消息\n"})
        parts.append({"type": "text", "text": f"<source_member key={member['key']!r}>\n"})
        original = _bridge_projection(member)["content"]
        parts.extend(original if isinstance(original, list) else [{"type": "text", "text": original}])
        parts.append({"type": "text", "text": "\n</source_member>\n"})
    parts.append({"type": "text", "text": "</source_bridge>\n"})
    content = converted["content"]
    return {**converted, "content": [*parts, *(content if isinstance(content, list)
                                            else [{"type": "text", "text": content}])]}


def _iter_unread_metadata(source: str):
    """Yield stable unread descriptions without opening archive message bodies."""
    try:
        window = parse_target(source)
        sources = _recovery_sources(window)
        if any(item["state"] == "fetching" for item in sources):
            raise ValueError("该窗口离线补回仍在进行")
        pending = context.mailbox(window).unread()
        pending_meta = {item["arrival"]: item for item in oplog.unread(window)}
        start = 0
        for item in sources:
            boundary = item["pending_boundary"]
            end = (next((index for index in range(start, len(pending))
                         if not oplog.arrival_before_or_at(pending[index].arrival, boundary)),
                        len(pending)) if boundary is not None else start)
            for entry in pending[start:end]:
                yield _live_member(entry, window, pending_meta), entry.event
            for page, offset, member in _source_unread_members(item):
                yield _source_member(item, window, page, offset, member), None
            start = end
        for entry in pending[start:]:
            yield _live_member(entry, window, pending_meta), entry.event
    except ValueError:
        state = oplog.resolve_source(source)
        if state is None or state["key"] != source:
            raise
        if state["source_type"] == "napcat_boot":
            raise ValueError("启动补回属于原窗口信源")
        if state["state"] == "fetching":
            raise ValueError("该信源仍在拉取")
        window = tuple(state["window"])
        for page, offset, member in _source_unread_members(state):
            yield _source_member(state, window, page, offset, member), None


def unread_members(source: str, limit: int | None = None) -> list[dict]:
    """Return detached unread dicts; use limit to avoid materializing a large source."""
    from copy import deepcopy
    from mods import chatlog

    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("limit 必须是非负整数或 None")
    result = []
    for member, live_event in _iter_unread_metadata(source):
        if limit is not None and len(result) >= limit:
            break
        event = (live_event if live_event is not None else
                 chatlog.read_origin(*member["window"], member["origin"]))
        if event is None:
            raise ValueError(f"信源档案位置已丢失：{member['origin']}")
        result.append({**member, "event": deepcopy(event)})
    return result


def _live_member(entry, window, pending_meta) -> dict:
    pending = pending_meta.get(entry.arrival, {})
    event = entry.event
    return {"key": entry.arrival, "arrival": entry.arrival,
            "window": list(window), "origin": pending.get("origin"),
            "message_id": event.get("message_id"), "mentioned":
            pending.get("activation_kind") == "mention"}


def _source_member(source, window, page, offset, member) -> dict:
    return {"key": f"{source['key']}:{page}:{offset}", "source": source["key"],
            "page": page, "offset": offset, "window": list(window),
            "origin": member["origin"], "message_id": member["message_id"],
            "user_id": member.get("user_id"), "time": member.get("time"),
            "message_seq": member.get("message_seq"),
            "mentioned": bool(member.get("mentioned"))}


def _unread_member_by_key(source: str, key: str) -> dict | None:
    """Resolve one stable member without scanning or reading other message bodies."""
    try:
        window = parse_target(source)
    except ValueError:
        named_source = oplog.resolve_source(source)
        if named_source is None or named_source["key"] != source:
            raise ValueError("找不到该窗口或信源 key")
        if named_source["source_type"] == "napcat_boot":
            raise ValueError("启动补回属于原窗口信源")
        window = tuple(named_source["window"])
    else:
        named_source = None
        if any(item["state"] == "fetching" for item in _recovery_sources(window)):
            raise ValueError("该窗口离线补回仍在进行")
    if ":" in key:
        source_key, page_text, offset_text = key.rsplit(":", 2)
        if not page_text.isdecimal() or not offset_text.isdecimal():
            return None
        state = oplog.resolve_source(source_key)
        page, offset = int(page_text), int(offset_text)
        if (state is None or state["state"] == "fetching"
                or tuple(state["window"]) != window
                or (named_source is None and state["source_type"] != "napcat_boot")
                or (named_source is not None and source_key != named_source["key"])
                or page >= state["pages"] or offset >= state["page_counts"][page]
                or oplog.source_position_read(source_key, page, offset)):
            return None
        member = _source_pages.read_page(oplog.source_page_root(), source_key, page)[offset]
        return _source_member(state, window, page, offset, member)
    if named_source is not None:
        return None
    pending = next((item for item in oplog.unread(window) if item["arrival"] == key), None)
    if pending is None:
        return None
    event = pending["event"]
    return {"key": key, "arrival": key, "window": list(window),
            "origin": pending.get("origin"), "message_id": event.get("message_id"),
            "mentioned": pending.get("activation_kind") == "mention"}


def _mail_message_identity(message_id, event_time, message_seq) -> tuple[str, int, str | None] | None:
    if message_id is None or type(event_time) is not int:
        return None
    value = str(message_id)
    value = str(int(value)) if value.lstrip("-").isdecimal() else value
    sequence = None if message_seq is None else str(message_seq)
    if sequence is not None and sequence.lstrip("-").isdecimal():
        sequence = str(int(sequence))
    return value, event_time, sequence


def _mark_source_events_read(window: tuple, source: dict, count: int,
                             read_by: str | None = None) -> tuple[int, int, int]:
    pending = {}
    for entry in oplog.unread(window):
        event = entry["event"]
        identity = _mail_message_identity(event.get("message_id"), event.get("time"),
                                          event.get("message_seq"))
        if identity is not None:
            pending[identity] = entry["arrival"]
    arrivals = []
    positions = []
    selected = mention_count = 0
    for page, offset, member in _source_unread_members(source, count):
        positions.append((page, offset))
        selected += 1
        mention_count += bool(member.get("mentioned"))
        identity = _mail_message_identity(member.get("message_id"), member.get("time"),
                                          member.get("message_seq"))
        arrival = pending.get(identity)
        if arrival is not None and arrival not in arrivals:
            arrivals.append(arrival)
    if not selected:
        return 0, 0, source["remaining"]
    state = context.mailbox(window).absorb(
        arrivals,
        lambda: oplog.mark_source_read(source["key"], positions, mention_count, arrivals, read_by),
    )
    return selected, mention_count, state["remaining"]


def _mark_mail_prefix_read(window: tuple, entries: list[context.MailEntry],
                           read_by: str | None = None) -> tuple[int, int]:
    if not entries:
        return 0, 0
    arrivals = [entry.arrival for entry in entries]
    pending = {entry["arrival"]: entry for entry in oplog.unread(window)}
    mentions = sum(pending[arrival].get("activation_kind") == "mention"
                   for arrival in arrivals)

    def commit(crossed: list[context.MailEntry]) -> int:
        if [entry.arrival for entry in crossed] != arrivals:
            raise ValueError("待读队首已变化；未标为已读")
        return oplog.mark_arrivals_read(window, arrivals, read_by)

    marked = context.mailbox(window).pull(len(entries), commit)
    return marked, mentions


def mark_window_read(window: tuple, count: int | None = None,
                     read_by: str | None = None) -> dict:
    """Mark the current ordered unread prefix as read without creating input events."""
    sources = _recovery_sources(window)
    if any(source["state"] == "fetching" for source in sources):
        raise ValueError("该窗口离线补回仍在进行，未读前端尚未固定")
    box = context.mailbox(window)
    through = oplog.latest_pending_arrival(window)
    frozen_sources = {source["key"]: source["remaining"] for source in sources}
    budget = count
    marked = mentions = 0

    def allowance(available: int) -> int:
        return available if budget is None else min(available, budget - marked)

    for original in sources:
        if budget is not None and marked >= budget:
            break
        prefix = box.unread_through(original["pending_boundary"], allowance(len(box.unread())))
        removed, mentioned = _mark_mail_prefix_read(window, prefix, read_by)
        marked += removed
        mentions += mentioned
        if budget is not None and marked >= budget:
            break
        state = oplog.resolve_source(original["key"])
        available = min(state["remaining"], frozen_sources[original["key"]])
        amount = allowance(available)
        if amount:
            removed, mentioned, _remaining = _mark_source_events_read(window, state, amount, read_by)
            marked += removed
            mentions += mentioned
    if budget is None or marked < budget:
        entries = box.unread()
        if through is not None:
            entries = [entry for entry in entries
                       if oplog.arrival_before_or_at(entry.arrival, through)]
        entries = entries[:allowance(len(entries))]
        removed, mentioned = _mark_mail_prefix_read(window, entries, read_by)
        marked += removed
        mentions += mentioned
    return {"marked_read": marked, "mentions": mentions,
            "remaining": len(box) + sum(oplog.resolve_source(source["key"])["remaining"]
                                         for source in sources)}


def mark_source_read(source: dict, count: int | None = None,
                     read_by: str | None = None) -> dict:
    if source["state"] == "fetching":
        raise ValueError("该信源仍在拉取，未读前端尚未固定")
    amount = source["remaining"] if count is None else min(count, source["remaining"])
    marked, mentions, remaining = _mark_source_events_read(tuple(source["window"]), source,
                                                           amount, read_by)
    return {"marked_read": marked, "mentions": mentions, "remaining": remaining}


def _take_members(session: llm.Chat, request: dict) -> tuple[list[dict], str | None]:
    """Commit selected members one by one before projecting the next request."""
    selected = request["members"]
    output = []
    used = 0
    bridge_plan = _source_bridge_plan(request["source"], request.get("last_key", ""), selected)
    while selected:
        if used >= _chat_root.MAIL_PULL_TOKENS - 400:
            break
        member = selected[0]
        window = tuple(member["window"])
        bridge = bridge_plan.get(member["key"])
        if "source" in member:
            source = oplog.resolve_source(member["source"])
            if source is None or oplog.source_position_read(
                    member["source"], member["page"], member["offset"]):
                selected.pop(0)
                if selected:
                    bridge_plan = _source_bridge_plan(request["source"],
                                                      request.get("last_key", ""), selected)
                continue
            projected, error = _take_source_events(
                window, source, session, [(member["page"], member["offset"])],
                _chat_root.MAIL_PULL_TOKENS - used, read_by=request.get("read_by"),
                read_via=request.get("read_via"), bridge=bridge)
            if error:
                return output, error
            output.extend(projected)
            used += sum(_view._message_cost(item) for item in projected)
            if projected:
                request["last_key"] = member["key"]
            selected.pop(0)
            continue
        arrival = member["arrival"]
        box = context.mailbox(window)
        entry = next((item for item in box.unread() if item.arrival == arrival), None)
        if entry is None:
            selected.pop(0)
            if selected:
                bridge_plan = _source_bridge_plan(request["source"],
                                                  request.get("last_key", ""), selected)
            continue
        located = {**entry.event, "_log_origin": oplog.arrival_origin(arrival), "_live": True}
        original = _view._model_event(located, window[0] == "group")
        converted = _view._read_projection(original, request.get("read_by"), request.get("read_via"))
        if converted is not None:
            converted = _with_bridge(converted, bridge)
        amount = _view._message_cost(converted) if converted is not None else 0
        if converted is not None and used + amount > _chat_root.MAIL_PULL_TOKENS - 300:
            if bridge:
                return output, "来源桥超过本次输入预算；未消费后续成员"
            if output:
                break
            excerpt = _chat_root.bounded_excerpt(json.dumps(original["content"], ensure_ascii=False),
                                      _chat_root.MAIL_PULL_TOKENS - 600)
            converted = {"role": "user", "content":
                         f"来源窗口={window} origin={member.get('origin')} arrival={arrival}；"
                         f"消息过长，正式读取片段：{excerpt}；完整正文可用 read_messages 按 origin 再读"}
            converted = _view._read_projection(converted, request.get("read_by"),
                                         request.get("read_via"))
            if _view._message_cost(converted) > _chat_root.MAIL_PULL_TOKENS - 100:
                return output, "消息过长且无法投影；未消费"
            amount = _view._message_cost(converted)
        # WHY: I is durable before hiding this exact arrival. A provider failure
        # after request assembly can therefore leave it read but unprocessed;
        # exactly-once delivery is not promised. Never replace a whole mailbox
        # snapshot here: a concurrent arrival must remain unread.
        recorded = box.absorb([arrival], lambda: oplog.input(
            _chat_root.AGENT_WINDOW, entry.event, converted, arrival, source_window=window,
            read_by=request.get("read_by"), read_via=request.get("read_via")))
        if converted is not None:
            projection = _view._echo_relation(_view._numbered(converted, recorded["id"]), recorded,
                                        oplog.say_links(_chat_root.AGENT_WINDOW))
            output.append(projection)
            _agent._remember_stream(session, projection, recorded["id"])
        used += amount
        request["last_key"] = member["key"]
        selected.pop(0)
    return output, None


def _take_archive(session: llm.Chat, request: dict, window: tuple) -> tuple[list[dict], str | None]:
    records = request["records"]
    output = []
    used = 0
    while records:
        if used >= _chat_root.MAIL_PULL_TOKENS - 400:
            break
        record = records[0]
        origin = record.get("_log_origin")
        if not origin:
            return output, "档案记录缺少稳定 origin"
        event = {**record, "_history_source": "archive", "_live": False}
        original = _view._model_event(event, window[0] == "group")
        converted = _view._read_projection(original, request.get("read_by"), request.get("read_via"))
        if converted is None:
            records.pop(0)
            continue
        amount = _view._message_cost(converted)
        if used + amount > _chat_root.MAIL_PULL_TOKENS - 300:
            if output:
                break
            excerpt = _chat_root.bounded_excerpt(json.dumps(original["content"], ensure_ascii=False),
                                      _chat_root.MAIL_PULL_TOKENS - 600)
            converted = {"role": "user", "content":
                         f"来源窗口={window} origin={origin}；档案消息过长，正式读取片段：{excerpt}"}
            converted = _view._read_projection(converted, request.get("read_by"),
                                         request.get("read_via"))
            amount = _view._message_cost(converted)
            if amount > _chat_root.MAIL_PULL_TOKENS - 100:
                return output, "档案记录过长且无法投影；未消费"
        arrival = next((item["arrival"] for item in oplog.unread(window)
                        if item.get("origin") == origin), None)
        if arrival is None and record.get("message_id") is not None and type(record.get("time")) is int:
            arrival = oplog.pending_message(window, record["message_id"], record["time"],
                                            record.get("message_seq"))
        source_member = next((item for source in oplog.sources()
                              if tuple(source["window"]) == window and source["state"] != "fetching"
                              for page, offset, member in _source_unread_members(source)
                              if member["origin"] == origin
                              for item in [(source["key"], page, offset,
                                            bool(member.get("mentioned")))]), None)
        values = ({"source": source_member[0], "page": source_member[1],
                   "offset": source_member[2], "mentioned": source_member[3]}
                  if source_member is not None else {})
        commit = lambda: oplog.input_archive(_chat_root.AGENT_WINDOW, record, converted, origin,
                                             window, arrival=arrival,
                                             read_by=request.get("read_by"),
                                             read_via=request.get("read_via"), **values)
        recorded = (context.mailbox(window).absorb([arrival], commit)
                    if arrival is not None else commit())
        projection = _view._numbered(converted, recorded["id"])
        output.append(projection)
        _agent._remember_stream(session, projection, recorded["id"])
        used += amount
        records.pop(0)
    return output, None
