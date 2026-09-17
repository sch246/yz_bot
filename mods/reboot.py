"""Gracefully request a supervisor restart and report across the process boundary."""

import os
import logging
import threading

from mods import LATE, context, file, message, op
from mods.command import command


PHASE = LATE
LOAD_AFTER = ("link", "todo")
GREET_PATH = "data/reboot_greet.py"
_logger = logging.getLogger(__name__)


def on_load(_ctx):
    if not os.path.isfile(GREET_PATH):
        return
    try:
        payload = file.json_read(GREET_PATH)
        if isinstance(payload, dict) and "event" in payload:
            event, resume = payload["event"], bool(payload.get("resume"))
        else:
            # 旧格式：文件里就是那份事件本身，还没有写下来的答案。那就现算——判据和写下来
            # 的那个完全一样，而且事件本身就带着它（发件人是不是 Bot 自己）。默认取"不续"
            # 才是在丢信息：旧文件同样可能是 Bot 自己发起的。
            event, resume = payload, is_self_restart(payload)
        destination = message.target(event)
    except Exception:
        _logger.exception("无法读取 reboot 跨进程问候，保留原文件")
        return
    from mods import import_failures, load_failures

    failed = sorted(set(import_failures) | set(load_failures))
    report = "重启完成"
    if failed:
        report += f"\n加载失败: {failed}"
    message.send(report, **destination)
    os.remove(GREET_PATH)
    if resume:
        resume_chat(event)


def resume_chat(event: dict) -> None:
    """Reopen one chat turn in *event*'s window, once boot has finished.

    WHY: 只在"这次重启是 Bot 自己发起的"时候续。人自己重启是想让新代码生效，不该被带上
    一轮聊天；agent 重启是为了接着做没做完的事，断在那里就是把线索丢了。

    WHY: 续话不伪造入站消息——``context.set_current(event)`` + ``chat.chat()`` 正是路由
    处理一条真消息时做的事，所以续话期间插话、``^C``、``#hint`` 的行为都和正常聊天一致。
    缺的只是一条本来就不存在的消息（op-toolbox 提案的决定二）。

    WHY: 等 boot 结束再开。on_load 期间排在后面的模块还没加载完，这时候进聊天循环是在
    半成品上跑。
    """
    def worker() -> None:
        from mods import get_available, wait_booted

        if not wait_booted(120):
            _logger.warning("等待启动结束超时，放弃续话")
            return
        chat = get_available("chat")
        if chat is None:
            _logger.warning("chat 未加载，无法续话")
            return
        context.set_current(event)
        try:
            chat.chat()
        except Exception:
            _logger.exception("重启后续话失败")
        finally:
            context.clear_current()

    threading.Thread(target=worker, name="reboot-resume", daemon=True).start()


def is_self_restart(event: dict) -> bool:
    """Whether the Bot itself asked for this reboot rather than a person.

    WHY: 判据是``history.author``，不是顶层 ``user_id``——私聊窗口的顶层 ``user_id`` 是
    窗口对端（Bot 自己发的话也带着对端的 id），拿它问"谁发起的"必然问错。``sender.user_id``
    才是两种窗口下都指向作者的字段。
    """
    from mods import history, identity

    return history.author(event) == identity.bot_id()


def _greet(event: dict) -> dict:
    """What one process leaves for the next: the window, and whether to continue.

    WHY: *resume* 在这一侧算好、写下来。它是 :func:`is_self_restart` 的**缓存**——写下来
    的那一刻事件最新鲜，判据也只在一处。读到没有这个字段的旧文件时，调用方现算一次同一个
    函数，两条路得到的答案一样。
    """
    return {"event": event, "resume": is_self_restart(event)}


@command
def run(body: str):
    """优雅退出并请求外层监督进程重启（管理员）。

    格式：.reboot
    发送“重启中”后以退出码 233 结束；下次启动向原窗口回报完成。如果这次重启是 Bot 自己
    发起的（例如它用 op 工具集的 send_command 注入 .reboot），启动后会在这个窗口自动
    接着开一轮聊天，中断的那件事不会丢。
    """
    event = context.current()
    if not op.require_op(event):
        return None
    if body.strip():
        return run.__doc__
    message.send("重启中", **message.target(event)).result()
    file.json_write(file.ensure_file(GREET_PATH), _greet(event))
    raise SystemExit(233)
