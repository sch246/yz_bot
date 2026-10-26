"""看一眼现在的准确时间，或者在聊天里伸手戳一戳别人。

- **看表（`get_time`）**：读的是你所在宿主机的真实时钟，这是唯一准的"现在"。只要聊到今天几号、星期几、现在几点、离某个日子还有多久，都先调它看一眼表再开口，千万别拿训练数据里的旧日历去算。
- **戳一戳（`poke`）**：在 QQ 里轻轻戳对方一下（群聊可以戳群里的任何人，私聊就是戳对面的朋友）。记得写明窗口目标；戳一戳只是个小动作，不带文字，对方回戳或反应也不会自动弹给你，有话想说还是要正常发消息哦。"""

import time

from mods import connect, context


def get_time() -> str:
    """获取 Bot 所在机器的当前本地时间，返回形如"现在是2026年09月02日15时04分05秒"的中文字符串。"""
    return time.strftime("现在是%Y年%m月%d日%H时%M分%S秒")


def poke(user_id: int, target: str = "") -> str:
    """在明确目标窗口里戳一戳指定用户，成功返回"已戳用户 <QQ号>"。

    @param
    user_id: 目标用户 QQ 号；群聊里可以是任意群成员，私聊里只能是当前对话者
    target: g<群号> 或 u<私聊对端号>；显式目标优先，中心会话必须填写
    """
    if target or context.agent_mode():
        from mods import chat

        try:
            window = chat.parse_target(target)
        except ValueError as error:
            return str(error)
        group_id = window[1] if window[0] == "group" else None
        peer_id = window[1] if window[0] == "private" else None
    else:
        event = context.current() or {}
        group_id = event.get("group_id")
        peer_id = event.get("target_id") or event.get("user_id")
    if group_id is None and int(user_id) != int(peer_id or -1):
        return "戳一戳失败：私聊中只能戳当前对话者"
    params = {"user_id": int(user_id)}
    if group_id is not None:
        params["group_id"] = int(group_id)
    result = connect.call_api("send_poke", **params)
    return f"已戳用户 {user_id}" if result.get("retcode") == 0 else f"戳一戳失败：{result.get('wording', '接口失败')}"


__all__ = ["get_time", "poke"]
