"""Unread summons and one clock for debounce, waits, loops and awaker deadlines.

The queue is a projection of screen's persistent unread records. Claiming is not
consumption: only the runner's first output acknowledges the claim. The clock
only sets a work signal; it never constructs a model or drives a chat itself.
"""

from __future__ import annotations

from copy import deepcopy
import logging
import math
import threading
import time
from uuid import uuid4

from mods import history, identity, storage
from . import awaker, screen, windows

DEFAULT_IDLE = 3.0
DEFAULT_MAX = 12.0
LOOP_GAP = 2.0
LOOP_IDLE_MAX = 1800.0
_log = logging.getLogger(__name__)
_lock = threading.Condition(threading.RLock())
_jobs: dict[str, dict] = {}
_scheduled: dict[str, dict] = {}
# A deadline stays identifiable while its callback is firing. Replacing or
# cancelling it invalidates that exact dict before the final queue insertion.
_deadlines: dict[str, dict] = {}
_debouncing: dict[tuple, dict] = {}
_loop: dict | None = None
_thread: threading.Thread | None = None
_stopping = False
_generation = 0
_notify = None
ready = threading.Event()


def get_config() -> dict:
    cfg = storage.get("", "agent").get("debounce")
    if not isinstance(cfg, dict):
        cfg = {}
    return {"enabled": bool(cfg.get("enabled", True)),
            "idle": max(0.0, float(cfg.get("idle", DEFAULT_IDLE))),
            "max": max(0.0, float(cfg.get("max", DEFAULT_MAX)))}


def _signal() -> None:
    ready.set()
    if _notify is not None:
        try:
            _notify()
        except Exception:
            _log.exception("attention work notification failed")


def _put(window, *, kind, reason="", record_ids=(), author=None, event=None) -> bool:
    """Insert under _lock; callers signal only after leaving their critical section."""
    window = tuple(window)
    identities = list(dict.fromkeys(str(i) for i in record_ids))
    owned = {i for job in _jobs.values() if job["window"] == window for i in job["record_ids"]}
    if identities:
        identities = [i for i in identities if i not in owned]
        if not identities:
            return False
    elif any(job["window"] == window and job["kind"] == kind and job["reason"] == reason
             and not job["record_ids"] for job in _jobs.values()):
        return False
    key = uuid4().hex
    _jobs[key] = {"id": key, "window": window, "kind": str(kind), "reason": str(reason),
                  "record_ids": identities, "author": author, "event": deepcopy(event), "claim": None}
    return True


def enqueue(window, *, kind, reason="", record_ids=(), author=None, event=None) -> bool:
    """Queue a summon, retaining record ids, author and reason until acknowledged."""
    with _lock:
        added = _put(window, kind=kind, reason=reason, record_ids=record_ids, author=author, event=event)
    if added:
        _signal()
    return added


def _valid(job: dict) -> bool:
    if not job["record_ids"]:
        return True
    pending = screen.unread(job["window"])
    return any(pending.get(i, {}).get("kind") for i in job["record_ids"])


def claim() -> list[dict]:
    """Reserve available work. A released request can claim the same records again."""
    with _lock:
        ready.clear()
        token = uuid4().hex
        result = []
        for key, job in list(_jobs.items()):
            if not _valid(job):
                del _jobs[key]
                continue
            if job["claim"] is None:
                job["claim"] = token
                result.append(deepcopy(job))
        return result


def ack(claimed) -> None:
    """Consume only this claim's summons, even when a dismiss later restores unread."""
    for item in claimed:
        with _lock:
            job = _jobs.get(item["id"])
            if job is None or job["claim"] != item["claim"]:
                continue
            del _jobs[item["id"]]
        screen.consume(job["window"], job["record_ids"])


def release(claimed) -> None:
    released = False
    with _lock:
        for item in claimed:
            job = _jobs.get(item["id"])
            if job is not None and job["claim"] == item["claim"]:
                job["claim"] = None
                released = True
    if released:
        _signal()


def schedule(kind, due, *, window=None, reason="", **state) -> dict:
    """Replace a deadline by optional ``key``; all deadlines share the same clock."""
    due = float(due)
    if not math.isfinite(due):
        raise ValueError("due must be finite")
    key = str(state.pop("key", uuid4().hex))
    job = {"id": key, "kind": kind, "due": due, "window": tuple(window) if window is not None else None,
           "reason": str(reason), **state}
    with _lock:
        job["generation"] = _generation
        _scheduled[key] = _deadlines[key] = job
        _lock.notify_all()
    return deepcopy(job)


