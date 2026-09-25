"""Monthly LLM usage summaries."""

from __future__ import annotations

from datetime import datetime

from mods import chat, context, identity, op, storage
from mods.command import command

# WHY: 月份键由 chat.usage_name 统一生成——写入(chat)和读取(chattop)必须是同一份口径，
# 否则数据会落到两个文件里。这条依赖也必须在加载期成立：chattop 与 chat 同为 FEATURE
# 阶段，没有这条边时 chattop 的 indegree 为 0，会排在 chat 前面，on_load 时 chat 还没进
# available。INFRA 阶段的那些模块(identity/op/storage)靠阶段顺序天然在前，不需要声明。
LOAD_AFTER = ("chat",)


def _is_entry(value) -> bool:
    """Whether *value* is a ``[calls, cost]`` pair, not junk from an old run."""
    return isinstance(value, (list, tuple)) and len(value) == 2


def _lines(usage: dict, user_ids: set[int] | None = None) -> tuple[float, list[str]]:
    total = 0.0
    lines = []
    ordered = sorted(
        usage.items(),
        key=lambda item: item[1][1] if _is_entry(item[1]) else 0.0,
        reverse=True,
    )
    for raw_user, value in ordered:
        if not _is_entry(value):
            continue
        try:
            user_id = int(raw_user)
        except (TypeError, ValueError):
            continue
        if user_ids is not None and user_id not in user_ids:
            continue
        calls, cost = value
        total += cost
        lines.append(f"{identity.getname(user_id)}({user_id}): {calls} 次调用, 共 ￥{cost:.4f}")
    return total, lines


def _month(body: str) -> str | None:
    """Resolve the ``.chattop`` argument into a ``YYYY-MM`` storage name."""
    value = body.strip().replace("/", "-")
    today = datetime.today()
    if not value:
        return chat.usage_name()
    parts = value.split("-")
    try:
        if len(parts) == 1:
            year, month = today.year, int(parts[0])
        elif len(parts) == 2:
            year, month = int(parts[0]), int(parts[1])
        else:
            return None
    except ValueError:
        return None
    if not 1 <= month <= 12:
        return None
    return f"{year}-{month:02d}"


@command
def run(body: str):
    """查看指定月份的 LLM 使用费用。

    格式：.chattop [月份]，月份为 1..12（指今年）或 YYYY-MM；默认当前月。
    群聊显示群成员，私聊管理员显示全部，普通用户只显示自己。
    """
    name = _month(body)
    if name is None:
        return run.__doc__
    usage = storage.get("usage", name)
    event = context.current() or {}
    if event.get("group_id") is not None:
        members = {int(item["user_id"]) for item in identity.memberlist(event["group_id"])}
        members.add(identity.bot_id())
        total, lines = _lines(usage, members)
    elif op.is_op(event):
        total, lines = _lines(usage)
    else:
        total, lines = _lines(usage, {int(event["user_id"])})
    if not lines:
        return f"「{name}」没有使用记录"
    return f"「{name}」总费用:￥{total:.4f}\n" + "\n".join(lines)


def on_load(ctx) -> None:
    from mods import is_available

    missing = [
        name for name in ("chat", "identity", "op", "storage")
        if not is_available(name)
    ]
    if missing:
        raise RuntimeError("chattop requires available mods: " + ", ".join(missing))
