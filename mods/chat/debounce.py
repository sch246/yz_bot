"""Debounce and coalesce inbound chat wakes before driving the agent.

WHY: 维护者 2026-10-27（参考 MaiBot 机制）：真人聊天常分段发短句（“在吗”/“帮我看下这个”）。
直接秒回会导致前一句刚拿到锁开始思考、后一句就变成插话或拆成多次调用，浪费 token 且答复割裂。
防抖在收到首条唤醒时等待 3 秒；同窗口同一说话人继续发消息时重置计时，最多延长至 12 秒。

WHY: 判据为什么按 (窗口, 说话人) 重置而不是按整个群重置（2026-10-23 曾因整个群重置导致
热闹群里永远安静不下来、每次都拖到上限而撤掉延迟）：群里其他人的闲聊不打断当前说话人的打字等待；
只有叫醒她的那个人继续说话才重置。戳一戳与定时任务不防抖、立即触发。

WHY: 到达登记与 chatlog 写入照常立刻落账，防抖只延后 oplog.activate 与 chat()。这样既不丢消息，
防抖期间被打断 (^C) 时也不会留下悬空激活。
"""

from __future__ import annotations

import logging
import threading
import time

from mods import context, oplog, storage
import mods.chat as _chat_root
from mods.chat import windows as _windows

_log = logging.getLogger(__name__)

DEFAULT_IDLE = 3.0
DEFAULT_MAX = 12.0


def get_config() -> dict:
    cfg = storage.get("", "agent").get("debounce")
    if not isinstance(cfg, dict):
        return {"enabled": True, "idle": DEFAULT_IDLE, "max": DEFAULT_MAX}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "idle": float(cfg.get("idle", DEFAULT_IDLE)),
        "max": float(cfg.get("max", DEFAULT_MAX)),
    }


def is_enabled() -> bool:
    return get_config()["enabled"]


def is_debounceable(window: tuple) -> bool:
    """Only QQ group and private chat windows participate in conversational debounce."""
    return window is not None and len(window) >= 2 and window[0] in _windows.QQ


class _Session:
    def __init__(self, window: tuple, author: str | None, first_time: float) -> None:
        self.window = window
        self.author = str(author) if author is not None else None
        self.first_time = first_time
        self.last_time = first_time
        self.arrivals: list[tuple[str, str]] = []  # [(arrival_id, kind)]
        self.last_event: dict | None = None
        self.timer: threading.Timer | None = None


_sessions: dict[tuple, _Session] = {}
_lock = threading.RLock()


def schedule(window: tuple, author: str | None, arrival: str, kind: str, event: dict) -> bool:
    """Register an activation for *window* and start or reset the debounce timer; True if debouncing."""
    if not is_enabled() or not is_debounceable(window):
        return False
    author_str = str(author) if author is not None else None
    with _lock:
        now = time.time()
        cfg = get_config()
        idle = cfg["idle"]
        max_delay = cfg["max"]

        session = _sessions.get(window)
        if session is None:
            session = _Session(window, author_str, now)
            _sessions[window] = session
        else:
            if session.timer is not None:
                session.timer.cancel()
                session.timer = None
            session.last_time = now
            if author_str is not None:
                session.author = author_str

        if not any(a == arrival for a, _ in session.arrivals):
            session.arrivals.append((arrival, kind))
        session.last_event = event

        deadline = session.first_time + max_delay
        delay = min(idle, max(0.0, deadline - now))

        if delay <= 0:
            _sessions.pop(window, None)
            _fire(session, async_chat=True)
            return True

        def _on_timer():
            with _lock:
                s = _sessions.pop(window, None)
            if s is not None:
                _fire(s, async_chat=True)

        timer = threading.Timer(delay, _on_timer)
        timer.name = f"debounce-timer-{window[0]}"
        timer.daemon = True
        session.timer = timer
        timer.start()
        return True


def feed(window: tuple, author: str | None, event: dict) -> bool:
    """Reset the timer if *author* continues talking in a debouncing *window*; True if consumed."""
    if not is_enabled() or not is_debounceable(window):
        return False
    author_str = str(author) if author is not None else None
    with _lock:
        session = _sessions.get(window)
        if session is None:
            return False
        # WHY: 只为触发者重置；群里别人的闲聊不重置，避免热闹群把延迟拖到上限。
        if session.author is not None and author_str != session.author:
            return False

        if session.timer is not None:
            session.timer.cancel()
            session.timer = None

        now = time.time()
        session.last_time = now
        session.last_event = event

        cfg = get_config()
        idle = cfg["idle"]
        max_delay = cfg["max"]

        deadline = session.first_time + max_delay
        delay = min(idle, max(0.0, deadline - now))

        if delay <= 0:
            _sessions.pop(window, None)
            _fire(session, async_chat=True)
            return True

        def _on_timer():
            with _lock:
                s = _sessions.pop(window, None)
            if s is not None:
                _fire(s, async_chat=True)

        timer = threading.Timer(delay, _on_timer)
        timer.name = f"debounce-timer-{window[0]}"
        timer.daemon = True
        session.timer = timer
        timer.start()
        return True


def flush(window: tuple, activate_only: bool = False) -> bool:
    """Flush pending activations immediately; when activate_only is False, also triggers chat."""
    with _lock:
        session = _sessions.pop(window, None)
    if session is None:
        return False
    if session.timer is not None:
        session.timer.cancel()
        session.timer = None
    _fire(session, async_chat=not activate_only)
    return True


def cancel(window: tuple) -> bool:
    """Drop any active debounce session for *window* without activating (e.g. on ^C)."""
    with _lock:
        session = _sessions.pop(window, None)
    if session is None:
        return False
    if session.timer is not None:
        session.timer.cancel()
        session.timer = None
    return True


def cancel_all() -> None:
    """Drop all debounce timers on shutdown or reload."""
    with _lock:
        for session in _sessions.values():
            if session.timer is not None:
                session.timer.cancel()
                session.timer = None
        _sessions.clear()


def _fire(session: _Session, async_chat: bool = True) -> None:
    window = session.window
    arrivals = list(session.arrivals)
    event = session.last_event
    with context.window_lock(window):
        for arrival, kind in arrivals:
            oplog.activate(arrival, kind)
    if not async_chat:
        return

    def run() -> None:
        previous = context.current()
        if event is not None:
            context.set_current(event)
        try:
            _chat_root.chat()
        except Exception:
            _log.exception("failed to drive debounced agent for window %s", window)
        finally:
            context.set_current(previous)

    threading.Thread(target=run, name=f"debounce-wake-{window[0]}", daemon=True).start()