def cancel(kind=None, *, window=None) -> int:
    cancelled = []
    with _lock:
        keys = [key for key, job in _deadlines.items()
                if (kind is None or job["kind"] == kind)
                and (window is None or job["window"] == tuple(window))]
        for key in keys:
            _scheduled.pop(key, None)
            _deadlines.pop(key, None)
        if kind in (None, "debounce"):
            for key in list(_debouncing):
                if window is None or key[0] == tuple(window):
                    session = _debouncing.pop(key)
                    cancelled.append((session["window"], session["ids"]))
        _lock.notify_all()
    # WHY: ^C during debounce cancels the summon, not its unread body. Without
    # consuming its persistent kind, startup rescan would resurrect a cancelled
    # activation. stop() deliberately retains these kinds for ordinary restart.
    for target, identities in cancelled:
        screen.consume(target, identities)
    return len(keys)


def _debounce(window, author, identity_, kind, reason, event, now) -> None:
    cfg = get_config()
    key = (tuple(window), str(author))
    with _lock:
        session = _debouncing.setdefault(key, {"first": now, "ids": [], "window": tuple(window),
                                               "author": author, "kind": kind, "reason": reason})
        if identity_ not in session["ids"]:
            session["ids"].append(identity_)
        session["event"] = deepcopy(event)
        # WHY: 同窗口同作者的短句才重置；热闹群里别人的聊天不会拖延这个人的 3 秒。
        due = min(now + cfg["idle"], session["first"] + cfg["max"])
        schedule("debounce", due, window=window, key=f"debounce:{key}", session_key=key)


def on_record(window, event, *, recovered=False) -> bool:
    """Register a written record and decide whether it should notify the main runner.

    ``_wake_kind``/``_wake_reason`` carry an explicit chat.wake/push request;
    ``_default_wake`` carries a non-QQ producer's default trigger. These are
    caller metadata on a copy, never edits to the original OneBot event.
    """
    import mods.chat as chat
    from mods import msgs

    window = tuple(window)
    author = history.author(event) if window[0] in windows.QQ else event.get("author")
    try:
        self_id = identity.bot_id()
    except RuntimeError:
        self_id = None
    own = event.get("post_type") == "message_sent" or self_id is not None and author == self_id
    explicit = event.get("_wake_kind") if not own else None
    matched = (chat.activation_signal(event) if window[0] in windows.QQ
               else bool(event.get("_default_wake", explicit))) and not own
    # WHY: 普通离线补回不参与 follow/on/wait，也不变未读；只有当时会叫她的消息补醒。
    if recovered:
        kind = "离线时叫你" if matched else ""
        identity_ = screen.register(window, event, recovered=True, kind=kind,
                                    reason=event.get("_wake_reason", ""))
        if kind:
            if event.get("_defer_wake"):
                return True
            return enqueue(window, kind=kind, record_ids=[identity_], author=author, event=event)
        return False
    wanted, layer = awaker.triggered(window, event, author, matched) if not own else (False, None)
    waited = (awaker.take_waiting(window, author) if not own and msgs.is_msg(event) else [])
    producer = event.get("_default_wake")
    default_kind = str(producer) if isinstance(producer, str) and producer else "wake"
    kind = (str(explicit) if explicit else "poke" if msgs.is_poke(event)
            else default_kind if window[0] not in windows.QQ
            else "private" if window[0] == "private" else "mention") if wanted or explicit else ""
    reason = str(event.get("_wake_reason", ""))
    if layer and wanted:
        kind = f"覆盖层 {awaker.describe(layer)}"
        reason = layer["reason"]
    if waited:
        kind = "；".join(filter(None, [kind, *(f"等到了（{r['key']}）" for r in waited)]))
        reason = "；".join(filter(None, [reason, *(r["reason"] for r in waited)]))
    identity_ = screen.register(window, event, kind=kind, reason=reason)
    key = (window, str(author))
    # WHY: 同作者未再点名的后续短句也延长等待，但不会凭空新增一个召唤。
    if not kind:
        with _lock:
            waiting = _debouncing.get(key)
        if waiting and not own and msgs.is_msg(event):
            _debounce(window, author, identity_, waiting["kind"], waiting["reason"], event, time.time())
            return True
        return False
    if window[0] in windows.QQ and msgs.is_msg(event) and get_config()["enabled"]:
        _debounce(window, author, identity_, kind, reason, event, time.time())
        return True
    return enqueue(window, kind=kind, reason=reason, record_ids=[identity_], author=author, event=event)


