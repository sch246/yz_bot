from __future__ import annotations

import json
import re
import threading
import traceback
from typing import Callable

from mods import _source_pages, context, identity, llm, oplog

import mods.chat as _chat_root
from . import view as _view


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


def _pending_hint() -> str:
    rows = unread_details()
    all_sources = oplog.sources()
    latest = {tuple(source["window"]): source["key"] for source in all_sources
              if source["source_type"] in ("napcat_boot", "napcat_history")}
    independent = [source for source in all_sources
                   if source["source_type"] == "napcat_history"
                   and (source["remaining"] or source["state"] != "complete")
                   and (source["remaining"] or latest[tuple(source["window"])] == source["key"])]
    if not rows and not independent:
        return ""
    shown = []
    for detail in rows:
        if len(shown) >= 10 or _chat_root.count_tokens("；".join(_view._unread_detail_text(item, include_wakes=False)
                                                 for item in [*shown, detail])) > _chat_root.NOTICE_TOKENS - 150:
            break
        shown.append(detail)
    tail = f"；另有 {len(rows) - len(shown)} 个窗口，用 status() 查看" if len(rows) > len(shown) else ""
    source_lines = []
    for source in independent[:3]:
        mentions = [f"{ordinal}@{member.get('time')}"
                    for ordinal, (_page, _offset, member) in enumerate(
                        _source_unread_members(source), 1) if member.get("mentioned")]
        source_lines.append(f"{source['name']} key={source['key']} 未读={source['remaining']} "
                            f"提及={source['mention_count'] - source['read_mention_count']} "
                            f"提及未读序号@时间={','.join(mentions)} 状态={source['state']}"
                            + (f" 缺口={source['gap']}" if source['gap'] else ""))
    if len(independent) > 3:
        source_lines.append(f"另有 {len(independent) - 3} 个信源，用 status() 查看")
    parts = [*(_view._unread_detail_text(detail) for detail in shown), tail, *source_lines]
    return _chat_root.bounded_excerpt("待正式读取（当前快照；take 执行时重算序号）："
                           + "；".join(part for part in parts if part), _chat_root.NOTICE_TOKENS)


def _drain_legacy_results() -> list[tuple[dict, dict]]:
    # WHY: f4d3591 left completed tool batches in the agent mailbox. Drain only
    # those existing arrivals on the next activation; new code writes R directly.
    # Delete this bridge after deployment confirms no old pending arrivals remain.
    rows = []
    for entry in oplog.unread(_chat_root.AGENT_WINDOW):
        values = entry["event"].get("_stream_results")
        if values is None:
            continue
        recorded = oplog.result(_chat_root.AGENT_WINDOW, values["source"],
                                values["returns"], entry["arrival"])
        rows.append((recorded, _view._result_projection(
            recorded, oplog.say_links(_chat_root.AGENT_WINDOW))))
    return rows


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
            with context.window_lock(window):
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

    with context.window_lock(window):
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
                with context.window_lock(window):
                    state = oplog.resolve_source(source["key"])
                    oplog.finish_source(source["key"], gap="本地归档或入列失败；可重试",
                                        stop_cursor=state["cursor"])
            except Exception:
                traceback.print_exc()

    threading.Thread(target=fetch, name="napcat-source-fetch", daemon=True).start()
    return source


def _take_source_events(window: tuple, source: dict, session: llm.Chat | None,
                        positions: list[tuple[int, int]],
                        *, read_by: str | None = None, read_via: str | None = None,
                        bridge: dict | None = None) -> tuple[list[dict], str | None]:
    from mods import chatlog

    output = []
    pages = {}
    for page_number, offset in positions:
        with context.window_lock(window):
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
            arrival = oplog.pending_message(window, member["message_id"], member["time"],
                                            member.get("message_seq"))
            projected = _formal_input(
                session, window, event, read_by, read_via, bridge,
                lambda converted: oplog.input_source(
                    _chat_root.AGENT_WINDOW, event, converted, source["key"],
                    page_number, offset, origin, window, arrival=arrival,
                    mentioned=member.get("mentioned", False),
                    read_by=read_by, read_via=read_via), echo=True)
        if projected is not None:
            output.append(projected)
    return output, None

