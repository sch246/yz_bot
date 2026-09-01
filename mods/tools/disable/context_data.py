"""读取动态 Python 环境共用的持久数据字典。

该函数保留旧 ``read_data`` 工具的查询语义。数据仍由 ``storage`` 的
``py_data`` 键唯一持有；这里不复制或缓存第二份表示。
"""


def read_data(key: str) -> object:
    """按键读取动态环境的 data 内容。

    @param
    key: 要查询的键
    """
    from mods import storage

    return storage.get("", "py_data").get(key, "没有找到内容")


__all__ = ["read_data"]
