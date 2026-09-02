"""Persistent one-shot tasks restored into the shared scheduler."""

from __future__ import annotations

from datetime import datetime
from itertools import count
import re
from threading import Lock
import time
import traceback
from typing import Any, Callable
from uuid import uuid4

from mods import LATE, is_available
from mods import op, py, scheduler, storage
from mods.command import command


PHASE = LATE
LOAD_AFTER = ("py", "scheduler", "storage")

_id_iter = count()
_locks: dict[tuple[bool, int], Lock] = {}
_jobs: dict[int, str] = {}


def _current() -> dict[str, Any]:
    from mods import context

    return context.current()


def msg_id(msg: dict[str, Any]) -> tuple[bool, int]:
    if "group_id" in msg:
        return True, int(msg["group_id"])
    return False, int(msg["user_id"])


def get_lock(is_group: bool, identifier: int) -> Lock:
    return _locks.setdefault((is_group, int(identifier)), Lock())


def get_later_list(msg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if msg is None:
        msg = _current()
    if "group_id" in msg:
        return storage.get("later_list/groups", str(msg["group_id"]), list)
    return storage.get("later_list/users", str(msg["user_id"]), list)


def later(
    seconds: float,
    action: Callable[..., Any],
    argument: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
) -> Callable[[], None]:
    job = scheduler.get_scheduler().add_job(
        action,
        "date",
        run_date=datetime.fromtimestamp(time.time() + max(0.0, seconds)),
        args=argument,
        kwargs={} if kwargs is None else kwargs,
        id=f"later-call-{uuid4().hex}",
        misfire_grace_time=None,
    )

    def cancel_job() -> None:
        try:
            job.remove()
        except Exception:
            pass

    return cancel_job


def at(
    timestamp: float,
    action: Callable[..., Any],
    argument: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
) -> Callable[[], None]:
    return later(timestamp - time.time(), action, argument, kwargs)


def _list_pop(sequence: int, msg: dict[str, Any]) -> dict[str, Any]:
    tasks = get_later_list(msg)
    with get_lock(*msg_id(msg)):
        for index, task in enumerate(tasks):
            if task.get("seq") == sequence:
                return tasks.pop(index)
    return {}


def _action(sequence: int, expr: str, msg: dict[str, Any]) -> None:
    _list_pop(sequence, msg)
    _jobs.pop(sequence, None)

    def repeat_here(value: str) -> tuple[int, str]:
        return enter(value, expr, msg)

    future = py.run(
        expr,
        msg,
        skip_op=True,
        insert={"later_repeat": repeat_here, "repeat": repeat_here},
    )
    try:
        # Keep the scheduler job alive until the dynamic worker finishes so
        # scheduler.shutdown(wait=True) also covers a firing later task.
        future.result()
    finally:
        # py's insert names share its one persistent loc.  Restore the stable
        # public functions without overwriting a newer concurrent closure.
        if py.loc.get("later_repeat") is repeat_here:
            py.loc["later_repeat"] = repeat
        if py.loc.get("repeat") is repeat_here:
            py.loc["repeat"] = repeat


def repeat(value: str) -> tuple[int, str]:
    """Reschedule the currently executing task; intended for task expressions."""
    expr = py.loc.get("_py_expr")
    msg = py.loc.get("_py_msg")
    if expr is None or msg is None:
        raise RuntimeError("repeat() 只能在正在触发的 later 任务中调用")
    return enter(value, expr, msg)


def _read_timestamp(value: str) -> float:
    return time.mktime(time.strptime(value, "%Y-%m-%d %H:%M:%S"))


def _schedule(sequence: int, when: str, expr: str, msg: dict[str, Any]) -> None:
    job_id = f"later-{sequence}"
    job = scheduler.get_scheduler().add_job(
        _action,
        "date",
        run_date=datetime.fromtimestamp(_read_timestamp(when)),
        args=(sequence, expr, msg),
        id=job_id,
        replace_existing=True,
        misfire_grace_time=None,
    )
    _jobs[sequence] = job.id


def process_later_list(is_group: bool, tasks: list[dict[str, Any]]) -> None:
    for task in list(tasks):
        try:
            when, expr, msg = task["YMD_hms"], task["expr"], task["msg"]
            sequence = next(_id_iter)
            task["seq"] = sequence
            task["cancel"] = None
            _schedule(sequence, when, expr, msg)
        except Exception:
            traceback.print_exc()


def _get_days_in_month(year: int, month: int) -> int:
    days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        days[2] = 29
    return days[month]


def normalize_time_tuple(*values: int) -> tuple[int, ...]:
    year, month, day, hour, minute, second, *rest = values
    minute, second = minute + second // 60, second % 60
    hour, minute = hour + minute // 60, minute % 60
    day, hour = day + hour // 24, hour % 24
    year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    while day > (days := _get_days_in_month(year, month)):
        day -= days
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return year, month, day, hour, minute, second, *rest


re_date = re.compile(r"^\d+-\d+(?:-\d+)?$")
re_clock = re.compile(r"^\d+:\d+(?::\d+)?$")
re_abstime = re.compile(r"^(?:\d+:\d+(?::\d+)?|\d+-\d+(?:-\d+)?(?: \d+:\d+(?::\d+)?)?)$")
re_reltime = re.compile(r"^(\d+[Yy])?(\d+M)?(\d+[Dd])?(\d+h)?(\d+m)?(\d+s)?$")


def has_time_passed(hour: int, minute: int, second: int) -> bool:
    now = time.localtime()
    target = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, hour, minute, second, 0, 0, -1))
    return time.time() > target


