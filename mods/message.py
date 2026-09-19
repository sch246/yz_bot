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
    """Reduce a OneBot event to the explicit destination accepted by send_msg.

    WHY: 私聊的目的地是 ``target_id``——那条私聊的对端——不是顶层 ``user_id``。
    ``user_id`` 是作者，Bot 自己发出的回声也带着它；拿它当目的地，回复会发给自己。
    群聊本来只看 ``group_id``。
    """
    group_id = event.get("group_id")
    if group_id is not None:
        return {"group_id": group_id}
    user_id = event.get("target_id")
    if user_id is None:
        raise ValueError("event has no message destination")
    return {"user_id": user_id}


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
    # WHY: 这里**不**登记"我说过这句话"。写记录归 NapCat 的自发消息回声（post_type
    # message_sent），由 bot._route 收在 chatlog.write 那一行，对任何 action 自动生效。
    # 这里原先挂着 `record_sent`：发完再 get_msg 回查一次、自己写进 chatlog 与 history。
    # 它是在**手工枚举发送路径**，而 mods/forward.py 当初要补登记一次就是枚举没做完的
    # 证据；回查失败时还会静默丢账（消息发出去了，chatlog 和 history 里没有）。别因为
    # "某条路看起来没被记下来"就把它加回来——先确认 NapCat 的 reportSelfMessage 还开着。
    # 删除条件：回声不再进来（那时 bot._route 的自发消息关卡也一并失去意义）。
    return (result.get("data") or {}).get("message_id")


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
            # 同上：私聊回给窗口对端，不是回给作者。
            user_id = event.get("target_id")
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
