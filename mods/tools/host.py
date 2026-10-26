"""直接打理你所在的这台宿主机：读写文件、按行精准改写代码、跑终端命令。

## 改文件认准 header，不怕缩进乱掉

每次用 `read_file` 读文件，开头第一行都会给你一个定位标头（header）：

    file: mods/chat.py | line: 40 | size: 20 | v: a3f9
    40│def init_chat(...):
    41│    ...
    ...rest 312 lines

这里的 `line` 是从第几行开始（从 0 起算），`size` 是读了多少行，`v` 是这一小段内容的指纹校验值。
等你要用 `write_file` 改写这段代码时，直接把这整行 header 原封不动传给 `at` 参数，它就能严丝合缝地替换这一段。因为位置全靠 header 锁定，跟你要写进去的新内容长什么样毫无关系，所以不管你怎么调缩进、增删空行，都绝不会贴错地方。

只有当这段文件在你读完之后又被别人动过，指纹 `v` 才会对不上。万一失配，返回值里会直接把那一段现在的样子和新的 header 给你，照着新 header 再来一次就行，不用盲猜。

## 行号只是辅助看代码的标尺

像 `40│` 这样的行号前缀只是为了方便你看代码画上去的，不是文件里真有的字。你用 `write_file` 写回时，不管带着行号还是去掉行号（哪怕把头尾的 header 和 `...rest N lines` 一块贴进 `content`），工具都会聪明地帮你择干净，只把真正的代码写进去。

## 遇到大文件怎么省着读

如果一次要读的片段超过了 `max_bytes`，工具不会硬把几万字塞进你的上下文里（那会白白烧掉好多 token），而是先告诉你它有多大。遇到大文件按这三步走最划算：

1. **知道大概在第几行** 👉 用 `start` 和 `size` 只把那一小段切出来读；
2. **不知道在哪儿** 👉 先用 `run_command` 跑个 `grep -n` 找到行号，再去读那一段；
3. **非得从头到尾通读不可** 👉 用 `agents__assign_tasks` 派个子会话去替你读，让它只把整理好的摘要带回来给你。

## 跑终端命令（run_command）

- **每次都是干净的新进程**：它是一次性的 shell，不是一直开着的终端窗口，所以上一句的 `cd` 不会留到下一句；想换目录直接传 `cwd` 参数（默认就在你的主目录 `/root/bot` 下）。
- **超过 15 秒自动转后台**：普通命令跑完直接拿回输出；要是 15 秒还没跑完，不会粗暴杀掉它，而是自动帮你转进后台接着跑，先把任务号、日志路径（`data/commands/<任务号>.log`）和已经吐出来的字交还给你，让你腾出手干别的。等它跑完了会自动来 `me` 窗口叫醒你。
- **主动放后台**：传 `back="wake"`（跑完叫醒你）或 `"quiet"`（跑完悄悄把结果放进 `me` 窗口，不吵醒你），或者设 `timeout=0`，都能让命令一上来就去后台跑。
- **怎么管后台任务**：还在跑的命令会一直挂在你每次请求末尾的提示里；想停掉哪个就调 `stop_command(任务号)`，它会干脆利落地把整个进程组连同子进程一块收掉。就算你重启了，之前活着的后台任务也能接着管。如果要跑常驻服务，就交给 `screen`。

## 动手前的叮嘱

这四个工具直接连着你落脚的这台真实服务器，没有沙盒隔离：`data/` 下的记忆、`config.json`、`.env` 密钥、还有你自己的源码全都在手边。覆盖和删除是真刀真枪、没法撤回的，所以下手改之前一定先 `read_file` 看准了再动刀。改完自己的 Python 源码后，记得跑一下 `uv run --frozen python run.py --check` 确认没写漏括号或语法错误，再去重启（`.reboot`）。"""

from __future__ import annotations

import difflib
import hashlib
import os
import re

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


# WHY: `back` 是显式参数，不用函数属性 `.back`。工具绑定层看到 `.back` 会把 wake/quiet
# 排进 `_tasks` 的单 worker 队列，一条跑十分钟的 shell 会把后面所有工具调用堵住；这里的
# 后台由 `shell.run` 自己登记、自己起 watcher。
def run_command(
    command: str,
    cwd: str = "",
    timeout: float = 15,
    back: str = "wait",
    max_bytes: int = MAX_OUTPUT_BYTES,
) -> str:
    """在宿主机上执行一条 shell 命令；默认等 15 秒，没跑完转后台。需要管理员权限。

    @param
    command: 交给 shell 的完整命令，可以用管道、重定向和通配符
    cwd: 工作目录，默认是 Bot 的工作目录；每次调用都是新进程，cd 不会保留到下一次
    timeout: 等多少秒，默认 15；到点没跑完就转后台，不杀它；0 表示立刻转后台
    back: 结果怎么回来：wait 先等 timeout；wake 立刻转后台、跑完推进 me 窗口叫醒你；quiet 转后台但不叫醒
    max_bytes: 返回里最多保留多少字节的已有输出，默认 8192；后台输出文件本身不受此限
    """
    if not op.bot_is_op():
        return "权限不足"
    workdir = os.path.abspath(cwd) if cwd else os.getcwd()
    if not os.path.isdir(workdir):
        return f"工作目录不存在: {workdir}"
    from mods import shell

    return shell.run(command, workdir, timeout=timeout, back=back, max_bytes=max_bytes)


def stop_command(task: str) -> str:
    """按任务号停掉转后台的 shell 命令，连同它启动的子进程；需要管理员权限。

    @param
    task: run_command 返回的任务号，可以带 #，例如 "3"
    """
    if not op.bot_is_op():
        return "权限不足"
    from mods import shell

    return shell.stop(task)


__all__ = ["read_file", "write_file", "run_command", "stop_command"]
