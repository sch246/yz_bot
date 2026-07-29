"""Compile and run one temporary Nim program."""

import os
import shutil
import subprocess
import tempfile

from mods import context, cq, is_available, op, thread
from mods.command import command


LOAD_AFTER = ("op",)
_processes: set[subprocess.Popen] = set()


def on_load(_ctx):
    if not is_available("op"):
        raise RuntimeError("nim 依赖的 op 模块不可用")


def _execute(args):
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _processes.add(process)
    try:
        output, error = process.communicate(timeout=60)
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
    """编译并运行 Nim 临时代码（管理员）。

    格式：.nim <代码>
    需要 PATH 中的 nim，最长运行 60 秒；编译和运行输出合并返回。
    """
    if not op.require_op(context.current()):
        return None
    executable = shutil.which("nim")
    if executable is None:
        return "nim 未安装或不在 PATH 中"
    with tempfile.TemporaryDirectory(prefix="yuzu-nim-") as directory:
        source = os.path.join(directory, "main.nim")
        output = os.path.join(directory, "main")
        with open(source, "w", encoding="utf-8") as stream:
            stream.write(cq.unescape(body.strip()))
        return _execute(
            [
                executable,
                "compile",
                "--verbosity:0",
                "--hints:off",
                f"--out:{output}",
                "--run",
                source,
            ]
        )


def on_exit():
    for process in tuple(_processes):
        if process.poll() is None:
            process.terminate()
