"""列出并修改当前聊天窗口中的一次性延时任务。

操作沿用当前消息发送者权限，任务仍属于当前聊天窗口。
"""


def later_list() -> str:
    """列出当前聊天窗口的延时任务。"""
    from mods import later

    return later.run("")


def later_set(sequence: int, time: str, expr: str) -> str:
    """按序号修改延时任务的时间和表达式。

    @param
    sequence: 延时任务序号
    time: 相对或绝对时间
    expr: 到时求值并发送的 Python 表达式
    """
    from mods import later

    return later.run(f" set {sequence} {time} {expr}")


__all__ = ["later_list", "later_set"]
