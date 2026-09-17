"""登记一次工具执行期间可以被强制终止的子进程与执行线程。

## 它补的是哪个洞

`^C` 一直是协作式的：`llm.LLMClient.chat` 在每轮子请求之间、以及流式读取的每个 chunk 之后
检查 `should_stop`。但工具调用是**同步**执行的，检查点全落在它外面——2026-09-17 那次
`cwd="/"` 的 grep 卡死，`^C` 一点作用都没有，只能 `.reboot`；更糟的是卡住的那一轮会让整个
窗口失能，后续消息只排队、不发言。

这里把"工具执行期间在跑什么"登记下来，`^C` 于是多出两件能做的事：

1. **kill 掉当时还活着的子进程**——阻塞在 `wait`/`communicate` 上的调用因此返回；
2. **给登记过的执行线程注入一个异常**，让它从纯 Python 的忙循环里退出来。

## 登记什么、归属怎么算

- **子进程**：`subprocess.Popen.__init__` 在 `on_load` 里被包了一层，工具执行期间 spawn 的
  任何子进程都自动进登记表。接在 Popen 上而不是逐个调用点上，是因为会 spawn 的地方太多
  （`host`、`!命令`、`screen`、以及 `exec_code` 里模型自己写的任何东西），逐个去改既漏又散。
  登记只在**这次调用期间**有效：调用一结束就出表，所以工具起的常驻进程（REPL、MC 服务端）
  不会被后来的 `^C` 误杀。
- **执行线程**：只有 `run()` 起的那些子线程会登记。调用工具的那个线程**有意不登记**：对同步
  执行的工具半路注入异常，等于在它写文件写到一半时把它掀翻——那正是草籽说的"立即停止进程
  与怎么善后是两码事"。工具线程靠的是"子进程被杀之后它自己回来"，再加工具循环里的
  `should_stop` 检查。
- 归属按**窗口**算（`history.window(context.current())`），与 `^C` 的粒度一致：LLM 上下文
  本就按窗口共享，只让触发者能停会让群里其他人没法制止一轮跑偏的生成。工具在哪个线程跑，
  `to_thread` 已经把路由带过去了，所以连 `_run_bash` 那种另起的线程也能找到自己的窗口。

## 够不到的地方（有意，别当成 bug）

`PyThreadState_SetAsyncExc` 只在目标线程**回到 Python 字节码边界**时才真正抛出。卡在
`time.sleep()`、`Event.wait()`、或者一个 kill 不掉的系统调用里时，注入是挂着的，要等那个
调用自己返回。所以它是"尽快停"，不是"立刻停"。
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from mods import context


LOAD_AFTER = ("context", "history")

_log = logging.getLogger(__name__)


class Interrupted(Exception):
    """注入到被强制终止的执行线程里的异常。

    派生自 `Exception`：调用方（`exec_code`、工具循环）能按普通失败接住它，写成一条能读的
    结果，而不是把它变成谁也没接的 BaseException。
    """


class Job:
    """一次工具执行期间可以被终止的东西：它生的子进程 + 登记进来的执行线程。"""

    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.threads: set[int] = set()
        self.processes: set[Any] = set()
        self.reason = ""
        self._lock = threading.RLock()
        self._stopped = threading.Event()

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    def add_thread(self, ident: int) -> bool:
        """登记一个执行线程；这个 Job 已经停过就立刻给它注入，返回 False。"""
        with self._lock:
            self.threads.add(ident)
            stopped = self._stopped.is_set()
        if stopped:
            _async_raise(ident, Interrupted)
            return False
        return True

    def add_process(self, process: Any) -> None:
        with self._lock:
            self.processes.add(process)
            stopped = self._stopped.is_set()
        if stopped:
            _kill(process)

    def stop(self, reason: str = "") -> bool:
        """标记停止、kill 子进程、给执行线程注入中断；返回是否首次触发。"""
        with self._lock:
            first = not self._stopped.is_set()
            if reason:
                self.reason = reason
            self._stopped.set()
            processes = list(self.processes)
            threads = list(self.threads)
        for process in processes:
            _kill(process)
        if first:
            for ident in threads:
                _async_raise(ident, Interrupted)
            _log.warning(
                "watchdog 停止：%d 个子进程、%d 个执行线程（%s）",
                len(processes), len(threads), self.reason or "未说明原因",
            )
        return first


_jobs: dict[Any, list[Job]] = {}
_lock = threading.RLock()
_local = threading.local()


def owner_key() -> Any:
    """调用线程归属的窗口；没有路由信息时退回线程标识。"""
    event = context.current()
    if isinstance(event, dict) and event:
        from mods import history

        window = history.window(event)
        if window is not None:
            return window
    return threading.get_ident()


def begin() -> Job:
    """登记本次工具执行。同一个窗口可以叠多层（工具调用里再调 exec_code）。"""
    job = Job(owner_key())
    with _lock:
        _jobs.setdefault(job.owner, []).append(job)
    _local.job = job
    return job


def end(job: Job) -> None:
    with _lock:
        stack = _jobs.get(job.owner)
        if stack and job in stack:
            stack.remove(job)
            if not stack:
                del _jobs[job.owner]
    if getattr(_local, "job", None) is job:
        _local.job = None


def stop(owner: Any, reason: str = "") -> int:
    """停止 *owner* 窗口下所有登记中的执行，返回被停掉的数量。"""
    with _lock:
        stack = list(_jobs.get(owner) or ())
    return sum(1 for job in stack if job.stop(reason))


def current_job() -> Job | None:
    """这次 spawn 该记到哪个 Job 上：先看本线程登记的，再按窗口找最内层那个。"""
    if getattr(_local, "detached", 0):
        return None
    job = getattr(_local, "job", None)
    if job is not None:
        return job
    with _lock:
        stack = _jobs.get(owner_key())
        return stack[-1] if stack else None


@contextmanager
def detached() -> Iterator[None]:
    """在 with 块里 spawn 的子进程不进登记表。

    WHY: 登记表的语义是"这次工具执行期间**可以**被终止的东西"，而有些进程是**故意要长期
    活着**的——自管的 Chromium、REPL 前端这类。它们可能在一次工具调用里被懒启动（第一次
    访问网页时），但绝不该跟着那次调用一起被 `^C` 杀掉，否则下一个动作又要付一次冷启动。
    所以这里给"我要起一个常驻进程"一个显式出口，而不是事后把 pid 从表里抠掉。
    """
    previous = getattr(_local, "job", None)
    depth = getattr(_local, "detached", 0)
    _local.job = None
    _local.detached = depth + 1
    try:
        yield
    finally:
        _local.detached = depth
        _local.job = previous


def run(routine: Callable[[], Any], timeout: float = 0.0) -> Any:
    """在一个可以被终止的子线程里执行 *routine*；*timeout* 为 0 表示不限。

    超时会 kill 它启动的子进程、并向它注入 `Interrupted`，然后**在调用方**抛
    `TimeoutError`。这一点是刻意的：时限由调用方负责，所以哪怕那段代码卡在一个打断不了的
    系统调用里，调用方也一定在时限内拿回控制权——代价是那个线程可能还留着。

    环境不会自动继承：只有路由（`context.current()`）被显式带过去，别的模块的线程局部状态
    （例如图片检查台账）在新线程里是空的。
    """
    job = begin()
    origin = context.current()
    outcome: dict[str, Any] = {}
    done = threading.Event()

    def body() -> None:
        if not job.add_thread(threading.get_ident()):
            # 还没开跑就被叫停了，别再把它执行一遍。
            done.set()
            return
        context.set_current(origin)
        try:
            outcome["value"] = routine()
        except BaseException as error:
            outcome["error"] = error
        finally:
            context.clear_current()
            done.set()

    worker = threading.Thread(target=body, name="watchdog.run", daemon=True)
    try:
        worker.start()
        if not done.wait(timeout if timeout > 0 else None):
            killed = len(job.processes)
            job.stop(f"执行超过 {timeout} 秒")
            # 给它一点自己收尾的时间：中断落在字节码边界上时，这段代码接下来就会抛出来。
            done.wait(1.0)
            detail = f"已终止它启动的 {killed} 个子进程" if killed else "已中断执行线程"
            raise TimeoutError(f"执行超过 {timeout} 秒，{detail}")
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")
    finally:
        end(job)


_SetAsyncExc = ctypes.pythonapi.PyThreadState_SetAsyncExc
_SetAsyncExc.argtypes = (ctypes.c_ulong, ctypes.py_object)
_SetAsyncExc.restype = ctypes.c_int


def _async_raise(ident: int, error: type[BaseException]) -> bool:
    """往 *ident* 线程注入 *error*；只在它回到字节码边界时真正抛出。"""
    if ident == threading.get_ident():
        # 给自己注入会立刻在调用点抛出，那不是这里想要的时序。
        return False
    try:
        affected = _SetAsyncExc(ctypes.c_ulong(ident), ctypes.py_object(error))
    except Exception:
        _log.exception("watchdog 注入异常失败")
        return False
    if affected > 1:
        # 按定义不该发生；真发生了就撤销，免得误伤别的线程。
        _SetAsyncExc(ctypes.c_ulong(ident), None)
        _log.error("watchdog 注入命中了 %d 个线程，已撤销", affected)
        return False
    return affected == 1


def _kill(process: Any) -> None:
    try:
        if process.poll() is None:
            process.kill()
    except Exception:
        _log.debug("watchdog 杀进程失败", exc_info=True)


def _watch_processes() -> None:
    """把 spawn 出来的子进程接进登记表；重复调用是安全的。"""
    if getattr(subprocess.Popen, "_yuzu_watched", False):
        return
    original = subprocess.Popen.__init__

    def __init__(self, *args, **kwargs):
        original(self, *args, **kwargs)
        job = current_job()
        if job is not None:
            job.add_process(self)

    subprocess.Popen.__init__ = __init__
    subprocess.Popen._yuzu_watched = True


def on_load(_ctx) -> None:
    _watch_processes()


__all__ = ["Interrupted", "Job", "begin", "current_job", "detached", "end", "owner_key", "run", "stop"]
