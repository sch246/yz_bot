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


def _route_event(event: dict) -> str | None:
    context.set_current(event)
    chatlog = _optional("chatlog")
    chat = _optional("chat")
    if chatlog is not None:
        # The prefix and the body chatlog formats are one line of terminal
        # output, so they are one record rather than two racing writes.
        writer = lambda: chatlog.write(event)
        # Chat history and its durable arrival share the window lock. A concurrent
        # formal reader cannot observe one without the other.
        written = chat.record_event(event, writer) if chat is not None else writer()
        if written is not None:
            body = chatlog.display(written).removesuffix("\n")
            # Bot 自己那条是从 NapCat 回声回来的，走的也是这一行；标签不按 post_type 分，
            # 终端里自己说的话就会打成【收到消息】。
            label = "发送消息" if event.get("post_type") == "message_sent" else "收到消息"
            _stream.info(f'[{time.strftime("%H:%M:%S")}]【{label}】{body}')
    # WHY: 自己发出去的消息**记录、但不派发**。这条关卡守的不变量是：Bot 说的话是记录的
    # 来源，永远不是指令的来源。以后新增的派发路径该落在关卡哪一侧，由这句话回答，而不是由
    # "自己的消息不派发"回答。
    # WHY: 不派发挡住的是执行。派发会让 Bot 自己的话走命令、shell 和 link：`.` 开头当命令
    # 跑，`!` 开头过 op 门——而 Bot 作者的结果来自固定的 `config.bot_permissions.op`。
    # 于是"网页/检索里的不受信文本 → 模型复述 → 自己执行"会成为一条完整的路。
    # WHY: 位置在 chatlog.write **之后**是承重的，而且方向和 0a334b7 那版相反。回声现在是
    # "Bot 说过的话进聊天记录和内存历史"的唯一写入权威：`message.record_sent` 连同它那次
    # get_msg 回查已经删掉，两条发送路径（send_msg 与 send_forward_msg）都只靠回声落账。
    # 挪回 write 之前，Bot 自己的每句话会从 chatlog 和下一轮上下文里整段消失，而且是静默
    # 的。反过来，挪到 write 之后却**不**删 record_sent，每句话就记两遍——`chatlog.write`
    # 的判据收 message_sent，`history.add_msg` 又不按 message_id 去重。两者必须同进同退。
    # WHY: 判据只能是 post_type。`mods/tools/op.py` 的 `_event` 伪造的自注入命令和真回声只
    # 差这一个字段——两者的 `sender.user_id` 都是 Bot 自己。换成"作者是不是 Bot"会把
    # send_command 连同它唯一支撑的那条真重启路径一起挡掉。也不要改读 message_sent_type：
    # 那是 NapCat 的扩展字段，分的是自发消息的**种类**，而这里要挡的是"这是我自己发出去
    # 的"，与种类无关。
    if event.get("post_type") == "message_sent":
        return "self"
    if any(value in sys.argv[1:] for value in ("-l", "--log-only", "log_only")):
        return "log-only"

    if msgs.is_msg(event):
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
                    central = context.get_turn(chat.AGENT_WINDOW) if chat is not None else None
                    central_work = central is not None and window in getattr(central, "associated_windows", ())
                    context.cancel_turn(chat.AGENT_WINDOW if central_work else window)
                    # WHY: 软停止够不到正在同步执行的工具调用。真能让它回来的是把它
                    # 启动的子进程 kill 掉（卡死的 grep 就是这一类），`exec_code` 那种跑在
                    # 子线程里的代码则靠注入中断。两条都在 mods/watchdog，见那个模块的说明。
                    watchdog = _optional("watchdog")
                    if watchdog is not None:
                        watchdog.stop(chat.AGENT_WINDOW if central_work else window, "用户 ^C")

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


def _route(event: dict) -> str | None:
    """Release synchronous arrivals here; asynchronous link releases on completion."""
    route = None
    try:
        route = _route_event(event)
        return route
    finally:
        if route != "link":
            context.release_arrival(event)


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
