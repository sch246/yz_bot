"""列出并修改当前聊天窗口中的一次性延时任务。

``later_set`` 只修改时间和最终求值表达式。旧工具曾声明 ``code`` 参数，
但实现从未把它传给命令，当前 ``mods.later`` 也没有独立 code 阶段；这里
删除该无效参数，避免向模型承诺不存在的行为。
"""


def later_list() -> str:
    """列出当前聊天窗口的延时任务。"""
    from mods import identity, later

    return later.run("", exec_id=identity.bot_id())


def later_set(sequence: int, time: str, expr: str) -> str:
    """按序号修改延时任务的时间和表达式。

    @param
    sequence: 延时任务序号
    time: 相对或绝对时间
    expr: 到时求值并发送的 Python 表达式
    """
    from mods import identity, later

    return later.run(
        f" set {sequence} {time} {expr}",
        exec_id=identity.bot_id(),
    )


__all__ = ["later_list", "later_set"]
