"""Durable event stream: one indexed input, output, or tool-result batch per read."""

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
_events: list[dict] = []
_by_id: dict[str, dict] = {}
_by_arrival: dict[str, dict] = {}
_windows: dict[tuple, list[dict]] = {}
_next: dict[str, int] = {}
_pending: dict[str, dict] = {}
_notified: set[str] = set()
_floors: dict[tuple, str | None] = {}
# WHY: Arrival journal replay and dict insertion order are the FIFO authority;
# there is no before-insertion caller, so a historical linked list is redundant.
_arrival_order: dict[str, int] = {}
_covered: dict[tuple, set[str]] = {}
_coverage_nodes: dict[str, list[str]] = {}
_mentioned_by: dict[str, list[str]] = {}
_origins: dict[tuple[tuple, str], dict] = {}
_sources: dict[str, dict] = {}
_seen_messages: set[tuple[tuple, str, int, str | None]] = set()
_failed = False


def _accessible(window: tuple | None, entry: dict) -> bool:
    return entry["window"] == list(window or ()) or window == AGENT_WINDOW


def _directory() -> Path:
    from mods import storage

    return Path(storage.root_path).parent / "event_stream"


def source_page_root() -> Path:
    """Return the private spool directory beside the source journal."""
    return _directory() / "pages"


def _message_identity(window: tuple, event: dict) -> tuple[tuple, str, int, str | None] | None:
    if event.get("message_id") is None or type(event.get("time")) is not int:
        return None
    value = str(event["message_id"])
    sequence = event.get("message_seq")
    if sequence is not None:
        sequence = str(sequence)
        sequence = str(int(sequence)) if sequence.lstrip("-").isdecimal() else sequence
    return (window, str(int(value)) if value.lstrip("-").isdecimal() else value,
            event["time"], sequence)


def _input_message_identity(entry: dict, sources: dict[str, dict]) -> tuple[tuple, str, int, str | None] | None:
    if entry["kind"] != "input":
        return None
    window = (sources[entry["source"]]["window"] if entry.get("source")
              else entry.get("source_window") or entry["window"])
    return _message_identity(tuple(window), entry["event"])


def _restore() -> None:
    global _root, _failed
    directory = _directory()
    if _root == directory:
        return
    restored: list[dict] = []
    indexes: dict[str, dict] = {}
    windows: dict[tuple, list[dict]] = {}
    counters: dict[str, int] = {}
    pending: dict[str, dict] = {}
    notified: set[str] = set()
    floors: dict[tuple, str | None] = {}
    arrival_order: dict[str, int] = {}
    covered: dict[tuple, set[str]] = {}
    coverage_nodes: dict[str, list[str]] = {}
    mentioned_by: dict[str, list[str]] = {}
    origins: dict[tuple[tuple, str], dict] = {}
    sources: dict[str, dict] = {}
    seen_messages: set[tuple[tuple, str, int, str | None]] = set()
    for path in sorted(directory.glob("????????.jsonl")):
        data = path.read_bytes()
        complete = data.rfind(b"\n") + 1
        for line in data[:complete].splitlines():
            entry = json.loads(line.decode("utf-8"))
            _apply(entry, restored, indexes, windows, counters, pending, covered, coverage_nodes,
                   origins, mentioned_by, notified, floors, arrival_order, sources, seen_messages)
        if complete != len(data):
            # WHY: Only a missing final newline is a crash tail. A malformed complete
            # row is corruption, never permission to silently skip committed facts.
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
    _events[:] = restored
    _by_id.clear()
    _by_id.update(indexes)
    _by_arrival.clear()
    _by_arrival.update((entry["arrival"], entry) for entry in restored if entry.get("arrival"))
    _windows.clear()
    _windows.update(windows)
    _next.clear()
    _next.update(counters)
    _pending.clear()
    _pending.update(pending)
    _notified.clear()
    _notified.update(notified)
    _floors.clear()
    _floors.update(floors)
    _arrival_order.clear()
    _arrival_order.update(arrival_order)
    _covered.clear()
    _covered.update(covered)
    _coverage_nodes.clear()
    _coverage_nodes.update(coverage_nodes)
    _mentioned_by.clear()
    _mentioned_by.update(mentioned_by)
    _origins.clear()
    _origins.update(origins)
    _sources.clear()
    _sources.update(sources)
    _seen_messages.clear()
    _seen_messages.update(seen_messages)
    _root = directory
    _failed = False


def _reference_candidates(entry: dict) -> Iterable[str]:
    kind = entry["kind"]
    if kind == "input":
        projection = entry.get("projection") or {}
        content = projection.get("content", "")
        if isinstance(content, list):
            texts = [part.get("text", "") for part in content
                     if isinstance(part, dict) and part.get("type") == "text"]
        else:
            texts = [content]
    elif kind == "output":
        texts = [entry.get("body", ""), *(action.get("arguments", "") for action in entry.get("actions", ()))]
    elif kind == "result":
        texts = [value for result in entry.get("returns", ())
                 for value in (result.get("arguments", ""), result.get("content", ""))]
    else:
        texts = []
    for value in texts:
        for match in _REFERENCE.finditer(str(value)):
            yield match.group(1)


def _event_bound(value: str | None, name: str) -> tuple[str, int] | None:
    if value is None or value == "":
        return None
    match = re.fullmatch(r"(\d{8})-([1-9]\d*)", value)
    if match is None:
        raise ValueError(f"{name} 必须是 YYYYMMDD-N 正式事件号")
    return match.group(1), int(match.group(2))


