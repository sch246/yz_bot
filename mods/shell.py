"""执行宿主机命令：先等一会儿，没跑完就转到后台，按任务号管理。

命令的输出直接重定向到 `data/commands/<任务号>.log`，不读管道。等待超过时限就把它交给一个
后台线程，把任务号、输出文件和已有的输出交回去；跑完以后退出码和最后几十行推进她的 `me`
窗口。还在跑的命令由 `running_hint()` 在每次请求末尾重新算一遍。

WHY: 维护者 2026-10-25：超时不再杀进程。一条要跑十分钟的命令不该把这一轮卡死，也不该在
15 秒时被误杀。后台命令各自一个进程，不排 `tools._tasks` 的队列——一条慢命令不能堵住后面
所有工具调用。

任务用 pgid 跟踪，而不是只盯着最初那个 `sh`：`sh -c 'sleep 100 &'` 会先退出，把子进程留成
孤儿；只按 leader 的 pid 登记，之后既停不掉也看不到它。日志旁的 JSON 元数据让重启后的新
进程能重新接管还在跑的组，而不是看见日志就猜“早就结束了”。
"""

from __future__ import annotations

import atexit
import json
import os
import re
import signal
import subprocess
import threading
import time

_ROOT = os.path.abspath(os.path.join("data", "commands"))
MODES = ("wait", "wake", "quiet")
WAIT_SECONDS = 15.0
GRACE_SECONDS = 3.0
TAIL_BYTES = 16 * 1024
RESULT_LIMIT = 10_000
_COUNT_LIMIT = 64 * 1024 * 1024

_lock = threading.Lock()
_tasks: dict[int, "Job"] = {}
_recovered = False
_stream = None


def _log():
    global _stream
    if _stream is None:
        from mods import log

        _stream = log.stream("shell")
    return _stream


class Job:
    """一条转到后台的命令。`process` 在重启接管后为 None，只剩 pgid 可管。"""

    __slots__ = (
        "number",
        "process",
        "pid",
        "pgid",
        "path",
        "meta",
        "cwd",
        "command",
        "started",
        "mode",
        "stopped",
        "thread",
        "code",
        "announced",
        "adopted",
    )

    def __init__(
        self,
        number: int,
        process: subprocess.Popen | None,
        pid: int,
        pgid: int,
        path: str,
        cwd: str,
        command: str,
        mode: str,
        started: float | None = None,
        adopted: bool = False,
    ):
        self.number = number
        self.process = process
        self.pid = pid
        self.pgid = pgid
        self.path = path
        self.meta = _meta_path(number)
        self.cwd = cwd
        self.command = command
        self.started = time.time() if started is None else started
        self.mode = mode
        self.stopped = False
        self.thread: threading.Thread | None = None
        self.code: int | None = None
        self.announced = False
        self.adopted = adopted


def run(command: str, cwd: str, timeout: float = WAIT_SECONDS, back: str = "wait", max_bytes: int = 8192) -> str:
    """执行 *command*；*timeout* 内没跑完就转后台，返回任务号和已有的输出。"""
    if back not in MODES:
        return f"back 只能是 {'、'.join(MODES)}"
    _ensure_recovered()
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
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception as error:
        # WHY: 启动参数不合法（例如命令字符串里有空字节）抛的是 ValueError，不是 OSError；
        # 无论哪种都别把刚建好的日志文件留成一条假任务。
        handle.close()
        _remove(path)
        return f"启动失败: {error}"
    finally:
        if not handle.closed:
            handle.close()

    pid = process.pid
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        pgid = pid
    if pgid != pid:
        # WHY: start_new_session 正常时 pid 就是组长；万一环境没照做，killpg 可能打到
        # Bot 自己的进程组，所以这种进程只记 pid，由 _kill_group 单杀。
        pgid = 0
    job = Job(number, process, pid, pgid, path, cwd, command, "wake" if back == "wait" else back)
    _write_meta(job)

    if back == "wait" and wait > 0:
        try:
            process.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            pass
        else:
            if _group_alive(job.pgid):
                # shell 自己退了，但子进程还留在它的进程组里；不能当成“命令结束”。
                job.mode = "wake"
                _write_meta(job)
                _enroll(job)
                return _receipt(
                    job,
                    max_bytes,
                    None,
                    note=f"已转后台 #{number}（shell 已退出，退出码 {process.returncode}，但子进程还在跑）",
                )
            _remove_meta(job)
            return _finished(job, max_bytes)

    _enroll(job)
    return _receipt(job, max_bytes, wait if back == "wait" else None)


