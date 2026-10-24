"""后台任务：调用时声明了不等结果（`back` 是 wake 或 quiet）的工具调用，排进一条队列依次跑。

调用立刻拿到一张回执（任务号、收下的时刻），做完以后结果推进她的 `me` 窗口：wake 叫醒她，
quiet 只记下、不叫她，她下次醒来时看得到。结果里的图（`attach` 写的那行图片引用）换成
`[CQ:image]`，画面里照样是图。

WHY: 维护者 2026-10-24：像 `final_call` 一样，由发起调用的一方决定结果以什么方式回来；做成
通用的，每个函数自己设默认方式（函数属性 `back`），没设的和以前一样阻塞，也不多一个参数。
参照 Cortico 的 `mc_do`：下单立刻回执，结果作为事件送回来。只有一条队列、依次跑：手机只有一个
身体，两个后台脚本同时去推摇杆就乱了。她自己直接调的工具不排这条队，和后台任务可能同时进行。
"""

from __future__ import annotations

import itertools
import queue
import re
import threading
import time

from mods import log


MODES = ("wait", "wake", "quiet")
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


def submit(name: str, scope, run, mode: str) -> str:
    """把 *run*（在 *scope* 这份上下文里跑）排进队列，返回给她看的回执。"""
    global _worker
    number = next(_numbers)
    at = time.strftime("%H:%M:%S")
    with _lock:
        ahead = _pending.qsize() + (_running is not None)
        _pending.put((number, name, scope, run, mode, at))
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, daemon=True, name="tool-tasks")
            _worker.start()
    queued = f"前面还有 {ahead} 件，" if ahead else ""
    back = "推到 me 窗口叫醒你" if mode == "wake" else "记进 me 窗口，不叫你，下次醒来看得到"
    return f"任务 #{number} {name} 收下了（{at}）：在后台{queued}做完了{back}"


def _work() -> None:
    global _running
    while True:
        number, name, scope, run, mode, at = _pending.get()
        _running = number
        began = time.time()
        try:
            result, done = str(scope.run(run)), True
        except Exception as error:
            result, done = f"{type(error).__name__}: {error}", False
        finally:
            _running = None
        seconds = round(time.time() - began, 1)
        if len(result) > MAX_RESULT:
            result = result[:MAX_RESULT] + f"\n……（后面还有 {len(result) - MAX_RESULT} 字没放进来）"
        state = "做完了" if done else "出错了"
        text = f"任务 #{number} {name}（{at} 收下，跑了 {seconds:g} 秒）{state}：\n{_pictures(result)}"
        try:
            from mods import chat

            chat.push(chat.windows.ME, "任务", text, wake=f"任务 #{number} {name} {state}" if mode == "wake" else "")
        except Exception:
            _stream.exception("后台任务 #%s %s 的结果没能推进 me 窗口", number, name)


def _pictures(text: str) -> str:
    from mods import cq

    return _MARKDOWN_IMAGE.sub(lambda found: cq.cq("image", url=found.group(1)), text)
