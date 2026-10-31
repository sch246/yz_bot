"""Current event, per-interaction continuations, and window locks."""

from __future__ import annotations

from queue import Full, Queue
import threading
from typing import Any


class InteractionCancelled(Exception):
    """Raised inside a blocking interaction when its user sends ``^C``."""


class MessageWaiter:
    """The minimal blocking waiter used by chat-side ``input()``."""

    def __init__(self) -> None:
        self._queue: Queue[dict | BaseException] = Queue(maxsize=1)

    def deliver(self, event: dict) -> None:
        try:
            self._queue.put_nowait(event)
        except Full as error:
            raise RuntimeError("message waiter was already completed") from error

    def cancel(self) -> None:
        try:
            self._queue.put_nowait(InteractionCancelled())
        except Full:
            pass

    def wait(self, timeout: float | None = None) -> dict:
        value = self._queue.get(timeout=timeout)
        if isinstance(value, BaseException):
            raise value
        return value


_local = threading.local()
_latest: dict | None = None
_waiters: dict[tuple[Any, Any], Any] = {}
_lock = threading.RLock()


def set_current(event: dict | None) -> dict | None:
    """Set the event associated with the calling execution thread."""
    global _latest
    _local.event = event
    if event is not None:
        with _lock:
            _latest = event
    return event


def current(event: dict | None = None) -> dict | None:
    """Get the calling thread's event; a non-None argument sets it first."""
    if event is not None:
        return set_current(event)
    return getattr(_local, "event", None)


def latest() -> dict | None:
    """Return the latest event seen by the Bot, for diagnostics only."""
    with _lock:
        return _latest


def interaction_key(event: dict | None = None) -> tuple[Any, Any]:
    """Return ``(group, person)``; private conversations use ``None`` as group.

    One line of interaction, not one chat window: two people in the same group
    have different keys here, which is what lets a ``yield`` wait for the right
    person.  ``history.window`` is the other one, and keys the shared history.

    WHY: 私聊那一半是 ``target_id``——这条私聊的对端——不是 ``user_id``。这一位
    答的是"在和谁说话"，与"这句谁发的"无关：Bot 自己发出的回声指向同一条线。
    """
    event = current() if event is None else event
    if event is None:
        raise RuntimeError("the current event has no interaction line")
    if event.get("group_id") is not None:
        person = event.get("user_id")
    else:
        person = event.get("target_id")
    if person is None:
        raise RuntimeError("the current event has no interaction line")
    return event.get("group_id"), person


def register_waiter(key: tuple[Any, Any], waiter: Any) -> Any:
    """Install the sole continuation for an interaction line."""
    with _lock:
        previous = _waiters.get(key)
        if previous is not None and previous is not waiter:
            raise RuntimeError(f"interaction line {key!r} already has a waiter")
        _waiters[key] = waiter
    return waiter


def pop_waiter(key: tuple[Any, Any]) -> Any | None:
    with _lock:
        return _waiters.pop(key, None)


def cancel(key: tuple[Any, Any]) -> bool:
    """Remove and wake the continuation on *key*, if one exists."""
    waiter = pop_waiter(key)
    if waiter is None:
        return False
    cancel_waiter = getattr(waiter, "cancel", None)
    if callable(cancel_waiter):
        cancel_waiter()
    elif hasattr(waiter, "close"):
        waiter.close()
    elif hasattr(waiter, "put_nowait"):
        waiter.put_nowait(InteractionCancelled())
    return True


_window_locks: dict[Any, threading.RLock] = {}
_window_locks_guard = threading.Lock()
_arrival_links: dict[int, tuple[dict, str | None]] = {}
_arrival_links_guard = threading.Lock()


def window_lock(key: Any) -> threading.RLock:
    """Serialize one window's chatlog writer, arrival, and formal consumption."""
    with _window_locks_guard:
        return _window_locks.setdefault(key, threading.RLock())


def remember_arrival(event: dict, arrival: str | None) -> None:
    """Keep the decision only for this dispatch; never evict an in-flight capture."""
    with _arrival_links_guard:
        _arrival_links[id(event)] = (event, arrival)


def release_arrival(event: dict) -> None:
    """Release a completed dispatch without evicting another in-flight event."""
    with _arrival_links_guard:
        linked = _arrival_links.get(id(event))
        if linked is not None and linked[0] is event:
            del _arrival_links[id(event)]


def event_arrival(event: dict) -> str | None:
    """Resolve a routed event; a miss is a routing failure, not new mail."""
    with _arrival_links_guard:
        linked = _arrival_links.get(id(event))
        if linked is None or linked[0] is not event:
            raise RuntimeError("聊天事件没有到达号；不能在 capture 中补造")
        return linked[1]



def clear_current() -> None:
    if hasattr(_local, "event"):
        del _local.event


def agent_mode() -> bool:
    return bool(getattr(_local, "agent_mode", False))


def set_agent_mode(enabled: bool) -> None:
    _local.agent_mode = enabled