_STATUS_TOKENS = 3000

def arrange_take(session, source: str, count: int, start: int, ids: list[str] | None,
          arrival: str, origin: str, message_id: str,
          mentions_only: bool, via: str) -> str:
    if type(count) is not int or not 1 <= count <= _chat_root.MAX_PULL_EVENTS:
        return f"count 必须是 1..{_chat_root.MAX_PULL_EVENTS}"
    if type(start) is not int or start < 1:
        return "start 必须是从 1 起的当前未读序号"
    if ids is not None and (not isinstance(ids, list) or any(not isinstance(key, str) for key in ids)):
        return "ids 必须是稳定成员 key 列表"
    if sum((ids is not None, bool(arrival), bool(origin), bool(message_id))) > 1:
        return "ids、arrival、origin、message_id 只能指定一种"
    if start != 1 and any((ids is not None, arrival, origin, message_id)):
        return "start 只能用于未读范围选择，不能与精确定位并用"
    selected = []
    try:
        if ids is not None or arrival:
            for key in dict.fromkeys(ids if ids is not None else [arrival]):
                member = _unread_member_by_key(source, key)
                if member is not None and (not mentions_only or member["mentioned"]):
                    selected.append(member)
        else:
            for ordinal, (member, _event) in enumerate(_iter_unread_metadata(source), 1):
                if ordinal < start:
                    continue
                if mentions_only and not member["mentioned"]:
                    continue
                if origin and member["origin"] != origin:
                    continue
                if message_id and str(member["message_id"]) != message_id:
                    continue
                selected.append(member)
                if not origin and not message_id and len(selected) >= count:
                    break
    except ValueError as error:
        return f"未安排正式阅读：{error}"
    if message_id and len(selected) > 1:
        return "message_id 命中多条，请用 origin 或稳定成员 key 消歧义"
    if not selected:
        return "该信源没有匹配的未读成员；未安排阅读"
    if ids is not None and len(selected) > 1:
        keys = {member["key"] for member in selected}
        ranks = {member["key"]: position
                 for position, member in enumerate(_all_source_members(source))
                 if member["key"] in keys}
        selected.sort(key=lambda member: ranks[member["key"]])
    window = tuple(selected[0]["window"])
    session.associated_windows.add(window)
    session.requested_reads.append({"window": list(window), "source": source,
                                    "members": selected, "read_by": _output_id(session),
                                    "read_via": via})
    return f"已安排下一次模型请求前正式阅读 {len(selected)} 条；正文不在工具结果中返回"


def _output_id(session) -> str | None:
    action = getattr(session, "active_action", None)
    return str(action).partition("#")[0] if action else None


def _source_status_line(source: dict) -> str:
    return (f"{source['key']} {source['name']} 窗口={tuple(source['window'])} "
            f"状态={source['state']} 未读={source['remaining']} "
            f"提及={source['mention_count'] - source['read_mention_count']} "
            f"已拉取={source['pulled']}"
            + (f" 缺口={source['gap']}" if source['gap'] else ""))


