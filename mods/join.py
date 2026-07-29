"""Group participation list and random selection."""

from random import choice

from mods import identity
from mods.command import command, grouponly, params
from mods.message import sendmsg


LOAD_AFTER = ("identity",)


def on_load(_ctx) -> None:
    from mods import is_available

    if not is_available("identity"):
        raise RuntimeError("join 依赖的 identity 不可用")


def members(values: list[int]) -> str:
    return "\n".join(f"- {identity.getname(user_id)} ({user_id})" for user_id in values)


@command
@params
@grouponly
def run(msg, operation, size_text, _last, _last_lines):
    """在群聊中发起、参加并随机完成组队。

    格式：.join start [总人数]；.join；.join end
    默认总人数为 10；成员依次发送 .join 参加，满员自动抽取，end 可提前结束。
    """
    state = identity.getgroupstorage().setdefault("join", {})
    if operation == "start":
        try:
            size = int(size_text) if size_text else 10
        except ValueError:
            return "人数需要是整数"
        if size < 1:
            return "总人数至少为1"
        state.update({"size": size, "count": size, "lst": []})
        return "请参加人员依次输入.join"
    if not operation:
        if state.get("size") is None:
            return "请先输入.join start"
        user_id = msg["user_id"]
        if user_id in state["lst"]:
            return "已在列表中！"
        state["count"] -= 1
        state["lst"].append(user_id)
        sendmsg(
            f"已加入！\n当前人数: {len(state['lst'])} / {state['size']}\n"
            + members(state["lst"])
        )
        if state["count"] == 0:
            selected = choice(state["lst"])
            sendmsg("人数已满！")
            sendmsg(f"随机出的人选是: {identity.getname(selected)} ({selected})！")
            state.clear()
        return None
    if operation == "end":
        values = state.get("lst", [])
        if not values:
            state.clear()
            return "已取消"
        selected = choice(values)
        sendmsg("人数已定！")
        sendmsg(f"随机出的人选是: {identity.getname(selected)} ({selected})！")
        state.clear()
        return None
    return run.__doc__
