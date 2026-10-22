"""Optional selection and note work on Center's existing Chat and journal."""

from __future__ import annotations

from copy import deepcopy
import threading
import time
from uuid import uuid4

from mods import llm
from .center import _bytes, _request_state, _assembly_lock, require_assembly


def start(c):
    """Protect only model generation, including stream consumption, on this client."""
    c._closing = False
    c._worker, c._job_running = None, None
    c._original_response = c.client.generate_response

    def generate(*args, **kwargs):
        # WHY: the client belongs to every role. ContextVars identify the caller;
        # serializing network requests does not serialize the rest of a wake.
        with c._model_lock:
            return (yield from c._original_response(*args, **kwargs))

    c._guarded_response = generate
    c.client.generate_response = generate


def _state(c, main, role):
    return dict(center=c, owner=main["owner"], role=role, request=None,
                request_count=0, cutoff=main["cutoff"], activations=[], sources=[],
                note=None, materials=deepcopy(main["materials"]), assembly=main["assembly"])


def _add_note(c, state, messages, head):
    state["note"] = head["id"] if head else None
    if head is not None:
        state["sources"].append(head["id"])
    messages.append({"role": "user", "content": "current long term note " + _bytes(head).decode()})


def _run(c, state, session, job):
    marker = _request_state.set(state)
    session.fail_fast = True
    session.on_output = lambda assistant, calls: c._output(state, assistant, calls)
    session.on_results = lambda output, results, native: c._results(state, None, output, results)
    session.add_context_provider(lambda: c._note_access_provider(state))
    # The final allowed response still commits its complete tool batch. A role
    # never starts another request merely to announce that it has finished.
    session.turn_done = lambda: state["request_count"] >= c.role_request_limit
    outcome, error_name = "complete", None
    try:
        c.append("role_start", role=state["role"], owner=state["owner"], job=job,
                 cutoff=state["cutoff"], note=state["note"])
        with _assembly_lock:
            require_assembly(c, state["assembly"])
        def collect(chunk):
            from mods import chat
            if chunk.total_tokens:
                c.append("usage", role=state["role"], request=state["request"],
                         prompt_tokens=chunk.prompt_tokens, completion_tokens=chunk.completion_tokens,
                         cached_tokens=chunk.cached_tokens)
            chat.get_handler(session)(chunk)
        session.chat(recall_func=collect)
    except Exception as error:
        outcome, error_name = "failed", type(error).__name__
        # An observed auxiliary failure is not a request to retry on recovery.
        # Durable failures still close the shared Center gate.
        with c.lock:
            if c.failed is None and state["request"] is not None and not any(
                    e["kind"] == "output" and e["request"] == state["request"] for e in c.events):
                c.append("request_end", request=state["request"], outcome="failed", role=state["role"],
                         reason=error_name)
        if isinstance(error, llm.PersistenceError):
            raise
    finally:
        try:
            if c.failed is None:
                c.append("role_end", role=state["role"], owner=state["owner"], job=job,
                         outcome=outcome, error=error_name, requests=state["request_count"])
        finally:
            _request_state.reset(marker)
    return outcome


def select(c, main, recent_messages):
    if c.selector_model is None:
        return []
    state = _state(c, main, "selector")
    state["sources"] = list(main["sources"])
    messages = [{"role": "system", "content":
        "你为主循环补选材料。近期画面与当前笔记始终保留，不可删除。目录只有定位，不是原文。"
        "必要时read_archive/read_past查看冻结原件，再select_material一次提交额外key，可空选。"
        "选中后主循环会收到准确原件；你不代替主循环行动或写笔记。"}, *deepcopy(recent_messages),
        {"role": "user", "content": "frozen material directory " + _bytes(main["catalog"]).decode()}]
    _add_note(c, state, messages, c.note_head())
    session = llm.Chat(model=c.selector_model, chat_client=c.client, messages=messages)
    selected = []

    def select_material(keys: list[str]) -> str:
        """提交额外材料key；不会删除近期画面或笔记。一次选择即可结束。"""
        if not isinstance(keys, list) or any(not isinstance(key, str) or key not in state["materials"] for key in keys):
            raise ValueError("selection contains an unknown frozen key")
        selected[:] = list(dict.fromkeys(keys))
        session.turn_done = lambda: True
        return _bytes(dict(selected=selected)).decode()

    session.set_tools([*c._read_tools(state), select_material])
    return selected if _run(c, state, session, uuid4().hex) == "complete" else []


