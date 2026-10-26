"""按 QQ 号翻看或记下某个人的小档案（跨群、跨重启都会一直留着）。

每个人名下都有一个小字典（dict）。其中 `name` 是全系统通用的键，存的是大家自定义的称呼（很多地方认人都靠读它）；其他的键名可以自由约定。
因为不同模块可能都会往这个小字典里存东西，所以**动手改之前，一定先调 `get_user_data` 看一眼里面原来有什么**，别莽撞地把别人存好的字段给覆盖掉了。

## 写入时的格式规矩（`set_user_data`）

`value` 参数是按 **Python 字面量**（`ast.literal_eval`）来解析的，不是普通纯文本：
- **存文字（字符串）必须自己套上引号**！比如存称呼要写 `"'小明'"`，要是漏了里面那层引号直接写 `"小明"`，Python 会报错说语法不对；
- 存数字 `18`、布尔值 `True`、列表 `['a', 'b']`、字典 `{'k': 1}` 都照 Python 写法来；
- 想删掉某个键，直接传不带引号的 `del` 三个字母就行。

这里只放精炼的小档案（称呼、偏好开关等），长篇的经历和随口聊的事请记进那个人的 `person:<QQ号>` 笔记里，别往这个小字典里硬塞长文或密钥。"""

import ast

from mods import identity, op


def get_user_data(user_id: int) -> str:
    """查询某个用户的全部持久化数据，返回 dict 的字符串形式；没有数据时是 {}。

    @param
    user_id: 目标用户 QQ 号
    """
    return str(identity.getstorage(user_id))


def set_user_data(user_id: int, key: str, value: str) -> str:
    """写入或删除某个用户的一个数据键，成功返回 done。

    @param
    user_id: 目标用户 QQ 号；修改需要 Bot 自身拥有 op 权限
    key: 数据键名，例如 name
    value: Python 字面量形式的新值，字符串要带引号（如 '小明'）；传 del 表示删除该键
    """
    if not op.bot_is_op():
        return "权限不足"
    target = identity.getstorage(user_id)
    if value == "del":
        target.pop(key, None)
    else:
        target[key] = ast.literal_eval(value)
    return "done"


__all__ = ["get_user_data", "set_user_data"]