def source_status(source: str = "") -> str:
    """查看未读通知栏；可查看全部活跃信源，也可精确查看一个窗口或历史信源，不读取正文。

    @param
    source: 留空查看全部活跃信源；或填 g<群号>、u<私聊对端号>、fetch 返回的信源 key
    """
    details = unread_details()
    sources = oplog.sources()
    if source:
        try:
            window = parse_target(source)
        except ValueError:
            state = oplog.resolve_source(source)
            if state is None or state["key"] != source:
                return "找不到该窗口或信源 key"
            lines = [_source_status_line(state)]
        else:
            lines = [_view._unread_detail_text(detail) for detail in details
                     if tuple(detail["window"]) == window]
            lines.extend(_source_status_line(state) for state in sources
                         if tuple(state["window"]) == window
                         and (state["remaining"] or state["state"] != "complete"))
            if not lines:
                lines = [f"{source} 当前没有未读、补回缺口或活跃历史信源"]
    else:
        lines = []
        gap_counts = {}
        history_gap_counts = {}
        for detail in details:
            recovery = detail.get("recovery")
            if (recovery and recovery.get("gap") and not detail["unread"]
                    and not detail["mentions"] and not detail["other_wakes"]
                    and not recovery["remaining"]):
                reason = recovery["gap"].split("; cursor=", 1)[0]
                gap_counts[reason] = gap_counts.get(reason, 0) + 1
            else:
                lines.append(_view._unread_detail_text(detail))
        for state in sources:
            if state["source_type"] != "napcat_history":
                continue
            if (state["remaining"] or state["mention_count"] > state["read_mention_count"]
                    or state["state"] == "fetching"):
                lines.append(_source_status_line(state))
            elif state["gap"]:
                reason = state["gap"].split("; cursor=", 1)[0]
                history_gap_counts[reason] = history_gap_counts.get(reason, 0) + 1
            elif state["state"] != "complete":
                lines.append(_source_status_line(state))
        lines.extend(f"无待读内容的窗口缺口：{reason}，{count} 个窗口"
                     for reason, count in gap_counts.items())
        lines.extend(f"无待读内容的历史信源缺口：{reason}，{count} 个信源"
                     for reason, count in history_gap_counts.items())
        if not lines:
            return "当前没有待处理的未读信源；未读未减少"
        lines.append("可用 status(source) 按具体 g/u 窗口或历史信源 key 查看完整状态")
    rendered = "\n".join(lines)
    excerpt = _chat_root.bounded_excerpt(rendered, _STATUS_TOKENS)
    if len(excerpt) < len(rendered):
        complete_lines = excerpt.splitlines()
        if excerpt and not excerpt.endswith("\n") and rendered[len(excerpt)] != "\n":
            complete_lines.pop()
        return "\n".join([*complete_lines,
                          "状态过多，返回已截断；可用 status(source) 按具体窗口或信源 key 查询",
                          "未读未减少"])
    return rendered + "\n未读未减少"


def fetch_source(session, source: str) -> str:
    """从 NapCat 向前补一个信源；未读缺口可续接，已读信源会从旧端另开信源。

    @param
    source: g<群号>、u<私聊对端号>，或已有历史信源 key
    """
    try:
        window = parse_target(source)
    except ValueError:
        state = oplog.resolve_source(source)
        if state is None or state["key"] != source:
            return "找不到该窗口或信源 key"
        window = tuple(state["window"])
        source_key = state["key"]
    else:
        source_key = None
    session.associated_windows.add(window)
    source = fetch_remote_source(window, source_key)
    return (f"信源 {source['key']}（{source['name']}）状态={source['state']}；"
            "后台持续追到锚点或上游尽头，完成前不能正式 take；用 status 查看进度")


def arrange_archive(session, window: str, message_id: str = "", origin: str = "", timestamp: int = 0,
                  before: int = 4, after: int = 4) -> str:
    """从本地档案选消息，在下一次模型请求前作为正式 input 阅读；已读档案可再次阅读。

    @param
    window: g<群号> 或 u<私聊对端号>
    message_id: QQ 消息号；与 origin 二选一
    origin: 稳定档案位置；与 message_id 二选一
    timestamp: message_id 命中多条时用来消歧义的 Unix 整数秒；0 表示不指定
    before: 锚点之前返回多少条档案记录，非负整数
    after: 锚点之后返回多少条档案记录，非负整数
    """
    from mods import chatlog

    try:
        target = parse_target(window)
    except ValueError as error:
        return str(error)
    if not message_id and not origin:
        return "请指定 message_id 或 origin"
    if message_id and origin:
        return "message_id 与 origin 只能指定一个"
    session.associated_windows.add(target)
    try:
        records = chatlog.read_around(
            *target,
            message_id=message_id or None,
            origin=origin or None,
            timestamp=timestamp or None,
            before=before,
            after=after,
        )
    except ValueError as error:
        return f"未查看：{error}"
    selected = [record for record in records
                if _view._model_event(record, target[0] == "group") is not None]
    if not selected:
        return "本地档案没有命中可见记录；未安排阅读"
    session.requested_reads.append(
        {"window": list(target), "records": selected,
         "read_by": _output_id(session), "read_via": "read_messages"})
    return f"已安排下一次模型请求前从档案正式阅读 {len(selected)} 条；正文不在工具结果中返回"