def enqueue(c, main):
    if c.librarian_model is None:
        return
    with c.lock:
        if c._closing:
            raise RuntimeError("center is closing")
        # Freeze actual request exposures, plus this main wake's committed
        # outputs/results. Directory IDs and auxiliary traces add no evidence.
        turn_events = c.events[main.get("event_start", 0):]
        sources = list(dict.fromkeys(
            [key for e in turn_events if e["kind"] == "request" and e["owner"] == main["owner"]
             and e.get("role", "main") == "main" for key in e["sources"]]
            + [e["id"] for e in turn_events if e["kind"] in {"output", "results"}
               and e["owner"] == main["owner"] and e.get("role", "main") == "main"]))
        state = _state(c, main, "librarian")
        messages = [{"role": "system", "content":
            "你整理这一轮实际已见的材料与已提交输出/结果。可不修改笔记；有必要时commit_note，"
            "保留来源，分清外部事实、自身理解与未确认动作。不能把旧候选换父版本覆盖新笔记。"
            "完成后直接结束，不安排动作或唤醒。"}]
        by_id = {e["id"]: e for e in c.events}
        for key in sources:
            if key in state["materials"]:
                messages.append(c._material_message(state, key))
            elif key in by_id:
                messages.append({"role": "user", "content": "committed center fact " + _bytes(by_id[key]).decode()})
                state["sources"].append(key)
        state["cutoff"] = c.events[-1]["id"] if c.events else None
        head = c.note_head()
        _add_note(c, state, messages, head)
        job = c.append("librarian_queued", owner=main["owner"], cutoff=state["cutoff"], note=state["note"],
                       sources=list(dict.fromkeys(state["sources"])))
        c._jobs.append((job["id"], state, messages, head))
        if c._worker is None:
            c._worker = threading.Thread(target=_work, args=(c,), name="center-librarian", daemon=False)
            c._worker.start()
        c._jobs_changed.notify_all()


def _work(c):
    while True:
        with c.lock:
            while not c._jobs and not c._closing:
                c._jobs_changed.wait()
            if not c._jobs:
                c._jobs_changed.notify_all()
                return
            job, state, messages, head = c._jobs.pop(0)
            c._job_running = job
        try:
            session = llm.Chat(model=c.librarian_model, chat_client=c.client, messages=messages)

            def read_note() -> str:
                """重读本任务冻结的笔记；不把旧候选换成新的父版本。"""
                return _bytes(head).decode()

            session.set_tools([c._commit_note_tool(state), read_note])
            _run(c, state, session, job)
        except BaseException as error:
            # Catch at the worker boundary so join observes termination and no
            # later queued task dispatches after a persistence failure.
            with c.lock:
                if c.failed is None:
                    c.append("librarian_error", job=job, error=type(error).__name__)
                c._jobs.clear()
        finally:
            with c.lock:
                c._job_running = None
                c._jobs_changed.notify_all()


def join(c, timeout=None):
    deadline = None if timeout is None else time.monotonic() + timeout
    with c._jobs_changed:
        while c._jobs or getattr(c, "_job_running", None):
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return False
            c._jobs_changed.wait(remaining)
        worker = c._worker
    if c._closing and worker is not None and worker is not threading.current_thread():
        remaining = None if deadline is None else max(0, deadline - time.monotonic())
        worker.join(remaining)
        return not worker.is_alive()
    return True


def close(c, timeout=None):
    with c.lock:
        c._closing = True
        c._jobs_changed.notify_all()
    return join(c, timeout)
