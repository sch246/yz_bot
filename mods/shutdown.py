"""Gracefully stop the Bot and keep the next-start greeting."""

import ast
import logging
import os

from mods import context, file, message, op
from mods.command import command


LOAD_AFTER = ("message",)
GREET_PATH = "data/shutdown_greet.py"
_logger = logging.getLogger(__name__)


def _read_legacy_greeting(source):
    """Accept only the historical ``send(<literal>, **<dict literal>)`` shape."""
    tree = ast.parse(source, filename=GREET_PATH, mode="exec")
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Expr):
        raise ValueError("问候文件必须只包含一个表达式")
    call = tree.body[0].value
    if (
        not isinstance(call, ast.Call)
        or not isinstance(call.func, ast.Name)
        or call.func.id != "send"
        or len(call.args) != 1
        or len(call.keywords) != 1
        or call.keywords[0].arg is not None
    ):
        raise ValueError("问候文件不是已知 send(...) 形状")
    greeting = ast.literal_eval(call.args[0])
    event = ast.literal_eval(call.keywords[0].value)
    if not isinstance(greeting, str) or not isinstance(event, dict):
        raise ValueError("问候文本或事件不是普通数据")
    return greeting, event


def on_load(_ctx):
    if not os.path.isfile(GREET_PATH):
        return
    source = file.read(GREET_PATH)
    try:
        greeting, event = _read_legacy_greeting(source)
    except Exception:
        _logger.exception("无法安全读取 shutdown 跨进程问候，保留原文件")
        return
    message.send(greeting, **message.target(event))
    os.remove(GREET_PATH)


@command
def run(body: str):
    """优雅关闭 Bot，不请求自动重启（管理员）。

    格式：.shutdown
    发送“关闭中”后正常退出；下次人工启动会向原窗口发送醒来提示。
    """
    event = context.current()
    if not op.require_op(event):
        return None
    if body.strip():
        return run.__doc__
    message.send("关闭中", **message.target(event)).result()
    from mods import identity

    nickname = identity.bot_name()
    source = f"send({nickname + '醒了！'!r}, **{event!r})"
    file.write(file.ensure_file(GREET_PATH), source)
    raise SystemExit(0)
