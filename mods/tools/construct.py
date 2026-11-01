"""构造运行专用：为接下来行动的自己提交一份运行规格（目的、材料引用、注意）。

只在每次唤醒前的构造运行里有用；在别处调用会直接返回错误。一次唤醒可以提交几份，
每份是一次独立的行动，按提交顺序依次进行（被叫到的窗口先）。
"""

from __future__ import annotations

from mods import tools as tool_modules


def assemble(goal: str, refs: str = "", note: str = "", windows: str = "") -> str:
    """提交一份行动规格，返回记下的结果或需要改正的地方。

    @param
    goal: 这一份要做的具体的事，一句话，例如“接着回答某人关于某事的追问”
    refs: 每行一条材料引用：笔记名、日志编号、运行 ctx、log:窗口@时刻*条数、policy:名字
    note: 写给行动的自己的注意：陌生说法用「」括起来、矛盾、情绪、意外；没有就留空
    windows: 这份负责回应的窗口，空格分隔，如 "g123 u456"、"me"；留空表示还没被其它规格认领的被叫醒窗口
    """
    from mods.chat import construct

    try:
        binding = tool_modules.current_binding()
    except RuntimeError:
        return "assemble 只能在构造运行里用"
    state = binding.get("run")
    if not state or state.get("kind") != "construct":
        return "assemble 只能在构造运行里用"
    return construct.submit(state, goal, refs, note, windows)


__all__ = ["assemble"]
