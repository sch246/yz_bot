"""Durable event stream: arrivals, activations, notifications, and her outputs and results."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
from threading import RLock
import time
from typing import Any
from uuid import uuid4

from mods import INFRA


PHASE = INFRA
LOAD_AFTER = ("storage",)
AGENT_WINDOW = ("agent", 0)
_REFERENCE = re.compile(r"(?<![A-Za-z0-9_-])(\d{8}-[1-9]\d*(?:#[1-9]\d*)?)(?![A-Za-z0-9_#-])")

_lock = RLock()
_root: Path | None = None
# pending: 未读到达，按到达顺序；order: 每个到达的序号；notified: 已通知过的未读到达；
# notices: 还没确认的通知；outputs/actions: 她自己的输出与结果；counters: 每天用到的最大正式号。
_state: dict[str, Any] = {}
_failed = False
_restore_error = None

from .replay import _apply, _complete_output, _message_identity, _reference_candidates, _validate, _validate_shape


class PersistenceError(RuntimeError):
    """The durable stream is uncertain; no further actions may be dispatched."""


class RestoreError(PersistenceError):
    """A journal read could not establish a trustworthy in-memory stream."""


def raise_if_failed() -> None:
    with _lock:
        if _restore_error is not None:
            raise _restore_error("event stream restore failed; refusing further actions")
        if _failed:
            raise PersistenceError("event stream write failed; refusing further actions")


def ensure_available() -> None:
    """Verify the full journal before a central reader starts its turn."""
    with _lock:
        _restore()


def _directory() -> Path:
    from mods import storage

    return Path(storage.root_path).parent / "event_stream"


def _restore() -> None:
    global _restore_error
    raise_if_failed()
    try:
        _restore_impl()
    except Exception as error:
        _restore_error = RestoreError
        raise RestoreError("event stream restore failed; refusing further actions") from error


def _empty() -> dict[str, Any]:
    return {"pending": {}, "order": {}, "notified": set(), "notices": {},
            "outputs": {}, "actions": [], "counters": {}}


def _restore_impl() -> None:
    global _root, _failed
    directory = _directory()
    if _root == directory:
        return
    restored = _empty()
    incomplete: list[tuple[Path, bytes, int]] = []
    for path in sorted(directory.glob("????????.jsonl")):
        data = path.read_bytes()
        complete = data.rfind(b"\n") + 1
        for line in data[:complete].splitlines():
            _apply(json.loads(line.decode("utf-8")), restored)
        if complete != len(data):
            incomplete.append((path, data, complete))
    for path, data, complete in incomplete:
        # WHY: Only a missing final newline is a crash tail. Validate the
        # entire journal before touching even that tail, so a later bad row
        # leaves the failed restore completely read-only.
        backup = path.with_name(path.name + ".incomplete-" + uuid4().hex)
        with backup.open("xb") as stream:
            stream.write(data[complete:])
            stream.flush()
            os.fsync(stream.fileno())
        with path.open("r+b") as stream:
            if stream.read() != data:
                raise RuntimeError("event stream changed during crash-tail recovery")
            stream.truncate(complete)
            stream.flush()
            os.fsync(stream.fileno())
        logging.warning("event stream recovered %d trailing bytes from %s into %s",
                        len(data) - complete, path.name, backup.name)
    _state.clear()
    _state.update(restored)
    _root = directory
    _failed = False


def _event_bound(value: str | None, name: str) -> tuple[str, int] | None:
    if value is None or value == "":
        return None
    match = re.fullmatch(r"(\d{8})-([1-9]\d*)", value)
    if match is None:
        raise ValueError(f"{name} 必须是 YYYYMMDD-N 正式事件号")
    return match.group(1), int(match.group(2))


def iter_events(start: str | None = None, stop: str | None = None) -> Iterable[dict]:
    """Iterate detached formal rows straight from disk.

    ``start`` is inclusive and ``stop`` is exclusive.  Both are optional
    formal event ids.  Old journals also hold ``input`` rows, the formal reads
    of the old reading model; they are yielded as written.  The iterator does
    not restore or mutate live oplog state.
    """
    lower = _event_bound(start, "start")
    upper = _event_bound(stop, "stop")
    if lower is not None and upper is not None and lower >= upper:
        raise ValueError("start 必须早于 stop")
    directory = _directory()
    started = lower is None
    for path in sorted(directory.glob("????????.jsonl")):
        day = path.stem
        if lower is not None and day < lower[0]:
            continue
        if upper is not None and day > upper[0]:
            break
        size = path.stat().st_size
        with path.open("rb") as stream:
            while stream.tell() < size:
                line = stream.readline(size - stream.tell())
                # WHY: Freeze the visible prefix so a live append cannot make one
                # inspection chase the file forever.  A concurrent crash-tail
                # recovery may shorten that prefix, in which case EOF also ends it.
                if not line or not line.endswith(b"\n"):
                    break
                entry = json.loads(line.decode("utf-8"))
                event_id = entry.get("id")
                if event_id is None:
                    continue
                position = _event_bound(event_id, "journal id")
                if upper is not None and position >= upper:
                    return
                if not started:
                    if position < lower:
                        continue
                    started = True
                if entry.get("kind") == "output":
                    _complete_output(entry)
                entry["references"] = list(dict.fromkeys(_reference_candidates(entry)))
                yield entry


def _append(entry: dict, day: str) -> None:
    global _failed
    # WHY: 追加、fsync 或落盘后的索引应用可能失败，磁盘上可能已有这条或残尾；继续行动会让
    # 外部副作用失去可信的来源记录。保持失败直到重启恢复检查磁盘，而非在进程内重试编号。
    raise_if_failed()
    _validate_shape(entry)
    _validate(entry, _state)
    path = _root / f"{day}.jsonl"
    line = (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        _apply(entry, _state)
    except BaseException as error:
        _failed = True
        if not isinstance(error, Exception):
            raise
        raise PersistenceError("event stream write failed; refusing further actions") from error


def _ordered_pending(window: tuple | None = None) -> list[dict]:
    return [entry for entry in _state["pending"].values()
            if window is None or entry["window"] == list(window)]


def arrive(window: tuple, event: dict, *, activated: bool = False) -> str:
    with _lock:
        _restore()
        arrival_id = uuid4().hex
        entry = {"kind": "arrival", "window": list(window), "arrival": arrival_id,
                 "arrived_at": time.time(), "event": event, "activated": activated}
        _append(entry, datetime.now().strftime("%Y%m%d"))
        return arrival_id


def unread(window: tuple) -> list[dict]:
    with _lock:
        _restore()
        return _ordered_pending(window)


def mark_arrivals_read(window: tuple, arrivals: Iterable[str]) -> int:
    """Durably mark one exact unread window prefix without assigning event ids."""
    with _lock:
        _restore()
        selected = list(arrivals)
        # WHY: 「标为已读」只推进持久未读水位，不是一段经历，所以它没有正式事件号。
        # read_by 不再有人填，仍写 None：回退到旧代码时它按旧形状照样能读。
        _append({"kind": "mark_read", "window": list(window), "arrivals": selected, "read_by": None},
                datetime.now().strftime("%Y%m%d"))
        return len(selected)


def pending_message(window: tuple, message_id: int | str, event_time: int,
                    event_seq: int | str | None = None) -> str | None:
    """Find an unread QQ message identity from its original chat window."""
    with _lock:
        _restore()
        identity = _message_identity(window, {"message_id": message_id, "time": event_time,
                                              "message_seq": event_seq})
        if identity is None:
            return None
        return next((entry["arrival"] for entry in _ordered_pending(window)
                     if _message_identity(window, entry["event"]) == identity), None)


def pending_summary() -> list[tuple[tuple, int, bool]]:
    with _lock:
        _restore()
        counts: dict[tuple, list] = {}
        for entry in _ordered_pending():
            if tuple(entry["window"]) == AGENT_WINDOW and "_stream_results" in entry["event"]:
                continue
            window = tuple(entry["window"])
            state = counts.setdefault(window, [0, False])
            state[0] += 1
            state[1] |= bool(entry.get("activated"))
        return [(window, count, active) for window, (count, active) in counts.items()]


def pending_details() -> list[dict]:
    """Unread window counts and wake metadata, never unread bodies."""
    with _lock:
        _restore()
        details: dict[tuple, dict] = {}
        for entry in _ordered_pending():
            if tuple(entry["window"]) == AGENT_WINDOW and "_stream_results" in entry["event"]:
                continue
            window = tuple(entry["window"])
            detail = details.setdefault(window, {"window": list(window), "unread": 0,
                                                 "ordinary": 0, "mentions": 0,
                                                 "other_wakes": 0, "wake_sources": []})
            detail["unread"] += 1
            ordinal = detail["unread"]
            if not entry.get("activated"):
                detail["ordinary"] += 1
                continue
            kind = entry.get("activation_kind", "wake")
            detail["mentions" if kind == "mention" else "other_wakes"] += 1
            detail["wake_sources"].append({"kind": kind,
                                           "user_id": entry["event"].get("user_id"),
                                           "time": entry["event"].get("time"),
                                           "ordinal": ordinal})
            del detail["wake_sources"][:-3]
        return list(details.values())


def has_unnotified() -> bool:
    with _lock:
        _restore()
        return bool(unacknowledged_windows()) or any(
            entry.get("activated") and arrival not in _state["notified"]
            for arrival, entry in _state["pending"].items())


def unacknowledged_windows() -> list[tuple]:
    with _lock:
        _restore()
        return list(dict.fromkeys(tuple(window) for entry in _state["notices"].values()
                                  for window in entry["windows"]))


def acknowledge_notification(event_id: str) -> None:
    with _lock:
        _restore()
        if event_id in _state["notices"]:
            _append({"kind": "notification_ack", "notification": event_id},
                    datetime.now().strftime("%Y%m%d"))


def deliver_notifications(agent_window: tuple) -> dict | None:
    """Offer only newly activated arrivals, retrying any unacknowledged notice."""
    with _lock:
        _restore()
        for entry in _state["notices"].values():
            if entry["window"] == list(agent_window):
                return entry
        newly_activated = [entry for entry in _state["pending"].values()
                           if entry.get("activated") and entry["arrival"] not in _state["notified"]]
        if not newly_activated:
            return None
        windows = list(dict.fromkeys(tuple(entry["window"]) for entry in newly_activated))
        return _register(agent_window, "notification",
                         version=2,
                         arrivals=[entry["arrival"] for entry in newly_activated],
                         windows=[list(window) for window in windows],
                         activations=[{"window": list(entry["window"]),
                                       "kind": entry.get("activation_kind", "wake"),
                                       "user_id": entry["event"].get("user_id"),
                                       "time": entry["event"].get("time")}
                                      for entry in newly_activated])


def latest_pending_arrival(window: tuple) -> str | None:
    with _lock:
        _restore()
        ordered = _ordered_pending(window)
        return ordered[-1]["arrival"] if ordered else None


def arrival_before_or_at(arrival: str, through: str) -> bool:
    with _lock:
        _restore()
        return _state["order"][arrival] <= _state["order"][through]


def activate(arrival: str, kind: str = "wake") -> None:
    with _lock:
        _restore()
        if arrival in _state["pending"] and not _state["pending"][arrival]["activated"]:
            _append({"kind": "activation", "arrival": arrival, "activation_kind": kind},
                    datetime.now().strftime("%Y%m%d"))


def _register(window: tuple | None, kind: str, **values: Any) -> dict | None:
    if window is None:
        return None
    with _lock:
        _restore()
        day = datetime.now().strftime("%Y%m%d")
        # WHY: 当日号只随追加递增，clear 和覆盖都不回拨。模型写下的引用与反查
        # 必须在重建、重启和清理视图之后仍指向同一次经历。
        number = _state["counters"].get(day, 0) + 1
        entry = {"id": f"{day}-{number}", "window": list(window), "kind": kind,
                 "read_at": time.time(), **values}
        _append(entry, day)
        return entry


def output(window: tuple | None, assistant: dict, calls: list[dict],
           *, persist_reasoning: bool = False, model: str | None = None) -> str | None:
    reasoning = assistant.get("reasoning_content")
    # WHY: One durable output shape also covers direct Python callers without
    # inventing provider call ids; only model-tagged rows may be native-paired.
    values = {"assistant": {"role": assistant.get("role", "assistant"),
                            "content": assistant.get("content", "")}}
    if model is not None:
        values.update(model=model, protocol="openai_chat_completions")
    if calls:
        values["assistant"]["tool_calls"] = calls
    if persist_reasoning and isinstance(reasoning, str) and reasoning:
        values["assistant"]["reasoning_content"] = reasoning
    elif model is not None and "reasoning_content" in assistant:
        values["assistant"]["reasoning_content"] = reasoning
    recorded = _register(window, "output", **values)
    return recorded["id"] if recorded else None


def result(window: tuple | None, source: str, returns: list[dict], arrival: str) -> dict | None:
    return _register(window, "result", source=source, returns=returns, arrival=arrival)


def events() -> list[dict]:
    """Her outputs and the result batches that answered them, oldest first."""
    with _lock:
        _restore()
        return list(_state["actions"])
