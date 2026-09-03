"""A linear persisted reaction list followed by process-local captures."""

import json
import logging
import os
import re
import traceback

from mods import LATE, context, cq, is_available, log, message, msgs, op, pages, py, storage, text, thread
from mods.capture import items as capture_items
from mods.command import command
from .schema import snapshot as _snapshot
from .schema import validate_import as _validate_import


PHASE = LATE
LOAD_AFTER = ("capture", "py")

Int = r"(?:0|-?[1-9]\d*)"
Name = r"\w+"
Param = r"\S+"
All = r"[\S\s]+"
CQ = r"\[CQ:[^,\]]+(?:,[^,=]+=[^,\]]+)*\]"


def CQ_at(qq):
    return rf"\[CQ:at,qq={qq}\]"


links: list[dict] = []
_startup_snapshot = "[]"
_logger = logging.getLogger(__name__)
_stream = log.stream("link")


def on_load(_ctx) -> None:
    global links, _startup_snapshot
    if not is_available("py"):
        raise RuntimeError("link 需要已成功加载的 py")
    loaded = storage.get("", "links", list)
    errors = _validate_import(loaded)
    if errors:
        raise ValueError("links 数据无效:\n" + "\n".join(errors[:10]))
    links = loaded
    _startup_snapshot = _snapshot(links)


def get_link(name: str):
    return next((item for item in links if item["name"] == name), None)


def del_link(name: str) -> bool:
    item = get_link(name)
    if item is None:
        return False
    links.remove(item)
    return True


def _traceback_text() -> str:
    return "#" + "".join(traceback.format_exc().splitlines(True)[3:]).strip()


def _report_error(name: str, stage: str) -> None:
    _logger.exception('link "%s" %s failed', name, stage)
    message.sendmsg(_traceback_text())


def _eval_last(source: str, environment: dict):
    lines = source.splitlines(keepends=True)
    if not lines:
        return None
    exec("".join(lines[:-1]), environment)
    last = lines[-1].strip()
    if not last or last.startswith("#"):
        return None
    return eval(last, environment)


def _action_py(name: str, action: str, environment: dict) -> bool:
    if not action:
        return True
    try:
        exec(cq.unescape(action), environment)
        return True
    except Exception:
        _report_error(name, "action")
        return False


def _action_re(name: str, action: str, environment: dict, captures: dict) -> bool:
    if not action:
        return True
    try:
        rendered = text.stc_set(cq.unescape(action))(captures)
        result = _eval_last(rendered, environment)
        if result is not None:
            message.sendmsg(result)
        return True
    except Exception:
        _report_error(name, "action")
        return False


def _announce_link(name: str) -> None:
    _stream.info(f"触发 link：{name}")


def _evaluate(item: dict, perform_action: bool = True, announce: bool = False) -> tuple[bool, bool]:
    name = item["name"]
    condition = cq.unescape(item["cond"])
    if item["type"] == "py":
        if not condition:
            return False, True
        try:
            matched = bool(_eval_last(condition, py.loc))
        except Exception:
            _report_error(name, "cond")
            return False, True
        if matched and perform_action and announce:
            _announce_link(name)
        return matched, not perform_action or not matched or _action_py(name, item["action"], py.loc)

    if not condition:
        captures = {}
    else:
        event = context.current()
        if not msgs.is_msg(event):
            return False, True
        try:
            captures = text.stc_get(condition)(cq.unescape(msgs.body(event)), py.loc)
        except Exception:
            _report_error(name, "cond")
            return False, True
        if captures is None:
            return False, True
    if perform_action and announce:
        _announce_link(name)
    return True, not perform_action or _action_re(name, item["action"], py.loc, captures)


def _dispatch(event):
    context.set_current(event)
    py.loc["msg"] = event
    py.loc["_py_msg"] = event
    captures = capture_items()
    persisted_names = {item["name"] for item in links}

    def run_captures(before: str | None, *, missing: bool = False):
        for name, function, anchor in captures:
            selected = anchor == before
            if missing:
                selected = anchor is not None and anchor not in persisted_names
            if not selected:
                continue
            try:
                if function(event):
                    _stream.info(f"触发 capture：{name}")
                    return name
            except Exception:
                _report_error(name, "capture")
                # A failed fallback has not successfully claimed the event.
                # Keep later persisted reactions and captures reachable.
                continue
        return None

    for item in links:
        captured = run_captures(item["name"])
        if captured is not None:
            return captured
        matched, _action_ok = _evaluate(item, announce=True)
        if matched:
            return item["name"]
    captured = run_captures(None)
    if captured is not None:
        return captured
    captured = run_captures(None, missing=True)
    if captured is not None:
        return captured
    return None


dispatch = thread.to_thread(None)(_dispatch)
exec_links = dispatch


def run_action(name: str, environment=None, names=None):
    item = get_link(name)
    if item is None:
        return None
    environment = py.loc if environment is None else environment
    if item["type"] == "py":
        return _action_py(name, item["action"], environment)
    return _action_re(name, item["action"], environment, names or {})


do_action = run_action


def formats_link(item: dict, detail: bool = False) -> str:
    result = f'{item["type"]} {item["name"]}'
    if detail:
        result += f'\n{item["cond"]}\n===\n{item["action"]}'
    return result


