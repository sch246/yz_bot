"""Daily cat identity backed by the rebuilt 百变小猫 corpus."""

import random
import re
import time

from mods import context, cq, identity, storage, text
from mods.command import command


re_int = re.compile(r"-?\d+$")


LOAD_AFTER = ("identity", "image", "storage")


def on_load(_ctx) -> None:
    from mods import is_available

    missing = [name for name in ("identity", "image", "storage") if not is_available(name)]
    if missing:
        raise RuntimeError("jrxm 依赖不可用: " + ", ".join(missing))


def _daily_state(date: str) -> tuple[dict, list]:
    state = storage.get("", "jrxm")
    daily = state.setdefault("jrxm", [])
    if state.get("jrxm_date") != date:
        state["jrxm_date"] = date
        daily.clear()
    return state, daily


@command
def run(body: str) -> str:
    """抽取自己的今日小猫。

    格式：.jrxm [序号|list]
    空参数抽取；群聊中数字按当日抽取顺序查看第 N 只，list 列出今日结果；私聊只支持空参数。
    """
    event = context.current()
    user_id = event["user_id"]
    group_mode = event.get("group_id") is not None
    sender = identity.getname()
    try:
        with open("data/bbxm.txt", encoding="utf-8") as file:
            lines = [line.strip() for line in file if line.strip()]
    except OSError as error:
        return f"百变小猫语料不可用: {error}"
    if not lines:
        return "百变小猫语料为空，请先运行 .bbxm"
    date = time.strftime("%y-%m-%d")
    _state, daily = _daily_state(date)
    user_data = identity.getstorage()

    if not body.strip():
        if user_data.get("jrxm_date") != date:
            user_data["jrxm_date"] = date
            user_data["jrxm"] = random.choice(lines)
        if user_id not in daily:
            daily.append(user_id)
        number = daily.index(user_id) + 1
        result = user_data["jrxm"]
        if group_mode and time.strftime("%m-%d") == "04-01":
            return (
                f"今日鸽子（第{number}只）是\n{sender}！\n{identity.headshot(user_id)}\n"
                "今天只有柚子是小猫！www！"
            )
        prefix = f"今日小猫（第{number}只）是" if group_mode else "今日小猫是"
        return f"{prefix}\n{sender}！\n{identity.headshot(user_id)}\n今天{result}!"

    operation, _rest = text.read_params(body)
    fool = time.strftime("%m-%d") == "04-01"
    if re_int.fullmatch(operation):
        if not group_mode:
            return "世界就是绕着你打转！"
        index = int(operation)
        noun = "鸽子" if fool else "小猫"
        if index < 1 or index > len(daily):
            return f"今天还没有{noun}呢" if index == 1 else f"这里还没有{noun}呢"
        selected = daily[index - 1]
        if fool:
            return (
                f"今天第{index}只鸽子是\n{identity.getname(selected)}！\n"
                f"{identity.headshot(selected)}\n是什么鸽子呢？"
            )
        description = str(identity.getstorage(selected).get("jrxm", "")).replace("你", "它")
        return (
            f"今天第{index}只小猫是\n{identity.getname(selected)}！\n"
            f"{identity.headshot(selected)}\n↑今天{description}"
        )
    if operation == "list":
        if not group_mode:
            return "世界就是绕着你打转！"
        if not daily:
            return "今天还没有小猫呢"
        if fool:
            result = "\n".join(
                f"{cq.url2cq(f'http://q1.qlogo.cn/g?b=qq&nk={uid}&s=1')} {identity.getname(uid)}"
                for uid in daily
            )
            suffix = "\n\n今天真是鸽子大军呢..." if len(daily) > 5 else ""
            return f"今日鸽子（们）：\n{result}{suffix}"
        result = "\n".join(
            f"{cq.url2cq(f'http://q1.qlogo.cn/g?b=qq&nk={uid}&s=1')}   ↖"
            f"{str(identity.getstorage(uid).get('jrxm', '')).replace('你', '它')[2:]}"
            for uid in daily
        )
        suffix = "\n\n今天真是小猫大军呢..." if len(daily) > 5 else ""
        return f"今日小猫（们）：\n{result}{suffix}"
    return run.__doc__ or ""
