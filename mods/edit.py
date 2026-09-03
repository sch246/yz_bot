"""Persistent, generator-driven text editor sessions."""

import os
import re

from mods import context, identity, msgs, op, storage, text
from mods.command import command


LOAD_AFTER = ("storage",)

sessions: dict = {}

PAGE_SIZE_REGEX = re.compile(r"^s\s+(\d+)$", re.IGNORECASE)
PAGE_NAV_REGEX = re.compile(r"^(p|n)\s*(\d*)$", re.IGNORECASE)
JUMP_REGEX = re.compile(r"^(-?\d+)$")
BLOCK_EDIT_HEADER_REGEX = re.compile(
    r"^file: (.*?)(?:\s*\(unsaved\))? \| line: (\d+) \| size: (\d+)",
    re.IGNORECASE,
)
SINGLE_LINE_EDIT_REGEX = re.compile(r"^(\d+)\s*[|│](.*)$")
REST_LINE_REGEX = re.compile(r"^\.\.\.rest \d+ lines?$")


def on_load(_ctx):
    global sessions
    sessions = storage.get("edit", "sessions", dict)


def _get_path(user_storage: dict, group_id):
    if group_id:
        return user_storage.setdefault("edits", {}).get(str(group_id))
    return user_storage.get("edit")


def _set_path(user_storage: dict, group_id, path: str):
    if group_id:
        user_storage.setdefault("edits", {})[str(group_id)] = path
    else:
        user_storage["edit"] = path


def _chat_id(group_id, user_id):
    return f"group{group_id}" if group_id else f"user{user_id}"


def _get_session(path, owner):
    if os.path.isdir(path):
        return None, f"'{path}' 是一个目录，无法作为文件编辑。"
    if path in sessions:
        return sessions[path], None
    try:
        with open(path, encoding="utf-8") as stream:
            lines = stream.read().splitlines()
    except FileNotFoundError:
        lines = []
    except PermissionError:
        return None, f"权限不足，无法读取 '{path}'。"
    except OSError as exc:
        return None, f"无法读取文件 '{path}'。原因: {exc}"

    session = {
        "owner": owner,
        "filepath": path,
        "lines": lines,
        "current_line": 0,
        "page_size": 20,
        "show_linenumbers": True,
        "undo_stack": [],
        "redo_stack": [],
        "is_dirty": False,
        "quit_confirm": False,
        "last_input_was_invalid": False,
    }
    sessions[path] = session
    return session, None


def _strip_index(line: str):
    match = SINGLE_LINE_EDIT_REGEX.match(line)
    return match.group(2) if match else line


def _render(session: dict):
    lines = session["lines"]
    page_size = session["page_size"]
    maximum = max(0, len(lines) - page_size) if len(lines) > page_size else 0
    current = max(0, min(session["current_line"], maximum))
    session["current_line"] = current
    end = current + page_size
    dirty = " (unsaved)" if session["is_dirty"] else ""
    header = (
        f'file: {session["filepath"]}{dirty} | line: {current} | size: {page_size}'
    )
    page = lines[current:end]
    if session["show_linenumbers"]:
        content = [f"{index}│{line}" for index, line in enumerate(page, current)]
    else:
        content = list(page)
    remaining = len(lines) - end
    if remaining > 0:
        content.append(f"...rest {remaining} line" + ("s" if remaining != 1 else ""))
    return header + "\n" + "\n".join(content)


def _save(session):
    try:
        parent = os.path.dirname(session["filepath"])
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(session["filepath"], "w", encoding="utf-8") as stream:
            stream.write("\n".join(session["lines"]))
        return True, "Saved."
    except Exception as exc:
        return False, f"Save failed: {exc}"


