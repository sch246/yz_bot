"""Replay rules shared with append validation."""

from __future__ import annotations

from collections.abc import Iterable

from . import AGENT_WINDOW, _REFERENCE


_REQUIRED = {
    "arrival": ("window", "arrival", "event"),
    "activation": ("arrival", "activation_kind"),
    "source_start": ("source", "name", "window", "queue_window", "source_type"),
    "source_page": ("source", "page_number", "member_count", "mention_count", "cursor"),
    "source_progress": ("source", "cursor"),
    "source_finish": ("source", "state", "stop_cursor"),
    "source_reopen": ("source", "cursor"),
    "source_pulled": ("source",),
    "mark_read": ("window", "arrivals", "read_by"),
    "source_mark_read": ("source", "positions", "count", "mention_count", "arrivals", "read_by"),
    "notification_ack": ("notification",),
    "cover": ("window", "node", "members"),
    "input": ("id", "window", "event", "projection", "read_by", "read_via"),
    "output": ("id", "window", "assistant"),
    "result": ("id", "window", "source", "returns"),
    "notification": ("id", "window", "version", "windows", "arrivals", "activations"),
}
_FORMAL = {"input", "output", "result", "notification"}


def _validate_shape(entry: dict) -> None:
    """Check the current disk vocabulary before replay can reinterpret a row."""
    if not isinstance(entry, dict) or entry.get("kind") not in _REQUIRED:
        raise ValueError("unknown event stream row kind")
    kind = entry["kind"]
    if any(field not in entry for field in _REQUIRED[kind]):
        raise ValueError(f"invalid {kind} row shape")
    if (kind in _FORMAL) != ("id" in entry):
        raise ValueError("formal event id does not match row kind")
    if "window" in entry and not isinstance(entry["window"], list):
        raise ValueError("event window must be a list")
    if kind == "arrival" and not isinstance(entry["event"], dict):
        raise ValueError("arrival event must be an object")
    if kind == "input" and (not isinstance(entry["event"], dict)
                            or entry["projection"] is not None
                            and not isinstance(entry["projection"], dict)):
        raise ValueError("input event or projection is invalid")
    if kind == "input" and (entry["read_by"] is not None and not isinstance(entry["read_by"], str)
                            or entry["read_via"] is not None and not isinstance(entry["read_via"], str)):
        raise ValueError("input provenance is invalid")
    if kind == "notification" and (entry["version"] != 2
                                   or not isinstance(entry["activations"], list)):
        raise ValueError("notification row shape is old")
    if kind == "output" and not isinstance(entry["assistant"], dict):
        raise ValueError("output row shape is old")
    if kind == "output":
        assistant = entry["assistant"]
        calls = assistant.get("tool_calls", [])
        if (assistant.get("role") != "assistant"
                or not isinstance(assistant.get("content"), (str, type(None)))
                or not isinstance(calls, list)
                or any(not isinstance(call, dict) or not isinstance(call.get("function"), dict)
                       or not isinstance(call["function"].get("name"), str)
                       or not isinstance(call["function"].get("arguments"), str)
                       for call in calls)):
            raise ValueError("output assistant is invalid")
    if kind == "result" and (not isinstance(entry["returns"], list) or any(
            not isinstance(item, dict) or type(item.get("position")) is not int
            or not isinstance(item.get("name"), str) or "content" not in item
            for item in entry["returns"])):
        raise ValueError("result returns are invalid")
    if kind == "notification" and (not isinstance(entry["windows"], list)
                                   or not isinstance(entry["arrivals"], list)
                                   or any(not isinstance(item, dict) or not isinstance(item.get("window"), list)
                                          or not isinstance(item.get("kind"), str)
                                          for item in entry["activations"])):
        raise ValueError("notification facts are invalid")
    if kind == "source_mark_read" and not isinstance(entry["positions"], list):
        raise ValueError("source mark-read shape is old")
    if kind == "source_start" and not isinstance(entry["queue_window"], list):
        raise ValueError("source start shape is old")


def _validate_internal_shape(entry: dict) -> None:
    if not isinstance(entry, dict) or entry.get("kind") not in (
            *_REQUIRED, "hide_events", "drop_arrivals"):
        raise ValueError("adapter returned unknown row kind")
    if entry["kind"] == "hide_events":
        if not isinstance(entry.get("ids"), list):
            raise ValueError("invalid visibility translation")
    elif entry["kind"] == "drop_arrivals":
        if not isinstance(entry.get("arrivals"), list):
            raise ValueError("invalid arrival translation")
    elif entry["kind"] == "output" and entry.get("assistant") is None:
        if any(field not in entry for field in ("id", "window", "body", "actions")) or not isinstance(
                entry["actions"], list) or any(
                not isinstance(action, dict) or not isinstance(action.get("name"), str)
                or not isinstance(action.get("arguments"), str)
                for action in entry["actions"]):
            raise ValueError("invalid non-native output translation")
    elif entry["kind"] == "notification" and "projection" in entry:
        if any(field not in entry for field in ("id", "window", "windows", "arrivals")) or not isinstance(
                entry["projection"], dict):
            raise ValueError("invalid notification translation")
    else:
        _validate_shape(entry)


