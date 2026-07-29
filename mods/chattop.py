"""Monthly LLM usage summaries."""

from __future__ import annotations

from datetime import datetime

from mods import context, identity, op, storage
from mods.command import command


_last_call: dict = {}


def _lines(usage: dict, user_ids: set[int] | None = None) -> tuple[float, list[str]]:
    total = 0.0
    lines = []
    for raw_user, value in sorted(usage.items(), key=lambda item: item[1][1], reverse=True):
        user_id = int(raw_user)
        if user_ids is not None and user_id not in user_ids:
            continue
        calls, cost = value
        total += cost
        lines.append(f"{identity.getname(user_id)}({user_id}): {calls} 次调用, 共 ￥{cost:.4f}")
    return total, lines


@command
def run(body: str):
    """查看指定月份的 LLM 使用费用。

    格式：.chattop [月份 1..12]
    默认当前月；群聊显示群成员，私聊管理员显示全部，普通用户只显示自己。
    """
    value = body.strip() or str(datetime.today().month)
    try:
        month = int(value)
    except ValueError:
        return run.__doc__
    if not 1 <= month <= 12:
        return run.__doc__
    usage = storage.get("usage", str(month))
    current_month = f"{datetime.today().year}-{datetime.today().month}"
    if _last_call.get("last_call") != current_month and month == datetime.today().month:
        usage.clear()
    _last_call["last_call"] = current_month
    event = context.current() or {}
    if event.get("group_id") is not None:
        members = {int(item["user_id"]) for item in identity.memberlist(event["group_id"])}
        total, lines = _lines(usage, members)
    elif op.is_op(event):
        total, lines = _lines(usage)
    else:
        total, lines = _lines(usage, {int(event["user_id"])})
    return f"总费用:￥{total:.4f}\n" + "\n".join(lines) if lines else "这个月没有使用记录"


def on_load(ctx) -> None:
    global _last_call
    from mods import is_available

    missing = [name for name in ("identity", "op", "storage") if not is_available(name)]
    if missing:
        raise RuntimeError("chattop requires available mods: " + ", ".join(missing))
    _last_call = storage.get("usage", "last_call")
    _last_call.setdefault("last_call", "")
