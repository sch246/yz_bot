"""Master/op trust-domain membership and the ``.op`` command."""

from __future__ import annotations

from itertools import chain
import re
from typing import Any

from mods import INFRA
from mods import config, history
from mods.command import command


PHASE = INFRA
LOAD_AFTER = ("identity",)

ops: list[int] = []
_match_at = re.compile(r"\[CQ:at,qq=([0-9]+)\]$")
_match_qq = re.compile(r"[0-9]+$")


def _current() -> dict[str, Any]:
    from mods import context

    current = getattr(context, "current", None)
    if callable(current):
        return current()
    thismsg = getattr(context, "thismsg", None)
    if callable(thismsg):
        return thismsg()
    raise RuntimeError("context 未提供当前消息接口")


def is_op(user_or_msg: int | dict[str, Any]) -> bool:
    user_id = user_or_msg.get("user_id") if isinstance(user_or_msg, dict) else user_or_msg
    return user_id is not None and int(user_id) in ops


def require_op(msg: dict[str, Any] | None = None, remind: bool = True) -> bool:
    """Return whether this event is in the host-level trusted op domain."""
    if msg is None:
        msg = _current()
    if is_op(msg):
        return True
    # WHY: 这是全仓库的提醒节流约定——同一窗口最近若干条里已经出现过同类尝试，就不再
    # 重复提醒，否则一个人连点几次会把群刷满。判据是聊天记录而不是计时器或计数器，因为
    # 记录本来就在，不需要再引入一份状态。post.py 的 .post 提醒用的是同一个模式。
    if remind and not history.any_same(msg, r"^(?:!|\.op)"):
        from mods import message

        group_id = msg.get("group_id")
        user_id = None if group_id is not None else msg.get("user_id")
        message.send(
            "权限不足(一定消息内将不再提醒)",
            user_id=user_id,
            group_id=group_id,
        )
    return False


def get_uid(value: str) -> int | None:
    at = _match_at.fullmatch(value)
    if at:
        return int(at.group(1))
    if _match_qq.fullmatch(value):
        return int(value)
    return None


def get_uids_from_body(body: str) -> list[str]:
    return list(
        filter(
            str.strip,
            chain.from_iterable(line.split() for line in body.splitlines()),
        )
    )


def _save() -> None:
    config.save_config(ops, "ops")


@command
def run(body: str) -> str | None:
    """添加或移除 Bot 管理员（仅现有管理员）。

    格式：.op [del] (<QQ号>|<@某人>)+
    目标可用空格或换行分隔；首位 master 不能通过 del 移除。
    """
    msg = _current()
    if not is_op(msg):
        if not history.any_same(msg, r"\.op"):
            return "权限不足(一定消息内将不再提醒)"
        return None
    body = body.strip()
    if not body:
        return run.__doc__

    deleting = body.startswith("del") and (len(body) == 3 or body[3].isspace())
    if deleting:
        body = body[3:].strip()
    success: list[int] = []
    failures: list[str] = []
    for raw in get_uids_from_body(body):
        user_id = get_uid(raw)
        if user_id is None:
            failures.append(f"{raw}:格式错误")
        elif deleting and user_id == ops[0]:
            failures.append(f"{user_id}:不能移除master")
        elif deleting and user_id not in ops:
            failures.append(f"{user_id}:不是op")
        elif not deleting and user_id in ops:
            failures.append(f"{user_id}:已是op")
        elif deleting:
            ops.remove(user_id)
            success.append(user_id)
        else:
            ops.append(user_id)
            success.append(user_id)
    _save()
    return f"执行完毕,成功:{success}，失败:{failures}"


def on_load(_ctx: dict[str, Any] | None = None) -> None:
    global ops
    loaded = config.load_config("ops")
    if not isinstance(loaded, list) or not loaded:
        raise ValueError("config.ops 必须是包含 master 的非空列表")
    ops = [int(user_id) for user_id in loaded]
