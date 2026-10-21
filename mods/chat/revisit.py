"""Persistent self-requested revisits; scheduler jobs are disposable projections.

The center owns validation and durable append of its journal.  In particular,
``activate_delivery(delivery, window, reason)`` must append one
``revisit_delivery`` activation containing ``request=delivery['request']`` and
the complete ``delivery`` dict, under the same center lock.  An uncertain write
must close the center's persistence gate, including after process recovery.

Call ``project`` at assembly and after request/cancel.  Without a scheduler,
only explicit ``deliver_due`` calls deliver revisits.  Neither path retries a
delivery whose activation was committed, even if waking failed or the process
stopped between commit and wake.  Recovery of pending activations belongs to
the center; it must not manufacture another delivery for that request.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import math
import time
from typing import Any

from apscheduler.jobstores.base import JobLookupError


def _timestamp(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("revisit time must be a finite Unix timestamp")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("revisit time must be a finite Unix timestamp")
    # Reject dates the scheduler cannot represent before committing a request.
    datetime.fromtimestamp(value, timezone.utc)
    return value


def _state(c: Any) -> tuple[dict[str, dict], dict[str, dict]]:
    """Rebuild request and terminal records while the caller holds c.lock."""
    requests: dict[str, dict] = {}
    terminal: dict[str, dict] = {}
    for event in c.events:
        kind = event["kind"]
        if kind == "revisit_request":
            requests[event["id"]] = event
        elif kind in ("revisit_cancel", "revisit_delivery"):
            terminal[event["request"]] = event
    return requests, terminal


def request(c: Any, *, due: float, window: tuple, reason: str) -> dict:
    """Commit a self-requested revisit and return its detached journal record."""
    due = _timestamp(due)
    if not isinstance(window, tuple) or len(window) != 2:
        raise ValueError("revisit window must be a two-part window tuple")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("revisit reason must be nonempty text")
    with c.lock:
        c.ensure_available()
        return deepcopy(c.append("revisit_request", due=due, window=window, reason=reason))


def cancel(c: Any, request_id: str) -> dict:
    """Cancel before delivery; repeated cancellation returns the same record.

    A delivered request cannot be undone here: its activation already belongs
    to the center's ordinary acknowledgement/disposition path.
    """
    with c.lock:
        c.ensure_available()
        requests, terminal = _state(c)
        if request_id not in requests:
            raise KeyError("unknown revisit request")
        if request_id in terminal:
            return deepcopy(terminal[request_id])
        return deepcopy(c.append("revisit_cancel", request=request_id))


def pending(c: Any) -> list[dict]:
    """Return detached requests still awaiting delivery, including overdue ones.

    This observation remains available when the persistence gate is closed.
    """
    with c.lock:
        requests, terminal = _state(c)
        return deepcopy(sorted(
            (entry for key, entry in requests.items() if key not in terminal),
            key=lambda entry: entry["due"],
        ))


def deliver_due(c: Any, now: float | None = None) -> list[dict]:
    """Commit due activations once, then invoke the ordinary wake path unlocked.

    A wake exception is an unknown outcome, never evidence that no work began.
    Results describe only activations committed by this call.  Persistence
    errors propagate immediately and prevent subsequent wake/activation work.
    """
    now = _timestamp(time.time() if now is None else now)
    results = []
    while True:
        with c.lock:
            c.ensure_available()
            due = next((entry for entry in pending(c) if entry["due"] <= now), None)
            if due is None:
                return results
            delivery = {"request": due["id"], "id": "revisit:" + due["id"], "due": due["due"]}
            window = tuple(due["window"])
            activation = c.activate_delivery(delivery, window, due["reason"])
            result = {"request": due["id"], "delivery": delivery, "activation": deepcopy(activation)}
        try:
            c.wake(window)
        except Exception as error:
            # WHY: wake can start a worker before raising.  The committed
            # delivery consumes this request even when its outcome is unknown.
            with c.lock:
                c.append(
                    "revisit_wake_error", request=due["id"], delivery=delivery["id"],
                    status="unknown", error=type(error).__name__,
                )
            result.update(wake_status="unknown", error=type(error).__name__)
        else:
            result["wake_status"] = "called"
        results.append(result)


def project(c: Any, scheduler: Any) -> list[str]:
    """Rebuild this center's date jobs; call after journal changes and at boot.

    The supplied scheduler is the existing BackgroundScheduler instance.  No
    job stores completion state.  Stale or concurrent callbacks are harmless
    because ``deliver_due`` claims requests under the center lock.
    """
    if scheduler is None:
        raise ValueError("automatic revisits require a scheduler; use deliver_due manually")
    prefix = "center-revisit-" + sha256(c.scope_id.encode("utf-8")).hexdigest() + "-"
    with c.lock:
        c.ensure_available()
        requests = pending(c)
        wanted = {prefix + entry["id"] for entry in requests}
        for job in scheduler.get_jobs():
            if job.id.startswith(prefix) and job.id not in wanted:
                try:
                    job.remove()
                except JobLookupError:
                    # A date job may have fired since get_jobs() took its view.
                    pass
        for entry in requests:
            scheduler.add_job(
                deliver_due, "date", args=(c,),
                run_date=datetime.fromtimestamp(entry["due"], timezone.utc),
                id=prefix + entry["id"], replace_existing=True,
                misfire_grace_time=None,
            )
        return sorted(wanted)
