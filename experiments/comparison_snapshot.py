"""Synthetic multi-window archive snapshots, without a model or Bot lifecycle.

``freeze_sources(sources, bot=..., version="v0", synthetic=True)`` accepts only
in-memory ordinary group day bytes. Each source has ``source_alias``, ``target``
and ``days`` (ISO date -> bytes). All identities must be invented fixtures.
``open_snapshot(frozen, schedule, cursor)`` freezes availability, independently
of event time. Schedule rows contain opened_cursor (1..N), virtual_time (epoch
seconds), and event_keys. Every frozen record must occur exactly once.

``resolve_ref(snapshot, {"snapshot_id": ..., "source_alias": ..., "event_key":
...})`` returns exact bytes and source coordinates, never an arbitrary path.
An alternative message_id lookup requires a source_alias and refuses ambiguity.
``bind_a0_view(snapshot, {"root": fresh_external_path, "bot": ..., "targets":
{alias: target}})`` yields the existing A0 isolated scope, with ``mapping`` and
``check_view()``. Use its real chatlog readers or reader.arrange_archive.

Snapshots contain private control data, including unopened bytes; do not give
the snapshot object to a model. Only the bound archive/names are the legal view.
No persistent source store, A0 replacement, arrivals, wake, echo, continuation,
generic filesystem access, or concurrent process-global bindings are provided.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager, ExitStack
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import a0_calibration as a0
from a0_ledger import Ledger

CAPABILITIES = ("ordinary-group", "utf8", "LF/CRLF-message-records", "multiline", "CQ-raw",
                "missing/duplicate-message-id", "poke/recall/opaque-notice")


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _immutable(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_immutable(item) for item in value)
    return value


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    def bytes_hash(item):
        if isinstance(item, bytes):
            return {"bytes_sha256": _hash(item), "byte_count": len(item)}
        raise TypeError("unsupported snapshot value")
    return _hash(json.dumps(_plain(value), sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"), allow_nan=False,
                            default=bytes_hash).encode())


@contextmanager
def _timezone():
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Shanghai"
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def _binding(bot, targets):
    bot, targets = _plain(bot), _plain(targets)
    if (set(bot) != {"id", "name", "names"} or type(bot["id"]) is not int
            or bot["id"] <= 0 or not isinstance(bot["name"], str) or not bot["name"]
            or not isinstance(bot["names"], dict) or not bot["names"]
            or any(not isinstance(name, str) or not name or type(number) is not int or number <= 0
                   for name, number in bot["names"].items())
            or bot["names"].get(bot["name"]) != bot["id"]):
        raise ValueError("explicit synthetic Bot identity and historical names required")
    if (not targets or any(not isinstance(alias, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", alias)
                           or type(target) is not int or target <= 0 for alias, target in targets.items())
            or len(set(targets.values())) != len(targets)):
        raise ValueError("source aliases require distinct positive synthetic group targets")
    return {"bot": bot, "targets": targets}


def freeze_sources(sources, *, bot, version="v0", synthetic=False):
    """Freeze caller-created bytes; no path/config/real material import exists.

    v0 and v1 group files have the same header. The caller must declare one
    common version; mixed switch-day, private, bot, and sidecar inputs are
    rejected rather than silently omitted. Parser quirks (including CRLF IDs)
    are facts of the existing reader and are not normalized here.
    """
    from mods import chatlog
    if synthetic is not True or version not in {"v0", "v1"}:
        raise ValueError("only declared synthetic ordinary group v0/v1 fixtures supported")
    sources = list(sources)
    if not sources or any(set(source) != {"source_alias", "target", "days"} for source in sources):
        raise ValueError("ordinary group source schema required")
    aliases = [source["source_alias"] for source in sources]
    if len(set(aliases)) != len(aliases):
        raise ValueError("duplicate source_alias")
    binding = _binding(bot, {source["source_alias"]: source["target"] for source in sources})
    files, events = [], []
    with _timezone():
        for source in sorted(sources, key=lambda item: item["source_alias"]):
            alias, target = source["source_alias"], source["target"]
            if not isinstance(source["days"], Mapping) or not source["days"]:
                raise ValueError("explicit ordinary day bytes required")
            for day_text, raw in sorted(source["days"].items()):
                day = date.fromisoformat(day_text)
                if day.isoformat() != day_text or type(raw) is not bytes or not raw or not raw.endswith(b"\n"):
                    raise ValueError("canonical date and nonempty LF-terminated original bytes required")
                file_hash = _hash(raw)
                origin_path = f"group/{target}/{day:%Y-%m}/{day:%d}.log"
                parsed = chatlog.parse_log(raw.decode("utf-8", errors="strict"), kind="group", target=target,
                                           day=(day.year, day.month, day.day), bot_id=binding["bot"]["id"],
                                           bot_names=binding["bot"]["names"], names_complete=True,
                                           version=version, origin_path=origin_path)
                offsets = [0]
                for part in raw.split(b"\n")[:-1]:
                    offsets.append(offsets[-1] + len(part) + 1)
                if not parsed or parsed[0]["_log_origin"].rpartition(":")[2] != "1":
                    raise ValueError("unparsed preamble/empty ordinary source refused")
                for index, record in enumerate(parsed):
                    if record.get("_kind") not in {None, "poke", "recall", "notice"} or type(record.get("time")) is not int:
                        raise ValueError("unsupported ordinary record kind/time")
                    if record.get("_kind") is None:
                        number = record.get("message_id")
                        crlf_number = isinstance(number, str) and re.fullmatch(r"-?\d+\r", number)
                        if (set(record.get("_missing", [])) - {"message_id"} or record.get("_guessed")
                                or type(record.get("user_id")) is not int
                                or (number is not None and type(number) is not int and not crlf_number)):
                            raise ValueError("unsupported sender/message ID representation")
                    first = int(record["_log_origin"].rpartition(":")[2]) - 1
                    last = (int(parsed[index + 1]["_log_origin"].rpartition(":")[2]) - 1
                            if index + 1 < len(parsed) else len(offsets) - 1)
                    start, end = offsets[first], offsets[last]
                    key = _digest([alias, day_text, file_hash, start, end])
                    events.append({"source_alias": alias, "event_key": key, "day": day_text,
                                   "file_sha256": file_hash, "byte_start": start, "byte_end": end,
                                   "line": first + 1, "sha256": _hash(raw[start:end]), "record": record})
                files.append({"source_alias": alias, "day": day_text, "sha256": file_hash,
                              "byte_count": len(raw), "count": len(parsed), "raw": raw})
    value = {"schema": 1, "synthetic": True, "version": version, "timezone": "Asia/Shanghai",
             "capabilities": CAPABILITIES, "binding": binding, "files": files, "events": events}
    return _immutable({**value, "frozen_id": _digest(value)})


def _verify_frozen(frozen):
    if _digest({key: value for key, value in frozen.items() if key != "frozen_id"}) != frozen["frozen_id"]:
        raise ValueError("frozen source/identity drift")


def open_snapshot(frozen_sources, schedule, cursor):
    """Availability is an explicit sequence, never inferred from timestamps."""
    _verify_frozen(frozen_sources)
    schedule = _plain(schedule)
    events = {event["event_key"]: event for event in frozen_sources["events"]}
    if type(cursor) is not int or not 1 <= cursor <= len(schedule):
        raise ValueError("cursor must name an explicit opening")
    seen, opened = set(), []
    previous_clock = None
    for ordinal, row in enumerate(schedule, 1):
        if (set(row) != {"opened_cursor", "virtual_time", "event_keys"}
                or type(row["opened_cursor"]) is not int or row["opened_cursor"] != ordinal
                or type(row["virtual_time"]) is not int or not isinstance(row["event_keys"], list)
                or not row["event_keys"] or (previous_clock is not None and row["virtual_time"] < previous_clock)):
            raise ValueError("explicit monotonic opening table required")
        previous_clock = row["virtual_time"]
        for key in row["event_keys"]:
            if key not in events or key in seen or events[key]["record"]["time"] > row["virtual_time"]:
                raise ValueError("unknown/repeated event or opening before occurrence")
            seen.add(key)
            if ordinal <= cursor:
                opened.append(key)
    if seen != set(events):
        raise ValueError("opening table must account for every frozen event")
    positions, mapping = {}, []
    for key in opened:
        event = events[key]
        pair = (event["source_alias"], event["day"])
        offset, line = positions.get(pair, (0, 1))
        raw = _source_bytes(frozen_sources, event)
        target = frozen_sources["binding"]["targets"][event["source_alias"]]
        day = date.fromisoformat(event["day"])
        mapping.append({"event_key": key, "source_alias": event["source_alias"],
                        "visible_origin": f"group/{target}/{day:%Y-%m}/{day:%d}.log:{line}",
                        "visible_byte_start": offset, "visible_byte_end": offset + len(raw)})
        positions[pair] = (offset + len(raw), line + raw.count(b"\n"))
    # WHY: the native archive range view deduplicates same-ID/same-second
    # copies. Keep every canonical origin, report that projection, and refuse
    # ambiguous ID lookups even when the native index would choose a copy.
    last = {}
    excluded = [{"event_key": key, "reason": "not-opened"} for key in events if key not in opened]
    for key in opened:
        event = events[key]
        record = event["record"]
        if not record.get("_kind") and "message_id" in record:
            from mods import chatlog
            identity = (event["source_alias"], event["day"], chatlog.recall_key(record["message_id"]), record["time"])
            if identity in last:
                excluded.append({"event_key": last[identity], "reason": "native-range-same-id-time-dedup"})
            last[identity] = key
    value = {"schema": 1, "frozen_sources": frozen_sources, "schedule": schedule,
             "opened_cursor": cursor, "virtual_time": schedule[cursor - 1]["virtual_time"],
             "source_sha256": frozen_sources["frozen_id"], "schedule_sha256": _digest(schedule),
             "binding_sha256": _digest(frozen_sources["binding"]), "visible_event_keys": opened,
             "unopened_count": len(events) - len(opened), "excluded": excluded, "mapping": mapping}
    return _immutable({**value, "snapshot_id": _digest(value)})


def _verify_snapshot(snapshot):
    _verify_frozen(snapshot["frozen_sources"])
    if _digest({key: value for key, value in snapshot.items() if key != "snapshot_id"}) != snapshot["snapshot_id"]:
        raise ValueError("snapshot source/opening/binding drift")


def _source_bytes(frozen, event):
    source = next(item for item in frozen["files"] if item["source_alias"] == event["source_alias"]
                  and item["day"] == event["day"])
    raw = source["raw"][event["byte_start"]:event["byte_end"]]
    if source["sha256"] != event["file_sha256"] or _hash(raw) != event["sha256"]:
        raise ValueError("source coordinate/hash drift")
    return raw


def resolve_ref(snapshot, reference):
    """Resolve only this snapshot's visible source-qualified event or unique ID."""
    from mods import chatlog
    _verify_snapshot(snapshot)
    keys = set(reference)
    if (not {"snapshot_id", "source_alias"} <= keys or reference["snapshot_id"] != snapshot["snapshot_id"]
            or keys - {"snapshot_id", "source_alias", "event_key", "message_id", "timestamp"}
            or ("event_key" in keys) == ("message_id" in keys)
            or ("event_key" in keys and "timestamp" in keys)):
        raise ValueError("snapshot-bound source-qualified reference required")
    candidates = []
    for event in snapshot["frozen_sources"]["events"]:
        if event["event_key"] not in snapshot["visible_event_keys"] or event["source_alias"] != reference["source_alias"]:
            continue
        if "event_key" in keys:
            match = event["event_key"] == reference["event_key"]
        else:
            record = event["record"]
            match = (not record.get("_kind") and "message_id" in record
                     and chatlog.recall_key(record["message_id"]) == chatlog.recall_key(reference["message_id"])
                     and ("timestamp" not in keys or record["time"] == reference["timestamp"]))
        if match:
            candidates.append(event)
    if len(candidates) != 1:
        raise ValueError("reference is unopened/unknown/ambiguous; use visible event_key")
    event = candidates[0]
    mapping = next(item for item in snapshot["mapping"] if item["event_key"] == event["event_key"])
    return {**_plain(event), **_plain(mapping), "snapshot_id": snapshot["snapshot_id"],
            "opened_cursor": snapshot["opened_cursor"], "raw": _source_bytes(snapshot["frozen_sources"], event)}


