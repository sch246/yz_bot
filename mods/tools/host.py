"""读写宿主机文件、按行区间精确改写，并执行 shell 命令。

## 定位靠 header，不靠内容匹配

`read_file` 的返回总是以一行 header 开头：

    file: mods/chat.py | line: 40 | size: 20 | v: a3f9
    40│def init_chat(...):
    41│    ...
    ...rest 312 lines

`line` 是起始行号（从 0 数），`size` 是这次取了多少行，`v` 是这段内容的校验值。改写
时把这行 header 原样交给 `write_file` 的 `at`，就精确替换这一段。位置完全由 header
决定，与新内容长什么样无关，所以缩进、空行、格式怎么改都不会定位失败。

`v` 只在这段内容自读取之后被改动过时才失配。失配的返回里附有该区间的现状和新
header，照着重来一次即可，不要凭猜测重试。

## 行号是外挂

`40│` 这样的前缀是渲染出来的，不属于文件内容。写回时带着或去掉都行，会被自动剥离；
把整块（含 header 和结尾的 `...rest N lines`）原样贴进 `content` 也能正确处理。

## 大文件

超过 `max_bytes` 的读取不展开内容，只回一行大小说明。这时按顺序考虑三条路：

1. 已经知道大概位置 → 用 `start`/`size` 读那一段；
2. 不知道位置 → 用 `run_command` 跑 `grep -n` 定位，再读那一段；
3. 确实要通读 → 用 `agents__assign_tasks` 开子会话读，只把摘要带回来。

调高 `max_bytes` 硬读是最后手段：整段内容会进入上下文并按 token 计费。判定用的是本次
实际截取的那段的字节数，不是整个文件的大小，所以缩小 `size` 就能读进来。

## run_command

一次性的 shell，不是常驻会话：每次调用都是新进程，`cd` 不会留到下一次，要换目录用
`cwd` 参数，默认是 Bot 的工作目录。返回带退出码，非 0 就是失败，不要只看 stdout 有没
有内容。需要长期驻留的进程交给 `screen`。

## 边界

三个函数都需要 Bot 自身拥有 op 权限，直接作用于宿主机真实文件系统，没有沙箱也没有目录白名单：
`data/`、`config.json`、`.env`、聊天记录都在可及范围内。覆盖和删除不可撤销，动手前先
读一遍确认改的是想改的地方。写入的内容末尾会补一个换行。改完 Python 源码记得跑
`uv run --frozen python run.py --check`。
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import subprocess

from mods import file, op


MAX_READ_BYTES = 32 * 1024
MAX_OUTPUT_BYTES = 8 * 1024

_HEADER = re.compile(
    r"file:\s*(?P<path>.*?)\s*\|\s*line:\s*(?P<line>-?\d+)"
    r"\s*\|\s*size:\s*(?P<size>\d+)(?:\s*\|\s*v:\s*(?P<v>\w+))?"
)
_REST = re.compile(r"^\.\.\.rest \d+ lines?$")


def _digest(value: str) -> str:
    return hashlib.blake2s(value.encode("utf-8"), digest_size=2).hexdigest()


def _human(size: int) -> str:
    return f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB"


def _clamp(lines: list[str], start: int, size: int) -> tuple[int, int]:
    """Resolve one window, letting a negative start count from the end."""
    if start < 0:
        start += len(lines)
    start = max(0, min(start, len(lines)))
    end = len(lines) if size <= 0 else min(len(lines), start + size)
    return start, end


def _header(path: str, lines: list[str], start: int, end: int) -> str:
    window = "\n".join(lines[start:end])
    return f"file: {path} | line: {start} | size: {end - start} | v: {_digest(window)}"


def _render(path: str, lines: list[str], start: int, end: int) -> str:
    """Render one window in the format ``write_file`` accepts straight back."""
    body = [f"{index}│{line}" for index, line in enumerate(lines[start:end], start)]
    remaining = len(lines) - end
    if remaining > 0:
        body.append(f"...rest {remaining} line" + ("s" if remaining != 1 else ""))
    return "\n".join([_header(path, lines, start, end), *body])


def _missing(path: str) -> str:
    """Say what is not there, and what nearby is -- a typo is the common case."""
    parent = os.path.dirname(path) or "."
    try:
        names = os.listdir(parent)
    except OSError:
        return f"路径不存在: {path}"
    close = difflib.get_close_matches(os.path.basename(path), names, n=3)
    if not close:
        return f"路径不存在: {path}"
    nearby = "、".join(os.path.join(parent, name) for name in close)
    return f"路径不存在: {path}\n同目录下相近的有: {nearby}"


def _lines(path: str) -> list[str]:
    return file.read(path).splitlines()


def _truncate(value: str, limit: int) -> str:
    """Drop the middle: errors sit at the end, context sits at the start."""
    data = value.encode("utf-8")
    if len(data) <= limit:
        return value
    half = limit // 2
    head = data[:half].decode("utf-8", "ignore")
    tail = data[-half:].decode("utf-8", "ignore")
    return f"{head}\n...（省略 {_human(len(data) - limit)}）...\n{tail}"


def read_file(path: str, start: int = 0, size: int = 0, max_bytes: int = MAX_READ_BYTES) -> str:
    """读取宿主机文件的一段并带行号返回，首行 header 可直接用作 write_file 的 at；目录则返回目录列表。需要管理员权限。

    @param
    path: 文件或目录路径，相对路径按 Bot 工作目录解析
    start: 起始行号，从 0 数；负数表示从末尾倒数
    size: 读取行数，默认 0 表示读到文件末尾
    max_bytes: 本次截取内容的字节上限，默认 32768；超出则不展开内容，只回大小和后续建议
    """
    if not op.bot_is_op():
        return "权限不足"
    if os.path.isdir(path):
        return file.listdir(path)
    try:
        lines = _lines(path)
    except FileNotFoundError:
        return _missing(path)
    except (OSError, UnicodeDecodeError) as exc:
        return f"读取失败: {exc}"
    start, end = _clamp(lines, start, size)
    measured = len("\n".join(lines[start:end]).encode("utf-8"))
    if measured > max_bytes:
        return (
            f"file: {path} | line: {start} | size: {end - start} | {_human(measured)}\n"
            f"超过 {_human(max_bytes)} 未展开。可以用 start/size 读更小的区间，"
            f"用 run_command 跑 grep -n 定位后再读，"
            f"或用 assign_tasks 开子会话通读后只带回摘要；"
            f"确实要整段进上下文时再调高 max_bytes。"
        )
    return _render(path, lines, start, end)


def write_file(path: str, content: str, at: str = "") -> str:
    """写入宿主机文件：带 at 就替换 header 指定的行区间，不带 at 则新建文件；返回写入后的实际内容。需要管理员权限。

    @param
    path: 目标文件路径，必须与 at 里的 file 一致
    content: 新内容；行号前缀、header 行和结尾的 ...rest 提示都会被自动剥离
    at: read_file 返回的首行 header，原样贴回即可；留空表示新建文件，目标已存在时会拒绝并回一份 header
    """
    if not op.bot_is_op():
        return "权限不足"
    body = content.splitlines()
    if body and not at:
        # The whole block pasted back is the cheapest thing to type; take the
        # anchor from it rather than making that a mistake.
        head = _HEADER.match(body[0].strip())
        if head:
            at, body = body[0], body[1:]
    if body and _REST.match(body[-1].strip()):
        body.pop()
    text = file.strip_linemark("\n".join(body))

    if not at:
        if os.path.isdir(path):
            return f"{path} 是目录，无法作为文件写入"
        if os.path.exists(path):
            try:
                lines = _lines(path)
            except (OSError, UnicodeDecodeError) as exc:
                return f"{path} 已存在且无法读取，未写入: {exc}"
            return (
                f"{path} 已存在，未写入。整体替换就把下面这行作为 at 再调一次，"
                f"只改一段就先用 read_file 取回目标区间。\n"
                + _header(path, lines, 0, len(lines))
            )
        try:
            file.write(path, text + "\n" if text and not text.endswith("\n") else text)
        except OSError as exc:
            return f"写入失败: {exc}"
        return _confirm(path, 0, len(text.splitlines()))

    anchor = _HEADER.search(at)
    if anchor is None:
        return "at 不是有效的 header，应当原样使用 read_file 返回的第一行"
    target = anchor.group("path")
    if target and os.path.abspath(target) != os.path.abspath(path):
        return f"header 指向 {target}，与 path 参数 {path} 不一致，未写入"
    try:
        lines = _lines(path)
    except FileNotFoundError:
        return _missing(path)
    except (OSError, UnicodeDecodeError) as exc:
        return f"读取失败: {exc}"
    start = int(anchor.group("line"))
    if start < 0:
        start += len(lines)
    start = max(0, min(start, len(lines)))
    end = min(len(lines), start + int(anchor.group("size")))
    expected = anchor.group("v")
    if expected and _digest("\n".join(lines[start:end])) != expected:
        return (
            "该区间自读取后已改变，未写入。当前内容如下，据此重新编辑：\n"
            + _render(path, lines, start, end)
        )
    try:
        file.overwrite(path, text, start, end)
    except OSError as exc:
        return f"写入失败: {exc}"
    return _confirm(path, start, len(text.splitlines()))


def _confirm(path: str, start: int, count: int) -> str:
    """Re-render what is now there, so the result carries the next anchor."""
    try:
        lines = _lines(path)
    except (OSError, UnicodeDecodeError) as exc:
        return f"已写入 {path}，但回读失败: {exc}"
    if not lines:
        return f"已写入 {path}，文件现在为空"
    start = min(start, len(lines) - 1)
    return f"已写入 {path}\n" + _render(path, lines, start, min(len(lines), start + max(count, 1)))


def run_command(
    command: str, cwd: str = "", timeout: int = 60, max_bytes: int = MAX_OUTPUT_BYTES
) -> str:
    """在宿主机上执行一条 shell 命令，返回工作目录、退出码和被截断的 stdout/stderr。需要管理员权限。

    @param
    command: 交给 shell 的完整命令，可以用管道、重定向和通配符
    cwd: 工作目录，默认是 Bot 的工作目录；每次调用都是新进程，cd 不会保留到下一次
    timeout: 超时秒数，默认 60；超时会终止进程并回传已有输出
    max_bytes: stdout 和 stderr 各自的字节上限，默认 8192；超出时省略中间部分
    """
    if not op.bot_is_op():
        return "权限不足"
    workdir = os.path.abspath(cwd) if cwd else os.getcwd()
    if not os.path.isdir(workdir):
        return f"工作目录不存在: {workdir}"
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=workdir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return f"启动失败: {exc}"
    try:
        output, error = process.communicate(timeout=timeout)
        status = f"exit: {process.returncode}"
    except subprocess.TimeoutExpired:
        process.kill()
        output, error = process.communicate()
        status = f"超时 {timeout}s，已终止"
    sections = [f"cwd: {workdir} | {status}"]
    for label, value in (("stdout", output), ("stderr", error)):
        value = _truncate(value.strip(), max_bytes)
        if value:
            sections.append(f"--- {label} ---\n{value}")
    if len(sections) == 1:
        sections.append("(无输出)")
    return "\n".join(sections)


__all__ = ["read_file", "write_file", "run_command"]
