"""向当前窗口或明确指定的群聊、私聊排队发送消息。

函数保留旧工具返回固定“已发送”的同步外观；该文本只表示消息已经交给
现有发送队列，不表示 OneBot 已确认送达。群和私聊目标互斥，省略两者时
沿用当前聊天窗口。
"""


def sendmsg(
    text: str,
    user_id: int | None = None,
    group_id: int | None = None,
) -> str:
    """排队发送一条消息。

    @param
    text: 要发送的消息
    user_id: 私聊目标 QQ 号；不能与 group_id 同时提供
    group_id: 群聊目标群号；不能与 user_id 同时提供
    """
    if user_id is not None and group_id is not None:
        raise ValueError("user_id 和 group_id 不能同时提供")

    from mods import message

    message.sendmsg(text, user_id=user_id, group_id=group_id)
    return "已发送"


__all__ = ["sendmsg"]
