"""What is left of the old reader: who a message addresses, explicit say targets,
boot recovery sources, and marking a window read once the agent has seen it."""

from __future__ import annotations

import re
import traceback

from mods import context, identity, oplog

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
