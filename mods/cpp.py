"""Compile and interact with one temporary C++ program."""

import os
import select
import shutil
import subprocess
import tempfile

from mods import context, cq, is_available, message, msgs, op
from mods.command import command


LOAD_AFTER = ("op",)
_processes: set[subprocess.Popen] = set()


def on_load(_ctx):
    if not is_available("op"):
        raise RuntimeError("cpp 依赖的 op 模块不可用")


def ensure():
    compiler = shutil.which("g++")
    if compiler is None:
        return False, "g++ 未安装或不在 PATH 中"
    result = subprocess.run(
        [compiler, "--version"], capture_output=True, text=True, check=False
    )
    if result.returncode:
        return False, "g++ 错误:\n" + (result.stderr or result.stdout)
    return True, compiler


def _interact(process):
    _processes.add(process)
    try:
        while True:
            readable, _, _ = select.select(
                [process.stdout, process.stderr], [], [], 0.1
            )
            if process.stdout in readable:
                output = process.stdout.readline()
                if output:
                    message.sendmsg(output.strip())
            if process.stderr in readable:
                error = process.stderr.readline()
                if error:
                    message.sendmsg("错误: " + error.strip())
            if process.poll() is not None:
                break
            reply = yield "等待输入..."
            if not msgs.is_msg(reply):
                message.sendmsg("请输入文本消息")
                continue
            if process.poll() is not None:
                break
            process.stdin.write(reply["message"] + "\n")
            process.stdin.flush()
        remaining_output, remaining_error = process.communicate()
        if remaining_output:
            message.sendmsg(remaining_output.strip())
        if remaining_error:
            message.sendmsg("错误: " + remaining_error.strip())
        message.sendmsg(f"程序已退出，返回值 {process.returncode}")
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        _processes.discard(process)


@command
def run(body: str):
    """编译并交互运行 C++ 代码（管理员）。

    格式：.cpp <代码>
    需要可用的 g++；编译成功后进入子进程交互，后续文本作为标准输入，结束时返回退出码。
    """
    event = context.current()
    if not op.require_op(event):
        return None
    available, compiler = ensure()
    if not available:
        return compiler
    source = cq.unescape(body.strip())
    with tempfile.TemporaryDirectory(prefix="yuzu-cpp-") as directory:
        source_path = os.path.join(directory, "main.cpp")
        output_path = os.path.join(directory, "main")
        with open(source_path, "w", encoding="utf-8") as stream:
            stream.write(source)
        compiled = subprocess.run(
            [compiler, source_path, "-o", output_path],
            capture_output=True,
            text=True,
            check=False,
        )
        if compiled.returncode:
            return "编译失败:\n" + compiled.stderr
        process = subprocess.Popen(
            [output_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        yield from _interact(process)


def on_exit():
    for process in tuple(_processes):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
