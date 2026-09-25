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
DISPLAY_CHARS = 200
AGENT_WINDOW = ("agent", 0)
_REFERENCE = re.compile(r"(?<![A-Za-z0-9_-])(\d{8}-[1-9]\d*(?:#[1-9]\d*)?)(?![A-Za-z0-9_#-])")

_lock = RLock()
_root: Path | None = None
_events: list[dict] = []
_by_id: dict[str, dict] = {}
_windows: dict[tuple, list[dict]] = {}
_next: dict[str, int] = {}
_pending: dict[str, dict] = {}
_notified: set[str] = set()
_floors: dict[tuple, str | None] = {}
_arrival_links: dict[str | None, list[str | None]] = {None: [None, None]}
_arrival_ranks: dict[str, int] = {}
_covered: dict[tuple, set[str]] = {}
_coverage_nodes: dict[str, list[str]] = {}
_mentioned_by: dict[str, list[str]] = {}
_origins: dict[tuple[tuple, str], dict] = {}
_sources: dict[str, dict] = {}
_seen_messages: set[tuple[tuple, str]] = set()
_failed = False


def _accessible(window: tuple | None, entry: dict) -> bool:
    return entry["window"] == list(window or ()) or window == AGENT_WINDOW


def _directory() -> Path:
    from mods import storage

    return Path(storage.root_path).parent / "event_stream"


def source_page_root() -> Path:
    """Return the private spool directory beside the source journal."""
    return _directory() / "pages"


def _input_message_identity(entry: dict, sources: dict[str, dict]) -> tuple[tuple, str] | None:
    if entry["kind"] != "input" or entry["event"].get("message_id") is None:
        return None
    window = (sources[entry["source"]]["window"] if entry.get("source")
              else entry.get("source_window") or entry["window"])
    value = str(entry["event"]["message_id"])
    return tuple(window), str(int(value)) if value.lstrip("-").isdecimal() else value


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
    arrival_links: dict[str | None, list[str | None]] = {None: [None, None]}
    covered: dict[tuple, set[str]] = {}
    coverage_nodes: dict[str, list[str]] = {}
    mentioned_by: dict[str, list[str]] = {}
    origins: dict[tuple[tuple, str], dict] = {}
    sources: dict[str, dict] = {}
    for path in sorted(directory.glob("????????.jsonl")):
        data = path.read_bytes()
        complete = data.rfind(b"\n") + 1
        for line in data[:complete].splitlines():
            entry = json.loads(line.decode("utf-8"))
            _apply(entry, restored, indexes, windows, counters, pending, covered, coverage_nodes,
                   origins, mentioned_by, notified, floors, arrival_links, sources)
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
    _arrival_links.clear()
    _arrival_links.update(arrival_links)
    _arrival_ranks.clear()
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
    _seen_messages.update(identity for entry in restored
                          if (identity := _input_message_identity(entry, sources)) is not None)
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


def _arrival_sequence(links: dict[str | None, list[str | None]]) -> Iterable[str]:
    arrival = links[None][1]
    while arrival is not None:
        yield arrival
        arrival = links[arrival][1]


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
                or str(linked["event"].get("message_id")) != str(entry["event"].get("message_id"))):
            raise ValueError("source read does not match its live arrival")


