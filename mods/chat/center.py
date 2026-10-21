"""Explicit isolated assembly of the production reader's next journal format.

No live configuration selects this path.  A caller must supply a temporary root,
an archive identity, a client and a send sink, before routing any events.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from mods import context, history, llm


_active = None
_assembly_lock = threading.RLock()
_request_state = ContextVar("center_request_state", default=None)


def active():
    return _active


def require_assembly(runtime, identity):
    """Caller holds _assembly_lock through the protected mutation/turn claim."""
    if _active is not runtime or identity is None or runtime._assembly is not identity:
        raise RuntimeError("center assembly was detached or replaced")


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


@contextmanager
def isolated(runtime):
    """Select one authority for this process; never switch while an owner runs."""
    global _active
    with _assembly_lock:
        if _active is not None or context.get_turn(("agent", 0)) is not None:
            raise RuntimeError("a center reader is already installed or running")
        runtime._assembly = object()
        _active = runtime
    try:
        runtime.ensure_available()
        if runtime.scheduler is not None:
            from . import revisit
            revisit.project(runtime, runtime.scheduler)
        yield runtime
    finally:
        with _assembly_lock:
            if context.get_turn(("agent", 0)) is not None:
                # Retain the assembly on misuse: an in-flight call must not fall
                # back into the legacy writer when its owner finally returns.
                raise RuntimeError("cannot detach a running center")
            _active = None
            runtime._assembly = None


class Center:
    def __init__(self, *, root, archive_root, source_namespace, source_format,
                 bot_id, bot_names, names_complete, timezone, client, send_sink,
                 request_limit, registry=None, scheduler=None):
        self.root = Path(root).resolve()
        self.archive_root = Path(archive_root).resolve()
        self.scope_id = source_namespace
        if not isinstance(source_namespace, str) or not source_namespace.strip():
            raise ValueError("source namespace is required")
        if source_format != "v1":
            raise ValueError("routed isolated center requires ordinary v1; old/sidecar imports need a reviewed migration")
        if not callable(send_sink) or request_limit < 1:
            raise ValueError("an explicit sink and finite request allowance are required")
        self.archive_options = dict(source_namespace=source_namespace, source_format=source_format,
                                    bot_id=bot_id, bot_names=deepcopy(bot_names),
                                    names_complete=names_complete, timezone=timezone)
        self.timezone = ZoneInfo(timezone)
        self.client, self.send_sink = client, send_sink
        self.registry, self.scheduler = registry, scheduler
        self.request_limit = int(request_limit)
        self.lock = threading.RLock()
        self._assembly = None
        self.events = []
        self.failed = None
        self.path = self.root / "center.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            data = self.path.read_bytes() if self.path.exists() else b""
            if data and not data.endswith(b"\n"):
                raise ValueError("incomplete center journal")
            for line in data.splitlines():
                entry = json.loads(line)
                digest = entry.pop("sha256")
                if (entry.get("schema") != "center-v1" or entry.get("scope") != self.scope_id
                        or entry.get("id") != f"C{len(self.events) + 1}"
                        or entry.get("previous") != (self.events[-1]["sha256"] if self.events else None)
                        or digest != _hash(entry)):
                    raise ValueError("center journal identity/hash mismatch")
                entry["sha256"] = digest
                self.events.append(entry)
            self._validate_restore()
        except Exception as error:
            self.failed = error

    def _validate_restore(self):
        head = None
        outputs = set()
        dispatches = set()
        results = set()
        requests = set()
        responses = set()
        hints = set()
        hint_results = set()
        for event in self.events:
            kind = event["kind"]
            if kind == "note":
                if event["parent"] != head or event["version"] != _hash(event["note"]):
                    raise ValueError("note lineage mismatch")
                head = event["id"]
            elif kind == "output":
                outputs.add(event["id"])
                responses.add(event["request"])
            elif kind == "request":
                requests.add(event["id"])
            elif kind == "request_end" and event["outcome"] == "cancelled":
                responses.add(event["request"])
            elif kind == "hint_dispatch":
                hints.add(event["id"])
            elif kind == "hint_result":
                if event["status"] == "unknown":
                    raise ValueError("unconfirmed hint dispatch")
                hint_results.add(event["dispatch"])
            elif kind == "dispatch":
                if event["output"] not in outputs:
                    raise ValueError("dispatch without committed output")
                dispatches.add(event["action"])
            elif kind == "results":
                results.update(event["actions"])
            elif kind in {"unknown", "fault"}:
                raise ValueError("center has unresolved unknown/fault")
        if dispatches - results:
            raise ValueError("dispatched action has no durable result; reconciliation required")
        if requests - responses or hints - hint_results:
            raise ValueError("request or hint has an unknown outcome; reconciliation required")

    def ensure_available(self):
        if self.failed is not None:
            raise llm.PersistenceError("center journal is closed; maintenance/reconciliation required") from self.failed

    def append(self, kind, **values):
        with self.lock:
            self.ensure_available()
            entry = dict(schema="center-v1", scope=self.scope_id, id=f"C{len(self.events)+1}",
                         previous=self.events[-1]["sha256"] if self.events else None,
                         kind=kind, **deepcopy(values))
            entry["sha256"] = _hash(entry)
            try:
                created = not self.path.exists()
                with self.path.open("ab") as stream:
                    stream.write(_bytes(entry) + b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                if created:
                    descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                self.events.append(entry)
            except BaseException as error:
                self.failed = error
                if not isinstance(error, Exception):
                    raise
                raise llm.PersistenceError("center append uncertain; no further dispatch") from error
            return deepcopy(entry)

    def fault(self, error):
        with self.lock:
            if self.failed is None:
                try:
                    self.append("fault", error=type(error).__name__)
                finally:
                    self.failed = error

    def record_event(self, event, write):
        from mods import chat, chatlog, msgs

        window = history.window(event)
        if window is None and msgs.is_friend_recall(event) and event.get("user_id") is not None:
            window = ("private", event["user_id"])
        if window is None:
            return write()
        with context.window_lock(window):
            # Ordinary archive retains its existing flush guarantee.  The center
            # only publishes after that write succeeds; this is not a two-file transaction.
            try:
                written = write()
            except Exception as error:
                self.fault(error)
                raise
            context.remember_arrival(event, None)
            if written is None and chat._mail_candidate(event):
                self.fault(ValueError("archive did not commit a candidate event"))
            if written is None or not chat._mail_candidate(event):
                return written
            try:
                self.ensure_available()
                origin = chatlog.consume_origin(event)
                if not origin:
                    raise ValueError("archive writer did not return an origin")
                day = datetime.fromtimestamp(event["time"], self.timezone).date().isoformat()
                eligible = not (msgs.is_msg(event) and msgs.body(event).startswith("#"))
                arrival = self.append("arrival", window=list(window), origin=origin, day=day,
                                      raw_sha256=hashlib.sha256(written.encode("utf-8")).hexdigest(),
                                      eligible=eligible, echo=event.get("post_type") == "message_sent")
                context.remember_arrival(event, arrival["id"])
            except Exception as error:
                # Recording failures close agent mail while explicit maintenance
                # commands retain the router's existing priority and availability.
                self.fault(error)
            return written

    def activate(self, arrival, kind):
        with self.lock:
            self.ensure_available()
            source = next((e for e in self.events if e["id"] == arrival and e["kind"] == "arrival"), None)
            if source is None:
                raise ValueError("activation requires a registered arrival")
            if any(e["kind"] == "activation" and e["arrival"] == arrival for e in self.events):
                return
            self.append("activation", arrival=arrival, window=source["window"], activation_kind=kind)

    def pending(self):
        acknowledged = {key for e in self.events if e["kind"] in {"output", "disposition"}
                        for key in e.get("activations", [])}
        return [deepcopy(e) for e in self.events if e["kind"] in {"activation", "revisit_delivery", "resume"}
                and e["id"] not in acknowledged]

    def activate_delivery(self, delivery, window, reason):
        return self.append("revisit_delivery", request=delivery["request"], delivery=delivery,
                           window=list(window), reason=reason)

    def wake(self, window, *, assembly=None):
        from mods import chat

        previous = context.current()
        event = {"group_id": window[1]} if window[0] == "group" else {"target_id": window[1]}
        context.set_current(event)
        try:
            chat.chat(_assembly=(self, self._assembly if assembly is None else assembly))
        finally:
            context.set_current(previous)

    def resume(self, event, *, self_restart):
        if not self_restart:
            return
        window = history.window(event)
        if window is None:
            raise ValueError("resume has no origin window")
        self.append("resume", window=list(window), reason="bot self restart")
        self.wake(window)

    def note_head(self):
        return next((deepcopy(e) for e in reversed(self.events) if e["kind"] == "note"), None)

    def send_hint(self, window, body):
        self.ensure_available()
        dispatch = self.append("hint_dispatch", window=list(window), body=body)
        try:
            message_id = self.send_sink(body, window)
        except Exception as error:
            self.append("unknown", dispatch=dispatch["id"], reason=type(error).__name__)
            self.fault(error)
            raise
        self.append("hint_result", dispatch=dispatch["id"], message_id=message_id,
                    status="complete" if message_id is not None else "unknown")
        if message_id is None:
            self.fault(ValueError("unconfirmed hint dispatch"))
            self.ensure_available()

    def commit_note(self, *, parent, text, sources, request):
        """One CAS write entry, shared by the foreground and optional callers."""
        with self.lock:
            self.ensure_available()
            seen = next((e for e in self.events if e["kind"] == "request" and e["id"] == request), None)
            if seen is None:
                raise ValueError("note needs an actually exposed request cutoff")
            head = self.note_head()
            if parent != (head["id"] if head else None):
                raise ValueError("stale note parent; read the current head")
            if parent != seen["note"]:
                raise ValueError("candidate did not see this note parent in its actual request")
            if not isinstance(text, str) or not isinstance(sources, list):
                raise ValueError("note text and explicit source list are required")
            if set(sources) - set(seen["sources"]):
                raise ValueError("note source was not seen at this request cutoff")
            note = dict(text=text, sources=list(dict.fromkeys(sources)), cutoff=seen["cutoff"], request=request)
            return self.append("note", parent=parent, version=_hash(note), note=note)

    def observe_request(self, selection, params):
        state = _request_state.get()
        with self.lock:
            self.ensure_available()
            if state is None or state["center"] is not self:
                raise llm.PersistenceError("isolated center rejects unbound model requests")
            if sum(e["kind"] == "request" for e in self.events) >= self.request_limit:
                raise llm.PersistenceError("isolated request allowance exhausted")
            from mods import chat
            if chat.count_tokens(_bytes(params).decode()) > chat.limit()[1]:
                raise llm.RequiredContextError("isolated SDK payload exceeds the configured context allowance")
            entry = self.append("request", owner=state["owner"], cutoff=state["cutoff"],
                                activations=state["activations"], sources=state["sources"],
                                note=state["note"], selection=selection, payload=params)
            state["request"] = entry["id"]

    def observe_action(self, action):
        if action is None:
            return
        state = _request_state.get()
        self.ensure_available()
        if state is None or state["center"] is not self:
            raise llm.PersistenceError("isolated center rejects unbound actions")
        self.append("dispatch", owner=state["owner"], output=action.partition("#")[0], action=action)

    def _freeze(self, state):
        from mods import chatlog

        with self.lock:
            self.ensure_available()
            frozen = deepcopy(self.events)
            from mods import chat
            if sum(e["kind"] == "arrival" and e["eligible"] for e in frozen) > chat.limit()[0]:
                raise llm.RequiredContextError("isolated archive view exceeds the configured event allowance")
            state["cutoff"] = frozen[-1]["id"] if frozen else None
            state["activations"] = [e["id"] for e in self.pending()]
            state["sources"] = []
            messages = []
            days = {}
            references = []
            for entry in frozen:
                if entry["kind"] == "arrival" and entry["eligible"]:
                    window = entry["window"]
                    day_key = (*window, entry["day"])
                    if day_key not in days:
                        days[day_key] = {item["ref"]["origin"]: item for item in chatlog.freeze_archive_day(
                            root=self.archive_root, kind=window[0], target=window[1], day=entry["day"],
                            **self.archive_options)}
                    item = days[day_key].get(entry["origin"])
                    if item is None or item["ref"]["sha256"] != entry["raw_sha256"]:
                        raise ValueError("archive no longer matches its registered arrival")
                    ref, raw = item["ref"], item["raw"]
                    references.append(dict(arrival=entry["id"], ref=ref))
                    key = ref["key"]
                    state["sources"].append(key)
                    messages.append({"role": "user", "content": [
                        {"type": "text", "text": f"archive {key} {entry['window']}\n"},
                        *chat.msg_split(raw.decode("utf-8"))]})
                elif entry["kind"] in {"output", "results", "revisit_delivery", "resume"}:
                    state["sources"].append(entry["id"])
                    messages.append({"role": "user", "content": "committed center fact " + _bytes(entry).decode()})
            messages.append({"role": "user", "content": "frozen activations " + _bytes(self.pending()).decode()})
            self.append("freeze", owner=state["owner"], cutoff=state["cutoff"], references=references,
                        activations=state["activations"])
            return messages

    def drive(self, model, window, *, assembly):
        from mods import chat, image

        while True:
            with _assembly_lock, self.lock:
                # WHY: a scheduler callback can outlive detach and even a new
                # installation of this same object.  Bind before claiming the
                # turn so detach cannot move it onto another authority.
                require_assembly(self, assembly)
                self.ensure_available()
                turn, owner = context.begin_turn(chat.AGENT_WINDOW)
                if owner:
                    turn.associated_windows = {window}
                    break
                if not turn.cancelled:
                    turn.associated_windows.add(window)
                    return
            # A cancelled owner's frozen disposition cannot consume this new
            # activation.  Join its exit outside both locks, then claim anew.
            context.wait_turn_end(chat.AGENT_WINDOW)
        token = uuid4().hex
        ledger = None
        image_started = False
        origin = context.current()
        outcome = "complete"
        try:
            self.append("owner_start", owner=token, window=list(window))
            ledger = image.begin_conversation()
            image_started = True
            while True:
                context.set_current(None)
                context.set_agent_mode(True)
                state = dict(center=self, owner=token, request=None, note=None)
                marker = _request_state.set(state)
                try:
                    self._run(model, turn, state)
                finally:
                    _request_state.reset(marker)
                    context.set_agent_mode(False)
                    context.set_current(origin)
                with self.lock:
                    if turn.cancelled:
                        outcome = "cancelled"
                        self.append("disposition", owner=token, reason="cancelled",
                                    activations=state.get("activations", []))
                        return
                    if not context.finish_turn(chat.AGENT_WINDOW, turn, lambda: bool(self.pending())):
                        return
        except BaseException as error:
            outcome = "failed"
            self.fault(error)
            raise
        finally:
            try:
                if image_started:
                    image.end_conversation(ledger)
                with self.lock:
                    if self.failed is None:
                        self.append("owner_end", owner=token, outcome=outcome)
            finally:
                context.end_turn(chat.AGENT_WINDOW, turn)
            if origin is not None:
                chat._run_hint(window, turn, _assembly=(self, assembly))

    def _run(self, model, turn, state):
        from mods import chat

        session = llm.Chat(model=model or chat.get_model(), chat_client=self.client)
        session.fail_fast = True
        session.associated_windows = turn.associated_windows
        session.requested_reads = []
        messages = self._freeze(state)
        binding = chat._activate_chat(session, messages)
        # The old reader callbacks are replaced before Chat is invoked.  TTL,
        # schema restore, module loading, image/reasoning and hint stay native.
        session.on_output = lambda assistant, calls: self._output(state, assistant, calls)
        session.on_results = lambda output, results, native: self._results(state, binding, output, results)
        session.should_stop = lambda: turn.cancelled
        session.add_context_provider(lambda: self._notes_provider(state, binding))
        turn._chat_limit = chat.limit()

        def commit_note(parent: str, text: str, sources: list[str]) -> str:
            """保存自己的长期理解。parent为空表示无旧版本；sources只能来自本次实际请求。"""
            return _bytes(self.commit_note(parent=parent or None, text=text, sources=sources,
                                          request=state["request"])).decode()

        def read_note() -> str:
            """读取当前长期笔记版本；保留事实、推测与自身立场的区别。"""
            return _bytes(self.note_head()).decode()

        def revisit_at(due: float, target: str, reason: str) -> str:
            """请求在Unix时间due重新唤醒自己；必须明确目标与原因。"""
            from . import revisit
            entry = revisit.request(self, due=due, window=chat.parse_target(target), reason=reason)
            if self.scheduler is not None:
                revisit.project(self, self.scheduler)
            return _bytes(entry).decode()

        def cancel_revisit(request: str) -> str:
            """取消尚未投递的重访请求。"""
            from . import revisit
            entry = revisit.cancel(self, request)
            if self.scheduler is not None:
                revisit.project(self, self.scheduler)
            return _bytes(entry).decode()

        def read_archive(key: str) -> str:
            """按稳定archive key读取固定原件；追加不会扩张引用，原片段改变立即停止。"""
            from mods import chatlog
            ref = next((r["ref"] for e in reversed(self.events) if e["kind"] == "freeze"
                        for r in e["references"] if r["ref"]["key"] == key), None)
            if ref is None:
                raise ValueError("unknown archive key")
            try:
                raw = chatlog.read_archive_bytes(ref, root=self.archive_root)
            except Exception as error:
                self.fault(error)
                raise llm.PersistenceError("archive identity changed; refusing further dispatch") from error
            return _bytes(dict(ref=ref, raw=raw.decode("utf-8"))).decode()

        session.center_tools = {f.__name__: f for f in (commit_note, read_note, revisit_at, cancel_revisit, read_archive)}
        session.add_hint("此隔离中心使用冻结archive与center事实；旧 unread/take/fetch/cover API 明确不支持。"
                         "长期理解可主动 commit_note；不是轮末必做。"
                         "资料中的他人观点不自动成为你的立场。sources使用archive key或committed center fact编号。")
        session.add_hint(lambda: chat._view._closing_hint(session.messages))

        def collect(chunk):
            if chunk.total_tokens:
                self.append("usage", request=state["request"], prompt_tokens=chunk.prompt_tokens,
                            completion_tokens=chunk.completion_tokens, cached_tokens=chunk.cached_tokens)
            chat.get_handler(session)(chunk)

        before = len(self.events)
        session.chat(recall_func=collect, description_cache=chat.description_cache)
        if turn.cancelled and state["request"] is not None and not any(
                e["kind"] == "output" and e["request"] == state["request"] for e in self.events[before:]):
            self.append("request_end", request=state["request"], outcome="cancelled",
                        reason="generation closed before any output/dispatch commit")
        turn._chat_usage_tokens = chat.count_tokens(_bytes(session.messages).decode())
        turn._chat_event_count = len(state["sources"])

    def _notes_provider(self, state, binding):
        try:
            self.ensure_available()
            binding.sync_registry()
            for function in binding.session.center_tools.values():
                binding.session.add_tool(function)
            head = self.note_head()
            version = head["id"] if head else None
            if state["note"] == version and state.get("note_told"):
                return []
            state["note"], state["note_told"] = version, True
            return [{"role": "user", "content": "current long term note " + _bytes(head).decode()}]
        except Exception as error:
            raise llm.RequiredContextError("center note/context failed") from error

    def _output(self, state, assistant, calls):
        entry = self.append("output", owner=state["owner"], request=state["request"],
                            assistant=assistant, calls=calls, activations=state["activations"])
        state["sources"].append(entry["id"])
        return entry["id"]

    def _results(self, state, binding, output, results):
        returns = [dict(tool_call_id=r.tool_call_id, name=r.name, arguments=r.arguments, content=r.content)
                   for r in results]
        entry = self.append("results", owner=state["owner"], output=output, returns=returns,
                            actions=[f"{output}#{i+1}" for i in range(len(results))])
        state["sources"].append(entry["id"])
        for result in results:
            binding.touch(result.name)