def read_abstime(value: str) -> str:
    parts = value.split()
    if len(parts) == 1 and re_date.fullmatch(parts[0]):
        date = list(map(int, parts[0].split("-")))
        if len(date) == 2:
            date.insert(0, time.localtime().tm_year)
        fields = date + [4, 0, 0]
    elif len(parts) == 1 and re_clock.fullmatch(parts[0]):
        clock = list(map(int, parts[0].split(":")))
        if len(clock) == 2:
            clock.append(0)
        base = time.localtime(time.time() + 86400 if has_time_passed(*clock) else time.time())
        fields = [base.tm_year, base.tm_mon, base.tm_mday, *clock]
    elif len(parts) == 2 and re_date.fullmatch(parts[0]) and re_clock.fullmatch(parts[1]):
        date = list(map(int, parts[0].split("-")))
        clock = list(map(int, parts[1].split(":")))
        if len(date) == 2:
            date.insert(0, time.localtime().tm_year)
        if len(clock) == 2:
            clock.append(0)
        fields = date + clock
    else:
        raise ValueError(f"绝对时间字符串格式错误，得到了: {value!r}")
    return time.strftime("%Y-%m-%d %H:%M:%S", (*fields, 0, 0, -1))


def read_reltime(value: str) -> str:
    match = re_reltime.fullmatch(value)
    if match is None or not any(match.groups()):
        raise ValueError(f"相对时间字符串格式错误，得到了: {value!r}")
    year = int(match.group(1)[:-1]) if match.group(1) else 0
    month = int(match.group(2)[:-1]) if match.group(2) else 0
    day = int(match.group(3)[:-1]) if match.group(3) else 0
    hour = int(match.group(4)[:-1]) if match.group(4) else 0
    minute = int(match.group(5)[:-1]) if match.group(5) else 0
    second = int(match.group(6)[:-1]) if match.group(6) else 0
    now = time.localtime()
    fields = normalize_time_tuple(
        now.tm_year + year,
        now.tm_mon + month,
        now.tm_mday + day,
        now.tm_hour + hour,
        now.tm_min + minute,
        now.tm_sec + second,
        0,
        0,
        now.tm_isdst,
    )
    return time.strftime("%Y-%m-%d %H:%M:%S", fields)


def _parse_time(value: str) -> str:
    if re_reltime.fullmatch(value) and any(re_reltime.fullmatch(value).groups()):
        return read_reltime(value)
    if re_abstime.fullmatch(value):
        return read_abstime(value)
    raise ValueError(f"时间格式错误: {value!r}")


def enter(value: str, expr: str, msg: dict[str, Any]) -> tuple[int, str]:
    when = _parse_time(value)
    tasks = get_later_list(msg)
    sequence = next(_id_iter)
    task = {"YMD_hms": when, "expr": expr, "msg": msg, "seq": sequence, "cancel": None}
    with get_lock(*msg_id(msg)):
        tasks.insert(0, task)
    _schedule(sequence, when, expr, msg)
    return sequence, when


def cancel(sequence: int, msg: dict[str, Any]) -> tuple[str, str]:
    task = _list_pop(sequence, msg)
    if not task:
        return "删除失败", "任务不存在或不在当前聊天区域"
    job_id = _jobs.pop(sequence, None)
    if job_id:
        try:
            scheduler.get_scheduler().remove_job(job_id)
        except Exception:
            pass
    return str(task.get("YMD_hms")), str(task.get("expr"))