def mark_source(session, source: str) -> str:
    """将一个信源在调用时已存在的全部未读设为已读。这些内容不取得经历号，但仍可用 read_messages 再读档案。

    @param
    source: g<群号>、u<私聊对端号>，或 fetch 返回的独立信源 key
    """
    import json

    try:
        window = parse_target(source)
        state = None
    except ValueError:
        state = oplog.resolve_source(source)
        if state is None or state["key"] != source:
            return "找不到该窗口或信源 key"
        if state["source_type"] == "napcat_boot":
            return "启动补回属于原窗口信源，请用对应的 g/u 窗口名标为已读"
        window = tuple(state["window"])
    session.associated_windows.add(window)
    try:
        read_by = _output_id(session)
        result = (mark_window_read(window, read_by=read_by) if state is None
                  else mark_source_read(state, read_by=read_by))
    except ValueError as error:
        return f"未标为已读：{error}"
    return json.dumps(result, ensure_ascii=False)


def _formal_input(session: llm.Chat | None, window: tuple, event: dict,
                  read_by: str | None, read_via: str | None, bridge: dict | None,
                  commit: Callable[[dict | None], dict], *, echo: bool,
                  skip_unprojectable: bool = False) -> dict | None:
    converted = _view._read_projection(_view._model_event(event, window[0] == "group"),
                                       read_by, read_via)
    if converted is None and skip_unprojectable:
        return None
    if converted is not None and bridge:
        converted = _with_bridge(converted, bridge)
    recorded = commit(converted)
    if converted is None:
        return None
    projection = _view._numbered(converted, recorded["id"])
    if echo:
        projection = _view._echo_relation(projection, recorded,
                                          oplog.say_links(_chat_root.AGENT_WINDOW))
    if session is not None:
        _view._remember_stream(session, projection, recorded["id"])
    return projection


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
        if converted is not None:
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
        pending = oplog.unread(window)
        start = 0
        for item in sources:
            boundary = item["pending_boundary"]
            end = (next((index for index in range(start, len(pending))
                         if not oplog.arrival_before_or_at(pending[index]["arrival"], boundary)),
                        len(pending)) if boundary is not None else start)
            for entry in pending[start:end]:
                yield _live_member(entry, window), entry["event"]
            for page, offset, member in _source_unread_members(item):
                yield _source_member(item, window, page, offset, member), None
            start = end
        for entry in pending[start:]:
            yield _live_member(entry, window), entry["event"]
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


def _live_member(entry: dict, window: tuple) -> dict:
    event = entry["event"]
    return {"key": entry["arrival"], "arrival": entry["arrival"],
            "window": list(window), "origin": entry.get("origin"),
            "message_id": event.get("message_id"), "mentioned":
            entry.get("activation_kind") == "mention"}


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
    with context.window_lock(window):
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
        state = oplog.mark_source_read(source["key"], positions, mention_count, arrivals, read_by)
        return selected, mention_count, state["remaining"]


def _mark_arrival_prefix_read(window: tuple, entries: list[dict],
                              read_by: str | None = None) -> tuple[int, int]:
    if not entries:
        return 0, 0
    arrivals = [entry["arrival"] for entry in entries]
    mentions = sum(entry.get("activation_kind") == "mention" for entry in entries)
    marked = oplog.mark_arrivals_read(window, arrivals, read_by)
    return marked, mentions