def start_loop(note: str) -> dict:
    global _loop
    note = str(note)
    if not note.strip():
        raise ValueError("loop note cannot be empty")
    with _lock:
        if _loop is None:
            _loop = {"note": note, "round": 0, "idle": 0, "pending": False}
        _loop.update(note=note, since=time.time(), note_round=_loop["round"])
        return deepcopy(_loop)


def stop_loop(reason="") -> dict | None:
    global _loop
    with _lock:
        old, _loop = _loop, None
        cancel("loop")
        # A loop round already queued when ^C/reflection arrived must not restart it.
        for key, job in list(_jobs.items()):
            if job["kind"] == "loop":
                del _jobs[key]
        return deepcopy(old)


def wait(seconds: float, reason: str = "") -> dict:
    """A seconds deadline retains its reason even without an active loop."""
    seconds = float(seconds)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("seconds must be finite and nonnegative")
    with _lock:
        due = time.time() + seconds
        if _loop is not None:
            cancel("loop")
            _loop.update(due=due, waited=seconds, wait_reason=str(reason), pending=False)
            # A wait is armed now; the runner is serial, so the signal waits for this wake to finish.
            return schedule("loop", due, window=windows.ME, reason=reason, key="loop")
        return schedule("wait", due, window=windows.ME, reason=reason, key="seconds-wait", seconds=seconds)


def next_loop(*, acts=0) -> dict | None:
    """Called once at wake end. External wakes never create a loop."""
    with _lock:
        if _loop is None:
            return None
        if "due" in _loop:
            return schedule("loop", _loop["due"], window=windows.ME,
                            reason=_loop.get("wait_reason", ""), key="loop")
        if _loop["pending"]:
            return None
        _loop["idle"] = 0 if acts else _loop["idle"] + 1
        gap = min(LOOP_GAP * 2 ** min(_loop["idle"], 10), LOOP_IDLE_MAX)
        _loop["pending"] = True
        return schedule("loop", time.time() + gap, window=windows.ME, key="loop")


def loop_state() -> dict | None:
    with _lock:
        return deepcopy(_loop)


def sync_rules() -> None:
    """Read file-authoritative layers, replacing their deadlines after note edits."""
    found, _ = awaker.rules()
    cancel("layer_expire")
    for rule in found:
        if rule["kind"] == "layer":
            schedule("layer_expire", rule["moment"], key="layer:" + rule["target"], rule=rule)
    for target, item in list(awaker._custom.items()):
        if item["moment"] is not None:
            schedule("layer_expire", item["moment"], key="custom:" + target, custom=target)


def tick(now=None) -> None:
    """Process due deadlines; also usable with a synthetic clock outside a Bot."""
    now = time.time() if now is None else now
    with _lock:
        due = [job for job in _scheduled.values() if job["due"] <= now]
        for job in due:
            _scheduled.pop(job["id"], None)
    for job in sorted(due, key=lambda item: item["due"]):
        try:
            _fire(job, now)
        except Exception:
            _log.exception("attention deadline failed: %s", job["kind"])
        finally:
            with _lock:
                if _deadlines.get(job["id"]) is job:
                    del _deadlines[job["id"]]
            if job["kind"] == "awaker_check":
                with _lock:
                    if (not _stopping and job["generation"] == _generation
                            and "awaker-check" not in _deadlines):
                        schedule("awaker_check", now + 60, key="awaker-check")


