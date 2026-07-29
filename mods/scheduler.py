"""The process-wide APScheduler instance.

Business modules own their jobs.  This module only owns scheduler lifecycle.
"""

from __future__ import annotations

from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

from mods import INFRA


PHASE = INFRA
LOAD_AFTER = ("message", "storage")

scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    if scheduler is None or not scheduler.running:
        raise RuntimeError("scheduler 尚未加载或已经关闭")
    return scheduler


def on_load(_ctx: dict[str, Any] | None = None) -> None:
    global scheduler
    instance = BackgroundScheduler()
    instance.start()
    scheduler = instance


def on_exit() -> None:
    global scheduler
    instance, scheduler = scheduler, None
    if instance is not None and instance.running:
        print("Shutting down scheduler...")
        instance.shutdown(wait=True)
