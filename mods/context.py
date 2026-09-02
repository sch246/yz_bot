"""Current event and per-interaction continuation state."""

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
    """Return ``(group, user)``; private conversations use ``None`` as group.

    One line of interaction, not one chat window: two people in the same group
    have different keys here, which is what lets a ``yield`` wait for the right
    person.  ``history.window`` is the other one, and keys the shared history.
    """
    event = current() if event is None else event
    if event is None or event.get("user_id") is None:
        raise RuntimeError("the current event has no interaction line")
    return event.get("group_id"), event["user_id"]


def register_waiter(key: tuple[Any, Any], waiter: Any) -> Any:
    """Install the sole continuation for an interaction line."""
    with _lock:
        previous = _waiters.get(key)
        if previous is not None and previous is not waiter:
            raise RuntimeError(f"interaction line {key!r} already has a waiter")
        _waiters[key] = waiter
    return waiter


def get_waiter(key: tuple[Any, Any]) -> Any | None:
    with _lock:
        return _waiters.get(key)


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


def clear_current() -> None:
    if hasattr(_local, "event"):
        del _local.event
