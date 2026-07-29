"""Gracefully request a supervisor restart and report across the process boundary."""

import os
import logging

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
        event = file.json_read(GREET_PATH)
        message.target(event)
    except Exception:
        _logger.exception("无法读取 reboot 跨进程问候，保留原文件")
        return
    from mods import import_failures, load_failures

    failed = sorted(set(import_failures) | set(load_failures))
    report = "重启完成"
    if failed:
        report += f"\n加载失败: {failed}"
    message.send(report, **message.target(event))
    os.remove(GREET_PATH)


@command
def run(body: str):
    """优雅退出并请求外层监督进程重启（管理员）。

    格式：.reboot
    发送“重启中”后以退出码 233 结束；下次启动向原窗口回报完成。
    """
    event = context.current()
    if not op.require_op(event):
        return None
    if body.strip():
        return run.__doc__
    message.send("重启中", **message.target(event)).result()
    file.json_write(file.ensure_file(GREET_PATH), event)
    raise SystemExit(233)
