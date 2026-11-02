"""构造运行专用：提交这次关注的情境、材料与提醒，帮助接下来的自己自主判断。

每次只提交一份。构造提供信息和洞察；怎样行动、怎样表达由行动运行决定。
"""
from __future__ import annotations

from mods import tools as tool_modules


def assemble(focus: str, refs: str = "", note: str = "", windows: str = "", tools: str = "", history: int = 10) -> str:
    """为接下来的自己交一份上下文准备；提交成功就结束这次构造。

    @param
    focus: 这次关注的情境或问题，例如“某人在切换模型后确认是否在线”；描述情况，具体行动留给接下来的自己
    refs: 每行一条：笔记名、局部笔记如 topic:线索@L12-28、日志编号、运行 ctx、log:窗口@时刻*条数、policy:名字
    note: checklist 帮你注意到的信息、提醒和洞察，附依据或不确定之处；没有发现就留空
    windows: 这份关注的窗口，空格分隔，如 "g123 u456"、"me"；留空表示本次所有等着的窗口
    tools: 行动开场需要的工具/Skill 模块名，空格或逗号分隔；同时提供说明与函数；空串只有 meta 基础能力，行动还可自行加载
    history: 窗口新消息之前的接话条数，可为 0，默认 10；新消息仍可见，更早的用 log: 引用
    """
    from mods.chat import construct

    try:
        binding = tool_modules.current_binding()
    except RuntimeError:
        return "assemble 只能在构造运行里用"
    state = binding.get("run")
    if not state or state.get("kind") != "construct":
        return "assemble 只能在构造运行里用"
    return construct.submit(state, focus, refs, note, windows, tools, history)


__all__ = ["assemble"]
