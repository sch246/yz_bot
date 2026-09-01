"""查询今天的农历日期并计算当前时辰的小六壬结果。

实际历法和六态算法继续由 ``mods.lunar`` 持有，本模块只恢复旧 LLM
工具的无参数包装，不暴露底层历史上未生效的 offset 参数。
"""


def lunar_date() -> str:
    """获取今天的农历日期。"""
    from mods import lunar

    return str(lunar.lunar_time())


def xiaoliu() -> str:
    """计算当前时辰的小六壬结果。"""
    from mods import lunar

    return lunar.小六壬()


__all__ = ["lunar_date", "xiaoliu"]
