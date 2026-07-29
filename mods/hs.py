"""On-demand GHCi session."""

import os

from mods import context, cq, is_available, message, op, repl, thread
from mods.command import command


LOAD_AFTER = ("op", "repl")
ghci_path = os.environ.get("GHCI_PATH", "/root/.ghcup/bin/ghci")
ghci_repl = repl.Repl([ghci_path])
ghci_signs = ["ghci>", "ghci|"]


def on_load(_ctx):
    missing = [name for name in ("op", "repl") if not is_available(name)]
    if missing:
        raise RuntimeError("hs 依赖模块不可用: " + ", ".join(missing))


@command
def run(body: str):
    """在长驻 GHCi 中运行 Haskell（管理员）。

    格式：.hs <代码> | .hs :quit
    解释器路径可由 GHCI_PATH 配置；每次执行最多等待 30 秒，:quit 关闭会话。
    """
    event = context.current()
    if not op.require_op(event):
        return None
    source = cq.unescape(body.strip())
    available, error = repl.ensure([ghci_path, "--version"])
    if not available:
        return error
    if source == ":quit":
        ghci_repl.stop()
        return "GHCi 已关闭"

    def reply(value):
        for sign in ghci_signs:
            value = value.replace(sign + " ", "")
        message.sendmsg("结果为空" if not value else value)

    return thread.to_thread(ghci_repl.run_code)(
        source, reply, ghci_signs, timeout=30
    )
