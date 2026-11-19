"""后台任务：调用时声明了不等结果（`back` 是 wake 或 quiet）的工具调用，排进一条队列依次跑。

调用立刻拿到一张回执（任务号、收下的时刻），做完以后结果推进她的 `me` 窗口：wake 叫醒她，
quiet 只记下、不叫她，她下次醒来时看得到。结果里的图（`attach` 写的那行图片引用）换成
`[CQ:image]`，画面里照样是图。

WHY: 维护者 2026-10-24：像 `final_call` 一样，由发起调用的一方决定结果以什么方式回来；做成
通用的，每个函数自己决定默认方式（可以设函数属性 `back`，也可以显式参数），没设的和以前一样
阻塞，也不多一个参数。参照 Cortico 的 `mc_do`：下单立刻回执，结果作为事件送回来。只有一条队列、
依次跑：手机只有一个身体，两个后台脚本同时去推摇杆就乱了。她自己直接调的工具不排这条队，和
后台任务可能同时进行。

`detached=True` 是给 `exec_code` 的例外：它的 `timeout=0` 可能真的无限等，排进这条单队列会
把后面的手机工具永远堵住，所以它自己起一条线程。队列的“依次跑”仍只约束排队的那批。
"""

from __future__ import annotations

import itertools
import queue
import re
import threading
import time

from mods import log


MODES = ("wait", "wake", "quiet")
LINE_FG_SECONDS = 5.0
# 推进 me 窗口的结果最多留多少字：chatlog 会一直存着它。
MAX_RESULT = 10_000

_stream = log.stream("tools")
_pending: queue.Queue = queue.Queue()
_numbers = itertools.count(1)
_worker: threading.Thread | None = None
# 正在跑的那件（回执里"前面还有几件"要算上它）。
_running: int | None = None
_lock = threading.Lock()
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


def submit(name: str, scope, run, mode: str, detached: bool = False, *, origin_line: str = "") -> str:
    """把 *run*（在 *scope* 这份上下文里跑）排进队列，返回给她看的回执。

    *detached* 为真时不排队，直接起一条独立线程；回执不报“前面还有几件”。
    """
    global _worker
    number = next(_numbers)
    at = time.strftime("%H:%M:%S")
    if detached:
        threading.Thread(
            target=_run,
            args=(number, name, scope, run, mode, at, origin_line),
            daemon=True,
            name=f"tool-task-{number}",
        ).start()
        queued = ""
    else:
        with _lock:
            ahead = _pending.qsize() + (_running is not None)
            _pending.put((number, name, scope, run, mode, at, origin_line))
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(target=_work, daemon=True, name="tool-tasks")
                _worker.start()
        queued = f"前面还有 {ahead} 件，" if ahead else ""
    back = "推到 me 窗口叫醒你" if mode == "wake" else "记进 me 窗口，不叫你，下次醒来看得到"
    prefix = f"[已转入后台运行 task_id=#{number}；完成后会自动唤醒本线]\n" if mode == "wake" else ""
    return f"{prefix}任务 #{number} {name} 收下了（{at}）：在后台{queued}做完了{back}"


def run_with_fg_timeout(name: str, task_fn, fg_wait: float, *, origin_line: str = "") -> str:
    """前台最多等待 *fg_wait* 秒；超时未完不杀任务，自动转入后台并在完成时唤醒源线。"""
    done = threading.Event()
    box: dict = {}
    began = time.time()
    at = time.strftime("%H:%M:%S")
    detached_flag = False
    state_lock = threading.Lock()
    number_holder: list[int] = []

    def worker() -> None:
        try:
            res, ok = str(task_fn()), True
        except Exception as error:
            res, ok = f"{type(error).__name__}: {error}", False
        with state_lock:
            box["result"] = res
            box["ok"] = ok
            was_detached = detached_flag
            num = number_holder[0] if number_holder else 0
        done.set()
        if was_detached:
            _deliver(num, name, res, ok, at, time.time() - began, "wake", origin_line=origin_line)

    threading.Thread(target=worker, daemon=True, name=f"fg-task-{name}").start()
    if done.wait(max(0.0, float(fg_wait))):
        return box["result"]

    with state_lock:
        if "result" in box:
            return box["result"]
        num = next(_numbers)
        number_holder.append(num)
        detached_flag = True
    return (
        f"[已转入后台运行 task_id=#{num}；完成后会自动唤醒本线]\n"
        f"任务 #{num} {name} 前台等待 {fg_wait:g}s 未跑完（{at}），已转入后台继续运行。"
    )


def _work() -> None:
    global _running
    while True:
        number, name, scope, run, mode, at, origin_line = _pending.get()
        _running = number
        try:
            _run(number, name, scope, run, mode, at, origin_line)
        finally:
            _running = None


def _deliver(number: int, name: str, result: str, done: bool, at: str, elapsed: float, mode: str,
             *, origin_line: str = "") -> None:
    seconds = round(elapsed, 1)
    if len(result) > MAX_RESULT:
        result = result[:MAX_RESULT] + f"\n……（后面还有 {len(result) - MAX_RESULT} 字没放进来）"
    state = "做完了" if done else "出错了"
    text = f"任务 #{number} {name}（{at} 收下，跑了 {seconds:g} 秒）{state}：\n{_pictures(result)}"
    try:
        from mods import chat

        wake = f"任务 #{number} {name} {state}" if mode == "wake" else ""
        if origin_line:
            from mods.chat import line as _line

            _line.send("system:task", origin_line, text, wake=(mode == "wake"))
            chat.push(chat.windows.ME, "任务", text, wake="")
        else:
            chat.push(chat.windows.ME, "任务", text, wake=wake)
    except Exception:
        _stream.exception("后台任务 #%s %s 的结果没能推进 me 窗口", number, name)


def _run(number: int, name: str, scope, run, mode: str, at: str, origin_line: str = "") -> None:
    began = time.time()
    try:
        result, done = str(scope.run(run)), True
    except Exception as error:
        result, done = f"{type(error).__name__}: {error}", False
    _deliver(number, name, result, done, at, time.time() - began, mode, origin_line=origin_line)


def _pictures(text: str) -> str:
    from mods import cq

    return _MARKDOWN_IMAGE.sub(lambda found: cq.cq("image", url=found.group(1)), text)
