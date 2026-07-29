"""On-demand Node.js REPL session."""

from mods import context, cq, is_available, message, op, repl, thread
from mods.command import command


LOAD_AFTER = ("op", "repl")
node_repl = repl.Repl(["node", "-i"])
node_signs = [">", "..."]


def on_load(_ctx):
    missing = [name for name in ("op", "repl") if not is_available(name)]
    if missing:
        raise RuntimeError("js 依赖模块不可用: " + ", ".join(missing))


@command
def run(body: str):
    """在长驻 Node REPL 中运行 JavaScript（管理员）。

    格式：.js <代码> | .js :bye
    每次执行最多等待 30 秒；:bye 关闭当前 Node 会话。
    """
    event = context.current()
    if not op.require_op(event):
        return None
    source = cq.unescape(body.strip())
    available, error = repl.ensure(["node", "-v"])
    if not available:
        return error
    if source == ":bye":
        node_repl.stop()
        return "已关闭"

    def reply(value):
        for sign in node_signs:
            value = value.replace(sign + " ", "")
        message.sendmsg("结果为空" if not value else value)

    return thread.to_thread(node_repl.run_code)(
        source, reply, node_signs, timeout=30
    )
