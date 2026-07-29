"""Daily spouse draw within a group."""

import time

from mods import context, identity, storage
from mods.command import command
from mods.randoms import getran


LOAD_AFTER = ("storage", "identity")
jrlp_settings: dict | None = None


def on_load(_ctx) -> None:
    global jrlp_settings
    from mods import is_available

    missing = [name for name in ("storage", "identity") if not is_available(name)]
    if missing:
        raise RuntimeError("jrlp 依赖不可用: " + ", ".join(missing))
    jrlp_settings = storage.get("jrlp", "settings")


@command
def run(_body: str) -> str:
    """在群聊中抽取自己今天的老婆。

    格式：.jrlp
    每个用户、每个群、每天固定一次；候选为当前群内除自己外的成员，部分群可由配置禁用。
    """
    event = context.current()
    group_id, user_id = event.get("group_id"), event.get("user_id")
    if group_id is None:
        return "不支持私聊"
    settings = jrlp_settings
    if settings is None:
        raise RuntimeError("今日老婆设置尚未加载")
    disabled = settings.get("disabled_groups")
    if not isinstance(disabled, list) or not all(type(item) is int and item > 0 for item in disabled):
        return (
            "未配置今日老婆禁用群：请设置 "
            "storage.get('jrlp', 'settings')['disabled_groups'] 为群号列表"
        )
    if group_id in disabled:
        return "该群已禁用这个功能"
    date = time.strftime("%y-%m-%d")
    group_data = identity.getgroupstorage()
    data = group_data.setdefault(user_id, {})
    if data.get("jrlp_date") != date:
        candidates = [member for member in identity.memberlist() if member["user_id"] != user_id]
        if not candidates:
            return "群内没有可抽取的其他成员"
        data["jrlp_date"] = date
        data["jrlp"] = getran(candidates)["user_id"]
    spouse = data["jrlp"]
    return f"[CQ:at,qq={user_id}]今天的老婆是\n{identity.headshot(spouse)}\n{identity.getname(spouse)}！"
