"""Append-only experiences; no model projection or external action runs here.

Rows: {id: YYYYMMDD-N, time: float, ctx: str, kind: str, messages: list[dict],
...source metadata}. ``item_id``/``timestamp`` on append are for offline import;
ids are never reused, even when their references disappear from a view.

Act rows contain exactly one original assistant message. ``tool_started`` must
succeed before dispatch; each ``record_results`` tool message is its own row,
linked by act_id and tool_call_id. ``complete=True`` closes the act. A missing
response is projected as interrupted/unknown/unexecuted, never retried here.
All returns are detached JSON copies. Importing this module reads no runtime.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import threading
import time

ROOT = Path("data/chat/journal")
_lock = threading.RLock()
_local = threading.local()
_cache = {}  # path -> (stat signature, ordered rows, id index); never a write owner
_ID = re.compile(r"(\d{8})-([1-9]\d*)\Z")


def clone(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


@contextmanager
def _writing():
    with _lock:
        ROOT.mkdir(parents=True, exist_ok=True)
        with (ROOT / ".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            _local.writing = True
            try:
                yield
            finally:
                _local.writing = False
                fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def _reading():
    with _lock:
        if getattr(_local, "writing", False) or not ROOT.is_dir():
            yield
        else:
            with (ROOT / ".lock").open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_SH)
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def _read(path):
    if not path.is_file():
        _cache.pop(path, None)
        return []
    stat = path.stat()
    signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    cached = _cache.get(path)
    if cached is not None and cached[0] == signature:
        return cached[1]
    rows, seen = [], set()
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not _ID.fullmatch(row.get("id", "")):
                    raise ValueError("invalid id")
                if row["id"].split("-")[0] != path.stem:
                    raise ValueError("id in wrong journal day")
                if not isinstance(row.get("messages"), list):
                    raise ValueError("invalid messages")
            except (ValueError, TypeError) as error:
                raise ValueError(f"invalid journal {path.name}:{number}") from error
            if row["id"] in seen:
                raise ValueError(f"duplicate journal id: {row['id']}")
            seen.add(row["id"])
            rows.append(row)
    _cache[path] = (signature, rows, {row["id"]: row for row in rows})
    return rows


def events(*, ctx=None, kind=None, after=0):
    """Detached rows in append order within each day, oldest day first."""
    with _reading():
        found = []
        for path in sorted(ROOT.glob("[0-9]" * 8 + ".jsonl")):
            found.extend(row for row in _read(path)
                         if (ctx is None or row["ctx"] == ctx)
                         and (kind is None or row["kind"] == kind)
                         and row["time"] >= after)
        return clone(found)


def get(item_id: str) -> dict:
    match = _ID.fullmatch(str(item_id))
    if match is None:
        raise ValueError(f"invalid journal id: {item_id}")
    with _reading():
        path = ROOT / (match[1] + ".jsonl")
        _read(path)
        cached = _cache.get(path)
        if cached is not None and item_id in cached[2]:
            return clone(cached[2][item_id])
    raise KeyError(item_id)


def _append(kind, *, ctx, messages=(), item_id=None, timestamp=None, **metadata):
    messages = list(messages)
    if not isinstance(ctx, str) or not ctx or not isinstance(kind, str) or not kind:
        raise ValueError("journal kind and ctx must be nonempty strings")
    if {"id", "time", "messages", "ctx", "kind"} & metadata.keys():
        raise ValueError("reserved journal metadata")
    stamp = time.time() if timestamp is None else float(timestamp)
    day = time.strftime("%Y%m%d", time.localtime(stamp))
    if item_id is not None:
        match = _ID.fullmatch(item_id)
        if match is None:
            raise ValueError("invalid imported journal id")
        day = match[1]
    path = ROOT / (day + ".jsonl")
    existing = _read(path)
    ids = {row["id"] for row in existing}
    if item_id in ids:
        raise ValueError(f"journal id already exists: {item_id}")
    item_id = item_id or f"{day}-{max((int(row['id'].split('-')[1]) for row in existing), default=0) + 1}"
    if kind == "wake" and "number" not in metadata:
        metadata["number"] = max((row.get("number", 0) for row in events(ctx=ctx, kind="wake")), default=0) + 1
    if kind == "assistant" and any(message.get("tool_calls") for message in messages if isinstance(message, dict)) and metadata.get("wake_id") and "step" not in metadata:
        metadata["step"] = max((row.get("step", 0) for row in events(ctx=ctx, kind="assistant")
                                if row.get("wake_id") == metadata["wake_id"]), default=0) + 1
    row = clone({"id": item_id, "time": stamp, "ctx": ctx, "kind": kind,
                 "messages": list(messages), **metadata})
    if not all(isinstance(message, dict) for message in row["messages"]):
        raise ValueError("journal messages must be dictionaries")
    payload = (json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    # WHY: a failed append must not leave a half JSON row before the next wake.
    # The lock protects both the id allocation and this rollback of our own tail.
    created = not path.exists()
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        offset = handle.tell()
        try:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.seek(offset)
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
            raise
    if created:
        directory = os.open(ROOT, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return item_id


def append(kind, *, ctx, messages=(), **metadata) -> str:
    with _writing():
        return _append(kind, ctx=ctx, messages=messages, **metadata)


def begin_act(ctx, assistant, **metadata) -> str:
    if assistant.get("role") != "assistant":
        raise ValueError("begin_act needs an assistant message")
    calls = assistant.get("tool_calls") or []
    ids = [call.get("id") for call in calls]
    if any(not isinstance(one, str) or not one for one in ids) or len(set(ids)) != len(ids):
        raise ValueError("tool calls need unique nonempty ids")
    return append("assistant", ctx=ctx, messages=[assistant], **metadata)


def act_entries(act_id) -> list[dict]:
    act = get(act_id)
    if act["kind"] != "assistant":
        raise ValueError("not an assistant act")
    return [act, *(row for row in events(ctx=act["ctx"]) if row.get("act_id") == act_id)]


def _act(act_id):
    rows = act_entries(act_id)
    calls = rows[0]["messages"][0].get("tool_calls") or []
    return rows, {call["id"] for call in calls}


def tool_started(act_id, call_id) -> str:
    with _writing():
        rows, wanted = _act(act_id)
        if call_id not in wanted:
            raise ValueError("unknown tool call id")
        if any(row["kind"] == "act_end" for row in rows):
            raise ValueError("act is closed")
        if any(row.get("call_id") == call_id for row in rows[1:]):
            raise ValueError("tool already dispatched or answered; do not replay")
        return _append("call_start", ctx=rows[0]["ctx"], act_id=act_id, call_id=call_id, wake_id=rows[0].get("wake_id"))


def record_results(act_id, results, *, complete=False) -> None:
    for result in results:
        with _writing():
            rows, wanted = _act(act_id)
            if any(row["kind"] == "act_end" for row in rows):
                raise ValueError("act is closed")
            message = clone(result)
            call_id = message.get("tool_call_id")
            if message.get("role") != "tool" or call_id not in wanted:
                raise ValueError("result must answer a call of this act")
            if any(row["kind"] == "tool" and row.get("call_id") == call_id for row in rows[1:]):
                raise ValueError("tool already answered")
            _append("tool", ctx=rows[0]["ctx"], messages=[message], act_id=act_id, call_id=call_id, wake_id=rows[0].get("wake_id"))
    if complete:
        with _writing():
            rows, _ = _act(act_id)
            if not any(row["kind"] == "act_end" for row in rows):
                _append("act_end", ctx=rows[0]["ctx"], act_id=act_id, wake_id=rows[0].get("wake_id"))


def missing_result(call_id, rows) -> dict:
    started = any(row["kind"] == "call_start" and row.get("call_id") == call_id for row in rows)
    closed = any(row["kind"] == "act_end" for row in rows)
    if started:
        text = "（这次调用已开始但没有记录到结果；外部结果未知，不要自动重跑）"
    elif closed:
        text = "（这次调用未执行）"
    else:
        text = "（这次调用被打断，没有记录到结果；不要自动重跑）"
    return {"role": "tool", "tool_call_id": call_id, "content": text}


def resolve_act(act_id) -> list[dict]:
    rows, _ = _act(act_id)
    assistant = rows[0]["messages"][0]
    results = {row["call_id"]: row["messages"][0] for row in rows[1:] if row["kind"] == "tool"}
    return [assistant, *(results.get(call["id"]) or missing_result(call["id"], rows)
                         for call in assistant.get("tool_calls") or [])]