@command
def run(body: str):
    """在聊天中交互编辑文本文件（管理员）。

    格式：.edit [文件路径]
    无路径时沿用当前窗口上次文件；w/wq 保存，q/q! 退出，d 挂起，u/r 撤销重做，n/p 翻页，i 切换行号。
    """
    event = context.current()
    if not op.require_op(event):
        return None
    user_id = event["user_id"]
    group_id = event.get("group_id")
    first = body.splitlines()[0] if body.strip() else ""
    path, _ = text.read_params(first)
    user_storage = identity.getstorage(user_id)
    path = path or _get_path(user_storage, group_id)
    if not path:
        return run.__doc__
    _set_path(user_storage, group_id, path)

    owner = _chat_id(group_id, user_id)
    session, error = _get_session(path, owner)
    if session is None:
        return f"获取session失败: {error}"
    if session["owner"] != owner:
        return f'当前文件正被 {session["owner"]} 编辑中'
    reply = yield _render(session)

    while True:
        if not msgs.is_msg(reply):
            reply = yield
            continue
        session, error = _get_session(path, owner)
        if session is None:
            return f"获取session失败: {error}"
        if session["owner"] != owner:
            return f'当前文件正被 {session["owner"]} 编辑中'

        user_input = msgs.body(reply)
        value = user_input.strip().lower()
        previous_invalid = session["last_input_was_invalid"]
        session["last_input_was_invalid"] = False
        if value != "q":
            session["quit_confirm"] = False

        if value == "q":
            if session["is_dirty"] and not session["quit_confirm"]:
                session["quit_confirm"] = True
                reply = yield "有未保存的更改。再次输入 'q' 将不保存并退出。"
                continue
            del sessions[path]
            return "编辑会话已结束。"
        if value == "q!":
            del sessions[path]
            return "编辑会话已结束。"
        if value in ("w", "wq"):
            success, result = _save(session)
            if success:
                session["is_dirty"] = False
            if value == "wq" and success:
                del sessions[path]
                return result + "\n编辑会话已结束。"
            reply = yield result + ("\n保存失败，会话未退出。" if not success else "")
            continue
        if value == "d":
            return "挂起编辑会话。"
        if value == "u":
            if session["undo_stack"]:
                session["redo_stack"].append(session["lines"][:])
                session["lines"] = session["undo_stack"].pop()
                session["is_dirty"] = True
            reply = yield _render(session)
            continue
        if value == "r":
            if session["redo_stack"]:
                session["undo_stack"].append(session["lines"][:])
                session["lines"] = session["redo_stack"].pop()
                session["is_dirty"] = True
            reply = yield _render(session)
            continue
        if value == "i":
            session["show_linenumbers"] = not session["show_linenumbers"]
            reply = yield _render(session)
            continue
        if match := PAGE_SIZE_REGEX.match(value):
            session["page_size"] = max(1, int(match.group(1)))
            reply = yield _render(session)
            continue
        if match := PAGE_NAV_REGEX.match(value):
            direction, count = match.group(1), int(match.group(2) or 1)
            offset = session["page_size"] * count
            session["current_line"] += offset if direction == "n" else -offset
            reply = yield _render(session)
            continue
        if match := JUMP_REGEX.match(value):
            line = int(match.group(1))
            session["current_line"] = line + len(session["lines"]) if line < 0 else line
            reply = yield _render(session)
            continue
        if match := BLOCK_EDIT_HEADER_REGEX.match(user_input):
            parsed_path, start, size = match.groups()
            if os.path.abspath(parsed_path) != os.path.abspath(session["filepath"]):
                reply = yield (
                    f"错误：您正在尝试编辑 '{parsed_path}'，但当前会话绑定的是 "
                    f"'{session['filepath']}'。\n请先使用 'q' 退出当前会话。"
                )
                continue
            replacement = user_input.splitlines()[1:]
            if replacement and REST_LINE_REGEX.match(replacement[-1]):
                replacement.pop()
            session["undo_stack"].append(session["lines"][:])
            session["redo_stack"].clear()
            current, page_size = session["current_line"], session["page_size"]
            session["lines"][current : current + page_size] = map(_strip_index, replacement)
            session["is_dirty"] = True
            session["current_line"], session["page_size"] = int(start), int(size)
            reply = yield _render(session)
            continue
        if match := SINGLE_LINE_EDIT_REGEX.match(user_input):
            line, content = int(match.group(1)), match.group(2)
            if line >= len(session["lines"]):
                session["lines"].extend([""] * (line - len(session["lines"]) + 1))
            session["undo_stack"].append(session["lines"][:])
            session["redo_stack"].clear()
            session["lines"][line] = content
            session["is_dirty"] = True
            reply = yield _render(session)
            continue

        if previous_invalid:
            return "连续两次无效输入，自动挂起编辑会话。"
        session["last_input_was_invalid"] = True
        reply = yield (
            "无效指令。可用指令:\n"
            "q:退出, w:保存, q!:强制退出, wq:保存并退出\n"
            "d:挂起, i:切换行号, s <每页行数:int>:设置页大小\n"
            "p/n [N:int]:上下翻页, <行号:int>:跳转\n"
            "u:撤销, r:重做\n"
            "或直接复制消息/行，修改后重新发送进行编辑。"
        )
