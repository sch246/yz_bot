"""One cooperative action worker for an explicitly installed isolated Center.

``step(description, cancelled, report)`` runs outside the model thread and all
locks. It owns its physical loop and must poll ``cancelled()``. ``report(dict,
wake=False)`` records deliberate observations, not every physical tick. A step
returns a JSON dict; status blocked/failed/unknown stops the remaining steps.
An exception is a failed execution with unknown side effects, never a retry.

The journal is the only durable task authority. Queues and cancellation flags
are disposable. Reopening marks unfinished execution unknown and drops waiting
work with a cancellation receipt; neither path replays a side effect. Call
``close()`` before detaching the Center; it joins cooperative execution.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
import json
import math
import threading
from uuid import uuid4

from .center import _assembly_lock, require_assembly


_TERMINAL = {"completed", "failed", "cancelled", "unknown"}


def _json(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


class Actions:
    def __init__(self, *, center, step, window, tick_seconds=0.05):
        if not callable(step):
            raise ValueError("an explicit action step function is required")
        if not isinstance(window, tuple) or len(window) != 2:
            raise ValueError("action window must be a two-part tuple")
        if (isinstance(tick_seconds, bool) or not isinstance(tick_seconds, (int, float))
                or not math.isfinite(tick_seconds) or tick_seconds <= 0):
            raise ValueError("tick_seconds must be positive and finite")
        self.center, self.step, self.window = center, step, window
        self.tick_seconds = tick_seconds
        self._assembly = center._assembly
        self._condition = threading.Condition(threading.RLock())
        self._queue = deque()
        self._current = None
        self._callback = None
        self._closed = False
        self._error = None
        self._thread = threading.Thread(target=self._work, name="center-actions", daemon=True)
        # WHY: recovery cannot distinguish an interrupted side effect from an
        # unexecuted one. Only a human/new explicit submission can retry it.
        with _assembly_lock, center.lock, self._condition:
            self._available()
            workers = getattr(center, "_action_workers", {})
            previous = workers.get(window)
            if (previous is not None and previous._assembly is self._assembly
                    and previous._thread.is_alive()):
                raise RuntimeError("an action worker already owns this window")
            for task in self._tasks().values():
                if task["status"] not in _TERMINAL:
                    started = task.get("started", False)
                    self._post("unknown" if started else "cancelled", task["task_id"],
                               reason="recovered unfinished execution" if started
                               else "waiting work is not restored", wake=True)
            center._action_workers = {**workers, window: self}
            self._thread.start()

    def _available(self):
        """Caller holds assembly, center, then condition locks, in that order."""
        try:
            require_assembly(self.center, self._assembly)
            self.center.ensure_available()
            if self._error is not None:
                raise RuntimeError("action worker stopped") from self._error
        except BaseException as error:
            self._halt(error)
            raise

    def _halt(self, error):
        self._error = self._error or error
        self._queue.clear()
        if self._current is not None:
            self._current["cancel"].set()
        self._condition.notify_all()

    def _post(self, kind, task_id, *, wake=False, **payload):
        self._available()
        try:
            return self.center.post_event(
                "actions", kind, dict(task_id=task_id, **payload), window=self.window,
                wake=wake, assembly=self._assembly,
            )
        except BaseException as error:
            self._halt(error)
            raise

    def _tasks(self):
        tasks = {}
        for event in self.center.events:
            if (event["kind"] != "environment" or event.get("source") != "actions"
                    or tuple(event.get("window", ())) != self.window):
                continue
            payload, kind = event["payload"], event["type"]
            task_id = payload["task_id"]
            if kind == "submitted":
                tasks[task_id] = dict(task_id=task_id, status="queued", started=False,
                                      steps=deepcopy(payload["steps"]), event_id=event["id"])
            elif task_id in tasks:
                task = tasks[task_id]
                task.update(deepcopy(payload), event_id=event["id"])
                if kind == "started":
                    task["started"] = True
                # Step result dicts may have their own status, kept inside result.
                task["status"] = "running" if kind in {"started", "progress"} else kind
        return tasks

    def submit(self, steps, mode="append"):
        """Accept a task without waiting for execution.

        append keeps the queue; replace cancels waiting tasks but retains the
        current task; now requests current cancellation and prepends the new
        task to the existing queue. Cancellation is cooperative.
        """
        if mode not in {"append", "replace", "now"}:
            raise ValueError("mode must be append, replace or now")
        if not isinstance(steps, list) or not steps or any(not isinstance(s, dict) for s in steps):
            raise ValueError("steps must be a nonempty list of JSON dicts")
        steps = _json(steps)
        with _assembly_lock, self.center.lock, self._condition:
            self._available()
            if self._closed:
                raise RuntimeError("action worker is closed")
            task_id = "A" + uuid4().hex
            receipt = self._post("submitted", task_id, steps=steps, mode=mode)
            if mode == "replace":
                self._clear_waiting("replaced by " + task_id)
            elif mode == "now":
                self._cancel_current("interrupted by " + task_id)
            task = dict(task_id=task_id, steps=steps, cancel=threading.Event(), reason=None)
            if mode == "now":
                self._queue.appendleft(task)
            else:
                self._queue.append(task)
            self._condition.notify_all()
            return dict(task_id=task_id, status="accepted", event_id=receipt["id"])

    def _clear_waiting(self, reason):
        while self._queue:
            task = self._queue[0]
            self._post("cancelled", task["task_id"], reason=reason, executed_steps=0, wake=True)
            self._queue.popleft()

    def _cancel_current(self, reason):
        if self._current is not None and not self._current["cancel"].is_set():
            self._post("cancel_requested", self._current["task_id"], reason=reason)
            self._current["reason"] = reason
            self._current["cancel"].set()

    def stop(self, reason="stopped"):
        """Drop waiting work and request cancellation; returning is not completion."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("stop reason must be nonempty text")
        with _assembly_lock, self.center.lock, self._condition:
            self._available()
            self._clear_waiting(reason)
            self._cancel_current(reason)
            self._condition.notify_all()
            return dict(status="stop_requested", current=self._current["task_id"]
                        if self._current is not None else None)

    def status(self):
        """Detached journal projection, also readable after persistence failure."""
        with self.center.lock, self._condition:
            return dict(closed=self._closed, worker_alive=self._thread.is_alive(),
                        error=type(self._error).__name__ if self._error else None,
                        current=self._current["task_id"] if self._current else None,
                        queued=[task["task_id"] for task in self._queue],
                        tasks=list(self._tasks().values()))

    def close(self):
        """Stop and join; retain journal/assembly failures without raising them.

        A skill that ignores cancellation can delay this call. Failed writes
        remain in the Center's failure gate and this worker's status.
        """
        if threading.current_thread() is self._thread:
            raise RuntimeError("action worker cannot join itself")
        try:
            with _assembly_lock, self.center.lock, self._condition:
                if not self._closed:
                    try:
                        self._available()
                        self._clear_waiting("worker closed")
                        self._cancel_current("worker closed")
                    except Exception as error:
                        # WHY: close runs in the owner's finally block. A closed
                        # persistence gate must not mask its original failure or
                        # prevent cooperative cleanup; no receipt can be trusted
                        # after that gate closes, so leave journal outcomes open.
                        self._halt(error)
                    finally:
                        self._closed = True
                        self._queue.clear()
                        if self._current:
                            self._current["cancel"].set()
                        self._condition.notify_all()
        finally:
            self._thread.join()

    def _cancelled(self, task):
        with _assembly_lock, self.center.lock, self._condition:
            try:
                self._available()
            except Exception:
                return True
            return self._current is not task or self._closed or task["cancel"].is_set()

    def _report(self, task, callback, index, payload, *, wake=False):
        if not isinstance(payload, dict):
            raise ValueError("progress must be a JSON dict")
        payload = _json(payload)
        with _assembly_lock, self.center.lock, self._condition:
            self._available()
            if self._callback is not callback or self._current is not task:
                raise RuntimeError("action progress callback is no longer current")
            if task["cancel"].is_set() or self._closed:
                return None
            return self._post("progress", task["task_id"], step_index=index,
                              progress=payload, wake=bool(wake))

    def _execute(self, task):
        results = []
        for index, description in enumerate(task["steps"]):
            if self._cancelled(task):
                break
            callback = object()
            with self._condition:
                self._callback = callback
            # Bind each callback's index/token: a skill may retain it after return.
            def report(payload, *, wake=False, _token=callback, _index=index):
                return self._report(task, _token, _index, payload, wake=wake)

            try:
                result = self.step(deepcopy(description), lambda: self._cancelled(task), report)
                if not isinstance(result, dict):
                    raise ValueError("action step must return a JSON dict")
                result = _json(result)
            except Exception as error:
                with _assembly_lock, self.center.lock, self._condition:
                    self._callback = None
                    self._post("failed", task["task_id"], failed_step=index, results=results,
                               error=type(error).__name__, side_effects="unknown", wake=True)
                    self._current = None
                return
            with _assembly_lock, self.center.lock, self._condition:
                self._callback = None
                self._available()
                results.append(result)
                outcome = result.get("status")
                if outcome in ("blocked", "failed", "unknown"):
                    self._post("unknown" if outcome == "unknown" else "failed", task["task_id"],
                               failed_step=index, results=results, reason=outcome,
                               side_effects="unknown", wake=True)
                    self._current = None
                    return
                # At most one automatic observation per logical step, regardless
                # of how many physical iterations the supplied skill performed.
                self._post("progress", task["task_id"], step_index=index,
                           executed_steps=len(results), result=result)
        with _assembly_lock, self.center.lock, self._condition:
            self._post("cancelled" if task["cancel"].is_set() or self._closed else "completed",
                       task["task_id"], results=results, executed_steps=len(results),
                       reason=task["reason"], wake=True)
            self._current = None

    def _work(self):
        try:
            while True:
                with self._condition:
                    if not self._queue and not self._closed and self._error is None:
                        self._condition.wait(self.tick_seconds)
                    if self._closed or self._error is not None:
                        return
                with _assembly_lock, self.center.lock, self._condition:
                    self._available()
                    if not self._queue or self._closed:
                        continue
                    task = self._queue.popleft()
                    self._current = task
                    self._post("started", task["task_id"])
                self._execute(task)
                with self._condition:
                    self._current = None
                    self._callback = None
        except BaseException as error:
            with self._condition:
                self._callback = None
                self._halt(error)
