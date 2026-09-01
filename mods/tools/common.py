"""提供当前时间和聊天内戳一戳能力。"""

import time

from mods import connect, context


def get_time() -> str:
    """获取当前时间。"""
    return time.strftime("现在是%Y年%m月%d日%H时%M分%S秒")


def poke(user_id: int) -> str:
    """戳一戳当前聊天中的用户。

    @param
    user_id: 目标用户 QQ 号
    """
    event = context.current() or {}
    group_id = event.get("group_id")
    if group_id is None and int(user_id) != int(event.get("user_id", -1)):
        return "戳一戳失败：私聊中只能戳当前对话者"
    params = {"user_id": int(user_id)}
    if group_id is not None:
        params["group_id"] = int(group_id)
    result = connect.call_api("send_poke", **params)
    return f"已戳用户 {user_id}" if result.get("retcode") == 0 else f"戳一戳失败：{result.get('wording', '接口失败')}"


__all__ = ["get_time", "poke"]
