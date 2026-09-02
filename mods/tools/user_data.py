"""提供用户数据的查询与编辑能力。"""

import ast

from mods import context, identity, op


def get_user_data(user_id: int) -> str:
    """查询用户数据。

    @param
    user_id: 用户 QQ 号
    """
    return str(identity.getstorage(user_id))


def set_user_data(user_id: int, key: str, value: str) -> str:
    """编辑用户数据。

    @param
    user_id: 用户 QQ 号
    key: 数据键
    value: Python 字面量，del 表示删除
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