def _apply(
    entry: dict, recorded: list[dict], indexes: dict[str, dict], windows: dict[tuple, list[dict]],
    counters: dict[str, int], pending: dict[str, dict], covered: dict[tuple, set[str]],
    coverage_nodes: dict[str, list[str]], origins: dict[tuple[tuple, str], dict],
    mentioned_by: dict[str, list[str]], notified: set[str], floors: dict[tuple, str | None],
    arrival_links: dict[str | None, list[str | None]], sources: dict[str, dict],
) -> None:
    if entry["kind"] == "arrival":
        before = entry.get("before")
        if before is not None:
            if before not in pending or pending[before]["window"] != entry["window"]:
                raise ValueError("arrival before must name a pending arrival in the same window")
            previous = arrival_links[before][0]
        else:
            previous = arrival_links[None][0]
        arrival_links[entry["arrival"]] = [previous, before]
        arrival_links[previous][1] = entry["arrival"]
        arrival_links[before][0] = entry["arrival"]
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
            for arrival in _arrival_sequence(arrival_links):
                if arrival not in pending:
                    continue
                if tuple(pending[arrival]["window"]) == window and not pending[arrival].get("fetched"):
                    pending.pop(arrival)
                if arrival == entry["before"]:
                    break
        return
    if entry["kind"] == "notification_ack":
        notice = indexes[entry["notification"]]
        if notice["kind"] != "notification":
            raise ValueError("notification ack does not name a notification")
        notice["acknowledged"] = True
        notified.update(notice["arrivals"])
        return
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


def _append(entry: dict, day: str) -> None:
    global _failed
    # WHY: 追加可能在写入或 fsync 中途失败，磁盘上可能已有这条或残尾；继续行动会让
    # 外部副作用失去可信的来源记录。保持失败直到重启恢复检查磁盘，而非在进程内重试编号。
    if _failed:
        raise RuntimeError("event stream write failed; refusing further actions")
    if entry["kind"] == "arrival":
        before = entry.get("before")
        if before is not None and (before not in _pending or _pending[before]["window"] != entry["window"]):
            raise ValueError("arrival before must name a pending arrival in the same window")
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
           _origins, _mentioned_by, _notified, _floors, _arrival_links, _sources)
    identity = _input_message_identity(entry, _sources)
    if identity is not None:
        _seen_messages.add(identity)
    if entry["kind"] == "arrival":
        _arrival_ranks.clear()


def _ordered_pending(window: tuple | None = None) -> list[dict]:
    return [_pending[arrival] for arrival in _arrival_sequence(_arrival_links)
            if arrival in _pending and (window is None or _pending[arrival]["window"] == list(window))]


def _arrival_before_or_at(arrival: str, through: str) -> bool:
    if len(_arrival_ranks) != len(_arrival_links) - 1:
        _arrival_ranks.clear()
        _arrival_ranks.update((item, position) for position, item in enumerate(
            _arrival_sequence(_arrival_links)))
    return _arrival_ranks[arrival] <= _arrival_ranks[through]


def _source_snapshot(state: dict) -> dict:
    return {**state, "window": list(state["window"]),
            "queue_window": list(state["queue_window"]),
            "page_counts": list(state["page_counts"]),
            "previous_gaps": list(state["previous_gaps"]),
            "remaining": state["member_count"] - state["read_count"]}


def arrive(window: tuple, event: dict, *, activated: bool = False, origin: str | None = None,
           before: str | None = None) -> str:
    from mods import chatlog

    with _lock:
        _restore()
        arrival_id = uuid4().hex
        entry = {"kind": "arrival", "window": list(window), "arrival": arrival_id,
                 "arrived_at": time.time(), "event": event, "activated": activated,
                 "origin": origin if origin is not None else chatlog.consume_origin(event)}
        if before is not None:
            entry["before"] = before
        _append(entry, datetime.now().strftime("%Y%m%d"))
        return arrival_id


def unread(window: tuple) -> list[dict]:
    with _lock:
        _restore()
        return _ordered_pending(window)


def pending_message(window: tuple, message_id: int | str) -> str | None:
    """Find an unread QQ message identity from its original chat window."""
    with _lock:
        _restore()
        return next((entry["arrival"] for entry in _pending.values()
                     if (tuple(_sources[entry["source"]]["window"]) if entry.get("source")
                         else tuple(entry["window"])) == window
                     if entry["event"].get("message_id") is not None
                     and str(entry["event"]["message_id"]) == str(message_id)), None)


