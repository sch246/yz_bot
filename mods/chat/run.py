"""One model run over journal references, with an explicit purpose.

The view owns durable inputs and outputs; this module owns only the lifetime of
one execution. Request hints and the tool binding never become a second view.
"""

from __future__ import annotations

import contextvars
import logging
import re
import threading
import time

from mods import context, image, llm, tools as tool_modules, usage, watchdog

from . import segment


_wake = contextvars.ContextVar("chat_current_wake", default=None)
_run = contextvars.ContextVar("chat_current_run", default=None)


def current_run() -> dict | None:
    """The run owning this execution, including auxiliary and parallel runs."""
    return _run.get()


def current_wake() -> dict | None:
    """The main wake executing here; auxiliary and parallel runs have none."""
    return _wake.get()


def write(state, operation, *args, **kwargs):
    """A durable tool mutation can stop its run without poisoning later runs."""
    try:
        return operation(*args, **kwargs)
    except OSError as error:
        state["failure"] = error
        raise


def run(items, goal, *, tools=(), model=None, kind="fork", view=None,
        stop=None, timeout=None, state=None, prepare=None, hints=(),
        on_output=None, after_round=None, effort=None, grant=(), functions=None, inherit_tools=True,
        allow_reframe=None, history=None, deadline=None) -> tuple[str, str]:
    """Execute and record one purpose, returning its ctx and assistant text.

    ``items`` are journal ids or original message dictionaries. A supplied main
    view is saved after each append; independent runs freeze their own view in
    finally, including input references and any results completed before failure.
    If a timed-out worker is still blocked, closing returns without waiting for
    its locks. ``state['finalization']`` stays ``pending`` until that worker exits
    and background archiving finishes; timeout does not promise an immediate freeze.
    ``prepare(state)`` may append durable sources and return more source dicts
    to append after the tool announcement. It runs before every actual request.
    ``functions``, when given, is the exact set of tool names this run may see.
    ``inherit_tools=False`` keeps tool activation local to this run; its caller
    supplies the opening modules rather than restoring the global selection.
    Run-owned fork views allow reframe by default; main/construct opt in with
    allow_reframe=True. A caller-owned view cannot change ctx. prepare/on_output/
    after_round receive the same state throughout; state["view"] and optional
    state["history"] point at the current segment. The outer finally closes the
    whole invocation once, including an inherited absolute monotonic deadline.
    """
    import mods.chat as chat

    goal = str(goal).strip()
    if not goal:
        raise ValueError("run goal cannot be empty")
    if timeout is not None and float(timeout) <= 0:
        raise ValueError("run timeout must be positive")
    can_reframe = kind == "fork" and view is None if allow_reframe is None else bool(allow_reframe)
    if can_reframe and (view is not None or kind not in {"fork", "construct", "main"}):
        raise ValueError("reframe requires a run-owned fork/construct/main view; supplied persistent views cannot change owner")
    try:
        parent_binding = tool_modules.current_binding()
    except RuntimeError:
        parent_binding = None
    own_view = view is None
    view = segment.fresh() if own_view else view
    state = {} if state is None else state
    persist = not own_view
    state.update(view=view, kind=kind, goal=goal, persist=persist, end=False,
                 closed=False, output_count=0, last=None, action=None)
    state.setdefault("cancelled", False)
    if history is not None:
        state["history"] = history
    if deadline is None:
        deadline = state.get("deadline")
    if timeout is not None:
        deadline = min(deadline, time.monotonic() + float(timeout)) if deadline is not None else time.monotonic() + float(timeout)
    state["deadline"] = deadline
    state["origin_ctx"] = view["ctx"]
    state["segments"] = [view["ctx"]]
    state["segment_index"] = 0
    completed_usage = {}
    state.setdefault("associated_windows", set())
    state.setdefault("log", {"start": time.time(), "peak": 0, "acts": 0, "kind": kind})
    guard = threading.RLock()
    finished = threading.Event()
    invoked = threading.Event()
    pieces = []

    def expired():
        return deadline is not None and time.monotonic() >= deadline

    def stopped():
        return bool(expired() or state["closed"] or state["cancelled"] or state.get("failure") or (stop is not None and stop()))

    def writable(*, dispatch=False):
        if dispatch and expired():
            raise TimeoutError("run 已到原定截止时间")
        if state["closed"] or dispatch and stopped():
            raise watchdog.Interrupted("run 已结束或被取消")

    def append(source):
        with guard:
            writable()
            return segment.append(view, persist=persist, **source)

    def refresh_usage():
        if can_reframe:
            state["call_usage"] = segment.combine_usage(completed_usage, view["stats"].get("requests", {}))
            view["stats"]["call_usage"] = state["call_usage"]

    def execute():
        nonlocal view, completed_usage
        # WHY: copy_context can carry a parent's wake into fork or construct.
        # Clearing explicitly is what keeps their tools from ending that wake.
        token = _wake.set(state if kind == "main" else None)
        run_token = _run.set(state)
        previous_event, previous_mode = context.current(), context.agent_mode()
        context.set_current(None)
        context.set_agent_mode(kind in {"main", "settle", "reflect"})
        ledger = image.begin_conversation()

        def new_session(selected, *, opening=False):
            session = llm.Chat(model=selected_model, chat_client=llm.get_client(),
                               description_cache=chat.description_cache,
                               do_process_image=chat.get_image_mode() != "off")
            session.fail_fast = True
            session.preserve_native = True
            session.keep_reasoning = True
            session.hint_role = "system"
            session.reasoning_effort = None if configured_effort == "default" else configured_effort
            # WHY: #temperature 是给她说话用的（effort=none 时默认 1.3 是创意写作的推荐值）；
            # 构造只挑材料、另有自己的 effort 设置，不跟着升温。
            session.temperature = None if kind == "construct" else chat.temperature_for(session.reasoning_effort)
            session.describe_images = chat.get_image_describe()
            binding = None
            if selected:
                prior = segment.derive(view)["tool_state"]
                binding = tool_modules.bind_session(
                    session,
                    visible=lambda name, module: name not in {"baidumap", "dianping"}
                    and tool_modules.bot_op_tool_visible(name, module),
                    persist=chat._persist_modules if central and inherit_tools and opening else None,
                    schema_modules=list((prior or {}).get("schemas", {})) if central and opening else (),
                    kind=kind, grant=grant,
                    function_visible=None if functions is None else lambda name, _tool: name in functions,
                    local_function_visible=lambda name, _tool: name != "reframe" or can_reframe,
                    parent_binding=parent_binding,
                )
                binding["run"] = state
                # WHY: 每次行动的工具由构造选，旧激活态不应绕过这次选择。
                # 临时选用也不覆盖反思/沉淀自行维护的持久集合（2026-11-02）。
                if central and inherit_tools and opening:
                    active = chat._active_modules()
                    if active:
                        tool_modules.restore(binding, active)
                requested = [name for name in selected if name != "meta"]
                if requested:
                    loaded = tool_modules.load(binding, requested)
                    errors = [f"{name}: {result['error']}" for name, result in loaded.items() if result.get("error")]
                    if errors:
                        raise ValueError("requested tools unavailable: " + "\n".join(errors))
                tool_modules.restore_schema(binding)

            head = [*chat.get_prompt(), *chat._view._base_prompt(),
                    {"role": "system", "content": "你是唯一的中心 agent；窗口只是来源与明确发送目标。"}]
            if kind == "summary":
                head = []

            return session, binding, head

        try:
            chat.inc_call_count()
            selection = list(tools)
            selected_model = model or chat.get_model()
            configured_effort = effort if effort is not None else chat.get_reasoning_effort()
            central = kind in {"main", "settle", "reflect"}
            prepared = None
            while True:
                writable(dispatch=True)
                session, binding, head = prepared or new_session(selection, opening=state["segment_index"] == 0)
                prepared = None
                state["model"] = session.model
                state["binding"] = binding
                state.pop("pending_reframe", None)
                def assemble():
                    writable(dispatch=True)
                    # WHY: preparation can wait on an observer or external source.
                    # Closing a timed-out run must not wait for that same callback.
                    sources = list(prepare(state) or ()) if prepare is not None else []
                    with guard:
                        writable(dispatch=True)
                        if binding is not None:
                            tool_modules.reconcile(binding)
                            previous = segment.own_tool_state(view) if own_view else segment.derive(view)["tool_state"]
                            snapshot = tool_modules.state_snapshot(binding)
                            if snapshot != previous:
                                text = tool_modules.state_text(binding, previous)
                                added = append({"kind": "system", "messages": [{"role": "system", "content": text}],
                                                "tool_state": snapshot})
                                if previous is None and own_view:
                                    # WHY: 2026-11-01 切到无状态后缓存命中从 96% 掉到 80%：这份约 8k token
                                    # 的完整工具宣告跨运行不变，却排在本次的材料和目的之后，每次运行都
                                    # 重新计费。只在第一次请求前挪到最前，之后的请求仍是追加。
                                    view["items"].remove(added)
                                    view["items"].insert(0, added)
                        for source in sources or ():
                            append(source)
                        rendered, source_refs = segment.render(view, reasoning=chat.get_reasoning_mode(),
                                                               mark_borrowed=True, with_sources=True)
                        attachments = state.pop("images", [])
                        session.messages[:] = [*head, *rendered, *attachments]
                        state["request_sources"] = source_refs
                        state["request_attachments"] = len(attachments)
                        state["used"] = segment.context_size(view["stats"].get("tokens"), session.messages[len(head):])
                        state["initial"] = state.get("initial", state["used"])
                        state["log"]["peak"] = max(state["log"].get("peak", 0), state["used"])
                        return []

                def record_request(messages, schemas):
                    with guard:
                        writable(dispatch=True)
                        outline = segment.request_outline(
                            view, messages, schemas, head_count=len(head), source_refs=state["request_sources"],
                            attachment_count=state["request_attachments"], model=session.model, goal=goal)
                        state["request_number"] = segment.record_request(view, outline, persist=persist)
                        refresh_usage()

                def record_request_usage(info):
                    with guard:
                        # A dispatched request may report usage while cancellation
                        # closes its stream; keep the bill with that request.
                        segment.record_request_usage(view, state["request_number"], info, persist=persist)
                        refresh_usage()

                def before_output(_assistant, calls):
                    error = "reframe 必须独占一次工具批次；本批全部工具均未执行，请单独重试" if (
                        len(calls) > 1 and any(call.get("function", {}).get("name") == "reframe" for call in calls)) else None
                    state["batch_error"] = error
                    return error

                def record_output(assistant, calls):
                    with guard:
                        writable(dispatch=True)
                        source = segment.begin_act(view, assistant, persist=persist, run_kind=kind, model=session.model)
                        state["last"], state["act"] = assistant, source
                        state["output_count"] += 1
                        if state.get("batch_error") is None:
                            state["log"]["acts"] += len(calls)
                        if state.pop("reported", None) is not None:
                            view["stats"]["tokens"] = [state["last_usage"], len(segment.render(view))]
                        if on_output is not None and state.get("batch_error") is None:
                            on_output(state)
                        if persist:
                            segment.save(view)
                        return source

                def tool_start(source, call):
                    with guard:
                        writable(dispatch=True)
                        segment.tool_started(view, source, call["id"], persist=persist)

                def tool_result(source, result, native):
                    with guard:
                        # A completed external action still gets its result after ^C.
                        segment.record_results(view, source, [native], persist=persist)
                        if binding is not None and state.get("batch_error") is None:
                            tool_modules.touch(binding, result.name)

                def results(source, _results, _native):
                    with guard:
                        segment.record_results(view, source, [], complete=True, persist=persist)
                        state["used"] = segment.context_size(view["stats"].get("tokens"), segment.render(view))
                        state["log"]["peak"] = max(state["log"].get("peak", 0), state["used"])
                        if after_round is not None and not stopped() and state.get("batch_error") is None:
                            after_round(state)

                def prepare_reframe(focus, refs, note, requested_tools, history):
                    writable(dispatch=True)
                    if tool_modules.current_binding() is not binding:
                        return "旧段的工具不能替当前段 reframe"
                    focus = str(focus).strip()
                    if not focus:
                        return "focus 不能空；原段继续"
                    if history is not None and (kind != "main" or isinstance(history, bool)
                                                or not isinstance(history, int) or history < 0):
                        return "history 仅主行动可用，写非负整数；原段继续"
                    if state.get("pending_reframe") is not None:
                        return "本段已经准备换段，不能重复提交"
                    modules = [name for name in binding["active"] if name != "meta"] if requested_tools is None else list(
                        dict.fromkeys(name for name in re.split(r"[\s,，、]+", str(requested_tools)) if name and name != "meta"))
                    selected = ["meta", *modules]
                    try:
                        resolved = segment.resolve_refs(view, refs)
                        candidate = new_session(selected)
                    except (ValueError, KeyError, FileNotFoundError) as error:
                        return f"reframe 材料或工具不可用：{error}；原段继续"
                    except OSError as error:
                        state["failure"] = error
                        raise
                    writable(dispatch=True)
                    planned = segment.fresh()
                    state["pending_reframe"] = {"view": planned, "items": resolved, "focus": focus,
                                               "note": str(note).strip(), "refs": str(refs).splitlines(),
                                               "tools": modules, "history": state.get("history") if history is None else history,
                                               "session": candidate}
                    return f"已准备下一段 {planned['ctx']}；本批结果记下后继续。原段 {view['ctx']} 可追溯"

                def collect(chunk):
                    if chunk.role == "assistant" and chunk.content:
                        pieces.append(str(chunk.content))
                        if central:
                            # WHY: text is self-talk, never an implicit QQ send.
                            chat._self_talk.info(f'[{time.strftime("%H:%M:%S")}]【自言自语】{chunk.content}')
                    if chunk.total_tokens:
                        chat.inc_call_cost(session.model, chunk.prompt_tokens, chunk.completion_tokens,
                                           chunk.cached_tokens, chunk.requested_at)
                        usage.log_call(session.model, chunk.prompt_tokens, chunk.completion_tokens,
                                       chunk.cached_tokens, chunk.requested_at)
                        state["reported"] = True
                        state["last_usage"] = (chunk.prompt_tokens or 0) + (chunk.completion_tokens or 0)

                session.on_output = record_output
                session.before_output = before_output
                session.on_request = record_request
                session.on_request_usage = record_request_usage
                session.on_tool_start = tool_start
                session.on_tool_result = tool_result
                session.on_results = results
                session.on_action = lambda reference: state.update(action=reference)
                session.should_stop = stopped
                session.turn_done = lambda: state["end"] or state.get("pending_reframe") is not None
                if can_reframe:
                    state["prepare_reframe"] = prepare_reframe
                session.add_context_provider(assemble)
                # WHY: 构造决定 todo 全文或选区；其它运行保留召回入口，不再自动摊开全文。
                if kind != "construct":
                    session.add_hint('待办保存在笔记 todo（data/notes/todo.md），需要时用 read_file("data/notes/todo.md") 召回。')
                for hint in hints:
                    session.add_hint(hint)
                session.add_hint(lambda: f"现在是 {chat._screen.clock()}。")
                session.add_hint(f"【本次目的】{goal}")
                # WHY: the construct run (formerly observer) prepares context; its live
                # words must not become the central agent's terminal self-talk (2026-10-29).
                session.chat(recall_func=collect, stream=kind != "construct")
                if state.get("failure"):
                    raise state["failure"]
                if expired():
                    raise TimeoutError("run 已到原定截止时间")
                plan = state.pop("pending_reframe", None)
                if plan is None or stopped() or state["end"]:
                    break
                writable(dispatch=True)
                with guard:
                    writable(dispatch=True)
                    write(state, segment.link_reframe, view, plan["view"], items=plan["items"], goal=goal, kind=kind,
                                         focus=plan["focus"], note=plan["note"], refs=plan["refs"],
                                         tools=plan["tools"], history=plan["history"], origin=state["origin"])
                    refresh_usage()
                    segment.archive(view, kind)
                    completed_usage = state["call_usage"]
                    view = plan["view"]
                    state["view"] = view
                    state["segments"].append(view["ctx"])
                    state["segment_index"] += 1
                    state["history"] = plan["history"]
                    for key in ("initial", "used", "request_number", "request_sources", "request_attachments",
                                "reported", "last_usage", "act", "images", "batch_error"):
                        state.pop(key, None)
                    state.update(last=None, action=None)
                    pieces.clear()
                    prepared = plan["session"]
        finally:
            image.end_conversation(ledger)
            context.set_current(previous_event)
            context.set_agent_mode(previous_mode)
            _wake.reset(token)
            _run.reset(run_token)

    def invoke():
        invoked.set()
        try:
            execute()
        finally:
            finished.set()

    failure = None
    deferred = False

    def finalize():
        with guard:
            refresh_usage()
            if failure is not None:
                view["stats"]["failure"] = failure
            if state["cancelled"]:
                view["stats"]["cancelled"] = True
            view["stats"]["kind"] = kind if own_view else view["stats"].get("kind", "")
            if own_view:
                segment.archive(view, kind)
            else:
                segment.save(view)
        state["finalization"] = "complete"

    def finalize_later():
        # WHY: a worker may be stuck in fsync while holding both our guard and
        # segment's lock. Only its exit makes the view safe to freeze; waiting
        # here keeps that necessary ordering out of the timeout caller's path.
        finished.wait()
        try:
            finalize()
        except BaseException as error:
            state["finalization"] = "failed"
            state["finalize_error"] = error
            logging.getLogger(__name__).exception("延后的 run 归档失败：%s", view["ctx"])

    try:
        messages = []
        for item in items or ():
            if isinstance(item, dict):
                messages.append(item)
            else:
                if messages:
                    append({"kind": "input", "messages": messages})
                    messages = []
                segment.extend(view, [item], persist=persist)
        if messages:
            append({"kind": "input", "messages": messages})
        original_goal = append({"kind": "goal", "goal": goal, "run_kind": kind})
        state["origin"] = {"ctx": view["ctx"], "goal_ref": original_goal}
        if can_reframe:
            view["stats"].update(origin=state["origin"], focus=goal)
        if deadline is None:
            invoke()
        else:
            writable(dispatch=True)
            scope = contextvars.copy_context()
            watchdog.run(lambda: scope.run(invoke), max(0.001, deadline - time.monotonic()))
        return view["ctx"], "".join(pieces).strip()
    except BaseException as error:
        state["cancelled"] = True
        failure = f"{type(error).__name__}: {error}"
        deferred = (deadline is not None and isinstance(error, TimeoutError)
                    and invoked.is_set() and not finished.is_set())
        raise
    finally:
        state["closed"] = True
        state.pop("prepare_reframe", None)
        if stop is not None and stop():
            state["cancelled"] = True
        state["finalization"] = "pending"
        if deferred:
            threading.Thread(target=finalize_later, name="chat-run-finalize", daemon=True).start()
        else:
            try:
                finalize()
            except BaseException:
                state["finalization"] = "failed"
                raise
