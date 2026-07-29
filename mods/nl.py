"""On-demand newLISP session inside GNU screen."""

import subprocess

from mods import context, cq, is_available, op, screen
from mods.command import command


SCREEN_NAME = "newlisp"
LOAD_AFTER = ("op", "screen")
_started_here = False


def on_load(_ctx):
    missing = [name for name in ("op", "screen") if not is_available(name)]
    if missing:
        raise RuntimeError("nl 依赖模块不可用: " + ", ".join(missing))


@command
def run(body: str):
    """在 GNU screen 长驻会话中运行 newLISP（管理员）。

    格式：.nl <代码>
    需要可用的 screen 和 newlisp；首次调用建立会话，后续调用复用它。
    """
    if not op.require_op(context.current()):
        return None
    source = cq.unescape(body.strip())
    if not screen.check():
        result = subprocess.run(
            ["screen", "-v"], capture_output=True, text=True, check=False
        )
        return "screen错误:" + (result.stdout + result.stderr)
    if not screen.check(SCREEN_NAME):
        global _started_here
        error = screen.start(SCREEN_NAME)
        if error and not error.startswith("已启动"):
            return error
        _started_here = True
    if not screen.send(SCREEN_NAME, "1").strip().endswith(">"):
        result = screen.send(SCREEN_NAME, "newlisp")
        if "newlisp -h" not in result:
            return "newlisp错误:" + result
    return screen.send(SCREEN_NAME, source).strip()


def on_exit():
    if _started_here and screen.check(SCREEN_NAME):
        screen.stop(SCREEN_NAME)
