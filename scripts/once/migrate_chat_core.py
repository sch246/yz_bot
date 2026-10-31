#!/usr/bin/env python3
"""Offline, loss-checked import of the pre-journal chat core.

No lifecycle hook is called. All reconstruction and behavioral checks happen in
a temporary directory, even during dry-run. Diagnostics contain locations and
fixed codes only; source content is never formatted into an exception/report.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from uuid import uuid4

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from mods import chatlog, storage
from mods.chat import journal, notes, screen, segment, windows

VERSION = 1
NAME = "chat-core-v1"
ID = re.compile(r"\d{8}-[1-9]\d*\Z")
FOLD = re.compile(r"\b(\d{8}-\d{6}-folded(?:-\d+)?)\b")
NOTE = re.compile(r"^## 笔记 ([^\s（]+)（(data/notes/[^\n]+)）\n", re.MULTILINE)
OLD_KINDS = {"arrival", "activation", "mark_read", "notification_ack", "output", "result",
             "notification", "input", "cover", "condensed", "clear", "floor", "source_start",
             "source_page", "source_progress", "source_finish", "source_reopen", "source_pulled",
             "source_mark_read"}
# These are source names, not an alternate runtime implementation.
OLD_NAMES = re.compile(r"\b(?:oplog|ToolRegistry|SessionBinding|assign_tasks|turn_done|"
                       r"subconscious|mark_window_read|seen_snapshot|restore_seen|"
                       r"_next_loop|wait_loop|request_compress|request_decompress)\b")


class Blocked(Exception):
    def __init__(self, code, path, index=None):
        parts = Path(path).parts
        location = str(Path(*parts[:parts.index("chatlog") + 1])) if "chatlog" in parts else str(path)
        self.issue = {"status": "blocked", "code": code, "path": location}
        if index is not None:
            self.issue["index"] = index
        super().__init__(code)


def clone(value):
    return journal.clone(value)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_read(path, default=None):
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError):
        raise Blocked("invalid-json", path) from None


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_parents(path, root):
    while path.is_relative_to(root):
        fsync_dir(path)
        if path == root:
            break
        path = path.parent


def no_symlink(root, relative):
    path = root / relative
    while path != root:
        if path.is_symlink():
            raise Blocked("symlink-source", relative)
        path = path.parent


def plain(message):
    content = message.get("content")
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")


def day(stamp):
    return time.strftime("%Y%m%d", time.localtime(stamp))


def finite(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Blocked("invalid-timestamp", path)
    return float(value)


def unpair(value):
    if not isinstance(value, list) or len(value) != 2 or not isinstance(value[0], str):
        raise Blocked("invalid-window", "data/event_stream")
    return tuple(value)


def compress_projection(message):
    result = clone(message)
    for call in result.get("tool_calls") or []:
        function = call.get("function", {})
        if function.get("name") != "compress":
            continue
        try:
            arguments = json.loads(function.get("arguments", "{}"))
            summary = arguments.get("summary", "")
            if len(summary) > 60:
                arguments["summary"] = summary[:60] + f"……（共 {len(summary)} 字，完整见收起行）"
                function["arguments"] = json.dumps(arguments, ensure_ascii=False)
        except (TypeError, ValueError, AttributeError):
            pass
    return result


def context_matches(original, old):
    value = compress_projection(original)
    # The old runner deleted previous thinking in drop mode; its oplog can
    # still retain the native field. Keep that source and verify every field
    # that the saved context actually contained.
    for key in ("reasoning_content", "reasoning"):
        if key not in old:
            value.pop(key, None)
    return value == old


@contextmanager
def isolated(root, agent=None):
    """Use pure module APIs with an explicit root, without loading storage."""
    previous = (journal.ROOT, segment.ROOT, chatlog.rootfile, storage.root_path,
                storage.storage, storage.save, notes.ROOT)
    journal.ROOT = root / "data/chat/journal"
    segment.ROOT = root / "data/chat"
    chatlog.rootfile = str(root / "chatlog")
    storage.root_path = str(root / "data/storage")
    storage.storage = {"": {"agent": deepcopy(agent or {})}}
    storage.save = lambda: None
    notes.ROOT = root / "data/notes"
    try:
        yield
    finally:
        (journal.ROOT, segment.ROOT, chatlog.rootfile, storage.root_path,
         storage.storage, storage.save, notes.ROOT) = previous


class Import:
    def __init__(self, root):
        self.root = root
        self.rows = {}
        self.counters = defaultdict(int)
        self.views = {}
        self.current = None
        self.issues = []
        self.sources = {}
        self.events = []
        self.outputs = {}
        self.returns = defaultdict(list)
        self.pending = {}
        self.arrivals = {}
        self.notified = set()
        self.notices = {}
        self.records = {}
        self.agent = {}
        self.awaker = None
        self.detailed = {}
        self.folded = {}
        self.used_acts = set()
        self.expected = {}
        self.steps = defaultdict(int)
        self.inventory = {}
        for relative in ("data/context", "data/event_stream", "chatlog"):
            directory = self.root / relative
            if directory.exists():
                if directory.is_symlink() or any(path.is_symlink() for path in directory.rglob("*")):
                    raise Blocked("symlink-source", relative)
                self.inventory[relative] = sorted(str(path.relative_to(directory)) for path in directory.rglob("*") if path.is_file())

    def remember(self, path):
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != self.root.parent):
            raise Blocked("symlink-source", path.relative_to(self.root))
        self.sources[str(path.relative_to(self.root))] = digest(path)

    def read(self, path, default=None):
        if path.is_file():
            self.remember(path)
        return json_read(path, default)

    def warn(self, code, path, index=None):
        issue = {"status": "preserved", "code": code, "path": str(path)}
        if index is not None:
            issue["index"] = index
        self.issues.append(issue)

    def add(self, kind, ctx, messages=(), *, stamp, item_id=None, **metadata):
        shard = day(stamp)
        for key in ("wake_id", "act_id"):
            if metadata.get(key):
                shard = max(shard, metadata[key].split("-")[0])
        if kind == "wake":
            shard = max(shard, day(metadata.get("ctx_started", stamp)))
        if item_id is None:
            self.counters[shard] += 1
            item_id = f"{shard}-{self.counters[shard]}"
        if item_id in self.rows:
            raise Blocked("duplicate-import-id", "data/event_stream")
        row = {"id": item_id, "kind": kind, "ctx": ctx, "time": stamp,
               "messages": clone(list(messages)), **clone(metadata)}
        self.rows[item_id] = row
        return item_id

    def read_oplog(self):
        directory = self.root / "data/event_stream"
        order = {}
        if any(not re.fullmatch(r"\d{8}\.jsonl", path.name) for path in directory.glob("*.jsonl")):
            raise Blocked("unrecognized-oplog-file", "data/event_stream")
        for path in sorted(directory.glob("????????.jsonl")):
            self.remember(path)
            payload = path.read_bytes()
            if payload and not payload.endswith(b"\n"):
                raise Blocked("unterminated-oplog-tail", path.relative_to(self.root))
            for number, line in enumerate(payload.splitlines(), 1):
                try:
                    entry = json.loads(line)
                    if not isinstance(entry, dict) or entry.get("kind") not in OLD_KINDS:
                        raise ValueError()
                except (ValueError, UnicodeError):
                    raise Blocked("invalid-oplog-row", path.relative_to(self.root), number) from None
                entry = clone(entry)
                entry["_source"] = {"path": str(path.relative_to(self.root)), "line": number}
                kind = entry["kind"]
                eid = entry.get("id")
                if eid is not None:
                    if not isinstance(eid, str) or not ID.fullmatch(eid) or eid in self.rows:
                        raise Blocked("invalid-or-duplicate-formal-id", path.relative_to(self.root), number)
                    shard, counter = eid.split("-")
                    if shard != path.stem or int(counter) <= self.counters[shard]:
                        raise Blocked("nonmonotonic-formal-id", path.relative_to(self.root), number)
                    self.counters[shard] = int(counter)
                    # Reserve every formal id, including no-longer-live input rows.
                    self.rows[eid] = None
                self.events.append(entry)
                if kind == "arrival":
                    arrival = entry.get("arrival")
                    if not isinstance(arrival, str) or arrival in order or not isinstance(entry.get("event"), dict):
                        raise Blocked("invalid-arrival", path.relative_to(self.root), number)
                    unpair(entry.get("window"))
                    order[arrival] = len(order)
                    self.arrivals[arrival] = clone(entry)
                    if entry["window"][:1] != ["source"]:
                        self.pending[arrival] = clone(entry)
                elif kind == "activation":
                    if entry.get("arrival") in self.pending:
                        self.pending[entry["arrival"]].update(activated=True, activation_kind=entry.get("activation_kind", "wake"))
                        self.arrivals[entry["arrival"]].update(activated=True, activation_kind=entry.get("activation_kind", "wake"))
                elif kind in ("mark_read", "source_mark_read"):
                    for arrival in entry.get("arrivals") or ():
                        self.pending.pop(arrival, None)
                        self.notified.discard(arrival)
                elif kind == "floor":
                    boundary = order.get(entry.get("before"), -1)
                    for arrival, item in list(self.pending.items()):
                        if order[arrival] <= boundary and item["window"] == entry.get("window") and not item.get("fetched"):
                            self.pending.pop(arrival)
                            self.notified.discard(arrival)
                elif kind == "notification":
                    self.notices[eid] = entry
                elif kind == "notification_ack":
                    notice = self.notices.pop(entry.get("notification"), None)
                    if notice:
                        self.notified.update(a for a in notice.get("arrivals", []) if a in self.pending)
                elif kind == "output":
                    if eid is None:
                        raise Blocked("output-without-formal-id", path.relative_to(self.root), number)
                    self.outputs[eid] = entry
                elif kind == "result":
                    self.returns[entry.get("source")].append(entry)
                if kind in ("input", "result") and entry.get("arrival"):
                    self.pending.pop(entry["arrival"], None)
                    self.notified.discard(entry["arrival"])
        self.rows.clear()  # reservations are represented by counters from here on

    def import_actions(self):
        for eid, entry in self.outputs.items():
            source = entry["_source"]
            assistant = entry.get("assistant")
            legacy = not isinstance(assistant, dict)
            if legacy:
                # No native call ids existed in this format. Name the translation,
                # retain the formal action id, and never pretend these ids were supplied.
                assistant = {"role": "assistant", "content": entry.get("body", "")}
                calls = entry.get("actions") or []
                if calls:
                    assistant["tool_calls"] = [{"id": f"legacy_{eid}_{position}", "type": "function", "function": call}
                                                for position, call in enumerate(calls)]
                self.warn("translated-nonnative-action", source["path"], source["line"])
            calls = assistant.get("tool_calls") or []
            ids = [call.get("id") for call in calls]
            if assistant.get("role") != "assistant" or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
                raise Blocked("invalid-assistant-protocol", source["path"], source["line"])
            stamp = finite(entry.get("read_at"), source["path"])
            self.add("assistant", "legacy-actions", [assistant], stamp=stamp, item_id=eid,
                     source=source, legacy_protocol=legacy,
                     legacy_window=entry.get("window"), model=entry.get("model"))
            answered = set()
            for batch in self.returns.get(eid, []):
                returns = batch.get("returns")
                if not isinstance(returns, list):
                    raise Blocked("invalid-result-batch", batch["_source"]["path"], batch["_source"]["line"])
                batch_id = batch["id"]
                for position, result in enumerate(returns):
                    try:
                        call_position = result["position"]
                        if type(call_position) is not int or not 0 <= call_position < len(ids):
                            raise ValueError()
                        call_id = result.get("tool_call_id") or ids[call_position]
                        if call_id != ids[call_position] or call_id in answered:
                            raise ValueError()
                        if result["name"] != calls[ids.index(call_id)]["function"]["name"]:
                            raise ValueError()
                        message = {"role": "tool", "tool_call_id": call_id, "content": result["content"]}
                    except (KeyError, TypeError, IndexError, ValueError):
                        raise Blocked("unresolved-tool-result", batch["_source"]["path"], batch["_source"]["line"]) from None
                    self.add("tool", "legacy-actions", [message],
                             stamp=finite(batch.get("read_at"), batch["_source"]["path"]),
                             item_id=batch_id if position == 0 else None,
                             source=dict(batch["_source"], position=position), act_id=eid, call_id=call_id)
                    answered.add(call_id)
                if not returns:
                    self.add("legacy", "legacy-actions", stamp=finite(batch.get("read_at"), batch["_source"]["path"]),
                             item_id=batch_id, source=batch["_source"], legacy_kind="empty_result", act_id=eid)
            # Absence of a return is an unknown outcome, not evidence that it ran
            # or did not run. No call_start/act_end is manufactured for missing calls.
        if set(self.returns) - self.outputs.keys():
            raise Blocked("orphan-result-source", "data/event_stream")
        for entry in self.events:
            if entry.get("id") and entry["id"] not in self.rows:
                self.add("legacy", "legacy-oplog", stamp=finite(entry.get("read_at", 0), entry["_source"]["path"]),
                         item_id=entry["id"], source=entry["_source"], legacy_kind=entry["kind"])

    def window_records(self, text):
        if text in self.records:
            return self.records[text]
        if re.fullmatch(r"[gu][1-9]\d*", text):
            window = ("group" if text[0] == "g" else "private", int(text[1:]))
        elif text == "me":
            window = windows.ME
        else:
            kind, separator, target = text.partition(":")
            if not separator or not re.fullmatch(r"[a-z][a-z0-9_]*", kind) or not target or "/" in target or "\\" in target or target in (".", ".."):
                raise Blocked("invalid-window-label", "data/context/current.json")
            window = (kind, target)
        directory = chatlog.window_path(*window, root=self.root / "chatlog")
        for path in directory.rglob("*.log"):
            self.remember(path)
        with isolated(self.root):
            records = screen.records(window, 1_000_000)
        by_id = {}
        for record in records:
            if record["id"] in by_id and by_id[record["id"]] != record:
                raise Blocked("conflicting-record-identity", "chatlog")
            by_id[record["id"]] = record
        self.records[text] = (window, records)
        return window, records

    def metadata(self, text, record, *, unread=True, kind="", recovered=False, reason=""):
        window, _ = self.window_records(text)
        exact = [entry["event"] for entry in self.arrivals.values()
                 if tuple(entry["window"]) == window and screen.record_id(entry["event"]) == record["id"]]
        if exact:
            event = clone(exact[-1])
        else:
            with isolated(self.root):
                event = screen.event_for_record(window, record)
        return {"time": record["time"], "event": event, "kind": kind,
                "reason": reason, "recovered": recovered, "unread": unread}

    def import_read(self):
        seen = self.agent.pop("seen", {})
        if not isinstance(seen, dict):
            raise Blocked("invalid-seen-map", "data/storage/agent.json")
        labels = set(seen)
        logroot = self.root / "chatlog"
        for directory in logroot.iterdir() if logroot.is_dir() else ():
            if not directory.is_dir():
                continue
            if directory.name == "bot":
                continue
            if directory.name == "me":
                labels.add("me")
                continue
            for target in directory.iterdir():
                if target.is_dir():
                    labels.add(windows.label((directory.name, int(target.name) if directory.name in windows.QQ and target.name.isdecimal() else target.name)))
        for arrival in self.arrivals.values():
            if arrival["window"][:1] != ["source"]:
                labels.add(windows.label(tuple(arrival["window"])))
        current = self.read(self.root / "data/context/current.json", None)
        if current is not None:
            labels.update((current.get("last_shown") or {}).keys())
            labels.update(current.get("windows") or [])
        read = {}
        for text in labels:
            _, records = self.window_records(text)
            stamps = [record["time"] for record in records if record["time"] is not None]
            boundary = max(stamps + ([seen[text]] if text in seen else []), default=None)
            state = {"through": boundary, "at": [r["id"] for r in records if r["time"] == boundary],
                     "pending": {}, "excluded": {r["id"]: None for r in records if r["time"] is None}}
            # Only actual old arrivals below supply unreadness. Ordinary
            # recovered records were deliberately never arrivals; the high
            # watermark prevents their first rescan from inventing summons.
            read[text] = state
        for entry in self.arrivals.values():
            if entry["window"][:1] == ["source"]:
                continue
            text = windows.label(tuple(entry["window"]))
            stamp = entry["event"].get("time")
            if stamp is not None and (text not in seen or stamp > seen[text]):
                identity_ = screen.record_id(entry["event"])
                read[text]["pending"][identity_] = {
                    "time": stamp, "event": clone(entry["event"]), "kind": "", "reason": "",
                    "unread": True, "recovered": False}
        for arrival, entry in self.pending.items():
            text = windows.label(tuple(entry["window"]))
            _, records = self.window_records(text)
            try:
                identity_ = screen.record_id(entry["event"])
            except (ValueError, KeyError):
                raise Blocked("unresolved-pending-identity", entry["_source"]["path"], entry["_source"]["line"]) from None
            if not any(record["id"] == identity_ for record in records):
                raise Blocked("pending-record-missing", entry["_source"]["path"], entry["_source"]["line"])
            kind = entry.get("activation_kind", "wake") if entry.get("activated") else ""
            consumed = arrival in self.notified
            item = {"time": entry["event"].get("time"), "event": clone(entry["event"]),
                    "kind": "" if consumed else kind, "reason": "", "unread": True,
                    "recovered": kind == "离线时叫你"}
            if consumed:
                item["source_kind"] = kind
            read[text]["pending"][identity_] = item
        # An open notification can outlive its pending arrivals. Such a summon
        # cannot be silently lost merely because an older mark_read consumed it.
        for notice in self.notices.values():
            for arrival in notice.get("arrivals", []):
                entry = self.arrivals.get(arrival)
                if entry is None:
                    raise Blocked("notification-arrival-missing", notice["_source"]["path"], notice["_source"]["line"])
                text = windows.label(tuple(entry["window"]))
                identity_ = screen.record_id(entry["event"])
                state = read[text]
                if identity_ not in state["pending"]:
                    state["pending"][identity_] = {"time": entry["event"].get("time"), "event": clone(entry["event"]),
                                                    "unread": True, "kind": entry.get("activation_kind", "wake"),
                                                    "reason": "", "recovered": False}
        self.agent["read"] = read
        self.seen = seen
        self.legacy_current = current

    def note_sources(self, message, path, index):
        text = plain(message)
        if message.get("role") != "system" or not text.startswith(("以下是你的笔记", "这一段里第一次出现的人或群，你的笔记：", "【笔记更新】")):
            return {}
        matches = list(NOTE.finditer(text))
        if not matches:
            raise Blocked("note-sources-missing", path, index)
        result = {}
        for n, match in enumerate(matches):
            scope = match[1]
            try:
                expected = notes._path(scope).as_posix()
            except ValueError:
                raise Blocked("invalid-note-source", path, index) from None
            if match[2] != expected or scope in result:
                raise Blocked("ambiguous-note-source", path, index)
            body = text[match.start():matches[n + 1].start() if n + 1 < len(matches) else None].strip()
            content = body.partition("\n")[2]
            name = content.partition("\n")[0].removeprefix("维护者设定的称呼：") if content.startswith("维护者设定的称呼：") else scope
            result[scope] = {"name": name, "version": hashlib.md5(body.encode()).hexdigest()}
        return result

    def shown_records(self, message, path, index):
        text = plain(message)
        match = re.match(r"窗口 (\S+) · (?:画面|你干活时刚到的) · ", text)
        if message.get("role") != "user" or match is None:
            raise Blocked("unresolved-screen-protocol", path, index)
        label = match[1]
        _, records = self.window_records(label)
        # Head identities survive image/audio/face transforms in the body. Match
        # complete record heads, not prose, and check day sections as well.
        heads = {record["text"].partition("\n")[0]: [] for record in records}
        for record in records:
            heads[record["text"].partition("\n")[0]].append(record)
        found = []
        rendered_day = None
        for line in text.splitlines()[1:]:
            if matched := re.fullmatch(r"# " + re.escape(label) + r" (\d{4})-(\d{2})-(\d{2})", line):
                rendered_day = tuple(map(int, matched.groups()))
                continue
            candidates = heads.get(line, [])
            if rendered_day is not None:
                candidates = [record for record in candidates if tuple(record["day"]) == rendered_day]
            if len(candidates) > 1:
                raise Blocked("ambiguous-screen-record", path, index)
            if candidates:
                found.append(candidates[0])
            elif not line.startswith("    ") and (" | " in line or chatlog._TIME_SUFFIX.search(line)):
                raise Blocked("screen-record-missing", path, index)
        if not found and not re.search(r"（[^\n]+暂无新消息）", text):
            raise Blocked("screen-identities-missing", path, index)
        if len({record["id"] for record in found}) != len(found):
            raise Blocked("duplicate-screen-identity", path, index)
        return label, found

    def act_match(self, message, path, index, *, strict):
        candidates = []
        for eid in self.outputs:
            row = self.rows[eid]
            source = row["messages"][0]
            projected = compress_projection(source)
            if source.get("content") != message.get("content") or (
                    source.get("tool_calls", []) != message.get("tool_calls", []) and
                    projected.get("tool_calls", []) != message.get("tool_calls", [])):
                continue
            candidates.append(eid)
        if len(candidates) == 1:
            eid = candidates[0]
            row = self.rows[eid]
            source = row["messages"][0]
            for key, value in source.items():
                if key in message and message[key] != value and not (
                        key == "tool_calls" and message[key] == compress_projection(source).get(key)):
                    if strict:
                        raise Blocked("assistant-source-conflict", path, index)
                    self.warn("archive-action-not-deduplicated", path, index)
                    return None
            # The oplog intentionally omitted native thinking. The context is
            # its actual owner; merge it once into the original assistant row.
            for key, value in message.items():
                if key not in source:
                    source[key] = clone(value)
            return eid
        if strict:
            raise Blocked("ambiguous-action-source" if candidates else "action-source-missing", path, index)
        self.warn("archive-action-not-deduplicated", path, index)
        return None

    def snapshot(self, mark):
        result = {}
        for text, stamp in (mark.get("seen") or {}).items():
            _, records = self.window_records(text)
            result[text] = {"through": stamp, "at": [r["id"] for r in records if r["time"] == stamp],
                            "pending": {}, "excluded": {}}
        return result

    def convert_context(self, data, name, path, *, strict=False, inherited=None, owner=None, inherited_mark=None):
        if name in self.detailed:
            return self.detailed[name]
        messages = data.get("messages")
        if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
            raise Blocked("invalid-context-messages", path)
        started = finite(data.get("started"), path)
        view = segment.fresh(ctx=name, started=started)
        owner = owner or name
        envelope = strict and name == "legacy-current"
        stats = {key: clone(value) for key, value in data.items() if key not in {
            "started", "messages", "last_shown", "windows", "scopes", "told", "marks", "note_hashes", "last_number"}}
        view["stats"] = stats
        if not strict:
            view["stats"]["legacy_metadata"] = {key: clone(value) for key, value in data.items() if key != "messages"}
        self.detailed[name] = view
        marks = (data.get("marks") or []) if strict else []
        if strict and messages and not marks and inherited is None:
            raise Blocked("current-wake-snapshots-missing", path)
        marks_by_index = {}
        previous = -1
        previous_number = 0
        for mark in marks:
            index = mark.get("index")
            number = mark.get("number")
            if type(index) is not int or index < 0 or index > len(messages) or index <= previous or type(number) is not int or number <= previous_number:
                raise Blocked("invalid-wake-mark", path)
            if strict and not {"time", "at", "boxes", "seen", "last_shown"}.issubset(mark):
                raise Blocked("incomplete-wake-snapshot", path, index)
            marks_by_index[index] = mark
            previous, previous_number = index, number
        if strict and marks and marks[0]["index"] != 0 and inherited is None:
            raise Blocked("current-prefix-wake-metadata-missing", path, 0)
        wake_id = inherited
        latest_notes = {}
        screen_rows = []
        tool_rows = []
        active_act = None
        for index in range(len(messages) + 1):
            mark = marks_by_index.get(index)
            if mark:
                # Snapshot's tool state is the value immediately before this
                # wake. Attach it to the announcement that really precedes it.
                if tool_rows and mark.get("told") is not None:
                    self.rows[tool_rows[-1]]["tool_state"] = clone(mark["told"])
                wake_id = self.add("wake", owner, stamp=finite(mark["time"], path),
                                   number=mark["number"], at=mark["at"], ctx_started=started,
                                   boxes=mark.get("boxes", {}), read_snapshot=self.snapshot(mark),
                                   source={"path": str(path), "mark": index})
                view["items"].append(wake_id)
            if index == len(messages):
                break
            message = messages[index]
            metadata = {"source": {"path": str(path), "message": index}}
            if wake_id:
                metadata["wake_id"] = wake_id
            stamp = self.rows[wake_id]["time"] if wake_id else started
            text = plain(message)
            kind = "legacy" if not strict else "system"
            eid = None
            names = FOLD.findall(text) if message.get("role") == "system" and text.startswith(("【收起", "【步骤收起")) else []
            if names and not strict:
                metadata["legacy_fold_sources"] = list(dict.fromkeys(names))
                names = []
            if names:
                if len(set(names)) != 1:
                    raise Blocked("ambiguous-fold-source", path, index)
                fold_name = names[0]
                if fold_name in self.folded:
                    raise Blocked("cyclic-fold-source", path, index)
                archive_path = self.root / "data/context/archive" / (fold_name + ".json")
                folded = self.read(archive_path)
                if not isinstance(folded, dict):
                    raise Blocked("fold-source-missing", path, index)
                self.folded[fold_name] = True
                covered = self.convert_context(folded, fold_name, str(archive_path.relative_to(self.root)), strict=strict,
                                               inherited=wake_id, owner=owner,
                                               inherited_mark=next((m for m in reversed(marks) if m["index"] <= index), inherited_mark))
                del self.folded[fold_name]
                self.views[fold_name] = covered
                kind = "fold"
                metadata.update(covers=covered["items"], summary=folded.get("reason", ""), level=folded.get("level", 1))
                for covered_id in covered["items"]:
                    for row in self.deep_rows(covered_id):
                        if row["kind"] == "screen":
                            label = row["window"]
                            records = {r["id"]: r for r in self.window_records(label)[1]}
                            screen_rows.append((label, [records[i] for i in row["shown"]]))
            elif message.get("role") == "assistant":
                eid = self.act_match(message, path, index, strict=strict)
                if eid:
                    row = self.rows[eid]
                    if strict:
                        if eid in self.used_acts:
                            raise Blocked("current-action-reused", path, index)
                        self.used_acts.add(eid)
                        row["ctx"] = owner
                        row.update({key: value for key, value in metadata.items() if key != "source"})
                        if message.get("tool_calls") and wake_id:
                            self.steps[wake_id] += 1
                            row["step"] = self.steps[wake_id]
                        for result in self.rows.values():
                            if result.get("act_id") == eid:
                                result["ctx"] = owner
                                if wake_id:
                                    result["wake_id"] = wake_id
                    active_act = eid
                else:
                    kind = "legacy"
            elif message.get("role") == "tool" and active_act:
                candidates = [row["id"] for row in self.rows.values() if row.get("act_id") == active_act and row["messages"] == [message]]
                if len(candidates) == 1:
                    eid = candidates[0]
                elif strict and message.get("content") == "（这次调用被打断，没有结果）" and any(
                        call.get("id") == message.get("tool_call_id")
                        for call in self.rows[active_act]["messages"][0].get("tool_calls", [])):
                    kind = "legacy"
                    metadata.update(act_id=active_act, legacy_kind="interrupted_projection")
                elif strict:
                    raise Blocked("tool-message-source-missing", path, index)
            elif strict and message.get("role") == "user":
                kind = "screen"
                label, shown = self.shown_records(message, path, index)
                mark = next((m for m in reversed(marks) if m["index"] <= index), inherited_mark)
                if mark is None:
                    raise Blocked("screen-wake-missing", path, index)
                boundary = (mark.get("seen") or {}).get(label)
                all_records = self.window_records(label)[1]
                previous_text = (mark.get("last_shown") or {}).get(label)
                positions = [n for n, record in enumerate(all_records) if record["text"] == previous_text]
                if previous_text is not None and len(positions) != 1:
                    raise Blocked("wake-screen-boundary-missing", path, index)
                positions_by_id = {record["id"]: n for n, record in enumerate(all_records)}
                patch = {}
                for record in shown:
                    unread = (positions_by_id[record["id"]] > positions[0] if positions else
                              boundary is None or record["time"] is not None and record["time"] > boundary)
                    patch[record["id"]] = self.metadata(label, record, unread=unread)
                metadata.update(window=label, shown=[r["id"] for r in shown],
                                read_patch={"window": label, "records": patch}, follow_ids=[])
                screen_rows.append((label, shown))
            elif strict:
                sources = self.note_sources(message, path, index)
                if sources:
                    metadata["note_sources"] = sources
                    latest_notes.update(sources)
                if text.startswith("<system-reminder>") and ("工具模块" in text or "可用模块" in text):
                    metadata["legacy_tool_announcement"] = True
            if eid is None:
                eid = self.add(kind, owner, [message], stamp=stamp, **metadata)
                if metadata.get("legacy_tool_announcement"):
                    tool_rows.append(eid)
            view["items"].append(eid)
        if envelope:
            if data.get("told") is not None:
                if not tool_rows:
                    raise Blocked("tool-announcement-missing", path)
                self.rows[tool_rows[-1]]["tool_state"] = clone(data["told"])
            for eid in tool_rows:
                if "tool_state" not in self.rows[eid]:
                    raise Blocked("intermediate-tool-state-unrecoverable", path, self.rows[eid]["source"]["message"])
            expected_scopes = set(data.get("scopes") or [])
            hashes = data.get("note_hashes") or {}
            if set(latest_notes) - expected_scopes or any(hashes.get(scope) != "" for scope in expected_scopes - set(latest_notes)):
                raise Blocked("visible-note-sources-disagree", path)
            if any(hashes.get(scope) != source["version"] for scope, source in latest_notes.items()):
                raise Blocked("note-version-unverified", path)
            last = {}
            for label, shown in screen_rows:
                last.setdefault(label, None)
                if shown:
                    last[label] = shown[-1]["text"]
            if last != (data.get("last_shown") or {}):
                raise Blocked("screen-boundary-unverified", path)
            if set(last) != set(data.get("windows") or []):
                raise Blocked("screen-windows-disagree", path)
            self.expected[name] = {"messages": clone(messages), "notes": latest_notes,
                                   "windows": set(last), "tool_state": data.get("told"),
                                   "numbers": sorted({row["number"] for item in view["items"] for row in self.deep_rows(item)
                                                      if row["kind"] == "wake"})}
        return view

    def deep_rows(self, eid):
        row = self.rows[eid]
        if row["kind"] == "fold":
            for child in row["covers"]:
                yield from self.deep_rows(child)
        else:
            yield row

    def build(self):
        self.agent = self.read(self.root / "data/storage/agent.json", {})
        if not isinstance(self.agent, dict) or "read" in self.agent:
            raise Blocked("source-agent-not-legacy", "data/storage/agent.json")
        self.read_oplog()
        self.import_actions()
        self.import_read()
        if self.legacy_current is not None:
            self.current = self.convert_context(self.legacy_current, "legacy-current", "data/context/current.json", strict=True)
            for row in self.rows.values():
                if row["ctx"] == self.current["ctx"] and row.get("legacy_tool_announcement") and "tool_state" not in row:
                    source = row["source"]
                    raise Blocked("folded-tool-state-unrecoverable", source["path"], source["message"])
            # Preserve high-water numbering even after old dismiss removed marks.
            highest = int(self.legacy_current.get("last_number") or 0)
            existing = max((row.get("number", 0) for row in self.rows.values() if row["kind"] == "wake" and row["ctx"] == self.current["ctx"]), default=0)
            if highest > existing:
                self.add("wake", self.current["ctx"], stamp=self.current["started"], number=highest,
                         ctx_started=self.current["started"], at="", boxes={}, read_snapshot={},
                         source={"path": "data/context/current.json", "field": "last_number"}, legacy_counter_only=True)
        for path in sorted((self.root / "data/context/archive").glob("*.json")):
            if path.stem in self.views:
                continue
            data = self.read(path)
            if not isinstance(data, dict):
                raise Blocked("invalid-archive", path.relative_to(self.root))
            try:
                self.views[path.stem] = self.convert_context(data, path.stem, str(path.relative_to(self.root)))
            except Blocked:
                raise
            self.warn("frozen-legacy-context", path.relative_to(self.root))
        loop_path = self.root / "data/context/loop.jsonl"
        if loop_path.is_file():
            self.remember(loop_path)
            for number, line in enumerate(loop_path.read_text(encoding="utf-8").splitlines(), 1):
                try:
                    row = json.loads(line)
                    if not isinstance(row.get("note"), str) or type(row.get("round")) is not int:
                        raise ValueError()
                    self.add("loop_note", "legacy-loop-notes", stamp=finite(row["t"], loop_path.relative_to(self.root)),
                             note=row["note"], round=row["round"], source={"path": str(loop_path.relative_to(self.root)), "line": number})
                except (KeyError, TypeError, ValueError):
                    raise Blocked("invalid-loop-history", loop_path.relative_to(self.root), number) from None
        self.agent.pop("loop", None)
        self.agent.pop("subconscious", None)
        order = self.agent.pop("tool_schema_modules", [])
        self.agent.pop("tool_state_told", None)
        if not isinstance(order, list) or not all(isinstance(name, str) for name in order):
            raise Blocked("invalid-tool-schema-order", "data/storage/agent.json")
        if order:
            if self.current is None:
                # Schema slots belong to a context; a head with no messages is
                # enough to retain order on the next append.
                self.current = segment.fresh(ctx="legacy-current")
            state = clone(self.legacy_current.get("told") or {}) if self.legacy_current else {}
            if not isinstance(state, dict) or not isinstance(state.get("schemas", {}), dict):
                raise Blocked("invalid-tool-state", "data/context/current.json")
            schemas = state.get("schemas", {})
            state["schemas"] = {name: schemas.get(name, {}) for name in dict.fromkeys([*order, *schemas])}
            self.add_to_view(self.current, "system", tool_state=state)
            if "legacy-current" in self.expected:
                self.expected["legacy-current"]["tool_state"] = state
        if self.current is None:
            self.current = segment.fresh(ctx="legacy-current")
        awaker = self.root / "data/notes/awaker.md"
        if awaker.is_file():
            self.remember(awaker)
            text = awaker.read_text(encoding="utf-8")
            lines = text.splitlines(keepends=True)
            self.awaker = "".join(line for line in lines if not re.match(r"^\s*(?:[-*]\s+)?wait\s+", line))
            removed = len(lines) - len(self.awaker.splitlines(keepends=True))
            if removed:
                self.warn("removed-process-local-waits", "data/notes/awaker.md")
        self.scan_names()
        return self

    def add_to_view(self, view, kind, **metadata):
        eid = self.add(kind, view["ctx"], stamp=view["started"], **metadata)
        view["items"].append(eid)
        return eid

    def scan_names(self):
        paths = [self.root / "data/pyload.py", self.root / "data/storage/links.json"]
        for path in paths:
            if not path.is_file():
                continue
            self.remember(path)
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if OLD_NAMES.search(line):
                    self.warn("runtime-old-public-name", path.relative_to(self.root), number)

    def write(self, stage):
        (stage / "data/chat").mkdir(parents=True)
        with isolated(stage):
            pending = dict(self.rows)
            while pending:
                progressed = False
                for eid, row in list(pending.items()):
                    parents = [row[key] for key in ("wake_id", "act_id") if row.get(key)]
                    parents.extend(row.get("covers", []))
                    if any(parent in pending for parent in parents):
                        continue
                    try:
                        journal.append(row["kind"], ctx=row["ctx"], messages=row["messages"],
                                       item_id=eid, timestamp=row["time"],
                                       **{k: v for k, v in row.items() if k not in {"kind", "ctx", "messages", "id", "time"}})
                    except (ValueError, KeyError):
                        source = row.get("source", {})
                        raise Blocked("journal-import-invariant", source.get("path", "data/event_stream"), source.get("message")) from None
                    del pending[eid]
                    progressed = True
                if not progressed:
                    raise Blocked("cyclic-import-references", "data/context")
            for name, view in self.views.items():
                segment._validate(view)
                atomic_json(segment.ROOT / "views" / (name + ".json"), view)
            if self.current:
                segment.save(self.current)
            self.validate(stage)
        atomic_json(stage / "data/storage/agent.json", self.agent)
        if self.awaker is not None:
            path = stage / "data/notes/awaker.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.awaker, encoding="utf-8")

    def validate(self, stage):
        for name, expected in self.expected.items():
            view = self.detailed[name]
            raw = segment.raw_messages(view)
            if len(raw) != len(expected["messages"]) or any(
                    original != old and not context_matches(original, old)
                    for original, old in zip(raw, expected["messages"])):
                raise Blocked("message-projection-disagrees", "data/context/current.json")
            projection = segment.render(view)
            # Missing returns are explicitly unknown in the new protocol. The
            # import never records such a repair as a successful tool result.
            actual_messages = [message for message in projection if message not in [
                journal.missing_result(call["id"], []) for message in raw
                for call in message.get("tool_calls", [])]]
            if actual_messages != [compress_projection(message) for message in raw]:
                raise Blocked("model-projection-disagrees", "data/context/current.json")
            derived = segment.derive(view)
            observed_notes = {key: {k: v for k, v in value.items() if k != "id"} for key, value in derived["notes"].items()}
            if observed_notes != expected["notes"] or set(derived["windows"]) != expected["windows"] or derived["tool_state"] != expected["tool_state"]:
                raise Blocked("derived-state-disagrees", "data/context/current.json")
            numbers = [wake["number"] for wake in derived["wakes"]]
            if numbers != expected["numbers"]:
                raise Blocked("wake-addresses-disagree", "data/context/current.json")
        for name, view in self.views.items():
            data = self.read(self.root / "data/context/archive" / (name + ".json"))
            raw = segment.raw_messages(view)
            if len(raw) != len(data["messages"]) or any(original != old and not context_matches(original, old)
                                                       for original, old in zip(raw, data["messages"])):
                raise Blocked("archive-messages-disagree", "data/context/archive" / Path(name + ".json"))
        # Exercise the first append and reference operations in a disposable fork
        # of the converted files. Never modify the validated staging tree.
        with tempfile.TemporaryDirectory(prefix="chat-core-behavior-") as temporary:
            probe = Path(temporary)
            shutil.copytree(stage / "data/chat", probe / "data/chat")
            with isolated(probe, self.agent):
                view = deepcopy(self.current) if self.current else segment.fresh(ctx="migration-probe")
                prefix = segment.render(view)
                wake = segment.begin_wake(view, boxes={}, read_snapshot=screen.read_snapshot())
                assistant = {"role": "assistant", "content": "", "tool_calls": [{"id": "migration-probe-call", "type": "function", "function": {"name": "probe", "arguments": "{}"}}]}
                act = segment.begin_act(view, assistant)
                segment.tool_started(view, act, "migration-probe-call")
                segment.record_results(view, act, [{"role": "tool", "tool_call_id": "migration-probe-call", "content": "probe"}], complete=True)
                if segment.render(view)[:len(prefix)] != prefix:
                    raise Blocked("append-prefix-disagrees", "data/chat/view.json")
                address = next(s["id"] for s in segment.derive(view)["steps"] if s["act_id"] == act)
                before = segment.raw_messages(view)
                folded = segment.fold(view, address, address, "probe")
                segment.unfold(view, folded)
                if segment.raw_messages(view) != before:
                    raise Blocked("fold-roundtrip-disagrees", "data/chat/view.json")
                restored = segment.dismiss(view, wake, boxes={})
                screen.restore_read(restored["snapshot"], restored["windows"], patches=restored["patches"])
                if segment.render(view) != prefix:
                    raise Blocked("dismiss-prefix-disagrees", "data/chat/view.json")
                again = segment.begin_wake(view, read_snapshot=screen.read_snapshot())
                if journal.get(again)["number"] <= journal.get(wake)["number"]:
                    raise Blocked("wake-number-reused", "data/chat/view.json")


def source_unchanged(plan):
    for relative, expected in plan.inventory.items():
        directory = plan.root / relative
        actual = sorted(str(path.relative_to(directory)) for path in directory.rglob("*") if path.is_file())
        if actual != expected:
            raise Blocked("source-inventory-changed", relative)
    for relative, expected in plan.sources.items():
        path = plan.root / relative
        if not path.is_file() or path.is_symlink() or digest(path) != expected:
            raise Blocked("source-changed-during-conversion", relative)


def tree_files(root):
    return {str(path.relative_to(root)): digest(path) for path in root.rglob("*") if path.is_file()}


def durable_tree(root):
    for path in root.rglob("*"):
        if path.is_symlink():
            raise Blocked("symlink-source", "data")
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        fsync_dir(directory)
    fsync_dir(root)


def uncommitted_preparation(root, migration, *, retire=False):
    """Retire only the shape preceding the first durable manifest.

    No switch is possible before that manifest. Never infer an inverse rename
    from a missing record: any backup bytes, prepared tree or switched target
    needs manual investigation instead.
    """
    if ((root / "data/chat").exists() or (root / "data/legacy" / NAME).exists()
            or not any((root / relative).exists() for relative in ("data/context", "data/event_stream"))):
        raise Blocked("missing-migration-manifest", migration.relative_to(root))
    for path in migration.iterdir():
        if path.is_symlink():
            raise Blocked("missing-migration-manifest", migration.relative_to(root))
        if path.name == "backup" and path.is_dir() and not any(path.iterdir()):
            continue
        if path.is_file() and re.fullmatch(r"manifest\.json\.[0-9a-f]{32}\.tmp", path.name):
            continue
        raise Blocked("missing-migration-manifest", migration.relative_to(root))
    if retire:
        target = migration.with_name(NAME + "-uncommitted-" + uuid4().hex)
        os.replace(migration, target)
        fsync_dir(migration.parent)
        return target
    return migration


def apply(plan, stage, *, fail_after=None):
    root = plan.root
    migration = root / "data/migrations" / NAME
    if migration.exists():
        if not (migration / "manifest.json").exists():
            source_unchanged(plan)
            uncommitted_preparation(root, migration, retire=True)
        else:
            previous = json_read(migration / "manifest.json", {})
            if previous.get("status") != "rolled-back":
                raise Blocked("unfinished-migration-use-rollback", migration.relative_to(root))
            os.replace(migration, migration.with_name(NAME + "-rolled-back-" + uuid4().hex))
            fsync_dir(migration.parent)
    source_unchanged(plan)
    backup = migration / "backup"
    # Only the changed owners are copied. Chatlogs and other runtime state are
    # read-only inputs, fingerprinted above, and are never rewritten or backed up.
    changes = ["data/context", "data/event_stream", "data/storage/agent.json", "data/notes/awaker.md"]
    manifest = {"version": VERSION, "status": "backing-up", "sources": plan.sources,
                "operations": [], "issues": plan.issues, "backup": {}}
    try:
        backup.mkdir(parents=True)
        sync_parents(backup, root)
        atomic_json(migration / "manifest.json", manifest)
        for relative in changes:
            source = root / relative
            if not source.exists():
                continue
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
            original = tree_files(source) if source.is_dir() else {"": digest(source)}
            copied = tree_files(target) if target.is_dir() else {"": digest(target)}
            if original != copied:
                raise Blocked("backup-validation-failed", relative)
            manifest["backup"][relative] = copied
        durable_tree(backup)
        source_unchanged(plan)
        prepared = migration / "prepared"
        shutil.copytree(stage / "data", prepared / "data")
        durable_tree(prepared)
        if tree_files(stage / "data") != tree_files(prepared / "data"):
            raise Blocked("prepared-validation-failed", "data/migrations/" + NAME + "/prepared")
        source_unchanged(plan)
        manifest["status"] = "switching"
        atomic_json(migration / "manifest.json", manifest)
        legacy = root / "data/legacy" / NAME
        if legacy.exists():
            raise Blocked("legacy-target-exists", legacy.relative_to(root))
        for relative in changes:
            source = root / relative
            if source.exists():
                target = legacy / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                sync_parents(target.parent, root)
                operation = {"kind": "move-old", "source": relative, "target": str(target.relative_to(root))}
                manifest["operations"].append(operation)
                atomic_json(migration / "manifest.json", manifest)
                os.replace(source, target)
                fsync_dir(source.parent)
                fsync_dir(target.parent)
        for relative in ("data/storage/agent.json", "data/notes/awaker.md", "data/chat"):
            source = prepared / relative
            if not source.exists():
                continue
            target = root / relative
            if target.exists():
                raise Blocked("switch-target-exists", relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            manifest["operations"].append({"kind": "install", "source": str(source.relative_to(root)), "target": relative})
            atomic_json(migration / "manifest.json", manifest)
            os.replace(source, target)
            fsync_dir(source.parent)
            fsync_dir(target.parent)
            if fail_after == relative:
                raise OSError("injected failure")
        manifest["status"] = "complete"
        manifest["target"] = tree_files(root / "data/chat")
        atomic_json(migration / "manifest.json", manifest)
    except BaseException:
        if (migration / "manifest.json").is_file():
            rollback(root, manifest, migration)
        raise


def rollback(root, manifest, migration):
    """Replay the write-ahead operation list backwards, including interrupted renames."""
    changes = {"data/context", "data/event_stream", "data/storage/agent.json", "data/notes/awaker.md"}
    installs = {"data/storage/agent.json", "data/notes/awaker.md", "data/chat"}
    for operation in manifest.get("operations", []):
        source, target = operation.get("source"), operation.get("target")
        valid = (operation.get("kind") == "move-old" and source in changes and target == f"data/legacy/{NAME}/{source}" or
                 operation.get("kind") == "install" and target in installs and source == f"data/migrations/{NAME}/prepared/{target}")
        if not valid:
            raise Blocked("invalid-recovery-operation", "data/migrations/" + NAME + "/manifest.json")
        no_symlink(root, source)
        no_symlink(root, target)
    for operation in reversed(manifest["operations"]):
        if operation.get("undone"):
            continue
        source, target = root / operation["source"], root / operation["target"]
        if source.exists() == target.exists():
            raise Blocked("recovery-paths-disagree", "data/migrations/" + NAME + "/manifest.json")
        if operation["kind"] == "install":
            if target.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, source)
                fsync_dir(source.parent)
                fsync_dir(target.parent)
        elif target.exists() and not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, source)
            fsync_dir(source.parent)
            fsync_dir(target.parent)
        operation["undone"] = True
        atomic_json(migration / "manifest.json", manifest)
    legacy = root / "data/legacy" / NAME
    if legacy.exists():
        for directory in sorted((path for path in legacy.rglob("*") if path.is_dir()), reverse=True):
            if not any(directory.iterdir()):
                directory.rmdir()
        if not any(legacy.iterdir()):
            legacy.rmdir()
            fsync_dir(legacy.parent)
    manifest["status"] = "rolled-back"
    atomic_json(migration / "manifest.json", manifest)


def run(root, *, apply_changes=False, recover=False):
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise Blocked("runtime-root-missing", root)
    for relative in ("data/context", "data/event_stream", "data/chat", "data/storage/agent.json",
                     "data/notes/awaker.md", "data/legacy/" + NAME,
                     "data/migrations/" + NAME + "/manifest.json"):
        no_symlink(root, relative)
    migration = root / "data/migrations" / NAME
    marker = migration / "manifest.json"
    orphan = migration.is_dir() and not marker.exists()
    if orphan:
        uncommitted_preparation(root, migration)
        if recover:
            if not apply_changes:
                raise Blocked("rollback-requires-apply", migration.relative_to(root))
            # Validate that these are still old owners before retiring even a
            # preparation-only attempt. Nothing is restored or overwritten.
            source_unchanged(Import(root).build())
            target = uncommitted_preparation(root, migration, retire=True)
            return {"status": "preparation-retired", "path": str(target)}
    existing = json_read(marker)
    if existing:
        if existing.get("version") != VERSION:
            raise Blocked("unsupported-migration-version", marker.relative_to(root))
        if existing.get("status") == "complete":
            if not (root / "data/chat").is_dir() or (root / "data/context").exists() or (root / "data/event_stream").exists():
                raise Blocked("completed-migration-paths-disagree", marker.relative_to(root))
            return {"status": "already-migrated", "path": str(marker)}
        if recover:
            if not apply_changes:
                raise Blocked("rollback-requires-apply", marker.relative_to(root))
            if existing.get("status") != "rolled-back":
                rollback(root, existing, migration)
            return {"status": "rolled-back", "path": str(marker)}
        if existing.get("status") != "rolled-back":
            raise Blocked("unfinished-migration-use-rollback", marker.relative_to(root))
    if recover:
        raise Blocked("no-migration-to-rollback", marker.relative_to(root))
    if (root / "data/chat").exists():
        raise Blocked("new-target-already-exists", "data/chat")
    if not (root / "data/context").exists() and not (root / "data/event_stream").exists():
        raise Blocked("legacy-source-missing", "data/context")
    plan = Import(root).build()
    with tempfile.TemporaryDirectory(prefix="chat-core-convert-") as temporary:
        stage = Path(temporary)
        plan.write(stage)
        source_unchanged(plan)
        report = {"status": "validated", "path": str(root), "entries": len(plan.rows),
                  "views": len(plan.views) + bool(plan.current), "issues": plan.issues}
        if apply_changes:
            apply(plan, stage)
            report["status"] = "applied"
            report["path"] = str(marker)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True, help="stopped, offline runtime root")
    parser.add_argument("--apply", action="store_true", help="back up and switch the validated owners")
    parser.add_argument("--rollback", action="store_true", help="recover an interrupted/failed switch; requires --apply")
    args = parser.parse_args(argv)
    try:
        if args.apply:
            # The runtime must be stopped. This lock serializes converters; it
            # does not claim to lock a running old Bot that knows no such lock.
            root = args.runtime_root.expanduser().resolve()
            if not root.is_dir():
                raise Blocked("runtime-root-missing", root)
            no_symlink(root, ".chat-core-migration.lock")
            with (root / ".chat-core-migration.lock").open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                report = run(root, apply_changes=True, recover=args.rollback)
        else:
            report = run(args.runtime_root, recover=args.rollback)
    except Blocked as error:
        print(json.dumps(error.issue, ensure_ascii=False))
        return 2
    except Exception as error:
        # JSON/OSError/provider exceptions may contain private data. Report only
        # their type, never str(error), repr(error), or a traceback.
        print(json.dumps({"status": "failed", "code": type(error).__name__, "path": str(args.runtime_root)}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
