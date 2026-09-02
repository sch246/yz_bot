"""读写按 QQ 号保存的用户数据，用来跨对话记住关于某个人的信息。

每个用户对应一份持久化的 dict，键由使用方自己约定，跨会话、跨群、跨重启保留。`name` 是全局约定的键，保存该用户的自定义称呼，Bot 其它地方（如 `getname`）会读它；其余键可以自由命名，写之前先 `get_user_data` 看一眼现有内容，别覆盖别人写的东西。

权限：任何人都能改自己的数据；改别人的数据需要管理员，否则返回"权限不足"。查询不限权限。

`set_user_data` 的 `value` 按 Python 字面量解析（`ast.literal_eval`），不是纯文本：

- 字符串必须自带引号，写称呼是 `'小明'` 而不是 `小明`；
- 数字 `18`、布尔 `True`、列表 `['a', 'b']`、字典 `{'k': 1}` 都可以；
- 传字符串 `del`（不带引号）表示删除这个键；
- 语法不合法会抛异常，错误文本会回传，改好引号再试。

这里存的是普通用户资料，不要往里塞聊天记录全文、密钥或长文本。
"""

import ast

from mods import context, identity, op


def get_user_data(user_id: int) -> str:
    """查询某个用户的全部持久化数据，返回 dict 的字符串形式；没有数据时是 {}。

    @param
    user_id: 目标用户 QQ 号
    """
    return str(identity.getstorage(user_id))


def set_user_data(user_id: int, key: str, value: str) -> str:
    """写入或删除某个用户的一个数据键，成功返回 done。

    @param
    user_id: 目标用户 QQ 号；不是自己时需要管理员权限
    key: 数据键名，例如 name
    value: Python 字面量形式的新值，字符串要带引号（如 '小明'）；传 del 表示删除该键
    """
    current = context.current() or {}
    if int(user_id) != int(current.get("user_id", -1)) and not op.require_op(current):
        return "权限不足"
    target = identity.getstorage(user_id)
    if value == "del":
        target.pop(key, None)
    else:
        target[key] = ast.literal_eval(value)
    return "done"


__all__ = ["get_user_data", "set_user_data"]
