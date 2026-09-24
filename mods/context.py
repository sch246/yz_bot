"""Current event, per-interaction continuations, per-window mailboxes and LLM turns."""

from __future__ import annotations

from dataclasses import dataclass
from queue import Full, Queue
import threading
from typing import Any, Callable


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


@dataclass
class MailEntry:
    """One event in a window mailbox and the activation fact fixed at arrival."""

    seq: int
    event: dict
    activated: bool = False
    arrival: str = ""


class Mailbox:
    """One window's buffer: what arrived and has not entered the context yet.

    WHY: 它按**窗口**登记，和 `_turns` **并列**，而且**不随一轮生灭**——这是它和
    `WindowTurn` 最重要的区别，也是把它摘出来的全部理由。轮是一次生成的生命周期，
    邮箱是这个窗口的东西；以后它还要升级成追加式的时序记录（「柚子什么时候知道的」
    这条轴今天没有任何地方记着），那更是跨轮的。见
    docs/working/proposals/mail-and-activation.md 3.0 与九点八。

    WHY: 自己带锁，而不是借 `WindowTurn` 的。轮会消失，锁不能跟着消失——不然
    「邮箱不随轮生灭」这句话在并发下就是假的。聊天记录写入与 mail 入列由 `record`
    在这把锁里一次提交；开轮时的历史重建与水位线推进由 `rebuild` 在同一把锁里完成。
    因此 history 与 mail 观察的是同一个截面，不需要靠事后去重补两份状态之间的竞态。
    """

    def __init__(self, key: Any) -> None:
        self.key = key
        self._lock = threading.RLock()
        # 按到达顺序排好的条目。序号是**单调递增**的，`_base` 是 `_entries[0]` 的序号，
        # 所以修剪掉开头的已读条目不会让后面的序号跟着变。
        self._entries: list[MailEntry] = []
        self._base = 0
        # 水位线：序号 < `_read` 的条目都已经进过这个窗口的上下文了。
        self._read = 0
        from mods import oplog
        self._entries = [MailEntry(index, item["event"], item["activated"], item["arrival"])
                         for index, item in enumerate(oplog.unread(key))]

    def _add(self, event: dict, *, activated: bool = False) -> MailEntry:
        from mods import oplog
        arrival = oplog.arrive(self.key, event, activated=activated)
        entry = MailEntry(self._base + len(self._entries), event, activated, arrival)
        self._entries.append(entry)
        return entry

    def add(self, event: dict, *, activated: bool = False) -> int:
        """Put one event in and return its sequence number."""
        with self._lock:
            return self._add(event, activated=activated).seq

    def record(self, event: dict, write: Callable[[], Any]) -> Any:
        """Commit one durable history write and its mailbox entry together.

        The writer runs while the mailbox is locked.  `rebuild` takes the same
        lock around its history snapshot, so a reader sees either both facts or
        neither; it can never rebuild an event whose mail entry has not arrived.
        """
        with self._lock:
            result = write()
            if result is not None:
                self._add(event)
            return result

    def ensure(self, event: dict) -> MailEntry:
        """Return *event*'s entry, adding it for non-router callers if needed."""
        with self._lock:
            for entry in reversed(self._entries):
                if entry.event is event:
                    return entry
            return self._add(event)

    def activate(self, event: dict) -> bool:
        """Mark an unread event active; return false when it was already read."""
        with self._lock:
            entry = next((item for item in reversed(self._entries) if item.event is event), None)
            if entry is None:
                entry = self._add(event)
            if entry.seq < self._read:
                return False
            from mods import oplog
            oplog.activate(entry.arrival)
            entry.activated = True
            return True

    def advance(self, project: Callable[[list[MailEntry]], Any] | None = None) -> Any:
        """Move the watermark to the end and return everything it crossed.

        Provider 用它读取建会话之后到达的段；建会话走 `rebuild`，但最终也调用同一个
        `_advance`。水位线怎么算、何时修剪因此只有一处。
        """
        with self._lock:
            if project is not None:
                projected = project(list(self._entries[self._read - self._base:]))
                self._advance()
                return projected
            return self._advance()

    def _advance(self) -> list[MailEntry]:
        start = self._read - self._base
        crossed = list(self._entries[start:])
        self._read = self._base + len(self._entries)
        self._trim()
        return crossed

    def rebuild(self, builder: Callable[[list[MailEntry]], Any]) -> tuple[Any, list[MailEntry]]:
        """Build from history and consume the same mailbox snapshot atomically."""
        with self._lock:
            crossed = list(self._entries[self._read - self._base:])
            built = builder(crossed)
            return built, self._advance()

    def unread(self) -> list[MailEntry]:
        """Look at what has not entered the context yet, without consuming it."""
        with self._lock:
            return list(self._entries[self._read - self._base:])

    def has_activation(self) -> bool:
        """Return whether an unread activated entry keeps the red dot lit."""
        with self._lock:
            start = self._read - self._base
            return any(entry.activated for entry in self._entries[start:])

    def _trim(self) -> None:
        """Drop consumed entries past the retention tail; never drop unread ones.

        WHY: 只修剪**水位线之前**的。之后的那些是「还没进过上下文」，丢了就是丢消息，
        而这个模块是它们在内存里唯一的落点。之前的那些今天没有任何消费者，留一小段
        纯粹为了出事时能看，所以一个上界就够。

        WHY: 这是**临时**的内存形态。主观时间轴（「柚子什么时候知道的」）最终要落成
        追加式文件，那时候「留多少」由存储回答，不再由这个上界回答。删除条件：
        mail 升级成时序记录之后。见 docs/working/proposals/mail-and-activation.md 第五节。
        """
        consumed = self._read - self._base
        if consumed > _MAILBOX_RETAIN:
            drop = consumed - _MAILBOX_RETAIN
            del self._entries[:drop]
            self._base += drop

    @property
    def watermark(self) -> int:
        with self._lock:
            return self._read

    def __len__(self) -> int:
        """**未读**条数——红点要问的就是这个，不是总条数。"""
        with self._lock:
            return len(self._entries) - (self._read - self._base)


