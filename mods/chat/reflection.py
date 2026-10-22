"""One nightly environment activation on the ordinary Center and journal."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import time
from zoneinfo import ZoneInfo

from apscheduler.jobstores.base import JobLookupError

from mods import log
from .center import _assembly_lock, require_assembly


_stream = log.stream("agent")
_PROMPT = (
    "现在是夜间自检时间。这是你自身的一次普通环境激活，不是新的主体，也不要求向QQ发送消息。"
    "可以抽查自上次自检以来的经历；首次优先看过去24小时：来源原件、主循环及选材实际发出的请求、"
    "工具返回、长期笔记的版本与引用。"
    "目录、计划、自己的描述和自评都不等于实际结果；区分外部事实、自身推断与尚未确认的动作。"
    "发现值得验证的问题时，可以沿已有exec_code在隔离目录中做小实验，保留可复查的证据，"
    "不能凭自评认定成功。可以更新理解、笔记或提出后续行动，也可以选择不改。"
    "不强制固定报告或修改；没有需要对外沟通的内容时，直接结束即可。"
)


def _job_id(c):
    return "center-reflection-" + sha256(c.scope_id.encode("utf-8")).hexdigest()


def _window(event):
    window = event.get("window")
    if not isinstance(window, (tuple, list)) or len(window) != 2 or window[0] not in {"group", "private"}:
        return None
    target = window[1]
    if isinstance(target, bool) or not isinstance(target, (int, str)):
        return None
    if isinstance(target, str) and (not target.isascii() or not target.isdecimal()):
        return None
    return tuple(window) if int(target) > 0 else None


def _recent_window(c):
    # WHY: a scheduled wake must not move the preferred destination away from
    # the latest real chat activation merely by starting another owner itself.
    for kinds in ({"activation"}, {"owner_start", "revisit_delivery", "resume", "environment_activation"}):
        for event in reversed(c.events):
            if event["kind"] in kinds and (window := _window(event)) is not None:
                return window
    return None


def deliver(c, now=None, *, timezone="Asia/Shanghai", assembly=None):
    """Claim this local date once, then wake unlocked; now is a Unix timestamp.

    A missing activity window or an already delivered date returns None. The
    environment fact consumes the date even if waking raises or the process
    exits before wake. The scheduler never owns completion or retry state.
    """
    day = datetime.fromtimestamp(time.time() if now is None else now, ZoneInfo(timezone)).date().isoformat()
    if assembly is None:
        assembly = c._assembly
    with _assembly_lock, c.lock:
        require_assembly(c, assembly)
        c.ensure_available()
        if c._closing:
            raise RuntimeError("center is closing")
        if any(event["kind"] == "environment" and event.get("source") == "reflection"
               and event.get("type") == "nightly" and isinstance(event.get("payload"), dict)
               and event["payload"].get("day") == day for event in c.events):
            return None
        window = _recent_window(c)
        if window is None:
            return None
        event = c.post_event("reflection", "nightly", {"day": day, "timezone": timezone, "prompt": _PROMPT},
                             window=window, wake=True, assembly=assembly)
    result = {"event": event, "wake_status": "called"}
    try:
        c.wake(window, assembly=assembly, quiet=True)
    except Exception as error:
        # WHY: wake can start work before raising. The durable day claim is
        # final even when the outcome is unknown; retrying could duplicate it.
        result.update(wake_status="unknown", error=type(error).__name__)
        _stream.warning("nightly reflection wake outcome unknown (%s)", type(error).__name__)
    return result


def install(c, scheduler, *, hour=4, timezone="Asia/Shanghai"):
    """Install the next cron occurrence, without a startup catch-up activation."""
    if scheduler is None:
        raise ValueError("nightly reflection requires a scheduler")
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("reflection hour must be an integer from 0 to 23")
    ZoneInfo(timezone)
    assembly = c._assembly
    with _assembly_lock, c.lock:
        require_assembly(c, assembly)
        c.ensure_available()
        if c._closing:
            raise RuntimeError("center is closing")
        job_id = _job_id(c)
        scheduler.add_job(
            deliver, "cron", args=(c,), kwargs={"assembly": assembly, "timezone": timezone},
            hour=hour, minute=0, second=0, timezone=timezone,
            id=job_id, replace_existing=True, coalesce=True, max_instances=1, misfire_grace_time=60,
        )
        return job_id


def uninstall(c, scheduler):
    """Remove this installation's job before detaching, without touching its journal."""
    if scheduler is None:
        return
    with _assembly_lock, c.lock:
        job = scheduler.get_job(_job_id(c))
        if job is None or not job.args or job.args[0] is not c or job.kwargs.get("assembly") is not c._assembly:
            return
        try:
            scheduler.remove_job(job.id)
        except JobLookupError:
            pass