def catch_links(event) -> tuple[list[str], list[str]]:
    context.set_current(event)
    py.loc["msg"] = event
    for item in links:
        matched, _action_ok = _evaluate(item, perform_action=False)
        if matched:
            return [item["name"]], [item["name"] + ":matched"]
    return [], ["end"]


def set_link(name: str, kind: str, condition: str, action: str) -> str:
    if kind not in ("re", "py"):
        return f'link类型无效: "{kind}"，只允许 re 或 py'
    item = get_link(name)
    if item is None:
        links.insert(0, {"name": name, "type": kind, "cond": condition, "action": action})
        return "创建成功"
    item.update(type=kind, cond=condition, action=action)
    return "修改成功"


def set_literal(name: str, condition: str, response: str) -> str:
    """Create or replace one exact-text reaction without generated Python."""
    return set_link(name, "re", re.escape(condition), repr(response))


def _set(name: str, kind: str, parts: list[str]):
    if parts and parts[0]:
        condition = parts.pop(0)
    else:
        reply = yield "输入cond"
        if not msgs.is_msg(reply):
            return "操作终止"
        condition = msgs.body(reply).strip()
    if parts and parts[0]:
        action = parts.pop(0)
    else:
        reply = yield "输入action"
        if not msgs.is_msg(reply):
            return "操作终止"
        action = msgs.body(reply).strip()
    return set_link(name, kind, condition, action)


def _save_links(path: str):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as stream:
                if _snapshot(json.load(stream)) == _snapshot(links):
                    return f"文件 {path} 内容与当前一致，无需保存"
        except Exception:
            pass
    if _snapshot(links) != _startup_snapshot:
        reply = yield f"当前 links 与启动时不同（{len(links)} 条），确认覆盖 {path}？(y/n)"
        if not (msgs.is_msg(reply) and msgs.body(reply).strip().lower() == "y"):
            return "操作取消"
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(links, stream, indent=2, ensure_ascii=False, default=str)
    return f"已保存 {len(links)} 条 link 到 {path}"


def _load_links(path: str):
    global _startup_snapshot
    if not os.path.exists(path):
        return f"文件不存在: {path}"
    try:
        with open(path, encoding="utf-8") as stream:
            incoming = json.load(stream)
    except Exception as error:
        return f"读取失败: {error}"
    errors = _validate_import(incoming)
    if errors:
        return "验证失败，保留当前配置:\n" + "\n".join(errors[:10])
    reply = yield f"将用 {len(incoming)} 条 link 替换当前 {len(links)} 条，确认？(y/n)"
    if not (msgs.is_msg(reply) and msgs.body(reply).strip().lower() == "y"):
        return "操作取消"
    links[:] = incoming
    storage.save()
    _startup_snapshot = _snapshot(links)
    return f"已加载 {len(links)} 条 link，配置已保存到磁盘"


@command
def run(body: str):
    """管理按顺序匹配的线性 link（管理员）。

    创建：.link re|py <名称>，随后两段正文以 === 分隔条件和动作。
    维护：.link get|del <名称>；move <名称> <索引>；list；captures；catch [文本]；save|load [路径]。
    save/load 默认使用 data/links_save.json，覆盖或载入前可能要求确认。
    """
    event = context.current()
    if not op.require_op(event):
        return None
    lines = cq.unescape(body).splitlines()
    first, remaining = (lines + [""])[0], lines[1:]
    action, tail = text.read_params(first)
    if action in ("re", "py"):
        name, _ = text.read_params(tail)
        if not name:
            return run.__doc__
        return (yield from _set(name, action, "\n".join(remaining).split("\n===\n")))
    if action == "del":
        name, _ = text.read_params(tail)
        return "删除成功" if del_link(name) else "没有找到link"
    if action == "get":
        name, _ = text.read_params(tail)
        item = get_link(name)
        return formats_link(item, True) if item else "没有找到link"
    if action == "move":
        name, index_text, extra = text.read_params(tail, 2)
        if extra.strip() or not index_text.lstrip("-").isdigit():
            return run.__doc__
        item = get_link(name)
        if item is None:
            return "没有找到link"
        links.remove(item)
        links.insert(int(index_text), item)
        return "移动成功"
    if action == "list":
        return pages.display([formats_link(item) for item in links], 10) if links else "links 为空"
    if action == "captures":
        return "\n".join(
            f"{name} before {before}" if before else name
            for name, _function, before in capture_items()
        ) or "captures 为空"
    if action == "catch":
        if tail.lstrip():
            reply = {**event, "message": tail.lstrip()}
        else:
            reply = yield "输入想筛选的文本"
        names, endings = catch_links(reply)
        end = "\n终结于: " + " ".join(endings)
        return ("触发的links: " + "\n".join(names) if names else "该消息不触发任何link") + end
    if action == "save":
        path, _ = text.read_params(tail)
        return (yield from _save_links(path.strip() or "data/links_save.json"))
    if action == "load":
        path, _ = text.read_params(tail)
        return (yield from _load_links(path.strip() or "data/links_save.json"))
    return run.__doc__