def _allocate() -> tuple[int, str, object]:
    """从 1 起找一个还没用过的任务号，顺手把日志文件建好。"""
    os.makedirs(_ROOT, exist_ok=True)
    number = 1
    while True:
        path = _log_path(number)
        if not os.path.exists(path) and not os.path.exists(_meta_path(number)):
            try:
                return number, path, open(path, "xb")
            except FileExistsError:
                pass
        number += 1


def _enroll(job: Job) -> None:
    with _lock:
        _tasks[job.number] = job
    job.thread = threading.Thread(target=_watch, args=(job,), daemon=True, name=f"shell-{job.number}")
    job.thread.start()


def _finished(job: Job, max_bytes: int) -> str:
    """等到命令跑完：照旧返回退出码和输出，然后删掉这次临时日志。"""
    code = job.process.returncode
    text = _read_output(job.path, max_bytes)
    _remove(job.path)
    _remove_meta(job)
    elapsed = time.time() - job.started
    body = text.rstrip() or "(无输出)"
    return f"cwd: {job.cwd} | exit: {code} | 跑了 {elapsed:.1f}s\n--- 输出 ---\n{body}"


def _receipt(job: Job, max_bytes: int, waited: float | None, note: str = "") -> str:
    elapsed = time.time() - job.started
    text, size = _read_tail(job.path, max_bytes)
    lines = _line_count(job.path)
    if lines is None:
        count = f"{_human(size)}（太大，没数行）"
    else:
        count = f"{lines} 行"
    last = _last_line(text) or "（还没有输出）"
    if note:
        state = note
    elif waited is None:
        state = f"已转后台 #{job.number}（刚启动）"
    elif waited <= 0:
        state = f"已转后台 #{job.number}（timeout=0，立刻转后台）"
    else:
        state = f"已转后台 #{job.number}（等了 {waited:g}s 没跑完）"
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
    """等整个进程组退出，把退出码和最后几十行推进她的 me 窗口。"""
    try:
        if job.process is not None:
            job.code = job.process.wait()
        while True:
            with _lock:
                stopped = job.stopped
            if job.pgid:
                alive = _group_alive(job.pgid)
            else:
                alive = bool(job.process is not None and job.process.poll() is None)
            if not alive:
                break
            # 停过但组里还有没退的进程时继续等，不要把任务从表里摘掉。
            time.sleep(0.2 if not stopped else 0.5)
    except Exception:
        _log().exception("等待命令 #%s 失败", job.number)
    with _lock:
        if job.stopped:
            _tasks.pop(job.number, None)
            _remove_meta(job)
            return
        _tasks.pop(job.number, None)
    _remove_meta(job)
    if job.announced:
        return
    job.announced = True
    _push_result(job)


def _push_result(job: Job) -> None:
    elapsed = time.time() - job.started
    text, size = _read_tail(job.path, TAIL_BYTES)
    lines = _line_count(job.path)
    count = f"{lines} 行" if lines is not None else _human(size)
    tail = "\n".join(text.splitlines()[-40:]).rstrip()
    code = "退出码未知" if job.code is None else f"退出码 {job.code}"
    if job.adopted:
        head = f"命令 #{job.number} 的进程组结束了（{code}"
    else:
        head = f"命令 #{job.number} 结束了（{code}"
    message = f"{head}，跑了 {elapsed:.1f}s，输出 {count}，文件 {job.path}）"
    if job.command:
        message += f"\n命令: {job.command[:200]}"
    if tail:
        message += f"\n--- 最后几行 ---\n{tail}"
    if len(message) > RESULT_LIMIT:
        message = message[:RESULT_LIMIT] + f"\n……（后面还有 {len(message) - RESULT_LIMIT} 字没放进来）"
    try:
        from mods import chat

        wake = f"命令 #{job.number} 结束（{code}）" if job.mode == "wake" else ""
        chat.push(chat.windows.ME, "命令", message, wake=wake)
    except Exception:
        _log().exception("命令 #%s 的结果没能推进 me 窗口", job.number)


def stop(task: str) -> str:
    """按任务号停掉后台命令，连同它启动的子进程。"""
    _ensure_recovered()
    number = _parse_number(task)
    if number is None:
        return f"任务号不对：写成数字，例如 stop_command(\"{task}\")"
    with _lock:
        job = _tasks.get(number)
        if job is not None:
            if not _alive(job):
                job.stopped = True
                _tasks.pop(number, None)
                _remove_meta(job)
                return f"命令 #{number} 早就结束了（输出在 {job.path}）"
            job.stopped = True
    if job is None:
        path = _log_path(number)
        if os.path.exists(path):
            return f"命令 #{number} 早就结束了（输出在 {path}）"
        return f"没有命令 #{number}"
    gone = _kill_group(job)
    if gone:
        return f"已停命令 #{number}（连同它启动的子进程；输出在 {job.path}）"
    return f"已给命令 #{number} 发停止信号，但它的进程组还没退（输出在 {job.path}）"


