"""提供当前时间和聊天内戳一戳能力。

`get_time` 读的是 Bot 宿主机的本地时间，是唯一可信的"现在"。凡是涉及今天几号、现在几点、距某天还有多久、最近的节日或纪念日，都先调用它再计算，不要用训练数据里的日期。

`poke` 发出 QQ 的戳一戳动作：中心会话须明确指定窗口；群聊里可以戳群里任意成员，私聊里只能戳对话者。它不发送文字，也拿不到对方的反应；想让对方看到内容还是要正常回复。
"""

import time

from mods import connect, context


def get_time() -> str:
    """获取 Bot 所在机器的当前本地时间，返回形如"现在是2026年09月02日15时04分05秒"的中文字符串。"""
    return time.strftime("现在是%Y年%m月%d日%H时%M分%S秒")


def poke(user_id: int, target: str = "") -> str:
    """在明确目标窗口里戳一戳指定用户，成功返回"已戳用户 <QQ号>"。

    @param
    user_id: 目标用户 QQ 号；群聊里可以是任意群成员，私聊里只能是当前对话者
    target: 中心会话必须明确给 g<群号> 或 u<私聊对端号>；私有会话可留空
    """
    if context.agent_mode():
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
