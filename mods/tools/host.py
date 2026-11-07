"""直接打理你所在的这台宿主机：跑终端命令、管理后台任务。（文件与笔记读写已收归基础工具 `read_file` / `write_file`。）

## 跑终端命令（run_command）

- **每次都是干净的新进程**：它是一次性的 shell，不是一直开着的终端窗口，所以上一句的 `cd` 不会留到下一句；想换目录直接传 `cwd` 参数（默认就在你的主目录 `/root/bot` 下）。
- **超过 15 秒自动转后台**：普通命令跑完直接拿回输出；要是 15 秒还没跑完，不会粗暴杀掉它，而是自动帮你转进后台接着跑，先把任务号、日志路径（`data/commands/<任务号>.log`）和已经吐出来的字交还给你，让你腾出手干别的。等它跑完了会自动来 `me` 窗口叫醒你。
- **主动放后台**：传 `back="wake"`（跑完叫醒你）或 `"quiet"`（跑完悄悄把结果放进 `me` 窗口，不吵醒你），或者设 `timeout=0`，都能让命令一上来就去后台跑。
- **怎么管后台任务**：还在跑的命令会一直挂在你每次请求末尾的提示里；想停掉哪个就调 `stop_command(任务号)`，它会干脆利落地把整个进程组连同子进程一块收掉。就算你重启了，之前活着的后台任务也能接着管。如果要跑常驻服务，就交给 `screen`。

## 动手前的叮嘱

这两个工具直接连着你落脚的这台真实服务器，没有沙盒隔离。改完自己的 Python 源码后，记得跑一下 `uv run --frozen python run.py --check` 确认没写漏括号或语法错误，再去重启（`.reboot`）。"""

from __future__ import annotations

import os

from mods import op
from mods.tools.meta import read_file, write_file


MAX_OUTPUT_BYTES = 8 * 1024


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


__all__ = ["run_command", "stop_command"]
