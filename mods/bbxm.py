"""Rebuild the 百变小猫 text corpus from configured chat logs."""

import os
import random
import re

from mods.bbx import phrase_allowed, source_log_dir
from mods.command import command


LOAD_AFTER = ("bbx",)


def random_phrase() -> str:
    with open("data/bbxm.txt", encoding="utf-8") as stream:
        lines = [line.strip() for line in stream if line.strip()]
    if not lines:
        raise ValueError("百变小猫语料为空")
    return f"现在{random.choice(lines)}！"


def on_load(_ctx) -> None:
    from mods import is_available

    if not is_available("bbx"):
        raise RuntimeError("bbxm 依赖的 bbx 不可用")


@command
def run(_body: str) -> str:
    """从配置的聊天日志重建“百变小猫”语料。

    格式：.bbxm
    扫描来源日志并更新本地语料文件；命令不读取参数。
    """
    before = re.compile(r"我是(.{0,16})猫")
    after = re.compile(r"我是猫(.{0,9})")
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

    with open("data/bbxm.txt", "w", encoding="utf-8") as file:
        for match in sorted(set(before_results)):
            file.write(f"我是{match}猫\n".replace("我", "你"))
        for match in sorted(set(after_results)):
            file.write(f"我是猫{match}\n".replace("我", "你"))
    return "百变小猫已更新"
