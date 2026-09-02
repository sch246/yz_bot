"""提供延时任务的新增与删除能力。

延时任务最终执行一段 Python body：先执行 ``code``，再求值并发送
``expr``。普通提醒也要把 ``expr`` 写成带引号的 Python 字符串。
"""


def later_add(time: str, code: str, expr: str) -> str:
    """添加延时任务，成功时返回任务序号、执行时间和表达式。

    @param
    time: 执行时间；支持 10s、5m、2h、1d、1M 等相对时间，以及 HH:MM、MM-DD HH:MM、YYYY-MM-DD HH:MM:SS 等绝对时间
    code: 到时在 expr 之前执行的 Python 代码；不需要时传空字符串；非空代码仅当前发送者为管理员时可用
    expr: 到时求值并发送的 Python 表达式；普通提醒必须传带引号的字符串表达式，例如 '提醒我喝水'
    """
    from mods import later

    return later.run(f" add {time} {code}\n{expr}")


def later_del(seqs: str) -> str:
    """按序号删除延时任务。

    @param
    seqs: 一个任务序号、逗号分隔的多个序号，或 * 表示删除当前聊天中的全部任务
    """
    from mods import later

    return later.run(f" del {seqs}")


__all__ = ["later_add", "later_del"]
