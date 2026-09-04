"""The single explicit event loop and routing path."""

from __future__ import annotations

from inspect import getgeneratorstate, GEN_CREATED
import logging
import subprocess
import sys
import time
from types import GeneratorType

from mods import command
from mods import connect
from mods import context
from mods import cq
from mods import log
from mods import message
from mods import msgs
from mods import thread


_log = logging.getLogger(__name__)
_stream = log.stream("msg")


def _optional(name: str):
    from mods import get_available

    return get_available(name)


def _report_error(error: BaseException) -> None:
    _log.error(
        "event routing failed",
        exc_info=(type(error), error, error.__traceback__),
    )
    try:
        message.sendmsg(f"处理失败: {type(error).__name__}: {error}")
    except Exception:
        _log.exception("failed to report routing error")


def _future_result(future) -> None:
    try:
        _handle_result(future.result())
    except Exception as error:
        _report_error(error)


def _advance_generator(generator: GeneratorType, event: dict | None = None) -> None:
    try:
        if event is None and getgeneratorstate(generator) == GEN_CREATED:
            result = next(generator)
        else:
            result = generator.send(event)
    except StopIteration as stop:
        _handle_result(stop.value)
        return
    context.register_waiter(context.interaction_key(), generator)
    _handle_result(result)


def _handle_result(result) -> None:
    if result is None or result == "":
        return
    if isinstance(result, GeneratorType):
        _advance_generator(result)
        return
    if isinstance(result, message.SendFuture):
        return
    add_callback = getattr(result, "add_done_callback", None)
    if callable(add_callback) and callable(getattr(result, "result", None)):
        origin = context.current()

        def completed(future) -> None:
            context.set_current(origin)
            try:
                _future_result(future)
            finally:
                context.clear_current()

        add_callback(completed)
        return
    message.sendmsg(result)


def _deliver_waiter(waiter, event: dict) -> None:
    if isinstance(waiter, GeneratorType):
        _advance_generator(waiter, event)
        return
    deliver = getattr(waiter, "deliver", None) or getattr(waiter, "feed", None)
    if callable(deliver):
        deliver(event)
        return
    put = getattr(waiter, "put", None)
    if callable(put):
        put(event)
        return
    if callable(waiter):
        _handle_result(waiter(event))
        return
    raise TypeError(f"unsupported continuation: {type(waiter).__name__}")


def _require_op(event: dict) -> bool:
    op = _optional("op")
    if op is None:
        message.sendmsg("权限模块不可用")
        return False
    if op.require_op(event):
        return True
    return False


@thread.to_thread
def _run_bash(command_text: str):
    completed = subprocess.run(
        cq.unescape2(command_text),
        shell=True,
        text=True,
        capture_output=True,
        timeout=10,
    )
    output = (completed.stdout + completed.stderr).strip()
    return cq.escape2(output) if output else None


def _route(event: dict) -> str | None:
    context.set_current(event)
    chatlog = _optional("chatlog")
    if chatlog is not None:
        # The prefix and the body chatlog formats are one line of terminal
        # output, so they are one record rather than two racing writes.
        written = chatlog.write(event)
        if written is not None:
            body = chatlog.display(written).removesuffix("\n")
            _stream.info(f'[{time.strftime("%H:%M:%S")}]【收到消息】{body}')
    if any(value in sys.argv[1:] for value in ("-l", "--log-only", "log_only")):
        return "log-only"

    if msgs.is_msg(event):
        chat = _optional("chat")
        if chat is not None:
            chat.eager_cache_images(event)
        key = context.interaction_key(event)
        # ``message`` stays exactly what NapCat sent, all the way into the chat
        # log and history; the entry-point form -- reply and leading ats off --
        # is derived here for dispatch only, so a rebuilt event and a live one
        # route identically.
        value = msgs.body(event)

        if value.rstrip() in ("^C", "^c"):
            context.cancel(key)
            # WHY: ^C 同时打断这条交互线上的 waiter 和这个**窗口**正在跑的那轮 LLM。
            # 两个粒度不同，所以是两次取消：waiter 按 (窗口, 用户) 登记，只有等它的人
            # 能收回自己的 yield；LLM 上下文按窗口共享，群里任何人都该能制止一轮跑偏的
            # 生成，不然只有触发者能停，其他人只能看着。
            history = _optional("history")
            if history is not None:
                window = history.window(event)
                if window is not None:
                    context.cancel_turn(window)

        waiter = context.pop_waiter(key)
        if waiter is not None:
            _deliver_waiter(waiter, event)
            return "continuation"

        if value.startswith("."):
            matched = command.match(value[1:])
            if matched is not None:
                _handle_result(command.run(*matched))
                return "command"

        if value.startswith("!"):
            if _require_op(event):
                _handle_result(_run_bash(value[1:]))
            return "shell"

        if value.startswith("#!"):
            if _require_op(event):
                message.sendmsg(f"执行了: {value[2:]}")
            return "shell-dry-run"

        link = _optional("link")
        if link is not None:
            link.dispatch(event)
            return "link"
        return None

    if msgs.is_notice(event):
        if msgs.is_recall(event):
            history = _optional("history")
            if history is not None:
                history.remove_message(
                    event.get("message_id"),
                    group_id=event.get("group_id"),
                    user_id=event.get("user_id"),
                )
            return "recall"
        link = _optional("link")
        if link is not None:
            link.dispatch(event)
            return "link"
    return None


def recv(event: dict | None):
    """Consume one raw OneBot event without hiding the route ordering."""
    if event is None:
        _log.warning("OneBot connection returned no event")
        time.sleep(1)
        return None
    if msgs.is_heartbeat(event):
        return "heartbeat"
    if msgs.is_notify(event) and event.get("sub_type") == "input_status":
        return "input-status"
    try:
        return _route(event)
    except context.InteractionCancelled:
        return "cancelled"
    except Exception as error:
        _report_error(error)
        return "error"


def run() -> None:
    while True:
        recv(connect.recv_msg())