def _accessible(window: tuple | None, entry: dict) -> bool:
    return entry["window"] == list(window or ()) or window == AGENT_WINDOW


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


def _complete_output(entry: dict) -> None:
    assistant = entry.get("assistant")
    if assistant is None:
        return
    entry["body"] = assistant.get("content") or ""
    entry["thought_present"] = bool(assistant.get("reasoning_content"))
    entry["actions"] = [call["function"] for call in assistant.get("tool_calls", ())]


def _validate_source_page(entry: dict, state: dict) -> None:
    if state["state"] != "fetching":
        raise ValueError("source fetch is not running")
    if (type(entry["page_number"]) is not int or entry["page_number"] != state["pages"]
            or type(entry["member_count"]) is not int or entry["member_count"] < 0
            or type(entry["mention_count"]) is not int
            or not 0 <= entry["mention_count"] <= entry["member_count"]):
        raise ValueError("source page must be the next page with a nonnegative count")


def _validate_source_read(entry: dict, state: dict, pending: dict[str, dict]) -> None:
    if state["state"] == "fetching":
        raise ValueError("source is not sealed")
    page, offset = entry["page"], entry["offset"]
    if (type(page) is not int or type(offset) is not int or page < 0
            or page >= state["pages"] or offset < 0
            or offset >= state["page_counts"][page]
            or (page, offset) in state["read_positions"]):
        raise ValueError("source member is absent or already read")
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


def _validate_source_mark_read(entry: dict, state: dict, pending: dict[str, dict]) -> None:
    if state["state"] == "fetching":
        raise ValueError("source is not sealed")
    positions = entry["positions"]
    if (not isinstance(positions, list) or not positions
            or any(not isinstance(position, list) or len(position) != 2
                   or any(type(value) is not int for value in position)
                   or position[0] < 0 or position[0] >= state["pages"]
                   or position[1] < 0 or position[1] >= state["page_counts"][position[0]]
                   or tuple(position) in state["read_positions"] for position in positions)):
        raise ValueError("source mark-read positions are invalid")
    if len(positions) != len({tuple(position) for position in positions}):
        raise ValueError("source mark-read positions are duplicated")
    if type(entry.get("count")) is not int or len(positions) != entry["count"]:
        raise ValueError("source mark-read count is invalid")
    mentions = entry["mention_count"]
    if (type(mentions) is not int or not 0 <= mentions <= entry["count"]
            or state["read_mention_count"] + mentions > state["mention_count"]):
        raise ValueError("source mark-read mention count is invalid")
    arrivals = entry["arrivals"]
    if (not isinstance(arrivals, list) or len(arrivals) != len(set(arrivals))
            or any(arrival not in pending or pending[arrival]["window"] != state["window"]
                   for arrival in arrivals)):
        raise ValueError("source mark-read arrivals are invalid")


def _validate_read_provenance(entry: dict, indexes: dict[str, dict]) -> None:
    if entry["kind"] not in ("input", "mark_read", "source_mark_read"):
        return
    read_by = entry["read_by"]
    if read_by is None:
        return
    output = indexes.get(read_by)
    if (not isinstance(read_by, str) or output is None or output["kind"] != "output"
            or "#" in read_by):
        raise ValueError("read provenance must name a prior formal output")
    if entry["kind"] == "input" and entry.get("read_via") not in (
            "take", "pull", "mentions", "read_messages"):
        raise ValueError("input read_via is invalid")


