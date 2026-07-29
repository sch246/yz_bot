"""Persistent cron-like tasks checked by the shared scheduler."""

from __future__ import annotations

from datetime import datetime
import logging
import re
import traceback
from typing import Any, Iterable

from mods import LATE, is_available
from mods import message, op, py, scheduler, storage
from mods.command import command


PHASE = LATE
LOAD_AFTER = ("py", "scheduler", "storage")
JOB_ID = "todo-minute-check"
logger = logging.getLogger(__name__)
_reported_invalid: set[str] = set()

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


def _current() -> dict[str, Any]:
    from mods import context

    return context.current()


def _format(index: int, todo: dict[str, Any]) -> str:
    return f'{index}: {todo.get("cond")} {todo.get("expr")}'


def _parse_atom(atom: str, minimum: int, maximum: int) -> set[int]:
    if not atom:
        raise SyntaxError("cron 字段为空")
    base, slash, step_text = atom.partition("/")
    step = int(step_text) if slash else 1
    if step <= 0:
        raise SyntaxError("cron 步长必须大于 0")
    if base == "*":
        start, end = minimum, maximum
    elif "-" in base:
        start_text, end_text = base.split("-", 1)
        start, end = int(start_text), int(end_text)
    else:
        start = end = int(base)
    if not minimum <= start <= end <= maximum:
        raise SyntaxError(f"cron 范围必须在 {minimum}..{maximum} 内")
    return set(range(start, end + 1, step))


def _field_values(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for atom in field.split(","):
        values.update(_parse_atom(atom, minimum, maximum))
    return values


def validate_cron_expr(expr: str) -> bool:
    parts = expr.split()
    if len(parts) != 5:
        raise SyntaxError(f"cron 表达式必须恰好有 5 个字段，得到 {len(parts)} 个")
    for field, (minimum, maximum) in zip(parts, _RANGES):
        _field_values(field, minimum, maximum)
    return True


def _read_cond(text: str) -> tuple[str, str]:
    parts = text.strip().split(maxsplit=5)
    if len(parts) < 6:
        raise SyntaxError("需要 5 段 cron 条件和一个表达式")
    cond, expr = " ".join(parts[:5]), parts[5]
    validate_cron_expr(cond)
    if not expr.strip():
        raise SyntaxError("表达式为空")
    return cond, expr


def _check(cond: str, now: datetime | None = None) -> bool:
    now = datetime.now() if now is None else now
    current = (now.minute, now.hour, now.day, now.month, now.weekday())
    return all(
        value in _field_values(field, minimum, maximum)
        for field, value, (minimum, maximum) in zip(cond.split(), current, _RANGES)
    )


def _read_range(value: str) -> Iterable[int]:
    for part in value.split(","):
        if "-" in part:
            start, end = map(int, part.split("-", 1))
            yield from range(start, end + 1)
        else:
            yield int(part)


def _is_safe(expr: str) -> bool:
    try:
        return isinstance(eval(expr, {"__builtins__": {}}, {}), str)
    except Exception:
        return False


def _split_once(text: str) -> tuple[str, str]:
    parts = text.strip().split(maxsplit=1)
    return (parts[0], parts[1] if len(parts) > 1 else "") if parts else ("", "")


def get_todo_list(msg: dict[str, Any] | None = None) -> list[dict[str, str]]:
    if msg is None:
        msg = _current()
    if "group_id" in msg:
        return storage.get("todo_list/groups", str(msg["group_id"]), list)
    return storage.get("todo_list/users", str(msg["user_id"]), list)


@command
def run(text: str) -> str:
    """查看和管理当前窗口的 Cron 计划任务。

    添加：.todo add <分 时 日 月 周> <表达式>；无参数列出任务。
    维护：.todo del <索引|范围|*>；set <索引> <五段时间> <表达式>；move <源索引> <目标索引>。普通用户只能安排安全字符串结果。
    """
    msg = _current()
    todos = get_todo_list(msg)
    if not text.strip():
        return "\n".join(_format(i, todo) for i, todo in enumerate(todos)) or "计划任务为空"
    operation, body = _split_once(text)
    try:
        if operation == "add":
            cond, expr = _read_cond(body)
            if not op.is_op(msg) and not _is_safe(expr):
                return "字符串以外的任务需要管理员权限"
            todo = {"cond": cond, "expr": expr}
            todos.insert(0, todo)
            return _format(0, todo)
        if operation == "del":
            value = body.strip()
            if value == "*":
                todos.clear()
                return "删除了全部计划任务"
            indexes = sorted(set(_read_range(value)), reverse=True)
            removed: list[str] = []
            for index in indexes:
                if 0 <= index < len(todos):
                    todos.pop(index)
                    removed.insert(0, str(index))
            return f'删除了 {",".join(removed)}'
        if operation == "set":
            index_text, rest = _split_once(body)
            index = int(index_text)
            cond, expr = _read_cond(rest)
            if not op.is_op(msg) and not _is_safe(expr):
                return "字符串以外的任务需要管理员权限"
            todos[index] = {"cond": cond, "expr": expr}
            return _format(index, todos[index])
        if operation == "move":
            source_text, rest = _split_once(body)
            target_text, extra = _split_once(rest)
            if extra:
                raise SyntaxError("move 输入了多余参数")
            source, target = int(source_text), int(target_text)
            todos.insert(target, todos.pop(source))
            return _format(target, todos[target])
        return run.__doc__ or ""
    except Exception:
        logger.warning("todo 命令解析失败\n%s", traceback.format_exc())
        return run.__doc__ or ""


def _eval_and_send(expr: str, *, group_id: str | None = None, user_id: str | None = None) -> None:
    result = eval(expr, py.loc)
    if result is None:
        return
    if group_id is not None:
        message.send(result, group_id=int(group_id))
    elif user_id is not None:
        message.send(result, user_id=int(user_id))


def _run_tasks(values: dict[str, Any], *, group: bool) -> None:
    for identifier, todos in list(values.items()):
        if not isinstance(todos, list):
            continue
        for todo in list(todos):
            try:
                cond, expr = str(todo["cond"]), str(todo["expr"])
                if _check(cond):
                    _eval_and_send(
                        expr,
                        group_id=identifier if group else None,
                        user_id=None if group else identifier,
                    )
            except Exception as error:
                label = f"{identifier}:{todo.get('cond')!r}"
                if label not in _reported_invalid:
                    _reported_invalid.add(label)
                    logger.error("todo 任务执行失败 (%s)：%s", label, error, exc_info=True)


def _tick() -> None:
    _run_tasks(storage.get_namespace("todo_list/groups"), group=True)
    _run_tasks(storage.get_namespace("todo_list/users"), group=False)


def on_load(_ctx: dict[str, Any] | None = None) -> None:
    missing = [name for name in ("py", "scheduler", "storage") if not is_available(name)]
    if missing:
        raise RuntimeError("todo 依赖未加载: " + ", ".join(missing))
    storage.get_namespace("todo_list/groups")
    storage.get_namespace("todo_list/users")
    scheduler.get_scheduler().add_job(
        _tick,
        "cron",
        minute="*",
        second=0,
        id=JOB_ID,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )


def on_exit() -> None:
    instance = scheduler.scheduler
    if instance is not None and instance.running:
        try:
            instance.remove_job(JOB_ID)
        except Exception:
            pass
