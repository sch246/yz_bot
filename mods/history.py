"""Recent per-window OneBot event history."""

from __future__ import annotations

import ast
import logging
import os
import re
import tempfile
from threading import RLock
from typing import Any, Callable

from mods import INFRA


PHASE = INFRA
LOAD_AFTER = ("storage",)
MAX_LEN = 256
CACHE_FILE = "data/cache_msgs"

logger = logging.getLogger(__name__)
_lock = RLock()


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
    """
    if event.get("group_id") is not None:
        return "group", event["group_id"]
    user_id = event.get("user_id")
    return ("private", user_id) if user_id is not None else None


def getlog(
    msg: dict[str, Any] | tuple[str, Any] | None = None,
    *,
    since: int | None = None,
    until: int | None = None,
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
    """
    if isinstance(msg, tuple):
        key: tuple[str, Any] | None = msg
    else:
        key = window(_current() if msg is None else msg)
    if key is None:
        return []
    if since is None and until is None:
        with _lock:
            return msgs[key[0]].setdefault(key[1], [])
    from mods import chatlog

    return chatlog.read_range(key[0], key[1], since=since, until=until)


def author(event: dict[str, Any]) -> Any:
    """Who actually sent an event.

    ``user_id`` cannot answer this.  A group event carries the sender there, but
    a private one carries the window's peer -- including for the Bot's own
    messages, which ``mods.message`` stamps with the peer's id -- so in a private
    window ``user_id`` makes the Bot and the person indistinguishable.  ``sender``
    is the field that names the author in both windows, on live events and on
    records rebuilt from chatlog alike.
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
    return lambda candidate: own(candidate) and pattern.match(str(candidate.get("message", ""))) is not None


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


def _valid(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("group"), dict)
        and isinstance(value.get("private"), dict)
        and isinstance(value.get("bot"), list)
        and "last" in value
    )


def on_load(_ctx: dict[str, Any] | None = None) -> None:
    global msgs
    if not os.path.exists(CACHE_FILE):
        return
    try:
        with open(CACHE_FILE, encoding="utf-8") as file:
            value = ast.literal_eval(file.read())
        if not _valid(value):
            raise ValueError("cache_msgs 形状错误")
    except Exception:
        logger.exception("读取近期消息缓存失败，保留空 history")
        return
    with _lock:
        msgs = value


def on_exit() -> None:
    directory = os.path.dirname(CACHE_FILE)
    os.makedirs(directory, exist_ok=True)
    with _lock:
        text = repr(msgs)
    fd, temporary = tempfile.mkstemp(
        dir=directory,
        prefix=".cache_msgs.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, CACHE_FILE)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
