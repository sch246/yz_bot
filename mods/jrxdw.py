"""Daily personalized small-animal identity."""

from __future__ import annotations

import random
import re
import time

from mods import bbxdw, context, cq, identity, storage, text
from mods.command import command


LOAD_AFTER = ("bbxdw", "image")
happys = [
    "＼(＾▽＾)／", "(≧▽≦)", "(｡♥‿♥｡)", "☆*:.｡.o(≧▽≦)o.｡.:*☆",
    "(￣▽￣)ノ", "(๑>ᴗ<๑)", "＼(＾０＾)／", "ヽ(＾Д＾)ﾉ", "(≧◡≦) ♡",
]
re_num = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)$")


def on_load(_ctx) -> None:
    from mods import is_available

    missing = [name for name in ("bbxdw", "identity", "image", "storage") if not is_available(name)]
    if missing:
        raise RuntimeError("jrxdw 依赖不可用: " + ", ".join(missing))
    bbxdw._state()


def fix(values):
    if not values:
        return None
    return [(value, 1.0) if isinstance(value, str) else tuple(value) for value in values]


def get_jrxdw(personal, animal_types: dict[str, float], prefix: list[str], suffix: list[str]) -> str:
    if not personal:
        animal = random.choice(list(animal_types))
    else:
        animals, weights = zip(*personal)
        animal = random.choices(animals, weights=weights)[0]
        if animal == "*":
            others = list(set(animal_types) - set(animals))
            animal = random.choice(others) if others else "虚空生物"
    final = animal
    while random.random() < animal_types.get(animal, 0):
        final += animal
    if not prefix and not suffix:
        return final
    side = random.choices(("prefix", "suffix"), weights=(len(prefix), len(suffix)))[0]
    return random.choice(prefix) + final if side == "prefix" else final + random.choice(suffix)


@command
def run(body: str) -> str:
    """抽取今日小动物并管理自己的候选偏好。

    格式：.jrxdw；list；me；set <动物 [权重]>...
    空参数抽取；list 仅群聊列出今日结果，me 查看个人设置，set 可用 * 或不带动物重置。
    """
    prefixes, suffixes, animal_types = bbxdw._state()
    if not animal_types:
        return "目前没有小动物，请先.bbxdw add 添加动物类型"
    event = context.current()
    user_id = event["user_id"]
    key = str(user_id)
    group_mode = "group_id" in event
    sender = identity.getname()
    date = time.strftime("%y-%m-%d")
    fool = time.strftime("%m-%d") == "04-01"
    user_data = identity.getstorage()
    personal = fix(user_data.get("animal_types"))
    user_data["animal_types"] = personal
    state = storage.get("", "jrxdw")
    daily = state.setdefault("dict", {})
    if state.get("date") != date:
        state["date"] = date
        daily.clear()

    if not body.strip():
        if key not in daily:
            daily[key] = get_jrxdw(personal, animal_types, prefixes, suffixes)
        number = list(daily).index(key) + 1
        if group_mode and fool:
            return (
                f"今日鸽子（第{number}只）是\n{sender}！\n{identity.headshot(user_id)}\n"
                f"今天柚子可以是{daily[key]}！{random.choice(happys)}"
            )
        prefix = f"今日小动物（第{number}只）是" if group_mode else "今日小动物是"
        return f"{prefix}\n{sender}！\n{identity.headshot(user_id)}\n今天你是{daily[key]}!"

    operation, rest = text.read_params(body)
    if operation == "list":
        if not group_mode:
            return "世界就是绕着你打转！"
        if not daily:
            return "今天还没有小动物呢"
        if fool:
            result = "\n".join(
                f"{cq.url2cq(f'http://q1.qlogo.cn/g?b=qq&nk={uid}&s=1')} {identity.getname(uid)}"
                for uid in daily
            )
            suffix = "\n\n今天真是鸽子大军呢..." if len(daily) > 5 else ""
            return f"今日鸽子（们）：\n{result}{suffix}"
        result = "\n".join(
            f"{cq.url2cq(f'http://q1.qlogo.cn/g?b=qq&nk={uid}&s=1')}   ↖{animal}"
            for uid, animal in daily.items()
        )
        suffix = "\n\n今天真是小动物大军呢..." if len(daily) > 5 else ""
        return f"今日小动物（们）：\n{result}{suffix}"
    if operation == "me":
        if not personal:
            return "你还没有设置自己的小动物！"
        return "你的小动物及其权重是：\n" + "\n".join(f"{name}: {weight}" for name, weight in personal)
    if operation == "set":
        args = rest.strip().split()
        if args:
            values: list[list] = []
            while args:
                animal = args.pop(0)
                if re_num.fullmatch(animal):
                    return run.__doc__ or ""
                if animal not in (*animal_types, "*"):
                    return f"不存在的动物 {animal}，请先通过 .bbxdw add 来添加动物"
                weight = float(args.pop(0)) if args and re_num.fullmatch(args[0]) else 1.0
                values.append([animal, weight])
            if not any(weight > 0 for _, weight in values):
                return "至少一个权重需要大于0"
            user_data["animal_types"] = values
            return "已设置你的小动物及其权重：\n" + "\n".join(
                f"{name}: {weight}" for name, weight in values
            )
        if user_data.get("animal_types"):
            del user_data["animal_types"]
            return "已重置你的小动物设置"
        return f"无需重置，本就没有设置小动物\n可设置种类:\n{list(animal_types)}"
    return run.__doc__ or ""
