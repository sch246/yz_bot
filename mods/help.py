"""Command discovery and docstring help."""

from inspect import cleandoc

from mods import command
from mods.command import command as register


@register
def run(body: str) -> str:
    """查看命令目录或详细帮助。

    格式：.help [命令]
    无参数列出可用命令摘要；指定命令显示完整说明。
    """
    name = body.strip().removeprefix(".")
    if not name:
        return "\n".join(
            f".{command_name} — {_summary(function)}"
            for command_name, function in command.items()
        )
    function = command.get(name)
    if function is None:
        return "该命令不存在！"
    return cleandoc(function.__doc__) if function.__doc__ else "该命令没有帮助说明。"


def _summary(function) -> str:
    if not function.__doc__:
        return "暂无说明"
    return cleandoc(function.__doc__).splitlines()[0]
