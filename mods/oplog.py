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
_REFERENCE = re.compile(r"(?<![A-Za-z0-9_-])(\d{8}-[1-9]\d*(?:#[1-9]\d*)?)(?![A-Za-z0-9_#-])")

_lock = RLock()
_root: Path | None = None
_events: list[dict] = []
_by_id: dict[str, dict] = {}
_windows: dict[tuple, list[dict]] = {}
_next: dict[str, int] = {}
_pending: dict[str, dict] = {}
_covered: dict[tuple, set[str]] = {}
_coverage_nodes: dict[str, list[str]] = {}
_mentioned_by: dict[str, list[str]] = {}
_origins: dict[tuple[tuple, str], dict] = {}
_failed = False


def _directory() -> Path:
    from mods import storage

    return Path(storage.root_path).parent / "event_stream"


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
    covered: dict[tuple, set[str]] = {}
    coverage_nodes: dict[str, list[str]] = {}
    mentioned_by: dict[str, list[str]] = {}
    origins: dict[tuple[tuple, str], dict] = {}
    for path in sorted(directory.glob("????????.jsonl")):
        data = path.read_bytes()
        complete = data.rfind(b"\n") + 1
        for line in data[:complete].splitlines():
            entry = json.loads(line.decode("utf-8"))
            _apply(entry, restored, indexes, windows, counters, pending, covered, coverage_nodes, origins, mentioned_by)
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
    _covered.clear()
    _covered.update(covered)
    _coverage_nodes.clear()
    _coverage_nodes.update(coverage_nodes)
    _mentioned_by.clear()
    _mentioned_by.update(mentioned_by)
    _origins.clear()
    _origins.update(origins)
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


def _apply(
    entry: dict, recorded: list[dict], indexes: dict[str, dict], windows: dict[tuple, list[dict]],
    counters: dict[str, int], pending: dict[str, dict], covered: dict[tuple, set[str]],
    coverage_nodes: dict[str, list[str]], origins: dict[tuple[tuple, str], dict],
    mentioned_by: dict[str, list[str]],
) -> None:
    if entry["kind"] == "arrival":
        pending[entry["arrival"]] = entry
        return
    if entry["kind"] == "activation":
        if entry["arrival"] in pending:
            pending[entry["arrival"]]["activated"] = True
        return
    if entry["kind"] == "condensed":
        indexes[entry["target"]]["condensed"] = True
        return
    if entry["kind"] == "cover":
        window = tuple(entry["window"])
        source = entry["node"].partition("#")[0]
        if (entry["node"] in coverage_nodes or source not in indexes
                or tuple(indexes[source]["window"]) != window or any(
                member not in indexes or tuple(indexes[member]["window"]) != window
                for member in entry["members"])):
            raise ValueError("覆盖日志引用了不存在的同窗口事件")
        coverage_nodes[entry["node"]] = list(entry["members"])
        covered.setdefault(window, set()).update(entry["members"])
        return
    if entry["kind"] == "clear":
        for existing in windows.get(tuple(entry["window"]), ()):
            if existing["kind"] == "result":
                existing["hidden"] = True
        return
    event_id = entry["id"]
    if event_id in indexes:
        raise ValueError(f"duplicate event id: {event_id}")
    mentions = []
    seen_mentions = set()
    for reference in _reference_candidates(entry):
        target_id, separator, position = reference.partition("#")
        target = indexes.get(target_id)
        if target is None or target["window"] != entry["window"]:
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
        if key in origins:
            raise ValueError(f"duplicate chatlog origin: {entry['origin']}")
        origins[key] = entry
    if entry.get("arrival"):
        pending.pop(entry["arrival"], None)


def _append(entry: dict, day: str) -> None:
    global _failed
    if _failed:
        raise RuntimeError("event stream write failed; refusing further actions")
    if entry["kind"] == "input" and entry.get("origin") and (tuple(entry["window"]), entry["origin"]) in _origins:
        raise ValueError(f"duplicate chatlog origin: {entry['origin']}")
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
    _apply(entry, _events, _by_id, _windows, _next, _pending, _covered, _coverage_nodes, _origins, _mentioned_by)


