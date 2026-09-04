"""Current event, per-interaction continuations, and per-window LLM turns."""

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


class WindowTurn:
    """One chat window's in-flight LLM turn: its interject queue and stop flag.

    WHY: 这里按**窗口**登记，而不是 interaction_key 的 (窗口, 用户)。插话和 ^C 都是
    任何人可用的：LLM 上下文本来就整个窗口共享，只让触发者能停，群里其他人就无法制止
    一轮跑偏的生成。这与 _waiters 的粒度不同，所以是另一份登记，不要合并。
    """

    def __init__(self, key: Any) -> None:
        self.key = key
        self._lock = threading.RLock()
        self._pending: list[dict] = []
        self._triggered = False
        self._cancelled = False

    def interject(self, event: dict, *, trigger: bool = False) -> None:
        """Queue one event that arrived while this turn was running.

        WHY: 分级是有意的。任何消息都进队列（让模型看到更多上下文），但只有原本就会
        触发聊天的消息（at、名字开头、poke）才置位 trigger，让本轮结束后再跑一轮。
        否则普通闲聊会让 Bot 无限续聊下去。
        """
        with self._lock:
            self._pending.append(event)
            if trigger:
                self._triggered = True

    def take_pending(self) -> list[dict]:
        """Hand over everything queued so far, leaving the trigger flag alone."""
        with self._lock:
            pending, self._pending = self._pending, []
            return pending

    def mark_trigger(self) -> None:
        """Ask for one more round without queueing an event.

        The event is already in history, so the next round's context rebuild sees
        it; what is missing is only the reason to run that round.
        """
        with self._lock:
            self._triggered = True

    def consume_trigger(self) -> bool:
        """Report and clear whether a triggering message arrived this round."""
        with self._lock:
            triggered, self._triggered = self._triggered, False
            return triggered

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled


_turns: dict[Any, WindowTurn] = {}


def begin_turn(key: Any) -> tuple[WindowTurn, bool]:
    """Claim *key*'s LLM turn; the second caller joins instead of starting one.

    Returns ``(turn, owner)``.  Only the owner drives the model; a non-owner has
    nothing to do beyond queueing its event, which keeps a second at-message from
    starting a concurrent generation that would read context and speak on its own.
    """
    with _lock:
        turn = _turns.get(key)
        if turn is not None:
            return turn, False
        turn = WindowTurn(key)
        _turns[key] = turn
        return turn, True


def get_turn(key: Any) -> WindowTurn | None:
    with _lock:
        return _turns.get(key)


def end_turn(key: Any, turn: WindowTurn) -> None:
    with _lock:
        if _turns.get(key) is turn:
            del _turns[key]


def finish_turn(key: Any, turn: WindowTurn) -> bool:
    """Close *key*'s turn, or keep it open when a trigger arrived.

    Checking the flag and removing the registration under one lock is what keeps
    an at-message that lands right as the turn ends from being dropped: either it
    is seen here and the turn runs again, or it arrives after removal and starts a
    turn of its own.
    """
    with _lock:
        if turn.consume_trigger():
            return True
        if _turns.get(key) is turn:
            del _turns[key]
        return False


def cancel_turn(key: Any) -> bool:
    """Ask *key*'s running turn to stop at its next checkpoint."""
    turn = get_turn(key)
    if turn is None:
        return False
    turn.cancel()
    return True


def clear_current() -> None:
    if hasattr(_local, "event"):
        del _local.event
