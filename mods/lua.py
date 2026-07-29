"""Run one temporary Lua program."""

import shutil
import subprocess
import tempfile

from mods import context, cq, is_available, op, thread
from mods.command import command


LOAD_AFTER = ("op",)
_processes: set[subprocess.Popen] = set()


def on_load(_ctx):
    if not is_available("op"):
        raise RuntimeError("lua 依赖的 op 模块不可用")


def _execute(args):
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _processes.add(process)
    try:
        output, error = process.communicate(timeout=30)
        return (output + error).strip() or "结果为空"
    except subprocess.TimeoutExpired:
        process.kill()
        output, error = process.communicate()
        return ((output + error).strip() + "\n执行超时").strip()
    finally:
        _processes.discard(process)


@command
@thread.to_thread
def run(body: str):
    """用本机 Lua 解释器执行临时代码（管理员）。

    格式：.lua <代码>
    优先使用 PATH 中的 lua，最长运行 30 秒；标准输出和错误会合并返回。
    """
    if not op.require_op(context.current()):
        return None
    executable = shutil.which("lua") or "/usr/bin/lua"
    if not shutil.which(executable) and not executable.startswith("/"):
        return "lua 未安装或不在 PATH 中"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".lua"
    ) as source:
        source.write(cq.unescape(body.strip()))
        source.flush()
        try:
            return _execute([executable, source.name])
        except FileNotFoundError:
            return "lua 未安装或不在 PATH 中"


def on_exit():
    for process in tuple(_processes):
        if process.poll() is None:
            process.terminate()