def arrive(window: tuple, event: dict, *, activated: bool = False) -> str:
    from mods import chatlog

    with _lock:
        _restore()
        arrival_id = uuid4().hex
        _append({"kind": "arrival", "window": list(window), "arrival": arrival_id,
                 "arrived_at": time.time(), "event": event, "activated": activated,
                 "origin": chatlog.consume_origin(event)},
                datetime.now().strftime("%Y%m%d"))
        return arrival_id


def unread(window: tuple) -> list[dict]:
    with _lock:
        _restore()
        return [entry for entry in _pending.values() if entry["window"] == list(window)]


def activate(arrival: str) -> None:
    with _lock:
        _restore()
        if arrival in _pending and not _pending[arrival]["activated"]:
            _append({"kind": "activation", "arrival": arrival}, datetime.now().strftime("%Y%m%d"))


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
            if entry is None or entry["window"] != list(window or ()):
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


def _register(window: tuple | None, kind: str, **values: Any) -> dict | None:
    if window is None:
        return None
    with _lock:
        _restore()
        day = datetime.now().strftime("%Y%m%d")
        number = _next.get(day, 0) + 1
        entry = {"id": f"{day}-{number}", "window": list(window), "kind": kind,
                 "read_at": time.time(), **values}
        _append(entry, day)
        return entry


def input(window: tuple | None, event: dict, projection: dict | None, arrival: str) -> dict | None:
    with _lock:
        _restore()
        origin = _pending.get(arrival, {}).get("origin")
        return _register(window, "input", event=event, projection=projection, arrival=arrival, origin=origin)


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


def import_legacy(window: tuple, records: list[tuple[dict, dict]], max_events: int,
                  max_tokens: int, cost) -> list[dict]:
    """Number only archive records actually admitted to the next primary read."""
    with _lock:
        _restore()
        day = datetime.now().strftime("%Y%m%d")
        chosen: list[tuple[dict, dict]] = []
        used = 0
        next_number = _next.get(day, 0)
        for event, projection in records:
            origin = event["_log_origin"]
            if origin_status(window, origin) is not None:
                continue
            provisional = f"{day}-{next_number + len(chosen) + 1}"
            amount = cost(projection, provisional)
            if len(chosen) >= max_events:
                break
            if used + amount > max_tokens:
                tentative = [*chosen, (event, projection)]
                actual = sum(cost(value, f"{day}-{next_number + len(tentative) - index}")
                             for index, (_record, value) in enumerate(tentative))
                if actual > max_tokens:
                    break
            chosen.append((event, projection))
            used += amount
        while chosen:
            actual = sum(cost(projection, f"{day}-{next_number + len(chosen) - index}")
                         for index, (_event, projection) in enumerate(chosen))
            if actual <= max_tokens:
                break
            chosen.pop()
        registered = []
        for event, projection in reversed(chosen):
            origin = event["_log_origin"]
            entry = _register(window, "input", event=event, projection=projection, origin=origin)
            registered.append(entry)
        return registered


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
        if source is None or source["window"] != list(window or ()):
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
            if (entry is None or entry["window"] != list(window or ())
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
        all_entries = _windows.get(window, ())
        by_source: dict[str, list[dict]] = {}
        for entry in all_entries:
            if entry["kind"] == "result":
                by_source.setdefault(entry["source"], []).append(entry)
        closure = set(requested)
        for event_id in requested:
            entry = _by_id.get(event_id)
            if entry is None or entry["window"] != list(window):
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
            if (entry is None or entry["window"] != list(window)
                    or (event_id not in visible and event_id not in _covered.get(window, ()))
                    or entry.get("hidden")
                    or entry.get("condensed") or (entry["kind"] == "input" and entry.get("projection") is None)):
                raise ValueError(f"覆盖成员不在当前主窗口可见已读流中: {event_id}")
        _append({"kind": "cover", "window": list(window), "node": node,
                 "members": sorted(closure)}, datetime.now().strftime("%Y%m%d"))
        return closure


def _call(entry: dict) -> str:
    return f"{entry['source']}#{entry['position'] + 1}"


def entries(window: tuple | None, include_condensed: bool = False) -> list[dict]:
    return [{**entry, **item, "cid": _call({**entry, **item})}
            for entry in events(window, include_condensed) if entry["kind"] == "result"
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
    recorded = events(window, True)
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