def _safe_root(value):
    path = Path(value).expanduser().absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("symlink output path refused")
    path = a0.external_root(path)
    registered = subprocess.run(["git", "-c", "core.quotePath=false", "worktree", "list", "--porcelain"],
                                cwd=a0.replay.REPOSITORY, capture_output=True, check=True).stdout
    # WHY: older Git lacks porcelain -z. Never guess-decode its quoted paths;
    # a control character in a registered root must close this write boundary.
    if not registered.endswith(b"\n") or b"\0" in registered:
        raise ValueError("malformed Git worktree metadata")
    roots, new_record = [], True
    for line in registered.split(b"\n"):
        if not line:
            new_record = True
        elif line.startswith(b"worktree ") and new_record:
            raw = line.removeprefix(b"worktree ")
            if not raw or raw.startswith(b'"') or any(byte < 32 or byte == 127 for byte in raw):
                raise ValueError("quoted/control Git worktree path refused")
            root = Path(os.fsdecode(raw))
            if not root.is_absolute():
                raise ValueError("nonabsolute Git worktree path refused")
            roots.append(root.resolve())
            new_record = False
        elif (new_record or not (line.startswith((b"HEAD ", b"branch ", b"locked ", b"prunable "))
                                 or line in (b"bare", b"detached", b"locked", b"prunable"))):
            raise ValueError("malformed Git worktree metadata")
    if not roots or any(path.is_relative_to(root) for root in roots):
        raise ValueError("output must be outside every registered Git worktree")
    if path.exists():
        raise ValueError("fresh external root required; no resume or branch reuse")
    return path