def message_seen(window: tuple, message_id: int | str) -> bool:
    """Check pending and already-read inputs for the same original QQ message."""
    with _lock:
        _restore()
        if pending_message(window, message_id) is not None:
            return True
        value = str(message_id)
        return (window, str(int(value)) if value.lstrip("-").isdecimal() else value) in _seen_messages


def arrival_origin(arrival: str) -> str | None:
    with _lock:
        _restore()
        return _pending.get(arrival, {}).get("origin")


def start_source(name: str, window: tuple, source_type: str, *, anchor: str | None = None,
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
                 "source_type": source_type, "anchor": anchor, "start_seq": start_seq,
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
        windows = dict.fromkeys(tuple(entry["window"]) for entry in _ordered_pending())
        return [_pending_detail(window) for window in windows]


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
        activated = [entry for entry in _ordered_pending()
                     if entry.get("activated") and entry["arrival"] not in _notified]
        if not activated:
            return None
        latest: dict[tuple, str] = {}
        for entry in _ordered_pending():
            latest[tuple(entry["window"])] = entry["arrival"]
        windows = list(dict.fromkeys(tuple(entry["window"]) for entry in activated))
        return _register(agent_window, "notification", arrivals=[entry["arrival"] for entry in activated],
                         through={str(window): latest[window] for window in windows},
                         windows=[list(window) for window in windows],
                         unread=[_pending_detail(window) for window in latest])


def work_windows(agent_window: tuple) -> list[tuple]:
    return list(work_targets(agent_window))


def work_targets(agent_window: tuple) -> dict[tuple, str]:
    """Pending prefixes frozen by delivered notifications, including after restart."""
    with _lock:
        _restore()
        targets: dict[tuple, str] = {}
        for entry in _windows.get(agent_window, ()):
            if entry["kind"] == "notification":
                for window in entry["windows"]:
                    key = tuple(window)
                    if key in targets:
                        continue
                    target = entry["through"][str(key)]
                    if any(tuple(pending["window"]) == key
                           and _arrival_before_or_at(arrival, target)
                           for arrival, pending in _pending.items()):
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
        return next((entry for entry in reversed(_events) if entry.get("arrival") == arrival), None)


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


def event_span(window: tuple | None, *, anchor: str = "", before: int = 0, after: int = 0,
               start: str = "", end: str = "", kinds: Iterable[str] = (),
               source_window: tuple | None = None) -> list[dict]:
    """Select a bounded span in read order, then filter without reordering it."""
    if any(not isinstance(value, int) or value < 0 for value in (before, after)):
        raise ValueError("before 和 after 必须是非负整数")
    if bool(anchor) == bool(start or end) or (start or end) and (not start or not end):
        raise ValueError("请只指定 anchor，或同时指定 start 和 end")
    if anchor and before + after > 39:
        raise ValueError("一次最多查看含中心的 40 条，请缩小范围")
    allowed_kinds = {"input", "output", "result", "notification"}
    selected_kinds = set(kinds)
    if selected_kinds - allowed_kinds:
        raise ValueError("kinds 只能包含 input、output、result、notification")
    with _lock:
        _restore()
        timeline = [entry for entry in _events if _accessible(window, entry)
                    and not (entry["kind"] == "input"
                    and entry.get("projection") is None)]
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
            if last - first >= 40:
                raise ValueError("区间一次最多 40 条；先用 anchor=start、before=0、after=39 查下一段")
            selected = timeline[first:last + 1]
        return [entry for entry in selected if (not selected_kinds or entry["kind"] in selected_kinds)
                and (source_window is None or tuple(entry.get("source_window") or entry["window"]) == source_window)]


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


def output(window: tuple | None, assistant: dict, calls: list[dict]) -> str | None:
    recorded = _register(window, "output", body=assistant.get("content", ""),
                         thought_present=bool(assistant.get("reasoning_content")),
                         actions=[call["function"] for call in calls])
    return recorded["id"] if recorded else None


def result(window: tuple | None, source: str, returns: list[dict], arrival: str) -> dict | None:
    return _register(window, "result", source=source, returns=returns, arrival=arrival)


def events(window: tuple | None, include_condensed: bool = False) -> list[dict]:
    with _lock:
        _restore()
        return [item for item in _windows.get(tuple(window or ()), ()) if not item.get("hidden")
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


def entries(window: tuple | None, include_condensed: bool = False) -> list[dict]:
    with _lock:
        _restore()
        listed = _events if window == AGENT_WINDOW else events(window, include_condensed)
        return [{**entry, **item, "cid": _call({**entry, **item})}
                for entry in listed if entry["kind"] == "result" and not entry.get("hidden")
                and (include_condensed or not entry.get("condensed"))
                for item in entry["returns"]]


def recall(window: tuple | None, cids: Iterable[str]) -> tuple[list[dict], list[str]]:
    wanted = {str(value) for value in cids}
    found = [entry for entry in entries(window, True) if entry["cid"] in wanted]
    return found, sorted(wanted - {entry["cid"] for entry in found})


def _pending_calls(window: tuple | None) -> list[dict]:
    listed = []
    for arrival in _pending.values():
        if arrival["window"] != list(window or ()) or "_stream_results" not in arrival["event"]:
            continue
        values = arrival["event"]["_stream_results"]
        source = values["source"]
        if source not in _by_id or _by_id[source]["window"] != list(window or ()):
            continue
        listed.extend({"source": source, **item, "cid": f"{source}#{item['position'] + 1}",
                       "condensed": bool(_by_id[source].get("condensed"))}
                      for item in values["returns"])
    return listed


def pending_calls(window: tuple | None, cids: Iterable[str]) -> list[dict]:
    """Locate arrived but unread results; callers must prove private visibility."""
    wanted = {str(value) for value in cids}
    with _lock:
        _restore()
        return [item for item in _pending_calls(window) if item["cid"] in wanted]


def condense(window: tuple | None, cids: Iterable[str]) -> int:
    wanted = {str(value) for value in cids}
    with _lock:
        _restore()
        live = entries(window)
        pending = _pending_calls(window)
        all_calls = [*live, *pending]
        sources = {entry["source"] for entry in all_calls if entry["cid"] in wanted
                   and not _by_id[entry["source"]].get("condensed")}
        partial = [entry["cid"] for entry in all_calls if entry["source"] in sources and entry["cid"] not in wanted]
        for source in sources:
            output_entry = _by_id[source]
            returned = [entry["position"] for entry in all_calls if entry["source"] == source]
            if len(returned) != len(output_entry["actions"]) or set(returned) != set(range(len(returned))):
                raise ValueError("这一输出还有行动未返回，不能收缩")
        if partial:
            raise ValueError("同一输出里的行动必须一起收缩，还差: " + ", ".join(sorted(partial)))
        for entry in [*(_by_id[source] for source in sources),
                      *{item["id"]: item for item in live if item["source"] in sources}.values()]:
            if not entry.get("condensed"):
                _append({"kind": "condensed", "target": entry["id"], "window": list(window or ())},
                        datetime.now().strftime("%Y%m%d"))
        return sum(entry["source"] in sources for entry in all_calls)


def clear(window: tuple | None) -> None:
    if window is None:
        return
    with _lock:
        _restore()
        _append({"kind": "clear", "window": list(window)}, datetime.now().strftime("%Y%m%d"))


def render(window: tuple | None) -> str | None:
    listed = entries(window, True)
    if not listed:
        return None
    def short(value: Any) -> str:
        shown = " ".join(str(value).split())
        return shown if len(shown) <= DISPLAY_CHARS else shown[:DISPLAY_CHARS] + "…"
    return "\n".join(["本窗口的操作历史:", *(
        f"- [{entry['cid']}]{'(已收缩)' if entry.get('condensed') else ''} "
        f"{entry['name']}({short(entry['arguments'])}) -> {short(entry['content'])}"
        for entry in listed)])


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
