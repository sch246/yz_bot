"""Outbound messages, current-window replies, and internal message injection."""

from __future__ import annotations

import logging
from pathlib import Path
from queue import Full, Queue
import random
import sys
import threading
import time
from typing import Any

from mods import INFRA, connect
from mods import context
from mods import log
from mods.thread import SimpleFuture


PHASE = INFRA
# The sender must remain open until scheduled jobs have stopped, then drain
# before storage performs its final save.
LOAD_AFTER = ("storage",)

_log = logging.getLogger(__name__)
_stream = log.stream("msg")
_BOT_DIR = Path(__file__).resolve().parent.parent
_STOP = object()
_queue: Queue = Queue(maxsize=20)
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_stopping = False


class SendFuture(SimpleFuture[int | None]):
    """A marker future whose result is delivery metadata, not reply text."""


def target(event: dict) -> dict[str, object]:
    """Reduce a OneBot event to the explicit destination accepted by send_msg."""
    group_id = event.get("group_id")
    if group_id is not None:
        return {"group_id": group_id}
    user_id = event.get("user_id") or event.get("sender_id")
    if user_id is None:
        raise ValueError("event has no message destination")
    return {"user_id": user_id}


def _chatlog_write(event: dict) -> None:
    from mods import get_available

    chatlog = get_available("chatlog")
    if chatlog is not None:
        written = chatlog.write(event)
        if written is not None:
            body = chatlog.display(written).removesuffix("\n")
            _stream.info(f'[{time.strftime("%H:%M:%S")}]【发送消息】{body}')


def _send_now(text: Any, user_id=None, group_id=None, **params) -> int | None:
    if "-d" in sys.argv or "--debug" in sys.argv:
        _stream.info("【准备发送消息】")
    text = str(text).replace("__botdir__", str(_BOT_DIR))
    params.pop("message", None)
    if group_id is not None:
        user_id = None
    if user_id is None and group_id is None:
        raise ValueError("user_id or group_id is required")

    result = connect.call_api(
        "send_msg",
        message=text,
        user_id=user_id,
        group_id=group_id,
        **params,
    )
    if result.get("retcode") != 0:
        raise RuntimeError(f"OneBot send_msg failed: {result.get('wording', result)!s}")
    message_id = (result.get("data") or {}).get("message_id")
    if message_id is None:
        return None

    fetched = connect.call_api(
        "get_msg",
        message_id=message_id,
        user_id=user_id,
        group_id=group_id,
    )
    if fetched.get("retcode") != 0 or not isinstance(fetched.get("data"), dict):
        _log.warning("sent OneBot message %s but failed to fetch it", message_id)
        return message_id
    sent_event = dict(fetched["data"])
    if group_id is None:
        sent_event["user_id"] = user_id
    elif isinstance(sent_event.get("sender"), dict):
        sent_event["user_id"] = sent_event["sender"].get("user_id")
    _chatlog_write(sent_event)
    return message_id


def _work() -> None:
    while True:
        item = _queue.get()
        try:
            if item is _STOP:
                return
            future, args, params, origin = item
            context.set_current(origin)
            try:
                future.set_result(_send_now(*args, **params))
            except BaseException as error:
                _log.exception("failed to send OneBot message")
                future.set_exception(error)
            finally:
                context.clear_current()
            time.sleep(random.uniform(0.3, 0.6))
        finally:
            _queue.task_done()


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            if _stopping:
                raise RuntimeError("message sender is stopping")
            _worker = threading.Thread(target=_work, name="mods.message.sender", daemon=True)
            _worker.start()


def send(text: Any, user_id=None, group_id=None, **params) -> SendFuture:
    """Queue a message to an explicit private user or group."""
    future = SendFuture()
    try:
        _ensure_worker()
        _queue.put_nowait((future, (text, user_id, group_id), params, context.current()))
    except (Full, RuntimeError) as error:
        _log.error("发送队列拒绝了新消息：%s", error)
        future.set_exception(error)
    return future


def sendmsg(text: Any, user_id=None, group_id=None, **params) -> SendFuture:
    """Queue a reply, defaulting to the current event's window."""
    if user_id is None and group_id is None:
        event = context.current()
        if event is None:
            future = SendFuture()
            future.set_exception(RuntimeError("sendmsg has no current event"))
            return future
        group_id = event.get("group_id")
        if group_id is None:
            user_id = event.get("user_id") or event.get("sender_id")
    return send(text, user_id=user_id, group_id=group_id, **params)


def recvmsg(text: str, sender_id=None, private: bool | None = None, **values):
    """Inject one internal message through the same real routing path."""
    origin = context.current()
    if origin is None:
        origin = {}
    synthetic_message_id = values.get("message_id", -time.time_ns())
    event = dict(origin)
    event.update(values)
    event.update(
        {
            "time": event.get("time", int(time.time())),
            "post_type": "message",
            "message": text,
            "raw_message": text,
            "message_id": synthetic_message_id,
        }
    )
    sender_id = sender_id if sender_id is not None else event.get("user_id")
    if sender_id is None:
        raise ValueError("recvmsg requires a sender_id or current message")
    event["user_id"] = sender_id
    sender = dict(event.get("sender") or {})
    sender["user_id"] = sender_id
    event["sender"] = sender
    if private is True:
        event.pop("group_id", None)
        event["message_type"] = "private"
        event.setdefault("sub_type", "friend")
    elif event.get("group_id") is not None:
        event["message_type"] = "group"
        event.setdefault("sub_type", "normal")
    else:
        event["message_type"] = "private"
        event.setdefault("sub_type", "friend")

    from mods import bot

    return bot.recv(event)


def on_exit() -> None:
    global _stopping
    with _worker_lock:
        _stopping = True
        worker = _worker
    if worker is None:
        return
    _queue.join()
    _queue.put(_STOP)
    worker.join()
def get_reply(event, predicate=lambda _value: True):
    """Fetch the OneBot message referenced by an event's reply CQ code."""
    from mods import connect, cq, msgs

    if not msgs.is_msg(event):
        return {}
    try:
        text = msgs.body(event)
        reply = msgs.reply_cq(event)
        if not (predicate(text) and reply):
            return {}
        message_id = cq.load(reply)["data"]["id"]
        response = connect.call_api("get_msg", message_id=message_id)
        return response.get("data") or {}
    except Exception:
        return {}
