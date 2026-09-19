"""Recent per-window OneBot event history.

Pure memory.  It used to persist itself to ``data/cache_msgs``, which made it a
second write authority for events ``chatlog`` had already written down; the
files are the authority now, and ``chatlog.on_load`` refills this at boot.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from threading import RLock
from typing import Any, Callable, Iterator

from mods import INFRA


PHASE = INFRA
LOAD_AFTER = ("storage",)
MAX_LEN = 256

_lock = RLock()


@contextmanager
def lock() -> Iterator[None]:
    """Guard a direct write to ``msgs``; only ``chatlog``'s boot rebuild needs it."""
    with _lock:
        yield


def _empty() -> dict[str, Any]:
    return {"group": {}, "private": {}, "bot": [], "last": None}


msgs: dict[str, Any] = _empty()


def add_msg(kind: str, uid: int | str, msg: dict[str, Any]) -> None:
    if kind not in ("group", "private"):
        raise ValueError(f"未知 history 类型：{kind}")
    with _lock:
        events = msgs[kind].setdefault(uid, [])
        events.insert(0, msg)
        msgs["last"] = msg
        del events[MAX_LEN:]


def add_self_msg(msg: dict[str, Any]) -> None:
    with _lock:
        events = msgs["bot"]
        events.insert(0, msg)
        del events[MAX_LEN:]


def get_last() -> dict[str, Any] | None:
    with _lock:
        return msgs["last"]


def _current() -> dict[str, Any]:
    from mods import context

    current = getattr(context, "current", None)
    if callable(current):
        return current()
    thismsg = getattr(context, "thismsg", None)
    if callable(thismsg):
        return thismsg()
    raise RuntimeError("context 未提供当前消息接口")


def window(event: dict[str, Any]) -> tuple[str, Any] | None:
    """The chat window an event belongs to: one group, or one private peer.

    This is the key both the recent-history dict and the chatlog directory tree
    are organised by, and it used to be spelled out separately in each.  It is
    *not* ``context.interaction_key``, which identifies one line of interaction
    -- a single person inside a window -- so that a ``yield`` knows whose next
    message it is waiting for.  A window has many lines.

    WHY: 私聊窗口读 ``target_id``，不读顶层 ``user_id``。``user_id`` 是**作者**，
    两个方向、实时与重建都一致（2026-09-19 实测：对端发来的与 Bot 自己发出的私聊
    事件里 ``user_id`` 都是作者，``target_id`` 都是那条私聊），而私聊"是哪一条"
    只有 ``target_id`` 答得出，NapCat 两个方向都填它。曾经靠"把 Bot 自己那条的
    ``user_id`` 改写成对端"凑出窗口——那要求每个入口都记得改写，也让"谁发的"和
    "发到哪个窗口"两个问题共用同一个字段。读不到 ``target_id`` 就返回 ``None``：
    窗口未知好过猜一个错的。
    """
    if event.get("group_id") is not None:
        return "group", event["group_id"]
    target_id = event.get("target_id")
    return ("private", target_id) if target_id is not None else None