def mark_window_read(window: tuple, count: int | None = None,
                     read_by: str | None = None) -> dict:
    """Mark the current ordered unread prefix as read without creating input events."""
    with context.window_lock(window):
        sources = _recovery_sources(window)
        if any(source["state"] == "fetching" for source in sources):
            raise ValueError("该窗口离线补回仍在进行，未读前端尚未固定")
        through = oplog.latest_pending_arrival(window)
        frozen_sources = {source["key"]: source["remaining"] for source in sources}
        marked = mentions = 0

        def allowance(available: int) -> int:
            return available if count is None else min(available, count - marked)

        for original in sources:
            if count is not None and marked >= count:
                break
            boundary = original["pending_boundary"]
            pending = oplog.unread(window)
            prefix = ([entry for entry in pending
                       if oplog.arrival_before_or_at(entry["arrival"], boundary)]
                      if boundary is not None else [])
            removed, mentioned = _mark_arrival_prefix_read(
                window, prefix[:allowance(len(prefix))], read_by)
            marked += removed
            mentions += mentioned
            if count is not None and marked >= count:
                break
            state = oplog.resolve_source(original["key"])
            available = min(state["remaining"], frozen_sources[original["key"]])
            amount = allowance(available)
            if amount:
                removed, mentioned, _remaining = _mark_source_events_read(
                    window, state, amount, read_by)
                marked += removed
                mentions += mentioned
        if count is None or marked < count:
            pending = oplog.unread(window)
            if through is not None:
                pending = [entry for entry in pending
                           if oplog.arrival_before_or_at(entry["arrival"], through)]
            removed, mentioned = _mark_arrival_prefix_read(
                window, pending[:allowance(len(pending))], read_by)
            marked += removed
            mentions += mentioned
        return {"marked_read": marked, "mentions": mentions,
                "remaining": len(oplog.unread(window)) + sum(
                    oplog.resolve_source(source["key"])["remaining"] for source in sources)}


def mark_source_read(source: dict, count: int | None = None,
                     read_by: str | None = None) -> dict:
    window = tuple(source["window"])
    with context.window_lock(window):
        source = oplog.resolve_source(source["key"])
        if source["state"] == "fetching":
            raise ValueError("该信源仍在拉取，未读前端尚未固定")
        amount = source["remaining"] if count is None else min(count, source["remaining"])
        marked, mentions, remaining = _mark_source_events_read(window, source, amount, read_by)
        return {"marked_read": marked, "mentions": mentions, "remaining": remaining}


def _take_members(session: llm.Chat, request: dict) -> tuple[list[dict], str | None]:
    """Commit selected members one by one before projecting the next request."""
    selected = request["members"]
    output = []
    bridge_plan = _source_bridge_plan(request["source"], request.get("last_key", ""), selected)
    while selected:
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
                read_by=request.get("read_by"),
                read_via=request.get("read_via"), bridge=bridge)
            if error:
                return output, error
            output.extend(projected)
            if projected:
                request["last_key"] = member["key"]
            selected.pop(0)
            continue
        arrival = member["arrival"]
        with context.window_lock(window):
            entry = next((item for item in oplog.unread(window)
                          if item["arrival"] == arrival), None)
            if entry is not None:
                located = {**entry["event"], "_log_origin": entry.get("origin"), "_live": True}
                # WHY: I is durable before hiding this exact arrival. A provider failure
                # after request assembly can leave it read but unprocessed; exactly-once
                # delivery is not promised. The oplog validates this exact arrival.
                projection = _formal_input(
                    session, window, located, request.get("read_by"), request.get("read_via"), bridge,
                    lambda converted: oplog.input(
                        _chat_root.AGENT_WINDOW, entry["event"], converted, arrival,
                        source_window=window, read_by=request.get("read_by"),
                        read_via=request.get("read_via")), echo=True)
        if entry is None:
            selected.pop(0)
            if selected:
                bridge_plan = _source_bridge_plan(request["source"],
                                                  request.get("last_key", ""), selected)
            continue
        if projection is not None:
            output.append(projection)
        request["last_key"] = member["key"]
        selected.pop(0)
    return output, None


def _take_archive(session: llm.Chat, request: dict, window: tuple) -> tuple[list[dict], str | None]:
    records = request["records"]
    output = []
    while records:
        record = records[0]
        origin = record.get("_log_origin")
        if not origin:
            return output, "档案记录缺少稳定 origin"
        event = {**record, "_history_source": "archive", "_live": False}
        with context.window_lock(window):
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

            def commit(converted: dict | None) -> dict:
                return oplog.input_archive(_chat_root.AGENT_WINDOW, record, converted, origin,
                                           window, arrival=arrival,
                                           read_by=request.get("read_by"),
                                           read_via=request.get("read_via"), **values)

            projection = _formal_input(session, window, event, request.get("read_by"),
                                       request.get("read_via"), None, commit, echo=False,
                                       skip_unprojectable=True)
        if projection is not None:
            output.append(projection)
        records.pop(0)
    return output, None