def change(sequence: int, value: str, expr: str, msg: dict[str, Any]) -> str | None:
    when = _parse_time(value)
    tasks = get_later_list(msg)
    with get_lock(*msg_id(msg)):
        task = next((item for item in tasks if item.get("seq") == sequence), None)
        if task is None:
            return None
        task.update({"YMD_hms": when, "expr": expr, "cancel": None})
    old_job = _jobs.pop(sequence, None)
    if old_job:
        try:
            scheduler.get_scheduler().remove_job(old_job)
        except Exception:
            pass
    _schedule(sequence, when, expr, msg)
    return when


def print_list(tasks: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{task['seq']}: {task['YMD_hms']} {task['expr']}"
        for task in sorted(tasks, key=lambda item: item["YMD_hms"])
    )


def is_safe(expr: str) -> bool:
    if expr.startswith("'"):
        expr = expr.replace("\\'", "")
        if "'" not in expr[1:-1]:
            return True
    if expr.startswith('"'):
        expr = expr.replace('\\"', "")
        if '"' not in expr[1:-1]:
            return True
    return False


def _split_once(text: str) -> tuple[str, str]:
    parts = text.strip().split(maxsplit=1)
    return (parts[0], parts[1] if len(parts) > 1 else "") if parts else ("", "")


def _read_time_expr(text: str) -> tuple[str, str]:
    first, rest = _split_once(text)
    second, tail = _split_once(rest)
    combined = f"{first} {second}"
    if second and re_abstime.fullmatch(combined):
        return combined, tail
    if rest and (re_reltime.fullmatch(first) or re_abstime.fullmatch(first)):
        return first, rest
    raise SyntaxError("时间格式不符")


def later_add(text: str, msg: dict[str, Any]) -> str:
    when_text, expr = _read_time_expr(text)
    if not expr.strip():
        raise SyntaxError("表达式为空")
    if not op.is_op(msg) and not is_safe(expr):
        return "字符串以外的任务需要管理员权限"
    sequence, when = enter(when_text, expr, msg)
    return f"{sequence}: {when} {expr}"


@command
def run(text: str) -> str:
    """设置发送回当前群或私聊的一次性定时消息。

    示例：.later 10m '十分钟后提醒我'；.later 21:30 '晚上提醒我'。add 可以省略。
    相对时间支持 10s、5m、2h、1d、1M（M 是月，m 是分钟）；绝对时间支持 HH:MM、MM-DD [HH:MM]、YYYY-MM-DD [HH:MM:SS]。
    无参数列出任务；.later del <序号[,序号...]|*> 删除；.later set <序号> <时间> <表达式> 修改。
    提醒文字必须用半角单引号或双引号包住；只有管理员可以使用字符串以外的 Python 表达式。
    """
    msg = _current()
    if not text.strip():
        tasks = get_later_list(msg)
        return print_list(tasks) if tasks else "延时任务为空"
    operation, body = _split_once(text)
    try:
        if operation in ("-h", "--help"):
            return run.__doc__ or ""
        if operation == "add":
            return later_add(body, msg)
        if operation == "del":
            body = body.strip()
            tasks = get_later_list(msg)
            if body == "*":
                for task in list(tasks):
                    cancel(int(task["seq"]), msg)
                return "删除了全部计划任务"
            sequences = sorted({int(value.strip()) for value in body.split(",")})
            return "\n".join(
                f"{sequence}: {when} {expr}"
                for sequence in sequences
                for when, expr in [cancel(sequence, msg)]
            )
        if operation == "set":
            sequence_text, rest = _split_once(body)
            when_text, expr = _read_time_expr(rest)
            if not expr.strip():
                raise SyntaxError("表达式为空")
            if not op.is_op(msg) and not is_safe(expr):
                return "字符串以外的任务需要管理员权限"
            when = change(int(sequence_text), when_text, expr, msg)
            return "没有找到任务" if when is None else f"{sequence_text}: {when} {expr}"
        return later_add(text, msg)
    except Exception:
        return traceback.format_exc()


def on_load(_ctx: dict[str, Any] | None = None) -> None:
    missing = [name for name in ("py", "scheduler", "storage") if not is_available(name)]
    if missing:
        raise RuntimeError("later 依赖未加载: " + ", ".join(missing))
    groups = storage.get_namespace("later_list/groups")
    users = storage.get_namespace("later_list/users")
    for tasks in list(groups.values()):
        if isinstance(tasks, list):
            process_later_list(True, tasks)
    for tasks in list(users.values()):
        if isinstance(tasks, list):
            process_later_list(False, tasks)


def on_exit() -> None:
    instance = scheduler.scheduler
    if instance is not None and instance.running:
        for job_id in list(_jobs.values()):
            try:
                instance.remove_job(job_id)
            except Exception:
                pass
    _jobs.clear()
