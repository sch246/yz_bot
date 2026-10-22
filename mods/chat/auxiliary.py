"""Bind explicit vision/delegate calls to one installed Center authority."""

from contextlib import contextmanager
from uuid import uuid4

from mods import llm
from .center import _assembly_lock, _request_state, active, require_assembly


def capture(client):
    """Capture before crossing a thread boundary; never inherit main evidence."""
    with _assembly_lock:
        c = active()
        parent = _request_state.get()
        if parent is not None:
            require_assembly(parent["center"], parent["assembly"])
        if c is None:
            return None
        c.ensure_available()
        if client is not c.client:
            raise llm.PersistenceError("center auxiliary call requires its installed LLM client")
        return c, c._assembly, parent["owner"] if parent is not None else None


def _available(captured):
    c, assembly, _ = captured
    require_assembly(c, assembly)
    c.ensure_available()
    if c._closing:
        raise llm.RequiredContextError("center is closing")
    return c


def usage(captured, state, chunk):
    if captured is not None and chunk.total_tokens:
        with _assembly_lock:
            c = _available(captured)
            c.append("usage", owner=state["owner"], role=state["role"], request=state["request"],
                     prompt_tokens=chunk.prompt_tokens, completion_tokens=chunk.completion_tokens,
                     cached_tokens=chunk.cached_tokens)


def output(captured, state, assistant, calls):
    with _assembly_lock:
        return _available(captured)._output(state, assistant, calls)


@contextmanager
def bind(captured, role, *, session=None, binding=None):
    if captured is None:
        yield None
        return
    with _assembly_lock:
        c = _available(captured)
        state = dict(center=c, assembly=captured[1], owner=uuid4().hex, role=role,
                     request=None, request_count=0, cutoff=None, activations=[],
                     sources=[], note=None, materials={})
        c.append("role_start", role=role, owner=state["owner"], parent_owner=captured[2],
                 job=state["owner"], cutoff=None, note=None)
        event_start = len(c.events)
    marker = _request_state.set(state)
    outcome, error_name = "complete", None
    try:
        if session is not None:
            if session.chat_client is not c.client:
                raise llm.PersistenceError("auxiliary Chat changed the installed LLM client")
            session.fail_fast = True
            session.preserve_native = True
            session.on_output = lambda assistant, calls: output(captured, state, assistant, calls)

            def results(source, values, native):
                with _assembly_lock:
                    _available(captured)._results(state, binding, source, values)

            session.on_results = results
        yield state
    except BaseException as error:
        outcome, error_name = "failed", type(error).__name__
        raise
    finally:
        try:
            with _assembly_lock, c.lock:
                _available(captured)
                events = [e for e in c.events[event_start:] if e.get("owner") == state["owner"]]
                returned = {action for e in events if e["kind"] == "results" for action in e["actions"]}
                unknown = [e["action"] for e in events if e["kind"] == "dispatch" and e["action"] not in returned]
                if unknown:
                    c.append("unknown", owner=state["owner"], role=role, actions=unknown,
                             reason="auxiliary dispatch has no durable result")
                    c.fault(RuntimeError("auxiliary dispatch outcome is unknown"))
                    c.ensure_available()
                responded = {e["request"] for e in events if e["kind"] in {"output", "request_end"}}
                pending = [e["id"] for e in events if e["kind"] == "request" and e["id"] not in responded]
                for request in pending:
                    c.append("request_end", owner=state["owner"], role=role, request=request,
                             outcome="failed", reason=error_name or "auxiliary response did not commit")
                c.append("role_end", role=role, owner=state["owner"], job=state["owner"],
                         outcome="failed" if pending else outcome, error=error_name,
                         requests=state["request_count"])
                if pending and outcome == "complete":
                    raise RuntimeError("auxiliary response did not commit")
        finally:
            _request_state.reset(marker)