def running_hint() -> str:
    """还在跑的命令，一行一条；每次请求重新算，不存。"""
    _ensure_recovered()
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


def _alive(job: Job) -> bool:
    if job.pgid:
        return _group_alive(job.pgid)
    return bool(job.process is not None and job.process.poll() is None)


def _kill_group(job: Job) -> bool:
    """先 SIGTERM 整个进程组，宽限几秒再 SIGKILL；返回组是否已经清空。"""
    if job.pgid == 0:
        # 不是自己的进程组时只杀 leader，避免打到别人的组。
        try:
            if job.process is not None:
                job.process.kill()
        except ProcessLookupError:
            pass
        return True
    if not _group_alive(job.pgid):
        return True
    try:
        os.killpg(job.pgid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + GRACE_SECONDS
    while time.monotonic() < deadline:
        if not _group_alive(job.pgid):
            return True
        time.sleep(0.05)
    try:
        os.killpg(job.pgid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not _group_alive(job.pgid):
            return True
        time.sleep(0.05)
    return not _group_alive(job.pgid)


def _parse_number(task: str) -> int | None:
    found = re.fullmatch(r"\s*#?(\d+)\s*", str(task))
    return int(found.group(1)) if found else None


def _meta_path(number: int) -> str:
    return os.path.join(_ROOT, f"{number}.json")


def _log_path(number: int) -> str:
    return os.path.join(_ROOT, f"{number}.log")


def _write_meta(job: Job) -> None:
    data = {
        "number": job.number,
        "pid": job.pid,
        "pgid": job.pgid,
        "path": job.path,
        "cwd": job.cwd,
        "command": job.command,
        "started": job.started,
        "mode": job.mode,
    }
    temporary = job.meta + ".tmp"
    try:
        os.makedirs(_ROOT, exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False)
        os.replace(temporary, job.meta)
    except OSError:
        _log().debug("命令 #%s 的元数据没写成", job.number, exc_info=True)


def _remove_meta(job: Job) -> None:
    _remove(job.meta)
    _remove(job.meta + ".tmp")


def _ensure_recovered() -> None:
    global _recovered
    if _recovered:
        return
    with _lock:
        if _recovered:
            return
        _recovered = True
    _recover()


def _recover() -> None:
    """重启后接管日志旁还活着的进程组；元数据在但组已经没了，就删掉元数据。"""
    try:
        names = os.listdir(_ROOT)
    except OSError:
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(_ROOT, name)
        try:
            with open(path, encoding="utf-8") as file:
                data = json.load(file)
            number = int(data.get("number", name[:-5]))
            pgid = int(data.get("pgid") or 0)
        except (OSError, ValueError, TypeError):
            _remove(path)
            continue
        if number <= 0 or pgid <= 1 or pgid == os.getpgid(0) or not _group_alive(pgid):
            _remove(path)
            continue
        with _lock:
            if number in _tasks:
                continue
            job = Job(
                number,
                None,
                int(data.get("pid") or 0),
                pgid,
                str(data.get("path") or _log_path(number)),
                str(data.get("cwd") or ""),
                str(data.get("command") or ""),
                str(data.get("mode") if data.get("mode") in MODES else "quiet"),
                float(data.get("started") or time.time()),
                adopted=True,
            )
            _tasks[number] = job
            job.thread = threading.Thread(target=_watch, args=(job,), daemon=True, name=f"shell-{number}")
            job.thread.start()


def _group_alive(pgid: int) -> bool:
    return bool(_group_pids(pgid))


def _group_pids(pgid: int) -> list[int]:
    """扫 /proc 找 pgrp 等于 *pgid* 的进程；跳过僵尸，避免组长死了还显示在跑。"""
    if not pgid:
        return []
    try:
        entries = os.listdir("/proc")
    except OSError:
        try:
            os.killpg(pgid, 0)
            return [pgid]
        except OSError:
            return []
    found = []
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as file:
                data = file.read()
            end = data.rfind(b")")
            if end < 0:
                continue
            fields = data[end + 2 :].split()
            if len(fields) < 3 or fields[0] == b"Z":
                continue
            if int(fields[2]) == pgid:
                found.append(int(entry))
        except (OSError, ValueError):
            continue
    return found


def _cleanup() -> None:
    """退出时收掉自己起的进程组；崩溃留下的由下次启动的 _recover 接管。"""
    with _lock:
        jobs = list(_tasks.values())
    for job in jobs:
        job.stopped = True
        try:
            if _alive(job):
                _kill_group(job)
        except Exception:
            _log().debug("退出时清理命令 #%s 失败", job.number, exc_info=True)
        _remove_meta(job)
    with _lock:
        _tasks.clear()


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


atexit.register(_cleanup)