def _validate(entry: dict, indexes: dict[str, dict], pending: dict[str, dict],
              coverage_nodes: dict[str, list[str]], arrival_order: dict[str, int],
              sources: dict[str, dict]) -> None:
    """Reject an invalid row before either append or replay mutates an index."""
    kind = entry["kind"]
    _validate_read_provenance(entry, indexes)
    if kind in ("source_page", "source_progress", "source_finish", "source_reopen",
                "source_pulled", "source_mark_read") and entry["source"] not in sources:
        raise ValueError("source does not exist")
    if kind == "arrival":
        if entry["arrival"] in arrival_order:
            raise ValueError("duplicate arrival")
        source = entry.get("source")
        if source is not None and (source not in sources or sources[source]["queue_window"] != entry["window"]):
            raise ValueError("arrival source does not match its window")
    elif kind == "source_start":
        if entry["source"] in sources:
            raise ValueError("duplicate source")
    elif kind == "source_page":
        _validate_source_page(entry, sources[entry["source"]])
    elif kind == "source_progress":
        state = sources[entry["source"]]
        if state["state"] != "fetching":
            raise ValueError("source fetch is not running")
        if entry["cursor"] is not None and entry["cursor"] != state["cursor"]:
            raise ValueError("source cursor advances only with a committed page")
    elif kind == "source_finish":
        if sources[entry["source"]]["state"] != "fetching":
            raise ValueError("source fetch is not running")
        if entry["state"] not in ("complete", "gap", "failed"):
            raise ValueError("source finish state is invalid")
    elif kind == "source_reopen":
        state = sources[entry["source"]]
        if state["state"] == "fetching" or state["pulled"]:
            raise ValueError("only an unpulled sealed source can extend")
        if entry["cursor"] is None and state["state"] not in ("gap", "failed"):
            raise ValueError("source has no reliable remote cursor")
    elif kind == "drop_arrivals":
        if any(arrival not in pending for arrival in entry["arrivals"]):
            raise ValueError("arrival to drop does not exist")
    elif kind == "mark_read":
        window = tuple(entry["window"])
        arrivals = entry["arrivals"]
        ordered = [arrival for arrival, item in pending.items() if tuple(item["window"]) == window]
        if (not isinstance(arrivals, list) or not arrivals
                or len(arrivals) != len(set(arrivals))
                or ordered[:len(arrivals)] != arrivals):
            raise ValueError("mark-read must consume one exact pending prefix")
    elif kind == "source_mark_read":
        _validate_source_mark_read(entry, sources[entry["source"]], pending)
    elif kind == "notification_ack":
        notice = indexes[entry["notification"]]
        if notice["kind"] != "notification":
            raise ValueError("notification ack does not name a notification")
    elif kind == "hide_events":
        if any(event_id not in indexes for event_id in entry["ids"]):
            raise ValueError("visibility target does not exist")
    elif kind == "cover":
        window = tuple(entry["window"])
        source = entry["node"].partition("#")[0]
        if (entry["node"] in coverage_nodes or source not in indexes
                or tuple(indexes[source]["window"]) != window or any(
                member not in indexes or not _accessible(window, indexes[member])
                for member in entry["members"])):
            raise ValueError("覆盖日志引用了不存在或不可访问的事件")
    if kind in ("input", "result") and entry.get("arrival") and entry["arrival"] not in pending:
        raise ValueError("arrival was already consumed or does not exist")
    if kind == "input" and entry.get("source") and entry["source"] not in sources:
        raise ValueError("input source does not exist")
    if kind == "input" and "page" in entry:
        _validate_source_read(entry, sources[entry["source"]], pending)
    if "id" in entry:
        event_id = entry["id"]
        if not isinstance(entry.get("window"), list):
            raise ValueError("formal event window is invalid")
        if not isinstance(event_id, str) or event_id.count("-") != 1:
            raise ValueError("invalid event id")
        if event_id in indexes:
            raise ValueError(f"duplicate event id: {event_id}")
        day, number = event_id.split("-", 1)
        if not day.isdecimal() or not number.isdecimal():
            raise ValueError("invalid event id")
        if kind == "result" and entry["source"] not in indexes:
            raise ValueError("result source does not exist")
        if kind == "input":
            _input_message_identity(entry, sources)
        list(_reference_candidates(entry))