# 水位线之前还留在内存里的条目上界，见 Mailbox._trim。对齐 history.MAX_LEN 只是为了
# 两边「内存里留多久」的量级一致，没有哪条逻辑依赖它们相等。
_MAILBOX_RETAIN = 256

_mailboxes: dict[Any, Mailbox] = {}


def mailbox(key: Any) -> Mailbox:
    """This window's mailbox, created on first use and then kept.

    WHY: 不删。窗口数量有界（群 + 私聊对端），而一个空邮箱只是一个空列表；
    反过来「用完就删」会把它退回成轮级对象，正是这一步要拆掉的那件事。
    """
    with _lock:
        box = _mailboxes.get(key)
        if box is None:
            box = _mailboxes[key] = Mailbox(key)
        return box


class WindowTurn:
    """One chat window's in-flight LLM reader and its stop flag.

    WHY: 这里按**窗口**登记，而不是 interaction_key 的 (窗口, 用户)。插话和 ^C 都是
    任何人可用的：LLM 上下文本来就整个窗口共享，只让触发者能停，群里其他人就无法制止
    一轮跑偏的生成。这与 _waiters 的粒度不同，所以是另一份登记，不要合并。

    WHY: 缓冲区与红点都不在这里。`Mailbox` 的未读条目是唯一事实；其中是否还有
    `activated` 条目就是红点。轮只保留正在执行与是否取消，不再复制一份 trigger 状态。
    """

    def __init__(self, key: Any) -> None:
        self.key = key
        self._lock = threading.RLock()
        self.mail = mailbox(key)
        self._cancelled = False

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

    Returns ``(turn, owner)``.  Only the owner drives the model; the event is
    already in mail, so a non-owner has nothing else to do.  This keeps a second
    at-message from starting a concurrent generation in the same window.
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
    """Close *key*'s turn, or keep it open while activated mail remains unread.

    The unread mailbox is the authority.  Under `_lock`, either an activated
    entry is already visible here and keeps this reader, or the turn is removed;
    a later activator then claims a new reader through `begin_turn`.  `capture_chat`
    never looks up a turn before delivery, so there is no detached-turn race.
    """
    with _lock:
        if turn.mail.has_activation():
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
