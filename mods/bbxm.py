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
    scanned = 0
    # WHY: source_log_dir／phrase_allowed 用 ValueError 报的都是用户能照着改的配置问题
    # （来源群没配、目录不存在、排除词配置无效），所以照原样回话；让它冒到事件路由去，
    # 用户看到的会变成「处理失败: ValueError: …」，同样的话多一层壳。
    try:
        log_dir = source_log_dir()
        for root, _, files in os.walk(log_dir):
            for filename in files:
                if not filename.endswith((".log", ".txt")):
                    continue
                try:
                    with open(os.path.join(root, filename), encoding="utf-8", errors="ignore") as file:
                        content = file.read()
                except OSError:
                    continue
                scanned += 1
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
    except ValueError as error:
        return str(error)

    # WHY: 一个文件都没读到就不要动语料。无条件重写会在来源群日志缺失或改名时把
    # data/bbxm.txt 清成空文件，而这个洞要到下次取随机语料才以「百变小猫语料为空」暴露。
    if not scanned:
        return f"来源群里没有可读的日志文件：{log_dir}"

    with open("data/bbxm.txt", "w", encoding="utf-8") as file:
        for match in sorted(set(before_results)):
            file.write(f"我是{match}猫\n".replace("我", "你"))
        for match in sorted(set(after_results)):
            file.write(f"我是猫{match}\n".replace("我", "你"))
    return "百变小猫已更新"
