"""提供延时任务的新增与删除能力。"""

from mods import identity


def later_add(time: str, code: str, expr: str) -> str:
    """添加延时任务。

    @param
    time: 相对或绝对时间
    code: 到时先执行的代码
    expr: 到时求值并发送的表达式
    """
    from mods import later

    return later.run(f" add {time} {code}\n{expr}", exec_id=identity.bot_id())


def later_del(seqs: str) -> str:
    """按序号删除延时任务。

    @param
    seqs: 逗号分隔的序号或 *
    """
    from mods import later

    return later.run(f" del {seqs}", exec_id=identity.bot_id())


__all__ = ["later_add", "later_del"]
