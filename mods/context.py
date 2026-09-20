"""Current event, per-interaction continuations, per-window mailboxes and LLM turns."""

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


class Mailbox:
    """One window's buffer: what arrived and has not entered the context yet.

    WHY: 它按**窗口**登记，和 `_turns` **并列**，而且**不随一轮生灭**——这是它和
    `WindowTurn` 最重要的区别，也是把它摘出来的全部理由。轮是一次生成的生命周期，
    邮箱是这个窗口的东西；以后它还要升级成追加式的时序记录（「柚子什么时候知道的」
    这条轴今天没有任何地方记着），那更是跨轮的。见
    docs/working/proposals/mail-and-activation.md 3.0 与九点八。

    WHY: **这一步只搬容器，不改任何策略。** 入列的条件仍然是「这个窗口正跑着一轮」
    （判据在 `chat.capture_chat`），排空时机仍然是每次子请求之前加开轮那次丢弃。
    「邮箱比一轮活得久」在**可观察行为**上要等水位线那一步才真正生效——在那之前，
    上一轮残留的条目会在下一轮开局被 `take` 丢掉，和过去随 `end_turn` 一起销毁
    完全等价。别以为搬完容器语义就已经变了。

    WHY: 自己带锁，而不是借 `WindowTurn` 的。轮会消失，锁不能跟着消失——不然
    「邮箱不随轮生灭」这句话在并发下就是假的。调用方仍然从 `WindowTurn.interject`
    进来，于是两把锁的获取顺序恒为 turn→mail，不存在反向路径。
    """

    def __init__(self, key: Any) -> None:
        self.key = key
        self._lock = threading.RLock()
        self._entries: list[dict] = []

    def add(self, event: dict) -> None:
        """Put one event in, unconditionally.  Nothing here decides anything."""
        with self._lock:
            self._entries.append(event)

    def take(self) -> list[dict]:
        """Hand over everything buffered so far and leave the box empty."""
        with self._lock:
            entries, self._entries = self._entries, []
            return entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


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
    """One chat window's in-flight LLM turn: who asked for it, and the stop flag.

    WHY: 这里按**窗口**登记，而不是 interaction_key 的 (窗口, 用户)。插话和 ^C 都是
    任何人可用的：LLM 上下文本来就整个窗口共享，只让触发者能停，群里其他人就无法制止
    一轮跑偏的生成。这与 _waiters 的粒度不同，所以是另一份登记，不要合并。

    WHY: 缓冲区**不在这里**，在 `Mailbox`（按窗口、不随轮生灭）。这一位留着
    `interject` / `take_pending` 两个方法转发过去，是为了**不动锁的结构**：它们今天
    在同一把 `self._lock` 下把「入列」和「置触发位」做成一个原子动作，而尾缘触发不丢
    正依赖这一点（见 `finish_turn`）。把调用方直接改成两次独立调用是另一件事，
    要连同水位线一起做，不在这一步。
    """

    def __init__(self, key: Any) -> None:
        self.key = key
        self._lock = threading.RLock()
        self.mail = mailbox(key)
        # WHY: 记的是**触发事件本身**，不是一个 bool。续跑的那一轮属于要求它的那个人，
        # 而"属于谁"决定了那一轮的 op 门（tools.op_tool_visible 问的就是当前事件）。
        # 只留一个 bool 的话，续轮只能沿用开轮那个人的身份，群里任何成员都能在管理员
        # 开的轮之后要来一轮、并在那一轮里看见 op 专属模块。见 chat.chat 的续轮分支。
        self._trigger_event: dict | None = None
        self._cancelled = False

    def interject(self, event: dict, *, trigger: bool = False) -> None:
        """Put one event in this window's mailbox; *trigger* also lights the dot.

        WHY: 分级是有意的。任何消息都进邮箱（让模型看到更多上下文），但只有原本就会
        触发聊天的消息（at、名字开头、poke）才置位 trigger，让本轮结束后再跑一轮。
        否则普通闲聊会让 Bot 无限续聊下去。

        WHY: 两件事在同一把锁里，这是**承重的**：尾缘触发不丢依赖「入列与置位不可分割」
        （见 `finish_turn`）。邮箱搬走之后它们分处两个对象，所以这里显式地把邮箱那次
        写入也罩在 `self._lock` 里，而不是让调用方各调各的。
        """
        with self._lock:
            self.mail.add(event)
            if trigger:
                self._trigger_event = event

    def take_pending(self) -> list[dict]:
        """Drain this window's mailbox, leaving the trigger flag alone."""
        with self._lock:
            return self.mail.take()

    def mark_trigger(self, event: dict) -> None:
        """Ask for one more round, on behalf of *event*, without queueing it.

        The event is already in history, so the next round's context rebuild sees
        it; what is missing is only the reason to run that round -- and who that
        round belongs to.
        """
        with self._lock:
            self._trigger_event = event

    def consume_trigger(self) -> dict | None:
        """Report and clear which message asked for another round, if any.

        WHY: 同一轮里来了好几次触发时，留下的是**最后**那一次——续跑的是"最近一次还没被
        回答的请求"。方向上也更安全：非 op 在 op 之后再要一轮只会把这轮降成普通轮，反过来
        要抬高身份，op 自己必须真的开口。
        """
        with self._lock:
            triggered, self._trigger_event = self._trigger_event, None
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


def finish_turn(key: Any, turn: WindowTurn) -> dict | None:
    """Close *key*'s turn, or keep it open for the message that asked for more.

    Returns that message, so the caller can run the extra round **as** its author
    instead of as whoever opened the turn; ``None`` closes the turn.

    Checking the flag and removing the registration under one lock is what keeps
    an at-message that lands right as the turn ends from being dropped: either it
    is seen here and the turn runs again, or it arrives after removal and starts a
    turn of its own.

    WHY: **上面那条保证有一个缺口，2026-09-20 实跑确认过，这里如实记下。** 它的第二条
    分支要求 `capture_chat` 查不到这一轮；但 `capture_chat` 是先 `get_turn`（拿模块锁、
    随即放掉）、再 `turn.interject(..., trigger=True)`（只拿 turn 的锁）。若这一轮恰好
    在这两步之间收摊，触发位就被置到一个**已经摘掉登记**的 turn 上，没有任何人会再读它
    ——那条 at 被静默丢掉。窗口很窄（要求正好落在这两步之间），但它是真的。

    WHY: 没有就地修，因为正解不是再加一把锁，而是**让这条路不存在**：邮箱改成无条件
    `add`、红点改成「邮箱里有没有激活元素」这个派生谓词之后，`capture_chat` 不再需要先
    查一轮才能投递，这个交错也就没有了。见
    docs/working/proposals/mail-and-activation.md 3.0 与第十一节第 5 步；
    那一步落地时，连同这段 WHY 一起删掉。
    """
    with _lock:
        triggered = turn.consume_trigger()
        if triggered is not None:
            return triggered
        if _turns.get(key) is turn:
            del _turns[key]
        return None


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
