"""Human-readable append-only QQ chat history."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import time
from typing import Any

from mods import INFRA
from mods import cq, history, identity


PHASE = INFRA
LOAD_AFTER = ("history", "identity")
rootfile = "chatlog"
logger = logging.getLogger(__name__)


def search_current(pattern: str) -> str:
    """Search the current window's log without invoking a shell."""
    from mods import context

    event = context.current()
    group_id = event.get("group_id")
    if group_id is not None:
        directory = Path(rootfile) / "group" / str(group_id)
    else:
        directory = Path(rootfile) / "private" / str(event["user_id"])
    try:
        expression = re.compile(pattern)
    except re.error as error:
        return f"搜索表达式无效: {error}"
    matches = []
    for path in sorted(directory.rglob("*.log")) if directory.is_dir() else []:
        try:
            with path.open(encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, 1):
                    if expression.search(line):
                        matches.append(f"{path}:{line_number}:{line.rstrip()}")
        except OSError:
            continue
    return "\n".join(matches)


def _unescape(text: Any) -> str:
    value = str(text)
    unescape = getattr(cq, "unescape", None)
    return unescape(value) if callable(unescape) else value


def _append(path: str, text: str) -> str:
    """Append to the log file and hand the text back for the caller to echo."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as file:
        file.write(text)
        file.flush()
    return text


def get_path(root: str, timestamp: int | float) -> str:
    local = time.localtime(timestamp)
    return os.path.join(root, time.strftime("%Y-%m", local), time.strftime("%d.log", local))


def _addtab(text: str) -> str:
    return "\n".join("    " + line for line in text.splitlines())


def _group_str(
    title: str,
    name: str,
    user_id: int,
    timestamp: int | float,
    text: str,
    message_id: int | str,
) -> str:
    return (
        f"【{title}】{name}({user_id}) "
        f'{time.strftime("%H:%M:%S", time.localtime(timestamp))} | {message_id}\n'
        f"{_addtab(text)}\n"
    )


def _private_str(
    name: str,
    timestamp: int | float,
    text: str,
    message_id: int | str = "",
) -> str:
    suffix = f" | {message_id}" if message_id != "" else ""
    return (
        f'{name} {time.strftime("%H:%M:%S", time.localtime(timestamp))}{suffix}\n'
        f"{_addtab(text)}\n"
    )


def _notice_str(timestamp: int | float, text: str) -> str:
    return f': {text} {time.strftime("%H:%M:%S", time.localtime(timestamp))}\n'


def _group_write(msg: dict[str, Any], group_id: int, text: str) -> str:
    result = _append(get_path(os.path.join(rootfile, "group", str(group_id)), msg["time"]), text)
    history.add_msg("group", group_id, msg)
    return result


def _private_write(msg: dict[str, Any], user_id: int, text: str) -> str:
    result = _append(get_path(os.path.join(rootfile, "private", str(user_id)), msg["time"]), text)
    history.add_msg("private", user_id, msg)
    return result


def _bot_write(msg: dict[str, Any], text: str) -> str:
    result = _append(get_path(os.path.join(rootfile, "bot"), msg["time"]), text)
    history.add_self_msg(msg)
    return result


def _file_str(file_info: dict[str, Any]) -> str:
    return f'【文件】{file_info.get("name", "")} {_get_size(int(file_info.get("size", 0)))}\n{file_info.get("url", "")}'


def _get_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000:
            return f"{value:.2f}{unit}"
        value /= 1024
    return f"{value:.2f}PB"


def gettime(seconds: int) -> tuple[int, int, int, int]:
    day, seconds = divmod(seconds, 86400)
    hour, seconds = divmod(seconds, 3600)
    minute, seconds = divmod(seconds, 60)
    return day, hour, minute, seconds


def format_poke(msg: dict[str, Any]) -> str:
    user_id, target_id = int(msg["user_id"]), int(msg["target_id"])
    name = identity.getname(user_id)
    target = identity.getname(target_id)
    if "group_id" in msg:
        return f"{name}({user_id})戳了戳{target}({target_id})"
    return f"{name}戳了戳{target}"


def _message(msg: dict[str, Any]) -> str:
    timestamp = msg["time"]
    sender = msg.get("sender")
    if not isinstance(sender, dict):
        sender = {"user_id": msg["user_id"]}
        msg["sender"] = sender
    identity.update(msg)
    sender_id = int(sender.get("user_id", msg["user_id"]))
    text = _unescape(msg.get("message", ""))
    message_id = msg.get("message_id", "")
    if "group_id" in msg:
        group_id = int(msg["group_id"])
        title, display = identity.get_group_user_info(group_id, sender_id)
        return _group_write(
            msg,
            group_id,
            _group_str(title, display, sender_id, timestamp, text, message_id),
        )
    window_user = int(msg.get("user_id", sender_id))
    display = identity.get_user_name(sender_id)
    return _private_write(
        msg,
        window_user,
        _private_str(display, timestamp, text, message_id),
    )


def _notice(msg: dict[str, Any]) -> str | None:
    timestamp = msg["time"]
    notice_type = msg.get("notice_type")
    sub_type = msg.get("sub_type")
    user_id_value = msg.get("operator_id", msg.get("user_id"))
    user_id = int(user_id_value) if user_id_value is not None else None
    group_id = int(msg["group_id"]) if msg.get("group_id") is not None else None

    if notice_type in ("group_recall", "friend_recall"):
        history.remove_message(
            int(msg["message_id"]),
            group_id=group_id,
            user_id=None if group_id is not None else user_id,
        )

    name = identity.get_user_name(user_id) if user_id is not None else "[unknown]"
    title = ""
    if group_id is not None and user_id is not None:
        title, name = identity.get_group_user_info(group_id, user_id)

    if notice_type == "group_upload" and group_id is not None:
        return _group_write(msg, group_id, _group_str(title, name, user_id or 0, timestamp, _file_str(msg["file"]), ""))
    if notice_type in ("offline_file", "private_upload") and user_id is not None:
        return _private_write(msg, user_id, _private_str(name, timestamp, _file_str(msg["file"])))
    if notice_type == "group_admin" and group_id is not None:
        text = f"{name}({user_id})被设为了管理员" if sub_type == "set" else f"{name}({user_id})被移除了管理员"
    elif notice_type == "group_decrease" and group_id is not None:
        if sub_type == "leave":
            text = f"{name}({user_id})离开了群"
        else:
            operator_id = int(msg.get("operator_id", 0))
            operator = identity.get_group_user_info(group_id, operator_id)[1]
            text = f"{name}({user_id})被{operator}({operator_id})踢出了群"
    elif notice_type == "group_increase" and group_id is not None:
        operator_id = int(msg.get("operator_id", 0))
        operator = identity.get_group_user_info(group_id, operator_id)[1]
        verb = "同意" if sub_type == "approve" else "邀请"
        text = f"{operator}({operator_id}){verb}{name}({user_id})加入了群"
    elif notice_type == "group_ban" and group_id is not None:
        operator_id = int(msg.get("operator_id", 0))
        operator = identity.get_group_user_info(group_id, operator_id)[1]
        if sub_type == "ban":
            day, hour, minute, second = gettime(int(msg.get("duration", 0)))
            text = f"{name}({user_id})被{operator}({operator_id})禁言{day}天{hour}时{minute}分{second}秒"
        else:
            text = f"{name}({user_id})被{operator}({operator_id})解除禁言"
    elif notice_type == "friend_add" and user_id is not None:
        return _bot_write(msg, _notice_str(timestamp, f"添加了{name}({user_id})为好友"))
    elif notice_type == "group_recall" and group_id is not None:
        operator_id = int(msg.get("operator_id", 0))
        operator = identity.get_group_user_info(group_id, operator_id)[1]
        if operator_id == int(msg.get("user_id", 0)):
            text = f"{name}({user_id})撤回了一条消息({msg['message_id']})"
        else:
            text = f"{operator}({operator_id})撤回了{name}({user_id})的一条消息({msg['message_id']})"
    elif notice_type == "friend_recall" and user_id is not None:
        return _private_write(msg, user_id, _notice_str(timestamp, f"{name}撤回了一条消息({msg['message_id']})"))
    elif notice_type == "notify" and sub_type == "poke":
        text = format_poke(msg)
    elif notice_type == "notify" and sub_type == "lucky_king":
        target_id = int(msg["target_id"])
        target = identity.get_user_name(target_id)
        text = f"{name}({user_id})的红包，{target}({target_id})是运气王"
    elif notice_type == "notify" and sub_type == "honor":
        text = f"{name}({user_id})获得荣誉：{msg.get('honor_type', '')}"
    elif notice_type == "group_card":
        card = msg.get("card_new", "")
        text = f'{name}({user_id})更新了ta的名片为"{card}"' if card else f"{name}({user_id})移除了ta的名片"
    elif notice_type == "essence" and group_id is not None:
        operator_id = int(msg.get("operator_id", 0))
        operator = identity.get_group_user_info(group_id, operator_id)[1]
        verb = "设为" if sub_type == "add" else "取消了"
        text = f"{operator}({operator_id}){verb}{name}({user_id})的精华消息({msg.get('message_id')})"
    else:
        return None

    if group_id is not None:
        return _group_write(msg, group_id, _notice_str(timestamp, text))
    if user_id is not None:
        return _private_write(msg, user_id, _notice_str(timestamp, text))
    return _bot_write(msg, _notice_str(timestamp, text))


def write(msg: dict[str, Any]) -> str | None:
    """Append one event and update recent history in the same operation."""
    msg.setdefault("time", int(time.time()))
    try:
        post_type = msg.get("post_type")
        # ``get_msg`` responses used to record Bot output do not consistently
        # include post_type, but do carry the normal message fields.
        if post_type in ("message", "message_sent") or (
            post_type is None and "message" in msg and "message_id" in msg
        ):
            return _message(msg)
        if post_type == "notice":
            return _notice(msg)
        if post_type == "request" and msg.get("request_type") == "friend":
            user_id = int(msg["user_id"])
            text = f"{identity.get_user_name(user_id)}({user_id})请求添加你为好友"
            return _bot_write(msg, _private_str(text, msg["time"], str(msg.get("comment", ""))))
        return _bot_write(msg, _private_str("其它消息", msg["time"], repr(msg)))
    except Exception:
        logger.exception("写入 chatlog 失败")
        try:
            return _bot_write(msg, _private_str("未捕获消息", msg["time"], repr(msg)))
        except Exception:
            logger.exception("写入 chatlog 兜底记录失败")
            return None