def _fire(job: dict, now: float) -> None:
    kind = job["kind"]
    signal = False
    with _lock:
        # WHY: A selected deadline can still be cancelled/replaced before firing;
        # key existence alone cannot distinguish its successor from this callback.
        if _deadlines.get(job["id"]) is not job:
            return
        if kind == "debounce":
            session = _debouncing.pop(job["session_key"], None)
            if session:
                signal = _put(session["window"], kind=session["kind"], reason=session["reason"],
                              record_ids=session["ids"], author=session["author"], event=session["event"])
        elif kind == "layer_expire":
            if "custom" in job:
                target = job["custom"]
                current = awaker._custom.get(target)
                if current and current["moment"] is not None and current["moment"] <= now:
                    awaker._custom.pop(target, None)
            else:
                with awaker._edit:
                    if awaker._claim([job["rule"]["key"]]):
                        # Claim and follow-start retirement are atomic with plant.
                        awaker._since.pop(job["rule"]["target"], None)
        elif kind == "loop":
            if _loop is None:
                return
            _loop["pending"] = False
            _loop.pop("due", None)
            waited = _loop.pop("waited", None)
            wait_reason = _loop.pop("wait_reason", "")
            _loop["round"] += 1
            doing = _loop["note"]
            age = now - _loop["since"]
            if age >= 1800 or _loop["round"] - _loop["note_round"] >= 5:
                doing += f"（这条 {age:.0f} 秒前、第 {_loop['note_round']} 轮留下的）"
            reason = f"循环第 {_loop['round']} 轮：接着做——{doing}"
            if waited is not None:
                reason += f"；等了 {waited:g} 秒：{wait_reason}"
            # State update and queue insertion share stop_loop's lock: ^C cannot
            # slip between them and leave one final round queued after stopping.
            signal = _put(windows.ME, kind="loop", reason=reason)
        elif kind not in ("awaker_check", "wait_rule"):
            signal = _put(job["window"] or windows.ME, kind=kind, reason=job["reason"],
                          record_ids=job.get("record_ids", ()))
    if signal:
        _signal()
    if kind == "wait_rule":
        awaker.expire_wait(job["rule"], now)
    elif kind == "awaker_check":
        def active():
            with _lock:
                return _deadlines.get(job["id"]) is job
        awaker.check(now, active=active)
        if active():
            sync_rules()


def rescan() -> None:
    """Recover persistent, unconsumed summons, including exceptions before the watermark."""
    import mods.chat as chat

    restored = screen.read_snapshot()
    for window in windows.logged():
        restored.setdefault(windows.label(window), {"through": None, "at": [], "pending": {}, "excluded": {}})
    for text, state in restored.items():
        try:
            window = windows.parse(text)
        except ValueError:
            continue
        # Close the write-before-registration crash gap. Older offline calls are
        # already explicit pending exceptions supplied by backfill/migration.
        for row in screen.records(window, 1_000_000, since=state.get("through")):
            identity_ = row["id"]
            if identity_ in state["pending"] or not screen.is_unread(state, identity_, row["time"]):
                continue
            event = screen.event_for_record(window, row)
            own = event.get("post_type") == "message_sent"
            matched = bool(chat.activation_signal(event)) if window[0] in windows.QQ and not own else False
            wanted, _ = awaker.triggered(window, event, history.author(event), matched) if not own else (False, None)
            screen.register(window, event, kind="恢复的召唤" if wanted else "")
        for identity_, item in screen.unread(window).items():
            if not item.get("kind"):
                continue
            with _lock:
                delayed = any(identity_ in s["ids"] and s["window"] == window for s in _debouncing.values())
            if not delayed:
                enqueue(window, kind=item["kind"], reason=item.get("reason", ""), record_ids=[identity_],
                        author=history.author(item.get("event", {})), event=item.get("event"))
    sync_rules()


def start(notify=None) -> None:
    """Start the single clock; ``notify`` should signal the serial main runner."""
    global _thread, _stopping, _notify
    with _lock:
        if _thread is not None and _thread.is_alive():
            if notify is not None:
                _notify = notify
            return
        _notify = notify
        _stopping = False
        schedule("awaker_check", time.time() + 5, key="awaker-check")
        _thread = threading.Thread(target=_clock, args=(_generation,), name="chat-attention", daemon=True)
        _thread.start()


def _clock(generation: int) -> None:
    while True:
        with _lock:
            if _stopping or generation != _generation:
                return
            closest = min((job["due"] for job in _scheduled.values()), default=None)
            if closest is None:
                _lock.wait()
                continue
            delay = closest - time.time()
            if delay > 0:
                _lock.wait(delay)
                continue
        try:
            tick()
        except Exception:
            _log.exception("attention clock failed")


def stop() -> None:
    global _stopping, _thread, _loop, _notify, _generation
    with _lock:
        _stopping = True
        _generation += 1
        _scheduled.clear()
        _deadlines.clear()
        _debouncing.clear()
        _jobs.clear()
        _loop = None
        _notify = None
        ready.clear()
        _lock.notify_all()
        thread = _thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=5)
    _thread = None
    with awaker._edit:
        awaker._waiting.clear()
        awaker._custom.clear()
        awaker._since.clear()
