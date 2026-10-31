"""直接打理你所在的这台宿主机：读写文件、按行精准改写代码、跑终端命令。

## 读写文件：所见即所改的对称视图与精准匹配

用 `read_file` 查看文件的一段代码，每一行开头都会带上绝对行号，末尾会标清总行数与 `EOF`：

    file: mods/chat.py | lines: [40, 60) | total: 350
    40│def init_chat(...):
    41│    ...
    ...rest 290 lines

读到的行号区间是标准的 Python **左闭右开** `[start, end)`。在末尾如果读到了文件最后一行，会直接显示 `─── EOF (350) ───`，清晰指示文件开区间终点。

用 `write_file` 改写代码时，支持以下四种灵活而安全的方式：

1. **带行号约束的精确查找替换（最推荐）**：
   同时传入 `start`、`end` 和 `old_string`。工具只会在 `[start, end)` 这一小段里查找 `old_string`。
   只要在这段区间内 `old_string` 是唯一的，就会精准替换为 `content`；区间之外同名的其他代码绝不会被误伤，也不会因行号差一行切坏邻居。
2. **全局查找替换**：
   只传 `old_string`（不传 `start`/`end`）。在全文件中查找 `old_string`，严格唯一命中时替换。
3. **区间整体替换（与 view 镜像对称）**：
   只传 `start` 和 `end`（不传 `old_string`）。你刚才 `read_file(start=40, end=60)` 看到了什么，想整体替换成什么，直接把新内容传给 `content` 重写这段区间。
4. **全文件新建/覆写**：
   不传 `start`、`end` 和 `old_string`，直接将整份 `content` 写入文件。

## 行号与标尺前缀自动剥离

像 `40│` 这样的行号前缀只是看代码时的标尺，不是文件里真有的字。你把读到的代码复制进 `content` 或 `old_string` 时，哪怕带着 `40│` 前缀或末尾的 `...rest` / `EOF` 提示，工具都会自动择干净，绝不会把行号写进代码。

## 遇到大文件怎么省着读

如果一次要读的片段超过了 `max_bytes`，工具不会硬把几万字塞进你的上下文里（那会白白烧掉好多 token），而是先告诉你它有多大。遇到大文件按这三步走最划算：

1. **知道大概在第几行** 👉 用 `start` 和 `end` 只把那一小段切出来读；
2. **不知道在哪儿** 👉 先用 `run_command` 跑个 `grep -n` 找到行号，再去读那一段；
3. **非得从头到尾通读不可** 👉 用 `agents__fork` 派个子会话去替你读，让它只把整理好的摘要带回来给你。

## 跑终端命令（run_command）

- **每次都是干净的新进程**：它是一次性的 shell，不是一直开着的终端窗口，所以上一句的 `cd` 不会留到下一句；想换目录直接传 `cwd` 参数（默认就在你的主目录 `/root/bot` 下）。
- **超过 15 秒自动转后台**：普通命令跑完直接拿回输出；要是 15 秒还没跑完，不会粗暴杀掉它，而是自动帮你转进后台接着跑，先把任务号、日志路径（`data/commands/<任务号>.log`）和已经吐出来的字交还给你，让你腾出手干别的。等它跑完了会自动来 `me` 窗口叫醒你。
- **主动放后台**：传 `back="wake"`（跑完叫醒你）或 `"quiet"`（跑完悄悄把结果放进 `me` 窗口，不吵醒你），或者设 `timeout=0`，都能让命令一上来就去后台跑。
- **怎么管后台任务**：还在跑的命令会一直挂在你每次请求末尾的提示里；想停掉哪个就调 `stop_command(任务号)`，它会干脆利落地把整个进程组连同子进程一块收掉。就算你重启了，之前活着的后台任务也能接着管。如果要跑常驻服务，就交给 `screen`。

## 动手前的叮嘱

这四个工具直接连着你落脚的这台真实服务器，没有沙盒隔离：`data/` 下的记忆、`config.json`、`.env` 密钥、还有你自己的源码全都在手边。覆盖和删除是真刀真枪、没法撤回的，所以下手改之前一定先 `read_file` 看准了再动刀。改完自己的 Python 源码后，记得跑一下 `uv run --frozen python run.py --check` 确认没写漏括号或语法错误，再去重启（`.reboot`）。"""

