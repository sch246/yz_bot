"""One model run over journal references, with an explicit purpose.

The view owns durable inputs and outputs; this module owns only the lifetime of
one execution. Request hints and the tool binding never become a second view.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time

from mods import context, image, llm, tools as tool_modules, usage, watchdog

from . import segment


_wake = contextvars.ContextVar("chat_current_wake", default=None)


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
        on_output=None, after_round=None, effort=None, grant=()) -> tuple[str, str]:
    """Execute and record one purpose, returning its ctx and assistant text.

    ``items`` are journal ids or original message dictionaries. A supplied main
    view is saved after each append; independent runs freeze their own view in
    finally, including input references and any results completed before failure.
    If a timed-out worker is still blocked, closing returns without waiting for
    its locks. ``state['finalization']`` stays ``pending`` until that worker exits
    and background archiving finishes; timeout does not promise an immediate freeze.
    ``prepare(state)`` may append durable sources and return more source dicts
    to append after the tool announcement. It runs before every actual request.
    """
    import mods.chat as chat

    goal = str(goal).strip()
    if not goal:
        raise ValueError("run goal cannot be empty")
    if timeout is not None and float(timeout) <= 0:
        raise ValueError("run timeout must be positive")
    own_view = view is None
    view = segment.fresh() if own_view else view
    state = {} if state is None else state
    persist = not own_view
    state.update(view=view, kind=kind, goal=goal, persist=persist, end=False,
                 closed=False, output_count=0, last=None, action=None)
    state.setdefault("cancelled", False)
    state.setdefault("associated_windows", set())
    state.setdefault("log", {"start": time.time(), "peak": 0, "acts": 0, "kind": kind})
    guard = threading.RLock()
    finished = threading.Event()
    pieces = []

    def stopped():
        return bool(state["closed"] or state["cancelled"] or state.get("failure") or (stop is not None and stop()))

    def writable(*, dispatch=False):
        if state["closed"] or dispatch and stopped():
            raise watchdog.Interrupted("run 已结束或被取消")

    def append(source):
        with guard:
            writable()
            return segment.append(view, persist=persist, **source)

    def execute():
        # WHY: copy_context can carry a parent's wake into fork or observer.
        # Clearing explicitly is what keeps their tools from ending that wake.
        token = _wake.set(state if kind == "main" else None)
        previous_event, previous_mode = context.current(), context.agent_mode()
        context.set_current(None)
        context.set_agent_mode(kind in {"main", "handoff", "reflect"})
        ledger = image.begin_conversation()
        try:
            chat.inc_call_count()
            session = llm.Chat(model=model or chat.get_model(), chat_client=llm.get_client(),
                               description_cache=chat.description_cache,
                               do_process_image=chat.get_image_mode() != "off")
            state["model"] = session.model
            session.fail_fast = True
            session.preserve_native = True
            session.keep_reasoning = True
            session.hint_role = "system"
            configured_effort = effort if effort is not None else chat.get_reasoning_effort()
            session.reasoning_effort = None if configured_effort == "default" else configured_effort
            central = kind in {"main", "handoff", "reflect"}
            binding = None
            if tools:
                prior = segment.derive(view)["tool_state"]
                binding = tool_modules.bind_session(
                    session,
                    visible=lambda name, module: name not in {"baidumap", "dianping"}
                    and tool_modules.bot_op_tool_visible(name, module),
                    persist=chat._persist_modules if central else None,
                    schema_modules=list((prior or {}).get("schemas", {})) if central else (),
                    kind=kind, grant=grant,
                )
                binding["run"] = state
                state["binding"] = binding
                if central:
                    active = chat._active_modules()
                    if active:
                        tool_modules.restore(binding, active)
                requested = [name for name in tools if name != "meta"]
                if requested:
                    loaded = tool_modules.load(binding, requested)
                    errors = [f"{name}: {result['error']}" for name, result in loaded.items() if result.get("error")]
                    if errors:
                        raise ValueError("requested tools unavailable: " + "\n".join(errors))
                tool_modules.restore_schema(binding)

            head = [*chat.get_prompt(), *chat._view._base_prompt(),
                    {"role": "system", "content": "你是唯一的中心 agent；窗口只是来源与明确发送目标。"}]
            if kind in {"observer", "summary"}:
                head = []

            def assemble():
                writable(dispatch=True)
                # WHY: preparation can wait on an observer or external source.
                # Closing a timed-out run must not wait for that same callback.
                sources = list(prepare(state) or ()) if prepare is not None else []
                with guard:
                    writable(dispatch=True)
                    if binding is not None:
                        tool_modules.reconcile(binding)
                        previous = segment.derive(view)["tool_state"]
                        snapshot = tool_modules.state_snapshot(binding)
                        if snapshot != previous:
                            text = tool_modules.state_text(binding, previous)
                            append({"kind": "system", "messages": [{"role": "system", "content": text}],
                                    "tool_state": snapshot})
                    for source in sources or ():
                        append(source)
                    session.messages[:] = [*head, *segment.render(view, reasoning=chat.get_reasoning_mode())]
                    session.messages.extend(state.pop("images", []))
                    state["used"] = segment.context_size(view["stats"].get("tokens"), session.messages[len(head):])
                    state["initial"] = state.get("initial", state["used"])
                    state["log"]["peak"] = max(state["log"].get("peak", 0), state["used"])
                    return []

            def record_output(assistant, calls):
                with guard:
                    writable(dispatch=True)
                    source = segment.begin_act(view, assistant, persist=persist, run_kind=kind, model=session.model)
                    state["last"], state["act"] = assistant, source
                    state["output_count"] += 1
                    state["log"]["acts"] += sum(call.get("function", {}).get("name") != "dismiss" for call in calls)
                    if state.pop("reported", None) is not None:
                        view["stats"]["tokens"] = [state["last_usage"], len(segment.render(view))]
                    if on_output is not None:
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
                    writable()
                    segment.record_results(view, source, [native], persist=persist)
                    if binding is not None:
                        tool_modules.touch(binding, result.name)

            def results(source, _results, _native):
                with guard:
                    writable()
                    segment.record_results(view, source, [], complete=True, persist=persist)
                    state["used"] = segment.context_size(view["stats"].get("tokens"), segment.render(view))
                    state["log"]["peak"] = max(state["log"].get("peak", 0), state["used"])
                    if after_round is not None and not stopped():
                        after_round(state)

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
            session.on_tool_start = tool_start
            session.on_tool_result = tool_result
            session.on_results = results
            session.on_action = lambda reference: state.update(action=reference)
            session.should_stop = stopped
            session.turn_done = lambda: state["end"]
            session.add_context_provider(assemble)
            session.add_hint(chat._agent_hint)
            for hint in hints:
                session.add_hint(hint)
            session.add_hint(lambda: f"现在是 {chat._screen.clock()}。")
            session.add_hint(f"【本次目的】{goal}")
            # WHY: observer is an internal assistant; its live words must not
            # become the central agent's terminal self-talk (2026-10-29).
            session.chat(recall_func=collect, stream=kind != "observer")
            if state.get("failure"):
                raise state["failure"]
        finally:
            image.end_conversation(ledger)
            context.set_current(previous_event)
            context.set_agent_mode(previous_mode)
            _wake.reset(token)

    def invoke():
        try:
            execute()
        finally:
            finished.set()

    failure = None
    deferred = False

    def finalize():
        with guard:
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
        append({"kind": "goal", "goal": goal, "run_kind": kind})
        if timeout is None:
            invoke()
        else:
            scope = contextvars.copy_context()
            watchdog.run(lambda: scope.run(invoke), float(timeout))
        return view["ctx"], "".join(pieces).strip()
    except BaseException as error:
        state["cancelled"] = True
        failure = f"{type(error).__name__}: {error}"
        deferred = timeout is not None and isinstance(error, TimeoutError) and not finished.is_set()
        raise
    finally:
        state["closed"] = True
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
