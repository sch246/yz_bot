"""A0 synthetic offline calibration: no credentials, provider, or real input accepted."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_replay as replay
from a0_ledger import LIMITS, PRICES, Ledger, canonical, cost, digest, durable

MODEL = "synthetic/a0-script-v1"
PILOT = "fictional-a0-pilot-v1"
FACT = "合成离线环境；只能读取已开放过去。say 仅产生分支回声；无真实消息发送。"
# The last record is deliberately never opened. A new window and a renamed
# identity arrive later, so a fixture can expose metadata leaks as well as text.
FIXTURE = [
    {"id": 101, "stamp": 946700000, "window": ["group", 1], "name": "Fable", "wake": True},
    {"id": 102, "stamp": 946700010, "window": ["group", 1], "name": "Fable", "wake": False},
    {"id": 103, "stamp": 946786400, "window": ["group", 1], "name": "Fable renamed", "wake": True},
    {"id": 104, "stamp": 946786410, "window": ["private", 3], "name": "Other fictional", "wake": False},
    {"id": 105, "stamp": 946872800, "window": ["private", 3], "name": "Other fictional", "wake": True},
    {"id": 106, "stamp": 946959200, "window": ["group", 4], "name": "Unopened future", "wake": True},
]
SCHEDULE = [1, 3, 5]
SKILL = "# Synthetic skill\nA fictional calibration instruction, with no host actions.\n"


class Clock:
    def __init__(self):
        self.now = FIXTURE[0]["stamp"]

    def time(self):
        return self.now

    def __getattr__(self, name):
        import time
        if name == "localtime":
            return lambda seconds=None: time.localtime(self.now if seconds is None else seconds)
        if name == "strftime":
            return lambda fmt, value=None: time.strftime(fmt, time.localtime(self.now) if value is None else value)
        return getattr(time, name)


def external_root(value):
    import subprocess
    path = Path(value).expanduser()
    result = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                            cwd=replay.REPOSITORY, capture_output=True, text=True, check=True)
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = replay.REPOSITORY / common
    resolved = path.resolve()
    if (resolved.is_relative_to(replay.REPOSITORY)
            or resolved.is_relative_to(common.resolve().parent)):
        raise ValueError("synthetic root must be outside repository and shared checkout roots")
    if path.is_symlink():
        raise ValueError("synthetic root must not be a symlink")
    return resolved


def code_hashes():
    import subprocess
    result = subprocess.run(["git", "ls-files", "-z", "--", "mods", "experiments"],
                            cwd=replay.REPOSITORY, capture_output=True, check=True)
    paths = {replay.REPOSITORY / value.decode() for value in result.stdout.split(b"\0") if value}
    paths.update(Path(__file__).parent / name for name in ("a0_calibration.py", "a0_ledger.py"))
    return {str(path.relative_to(replay.REPOSITORY)): replay._hash(path)
            for path in sorted(paths) if path.suffix in (".py", ".md") and path.is_file()}


@contextmanager
def isolated(root: Path, ledger: Ledger, *, failure=None):
    from mods import chat, chatlog, connect, context, identity, llm, message, oplog, storage, tools, history
    from mods.chat import view
    replay._require_fresh_process()
    root = external_root(root)
    clock = Clock()
    old_cwd = Path.cwd()
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Shanghai"
    time.tzset()
    old = []

    def bind(module, name, value):
        old.append((module, name, getattr(module, name)))
        setattr(module, name, value)

    counters = {"outbound": 0, "network": 0, "finalizer": 0}
    def forbidden(*args, **kwargs):
        counters["outbound"] += 1
        raise RuntimeError("A0 outbound forbidden")
    def network(*args, **kwargs):
        counters["network"] += 1
        raise RuntimeError("A0 network forbidden")
    def finalizer(*args, **kwargs):
        counters["finalizer"] += 1
        raise AssertionError("production hint finalizer called")

    class VirtualDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(clock.now, tz)
        @classmethod
        def today(cls):
            return cls.fromtimestamp(clock.now)

    scope = SimpleNamespace(clock=clock, names={}, opened=[], echoes=[], probes=[], counters=counters, actions=[], midstop=False)
    def safe_tree():
        if root.is_symlink() or any(path.is_symlink() for path in root.rglob("*")):
            raise RuntimeError("synthetic root contains a symlink; read refused")
        allowed = {tuple(item["window"]) for item in scope.opened}
        for kind in ("group", "private"):
            base = root / "archive" / kind
            if base.is_dir() and any((kind, int(path.name)) not in allowed
                                    for path in base.iterdir() if path.is_dir() and path.name.isdecimal()):
                raise RuntimeError("unregistered synthetic window; read refused")
    scope.safe_tree = safe_tree
    def fail(reason):
        ledger.stop(reason)
        raise RuntimeError(reason)
    scope.fail = fail
    try:
        for module, names in ((oplog, replay._OPLOG_BINDINGS), (context, replay._CONTEXT_BINDINGS),
                              (chatlog, replay._CHATLOG_BINDINGS), (storage, replay._STORAGE_BINDINGS)):
            for name in names:
                value = getattr(module, name)
                bind(module, name, None if name in ("_root", "_latest", "_boot_anchors") else type(value)())
        os.chdir(root)
        bind(history, "msgs", history._empty())
        bind(storage, "root_path", "data/storage")
        bind(chatlog, "rootfile", "archive")
        storage.get("archive", "format", lambda: {})["v1_since"] = 0
        storage.get("archive", "format", lambda: {})["bot_names"] = {"Synthetic Bot": 9}
        for name, value in (("settings", []), ("prompts", {}), ("chat_groups", []),
                            ("llm_config", {"default_model": MODEL}), ("description_cache", {})):
            bind(chat, name, value)
        for module in (chat, view, oplog, tools):
            if hasattr(module, "time"):
                bind(module, "time", clock)
        bind(oplog, "datetime", VirtualDateTime)
        bind(chat, "datetime", VirtualDateTime)
        bind(chat, "_run_hint", finalizer)
        bind(identity, "qq", 9)
        bind(identity, "name", "Synthetic Bot")
        bind(identity, "nicknames", ["Synthetic Bot"])
        bind(identity, "get_user_name", lambda user_id: "Synthetic Bot" if int(user_id) == 9 else scope.names.get(int(user_id), ""))
        bind(identity, "get_group_user_info", lambda group_id, user_id: ("", "Synthetic Bot" if int(user_id) == 9 else scope.names.get(int(user_id), "未知")))
        bind(identity, "getname", lambda user_id=None, group_id=None: scope.names.get(int(user_id), "未知") if user_id is not None else "未知")
        for name in ("send", "sendmsg", "_send_now"):
            bind(message, name, forbidden)
        bind(connect, "call_api", forbidden)
        bind(socket, "create_connection", network)
        bind(socket.socket, "connect", network)
        # WHY: a caller's current event invokes production _run_hint in finally.
        # Offline drives must have no current event, even if a caller had one.
        context.clear_current()
        skill_root = root / "skills"
        skill_root.mkdir(mode=0o700)
        (skill_root / "synthetic.md").write_text(SKILL)
        scope.registry = replay._safe_registry(skill_root)
        scope.send_context = scope.registry.get("meta").tools["say"].call.__globals__["_offline_send_sink"]
        for tool in scope.registry.get("meta").tools.values():
            original_call = tool.call
            def checked_tool(*args, original_call=original_call, **kwargs):
                safe_tree()
                result = original_call(*args, **kwargs)
                scope.actions.append(original_call.__name__)
                if failure in {"between-cancel", "between-oplog-fsync"} and len(scope.actions) == 1:
                    scope.midstop = True
                    if failure == "between-cancel":
                        ledger.stop("cancel-between-tools")
                        context.cancel_turn(oplog.AGENT_WINDOW)
                    else:
                        with patch("mods.oplog.os.fsync", side_effect=OSError("synthetic oplog fsync failure")):
                            oplog.input_tools(oplog.AGENT_WINDOW, "synthetic attempted durable input")
                return result
            tool.call = checked_tool
        for name in ("read_range", "read_origin", "read_around", "known_windows"):
            original_read = getattr(chatlog, name)
            def checked_read(*args, original_read=original_read, **kwargs):
                safe_tree()
                if kwargs.get("root") is not None:
                    raise RuntimeError("external archive roots forbidden")
                return original_read(*args, **kwargs)
            bind(chatlog, name, checked_read)
        scope.schema = {name: tool.description for name, tool in scope.registry.get("meta").tools.items()}
        scope.base = view._base_prompt()
        scope.client = fake_client(ledger, scope, failure=failure)
        bind(llm, "client", scope.client)

        def say(body, destination):
            ledger.guard()
            if destination not in {tuple(item["window"]) for item in scope.opened}:
                raise RuntimeError("say destination has not opened")
            number = -len(scope.echoes) - 1
            event = event_for({"id": number, "stamp": clock.now, "window": destination, "name": "Synthetic Bot"}, body=body, sender=9)
            event["post_type"] = "message_sent"
            if failure == "echo-write-none":
                with patch.object(chatlog, "write", return_value=None):
                    chatlog.write(event)
            else:
                chatlog.write(event)
            origin = chatlog.consume_origin(event)
            if failure == "echo-read-corrupt":
                with patch.object(chatlog, "read_origin", return_value={"message": "corrupt"}):
                    verify_written(scope, destination, event, origin)
            else:
                verify_written(scope, destination, event, origin)
            event["_log_origin"] = origin
            arrival = oplog.arrive(destination, event, origin=origin)
            scope.echoes.append({"event": event, "origin": origin, "arrival": arrival})
            if oplog.has_unnotified():
                raise AssertionError("candidate echo created external wake")
            return number
        scope.say = say
        yield scope
    finally:
        for module, name, value in reversed(old):
            setattr(module, name, value)
        os.chdir(old_cwd)
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()


def event_for(item, body=None, sender=2):
    kind, target = item["window"]
    return {"post_type": "message", "message_type": kind, "message_id": item["id"],
            "message": body or f"fictional event {item['id']}", "raw_message": body or f"fictional event {item['id']}",
            "user_id": sender, "sender": {"user_id": sender, "nickname": item["name"]},
            "time": item["stamp"], "group_id" if kind == "group" else "target_id": target}


def calls(items):
    return [{"id": f"fake-{index}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(arguments)}}
            for index, (name, arguments) in enumerate(items)]


def fake_client(ledger, scope, *, failure=None):
    from mods import llm, oplog

    class Scripted(llm.LLMClient):
        def __init__(self):
            # No super(): it constructs SDK clients, which this entry never needs.
            self.config = {"default_model": MODEL}
            self.strict_tools = True
            self.attempts = 0
            self.round = 0
            self.requests = []

        def generate_response(self, messages, tools=None, tool_choice=None, model=None, *args):
            if model != MODEL:
                raise RuntimeError("synthetic model changed")
            request = {"model": model, "messages": messages,
                       "tools": [tool.description for tool in tools or []],
                       "max_tokens": LIMITS["output"], "retry": 0}
            scope.safe_tree()
            rendered = canonical(request).decode()
            for item in FIXTURE[len(scope.opened):]:
                assert f"fictional event {item['id']}" not in rendered
                if item["name"] not in {opened["name"] for opened in scope.opened}:
                    assert item["name"] not in rendered
            row = ledger.reserve(request, LIMITS["output"])
            request_root = ledger.root / "requests"
            request_root.mkdir(exist_ok=True)
            durable(request_root / f"{len(ledger.state['requests']):04d}.json", request)
            self.attempts += 1
            self.round += 1
            self.requests.append(request)
            failure_kind, failure_at = (failure if isinstance(failure, tuple) else (failure, 1))
            outcome = failure_kind if failure_kind and not failure_kind.startswith(("between-", "echo-")) and self.attempts == failure_at else "stop"
            usage = None if outcome in {"transport", "stream-disconnect", "missing-usage"} else [100, 40, 20]
            if outcome == "overreport":
                usage = [row["input_bound"] + 1, 0, row["allowance"] + 1]
            if outcome == "cancel":
                from mods import context
                context.cancel_turn(oplog.AGENT_WINDOW)
            # Settle BEFORE yielding calls: usage/transport/persistence failure can
            # never dispatch even the first action, much less the second action.
            if outcome == "fsync":
                with patch("a0_ledger.os.fsync", side_effect=OSError("synthetic fsync failure")):
                    ledger.settle(row, usage)
            else:
                ledger.settle(row, usage, outcome)
            if outcome != "stop":
                raise AssertionError("failure escaped closed ledger")
            source = scope.source
            phase = (self.round - 1) % 4
            inputs = [entry["id"] for entry in oplog.events(oplog.AGENT_WINDOW) if entry["kind"] == "input"]
            if phase == 0:
                via = ("take", "mentions", "pull")[(self.round - 1) // 4]
                arguments = {"source": source}
                if via != "mentions":
                    arguments["count"] = 500
                actions = [("status", {}), (via, arguments), ("edit_hint", {"text": "synthetic durable hint"}),
                           ("load_tools", {"names": ["synthetic"]})]
            elif phase == 1:
                actions = [("recall_events", {"ids": inputs}), ("event_links", {"ids": inputs}),
                           ("cover_events", {"ids": inputs[:1], "conclusion": "synthetic grounded conclusion"})]
            elif phase == 2:
                actions = [("say", {"text": "fictional candidate echo", "target": source, "final_call": False}),
                           ("read_messages", {"window": source, "message_id": str(-len(scope.echoes) - 1), "before": 1, "after": 20}),
                           ("list_tools", {}), ("reload_tools", {"names": ["synthetic"]})]
            else:
                actions = [("say", {"text": "fictional completed wake", "target": source, "final_call": True})]
            if failure_kind and failure_kind.startswith("between-"):
                actions = [("edit_hint", {"text": "first tool completed"}),
                           ("edit_hint", {"text": "SECOND TOOL MUST NOT RUN"})]
            if failure_kind and failure_kind.startswith("echo-"):
                actions = [("say", {"text": "must verify before arrival", "target": source, "final_call": False}),
                           ("edit_hint", {"text": "SECOND TOOL MUST NOT RUN"})]
            for call in calls(actions):
                yield llm.LLMResponse(json.dumps(call), "tool")
            return llm.LLMResponse("", "assistant")
    return Scripted()


def open_prefix(scope, end):
    from mods import chatlog, oplog
    for item in FIXTURE[len(scope.opened):end]:
        scope.clock.now = item["stamp"]
        scope.names[2] = item["name"]
        event = event_for(item)
        chatlog.write(event)
        origin = chatlog.consume_origin(event)
        scope.opened.append({**item, "origin": origin, "body": event["message"]})
        verify_written(scope, tuple(item["window"]), event, origin)
        event["_log_origin"] = origin
        arrival = oplog.arrive(tuple(item["window"]), event, origin=origin)
        if item["wake"]:
            oplog.activate(arrival, "mention")


def verify_written(scope, window, event, origin):
    from mods import chatlog
    if origin is None:
        scope.fail("chatlog write has no origin; pilot closed")
    record = chatlog.read_origin(*window, origin)
    if (record.get("message") != event["message"]
            or str(record.get("message_id")) != str(event["message_id"])
            or record.get("time") != event["time"]
            or record.get("user_id") != event["user_id"]):
        scope.fail("chatlog readback differs from written message/identity; pilot closed")


def check_past(scope):
    from mods import chat, chatlog, identity, oplog
    allowed = {item["id"] for item in scope.opened} | {item["event"]["message_id"] for item in scope.echoes}
    windows = {tuple(item["window"]) for item in scope.opened}
    assert set(chatlog.known_windows()) == windows
    assert scope.clock.time() == scope.opened[-1]["stamp"]
    assert all(entry["id"].split("-")[0] <= scope.clock.strftime("%Y%m%d")
               for entry in oplog.events(oplog.AGENT_WINDOW))
    assert all(stamp <= scope.clock.now for stamp in chat._active_modules().values())
    assert identity.get_user_name(2) == scope.opened[-1]["name"]
    for window in windows:
        records = chatlog.read_range(*window)
        assert {int(item["message_id"]) for item in records} <= allowed
        for record in records:
            origin = record["_log_origin"]
            assert chatlog.read_origin(*window, origin)["message"] == record["message"]
            around = chatlog.read_around(*window, origin=origin, before=100, after=100)
            assert {int(item["message_id"]) for item in around} <= allowed
            around = chatlog.read_around(*window, message_id=record["message_id"], before=100, after=100)
            assert {int(item["message_id"]) for item in around} <= allowed
        assert not chatlog.read_around(*window, message_id=106, before=100, after=100)
    for item in scope.opened:
        assert chatlog.read_origin(*item["window"], item["origin"])["message"] == item["body"]
    for item in scope.echoes:
        event = item["event"]
        window = (event["message_type"], event.get("group_id", event.get("target_id")))
        assert chatlog.read_origin(*window, item["origin"])["message"] == event["message"]
    assert all(entry["event"].get("time", 0) <= scope.clock.now
               for window in windows for entry in oplog.unread(window))
    scope.probes.append({"prefix": len(scope.opened), "paths": ["range", "around-id", "around-origin", "origin", "directory", "names", "clock", "unread"], "ok": True})


def manifest(scope):
    return {"schema": "a0-synthetic-v1", "pilot": PILOT, "fixture": FIXTURE,
            "schedule": SCHEDULE, "skill_sha256": digest(SKILL), "code": code_hashes(),
            "base_sha256": digest(scope.base), "schema_sha256": digest(scope.schema),
            "model": MODEL, "limits": LIMITS, "prices": PRICES,
            "clock": "frozen at each prefix; no advance within drive", "timezone": "Asia/Shanghai",
            "input_bound": "complete canonical UTF-8 request bytes + 1024; synthetic interface only",
            "restore": "completed verification only; no partial continuation/fork"}


def run(root: Path, *, failure=None):
    from mods import chat, context, oplog, storage
    root = external_root(root)
    ledger = Ledger(root, PILOT, "initializing")
    result = None
    scope = None
    try:
        with isolated(root, ledger, failure=failure) as scope:
            frozen = manifest(scope)
            durable(root / "manifest.json", frozen)
            ledger.state["manifest"] = digest(frozen)
            ledger.save()
            for end in SCHEDULE:
                ledger.wake()
                open_prefix(scope, end)
                check_past(scope)
                window = tuple(scope.opened[-1]["window"])
                scope.source = ("g" if window[0] == "group" else "u") + str(window[1])
                chat.run_offline(MODEL, window, fact=FACT, registry=scope.registry,
                                 on_chunk=lambda chunk: None, persist_reasoning=False,
                                 send_context=scope.send_context, send_sink=scope.say)
                if scope.midstop:
                    raise RuntimeError("cancelled between tools; no continuation")
                assert context.current() is None
                assert not oplog.has_unnotified()
                check_past(scope)
            storage.save()
            replay._assert_storage_saved(storage)
            rows = replay._journal_rows(root)
            replay._assert_settled(rows)
            assert scope.client.attempts == 12 and len(scope.echoes) == 6
            assert ledger.state["wakes"] == 3
            assert all(scope.counters[key] == 0 for key in scope.counters)
            assert storage.get("", "agent")["agent_hint"] == "synthetic durable hint"
            assert "synthetic" in chat._active_modules()
            result = {"status": "complete", "external_wakes": 3, "fake_attempts": scope.client.attempts,
                      "echoes": len(scope.echoes), "opened_events": len(scope.opened),
                      "checks": scope.probes, "production_calls": scope.counters,
                      "journal_rows": len(rows), "resume": "completed verification only"}
        durable(root / "result.json", result)
        durable(root / "checkpoint.json", {"schema": "a0-complete-v1", "manifest": digest(frozen),
                                             "files": replay._file_hashes(root)})
        return result
    except BaseException as error:
        try:
            if not ledger.poisoned:
                ledger.stop(type(error).__name__ + ": " + str(error))
            durable(root / "stop.json", {"status": "stopped", "reason": str(error),
                                        "fake_attempts": scope.client.attempts if scope is not None else 0,
                                        "resume": "unsupported"})
        except OSError:
            pass
        raise


def verify_completed(root):
    root = external_root(root)
    if not root.is_dir():
        raise ValueError("resume requires a synthetic root outside repository")
    with replay._locked(root):
        if ((root / "stop.json").exists() or (root / "persistence-unknown.json").exists()
                or not (root / "checkpoint.json").is_file()):
            raise ValueError("partial/unknown runtime restoration unsupported; no complete A0 checkpoint")
        checkpoint = json.loads((root / "checkpoint.json").read_text())
        if checkpoint.get("schema") != "a0-complete-v1" or checkpoint["files"] != replay._file_hashes(root):
            raise ValueError("unsupported or corrupt A0 checkpoint")
        frozen = json.loads((root / "manifest.json").read_text())
        if (frozen["schema"] != "a0-synthetic-v1" or frozen["code"] != code_hashes()
                or frozen["fixture"] != FIXTURE or frozen["schedule"] != SCHEDULE
                or frozen["timezone"] != "Asia/Shanghai"
                or frozen["model"] != MODEL or frozen["prices"] != PRICES or frozen["limits"] != LIMITS
                or checkpoint["manifest"] != digest(frozen)):
            raise ValueError("frozen manifest drift")
        ledger = Ledger(root, PILOT, digest(frozen), reopen=True)
        if ledger.state["wakes"] != 3 or len(ledger.state["requests"]) != 12:
            raise ValueError("partial runtime restoration unsupported")
        result = json.loads((root / "result.json").read_text())
        if result["status"] != "complete":
            raise ValueError("stopped runtime restoration unsupported")
        return {"status": "verified-completed", "fake_attempts": 0,
                "cumulative_attempts": len(ledger.state["requests"]), "continuation": "unsupported"}


def binding_snapshot():
    from mods import chat, chatlog, connect, context, history, identity, llm, message, oplog, storage, tools
    from mods.chat import view
    groups = [(oplog, replay._OPLOG_BINDINGS), (context, replay._CONTEXT_BINDINGS),
              (chatlog, replay._CHATLOG_BINDINGS), (storage, replay._STORAGE_BINDINGS),
              (history, ("msgs",)), (llm, ("client",)), (storage, ("root_path",)),
              (chatlog, ("rootfile", "read_range", "read_origin", "read_around", "known_windows")), (connect, ("call_api",)),
              (identity, ("qq", "name", "nicknames", "getname", "get_user_name", "get_group_user_info")),
              (chat, ("settings", "prompts", "chat_groups", "llm_config", "description_cache", "time", "datetime", "_run_hint")),
              (message, ("send", "sendmsg", "_send_now")), (oplog, ("time", "datetime")),
              (tools, ("time",)), (view, ("time",)), (socket, ("create_connection",)),
              (socket.socket, ("connect",))]
    return [getattr(module, name) for module, names in groups for name in names]


def doctor(root):
    from mods import context
    reports = []
    def passed(name, **details):
        reports.append({"assertion": name, "status": "pass", **details})
    def rejects(name, action):
        try:
            action()
        except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as error:
            passed(name, observed=type(error).__name__ + ": " + str(error))
        else:
            raise AssertionError(name + " was accepted")
    def new_ledger(name, **kwargs):
        child = root / name
        child.mkdir()
        return Ledger(child, PILOT, "synthetic-manifest", **kwargs)
    caller = {"synthetic": "caller-current-event"}
    prior = context.current()
    prior_latest = context.latest()
    prior_had_current = hasattr(context._local, "event")
    context.set_current(caller)
    original = binding_snapshot()
    try:
        sandbox = root / "path-probes"
        sandbox.mkdir()
        path_ledger = Ledger(sandbox, PILOT, "synthetic-path-probes")
        with isolated(sandbox, path_ledger) as scope:
            from mods import chatlog
            open_prefix(scope, 1)
            origin = scope.opened[0]["origin"]
            rejects("absolute-origin-refused", lambda: chatlog.read_origin("group", 1, "/synthetic/outside"))
            rejects("parent-origin-refused", lambda: chatlog.read_around("group", 1, origin="../outside", after=1))
            rejects("foreign-window-origin-refused", lambda: chatlog.read_origin("group", 8, origin))
            rejects("external-root-argument-refused", lambda: chatlog.read_range("group", 1, root="/synthetic/outside"))
            link = sandbox / "archive" / "group" / "8"
            link.symlink_to(sandbox / "archive" / "group" / "1", target_is_directory=True)
            rejects("window-symlink-before-around-id-read", lambda: chatlog.read_around("group", 8, message_id=101))
            link.unlink()
            # SQLite's exact filename is implementation-owned: any link in the
            # private tree is refused before opening a reader/index.
            link = sandbox / "archive" / ".synthetic-index-link"
            link.symlink_to(sandbox / "ledger.json")
            rejects("index-symlink-before-origin-read", lambda: chatlog.read_origin("group", 1, origin))
            link.unlink()
        assert all(a is b for a, b in zip(original, binding_snapshot()))
        success = root / "success"
        success.mkdir()
        result = run(success)
        assert context.current() is caller
        assert all(a is b for a, b in zip(original, binding_snapshot()))
        passed("three-wakes/legal-past/all-read-paths/stable-origin/echo-no-wake/reader-tool-state/finally-restore",
               probes=result["checks"], fake_attempts=12, external_wakes=3, echoes=6,
               production_calls=result["production_calls"])
        # Full checkpoint verification is read-only and cannot reset/spend budget.
        verified = verify_completed(success)
        assert verified["cumulative_attempts"] == 12 and verified["fake_attempts"] == 0
        passed("completed-checkpoint-verification-keeps-cumulative-ledger")
        ledger = Ledger(success, PILOT, digest(json.loads((success / "manifest.json").read_text())), reopen=True)
        rejects("thirteenth-attempt-before-transport", lambda: ledger.reserve({}, 4096))
        rejects("fourth-external-wake", ledger.wake)
        rejects("4097-output-before-transport", lambda: new_ledger("output").reserve({}, 4097))
        rejects("explicit-context-before-transport", lambda: new_ledger("context", limits={**LIMITS, "context": 1}).reserve({}, 1))
        rejects("cumulative-allowances-before-transport", lambda: new_ledger("allowances", limits={**LIMITS, "allowances": 1}).reserve({}, 2))
        assert cost(PRICES, 100, 40, 20) == 110
        assert cost({**PRICES, "input": "0.0000001"}, 1, 0, 0) == 1
        passed("cached-tokens-count-once-and-cost-rounded-up", charged_micro_cny=110)
        request = {"full": "request"}
        bound = len(canonical(request)) + 1024
        edge_prices = {**PRICES, "input": "0", "cached": "0", "output": "5000000"}
        exact = cost(edge_prices, bound, 0, 1, reserve=True)
        assert exact == 5000000
        edge = new_ledger("exact", prices=edge_prices)
        row = edge.reserve(request, 1)
        edge.settle(row, [bound, 0, 1])
        assert row["charged"] == exact
        passed("exact-fee-boundary-accepted", micro_cny=exact)
        rejects("one-micro-over-fee-boundary-before-transport", lambda: new_ledger("over-fee", prices={**edge_prices, "output": "5000001"}).reserve(request, 1))
        rejects("missing-price-before-transport", lambda: new_ledger("missing-price", prices={key: value for key, value in PRICES.items() if key != "cached"}))
        rejects("unknown-extra-fee-before-transport", lambda: new_ledger("extra-fee", prices={**PRICES, "unknown_fee": 1}))
        missing = new_ledger("missing-usage")
        row = missing.reserve({}, 4096)
        rejects("missing-usage-closes-pilot", lambda: missing.settle(row, None))
        assert row["charged"] == row["reserved"] and row["usage_source"] == "full-reserve"
        rejects("missing-usage-reopen-keeps-charge", lambda: Ledger(missing.root, PILOT, "synthetic-manifest", reopen=True))
        over = new_ledger("overreport")
        row = over.reserve({}, 4096)
        rejects("usage-overreport-stored-before-close", lambda: over.settle(row, [row["input_bound"] + 1, 0, 4097]))
        assert row["charged"] > row["reserved"]
        disk = json.loads((over.root / "ledger.json").read_text())["state"]
        assert disk["requests"][-1]["charged"] == row["charged"] and disk["closed"] == "usage-overreport"
        rejects("overreport-no-followup", lambda: over.reserve({}, 1))
        inflight = new_ledger("inflight")
        inflight.reserve({}, 1)
        rejects("single-inflight", lambda: inflight.reserve({}, 1))
        rejects("unknown-inflight-reopen", lambda: Ledger(inflight.root, PILOT, "synthetic-manifest", reopen=True))
        rejects("price-drift", lambda: Ledger(edge.root, PILOT, "synthetic-manifest", limits=edge.limits, prices={**PRICES, "output": "3"}, reopen=True))
        rejects("manifest-identity-drift", lambda: Ledger(edge.root, PILOT, "different-manifest", limits=edge.limits, prices=edge.prices, reopen=True))
        path = edge.root / "ledger.json"
        raw = path.read_bytes()
        path.write_bytes(b"corrupt")
        rejects("corrupt-ledger", lambda: Ledger(edge.root, PILOT, "synthetic-manifest", limits=edge.limits, prices=edge.prices, reopen=True))
        path.write_bytes(raw)
        with replay._locked(edge.root):
            rejects("concurrent-root-lock", lambda: enter_lock(edge.root))
        frozen_path = success / "manifest.json"
        raw_manifest = frozen_path.read_bytes()
        frozen = json.loads(raw_manifest)
        frozen["model"] = "unknown-model"
        frozen_path.write_text(json.dumps(frozen))
        rejects("manifest-drift-checkpoint-refused", lambda: verify_completed(success))
        frozen_path.write_bytes(raw_manifest)
        rejects("old-checkpoint-format-refused", lambda: verify_completed(edge.root))
        for outcome in ("transport", "stream-disconnect", "length", "cancel", "overreport", "missing-usage", "fsync", "unknown-result"):
            child = root / ("failure-" + outcome)
            child.mkdir()
            rejects(outcome + "-stops-before-first-or-second-tool", lambda: run(child, failure=outcome))
            rows = replay._journal_rows(child)
            assert not any(row["kind"] in ("output", "result") for row in rows)
            state = json.loads((child / "ledger.json").read_text())["state"]
            assert len(state["requests"]) == 1
            assert (child / "stop.json").is_file()
            if outcome == "fsync":
                assert state["requests"][0]["status"] == "reserved"
                assert (child / "persistence-unknown.json").is_file()
            else:
                assert state["requests"][0]["status"] == "settled" and state["closed"]
            rejects(outcome + "-partial-resume-explicitly-unsupported", lambda: verify_completed(child))
            assert all(a is b for a, b in zip(original, binding_snapshot()))
            assert context.current() is caller
        for outcome in ("echo-write-none", "echo-read-corrupt"):
            child = root / outcome
            child.mkdir()
            rejects(outcome + "-blocks-arrival-and-later-tool", lambda: run(child, failure=outcome))
            rows = replay._journal_rows(child)
            assert not any(row.get("event", {}).get("post_type") == "message_sent" for row in rows)
            hint = json.loads((child / "data/storage/agent.json").read_text())
            assert hint.get("agent_hint") != "SECOND TOOL MUST NOT RUN"
            assert json.loads((child / "ledger.json").read_text())["state"]["closed"]
            assert (child / "stop.json").is_file()
        prefix_root = root / "prefix-write-none"
        prefix_root.mkdir()
        prefix_ledger = Ledger(prefix_root, PILOT, "synthetic-prefix-failure")
        with isolated(prefix_root, prefix_ledger) as scope:
            from mods import chatlog, oplog
            with patch.object(chatlog, "write", return_value=None):
                rejects("prefix-write-none-before-arrival", lambda: open_prefix(scope, 1))
            assert not oplog.unread(("group", 1)) and prefix_ledger.state["closed"]
        # These failures occur after the real first tool has durably saved its
        # side effect, inside the production double-tool dispatch loop.
        for outcome in ("between-cancel", "between-oplog-fsync"):
            child = root / outcome
            child.mkdir()
            rejects(outcome + "-blocks-second-tool-after-first-completed", lambda: run(child, failure=outcome))
            hint = json.loads((child / "data/storage/agent.json").read_text())
            assert hint["agent_hint"] == "first tool completed"
            rows = replay._journal_rows(child)
            outputs = [row for row in rows if row["kind"] == "output"]
            assert len(outputs) == 1 and len(outputs[0]["assistant"]["tool_calls"]) == 2
            assert (child / "stop.json").is_file() and not (child / "checkpoint.json").exists()
            rejects(outcome + "-partial-resume-unsupported", lambda: verify_completed(child))
            assert all(a is b for a, b in zip(original, binding_snapshot()))
        directory = new_ledger("directory-fsync")
        row = directory.reserve({}, 1)
        real_fsync = os.fsync
        def fail_directory(fd):
            import stat
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("synthetic directory fsync failure after rename")
            return real_fsync(fd)
        with patch("a0_ledger.os.fsync", side_effect=fail_directory):
            rejects("directory-fsync-after-rename-closed", lambda: directory.settle(row, [100, 40, 1]))
        assert json.loads((directory.root / "ledger.json").read_text())["state"]["requests"][0]["status"] == "settled"
        rejects("directory-fsync-unknown-reopen-refused", lambda: Ledger(directory.root, PILOT, "synthetic-manifest", reopen=True))
        child = root / "failure-at-12"
        child.mkdir()
        rejects("twelfth-transport-failure-no-retry-or-final-request", lambda: run(child, failure=("transport", 12)))
        state = json.loads((child / "ledger.json").read_text())["state"]
        assert len(state["requests"]) == 12 and state["requests"][-1]["usage"] is None
        assert state["requests"][-1]["charged"] == state["requests"][-1]["reserved"]
        passed("twelfth-failure-durable-full-reserve", fake_attempts=12)
        assert all(a is b for a, b in zip(original, binding_snapshot()))
        assert context.current() is caller
    finally:
        context.set_current(prior)
        if not prior_had_current:
            context.clear_current()
        context._latest = prior_latest
    result = {"status": "ok", "mode": "synthetic-only", "assertions": reports,
              "assertion_count": len(reports), "real_model_calls": 0,
              "network_calls": 0, "QQ_calls": 0,
              "limitation": "only completed verification; partial runtime restoration/fork unsupported; no production SDK retry claim"}
    durable(root / "doctor.json", result)
    return result


def enter_lock(root):
    with replay._locked(root):
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("doctor", "run", "resume"), default="doctor")
    parser.add_argument("--output", help="fresh external synthetic output root; resume uses an existing root")
    args = parser.parse_args(argv)
    try:
        if args.command == "resume":
            if not args.output:
                raise ValueError("resume requires --output")
            output = external_root(args.output)
            result = verify_completed(output)
        else:
            if args.output:
                output = replay._output_path(str(external_root(args.output)))
            else:
                temporary_root = external_root(tempfile.gettempdir())
                output = Path(tempfile.mkdtemp(prefix="bot-a0-calibration-", dir=temporary_root))
            output.mkdir(mode=0o700, parents=True, exist_ok=True)
            with replay._locked(output):
                result = doctor(output) if args.command == "doctor" else run(output)
        print(json.dumps({**result, "output": str(output)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, AssertionError, KeyError) as error:
        print(f"A0 calibration: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