def getlog(
    msg: dict[str, Any] | tuple[str, Any] | None = None,
    *,
    since: int | None = None,
    until: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """One window's events, newest first.

    Without a range this is what it has always been: one dict lookup returning
    the live in-memory list, which the hot callers (``op``, ``post``, ``link``,
    ``.py``) depend on being free.

    With ``since`` and/or ``until`` -- inclusive epoch seconds -- it reads the
    chatlog files instead and returns a **new** list of rebuilt records.  The
    files are the authority: ``chatlog`` appends before it calls ``add_msg``, so
    disk is never behind memory and there is nothing to merge.  Rebuilt records
    carry ``_source``/``_derived``/``_missing`` and so are distinguishable from
    live events; they also still include messages that were later recalled,
    because the tree is append-only.  Reading files can raise ``OSError``, which
    is exactly why the range is spelled out in the call and not inferred.

    *msg* may be an event, a ``window()`` key, or omitted for the current event.

    ``limit`` asks for at most the newest that many records.  It only makes
    sense together with a file read: the in-memory list is already capped at
    ``MAX_LEN``, so a ``limit`` at or below that is a plain slice and callers
    keep doing it themselves; a larger one has to come from the tree, and
    ``chatlog.read_range`` walks newest-first and stops early for it.
    """
    if isinstance(msg, tuple):
        key: tuple[str, Any] | None = msg
    else:
        key = window(_current() if msg is None else msg)
    if key is None:
        return []
    if since is None and until is None and limit is None:
        with _lock:
            return msgs[key[0]].setdefault(key[1], [])
    from mods import chatlog

    return chatlog.read_range(key[0], key[1], since=since, until=until, limit=limit)


def author(event: dict[str, Any]) -> Any:
    """Who actually sent an event.

    WHY: 两个方向、实时与重建都读 ``user_id`` 也可以——它现在就是作者（私聊窗口
    改由 ``target_id`` 给出，见 ``window``）。仍然优先 ``sender.user_id``，是因为
    从 chatlog 重建 v0 私聊行时作者只能按名字猜，那份猜测连同名字一起写在 ``sender``
    上，而 ``user_id`` 会缺；两者不一致时 ``sender`` 更具体。
    """
    sender = event.get("sender")
    if isinstance(sender, dict) and sender.get("user_id") is not None:
        return sender["user_id"]
    return event.get("user_id")


def same_author(msg: dict[str, Any]) -> Callable[[dict[str, Any]], bool]:
    """Match the messages written by whoever wrote *msg*."""
    who = author(msg)

    def predicate(candidate: dict[str, Any]) -> bool:
        return candidate.get("post_type") in ("message", "message_sent") and author(candidate) == who

    return predicate


def get_self_log(msg: dict[str, Any]) -> list[dict[str, Any]]:
    return list(filter(same_author(msg), getlog(msg)))


def _predicate(msg: dict[str, Any], value: Callable[[dict[str, Any]], bool] | str):
    if callable(value):
        return value
    own = same_author(msg)
    pattern = re.compile(value)
    from mods import msgs

    return lambda candidate: own(candidate) and pattern.match(msgs.body(candidate)) is not None


def same_times(
    msg: dict[str, Any],
    value: Callable[[dict[str, Any]], bool] | str,
    count: int | None = None,
) -> bool:
    events = getlog(msg)
    end = None if count is None else count + 1
    if end is not None and len(events) < end:
        return False
    return all(_predicate(msg, value)(event) for event in events[1:end])


def any_same(
    msg: dict[str, Any],
    value: Callable[[dict[str, Any]], bool] | str,
    count: int | None = None,
) -> bool:
    end = None if count is None else count + 1
    predicate = _predicate(msg, value)
    return any(predicate(event) for event in getlog(msg)[1:end])


def get_one(
    msg: dict[str, Any],
    predicate: Callable[[dict[str, Any]], bool],
    count: int | None = None,
) -> dict[str, Any] | None:
    end = None if count is None else count + 1
    return next((event for event in getlog(msg)[1:end] if predicate(event)), None)


def remove_message(
    message_id: int,
    group_id: int | None = None,
    user_id: int | None = None,
) -> dict[str, Any] | None:
    """Remove a recalled event from its recent window, if it is still cached."""
    key = window({"group_id": group_id, "user_id": user_id})
    with _lock:
        if key is not None:
            candidates = [msgs[key[0]].get(key[1], [])]
        else:
            candidates = [*msgs["group"].values(), *msgs["private"].values()]
        for events in candidates:
            for index, event in enumerate(events):
                is_message = event.get("post_type") in ("message", "message_sent") or (
                    event.get("post_type") is None and "message" in event
                )
                if is_message and event.get("message_id") == message_id:
                    return events.pop(index)
    return None
