"""Replay rules shared with append validation.

WHY: 维护者 2026-10-23：旧阅读模型留下的条目一起清理（docs/working/proposals/event-stream-core.md §7）。
重放只重建现在还有人读的状态：未读到达、激活、通知，以及 `read_actions` 要的她自己的输出和结果。
生产 JSONL 原样不动，旧行照常接受：它们对未读的影响（正式阅读、信源已读、`floor` 消掉的到达）
照算，其余直接跳过，不进内存。启动时整份重放进内存约 43 MB（2026-10-23 审查），大头是旧的
正式阅读和输出里的思考，现在都不留。
"""

from __future__ import annotations

from collections.abc import Iterable

from . import AGENT_WINDOW, _REFERENCE


# 现在还会写的行。
_REQUIRED = {
    "arrival": ("window", "arrival", "event"),
    "activation": ("arrival", "activation_kind"),
    "mark_read": ("window", "arrivals"),
    "notification_ack": ("notification",),
    "output": ("id", "window", "assistant"),
    "result": ("id", "window", "source", "returns"),
    "notification": ("id", "window", "version", "windows", "arrivals", "activations"),
}
# 只在旧日志里出现的行：正式阅读、覆盖树、离线补回信源、旧的折叠与清空。
_OLD = {"input", "cover", "condensed", "clear", "floor", "source_start", "source_page",
        "source_progress", "source_finish", "source_reopen", "source_pulled", "source_mark_read"}


def _validate_shape(entry: dict) -> None:
    """Check a row this code writes before it reaches the disk."""
    if not isinstance(entry, dict) or entry.get("kind") not in _REQUIRED:
        raise ValueError("unknown event stream row kind")
    kind = entry["kind"]
    if set(_REQUIRED[kind]) - entry.keys():
        raise ValueError(f"invalid {kind} row shape")
    if not isinstance(entry.get("window", []), list):
        raise ValueError("event window must be a list")
    if kind == "arrival" and not isinstance(entry["event"], dict):
        raise ValueError("arrival event must be an object")
    if kind == "output":
        assistant = entry["assistant"]
        calls = assistant.get("tool_calls", []) if isinstance(assistant, dict) else None
        if (not isinstance(assistant, dict) or assistant.get("role") != "assistant"
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


def _reference_candidates(entry: dict) -> Iterable[str]:
    if entry["kind"] == "output":
        texts = [entry.get("body", ""), *(action.get("arguments", "") for action in entry.get("actions", ()))]
    elif entry["kind"] == "result":
        texts = [value for result in entry.get("returns", ())
                 for value in (result.get("arguments", ""), result.get("content", ""))]
    else:
        texts = []
    for value in texts:
        for match in _REFERENCE.finditer(str(value)):
            yield match.group(1)


def _complete_output(entry: dict) -> None:
    """Give a native output the ``body``/``actions`` fields old outputs carry directly."""
    assistant = entry.get("assistant")
    if not isinstance(assistant, dict):
        return
    entry["body"] = assistant.get("content") or ""
    entry["thought_present"] = bool(assistant.get("reasoning_content"))
    entry["actions"] = [call["function"] for call in assistant.get("tool_calls", ())]


def _validate(entry: dict, state: dict) -> None:
    """Reject a new row that does not fit the replayed state before it is written."""
    kind = entry["kind"]
    pending = state["pending"]
    if kind == "arrival" and entry["arrival"] in state["order"]:
        raise ValueError("duplicate arrival")
    elif kind == "activation" and entry["arrival"] not in pending:
        raise ValueError("activation names no unread arrival")
    elif kind == "mark_read":
        window = tuple(entry["window"])
        arrivals = entry["arrivals"]
        ordered = [arrival for arrival, item in pending.items() if tuple(item["window"]) == window]
        if (not isinstance(arrivals, list) or not arrivals
                or len(arrivals) != len(set(arrivals))
                or ordered[:len(arrivals)] != arrivals):
            raise ValueError("mark-read must consume one exact pending prefix")
    elif kind == "notification_ack" and entry["notification"] not in state["notices"]:
        raise ValueError("notification ack names no open notification")
    elif kind == "result" and entry["source"] not in state["outputs"]:
        raise ValueError("result source does not exist")
    if "id" in entry:
        event_id = entry["id"]
        day, _, number = str(event_id).partition("-")
        if not (day.isdecimal() and number.isdecimal()):
            raise ValueError("invalid event id")
        if int(number) <= state["counters"].get(day, 0):
            raise ValueError(f"duplicate event id: {event_id}")


def _pop(state: dict, arrival) -> None:
    if state["pending"].pop(arrival, None) is not None:
        # WHY: 「已通知」只对还未读的到达有意义；离开未读就忘掉，这个集合才不会随历史一直长。
        state["notified"].discard(arrival)


def _apply(entry: dict, state: dict) -> None:
    """Fold one journal row, current or old, into the state that is still read."""
    kind = entry.get("kind")
    if kind not in _REQUIRED and kind not in _OLD:
        raise ValueError(f"unknown event stream row kind: {kind!r}")
    if "id" in entry:
        day, _, number = entry["id"].partition("-")
        state["counters"][day] = max(state["counters"].get(day, 0), int(number))
    pending = state["pending"]
    if kind == "arrival":
        state["order"][entry["arrival"]] = len(state["order"])
        # 旧的 fetch 工具把补回放进单独的 ("source", 键) 队列；那种队列已经没人读了。
        if entry["window"][:1] != ["source"]:
            pending[entry["arrival"]] = entry
    elif kind == "activation":
        if entry["arrival"] in pending:
            pending[entry["arrival"]]["activated"] = True
            pending[entry["arrival"]]["activation_kind"] = entry.get("activation_kind", "wake")
    elif kind in ("mark_read", "source_mark_read"):
        for arrival in entry.get("arrivals") or ():
            _pop(state, arrival)
    elif kind == "floor":
        window, before = entry["window"], entry["before"]
        boundary = state["order"].get(before, -1)
        for arrival in [arrival for arrival, item in pending.items()
                        if state["order"][arrival] <= boundary and item["window"] == window
                        and not item.get("fetched")]:
            _pop(state, arrival)
    elif kind == "notification":
        state["notices"][entry["id"]] = entry
    elif kind == "notification_ack":
        notice = state["notices"].pop(entry["notification"], None)
        if notice is not None:
            state["notified"].update(arrival for arrival in notice["arrivals"] if arrival in pending)
    elif kind in ("output", "result") and entry["window"] == list(AGENT_WINDOW):
        if kind == "output":
            entry = dict(entry)
            _complete_output(entry)
            if isinstance(entry.get("assistant"), dict):
                # read_actions 不读思考；它是旧日志里最占内存的一块。
                entry["assistant"] = {key: value for key, value in entry["assistant"].items()
                                      if key != "reasoning_content"}
            state["outputs"][entry["id"]] = entry
        state["actions"].append(entry)
    if kind in ("input", "result") and entry.get("arrival"):
        _pop(state, entry["arrival"])