def _semantic(record):
    return {key: value for key, value in _plain(record).items() if key not in {"_source", "_log_origin"}}


@contextmanager
def bind_a0_view(snapshot, target_binding):
    """Bind a fresh read-only projection, restoring every patched global on exit.

    This process-global scope must run serially in a fresh offline process.
    Its private tree is disposable. Only archive readers are supported: model
    requests, say, chatlog writes and arrival/wake scheduling are not supplied.
    """
    from mods import chatlog, identity, llm, storage
    from mods.chat import view
    _verify_snapshot(snapshot)
    if set(target_binding) != {"root", "bot", "targets"}:
        raise ValueError("explicit root/Bot/target binding required")
    expected = snapshot["frozen_sources"]["binding"]
    supplied = _binding(target_binding["bot"], target_binding["targets"])
    if _digest(supplied) != snapshot["binding_sha256"]:
        raise ValueError("target/Bot identity binding mismatch")
    a0.replay._require_fresh_process()
    root = _safe_root(target_binding["root"])
    root.mkdir(mode=0o700, parents=True)
    ledger = Ledger(root, "synthetic-comparison-snapshot", snapshot["snapshot_id"])
    clock = a0.Clock()
    clock.now = snapshot["virtual_time"]
    bot = _plain(expected["bot"])
    with a0.isolated(root, ledger, clock=clock, bot=bot, skill_files=(), client=object()) as scope:
        storage.get("archive", "format")["v1_since"] = 0 if snapshot["frozen_sources"]["version"] == "v1" else 2 ** 62
        events = {event["event_key"]: event for event in snapshot["frozen_sources"]["events"]}
        mapping = [_plain(item) for item in snapshot["mapping"]]
        expected_files = {}
        for item in mapping:
            event = events[item["event_key"]]
            raw = _source_bytes(snapshot["frozen_sources"], event)
            relative = item["visible_origin"].rpartition(":")[0]
            expected_files[relative] = expected_files.get(relative, b"") + raw
            record = event["record"]
            alias = event["source_alias"]
            target = expected["targets"][alias]
            sender = record.get("sender", {})
            if sender:
                scope.names[record["user_id"]] = sender["nickname"]
                scope.titles[(target, record["user_id"])] = sender.get("title", "")
            scope.opened.append({"window": ("group", target), "origin": item["visible_origin"]})
        expected_names, expected_titles = dict(scope.names), dict(scope.titles)
        for relative, raw in expected_files.items():
            path = root / "archive" / relative
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(raw)
        allowed = {("group", expected["targets"][events[key]["source_alias"]]) for key in snapshot["visible_event_keys"]}
        allowed_origins = {item["visible_origin"] for item in mapping}
        expected_directories = {str(parent) for relative in expected_files
                                for parent in Path(relative).parents if str(parent) != "."}
        index_paths = {f"group/{target}/.archive-reader.sqlite3" for _, target in allowed}
        original_safe_tree = scope.safe_tree

        def guard():
            _verify_snapshot(snapshot)
            if any(path.is_symlink() for path in (root, *root.parents)):
                raise ValueError("symlink output ancestry drift")
            original_safe_tree()
            if any(path.stat().st_nlink != 1 for path in root.rglob("*") if path.is_file()):
                raise ValueError("shared hardlink in private projection refused")
            if (Path.cwd() != root or chatlog.rootfile != "archive" or clock.now != snapshot["virtual_time"]
                    or identity.bot_id() != bot["id"] or identity.name != bot["name"]
                    or scope.names != expected_names or scope.titles != expected_titles
                    or storage.get("archive", "format") != {"v1_since": 0 if snapshot["frozen_sources"]["version"] == "v1" else 2 ** 62,
                                                               "bot_names": bot["names"]}):
                raise ValueError("active source/clock/identity binding drift")
            archive = root / "archive"
            actual = {str(path.relative_to(archive)) for path in archive.rglob("*") if path.is_file()}
            directories = {str(path.relative_to(archive)) for path in archive.rglob("*") if path.is_dir()}
            if actual - index_paths != set(expected_files) or directories != expected_directories:
                raise ValueError("archive file namespace drift")
            for relative, raw in expected_files.items():
                if (archive / relative).read_bytes() != raw:
                    raise ValueError("archive projection bytes drift")

        scope.safe_tree = guard
        scope.snapshot_id = snapshot["snapshot_id"]
        scope.opened_cursor = snapshot["opened_cursor"]
        scope.mapping = mapping

        def wrap(name, original):
            def read(*args, **kwargs):
                guard()
                if kwargs.get("root") is not None or (kwargs.get("bot_id") is not None and kwargs["bot_id"] != bot["id"]):
                    raise ValueError("external root/Bot override refused")
                if name != "known_windows":
                    kind = args[0] if args else kwargs.get("kind")
                    target = args[1] if len(args) > 1 else kwargs.get("target")
                    if kind != "group" or type(target) not in (int, str) or not str(target).isdecimal():
                        raise ValueError("invalid group window")
                    window = (kind, int(target))
                    if window not in allowed:
                        raise ValueError("unopened/foreign window refused")
                    if name == "read_around" and kwargs.get("message_id") is not None:
                        matches = [event for event in events.values()
                                   if event["event_key"] in snapshot["visible_event_keys"]
                                   and expected["targets"][event["source_alias"]] == window[1]
                                   and not event["record"].get("_kind") and "message_id" in event["record"]
                                   and chatlog.recall_key(event["record"]["message_id"]) == chatlog.recall_key(kwargs["message_id"])
                                   and (kwargs.get("timestamp") is None or kwargs["timestamp"] == event["record"]["time"])]
                        if len(matches) > 1:
                            raise ValueError("ambiguous message_id; use canonical event_key/visible origin")
                result = original(*args, **kwargs)
                records = ([result] if name == "read_origin" else result) if name != "known_windows" else []
                if name == "known_windows" and set(result) != allowed:
                    raise ValueError("archive window namespace leaked")
                for record in records:
                    if record.get("_log_origin") not in allowed_origins:
                        raise ValueError("archive reader returned an unopened/foreign origin")
                    item = next(item for item in mapping if item["visible_origin"] == record["_log_origin"])
                    if (expected["targets"][item["source_alias"]] != window[1]
                            or _semantic(record) != _semantic(events[item["event_key"]]["record"])):
                        raise ValueError("archive reader semantic/source mismatch")
                guard()
                return result
            return read

        def forbidden(*args, **kwargs):
            scope.counters["outbound"] += 1
            raise RuntimeError("snapshot is archive-read-only; action/model request refused")

        with ExitStack() as stack:
            for name in ("read_range", "read_origin", "read_around", "known_windows"):
                stack.enter_context(patch.object(chatlog, name, wrap(name, getattr(chatlog, name))))
            for name in ("write", "_append", "append_backfill_page"):
                stack.enter_context(patch.object(chatlog, name, forbidden))
            scope.client = SimpleNamespace(generate_response=forbidden)
            stack.enter_context(patch.object(llm, "client", scope.client))
            scope.say = forbidden
            for tool in scope.registry.get("meta").tools.values():
                tool.call = forbidden

            def check_view():
                guard()
                assert set(chatlog.known_windows()) == allowed
                for item in mapping:
                    event = events[item["event_key"]]
                    target = expected["targets"][event["source_alias"]]
                    record = chatlog.read_origin("group", target, item["visible_origin"])
                    assert _semantic(record) == _semantic(event["record"])
                    raw = (root / "archive" / item["visible_origin"].rpartition(":")[0]).read_bytes()
                    assert raw[item["visible_byte_start"]:item["visible_byte_end"]] == _source_bytes(snapshot["frozen_sources"], event)
                    item["model_exclusion"] = None if view._model_event(record, True) is not None else "current-A0-model-filter"
                for window in allowed:
                    for record in chatlog.read_range(*window):
                        chatlog.read_around(*window, origin=record["_log_origin"], before=len(mapping), after=len(mapping))
                return {"snapshot_id": snapshot["snapshot_id"], "opened_cursor": snapshot["opened_cursor"],
                        "records": len(mapping), "windows": len(allowed), "counters": dict(scope.counters)}

            scope.check_view = check_view
            check_view()
            scope.mapping = _immutable(mapping)
            yield scope
            guard()