def _apply(
    entry: dict, recorded: list[dict], indexes: dict[str, dict], windows: dict[tuple, list[dict]],
    counters: dict[str, int], pending: dict[str, dict], covered: dict[tuple, set[str]],
    coverage_nodes: dict[str, list[str]], origins: dict[tuple[tuple, str], dict],
    mentioned_by: dict[str, list[str]], notified: set[str],
    arrival_order: dict[str, int], sources: dict[str, dict],
    seen_messages: set[tuple[tuple, str, int, str | None]],
    arrival_members: dict[str, dict], arrival_skips: dict[str, dict],
    source_arrivals: set[str],
) -> None:
    if entry["kind"] == "arrival":
        arrival_order[entry["arrival"]] = len(arrival_order)
        arrival_members[entry["arrival"]] = {
            "arrival": entry["arrival"], "window": entry["window"],
            "origin": entry.get("origin"), "source": entry.get("source"),
            "order": arrival_order[entry["arrival"]],
        }
        pending[entry["arrival"]] = entry
        return
    if entry["kind"] == "source_start":
        source = entry["source"]
        sources[source] = {"key": source, "name": entry["name"], "window": entry["window"],
                           "queue_window": entry["queue_window"],
                           "source_type": entry["source_type"], "pulled": False,
                           "state": "fetching", "anchor": entry.get("anchor"),
                           "anchor_time": entry.get("anchor_time"),
                           "fetch_anchor": entry.get("anchor"),
                           "cursor": entry.get("start_seq"), "stop_cursor": None,
                           "pending_boundary": entry.get("pending_boundary"),
                           "arrival_boundary": len(arrival_order),
                           "pages": 0, "page_counts": [], "member_count": 0,
                           "mention_count": 0, "read_mention_count": 0,
                           "read_positions": set(), "read_entries": {}, "skip_entries": {},
                           "gap": None, "previous_gaps": [], "error": None}
        return
    if entry["kind"] == "source_page":
        state = sources[entry["source"]]
        state["page_counts"].append(entry["member_count"])
        state["pages"] += 1
        state["member_count"] += entry["member_count"]
        state["mention_count"] += entry["mention_count"]
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
        state["state"] = entry["state"]
        state["gap"] = entry.get("gap")
        state["error"] = entry.get("error")
        state["stop_cursor"] = entry["stop_cursor"]
        if state["gap"]:
            state["previous_gaps"].append(state["gap"])
        return
    if entry["kind"] == "source_reopen":
        state = sources[entry["source"]]
        state["state"] = "fetching"
        state["cursor"] = entry["cursor"]
        state["fetch_anchor"] = entry.get("fetch_anchor")
        state["gap"] = None
        state["error"] = None
        return
    if entry["kind"] == "source_pulled":
        sources[entry["source"]]["pulled"] = True
        return
    if entry["kind"] == "activation":
        if entry["arrival"] in pending:
            pending[entry["arrival"]]["activated"] = True
            pending[entry["arrival"]]["activation_kind"] = entry["activation_kind"]
        return
    if entry["kind"] == "drop_arrivals":
        for arrival in entry["arrivals"]:
            pending.pop(arrival)
        return
    if entry["kind"] == "mark_read":
        # WHY: 「标为已读」只推进持久未读水位，不是主体真正读到的一段经历，
        # 所以它没有正式事件号。原文权威仍是 chatlog，之后可按 message_id/origin 查回。
        window = tuple(entry["window"])
        arrivals = entry["arrivals"]
        for arrival in arrivals:
            marked = pending.pop(arrival)
            arrival_skips[arrival] = entry
            identity = _message_identity(window, marked["event"])
            if identity is not None:
                seen_messages.add(identity)
        return
    if entry["kind"] == "source_mark_read":
        # 历史信源的已读坐标只由日志重放派生；和 window mark_read 一样不伪造 input。
        state = sources[entry["source"]]
        positions = entry["positions"]
        state["read_positions"].update(map(tuple, positions))
        state["skip_entries"].update((tuple(position), entry) for position in positions)
        state["read_mention_count"] += entry["mention_count"]
        state["pulled"] = True
        for arrival in entry["arrivals"]:
            marked = pending.pop(arrival)
            source_arrivals.add(arrival)
            identity = _message_identity(tuple(state["window"]), marked["event"])
            if identity is not None:
                seen_messages.add(identity)
        return
    if entry["kind"] == "notification_ack":
        notice = indexes[entry["notification"]]
        notice["acknowledged"] = True
        notified.update(notice["arrivals"])
        return
    if entry["kind"] == "hide_events":
        for event_id in entry["ids"]:
            indexes[event_id]["hidden"] = True
        return
    if entry["kind"] == "cover":
        window = tuple(entry["window"])
        # WHY: 覆盖只改变默认投影，原事件仍须能按稳定号反查；多次覆盖同一成员
        # 只是多条结论边，不能把它从原索引里删除或改写。
        coverage_nodes[entry["node"]] = list(entry["members"])
        covered.setdefault(window, set()).update(entry["members"])
        return
    source_read = entry["kind"] == "input" and "page" in entry
    if entry["kind"] == "output":
        _complete_output(entry)
    event_id = entry["id"]
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
    if entry["kind"] == "result" and indexes[entry["source"]].get("hidden"):
        entry["hidden"] = True
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
        state["read_positions"].add((entry["page"], entry["offset"]))
        state["read_entries"][(entry["page"], entry["offset"])] = entry
        state["read_mention_count"] += bool(entry.get("mentioned"))
        state["pulled"] = True
    if entry.get("arrival"):
        arrival = pending.get(entry["arrival"])
        if entry["kind"] == "input" and arrival is not None and arrival.get("source"):
            sources[arrival["source"]]["pulled"] = True
        if source_read:
            source_arrivals.add(entry["arrival"])
        pending.pop(entry["arrival"], None)
    identity = _input_message_identity(entry, sources)
    if identity is not None:
        seen_messages.add(identity)