def iter_events(start: str | None = None, stop: str | None = None) -> Iterable[dict]:
    """Iterate detached formal experiences and cover facts straight from disk.

    ``start`` is inclusive and ``stop`` is exclusive.  Both are optional
    formal event ids.  Cover rows have no id; they appear at their physical
    journal position between the selected formal events.  The iterator does
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
                if event_id is not None:
                    position = _event_bound(event_id, "journal id")
                    if upper is not None and position >= upper:
                        return
                    if not started:
                        if position < lower:
                            continue
                        started = True
                    if entry.get("kind") not in ("input", "output", "result", "notification"):
                        continue
                    if entry["kind"] == "output" and isinstance(entry.get("assistant"), dict):
                        assistant = entry["assistant"]
                        entry["body"] = assistant.get("content") or ""
                        entry["thought_present"] = bool(assistant.get("reasoning_content"))
                        entry["actions"] = [call["function"]
                                            for call in assistant.get("tool_calls", ())]
                    entry["references"] = list(dict.fromkeys(_reference_candidates(entry)))
                    yield entry
                elif started and entry.get("kind") == "cover":
                    yield entry


def _validate_source_page(entry: dict, state: dict) -> None:
    if state["state"] != "fetching":
        raise ValueError("source fetch is not running")
    if (type(entry["page_number"]) is not int or entry["page_number"] != state["pages"]
            or type(entry["member_count"]) is not int or entry["member_count"] < 0
            or type(entry.get("mention_count", 0)) is not int
            or not 0 <= entry.get("mention_count", 0) <= entry["member_count"]):
        raise ValueError("source page must be the next page with a nonnegative count")


def _previous_nonempty_page(state: dict, before: int) -> int:
    return next((page for page in range(before - 1, -1, -1)
                 if state["page_counts"][page]), -1)


def _validate_source_read(entry: dict, state: dict, pending: dict[str, dict]) -> None:
    if state["state"] == "fetching":
        raise ValueError("source is not sealed")
    page, offset = entry["page"], entry["offset"]
    if (type(page) is not int or type(offset) is not int
            or (page, offset) != (state["read_page"], state["read_offset"])):
        raise ValueError("source read cursor is stale")
    if page < 0 or offset >= state["page_counts"][page]:
        raise ValueError("source read cursor is exhausted")
    next_position = ((page, offset + 1) if offset + 1 < state["page_counts"][page]
                     else (_previous_nonempty_page(state, page), 0))
    if (type(entry["next_page"]) is not int or type(entry["next_offset"]) is not int
            or (entry["next_page"], entry["next_offset"]) != next_position):
        raise ValueError("source read successor is not contiguous")
    if entry["source_window"] != state["window"]:
        raise ValueError("source read provenance is invalid")
    arrival = entry.get("arrival")
    if arrival is not None:
        linked = pending.get(arrival)
        if (linked is None or linked["window"] != state["window"]
                or _message_identity(tuple(linked["window"]), linked["event"]) is None
                or _message_identity(tuple(linked["window"]), linked["event"])
                   != _message_identity(tuple(linked["window"]), entry["event"])):
            raise ValueError("source read does not match its live arrival")


def _source_successor(state: dict, page: int, offset: int, count: int) -> tuple[int, int]:
    if type(count) is not int or count < 1 or count > state["member_count"] - state["read_count"]:
        raise ValueError("source mark-read count is invalid")
    current_page, current_offset = page, offset
    remaining = count
    while remaining:
        if current_page < 0 or current_offset >= state["page_counts"][current_page]:
            raise ValueError("source mark-read cursor is exhausted")
        available = state["page_counts"][current_page] - current_offset
        if remaining < available:
            return current_page, current_offset + remaining
        remaining -= available
        current_page, current_offset = _previous_nonempty_page(state, current_page), 0
    return current_page, current_offset


def _validate_source_mark_read(entry: dict, state: dict, pending: dict[str, dict]) -> None:
    if state["state"] == "fetching":
        raise ValueError("source is not sealed")
    page, offset = entry["page"], entry["offset"]
    if (type(page) is not int or type(offset) is not int
            or (page, offset) != (state["read_page"], state["read_offset"])):
        raise ValueError("source mark-read cursor is stale")
    _source_successor(state, page, offset, entry["count"])
    mentions = entry.get("mention_count", 0)
    if (type(mentions) is not int or not 0 <= mentions <= entry["count"]
            or state["read_mention_count"] + mentions > state["mention_count"]):
        raise ValueError("source mark-read mention count is invalid")
    arrivals = entry.get("arrivals", [])
    if (not isinstance(arrivals, list) or len(arrivals) != len(set(arrivals))
            or any(arrival not in pending or pending[arrival]["window"] != state["window"]
                   for arrival in arrivals)):
        raise ValueError("source mark-read arrivals are invalid")


def _apply(
    entry: dict, recorded: list[dict], indexes: dict[str, dict], windows: dict[tuple, list[dict]],
    counters: dict[str, int], pending: dict[str, dict], covered: dict[tuple, set[str]],
    coverage_nodes: dict[str, list[str]], origins: dict[tuple[tuple, str], dict],
    mentioned_by: dict[str, list[str]], notified: set[str], floors: dict[tuple, str | None],
    arrival_order: dict[str, int], sources: dict[str, dict],
    seen_messages: set[tuple[tuple, str, int, str | None]],
) -> None:
    if entry["kind"] == "arrival":
        arrival_order[entry["arrival"]] = len(arrival_order)
        source = entry.get("source")
        if source is not None:
            if source not in sources or sources[source]["queue_window"] != entry["window"]:
                raise ValueError("arrival source does not match its window")
        pending[entry["arrival"]] = entry
        return
    if entry["kind"] == "source_start":
        source = entry["source"]
        if source in sources:
            raise ValueError("duplicate source")
        sources[source] = {"key": source, "name": entry["name"], "window": entry["window"],
                           "queue_window": entry.get("queue_window", entry["window"]),
                           "source_type": entry["source_type"], "pulled": False,
                           "state": "fetching", "anchor": entry.get("anchor"),
                           "anchor_time": entry.get("anchor_time"),
                           "fetch_anchor": entry.get("anchor"),
                           "cursor": entry.get("start_seq"), "stop_cursor": None,
                           "pending_boundary": entry.get("pending_boundary"),
                           "pages": 0, "page_counts": [], "member_count": 0,
                           "mention_count": 0, "read_mention_count": 0,
                           "read_page": None, "read_offset": 0, "read_count": 0,
                           "gap": None, "previous_gaps": [], "error": None}
        return
    if entry["kind"] == "source_page":
        state = sources[entry["source"]]
        _validate_source_page(entry, state)
        state["page_counts"].append(entry["member_count"])
        state["pages"] += 1
        state["member_count"] += entry["member_count"]
        state["mention_count"] += entry.get("mention_count", 0)
        state["cursor"] = entry["cursor"]
        return
    if entry["kind"] == "source_progress":
        state = sources[entry["source"]]
        if entry["cursor"] is not None:
            state["cursor"] = entry["cursor"]
        state["gap"] = entry.get("gap")
        return
    if entry["kind"] == "source_finish":
        state = sources[entry["source"]]
        if state["state"] != "fetching":
            raise ValueError("source fetch is not running")
        state["state"] = entry["state"]
        state["gap"] = entry.get("gap")
        state["error"] = entry.get("error")
        state["stop_cursor"] = entry.get("stop_cursor", state["cursor"])
        if state["gap"]:
            state["previous_gaps"].append(state["gap"])
        if state["read_page"] is None and not state["read_count"]:
            state["read_page"] = _previous_nonempty_page(state, state["pages"])
        return
    if entry["kind"] == "source_reopen":
        state = sources[entry["source"]]
        if state["state"] == "fetching" or state["pulled"]:
            raise ValueError("only an unpulled sealed source can extend")
        state["state"] = "fetching"
        state["cursor"] = entry["cursor"]
        state["fetch_anchor"] = entry.get("fetch_anchor")
        state["gap"] = None
        state["error"] = None
        state["read_page"] = None
        state["read_offset"] = 0
        return
    if entry["kind"] == "source_pulled":
        sources[entry["source"]]["pulled"] = True
        return
    if entry["kind"] == "activation":
        if entry["arrival"] in pending:
            pending[entry["arrival"]]["activated"] = True
            pending[entry["arrival"]]["activation_kind"] = entry.get("activation_kind", "wake")
        return
    if entry["kind"] == "floor":
        window = tuple(entry["window"])
        floors[window] = entry["before"]
        if entry["before"] is not None:
            boundary = arrival_order[entry["before"]]
            for arrival, item in list(pending.items()):
                if arrival_order[arrival] <= boundary and tuple(item["window"]) == window and not item.get("fetched"):
                    pending.pop(arrival)
        return
    if entry["kind"] == "mark_read":
        # WHY: 「标为已读」只推进持久未读水位，不是主体真正读到的一段经历，
        # 所以它没有正式事件号。原文权威仍是 chatlog，之后可按 message_id/origin 查回。
        window = tuple(entry["window"])
        arrivals = entry["arrivals"]
        ordered = [arrival for arrival, item in pending.items()
                   if tuple(item["window"]) == window]
        if (not isinstance(arrivals, list) or not arrivals
                or len(arrivals) != len(set(arrivals))
                or ordered[:len(arrivals)] != arrivals):
            raise ValueError("mark-read must consume one exact pending prefix")
        for arrival in arrivals:
            marked = pending.pop(arrival)
            identity = _message_identity(window, marked["event"])
            if identity is not None:
                seen_messages.add(identity)
        return
    if entry["kind"] == "source_mark_read":
        # 历史信源的水位住在页内游标里；这条和上面的 window discard
        # 是同一个用户动作的两种底层投影，都不伪造 input。
        state = sources[entry["source"]]
        _validate_source_mark_read(entry, state, pending)
        state["read_page"], state["read_offset"] = _source_successor(
            state, entry["page"], entry["offset"], entry["count"])
        state["read_count"] += entry["count"]
        state["read_mention_count"] += entry.get("mention_count", 0)
        state["pulled"] = True
        for arrival in entry.get("arrivals", []):
            marked = pending.pop(arrival)
            identity = _message_identity(tuple(state["window"]), marked["event"])
            if identity is not None:
                seen_messages.add(identity)
        return
    if entry["kind"] == "notification_ack":
        notice = indexes[entry["notification"]]
        if notice["kind"] != "notification":
            raise ValueError("notification ack does not name a notification")
        notice["acknowledged"] = True
        notified.update(notice["arrivals"])
        return
    # WHY: 新代码不再产生 condensed/clear；这里只重放既有生产日志，避免升级后
    # 旧事件突然重新出现或让事件流因未知记录无法启动。旧日志退休后即可一起删除。
    if entry["kind"] == "condensed":
        indexes[entry["target"]]["condensed"] = True
        return
    if entry["kind"] == "cover":
        window = tuple(entry["window"])
        source = entry["node"].partition("#")[0]
        if (entry["node"] in coverage_nodes or source not in indexes
                or tuple(indexes[source]["window"]) != window or any(
                member not in indexes or not _accessible(window, indexes[member])
                for member in entry["members"])):
            raise ValueError("覆盖日志引用了不存在或不可访问的事件")
        # WHY: 覆盖只改变默认投影，原事件仍须能按稳定号反查；多次覆盖同一成员
        # 只是多条结论边，不能把它从原索引里删除或改写。
        coverage_nodes[entry["node"]] = list(entry["members"])
        covered.setdefault(window, set()).update(entry["members"])
        return
    if entry["kind"] == "clear":
        for existing in windows.get(tuple(entry["window"]), ()):
            if existing["kind"] == "result":
                existing["hidden"] = True
        return
    source_read = entry["kind"] == "input" and "page" in entry
    if entry["kind"] == "output" and isinstance(entry.get("assistant"), dict):
        assistant = entry["assistant"]
        entry["body"] = assistant.get("content") or ""
        entry["thought_present"] = bool(assistant.get("reasoning_content"))
        entry["actions"] = [call["function"] for call in assistant.get("tool_calls", ())]
    if source_read:
        _validate_source_read(entry, sources[entry["source"]], pending)
    event_id = entry["id"]
    if event_id in indexes:
        raise ValueError(f"duplicate event id: {event_id}")
    mentions = []
    seen_mentions = set()
    for reference in _reference_candidates(entry):
        target_id, separator, position = reference.partition("#")
        target = indexes.get(target_id)
        if target is None or (target["window"] != entry["window"]
                              and tuple(entry["window"]) != AGENT_WINDOW):
            continue
        if separator and (target["kind"] != "output" or not 1 <= int(position) <= len(target["actions"])):
            continue
        if reference not in seen_mentions:
            seen_mentions.add(reference)
            mentions.append(reference)
    entry["_mentions"] = mentions
    day, number = event_id.split("-", 1)
    if entry["kind"] == "result" and indexes[entry["source"]].get("condensed"):
        entry["condensed"] = True
    counters[day] = max(counters.get(day, 0), int(number))
    recorded.append(entry)
    indexes[event_id] = entry
    windows.setdefault(tuple(entry["window"]), []).append(entry)
    for target_id in dict.fromkeys(reference.partition("#")[0] for reference in mentions):
        mentioned_by.setdefault(target_id, []).append(event_id)
    if entry["kind"] == "input" and entry.get("origin"):
        key = (tuple(entry["window"]), entry["origin"])
        origins[key] = entry
    if source_read:
        state = sources[entry["source"]]
        state["read_page"] = entry["next_page"]
        state["read_offset"] = entry["next_offset"]
        state["read_count"] += 1
        state["read_mention_count"] += bool(entry.get("mentioned"))
        state["pulled"] = True
    if entry.get("arrival"):
        arrival = pending.get(entry["arrival"])
        if entry["kind"] == "input" and arrival is not None and arrival.get("source"):
            sources[arrival["source"]]["pulled"] = True
        pending.pop(entry["arrival"], None)
    identity = _input_message_identity(entry, sources)
    if identity is not None:
        seen_messages.add(identity)


def _append(entry: dict, day: str) -> None:
    global _failed
    # WHY: 追加可能在写入或 fsync 中途失败，磁盘上可能已有这条或残尾；继续行动会让
    # 外部副作用失去可信的来源记录。保持失败直到重启恢复检查磁盘，而非在进程内重试编号。
    if _failed:
        raise RuntimeError("event stream write failed; refusing further actions")
    if entry["kind"] == "arrival":
        source = entry.get("source")
        if source is not None:
            if source not in _sources or _sources[source]["queue_window"] != entry["window"]:
                raise ValueError("arrival source does not match its window")
    if entry["kind"] == "source_start" and entry["source"] in _sources:
        raise ValueError("duplicate source")
    if entry["kind"] == "source_page":
        _validate_source_page(entry, _sources[entry["source"]])
    if entry["kind"] == "input" and "page" in entry:
        _validate_source_read(entry, _sources[entry["source"]], _pending)
    if entry["kind"] == "mark_read":
        window = tuple(entry["window"])
        ordered = [arrival for arrival, item in _pending.items()
                   if tuple(item["window"]) == window]
        arrivals = entry.get("arrivals")
        if (not isinstance(arrivals, list) or not arrivals
                or len(arrivals) != len(set(arrivals))
                or ordered[:len(arrivals)] != arrivals):
            raise ValueError("mark-read must consume one exact pending prefix")
    if entry["kind"] == "source_mark_read":
        _validate_source_mark_read(entry, _sources[entry["source"]], _pending)
    if entry["kind"] in ("input", "result") and entry.get("arrival") and entry["arrival"] not in _pending:
        raise ValueError("arrival was already consumed or does not exist")
    path = _root / f"{day}.jsonl"
    line = (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        _failed = True
        raise
    _apply(entry, _events, _by_id, _windows, _next, _pending, _covered, _coverage_nodes,
           _origins, _mentioned_by, _notified, _floors, _arrival_order, _sources,
           _seen_messages)
    if entry.get("id") and entry.get("arrival"):
        _by_arrival[entry["arrival"]] = entry


def _ordered_pending(window: tuple | None = None) -> list[dict]:
    return [entry for entry in _pending.values()
            if window is None or entry["window"] == list(window)]


def _arrival_before_or_at(arrival: str, through: str) -> bool:
    return _arrival_order[arrival] <= _arrival_order[through]


def _source_snapshot(state: dict) -> dict:
    return {**state, "window": list(state["window"]),
            "queue_window": list(state["queue_window"]),
            "page_counts": list(state["page_counts"]),
            "previous_gaps": list(state["previous_gaps"]),
            "remaining": state["member_count"] - state["read_count"]}


def arrive(window: tuple, event: dict, *, activated: bool = False, origin: str | None = None) -> str:
    from mods import chatlog

    with _lock:
        _restore()
        arrival_id = uuid4().hex
        entry = {"kind": "arrival", "window": list(window), "arrival": arrival_id,
                 "arrived_at": time.time(), "event": event, "activated": activated,
                 "origin": origin if origin is not None else chatlog.consume_origin(event)}
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
        _append({"kind": "mark_read", "window": list(window), "arrivals": selected},
                datetime.now().strftime("%Y%m%d"))
        return len(selected)


def mark_source_read(source: str, count: int, mention_count: int,
                     arrivals: Iterable[str] = ()) -> dict:
    """Advance one sealed source without presenting its members to the model."""
    with _lock:
        _restore()
        state = _sources[source]
        entry = {"kind": "source_mark_read", "source": source,
                 "page": state["read_page"], "offset": state["read_offset"],
                 "count": count, "mention_count": mention_count,
                 "arrivals": list(arrivals)}
        _append(entry, datetime.now().strftime("%Y%m%d"))
        return _source_snapshot(_sources[source])


def pending_message(window: tuple, message_id: int | str, event_time: int,
                    event_seq: int | str | None = None) -> str | None:
    """Find an unread QQ message identity from its original chat window."""
    with _lock:
        _restore()
        identity = _message_identity(window, {"message_id": message_id, "time": event_time,
                                              "message_seq": event_seq})
        if identity is None:
            return None
        return next((entry["arrival"] for entry in _pending.values()
                     if (tuple(_sources[entry["source"]]["window"]) if entry.get("source")
                         else tuple(entry["window"])) == window
                     if entry["event"].get("message_id") is not None
                     and _message_identity(window, entry["event"]) == identity), None)


def message_seen(window: tuple, message_id: int | str, event_time: int,
                 event_seq: int | str | None = None) -> bool:
    """Check pending and already-read inputs for the same original QQ message."""
    with _lock:
        _restore()
        if pending_message(window, message_id, event_time, event_seq) is not None:
            return True
        return _message_identity(window, {"message_id": message_id, "time": event_time,
                                          "message_seq": event_seq}) in _seen_messages


def arrival_origin(arrival: str) -> str | None:
    with _lock:
        _restore()
        return _pending.get(arrival, {}).get("origin")


def start_source(name: str, window: tuple, source_type: str, *, anchor: str | None = None,
                 anchor_time: int | None = None,
                 queue_window: tuple | str | None = None,
                 start_seq: str | None = None) -> dict:
    """Start a named fetch; 'new' creates a separate durable FIFO for a repeated pull."""
    with _lock:
        _restore()
        source = uuid4().hex
        if queue_window == "new":
            queue_window = ("source", source)
        elif queue_window is None:
            queue_window = window
        if not name or not source_type or not isinstance(queue_window, tuple):
            raise ValueError("source needs a name, type, and queue window")
        existing = _ordered_pending(queue_window)
        boundary = existing[-1]["arrival"] if existing else None
        _append({"kind": "source_start", "source": source, "name": name,
                 "window": list(window), "queue_window": list(queue_window),
                 "source_type": source_type, "anchor": anchor, "anchor_time": anchor_time,
                 "start_seq": start_seq,
                 "pending_boundary": boundary}, datetime.now().strftime("%Y%m%d"))
        return _source_snapshot(_sources[source])


def source_progress(source: str, *, cursor: str | None = None, gap: str | None = None) -> dict:
    with _lock:
        _restore()
        if _sources[source]["state"] != "fetching":
            raise ValueError("source fetch is not running")
        if cursor is not None and cursor != _sources[source]["cursor"]:
            raise ValueError("source cursor advances only with a committed page")
        _append({"kind": "source_progress", "source": source,
                 "cursor": _sources[source]["cursor"], "gap": gap},
                datetime.now().strftime("%Y%m%d"))
        return _source_snapshot(_sources[source])


def publish_source_page(source: str, page_number: int, cursor: str | None,
                        member_count: int, mention_count: int = 0) -> dict:
    """Commit metadata only after the bounded page body is durable."""
    with _lock:
        _restore()
        _append({"kind": "source_page", "source": source, "page_number": page_number,
                 "cursor": cursor, "member_count": member_count, "mention_count": mention_count},
                datetime.now().strftime("%Y%m%d"))
        return _source_snapshot(_sources[source])


def finish_source(source: str, *, gap: str | None = None, error: str | None = None,
                  stop_cursor: str | None = None) -> dict:
    with _lock:
        _restore()
        if _sources[source]["state"] != "fetching":
            raise ValueError("source fetch is not running")
        state = "failed" if error is not None else "gap" if gap is not None else "complete"
        _append({"kind": "source_finish", "source": source, "state": state,
                 "gap": gap, "error": error, "stop_cursor": stop_cursor},
                datetime.now().strftime("%Y%m%d"))
        return _source_snapshot(_sources[source])


def reopen_source(source: str, *, fetch_anchor: str | None = None) -> dict:
    """Extend the front of a sealed FIFO only until its first successful pull."""
    with _lock:
        _restore()
        state = _sources[source]
        cursor = state["stop_cursor"] or state["cursor"]
        if cursor is None and state["state"] not in ("gap", "failed"):
            raise ValueError("source has no reliable remote cursor")
        _append({"kind": "source_reopen", "source": source, "cursor": cursor,
                 "fetch_anchor": fetch_anchor}, datetime.now().strftime("%Y%m%d"))
        return _source_snapshot(_sources[source])


def mark_source_pulled(source: str) -> dict:
    with _lock:
        _restore()
        if not _sources[source]["pulled"]:
            _append({"kind": "source_pulled", "source": source}, datetime.now().strftime("%Y%m%d"))
        return _source_snapshot(_sources[source])


def sources() -> list[dict]:
    """List all sources, including empty queues that are fetching or have gaps."""
    with _lock:
        _restore()
        return [_source_snapshot(state) for state in _sources.values()]


def resolve_source(name_or_key: str) -> dict | None:
    """Resolve an exact key or the most recently started source with this name."""
    with _lock:
        _restore()
        state = _sources.get(name_or_key)
        if state is None:
            state = next((item for item in reversed(list(_sources.values()))
                          if item["name"] == name_or_key), None)
        return _source_snapshot(state) if state is not None else None


def prepare_window(window: tuple, page_size: int) -> None:
    """Freeze an initial daily-reading floor without claiming older arrivals were read."""
    with _lock:
        _restore()
        if (window in _floors
                or any(entry["kind"] == "input" for entry in _windows.get(window, ()))
                or any(entry["kind"] == "input" and entry.get("source_window") == list(window)
                       for entry in _events)):
            return
        pending = _ordered_pending(window)
        if pending:
            before = pending[-page_size - 1]["arrival"] if len(pending) > page_size else None
            _append({"kind": "floor", "window": list(window), "before": before},
                    datetime.now().strftime("%Y%m%d"))


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


def _pending_detail(window: tuple, through: str | None = None) -> dict:
    rows = [(entry["arrival"], entry) for entry in _ordered_pending(window)
            if through is None or _arrival_before_or_at(entry["arrival"], through)]
    wakes = [entry for _arrival, entry in rows if entry.get("activated")]
    return {"window": list(window), "unread": len(rows), "ordinary": len(rows) - len(wakes),
            "mentions": sum(entry.get("activation_kind") == "mention" for entry in wakes),
            "other_wakes": sum(entry.get("activation_kind") != "mention" for entry in wakes),
            "wake_sources": [{"kind": entry.get("activation_kind", "wake"),
                              "user_id": entry["event"].get("user_id"),
                              "time": entry["event"].get("time")}
                             for entry in wakes[:3]]}


def pending_details() -> list[dict]:
    """Unread window counts and wake metadata, never unread bodies."""
    with _lock:
        _restore()
        details: dict[tuple, dict] = {}
        for entry in _pending.values():
            if tuple(entry["window"]) == AGENT_WINDOW and "_stream_results" in entry["event"]:
                continue
            window = tuple(entry["window"])
            detail = details.setdefault(window, {"window": list(window), "unread": 0,
                                                 "ordinary": 0, "mentions": 0,
                                                 "other_wakes": 0, "wake_sources": []})
            detail["unread"] += 1
            if not entry.get("activated"):
                detail["ordinary"] += 1
                continue
            kind = entry.get("activation_kind", "wake")
            detail["mentions" if kind == "mention" else "other_wakes"] += 1
            if len(detail["wake_sources"]) < 3:
                detail["wake_sources"].append({"kind": kind,
                                               "user_id": entry["event"].get("user_id"),
                                               "time": entry["event"].get("time")})
        return list(details.values())


def has_unnotified() -> bool:
    with _lock:
        _restore()
        return bool(unacknowledged_windows()) or any(
            entry.get("activated") and arrival not in _notified
            for arrival, entry in _pending.items())


def unacknowledged_windows() -> list[tuple]:
    with _lock:
        _restore()
        return list(dict.fromkeys(tuple(window) for entry in _events
                                  if entry["kind"] == "notification" and not entry.get("acknowledged")
                                  for window in entry["windows"]))


def notification_acknowledged(event_id: str) -> bool:
    with _lock:
        _restore()
        return bool(_by_id[event_id].get("acknowledged"))


def acknowledge_notification(event_id: str) -> None:
    with _lock:
        _restore()
        if not _by_id[event_id].get("acknowledged"):
            _append({"kind": "notification_ack", "notification": event_id},
                    datetime.now().strftime("%Y%m%d"))


def deliver_notifications(agent_window: tuple) -> dict | None:
    """Offer a numbered notice; only a committed model output acknowledges it."""
    with _lock:
        _restore()
        for entry in _windows.get(agent_window, ()):
            if entry["kind"] == "notification" and not entry.get("acknowledged"):
                return entry
        activated = []
        latest: dict[tuple, str] = {}
        for entry in _pending.values():
            latest[tuple(entry["window"])] = entry["arrival"]
            if entry.get("activated") and entry["arrival"] not in _notified:
                activated.append(entry)
        if not activated:
            return None
        windows = list(dict.fromkeys(tuple(entry["window"]) for entry in activated))
        return _register(agent_window, "notification", arrivals=[entry["arrival"] for entry in activated],
                         through={str(window): latest[window] for window in windows},
                         windows=[list(window) for window in windows],
                         unread=pending_details())


def work_targets(agent_window: tuple) -> dict[tuple, str]:
    """Frozen upper bounds for an explicit pull after a delivered notification."""
    with _lock:
        _restore()
        earliest: dict[tuple, str] = {}
        for arrival, pending in _pending.items():
            earliest.setdefault(tuple(pending["window"]), arrival)
        targets: dict[tuple, str] = {}
        for entry in _windows.get(agent_window, ()):
            if entry["kind"] == "notification":
                for window in entry["windows"]:
                    key = tuple(window)
                    if key in targets:
                        continue
                    target = entry["through"][str(key)]
                    if key in earliest and _arrival_before_or_at(earliest[key], target):
                        targets[key] = target
        return targets


def latest_pending_arrival(window: tuple) -> str | None:
    with _lock:
        _restore()
        ordered = _ordered_pending(window)
        return ordered[-1]["arrival"] if ordered else None


def arrival_before_or_at(arrival: str, through: str) -> bool:
    with _lock:
        _restore()
        return _arrival_before_or_at(arrival, through)


def activate(arrival: str, kind: str = "wake") -> None:
    with _lock:
        _restore()
        if arrival in _pending and not _pending[arrival]["activated"]:
            _append({"kind": "activation", "arrival": arrival, "activation_kind": kind},
                    datetime.now().strftime("%Y%m%d"))


def read(arrival: str) -> dict | None:
    with _lock:
        _restore()
        return _by_arrival.get(arrival)


def recall_events(window: tuple | None, ids: Iterable[str]) -> tuple[list[dict], list[str]]:
    with _lock:
        _restore()
        found: list[dict] = []
        missing: list[str] = []
        for event_id in dict.fromkeys(str(value) for value in ids):
            entry = _by_id.get(event_id)
            if entry is None or not _accessible(window, entry):
                missing.append(event_id)
            elif entry["kind"] == "input" and entry.get("projection") is None:
                missing.append(event_id)
            else:
                recalled = entry.copy()
                recalled["mentions"] = recalled.pop("_mentions", [])
                if entry["kind"] == "output":
                    members = {f"{event_id}#{position + 1}": list(_coverage_nodes[f"{event_id}#{position + 1}"])
                               for position in range(len(entry["actions"]))
                               if f"{event_id}#{position + 1}" in _coverage_nodes}
                    if members:
                        recalled["coverage"] = members
                found.append(recalled)
        return found, missing


def select_events(window: tuple | None, *, ids: Iterable[str] | None = None,
                  anchor: str = "", before: int = 0, after: int = 0,
                  start: str = "", end: str = "", kinds: Iterable[str] = (),
                  source_window: tuple | None = None) -> tuple[list[dict], list[str]]:
    """Resolve one seed selector, then filter its members without extending the seed."""
    if any(type(value) is not int or value < 0 for value in (before, after)):
        raise ValueError("before 和 after 必须是非负整数")
    if ids is not None and isinstance(ids, (str, bytes)):
        raise ValueError("ids 必须是正式事件号列表")
    requested = list(dict.fromkeys(str(value) for value in (ids or ())))
    has_range = bool(start or end)
    if sum((bool(requested), bool(anchor), has_range)) != 1 or (has_range and (not start or not end)):
        raise ValueError("请只指定非空 ids、anchor，或同时指定 start 和 end")
    if not anchor and (before or after):
        raise ValueError("before 和 after 只能与 anchor 一起使用")
    allowed_kinds = {"input", "output", "result", "notification"}
    selected_kinds = set(kinds)
    if selected_kinds - allowed_kinds:
        raise ValueError("kinds 只能包含 input、output、result、notification")
    if source_window is not None and (not isinstance(source_window, tuple)
                                      or len(source_window) != 2
                                      or source_window[0] not in ("group", "private")
                                      or type(source_window[1]) is not int or source_window[1] <= 0):
        raise ValueError("source 必须是 g<群号> 或 u<私聊对端号>")
    with _lock:
        _restore()
        missing = []
        if requested:
            selected = []
            for event_id in requested:
                entry = _by_id.get(event_id)
                if (entry is None or not _accessible(window, entry)
                        or (entry["kind"] == "input" and entry.get("projection") is None)):
                    missing.append(event_id)
                else:
                    selected.append(entry)
        else:
            timeline = [entry for entry in _events if _accessible(window, entry)
                        and not (entry["kind"] == "input" and entry.get("projection") is None)]
            positions = {entry["id"]: index for index, entry in enumerate(timeline)}
            for event_id in (anchor, start, end):
                if event_id and event_id not in positions:
                    raise ValueError(f"找不到可访问的已读事件: {event_id}")
            if anchor:
                index = positions[anchor]
                selected = timeline[max(0, index - before):index + after + 1]
            else:
                first, last = positions[start], positions[end]
                if first > last:
                    raise ValueError("start 必须早于或等于 end")
                selected = timeline[first:last + 1]
        return ([entry for entry in selected if (not selected_kinds or entry["kind"] in selected_kinds)
                 and (source_window is None
                      or tuple(entry.get("source_window") or entry["window"]) == source_window)], missing)


def _register(window: tuple | None, kind: str, **values: Any) -> dict | None:
    if window is None:
        return None
    with _lock:
        _restore()
        day = datetime.now().strftime("%Y%m%d")
        # WHY: 当日号只随追加递增，clear 和覆盖都不回拨。模型写下的引用与反查
        # 必须在重建、重启和清理视图之后仍指向同一次经历。
        number = _next.get(day, 0) + 1
        entry = {"id": f"{day}-{number}", "window": list(window), "kind": kind,
                 "read_at": time.time(), **values}
        _append(entry, day)
        return entry


def input(window: tuple | None, event: dict, projection: dict | None, arrival: str,
          source_window: tuple | None = None) -> dict | None:
    with _lock:
        _restore()
        pending = _pending.get(arrival, {})
        origin = pending.get("origin")
        original = (_sources[pending["source"]]["window"] if pending.get("source")
                    else list(window or ()))
        return _register(window, "input", event=event, projection=projection, arrival=arrival,
                         origin=origin, source=pending.get("source"),
                         source_window=list(source_window) if source_window is not None else original)


def input_source(window: tuple, event: dict, projection: dict | None, source: str,
                 page: int, offset: int, next_page: int, next_offset: int,
                 origin: str | None, source_window: tuple,
                 arrival: str | None = None, mentioned: bool = False) -> dict:
    """Index one sealed page member and advance its read cursor in the same append."""
    with _lock:
        _restore()
        if window is None:
            raise ValueError("source input needs a read window")
        values = {"event": event, "projection": projection, "source": source,
                  "page": page, "offset": offset, "next_page": next_page,
                  "next_offset": next_offset, "origin": origin,
                  "source_window": list(source_window), "mentioned": mentioned}
        if arrival is not None:
            values["arrival"] = arrival
        return _register(window, "input", **values)


def origin_status(window: tuple | None, origin: str) -> str | None:
    """Return the indexed id or a pending marker for one exact chatlog record."""
    with _lock:
        _restore()
        key = (tuple(window or ()), origin)
        if key in _origins:
            return _origins[key]["id"]
        if any(tuple(item["window"]) == key[0] and item.get("origin") == origin
               for item in _pending.values()):
            return "pending"
        return None


def output(window: tuple | None, assistant: dict, calls: list[dict],
           *, persist_reasoning: bool = False, model: str | None = None) -> str | None:
    reasoning = assistant.get("reasoning_content")
    if model is not None:
        # WHY: The provider's original assistant shape is the only durable
        # execution transcript. body/actions are reconstructed by _apply for
        # coverage and older readers, rather than written as a second version.
        values = {"model": model, "protocol": "openai_chat_completions",
                  "assistant": {key: assistant[key] for key in
                                ("role", "content", "reasoning_content") if key in assistant}}
        if calls:
            values["assistant"]["tool_calls"] = calls
    else:
        values = {"body": assistant.get("content", ""),
                  "thought_present": bool(reasoning),
                  "actions": [call["function"] for call in calls]}
        if persist_reasoning and isinstance(reasoning, str) and reasoning:
            values["thought"] = reasoning
    recorded = _register(window, "output", **values)
    return recorded["id"] if recorded else None


def result(window: tuple | None, source: str, returns: list[dict], arrival: str) -> dict | None:
    return _register(window, "result", source=source, returns=returns, arrival=arrival)


def events(window: tuple | None, include_condensed: bool = False) -> list[dict]:
    with _lock:
        _restore()
        return [item for item in _windows.get(tuple(window or ()), ())
                if not item.get("hidden")
                and (include_condensed or (not item.get("condensed")
                                           and item["id"] not in _covered.get(tuple(window or ()), ())))]


def covered(window: tuple | None) -> set[str]:
    with _lock:
        _restore()
        return set(_covered.get(tuple(window or ()), ()))


def coverage_members(window: tuple | None, node: str) -> list[str] | None:
    with _lock:
        _restore()
        source = _by_id.get(node.partition("#")[0])
        if source is None or not _accessible(window, source):
            return None
        members = _coverage_nodes.get(node)
        return list(members) if members is not None else None


def reference_links(window: tuple | None, ids: Iterable[str]) -> dict[str, dict]:
    """Resolve text mentions separately from coverage and tool-result source links."""
    with _lock:
        _restore()
        selected = {}
        for event_id in dict.fromkeys(str(value) for value in ids):
            entry = _by_id.get(event_id)
            if (entry is None or not _accessible(window, entry)
                    or (entry["kind"] == "input" and entry.get("projection") is None)):
                continue
            covered_by = [node for node, members in _coverage_nodes.items() if event_id in members]
            selected[event_id] = {
                "mentions": list(entry.get("_mentions", ())),
                "mentioned_by": list(_mentioned_by.get(event_id, ())),
                "covers": {node: list(members) for node, members in _coverage_nodes.items()
                           if node.partition("#")[0] == event_id},
                "covered_by": covered_by,
                "source": entry.get("source") if entry["kind"] == "result" else None,
            }
        return selected


def cover(window: tuple, node: str, ids: Iterable[str], visible: set[str]) -> set[str]:
    """Commit one validated coverage fact; all projections derive from its journal."""
    with _lock:
        _restore()
        source, separator, position = node.partition("#")
        output = _by_id.get(source)
        if (not separator or not position.isdecimal() or output is None
                or output["kind"] != "output" or output["window"] != list(window)
                or not 1 <= int(position) <= len(output["actions"])
                or output["actions"][int(position) - 1]["name"] != "cover_events"):
            raise ValueError("覆盖节点必须是本窗口本次 cover_events 行动")
        if node in _coverage_nodes:
            raise ValueError("这次总结行动已经提交过覆盖")
        requested = {str(event_id) for event_id in ids}
        if not requested or source in requested:
            raise ValueError("须指定非自身的已读事件")
        all_entries = _events if window == AGENT_WINDOW else _windows.get(window, ())
        by_source: dict[str, list[dict]] = {}
        for entry in all_entries:
            if entry["kind"] == "result":
                by_source.setdefault(entry["source"], []).append(entry)
        closure = set(requested)
        for event_id in requested:
            entry = _by_id.get(event_id)
            if entry is None or not _accessible(window, entry):
                raise ValueError(f"不是本窗口已读事件: {event_id}")
        links = say_links(window)
        linked_echoes = {echo for echo, _reference in links.values()}
        while True:
            expanded = set(closure)
            for event_id in closure:
                entry = _by_id[event_id]
                if entry["kind"] == "result":
                    expanded.add(entry["source"])
                elif entry["kind"] == "output":
                    expanded.update(result["id"] for result in by_source.get(event_id, ()))
            for echo, reference in links.values():
                group = {echo, reference.split("#")[0]}
                group.update(result["id"] for result in by_source.get(reference.split("#")[0], ()))
                if group & expanded:
                    expanded.update(group)
            if expanded == closure:
                break
            closure = expanded
        for event_id in closure:
            entry = _by_id[event_id]
            if (entry["kind"] == "input" and entry["event"].get("post_type") == "message_sent"
                    and event_id not in linked_echoes):
                raise ValueError("自发回声尚无唯一已读 say 返回，不能孤立覆盖")
            if entry["kind"] == "output":
                positions = [item["position"] for result in by_source.get(event_id, ())
                             for item in result["returns"]]
                if sorted(positions) != list(range(len(entry["actions"]))):
                    raise ValueError(f"输出 {event_id} 还有未读或未返回的行动，不能拆批覆盖")
            if entry["kind"] == "result":
                for item in entry["returns"]:
                    if item["name"] != "say" or not str(item["content"]).lstrip("-").isdecimal():
                        continue
                    linked = links.get(str(item["content"]))
                    if linked is None or linked[1] != _call({**entry, **item}):
                        raise ValueError("say 返回尚无唯一已读回声，不能孤立覆盖")
        if source in closure:
            raise ValueError("覆盖不能包含本次输出")
        for event_id in closure:
            entry = _by_id.get(event_id)
            if (entry is None or not _accessible(window, entry)
                    or (event_id not in visible and event_id not in _covered.get(window, ())
                        and not (window == AGENT_WINDOW and event_id in _covered.get(tuple(entry["window"]), ())))
                    or entry.get("hidden")
                    or entry.get("condensed") or (entry["kind"] == "input" and entry.get("projection") is None)):
                raise ValueError(f"覆盖成员不在当前主窗口可见已读流中: {event_id}")
        _append({"kind": "cover", "window": list(window), "node": node,
                 "members": sorted(closure)}, datetime.now().strftime("%Y%m%d"))
        return closure


def _call(entry: dict) -> str:
    return f"{entry['source']}#{entry['position'] + 1}"


def say_links(window: tuple | None) -> dict[str, tuple[str, str]]:
    with _lock:
        _restore()
        recorded = ([entry for entry in _events if not entry.get("hidden")]
                    if window == AGENT_WINDOW else events(window, True))
    echoes: dict[str, list[str]] = {}
    returns: dict[str, list[str]] = {}
    for entry in recorded:
        if entry["kind"] == "input" and entry["event"].get("post_type") == "message_sent":
            message_id = entry["event"].get("message_id")
            if message_id is not None:
                echoes.setdefault(str(message_id), []).append(entry["id"])
        if entry["kind"] == "result":
            for item in entry["returns"]:
                if item.get("name") == "say" and str(item["content"]).lstrip("-").isdecimal():
                    returns.setdefault(str(item["content"]), []).append(_call({**entry, **item}))
    return {key: (ids[0], returns[key][0]) for key, ids in echoes.items()
            if len(ids) == len(returns.get(key, ())) == 1}
