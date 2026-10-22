"""One installed journal reader, shared by live startup and isolated callers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, datetime
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


def _text_tokens(value):
    from mods import chat

    def text_parts(item):
        if isinstance(item, list):
            return [text_parts(part) for part in item]
        if isinstance(item, dict):
            if item.get("type") == "image_url" and isinstance(item.get("image_url"), dict):
                # WHY: a 256px PNG already exceeds 40000 when its Base64 is
                # tokenized as prose. Image tokens depend on the provider; this
                # guard estimates text only, while the journal keeps the image.
                return {**item, "image_url": {**item["image_url"], "url": "[image]"}}
            return {key: text_parts(part) for key, part in item.items()}
        return item

    return chat.count_tokens(_bytes(text_parts(value)).decode())


@contextmanager
def isolated(runtime):
    """Select one authority for this process; never switch while an owner runs."""
    global _active
    with _assembly_lock:
        if _active is not None or context.get_turn(("agent", 0)) is not None:
            raise RuntimeError("a center reader is already installed or running")
        runtime._assembly = object()
        runtime._start_installation()
        _active = runtime
    try:
        runtime.ensure_available()
        if runtime.scheduler is not None:
            from . import revisit
            revisit.project(runtime, runtime.scheduler)
        yield runtime
    finally:
        with _assembly_lock, runtime.lock:
            if context.get_turn(("agent", 0)) is not None:
                # Retain the assembly on misuse: an in-flight call must not fall
                # back into the legacy writer when its owner finally returns.
                raise RuntimeError("cannot detach a running center")
            runtime._closing = True
        runtime.close()
        with _assembly_lock:
            if context.get_turn(("agent", 0)) is not None:
                raise RuntimeError("cannot detach a running center")
            runtime._remove_client_guard()
            _active = None
            runtime._assembly = None


class Center:
    def __init__(self, *, root, archive_root, source_namespace, source_format,
                 bot_id, bot_names, names_complete, timezone, client, send_sink,
                 request_limit, registry=None, scheduler=None, selector_model=None,
                 librarian_model=None, recent_count=8, role_request_limit=4, wake_request_limit=None):
        self.root = Path(root).resolve()
        self.archive_root = Path(archive_root).resolve()
        self.scope_id = source_namespace
        if not isinstance(source_namespace, str) or not source_namespace.strip():
            raise ValueError("source namespace is required")
        if source_format != "v1":
            raise ValueError("routed isolated center requires ordinary v1; old/sidecar imports need a reviewed migration")
        if not callable(send_sink) or (request_limit is not None and (
                isinstance(request_limit, bool) or not isinstance(request_limit, int) or request_limit < 1)):
            raise ValueError("an explicit sink and positive request allowance or None are required")
        self.archive_options = dict(source_namespace=source_namespace, source_format=source_format,
                                    bot_id=bot_id, bot_names=deepcopy(bot_names),
                                    names_complete=names_complete, timezone=timezone)
        self.timezone = ZoneInfo(timezone)
        self.client, self.send_sink = client, send_sink
        self.registry, self.scheduler = registry, scheduler
        self.request_limit = request_limit
        for number in (recent_count, role_request_limit):
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                raise ValueError("recent_count and request limits must be positive counts")
        if wake_request_limit is not None and (isinstance(wake_request_limit, bool)
                or not isinstance(wake_request_limit, int) or wake_request_limit < 1):
            raise ValueError("wake request limit must be a positive count or None")
        for selection in (selector_model, librarian_model):
            if selection is not None and (not isinstance(selection, str) or not selection.strip()):
                raise ValueError("auxiliary model must be an explicit model selection or None")
        self.selector_model, self.librarian_model = selector_model, librarian_model
        self.recent_count, self.role_request_limit = recent_count, role_request_limit
        self.wake_request_limit = wake_request_limit
        # WHY: lazy vision may generate inside this same thread's main request;
        # other roles still wait for the entire outer stream to finish.
        self._model_lock = threading.RLock()
        self._jobs, self._worker, self._closing = [], None, False
        self.lock = threading.RLock()
        self._jobs_changed = threading.Condition(self.lock)
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
            elif kind == "request_end" and event["outcome"] in {"cancelled", "failed"}:
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
                         kind=kind, recorded_at=datetime.now(UTC).isoformat(), **deepcopy(values))
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
        acknowledged = {key for e in self.events if (e["kind"] == "disposition" or
                        (e["kind"] == "output" and e.get("role", "main") == "main"))
                        for key in e.get("activations", [])}
        return [deepcopy(e) for e in self.events if e["kind"] in {
                    "activation", "revisit_delivery", "resume", "environment_activation"}
                and e["id"] not in acknowledged]

    def post_event(self, source, type, payload, *, window, wake=False, assembly=None):
        """Record a fact and optional pending activation, without starting a model.

        Every callback must pass the installation identity captured at attach.
        """
        if not isinstance(source, str) or not source.strip() or not isinstance(type, str) or not type.strip():
            raise ValueError("event source and type must be nonempty text")
        if not isinstance(window, (tuple, list)) or len(window) != 2 or window[0] not in {"group", "private"}:
            raise ValueError("event requires an explicit chat window")
        _bytes(payload)
        with _assembly_lock, self.lock:
            require_assembly(self, assembly)
            if self._closing:
                raise RuntimeError("center is closing")
            event = self.append("environment", source=source, type=type, payload=payload,
                                window=list(window), time=time.time())
            if wake:
                self.append("environment_activation", event=event["id"], window=list(window),
                            reason=f"{source}:{type}")
            return event

    def _start_installation(self):
        from . import cognition
        cognition.start(self)

    def _remove_client_guard(self):
        if self.client.generate_response is self._guarded_response:
            self.client.generate_response = self._original_response

    def join(self, timeout=None):
        """Wait for queued auxiliary jobs; return False on timeout."""
        from . import cognition
        return cognition.join(self, timeout)

    def close(self, timeout=None):
        """Drain and join the auxiliary worker; restoration never replays jobs."""
        from . import cognition
        return cognition.close(self, timeout)

    def activate_delivery(self, delivery, window, reason):
        return self.append("revisit_delivery", request=delivery["request"], delivery=delivery,
                           window=list(window), reason=reason)

    def wake(self, window, *, assembly=None, quiet=False):
        previous = context.current()
        event = {"group_id": window[1]} if window[0] == "group" else {"target_id": window[1]}
        context.set_current(event)
        try:
            self.drive(None, window, assembly=self._assembly if assembly is None else assembly, quiet=quiet)
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
            if (not isinstance(text, str) or not isinstance(sources, list)
                    or any(not isinstance(source, str) for source in sources)):
                raise ValueError("note text and explicit source list are required")
            if set(sources) - set(seen["sources"]):
                raise ValueError("note source was not seen at this request cutoff")
            note = dict(text=text, sources=list(dict.fromkeys(sources)), cutoff=seen["cutoff"], request=request)
            return self.append("note", parent=parent, version=_hash(note), note=note)

    def _commit_note_tool(self, state):
        def commit_note(parent: str, text: str, sources: list[str]) -> str:
            """按已见版本和来源保存长期理解；parent空串表示尚无笔记。"""
            try:
                entry = self.commit_note(parent=parent or None, text=text, sources=sources,
                                         request=state["request"])
            except ValueError as error:
                # A rejected CAS has no side effect, even with strict tools.
                with self.lock:
                    seen = next((e for e in self.events if e["kind"] == "request"
                                 and e["id"] == state["request"]), None)
                    return _bytes(dict(status="rejected", reason=str(error),
                                       request=state["request"],
                                       allowed_source_ids=seen["sources"] if seen else [],
                                       frozen_parent=seen["note"] if seen else None)).decode()
            return _bytes(entry).decode()
        return commit_note

    def _note_access_provider(self, state):
        access = dict(allowed_source_ids=list(dict.fromkeys(state["sources"])),
                      frozen_parent=state["note"])
        return [{"role": "user", "content":
                 "本次请求的笔记引用范围 " + _bytes(access).decode() + "\n"
                 "commit_note的sources必须逐字取allowed_source_ids中的裸ID，不加archive、描述或哈希。"
                 "目录和正文中嵌套出现的ID不因此成为可引用来源；可引用已收到的整条结果的外壳ID。"
                 "若缺原件，先read_past/read_archive或inspect_runtime(full=true,ids=[单条ID])读取，"
                 "在下一请求的本表确认后再提交。历史笔记可读旧note后以当前parent提交旧text，形成新版本恢复。"
                 "parent使用本次frozen_parent；null时传空串。陈旧候选仍会被拒绝，不能换父版本覆盖。"}]

    def observe_request(self, selection, params):
        state = _request_state.get()
        with _assembly_lock, self.lock:
            self.ensure_available()
            if state is None or state["center"] is not self:
                raise llm.PersistenceError("isolated center rejects unbound model requests")
            require_assembly(self, state.get("assembly"))
            if self.request_limit is not None and sum(e["kind"] == "request" for e in self.events) >= self.request_limit:
                raise llm.PersistenceError("isolated request allowance exhausted")
            if state["role"] in {"selector", "librarian"} and state.get("request_count", 0) >= self.role_request_limit:
                raise llm.RequiredContextError("auxiliary role request allowance exhausted")
            from mods import chat
            if _text_tokens(params) > chat.limit()[1]:
                raise llm.RequiredContextError("SDK text estimate exceeds the configured context allowance")
            cutoffs = [key for key in [state["cutoff"], *state["sources"]]
                       if isinstance(key, str) and key.startswith("C") and key[1:].isdigit()]
            if cutoffs:
                state["cutoff"] = max(cutoffs, key=lambda key: int(key[1:]))
            entry = self.append("request", owner=state["owner"], cutoff=state["cutoff"],
                                role=state["role"], activations=state["activations"],
                                sources=list(dict.fromkeys(state["sources"])),
                                note=state["note"], selection=selection, payload=params)
            state["request"] = entry["id"]
            state["request_count"] = state.get("request_count", 0) + 1

    def observe_action(self, action):
        if action is None:
            return
        state = _request_state.get()
        with _assembly_lock, self.lock:
            self.ensure_available()
            if state is None or state["center"] is not self:
                raise llm.PersistenceError("isolated center rejects unbound actions")
            require_assembly(self, state.get("assembly"))
            self.append("dispatch", owner=state["owner"], role=state["role"],
                        output=action.partition("#")[0], action=action)

    def _freeze(self, state):
        from mods import chatlog, chat

        with self.lock:
            self.ensure_available()
            frozen = list(self.events)
            state["event_start"] = len(frozen)
            maximum = chat.limit()[0]
            arrivals = [e for e in frozen if e["kind"] == "arrival" and e["eligible"]][-maximum:]
            facts = [e for e in frozen if e["kind"] in {
                "output", "results", "environment", "revisit_delivery", "resume"}][-maximum:]
            candidates = sorted([*arrivals, *facts], key=lambda e: int(e["id"][1:]))
            state["cutoff"] = frozen[-1]["id"] if frozen else None
            activations = self.pending()
            state["activations"] = [e["id"] for e in activations]
            state["sources"], state["materials"], state["catalog"] = [], {}, []
            days, references, recent = {}, [], []
            for entry in candidates:
                if entry["kind"] == "arrival" and entry["eligible"]:
                    window = entry["window"]
                    day_key = (*window, entry["day"])
                    if day_key not in days:
                        days[day_key] = {item["ref"]["origin"]: item for item in chatlog.freeze_archive_day(
                            root=self.archive_root, kind=window[0], target=window[1], day=entry["day"],
                            **self.archive_options)}
                    item = days[day_key].get(entry["origin"])
                    if item is None or item["ref"]["sha256"] != entry["raw_sha256"]:
                        raise llm.PersistenceError("archive no longer matches its registered arrival")
                    ref = item["ref"]
                    references.append(dict(arrival=entry["id"], ref=ref))
                    key = ref["key"]
                    state["materials"][key] = dict(kind="archive", ref=ref)
                    state["catalog"].append(dict(key=key, kind="archive", window=window, day=entry["day"],
                                                     text_start=item["raw"].decode("utf-8")[:120]))
                    recent.append(key)
                elif entry["kind"] in {"output", "results", "environment", "revisit_delivery", "resume"}:
                    key = entry["id"]
                    state["materials"][key] = dict(kind="fact", fact=deepcopy(entry))
                    state["catalog"].append({k: v for k, v in dict(
                        key=key, kind=entry["kind"], role=entry.get("role"),
                        text_start=(_bytes(entry.get("assistant", entry.get("returns", entry.get("payload", {})))).decode()[:120]
                                    if entry.get("role", "main") == "main" else None),
                        window=entry.get("window"), source=entry.get("source"), type=entry.get("type")).items()
                                            if v is not None})
                    if entry.get("role", "main") == "main":
                        recent.append(key)
            # WHY: auxiliary traces are inspectable by ID without becoming the
            # main agent's default autobiographical stream.
            state["recent"] = recent[-self.recent_count:]
            state["environment_cursor"] = len(frozen)
            state["activation_message"] = {"role": "user", "content": "frozen activations " + _bytes(activations).decode()}
            self.append("freeze", owner=state["owner"], cutoff=state["cutoff"], references=references,
                        activations=state["activations"], recent=state["recent"], catalog=state["catalog"])
        messages = [self._material_message(state, key) for key in state["recent"]]
        state["sources"].extend(state["activations"])
        messages.append(state["activation_message"])
        return messages

    def _read_material(self, state, key, *, archive):
        material = state["materials"].get(key)
        if material is None or (material["kind"] == "archive") != archive:
            raise ValueError("unknown frozen archive key" if archive else "unknown frozen past fact")
        if archive:
            from mods import chatlog
            try:
                raw = chatlog.read_archive_bytes(material["ref"], root=self.archive_root)
            except Exception as error:
                self.fault(error)
                raise llm.PersistenceError("archive identity changed; refusing further dispatch") from error
            value = dict(ref=material["ref"], raw=raw.decode("utf-8"))
        else:
            value = material["fact"]
        state["sources"].append(key)
        return value

    def _material_message(self, state, key):
        from mods import chat
        material = state["materials"][key]
        value = self._read_material(state, key, archive=material["kind"] == "archive")
        if material["kind"] == "archive":
            return {"role": "user", "content": [
                {"type": "text", "text": f"archive {key} {[value['ref']['kind'], value['ref']['target']]}\n"},
                *chat.msg_split(value["raw"])]}
        return {"role": "user", "content": "committed center fact " + _bytes(value).decode()}

    def _read_tools(self, state):
        def read_archive(key: str) -> str:
            """按目录key读取本醒冻结的archive原件；追加不会扩张引用。"""
            return _bytes(self._read_material(state, key, archive=True)).decode()

        def read_past(key: str) -> str:
            """按目录key读取本醒冻结的原始输出、结果或环境事实；目录不等于正文。"""
            return _bytes(self._read_material(state, key, archive=False)).decode()

        return read_archive, read_past

    def _inspect_runtime_tool(self, state):
        def inspect_runtime(ids: list[str] | None = None, start: str = "", end: str = "",
                            kind: str = "", owner: str = "", request: str = "", role: str = "",
                            after: str = "", limit: int = 20, full: bool = False) -> str:
            """查询本请求cutoff内的运行轨迹JSON；默认仅元数据目录，不视为读到原文。

            Args:
                ids: 精确C编号，可与其他条件交集。
                start: 包含的起始C编号。
                end: 包含的结束C编号。
                kind: 精确记录类型，如request/usage/note/role_start/dispatch/results。
                owner: 所属主醒owner；usage、note与行动沿request/output追溯。
                request: 请求C编号，包含该请求及关联output/usage/note/dispatch/results。
                role: main/selector/librarian等精确角色。
                after: 分页游标，上页next_after；不包含此编号。
                limit: 目录条数1至100，默认20；正文不自动展开。
                full: true时ids必须只有一个ID，返回完整原条并登记引用来源。可读旧note，恢复时用commit_note新提交。
            """
            def number(key):
                if not isinstance(key, str) or not key.startswith("C") or not key[1:].isdigit() or int(key[1:]) < 1:
                    raise ValueError("expected a positive C journal ID")
                return int(key[1:])

            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
                raise ValueError("limit must be between 1 and 100")
            if not isinstance(full, bool):
                raise ValueError("full must be boolean")
            if ids is not None and (not isinstance(ids, list) or not 1 <= len(ids) <= 100):
                raise ValueError("ids must contain 1 to 100 journal IDs")
            chosen = {number(key) for key in ids} if ids is not None else None
            if full and (ids is None or len(ids) != 1):
                raise ValueError("full requires exactly one explicit ID")
            lower, upper, cursor = number(start) if start else 1, number(end) if end else None, number(after) if after else 0
            if upper is not None and lower > upper:
                raise ValueError("start must not follow end")
            if request:
                number(request)
            for value in (kind, owner, request, role):
                if not isinstance(value, str):
                    raise ValueError("filters must be text")
            with self.lock:
                self.ensure_available()
                seen = next((e for e in self.events if e["id"] == state.get("request") and e["kind"] == "request"), None)
                if seen is None:
                    raise ValueError("runtime inspection requires an actually exposed request")
                cutoff = seen["cutoff"]
                # WHY: tools in this output must share its request's boundary;
                # concurrent roles and the inspection's own dispatch are later facts.
                frozen = self.events[:number(cutoff)] if cutoff is not None else []
                by_id = {e["id"]: e for e in frozen}
                records, more = [], False
                for entry in frozen:
                    position = number(entry["id"])
                    if position < lower or position <= cursor or (upper is not None and position > upper):
                        continue
                    if chosen is not None and position not in chosen:
                        continue
                    linked = by_id.get(entry.get("output"), {})
                    request_id = entry.get("request", linked.get("request"))
                    if entry["kind"] == "request":
                        request_id = entry["id"]
                    elif entry["kind"] == "note":
                        request_id = entry["note"]["request"]
                    origin = by_id.get(request_id, {})
                    entry_owner = entry.get("owner", linked.get("owner", origin.get("owner")))
                    entry_role = entry.get("role", linked.get("role", origin.get("role")))
                    if ((kind and entry["kind"] != kind) or (owner and entry_owner != owner)
                            or (request and request_id != request) or (role and entry_role != role)):
                        continue
                    if len(records) == limit:
                        more = True
                        break
                    if full:
                        records.append(deepcopy(entry))
                    else:
                        summary = {key: entry[key] for key in (
                            "id", "kind", "recorded_at", "sha256", "parent", "version", "cutoff",
                            "output", "action", "dispatch", "job", "outcome", "status",
                            "prompt_tokens", "completion_tokens", "cached_tokens") if key in entry}
                        summary.update({key: value for key, value in dict(
                            request=request_id, owner=entry_owner, role=entry_role).items() if value is not None})
                        for field in ("sources", "activations", "calls", "returns", "references", "catalog"):
                            if field in entry:
                                summary[field + "_count"] = len(entry[field])
                        if entry["kind"] == "note":
                            summary["sources_count"] = len(entry["note"]["sources"])
                        records.append(summary)
                exposed = [records[0]["id"]] if full and records else []
                state["sources"].extend(exposed)
                return _bytes(dict(cutoff=cutoff, mode="full" if full else "directory", records=records,
                                   exposed_source_ids=exposed,
                                   next_after=records[-1]["id"] if more else None)).decode()
        return inspect_runtime

    def _environment_provider(self, state):
        with self.lock:
            self.ensure_available()
            tail = deepcopy(self.events[state["environment_cursor"]:])
            state["environment_cursor"] = len(self.events)
            entries = [e for e in tail if e["kind"] == "environment"]
            for entry in entries:
                state["materials"][entry["id"]] = dict(kind="fact", fact=entry)
                state["cutoff"] = entry["id"]
            # Newly delivered wakes stay pending for a fresh main activation;
            # this provider exposes progress, not an acknowledgement claim.
            return [self._material_message(state, e["id"]) for e in entries]

    def drive(self, model, window, *, assembly, quiet=False):
        from mods import chat, image

        while True:
            with _assembly_lock, self.lock:
                # WHY: a scheduler callback can outlive detach and even a new
                # installation of this same object.  Bind before claiming the
                # turn so detach cannot move it onto another authority.
                require_assembly(self, assembly)
                self.ensure_available()
                if self._closing:
                    raise RuntimeError("center is closing")
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
        state = None
        try:
            self.append("owner_start", owner=token, window=list(window), quiet=quiet)
            ledger = image.begin_conversation()
            image_started = True
            while True:
                context.set_current(None)
                context.set_agent_mode(True)
                state = dict(center=self, owner=token, role="main", request=None, note=None,
                             assembly=assembly, quiet=quiet)
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
            with self.lock:
                dispatched = {e["action"] for e in self.events
                              if e["kind"] == "dispatch" and e.get("owner") == token}
                returned = {action for e in self.events if e["kind"] == "results"
                            and e.get("owner") == token for action in e["actions"]}
                if isinstance(error, llm.PersistenceError) or dispatched - returned:
                    self.fault(error)
                elif self.failed is None and state is not None and state.get("request") is not None:
                    if not any(e["kind"] in {"output", "request_end"}
                               and e.get("request") == state["request"] for e in self.events):
                        # WHY: a provider/context failure with no uncertain
                        # dispatch is a failed wake, not a broken journal.
                        self.append("request_end", request=state["request"], owner=token, role="main",
                                    outcome="failed", reason=type(error).__name__)
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
            if origin is not None and not quiet:
                chat._run_hint(window, turn, _assembly=(self, assembly))

    def _run(self, model, turn, state):
        from mods import chat

        session = llm.Chat(model=model or chat.get_model(), chat_client=self.client)
        session.fail_fast = True
        session.associated_windows = turn.associated_windows
        session.requested_reads = []
        messages = self._freeze(state)
        from . import cognition
        selected = cognition.select(self, state, messages)
        messages.extend(self._material_message(state, key) for key in selected if key not in state["recent"])
        messages.append({"role": "user", "content": "frozen material directory (IDs are not original contents) "
                         + _bytes(state["catalog"]).decode()})
        binding = chat._activate_chat(session, messages)
        # The old reader callbacks are replaced before Chat is invoked.  TTL,
        # schema restore, module loading, image/reasoning and hint stay native.
        session.on_output = lambda assistant, calls: self._output(state, assistant, calls)
        session.on_results = lambda output, results, native: self._results(state, binding, output, results)
        session.should_stop = lambda: turn.cancelled
        # WHY: the live tool loop deliberately has no round limit (llm.chat's
        # WHY). Only an explicit experiment allowance may end a wake by count.
        session.turn_done = lambda: (self.wake_request_limit is not None
                                    and state.get("request_count", 0) >= self.wake_request_limit)
        session.add_context_provider(lambda: self._environment_provider(state))
        session.add_context_provider(lambda: self._notes_provider(state, binding))
        session.add_context_provider(lambda: self._note_access_provider(state))
        turn._chat_limit = chat.limit()

        commit_note = self._commit_note_tool(state)

        def read_note() -> str:
            """重读本请求已见笔记；历史版本用inspect_runtime全文读note。"""
            with self.lock:
                head = next((e for e in self.events if e["id"] == state["note"]), None)
                return _bytes(head).decode()

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

        read_archive, read_past = self._read_tools(state)
        session.center_tools = {f.__name__: f for f in (
            commit_note, read_note, revisit_at, cancel_revisit, read_archive, read_past,
            self._inspect_runtime_tool(state))}
        session.add_hint("中心使用冻结archive与center事实；旧 unread/take/fetch/cover API 明确不支持。"
                         "长期理解可主动 commit_note；不是轮末必做。"
                         "可用inspect_runtime自检请求、usage、笔记谱系、角色与工具派发/结果；目录不含正文。"
                         "重要决定可简记理由，以实际结果为准；自检可以不改动，不必每晚修改。"
                         "资料中的他人观点不自动成为你的立场。sources逐字采用本次allowed_source_ids中的裸ID。")
        if state.get("quiet"):
            session.add_hint("本醒是内部自检，不默认向聊天窗口外发。可先查证据，也可不改任何内容；"
                             "确有必要对外沟通时才显式say。")
        session.add_hint(lambda: chat._view._closing_hint(session.messages))

        def collect(chunk):
            if chunk.total_tokens:
                self.append("usage", role=state["role"], request=state["request"], prompt_tokens=chunk.prompt_tokens,
                            completion_tokens=chunk.completion_tokens, cached_tokens=chunk.cached_tokens)
            chat.get_handler(session)(chunk)

        before = len(self.events)
        session.chat(recall_func=collect, description_cache=chat.description_cache)
        if turn.cancelled and state["request"] is not None and not any(
                e["kind"] == "output" and e["request"] == state["request"] for e in self.events[before:]):
            self.append("request_end", request=state["request"], outcome="cancelled",
                        reason="generation closed before any output/dispatch commit")
        if not turn.cancelled:
            cognition.enqueue(self, state)
        turn._chat_usage_tokens = _text_tokens(session.messages)
        turn._chat_event_count = len(state["sources"])

    def _notes_provider(self, state, binding):
        try:
            self.ensure_available()
            binding.sync_registry()
            for name in {"status", "take", "mentions", "fetch", "pull", "mark_read",
                         "cover_events", "recall_events", "event_links"}:
                binding.session.functions.pop(name, None)
            for function in binding.session.center_tools.values():
                binding.session.add_tool(function)
            head = self.note_head()
            version = head["id"] if head else None
            if state["note"] == version and state.get("note_told"):
                return []
            state["note"], state["note_told"] = version, True
            # WHY: the injected version is a source only from this request onward.
            if version is not None:
                state["sources"].append(version)
                if state["cutoff"] is None or int(version[1:]) > int(state["cutoff"][1:]):
                    state["cutoff"] = version
            return [{"role": "user", "content": "current long term note " + _bytes(head).decode()}]
        except Exception as error:
            raise llm.RequiredContextError("center note/context failed") from error

    def _output(self, state, assistant, calls):
        entry = self.append("output", owner=state["owner"], role=state["role"], request=state["request"],
                            assistant=assistant, calls=calls,
                            activations=state["activations"] if state["role"] == "main" else [])
        state["sources"].append(entry["id"])
        return entry["id"]

    def _results(self, state, binding, output, results):
        returns = [dict(tool_call_id=r.tool_call_id, name=r.name, arguments=r.arguments, content=r.content)
                   for r in results]
        entry = self.append("results", owner=state["owner"], role=state["role"], output=output, returns=returns,
                            actions=[f"{output}#{i+1}" for i in range(len(results))])
        state["sources"].append(entry["id"])
        if binding is not None:
            for result in results:
                binding.touch(result.name)
