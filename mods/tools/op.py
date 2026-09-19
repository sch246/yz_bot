"""以 Bot 自身的身份执行命令（含重启自己）；只在 op 发起的聊天轮里可见、可加载。

## 使用时机

`send_command` 把一条文本当作**自己发给自己的命令**送进 Bot 唯一的那条事件循环
（`connect._events`），由主线程按真实路由处理——命令、shell、link、聊天都和真人触发的一模
一样；工具调用本身立刻返回，不阻塞当前这轮生成。

- 每次投递都会先在目标窗口留一行「以 Bot 身份投递：<命令>」，无条件、你关不掉。它是窗口里
  的人唯一能看见这次操作的地方，所以不要因为"命令自己会有输出"就觉得它多余，也不要为了少
  一条消息而绕开这个工具。
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

import logging
import re
import time

# 门控声明：只有 op 发起的轮才看得到、加载得了这个模块。判据见 tools.op_tool_visible。
OP_ONLY = True

_log = logging.getLogger(__name__)

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
    _receipt(command, group_id, user_id)
    connect._events.put(_event(command, group_id, user_id))
    where = f"群{group_id}" if group_id is not None else f"私聊{user_id}"
    return f"已投递到{where}，由主循环执行：{command}"


def _receipt(command: str, group_id, user_id) -> None:
    """Leave one visible line in the target window before the command runs.

    WHY: 这条回执是**无条件**的，因为"以 Bot 身份执行一条命令"是唯一一处人在窗口里看不见
    发起者的动作。多数命令自己会留下痕迹（`.reboot` 的「重启中」、`!` 的输出），但那是命令
    的性质，不是这条路的性质：一条没有输出的命令就会成为一次无痕的操作。窗口里的人能看见
    它，才谈得上事后追问和制止。

    WHY: 先发回执再投递。两边各走各的队列，所以严格顺序保证不了；但回执先入队，正常情况下
    它排在命令自己的输出前面，而 `.reboot` 那种同步发完就退出的更是如此。

    WHY: 命令原文要 `cq.escape`。它是模型写的字符串，里面的 `[CQ:...]` 一旦原样发出去就会
    被当成真的 at、图片或回复——回执是给人看的记录，不该顺手替模型发一次动作。

    WHY: 不加 `#` 前缀。这条回执确实是 Bot 做过的事，应当和别的发言一样进聊天记录、也进
    下一轮上下文；`#` 是"不进模型上下文"的前缀，用在这里等于让它自己看不见自己干过什么。
    """
    from mods import cq, message

    destination = {"group_id": group_id} if group_id is not None else {"user_id": user_id}
    try:
        message.send(f"以 Bot 身份投递：{cq.escape(command)}", **destination)
    except Exception:
        # 回执发不出去不该让命令投递本身失败——那会让"能不能执行"取决于"能不能说话"。
        _log.exception("send_command 回执发送失败：%s", command)


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
        event.update({"message_type": "private", "sub_type": "friend", "user_id": user_id})
    return event


__all__ = ["send_command"]