from __future__ import annotations

import difflib
import os
import re

from mods import file, op


MAX_READ_BYTES = 32 * 1024
MAX_OUTPUT_BYTES = 8 * 1024

_HEADER = re.compile(
    r"^file:\s*.*?\s*\|\s*(lines:\s*\[\s*-?\d+\s*,\s*-?\d+\s*\)|line:\s*-?\d+)"
)
_REST = re.compile(r"^\.\.\.rest \d+ lines?$")


def _human(size: int) -> str:
    return f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB"


def _clamp(lines: list[str], start: int = 0, end: int | None = None) -> tuple[int, int]:
    """Resolve one window, letting negative indexes count from the end."""
    total = len(lines)
    if start < 0:
        start += total
    start = max(0, min(start, total))
    if end is None or end > total:
        end = total
    elif end < 0:
        end = max(start, total + end)
    else:
        end = max(start, min(total, end))
    return start, end


def _header(path: str, lines: list[str], start: int, end: int) -> str:
    return f"file: {path} | lines: [{start}, {end}) | total: {len(lines)}"


def _render(path: str, lines: list[str], start: int, end: int) -> str:
    """Render one window with line numbers and EOF marker."""
    body = [f"{index}│{line}" for index, line in enumerate(lines[start:end], start)]
    remaining = len(lines) - end
    if remaining > 0:
        body.append(f"...rest {remaining} line" + ("s" if remaining != 1 else ""))
    else:
        body.append(f"─── EOF ({len(lines)}) ───")
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


def _clean_content(text: str) -> str:
    body = text.splitlines()
    if body and _HEADER.match(body[0].strip()):
        body = body[1:]
    if body and (_REST.match(body[-1].strip()) or body[-1].strip().startswith("─── EOF")):
        body.pop()
    return file.strip_linemark("\n".join(body))


