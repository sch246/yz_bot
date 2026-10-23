"""What is left of the old reader: who a message addresses, explicit send targets,
and marking a window read once the agent has seen it."""

from __future__ import annotations

import re

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


def mark_window_read(window: tuple, through=...) -> dict:
    """Mark the ordered unread prefix as read without creating input events.

    *through* is the last arrival to mark, as ``oplog.latest_pending_arrival``
    gave it when the screen was read; left out, it is whatever is unread now.
    """
    with context.window_lock(window):
        if through is ...:
            through = oplog.latest_pending_arrival(window)
        pending = ([entry for entry in oplog.unread(window)
                    if oplog.arrival_before_or_at(entry["arrival"], through)]
                   if through is not None else [])
        marked = oplog.mark_arrivals_read(window, [entry["arrival"] for entry in pending]) if pending else 0
        return {"marked_read": marked,
                "mentions": sum(entry.get("activation_kind") == "mention" for entry in pending),
                "remaining": len(oplog.unread(window))}
