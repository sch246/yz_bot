"""以 Bot 自身的身份执行命令（含重启自己）；只在 op 发起的聊天轮里可见、可加载。

## 使用时机

`send_command` 把一条文本当作**自己发给自己的命令**送进 Bot 唯一的那条事件循环
（`connect._events`），由主线程按真实路由处理——命令、shell、link、聊天都和真人触发的一模
一样；工具调用本身立刻返回，不阻塞当前这轮生成。

- 这是让模型**真正重启自己**的唯一路径：`.reboot` 的退出动作必须落在主线程（外层
  `run.py` 认退出码 233），而工具调用跑在 worker 线程里，直接 `raise SystemExit(233)`
  只会打死那个线程，进程照常活着。
- 消息的发送者写成 Bot 自己（`identity.bot_id()`），因此需要 op 的命令照样放行——前提是
  Bot 的 QQ 号本身在 op 名单里。窗口由 `target` 决定，缺省是当前窗口。
- 命令照常进聊天记录，所以「重启中」「重启完成」这类回执你看得见；它也以 `assistant`
  角色出现在后续上下文里，因为它确实是 Bot 自己做的事。要它**不**进上下文，让 `text` 以
  `#` 开头（那是全仓库通用的"不进模型上下文"前缀）。
- 调它注入 `.reboot` 之后**不必**再自己发一条「重启中」：`.reboot` 会先同步把「重启中」
  发进同一窗口、再退出，下次启动时 `reboot.on_load` 又发「重启完成」。补发只会让这个
  窗口里出现两条一样的回执。
"""

from __future__ import annotations

import re
import time

# 门控声明：只有 op 发起的轮才看得到、加载得了这个模块。判据见 tools.op_tool_visible。
OP_ONLY = True

_match_window = re.compile(r"^([guGU]?)([0-9]+)$")


def send_command(text: str, target: str = "") -> str:
    """以 Bot 自己的身份执行一条命令，走真实路由；立刻返回投递结果，不等它跑完。

    @param
    text: 命令原文，例如 .reboot、.chattop、!ls、#ops；首字符决定它走哪条路
    target: 这条命令属于哪个窗口：g<群号>、u<QQ号>，留空表示当前窗口
    """
    from mods import connect, context, identity, op

    current = context.current() or {}
    if not op.is_op(current):
        return "权限不足"
    command = text.strip()
    if not command:
        return "命令为空。text 要写成一条真正的命令，例如 .reboot"
    window = _resolve_window(target, current)
    if isinstance(window, str):
        return window
    group_id, user_id = window
    connect._events.put(_event(command, group_id, user_id))
    where = f"群{group_id}" if group_id is not None else f"私聊{user_id}"
    return f"已投递到{where}，由主循环执行：{command}"


def _resolve_window(target: str, current: dict):
    """Turn ``target`` into ``(group_id, user_id)``; a string means a complaint."""
    choice = target.strip()
    if not choice:
        group_id = current.get("group_id")
        if group_id is not None:
            return int(group_id), None
        user_id = current.get("user_id") or current.get("sender_id")
        if user_id is None:
            return "当前不在任何窗口里，请在聊天中调用或给出 target"
        return None, int(user_id)
    matched = _match_window.fullmatch(choice)
    if matched is None:
        return f"target 无法识别：{choice!r}。用 g<群号>、u<QQ号>，或留空表示当前窗口"
    kind, digits = matched.groups()
    if kind.lower() == "g":
        return int(digits), None
    return None, int(digits)


def _event(text: str, group_id, user_id) -> dict:
    """Build the inbound-shaped event that carries one self-issued command.

    WHY: 顶层 ``user_id`` **不动**，作者写在 ``sender.user_id`` 上——私聊窗口里顶层
    ``user_id`` 是**窗口对端**（Bot 自己发的话也带着对端的 id），拿它当作者会让重启回执
    发到错的窗口去。``sender.user_id`` 才是两种窗口下都指向作者的字段，与
    ``history.author``、``chat.msg2chat``、``op.is_op`` 同一条约定。
    """
    from mods import identity

    bot_id = identity.bot_id()
    event = {
        "time": int(time.time()),
        "self_id": bot_id,
        "post_type": "message",
        # 负数：真实 message_id 是正整数，撞不上；这里只是想留一个"不是 NapCat 发来的"
        # 痕迹。它进日志、进内存历史，但不足以让任何消费者改变行为，所以防回环不靠它。
        "message_id": -time.time_ns(),
        "message": text,
        "raw_message": text,
        "sender": {"user_id": bot_id, "nickname": identity.bot_name()},
        # 留给以后识别"这条是自己注入的"。目前没有消费者读它，别指望它挡回环。
        "_injected": True,
    }
    if group_id is not None:
        event.update({
            "message_type": "group",
            "sub_type": "normal",
            "group_id": group_id,
            "user_id": bot_id,
        })
    else:
        event.update({"message_type": "private", "sub_type": "friend", "user_id": user_id})
    return event


__all__ = ["send_command"]