def read_file(path: str, start: int = 0, end: int | None = None, max_bytes: int = MAX_READ_BYTES) -> str:
    """读取宿主机文件的一段并带行号返回；目录则返回目录列表。需要管理员权限。

    @param
    path: 文件或目录路径，相对路径按 Bot 工作目录解析
    start: 闭区间起始行号（包含本行），从 0 数；负数表示从末尾倒数，默认 0
    end: 开区间结束行号，查看止于此行之前（不包含本行）；留空或超出则一直读到文件末尾（EOF）
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
    start, end = _clamp(lines, start, end)
    measured = len("\n".join(lines[start:end]).encode("utf-8"))
    if measured > max_bytes:
        return (
            f"file: {path} | lines: [{start}, {end}) | total: {len(lines)} | {_human(measured)}\n"
            f"超过 {_human(max_bytes)} 未展开。可以用 start/end 缩小读取区间，"
            f"用 run_command 跑 grep -n 定位后再读，"
            f"或用 agents__fork 开子会话通读后只带回摘要；"
            f"确实要整段进上下文时再调高 max_bytes。"
        )
    return _render(path, lines, start, end)


def write_file(
    path: str,
    content: str,
    start: int | None = None,
    end: int | None = None,
    old_string: str = "",
) -> str:
    """写入或修改宿主机文件：支持区间整体重写、带行号约束的精确查找替换、全局查找替换、或全文件覆写/新建。需要管理员权限。

    @param
    path: 目标文件路径，相对路径按 Bot 工作目录解析
    content: 替换后的新内容；若行首带行号（如 40│）或头尾带 header/EOF/rest 标记，工具会自动剥离干净
    start: 闭区间起始行号，修改从本行开始（包含本行）。从 0 起算；传负数表示从末尾倒数。配 old_string 时留空默认 0；不配 old_string 整段改写时必填
    end: 开区间结束行号，修改止于此行之前（不包含本行）。超出总行数按文件末尾（EOF）算。配 old_string 时留空就查到末尾；不配 old_string 整段改写时必填
    old_string: 待替换的原始文本。传入时，唯一性匹配仅在指定的 [start, end) 区间内进行（不传 start/end 时在全文件进行）。若该范围内找不到或出现超过 1 次，则拒绝修改并报错，确保绝不误伤
    """
    if not op.bot_is_op():
        return "权限不足"
    if os.path.isdir(path):
        return f"{path} 是目录，无法作为文件写入"

    clean_text = _clean_content(content)
    clean_old = file.strip_linemark(old_string) if old_string else ""

    if not os.path.exists(path):
        if start is not None or end is not None or clean_old:
            return f"文件不存在，无法进行局部修改: {path}"
        try:
            file.write(path, clean_text + "\n" if clean_text and not clean_text.endswith("\n") else clean_text)
        except OSError as exc:
            return f"写入失败: {exc}"
        return _confirm(path, 0, len(clean_text.splitlines()))

    try:
        lines = _lines(path)
    except FileNotFoundError:
        return _missing(path)
    except (OSError, UnicodeDecodeError) as exc:
        return f"读取失败: {exc}"

    if clean_old:
        if start is None and end is None:
            c_start, c_end = 0, len(lines)
            sub_text = "\n".join(lines)
            range_desc = ""
        else:
            c_start, c_end = _clamp(lines, start or 0, end)
            sub_text = "\n".join(lines[c_start:c_end])
            range_desc = f" [{c_start}, {c_end}) 行号区间内"

        occurrences = sub_text.count(clean_old)
        if occurrences == 0:
            return f"在 {path}{range_desc} 未找到指定的 old_string，未写入；请确认文本内容或调整起止行号范围。"
        if occurrences > 1:
            return (
                f"指定的 old_string 在 {path}{range_desc} 匹配了 {occurrences} 次；"
                f"请缩小起止行号范围或提供更多上下文文本，确保唯一命中。"
            )

        replaced_sub_text = sub_text.replace(clean_old, clean_text, 1)
        try:
            file.overwrite(path, replaced_sub_text, c_start, c_end)
        except OSError as exc:
            return f"写入失败: {exc}"
        return _confirm(path, c_start, len(replaced_sub_text.splitlines()))

    if start is not None or end is not None:
        # WHY: 2026-10-29 审查：没有 old_string 时，区间就是要删掉的范围。只给 start 曾一路删到文件末尾，
        # 只给 end 则从第 0 行删起，而本意多半只是改几行。所以整段改写两头都得写明；真要改到末尾，
        # end 写一个比总行数大的数（超出照旧按 EOF 算）。
        if start is None or end is None:
            return (f"没有 old_string 时按 [start, end) 整段改写，start 和 end 都要给（只给了 "
                    f"{'start' if start is not None else 'end'}，什么都没写）；只想改一处就用 old_string")
        c_start, c_end = _clamp(lines, start, end)
        try:
            file.overwrite(path, clean_text, c_start, c_end)
        except OSError as exc:
            return f"写入失败: {exc}"
        return _confirm(path, c_start, len(clean_text.splitlines()))

    try:
        file.write(path, clean_text + "\n" if clean_text and not clean_text.endswith("\n") else clean_text)
    except OSError as exc:
        return f"写入失败: {exc}"
    return _confirm(path, 0, len(clean_text.splitlines()))


def _confirm(path: str, start: int, count: int) -> str:
    """Re-render what is now there, so the result carries the next view."""
    try:
        lines = _lines(path)
    except (OSError, UnicodeDecodeError) as exc:
        return f"已写入 {path}，但回读失败: {exc}"
    if not lines:
        return f"已写入 {path}，文件现在为空\n─── EOF (0) ───"
    start = max(0, min(start, len(lines) - 1))
    view_end = min(len(lines), start + max(count, 1))
    return f"已更新 {path}\n" + _render(path, lines, start, view_end)


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
