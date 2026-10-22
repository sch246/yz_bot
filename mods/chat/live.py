"""Install the journal reader before ingress is dispatched; keep its jobs local."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys
import threading

from apscheduler.jobstores.base import JobLookupError
from tzlocal import get_localzone_name

from mods import context, identity, llm, log, message, storage
from .center import Center, _assembly_lock, isolated


_stream = log.stream("agent")
_installation = None
_worker = None
_stopped = threading.Event()


def _send(text, window):
    return message.send(text, **{"group_id" if window[0] == "group" else "user_id": window[1]}).result(timeout=20)


def start():
    """Use existing identity/model settings; no experiment credentials or ledger."""
    global _installation, _worker
    from mods import chat, chatlog, scheduler

    if _installation is not None:
        raise RuntimeError("live center is already installed")
    options = storage.get("", "agent").get("memory", {})
    if not isinstance(options, dict):
        raise ValueError("agent.memory must be a dict")
    selection = chat.get_model()
    names, complete = chatlog._bot_identities()
    runtime = Center(
        root=Path(storage.root_path).parent / "agent-memory",
        archive_root=chatlog.rootfile, source_namespace="yuzu-center-v1", source_format="v1",
        bot_id=identity.bot_id(), bot_names=names, names_complete=complete,
        timezone=get_localzone_name(), client=llm.get_client(), send_sink=_send,
        request_limit=None, wake_request_limit=options.get("wake_requests"),
        selector_model=options.get("selector_model", selection),
        librarian_model=options.get("librarian_model", selection),
        recent_count=options.get("recent_count", 16),
        role_request_limit=options.get("role_requests", 4),
    )
    # WHY: scheduler is already running during on_load. Recovered date jobs may
    # be overdue; do not let them enter a half-loaded tool environment.
    manager = isolated(runtime)
    manager.__enter__()
    _installation = manager
    _stopped.clear()
    def after_boot():
        from mods import wait_booted
        from . import reflection, revisit

        while not _stopped.is_set():
            if wait_booted(0.1):
                break
        else:
            return
        try:
            with _assembly_lock, runtime.lock:
                if _stopped.is_set() or runtime._closing:
                    return
                runtime.scheduler = scheduler.get_scheduler()
                revisit.project(runtime, runtime.scheduler)
                if options.get("nightly", True):
                    reflection.install(runtime, runtime.scheduler,
                                       hour=options.get("reflection_hour", 4),
                                       timezone=options.get("reflection_timezone", "Asia/Shanghai"))
                    _stream.info("夜间自检已安排，每日 %s:00 (%s)",
                                 options.get("reflection_hour", 4),
                                 options.get("reflection_timezone", "Asia/Shanghai"))
            # Recover only pending facts from this journal. An ordinary manual
            # restart does not invent a new conversation or replay old actions.
            pending = runtime.pending()
            if pending and not _stopped.is_set():
                runtime.wake(tuple(pending[0]["window"]), assembly=runtime._assembly,
                             quiet=all(e.get("reason") == "reflection:nightly" for e in pending))
        except Exception:
            _stream.exception("中心定时/待处理激活恢复失败；未回退旧循环")

    # The normal smoke still installs the real reader, but never launches a
    # model or date job as a side effect of checking module lifecycle.
    try:
        if not runtime.events:
            runtime.append("installation", reason="journal reader enabled; legacy archives and event_stream retained",
                           legacy_pending="not replayed", archive_history="available through explicit lookup")
        _stream.info("中心记忆循环已安装；前置选材、轮后整理和运行轨迹可用")
        if "--smoke" not in sys.argv and "-l" not in sys.argv and "--log-only" not in sys.argv:
            _worker = threading.Thread(target=after_boot, name="center-after-boot", daemon=True)
            _worker.start()
    except BaseException:
        # A failed on_load is not in the loader's exit list; undo the install
        # here so later maintenance cannot inherit a half-installed reader.
        _stopped.set()
        manager.__exit__(*sys.exc_info())
        _installation, _worker = None, None
        raise
    return runtime


def stop():
    """Gate new owners, drain current work, then detach before storage exits."""
    global _installation, _worker
    from . import reflection
    from .center import active

    manager = _installation
    if manager is None:
        return
    _stopped.set()
    runtime = active()
    with _assembly_lock, runtime.lock:
        runtime._closing = True
        if runtime.scheduler is not None:
            reflection.uninstall(runtime, runtime.scheduler)
            prefix = "center-revisit-" + sha256(runtime.scope_id.encode()).hexdigest() + "-"
            for job in runtime.scheduler.get_jobs():
                if job.id.startswith(prefix):
                    try:
                        job.remove()
                    except JobLookupError:
                        pass
    context.cancel_turn(("agent", 0))
    context.wait_turn_end(("agent", 0))
    if _worker is not None and _worker is not threading.current_thread():
        _worker.join()
    manager.__exit__(None, None, None)
    _installation, _worker = None, None
