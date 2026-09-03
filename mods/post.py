"""Temporarily redirect one chat window's OneBot API endpoint."""

import re

from mods import connect, context, history, op, text
from mods.command import command


# WHY: 有意的方向选择——post 写，connect 只读。映射本身住在 connect 里，因为出站
# 选路(_target_port)必须能用它；但改它的入口在这里。反过来让 connect 去 import post
# 会让 INFRA 依赖 FEATURE，破坏相位顺序。所以这不是"没做完的封装"，别收回去。
post_map = connect.post_map
LOAD_AFTER = ("op", "history", "connect")
re_int = re.compile(r"\d+$")
re_group = re.compile(r"g(\d+)$")
re_user = re.compile(r"u(\d+)$")


def on_load(_ctx) -> None:
    from mods import is_available

    missing = [name for name in ("op", "history", "connect") if not is_available(name)]
    if missing:
        raise RuntimeError("post 依赖不可用: " + ", ".join(missing))


def setport(value: str, group_id, user_id) -> str | None:
    if value == "":
        if group_id is not None:
            post_map.pop(f"g{group_id}", None)
            return "端口已重置"
        if user_id is not None:
            post_map.pop(f"u{user_id}", None)
            return "端口已重置"
        return None
    if not re_int.fullmatch(value):
        return None
    port = int(value)
    if not 1 <= port <= 65535:
        return "端口必须在1~65535之间"
    if group_id is not None:
        post_map[f"g{group_id}"] = port
    elif user_id is not None:
        post_map[f"u{user_id}"] = port
    else:
        return None
    return f"端口已设置为 {port}"


@command
def run(body: str) -> str | None:
    """修改 OneBot 测试出站端口映射。

    当前窗口：.post [端口]，不写端口时恢复默认。
    管理员：.post g<群号>|u<QQ号> [端口] 指定窗口；.post * 清空全部映射。端口范围 1..65535。
    """
    event = context.current()
    user_id, group_id = event.get("user_id"), event.get("group_id")
    operation, rest = text.read_params(body)
    if operation == "" or re_int.fullmatch(operation):
        return setport(operation, group_id, user_id)
    group_match, user_match = re_group.fullmatch(operation), re_user.fullmatch(operation)
    if operation == "*" or group_match or user_match:
        if not op.is_op(event):
            # 同 op.require_op 的提醒节流约定，理由写在那里。
            if not history.any_same(event, r"\.post"):
                return "权限不足(一定消息内将不再提醒)"
            return None
        if operation == "*":
            post_map.clear()
            return "所有端口已重置"
        value, _ = text.read_params(rest)
        if value == "" or re_int.fullmatch(value):
            if group_match:
                return setport(value, int(group_match.group(1)), None)
            return setport(value, None, int(user_match.group(1)))
    return run.__doc__
