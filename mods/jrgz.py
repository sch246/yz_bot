"""Daily random group pigeon."""

import time

from mods import identity
from mods.command import command
from mods.randoms import getran


LOAD_AFTER = ("identity",)


def on_load(_ctx) -> None:
    from mods import is_available

    if not is_available("identity"):
        raise RuntimeError("jrgz 依赖的 identity 不可用")


@command
def run(_body: str) -> str:
    """从当前群成员中选出今日鸽子。

    格式：.jrgz
    仅群聊可用；每个群每天共享同一位结果。
    """
    date = time.strftime("%y-%m-%d")
    try:
        data = identity.getgroupstorage()
    except ValueError:
        return "不支持私聊"
    if data.get("jrgz_date") != date:
        members = identity.memberlist()
        if not members:
            return "群成员列表为空"
        data["jrgz_date"] = date
        data["jrgz"] = getran(members)["user_id"]
    user_id = data["jrgz"]
    return (
        f"今日鸽子（1/1）\n{identity.getname(user_id)}\n{identity.headshot(user_id)}\n"
        "恭喜这位鸽子，今天你可以光明正大的咕咕咕啦！"
    )
