"""Update and configure the 百变小动物 corpus."""

import os
import re

from mods import pages, storage, text
from mods.bbx import phrase_allowed, source_log_dir
from mods.command import command
from mods.message import sendmsg


LOAD_AFTER = ("bbx", "storage")
prefix: list[str] | None = None
suffix: list[str] | None = None
animal_types: dict[str, float] | None = None


def on_load(_ctx) -> None:
    global prefix, suffix, animal_types
    from mods import is_available

    missing = [name for name in ("bbx", "storage") if not is_available(name)]
    if missing:
        raise RuntimeError("bbxdw 依赖不可用: " + ", ".join(missing))
    prefix = storage.get("bbxdw", "prefix", list)
    suffix = storage.get("bbxdw", "suffix", list)
    animal_types = storage.get("bbxdw", "animal_types")


def _state() -> tuple[list[str], list[str], dict[str, float]]:
    if prefix is None or suffix is None or animal_types is None:
        raise RuntimeError("百变小动物状态尚未加载")
    return prefix, suffix, animal_types


@command
def run(body: str):
    """管理“百变小动物”类型并重建语料。

    格式：.bbxdw [add <动物>|set <动物> <叠词概率>|del <动物>|list]
    无参数从配置的聊天日志重建前后缀；叠词概率范围为 0（含）到 1（不含）。
    """
    prefixes, suffixes, animals = _state()
    operation, rest = text.read_params(body)
    if operation == "add":
        animal, _ = text.read_params(rest)
        if animal:
            if animal in animals:
                return f"{animal} 已存在"
            animals[animal] = 0
            return f"已添加 {animal}"
    elif operation == "set":
        animal, rest = text.read_params(rest)
        if animal:
            if animal not in animals:
                animals[animal] = 0
                sendmsg(f"{animal} 不存在，已创建")
            chance, _ = text.read_params(rest)
            if text.is_num(chance):
                value = float(chance)
                if not 0 <= value < 1:
                    return "叠词概率必须在0~1内，且不能等于1"
                animals[animal] = value
                return f"设置成功: {animal} {value}"
    elif operation == "del":
        animal, _ = text.read_params(rest)
        if animal:
            if animal not in animals:
                return f"{animal} 不存在"
            del animals[animal]
            return f"已删除 {animal}"
    elif operation == "list":
        if not animals:
            return "目前没有小动物"
        return pages.display(
            [f"{name}: {chance}" if chance > 0 else name for name, chance in animals.items()],
            page_size=20,
        )
    elif not body.strip():
        if not animals:
            return "目前没有小动物"
        animal_pattern = "|".join(
            f"{re.escape(name)}+" if len(name) == 1 else re.escape(name)
            for name in animals
        )
        animal_pattern = f"(?:{animal_pattern})"
        before = re.compile(rf"我是(.{{0,16}}?){animal_pattern}")
        after = re.compile(rf"我是{animal_pattern}(.{{0,9}})")
        before_results: list[str] = []
        after_results: list[str] = []
        try:
            log_dir = source_log_dir()
            for root, _, files in os.walk(log_dir):
                for filename in files:
                    with open(os.path.join(root, filename), encoding="utf-8") as file:
                        content = file.read()
                    before_results.extend(
                        match.strip()
                        for match in before.findall(content)
                        if phrase_allowed(match, 16)
                    )
                    after_results.extend(
                        match.strip()
                        for match in after.findall(content)
                        if phrase_allowed(match, 5)
                    )
        except (OSError, ValueError) as error:
            return str(error)
        prefixes[:] = sorted(set(before_results))
        suffixes[:] = sorted(set(after_results))
        return "百变小动物已更新"
    return run.__doc__
