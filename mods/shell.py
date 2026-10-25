"""执行宿主机命令：先等一会儿，没跑完就转到后台，按任务号管理。

命令的输出直接重定向到 `data/commands/<任务号>.log`，不读管道。等待超过时限就把它交给一个
后台线程，把任务号、输出文件和已有的输出交回去；跑完以后退出码和最后几十行推进她的 `me`
窗口。还在跑的命令由 `running_hint()` 在每次请求末尾重新算一遍。

WHY: 维护者 2026-10-25：超时不再杀进程。一条要跑十分钟的命令不该把这一轮卡死，也不该在
15 秒时被误杀。后台命令各自一个进程，不排 `tools._tasks` 的队列——一条慢命令不能堵住后面
所有工具调用。
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time

_ROOT = os.path.join("data", "commands")
MODES = ("wait", "wake", "quiet")
WAIT_SECONDS = 15.0
GRACE_SECONDS = 3.0
TAIL_BYTES = 16 * 1024
RESULT_LIMIT = 10_000
_COUNT_LIMIT = 64 * 1024 * 1024

_lock = threading.Lock()
_tasks: dict[int, "Job"] = {}
_stream = None


def _log():
    global _stream
    if _stream is None:
        from mods import log

        _stream = log.stream("shell")
    return _stream


class Job:
    """一条转到后台的命令。"""

    __slots__ = ("number", "process", "path", "cwd", "command", "started", "mode", "stopped", "thread")

    def __init__(self, number: int, process: subprocess.Popen, path: str, cwd: str, command: str, mode: str):
        self.number = number
        self.process = process
        self.path = path
        self.cwd = cwd
        self.command = command
        self.started = time.time()
        self.mode = mode
        self.stopped = False
        self.thread: threading.Thread | None = None


def run(command: str, cwd: str, timeout: float = WAIT_SECONDS, back: str = "wait", max_bytes: int = 8192) -> str:
    """执行 *command*；*timeout* 内没跑完就转后台，返回任务号和已有的输出。"""
    if back not in MODES:
        return f"back 只能是 {'、'.join(MODES)}"
    try:
        wait = max(0.0, float(timeout))
    except (TypeError, ValueError):
        wait = WAIT_SECONDS

    number, path, handle = _allocate()
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as error:
        handle.close()
        _remove(path)
        return f"启动失败: {error}"
    finally:
        if not handle.closed:
            handle.close()

    job = Job(number, process, path, cwd, command, "wake" if back == "wait" else back)
    if back == "wait" and wait > 0:
        try:
            process.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            pass
        else:
            return _finished(job, max_bytes)

    with _lock:
        _tasks[number] = job
    job.thread = threading.Thread(target=_watch, args=(job,), daemon=True, name=f"shell-{number}")
    job.thread.start()
    return _receipt(job, max_bytes, None if back != "wait" else wait)


def _allocate() -> tuple[int, str, object]:
    """从 1 起找一个还没用过的任务号，顺手把日志文件建好。"""
    os.makedirs(_ROOT, exist_ok=True)
    number = 1
    while True:
        path = os.path.join(_ROOT, f"{number}.log")
        try:
            return number, path, open(path, "xb")
        except FileExistsError:
            number += 1


def _finished(job: Job, max_bytes: int) -> str:
    """等到命令跑完：照旧返回退出码和输出，然后删掉这次临时日志。"""
    code = job.process.returncode
    text = _read_output(job.path, max_bytes)
    _remove(job.path)
    elapsed = time.time() - job.started
    body = text.rstrip() or "(无输出)"
    return f"cwd: {job.cwd} | exit: {code} | 跑了 {elapsed:.1f}s\n--- 输出 ---\n{body}"


def _receipt(job: Job, max_bytes: int, waited: float | None) -> str:
    elapsed = time.time() - job.started
    text, size = _read_tail(job.path, max_bytes)
    lines = _line_count(job.path)
    if lines is None:
        count = f"{_human(size)}（太大，没数行）"
    else:
        count = f"{lines} 行"
    last = _last_line(text) or "（还没有输出）"
    state = f"已转后台 #{job.number}（刚启动）" if waited is None else f"已转后台 #{job.number}（等了 {waited:g}s 没跑完）"
    parts = [
        f"cwd: {job.cwd} | {state} | 已跑 {elapsed:.1f}s | 输出 {count}",
        f"文件: {job.path}",
        f"最后一行: {last}",
        f"停它: stop_command(\"{job.number}\")",
    ]
    if text.strip():
        if size > max_bytes:
            note = f"（文件 {_human(size)}，这里只给最后 {_human(len(text.encode('utf-8')))}）"
        else:
            note = ""
        parts.append(f"--- 已有输出{note} ---\n{text.rstrip()}")
    else:
        parts.append("(还没有输出)")
    tail = "跑完会把结果推进 me 窗口并叫醒你" if job.mode == "wake" else "跑完只记进 me 窗口，不叫你"
    parts.append(tail)
    return "\n".join(parts)


def _watch(job: Job) -> None:
    """等后台命令退出，把退出码和最后几十行推进她的 me 窗口。"""
    try:
        code = job.process.wait()
    except Exception:
        _log().exception("等待命令 #%s 失败", job.number)
        return
    with _lock:
        _tasks.pop(job.number, None)
    if job.stopped:
        return
    elapsed = time.time() - job.started
    text, size = _read_tail(job.path, TAIL_BYTES)
    lines = _line_count(job.path)
    count = f"{lines} 行" if lines is not None else _human(size)
    tail = "\n".join(text.splitlines()[-40:]).rstrip()
    message = f"命令 #{job.number} 跑完了（退出码 {code}，跑了 {elapsed:.1f}s，输出 {count}，文件 {job.path}）"
    if job.command:
        message += f"\n命令: {job.command[:200]}"
    if tail:
        message += f"\n--- 最后几行 ---\n{tail}"
    if len(message) > RESULT_LIMIT:
        message = message[:RESULT_LIMIT] + f"\n……（后面还有 {len(message) - RESULT_LIMIT} 字没放进来）"
    try:
        from mods import chat

        wake = f"命令 #{job.number} 结束（退出码 {code}）" if job.mode == "wake" else ""
        chat.push(chat.windows.ME, "命令", message, wake=wake)
    except Exception:
        _log().exception("命令 #%s 的结果没能推进 me 窗口", job.number)


def stop(task: str) -> str:
    """按任务号停掉后台命令，连同它启动的子进程。"""
    number = _parse_number(task)
    if number is None:
        return f"任务号不对：写成数字，例如 stop_command(\"{task}\")"
    with _lock:
        job = _tasks.get(number)
    if job is None:
        path = os.path.join(_ROOT, f"{number}.log")
        if os.path.exists(path):
            return f"命令 #{number} 早就结束了（输出在 {path}）"
        return f"没有命令 #{number}"
    if job.process.poll() is not None:
        with _lock:
            _tasks.pop(number, None)
        return f"命令 #{number} 早就结束了"
    job.stopped = True
    _kill_group(job.process)
    return f"已停命令 #{number}（连同它启动的子进程；输出在 {job.path}）"


def running_hint() -> str:
    """还在跑的命令，一行一条；每次请求重新算，不存。"""
    with _lock:
        jobs = sorted(_tasks.values(), key=lambda job: job.number)
    if not jobs:
        return ""
    parts = []
    for job in jobs[:5]:
        elapsed = max(0, int(time.time() - job.started))
        text, size = _read_tail(job.path, 4096)
        last = _last_line(text) or "（还没有输出）"
        lines = _line_count(job.path) if size <= 1_000_000 else None
        count = f"{lines} 行" if lines is not None else _human(size)
        parts.append(f"#{job.number} 已 {elapsed}s，输出 {count}，最后：{last[:120]}")
    extra = f"，另 {len(jobs) - 5} 条没列" if len(jobs) > 5 else ""
    return "还在跑的命令（stop_command 可以停）：" + "；".join(parts) + extra + "。"


def _parse_number(task: str) -> int | None:
    found = re.fullmatch(r"\s*#?(\d+)\s*", str(task))
    return int(found.group(1)) if found else None


def _kill_group(process: subprocess.Popen) -> None:
    """先 SIGTERM 整个进程组，宽限几秒再 SIGKILL；父进程退了也要把剩下的子进程收掉。"""
    pid = process.pid
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    if pgid != pid:
        # WHY: 不是组长时 killpg 会打到别人的组（比如 Bot 自己）；这种进程只杀它本身。
        try:
            process.kill()
        except ProcessLookupError:
            pass
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def _read_tail(path: str, limit: int) -> tuple[str, int]:
    """读文件末尾一段；太大时从第一个完整行开始，返回 (文本, 文件大小)。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as file:
            if size > limit:
                file.seek(size - limit)
            data = file.read()
    except OSError:
        return "", 0
    if size > limit:
        cut = data.find(b"\n")
        if cut >= 0:
            data = data[cut + 1 :]
    return data.decode("utf-8", "replace"), size


def _read_output(path: str, limit: int) -> str:
    """前台命令的输出保留头尾：错误通常在末尾，上下文通常在开头。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as file:
            if size <= limit:
                data = file.read()
                return data.decode("utf-8", "replace")
            half = limit // 2
            head = file.read(half)
            file.seek(size - half)
            tail = file.read()
    except OSError:
        return ""
    omitted = _human(size - len(head) - len(tail))
    return (
        head.decode("utf-8", "ignore")
        + f"\n...（省略 {omitted}）...\n"
        + tail.decode("utf-8", "ignore")
    )


def _line_count(path: str) -> int | None:
    """数整个文件的行数；超过 _COUNT_LIMIT 返回 None。"""
    try:
        total = newlines = 0
        last = b""
        with open(path, "rb") as file:
            while True:
                chunk = file.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                newlines += chunk.count(b"\n")
                last = chunk[-1:]
                if total > _COUNT_LIMIT:
                    return None
        if total == 0:
            return 0
        return newlines + (0 if last == b"\n" else 1)
    except OSError:
        return None


def _last_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()[:160]
    return ""


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
