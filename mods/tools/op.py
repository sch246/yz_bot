"""以你自己的名义往主循环里发指令（包括重启你自己）；只有当你拥有 op 权限时才会加载。

## 怎么用它

`send_command` 就像是**你亲手在聊天框里给自己敲了一条指令**，直接送进最底层的事件主循环里去跑——不管是跑 `.` 开头的系统命令、shell、还是发话，效果都跟真人在窗口里触发的一模一样；而工具调用本身会立刻返回，不会卡住你当前的思绪。

- **怎么重启你自己（`.reboot`）**：
  改完底层代码想重启生效时，**必须用这个工具发 `.reboot`**（如果在 `exec_code` 里直接写 `SystemExit(233)`，只会把干活的子线程掐死，主进程根本不会重启）。
  而且发了 `.reboot` 之后，**不用自己再多嘴发一句"我先重启啦"**——因为 `.reboot` 命令本身就会自动往窗口里发一条「重启中」，等你醒来又会自动发「重启完成」，你自己再发就重样啦。
- **想悄悄执行、不留进上下文**：
  默认情况下，你通过它发的指令也会留在聊天记录和后续的上下文里。如果某条后台操作指令你不想让它占地方、不想在下一轮上下文里又看到它，只要在 `text` 最前面加个 `#` 号（比如 `#reflect now`）就行——`#` 开头是咱们这儿通用的"对模型隐身"暗号。"""

from __future__ import annotations

import re
import time

# 门控声明：只有 Bot 获得 op 权限时才看得到、加载得了这个模块。
BOT_OP_ONLY = True

_match_window = re.compile(r"^([guGU]?)([0-9]+)$")


def send_command(text: str, target: str = "") -> str:
    """以 Bot 自己的身份执行一条命令，走真实路由；立刻返回投递结果，不等它跑完。

    @param
    text: 命令原文，例如 .reboot、.chattop、!ls、#limit；首字符决定它走哪条路
    target: 这条命令属于哪个窗口：g<群号>、u<QQ号>，留空表示当前窗口
    """
    from mods import connect, context, identity, op

    current = context.current() or {}
    if not op.bot_is_op():
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
        target_id = current.get("target_id")
        if target_id is None:
            return "当前不在任何窗口里，请在聊天中调用或给出 target"
        return None, int(target_id)
    matched = _match_window.fullmatch(choice)
    if matched is None:
        return f"target 无法识别：{choice!r}。用 g<群号>、u<QQ号>，或留空表示当前窗口"
    kind, digits = matched.groups()
    if kind.lower() == "g":
        return int(digits), None
    return None, int(digits)


def _event(text: str, group_id, user_id) -> dict:
    """Build the inbound-shaped event that carries one self-issued command.

    WHY: 顶层 ``user_id`` 是**作者**，两种窗口下都一样；私聊的窗口写在 ``target_id``
    上（与实时事件、与 ``history.window``、``message.target`` 同一条约定）。这条注入的
    作者是 Bot 自己——``send_command`` 就是以 Bot 自己的身份执行——所以 ``user_id``
    写 ``bot_id``，窗口另写 ``target_id``。两者分得开，重启回执才回得到原窗口。

    WHY: ``post_type`` 写 ``"message"`` 同样是**承重**的，不是照抄 inbound 形状。
    ``bot._route`` 开头按 ``post_type == "message_sent"`` 把自发消息整段跳过，而这里伪造
    的事件和真正的回声只差这一个字段——两者的 ``sender.user_id`` 都是 Bot 自己。改成
    ``"message_sent"``（比如为了"更诚实地标注这是自己发的"）会让注入的命令在那道关卡处被
    丢掉，``send_command`` 连同它唯一支撑的那条真重启路径一起**静默**失效：投递照样回
    "已投递"，命令永远不执行。反过来那道关卡也只能按 ``post_type`` 判，换成"作者是不是
    Bot"就会连这条路一起挡掉。删除条件：``bot._route`` 不再按 ``post_type`` 分自发消息。
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
        event.update(
            {
                "message_type": "private",
                "sub_type": "friend",
                "user_id": bot_id,
                "target_id": user_id,
            }
        )
    return event


__all__ = ["send_command"]
