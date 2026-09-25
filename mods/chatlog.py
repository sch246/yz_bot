"""Human-readable append-only QQ chat history."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from contextlib import closing
from typing import Any

from mods import INFRA
from mods import _backfill_archive, cq, history, identity


PHASE = INFRA
LOAD_AFTER = ("storage", "history", "identity")
rootfile = "chatlog"
logger = logging.getLogger(__name__)
_append_lock = threading.RLock()
_reader_lock = threading.Lock()
_boot_anchors: dict[tuple[str, int], str | None] | None = None
_line_positions: dict[str, tuple[int, int]] = {}
_live_origins: dict[int, tuple[dict, str]] = {}
_recall_lock = threading.Lock()
_recalls: dict[tuple[str, int], set[str]] = {}
_live_recalls: dict[tuple[str, int], set[str]] = {}


def recall_key(message_id: Any) -> str:
    value = str(message_id)
    return str(int(value)) if value.lstrip("-").isdigit() else value


def on_load(_ctx: dict[str, Any] | None = None) -> None:
    """Stamp the moment v1 writing began.

    Every record from this boot on stores the raw body and a private sender id;
    everything before it does not.  Only this moment knows where the line falls,
    so it is written down now even though the reader (the range query) does not
    exist yet -- afterwards the fact is unrecoverable.
    """
    from mods import storage

    marker = storage.get(rootfile, "format", lambda: {})
    marker.setdefault("v1_since", int(time.time()))
    windows, records = _restore_history()
    logger.info("从 chatlog 重建近期消息：%d 个窗口 %d 条", windows, records)


def _file_records(lines: list[str]) -> list[tuple[int, int]]:
    """Split one file's lines into records as ``(start, end)`` index pairs.

    The record shape is the whole rule: a head is unindented, its body lines
    carry four spaces.  A notice is a head with no body, so it comes out as a
    one-line record and searching it is unchanged.  A file that somehow starts
    with a body line still yields a record rather than dropping those lines.
    """
    heads = [index for index, line in enumerate(lines) if not line.startswith("    ")]
    if not heads or heads[0] != 0:
        heads.insert(0, 0)
    return list(zip(heads, heads[1:] + [len(lines)]))


def search_current(pattern: str) -> list[str]:
    """Search the current window's log without invoking a shell, by record.

    Matching stays per line, but a hit returns the **whole record** it belongs
    to, under a ``path:line`` locator naming the record's first line.  Per-line
    results were wrong in the normal case rather than an edge one: searching
    what was said hits an indented body line and loses the sender, timestamp
    and message id sitting on the head above it, while searching who said it
    hits the head and loses what was said.

    Results are the stored record put through ``display``, never re-rendered
    from parsed fields -- the file line is already the best rendering for a
    reader, so round-tripping it through ``parse_log`` could only lose the
    locator and invent what v0 never wrote down.

    Raises ``re.error`` for an invalid pattern; phrasing that for the user
    belongs to the command, not here.
    """
    from mods import context

    event = context.current()
    group_id = event.get("group_id")
    if group_id is not None:
        directory = Path(rootfile) / "group" / str(group_id)
    else:
        # 私聊窗口是流水线的对端。顶层 `user_id` 是作者（Bot 自己说的话也带着它）。
        directory = Path(rootfile) / "private" / str(event["target_id"])
    expression = re.compile(pattern)
    matches = []
    for path in sorted(directory.rglob("*.log")) if directory.is_dir() else []:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for start, end in _file_records(lines):
            shown = display("\n".join(lines[start:end])).rstrip()
            if any(expression.search(line) for line in shown.split("\n")):
                matches.append(f"{path}:{start + 1}\n{shown}")
    for path in sorted(directory.rglob("*.backfill.jsonl")) if directory.is_dir() else []:
        for record in _backfill_archive.read_day(path):
            sender = record.get("sender") or {}
            name = sender.get("card") or sender.get("nickname") or str(record.get("user_id", ""))
            shown = display(format_message(record, str(name), str(sender.get("title", "")))).rstrip()
            if any(expression.search(line) for line in shown.split("\n")):
                matches.append(f"{Path(rootfile) / record['_log_origin'].partition(':')[0]}:"
                               f"{record['_log_origin'].rpartition(':')[2]}\n{shown}")
    return matches


def _unescape(text: Any) -> str:
    value = str(text)
    unescape = getattr(cq, "unescape", None)
    return unescape(value) if callable(unescape) else value


def display(record: str) -> str:
    """The reader-facing projection of stored records: bodies unescaped.

    v1 stores the raw OneBot body, because unescaping destroys the difference
    between a real CQ code and a user typing one.  Everything that shows a
    record to a person -- the terminal echo, ``.search`` -- goes through here,
    so what a reader sees is unchanged while the file keeps the fact.

    Only indented body lines are unescaped; a record head is the formatter's
    own text and never carries entities of its own.
    """
    return "\n".join(_unescape(line) if line.startswith("    ") else line for line in record.split("\n"))


def _append(path: str, text: str) -> tuple[str, str]:
    """Append one record and return its stable file/head-line locator."""
    encoded = text.encode("utf-8")
    with _append_lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a+b") as file:
            file.seek(0, os.SEEK_END)
            size = file.tell()
            cached = _line_positions.get(path)
            if cached is not None and cached[0] == size:
                lines = cached[1]
            else:
                file.seek(0)
                lines = 0
                while chunk := file.read(65536):
                    lines += chunk.count(b"\n")
            file.seek(0, os.SEEK_END)
            file.write(encoded)
            file.flush()
            _line_positions[path] = (size + len(encoded), lines + encoded.count(b"\n"))
        origin = f"{Path(path).relative_to(rootfile)}:{lines + 1}"
        return text, origin


def _remember_origin(event: dict, origin: str) -> None:
    _live_origins[id(event)] = (event, origin)
    if len(_live_origins) > 4096:
        _live_origins.pop(next(iter(_live_origins)))


def consume_origin(event: dict) -> str | None:
    """Pass writer provenance to the arrival journal without changing the OneBot event."""
    saved = _live_origins.pop(id(event), None)
    return saved[1] if saved is not None and saved[0] is event else None


def append_backfill_page(kind: str, target: int, messages: list[dict]) -> list[str]:
    """Commit a remote page without building a window-sized identity map."""
    with _append_lock:
        return _backfill_archive.append_messages(kind, target, messages, root=rootfile)


def _existing_backfill_origin(kind: str, target: int, event: dict) -> str | None:
    message_id = event.get("message_id")
    if message_id is None:
        return None
    path = _backfill_archive.sidecar_path(kind, target, int(event["time"]), root=rootfile)
    if not path.is_file():
        return None
    return _backfill_archive.lookup_origins(
        kind, target, [message_id], int(event["time"]), int(event["time"]), root=rootfile
    ).get(recall_key(message_id))


def get_path(root: str, timestamp: int | float) -> str:
    local = time.localtime(timestamp)
    return os.path.join(root, time.strftime("%Y-%m", local), time.strftime("%d.log", local))


def _addtab(text: str) -> str:
    return "\n".join("    " + line for line in text.splitlines())


def _deltab(text: str) -> str:
    """Undo ``_addtab``, dropping the trailing blanks the rule says carry nothing.

    Trailing newlines and whitespace are gone for good, and that is deliberate:
    the QQ client strips them too, and a chat message whose meaning lives in its
    trailing blanks does not occur.  Everything else survives, including the
    interior blank lines ``_addtab`` renders as four spaces.

    The ``rstrip`` is what makes the pair exact.  ``_addtab`` alone is not
    injective -- it renders both ``"a"`` and ``"a\n"`` as ``"    a"`` -- and a v0
    body ending in a blank line was written as a stray ``"    "`` line that no
    re-render can reproduce.  Stripping here parses such a body into what v1
    would have stored, which is the same message under the rule.
    """
    return "\n".join(line[4:] if line.startswith("    ") else line for line in text.split("\n")).rstrip()


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
    user_id: int | None = None,
) -> str:
    """A message-shaped private record.  Pass *user_id* whenever the head is a person.

    The heads that are not a person -- ``其它消息``, ``未捕获消息``, a friend
    request whose head is already a full sentence -- leave it out.
    """
    suffix = f" | {message_id}" if message_id != "" else ""
    head = name if user_id is None else f"{name}({user_id})"
    return (
        f'{head} {time.strftime("%H:%M:%S", time.localtime(timestamp))}{suffix}\n'
        f"{_addtab(text)}\n"
    )


def _notice_str(timestamp: int | float, text: str) -> str:
    return f': {text} {time.strftime("%H:%M:%S", time.localtime(timestamp))}\n'


def _group_write(msg: dict[str, Any], group_id: int, text: str) -> str:
    result, origin = _append(get_path(os.path.join(rootfile, "group", str(group_id)), msg["time"]), text)
    _remember_origin(msg, origin)
    history.add_msg("group", group_id, msg)
    return result


def _private_write(msg: dict[str, Any], user_id: int, text: str) -> str:
    result, origin = _append(get_path(os.path.join(rootfile, "private", str(user_id)), msg["time"]), text)
    _remember_origin(msg, origin)
    history.add_msg("private", user_id, msg)
    return result


def _bot_write(msg: dict[str, Any], text: str) -> str:
    result, _origin = _append(get_path(os.path.join(rootfile, "bot"), msg["time"]), text)
    history.add_self_msg(msg)
    return result


def _file_str(file_info: dict[str, Any]) -> str:
    return f'【文件】{file_info.get("name", "")} {_get_size(int(file_info.get("size", 0)))}\n{file_info.get("url", "")}'


def _get_size(size: int) -> str:
    # WHY: 阈值 1000、除数 1024、单位写 KB，三者不自洽——1000~1023 B 渲染成 "0.98KB"
    # 而不是 B，且与 image/__init__.py 的 KiB(/1024) 不一致。已知，不修。
    # 这行的输出直接进 chatlog 文件行(_file_str)，而 chatlog 是协议不是描述。解析侧
    # 不读回大小所以改了不会崩，但会在唯一写入权威里留下第二次静默的格式漂移，且不像
    # v0/v1 那样带标注——正是 chatlog-format.md 拒绝写转换脚本时给的理由。要改就得
    # 连同版本标注一起设计，不能当成顺手的单位订正。
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
    """Render a poke, with ids in both windows.

    A private line used to carry names only, which is the same asymmetry v1
    closed for private message records: with no ids the line cannot say which
    side poked, and the two candidates are only distinguishable by a nickname
    anyone can change.  Now both windows render alike, and ``_recognise_notice``
    reads this back exactly -- which is all a rebuilt poke has to be, because
    ``chat.get_msgs`` hands the event straight back to this function.

    The names come from the QQ side, like every other record here: the group
    card in a group, the account nickname in private.  Not ``identity.getname``,
    which the migration put here and which differs twice over -- it layers the
    ``.setname`` override on top, so a display preference would rewrite the
    record of what happened, and it reads ``context.current()`` for the window,
    so the same event rendered from a rebuild or from another window would come
    out differently.  A formatter has to be a function of its event alone.
    """
    user_id, target_id = int(msg["user_id"]), int(msg["target_id"])
    group_id = msg.get("group_id")
    if group_id is not None:
        name = identity.get_group_user_info(int(group_id), user_id)[1]
        target = identity.get_group_user_info(int(group_id), target_id)[1]
    else:
        name, target = identity.get_user_name(user_id), identity.get_user_name(target_id)
    return f"{name}({user_id})戳了戳{target}({target_id})"


def _message(msg: dict[str, Any]) -> str:
    timestamp = msg["time"]
    sender = msg.get("sender")
    if not isinstance(sender, dict):
        sender = {"user_id": msg["user_id"]}
        msg["sender"] = sender
    identity.update(msg)
    sender_id = int(sender.get("user_id", msg["user_id"]))
    # WHY: 私聊窗口正常由 ``target_id`` 给出（实时事件、回声、回查都带它），能走到
    # ``or`` 这一支说明上游没给窗口。落点取"作者自己的私聊"：它是一个稳定、且错处
    # 看得见的键，比替它猜一个对端好。
    kind, target = history.window(msg) or ("private", sender_id)
    if kind == "group":
        group_id = int(target)
        title, display = identity.get_group_user_info(group_id, sender_id)
        rendered = format_message(msg, display, title)
        with _append_lock:
            try:
                archived = _existing_backfill_origin(kind, group_id, msg)
            except (OSError, ValueError):
                logger.exception("检查回填档案失败，实时消息仍写入正常日档")
                archived = None
            if archived is None:
                return _group_write(msg, group_id, rendered)
            _remember_origin(msg, archived)
        history.add_msg("group", group_id, msg)
        return rendered
    window_user = int(target)
    display = identity.get_user_name(sender_id)
    # v1: a private record carries the sender the way a group record always did.
    # Without it the Bot's own line and the peer's line differ only by a nickname
    # anyone can change, so a private window has no reliable author at all.
    rendered = format_message(msg, display)
    with _append_lock:
        try:
            archived = _existing_backfill_origin(kind, window_user, msg)
        except (OSError, ValueError):
            logger.exception("检查回填档案失败，实时消息仍写入正常日档")
            archived = None
        if archived is None:
            return _private_write(msg, window_user, rendered)
        _remember_origin(msg, archived)
    history.add_msg("private", window_user, msg)
    return rendered


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
        key = ("group", group_id) if group_id is not None else ("private", user_id)
        if key[1] is not None:
            with _recall_lock:
                _live_recalls.setdefault(key, set()).add(recall_key(msg["message_id"]))
                if key in _recalls:
                    _recalls[key].add(recall_key(msg["message_id"]))

    name = identity.get_user_name(user_id) if user_id is not None else "[unknown]"
    title = ""
    if group_id is not None and user_id is not None:
        title, name = identity.get_group_user_info(group_id, user_id)

    if notice_type == "group_upload" and group_id is not None:
        return _group_write(msg, group_id, _group_str(title, name, user_id or 0, timestamp, _file_str(msg["file"]), ""))
    if notice_type in ("offline_file", "private_upload") and user_id is not None:
        return _private_write(msg, user_id, _private_str(name, timestamp, _file_str(msg["file"]), user_id=user_id))
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
        return _private_write(msg, user_id, _notice_str(timestamp, f"{name}({user_id})撤回了一条消息({msg['message_id']})"))
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
        if post_type in ("message", "message_sent"):
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


# --- v0/v1 round trip -------------------------------------------------------
#
# A vertical slice: two pure functions, nothing wired into ``write`` yet.  See
# docs/working/proposals/message-model.md for the minimal fact set and for why
# v0 lines are read but never rewritten.

V0 = "v0"
V1 = "v1"

# Parsed right to left, because the display name is the one field a user can set
# (``identity.setname``) and could otherwise be crafted to fake a separator.
_TIME_SUFFIX = re.compile(r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})$")
_SENDER_SUFFIX = re.compile(r"\((?P<user_id>\d+)\)$")
_TITLED = re.compile(r"^【(?P<title>.*)】(?P<name>.*)$", re.DOTALL)
# The two notice families with an actual consumer.  Both end in ids this file
# wrote itself, and both are matched right to left like every other head here.
_POKE = re.compile(r"^(?P<who>.*)\((?P<user_id>\d+)\)戳了戳(?P<whom>.*)\((?P<target_id>\d+)\)$", re.DOTALL)
_RECALL = re.compile(r"撤回了(?:.*的)?一条消息\((?P<message_id>-?\d+)\)$", re.DOTALL)
_DAY_NAME = re.compile(r"(?P<day>\d{2})\.log$")
_MONTH_NAME = re.compile(r"(?P<year>\d{4})-(?P<month>\d{2})$")


def window_of(path: str | os.PathLike, root: str | os.PathLike = rootfile) -> tuple[str, int | None] | None:
    """The window a log file belongs to, read off its path below *root*."""
    try:
        rest = Path(path).relative_to(root).parts
    except ValueError:
        # An absolute path against a relative root: fall back to the last
        # segment that names the root directory.
        parts, anchor = Path(path).parts, Path(root).parts[-1]
        if anchor not in parts:
            return None
        rest = parts[len(parts) - 1 - parts[::-1].index(anchor) + 1:]
    if not rest:
        return None
    if rest[0] == "bot":
        return "bot", None
    if rest[0] in ("group", "private") and len(rest) > 1 and rest[1].isdigit():
        return rest[0], int(rest[1])
    return None


def day_of(path: str | os.PathLike) -> tuple[int, int, int] | None:
    """The calendar day a log file covers, read off its path."""
    candidate = Path(path)
    day = _DAY_NAME.fullmatch(candidate.name)
    month = _MONTH_NAME.fullmatch(candidate.parent.name)
    if day is None or month is None:
        return None
    return int(month["year"]), int(month["month"]), int(day["day"])


def _archive_day(path: Path) -> tuple[int, int, int] | None:
    if path.name.endswith(".backfill.jsonl"):
        return day_of(path.with_name(path.name.removesuffix(".backfill.jsonl") + ".log"))
    return day_of(path)


def _archive_days(directory: Path) -> list[tuple[int, int, int]]:
    if not directory.is_dir():
        return []
    paths = (*directory.rglob("*.log"), *directory.rglob("*.backfill.jsonl"))
    return sorted({day for path in paths if (day := _archive_day(path)) is not None}, reverse=True)


def format_message(event: dict[str, Any], name: str, title: str = "") -> str:
    """Render one message event as its v1 record.

    Two things separate v1 from v0: a private record carries the sender's id the
    way a group record always did, and the body is the raw OneBot ``message``
    rather than its unescaped display form.  Unescaping is what makes a body
    unreadable back into an event -- a user typing ``[CQ:at,qq=1]`` and a real at
    code are the same bytes afterwards -- so it belongs to display, not storage.

    The body is stripped of trailing whitespace on the way in, which is the rule
    v0 only half applied: it dropped the final newline but rendered any earlier
    trailing blank as a ``"    "`` line.  Applying it fully is what makes this
    function and ``parse_log`` exact inverses.
    """
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_id = int(sender.get("user_id", event.get("user_id", 0)))
    stamp = time.strftime("%H:%M:%S", time.localtime(event.get("time", 0)))
    head = f"{name}({sender_id})"
    if event.get("group_id") is not None:
        head = f"【{title}】{head}"
    return f"{head} {stamp} | {event.get('message_id', '')}\n{_addtab(str(event.get('message', '')).rstrip())}\n"


def _epoch(day: tuple[int, int, int], match: re.Match) -> int:
    """Local time, as everywhere else here; the device is not expected to move."""
    year, month, number = day
    return int(time.mktime((year, month, number, int(match["hour"]), int(match["minute"]), int(match["second"]), 0, 0, -1)))


def _split_head(line: str) -> dict[str, Any] | None:
    """Peel ``【头衔】名字(id) 时:分:秒 | 消息号`` from the right."""
    left, separator, message_id = line.rpartition(" | ")
    if not separator:
        left, message_id = line, None
    stamp = _TIME_SUFFIX.search(left)
    if stamp is None:
        return None
    head = left[: stamp.start()].rstrip()
    sender = _SENDER_SUFFIX.search(head)
    sender_id = None
    if sender is not None:
        sender_id = int(sender["user_id"])
        head = head[: sender.start()]
    titled = _TITLED.fullmatch(head)
    title, name = (titled["title"], titled["name"]) if titled else ("", head)
    return {"stamp": stamp, "sender_id": sender_id, "title": title, "name": name, "message_id": message_id}


def _version_of(head: dict[str, Any], kind: str, hint: str | None) -> str:
    """Which format a record was written in, and therefore whether its body is fact.

    A private record announces itself: only v1 carries the sender id.  A group
    record cannot -- it always carried one -- so without the caller's hint the
    honest answer is v0, the version whose body is only a display projection.
    Under-claiming fidelity is the safe direction, and the hint (a switch
    timestamp in storage) is what removes the guess.  It also settles the one
    case a private line gets wrong on its own: a v0 display name ending in
    ``(12345)``.
    """
    if hint is not None:
        return hint
    if kind == "private" and head["sender_id"] is not None:
        return V1
    return V0


def _guess_private_sender(
    name: str,
    kind: str,
    target: int,
    bot_names: dict[str, int],
    names_complete: bool,
) -> int | None:
    """Which of a private window's two participants wrote a v0 line, or nobody.

    A private window has two participants and the path names the peer, so once
    the Bot's names are known exhaustively, "not the Bot" *is* "the peer" -- one
    rule, every line covered, and the only failure left is a peer who once
    displayed exactly one of the Bot's names.

    That reasoning is only as good as the map, which is why it is gated on
    *names_complete*.  With an incomplete map elimination is not merely weaker,
    it is wrong in a way that leaves no trace: a line the Bot wrote under an old
    account's name matches nothing and gets handed to the peer.  So without the
    assertion the peer side needs its own positive evidence -- the peer's current
    nickname, which is what a v0 line recorded -- and an unrecognised name stays
    ``_missing`` rather than being attributed to anyone.
    """
    if kind != "private":
        return None
    owner = bot_names.get(name)
    if owner is not None:
        return owner
    if names_complete:
        return target
    try:
        if name == identity.get_user_name(target):
            return target
    except Exception:
        pass
    return None


def _message_record(
    head: dict[str, Any],
    body: str,
    kind: str,
    target: int,
    day,
    bot_ids: set[int],
    version: str,
    bot_names: dict[str, int] | None = None,
    names_complete: bool = False,
) -> dict[str, Any]:
    derived = ["message_type", "time"]
    missing: list[str] = []
    guessed: list[str] = []
    sender_id = head["sender_id"]
    record: dict[str, Any] = {
        "_source": rootfile,
        "_version": version,
        "time": _epoch(day, head["stamp"]),
        "message_type": kind,
        "message": body,
    }
    if kind == "group":
        record["group_id"] = target
        derived.append("group_id")
    if kind == "private":
        # 私聊记录的两半来自两处：``target_id`` 是**窗口**（路径给出的那个对端），
        # ``user_id``/``sender`` 是**作者**（行头，v1 起带号码）。合成一个字段的日子
        # 到此为止——从前的写法是 group 存作者、private 存窗口，于是"谁发的"和
        # "发到哪"共用 ``user_id``，每个消费者都得记住自己在问哪一件事。
        record["target_id"] = target
        derived.append("target_id")
    if sender_id is None:
        # v0 私聊行只有名字、没有号码，作者只能按 bot_names 猜（见 _guess_private_sender）。
        author = _guess_private_sender(head["name"], kind, target, bot_names or {}, names_complete)
        if author is None:
            missing.append("sender")
            missing.append("user_id")
            record["user_id"] = None
        else:
            record["user_id"] = author
            record["sender"] = {"user_id": author, "nickname": head["name"]}
            record["post_type"] = "message_sent" if author in bot_ids else "message"
            derived.append("post_type")
            guessed.append("sender")
            guessed.append("user_id")
    else:
        record["user_id"] = sender_id
        sender: dict[str, Any] = {"user_id": sender_id, "nickname": head["name"]}
        if kind == "group":
            sender["card"] = head["name"]
            sender["title"] = head["title"]
        record["sender"] = sender
        derived.append("sender")
        if not bot_ids:
            missing.append("post_type")
        else:
            # Every account the Bot has ever used, so that lines from before the
            # QQ number change are not read as someone else's.
            record["post_type"] = "message_sent" if sender_id in bot_ids else "message"
            derived.append("post_type")
    message_id = head["message_id"]
    if message_id is None or message_id == "":
        # ``group_upload`` and friends borrow the message shape with no id.
        missing.append("message_id")
    else:
        record["message_id"] = int(message_id) if message_id.lstrip("-").isdigit() else message_id
    if version == V0:
        # A v0 body went through ``_unescape`` on the way in, so it is the
        # display projection and not the bytes OneBot sent.
        derived.append("message")
    record["_derived"] = derived
    record["_missing"] = missing
    record["_guessed"] = guessed
    return record


def _recognise_notice(record: dict[str, Any]) -> None:
    """Recover the machine fields of the two notice families that have consumers.

    This is not the general reversal of notice prose the proposal rules out.
    Those two shapes end in ids the formatter itself wrote, so the id -- the only
    thing either consumer needs -- is read off the right end, not inferred from
    the sentence around it.  A poke needs ``user_id``/``target_id`` because
    ``chat.get_msgs`` re-renders it through ``format_poke``, so the round trip is
    against this file's own formatter and current identity, exactly as a live
    event would be: a rebuilt poke is not a degraded one.  A recall needs only
    the id of the message it took back, so that the boot rebuild does not put a
    recalled message back into memory; it stays opaque text otherwise.

    A line that does not match is left alone.  So a wording change makes one of
    these go missing rather than wrong, which is the direction the version
    detection already takes.
    """
    poke = _POKE.fullmatch(record["text"])
    if poke is not None:
        record["_kind"] = "poke"
        record.update(
            {
                "post_type": "notice",
                "notice_type": "notify",
                "sub_type": "poke",
                "user_id": int(poke["user_id"]),
                "target_id": int(poke["target_id"]),
            }
        )
        record["_derived"] += ["post_type", "notice_type", "sub_type", "user_id", "target_id"]
        return
    recall = _RECALL.search(record["text"])
    if recall is not None:
        record["_kind"] = "recall"
        record["message_id"] = int(recall["message_id"])
        record["_derived"].append("message_id")


def parse_log(
    content: str,
    *,
    kind: str,
    target: int | None,
    day: tuple[int, int, int],
    bot_id: int | None = None,
    version: str | None = None,
    bot_names: dict[str, int] | None = None,
    names_complete: bool = False,
    origin_path: str | None = None,
) -> list[dict[str, Any]]:
    """Read one day's log back into records, marking what is fact and what is not.

    Every record carries ``_source``, ``_derived`` (computed from the path, the
    body or ``bot_id``), ``_missing`` (never written down, so unrecoverable) and
    ``_guessed`` (recovered under a stated assumption that could be wrong --
    today only the sender of a v0 private line, and only when *bot_names* is
    given).  *bot_id* names the current account and *bot_names* maps each of the
    Bot's historical display names to the account behind it; together they are
    what tells a line the Bot wrote from one it received.  *names_complete* says
    the map covers every name the Bot has ever displayed, which is what licenses
    attributing an unrecognised private name to the window's peer.
    Consumers read raw OneBot fields, so a record that quietly lacked one would
    degrade instead of failing; the marks are what keep that visible.

    Notices stay the prose ``write`` produced.  Reversing a localized sentence
    back into ``notice_type``/``sub_type``/``duration`` would break silently on
    the next wording change, so they are timestamped opaque text by definition.
    """
    bot_ids = set((bot_names or {}).values()) | ({bot_id} if bot_id is not None else set())
    records: list[dict[str, Any]] = []
    lines = content.split("\n")
    index = 0
    while index < len(lines):
        head_line = index + 1
        line = lines[index]
        index += 1
        if line == "" or line.startswith("    "):
            continue
        if kind == "bot" or line.startswith(":"):
            stamp = _TIME_SUFFIX.search(line)
            record = {
                "_source": rootfile,
                "_kind": "bot" if kind == "bot" else "notice",
                "_derived": ["time"] if stamp else [],
                "_missing": [] if stamp else ["time"],
                "text": line[2:].rstrip() if line.startswith(": ") else line,
            }
            if stamp is not None:
                record["time"] = _epoch(day, stamp)
                record["text"] = line[2: stamp.start()].rstrip() if line.startswith(": ") else line
            if kind == "group":
                record["group_id"] = target
            elif kind == "private":
                record["user_id"] = target
            _recognise_notice(record)
            if origin_path is not None:
                record["_log_origin"] = f"{origin_path}:{head_line}"
            records.append(record)
            continue
        head = _split_head(line)
        if head is None:
            record = {"_source": rootfile, "_kind": "unparsed", "_derived": [], "_missing": ["time"], "text": line}
            if origin_path is not None:
                record["_log_origin"] = f"{origin_path}:{head_line}"
            records.append(record)
            continue
        body: list[str] = []
        while index < len(lines) and (lines[index] == "" or lines[index].startswith("    ")):
            body.append(lines[index])
            index += 1
        if body != [""]:
            while body and body[-1] == "":
                body.pop()
        record = _message_record(
            head,
            _deltab("\n".join(body)),
            kind,
            target,
            day,
            bot_ids,
            _version_of(head, kind, version),
            bot_names,
            names_complete,
        )
        if origin_path is not None:
            record["_log_origin"] = f"{origin_path}:{head_line}"
        records.append(record)
    return records


# --- range reading ----------------------------------------------------------
#
# The file tree is the authority: ``_group_write`` appends before
# ``history.add_msg``, so every event that ever reached memory is on disk first.
# A range query therefore reads files only -- there is nothing to merge, and no
# window boundary to get wrong.


def window_path(kind: str, target: int | str | None = None, root: str | os.PathLike | None = None) -> Path:
    """The directory one window's day files live in; the inverse of ``window_of``."""
    base = Path(rootfile if root is None else root)
    if kind == "bot":
        return base / "bot"
    if kind not in ("group", "private"):
        raise ValueError(f"未知窗口类型：{kind}")
    return base / kind / str(target)


def _day_bounds(day: tuple[int, int, int]) -> tuple[int, int]:
    """The local-time half-open interval one day file covers."""
    year, month, number = day
    # mktime normalises an out-of-range day number, so ``number + 1`` rolls the
    # month over on its own, and a DST day is 23 or 25 hours as it should be.
    start = int(time.mktime((year, month, number, 0, 0, 0, 0, 0, -1)))
    end = int(time.mktime((year, month, number + 1, 0, 0, 0, 0, 0, -1)))
    return start, end


def _bot_id() -> int | None:
    """The Bot's own id, or ``None`` before login -- ``post_type`` then stays unknown."""
    try:
        return identity.bot_id()
    except Exception:
        return None


def _bot_identities() -> tuple[dict[str, int], bool]:
    """Historical display name -> account, and whether that map is complete.

    The Bot has changed QQ number, so "is this the Bot" is not one id and "what
    was it called" is not one name.  Neither is derivable from a log line, so
    ``chatlog/format`` may carry a ``bot_names`` map from each historical display
    name to the account behind it.

    Completeness is not a second setting: **configuring the map at all is the
    assertion**.  Writing it down means someone looked at which names the archive
    actually contains, and that is exactly what makes elimination sound below.
    The fallback -- the current account's current nickname -- is known to be
    missing everything from before the number change, so it is returned as
    incomplete and nothing may be concluded from a name it fails to match.
    """
    from mods import storage

    override = storage.get(rootfile, "format", lambda: {}).get("bot_names")
    if isinstance(override, dict) and override:
        return {str(name): int(user_id) for name, user_id in override.items()}, True
    try:
        return {identity.get_user_name(identity.bot_id()): identity.bot_id()}, False
    except Exception:
        return {}, False


def _switch_moment() -> int | None:
    from mods import storage

    value = storage.get(rootfile, "format", lambda: {}).get("v1_since")
    return int(value) if value is not None else None


def _day_version(start: int, end: int, switch: int | None) -> str | None:
    """Which format a whole day file is in, or ``None`` when it straddles.

    A straddling day gets no hint, which means group records in it are read as
    v0 -- their body marked a projection when part of the day's is raw.  That is
    the safe direction (under-claiming fidelity) and it affects the single day
    the switch happened on; private records still tell the reader themselves.
    """
    if switch is None:
        return V0
    if start >= switch:
        return V1
    if end <= switch:
        return V0
    return None


def _day_paths(kind: str, target: int | str | None, day: tuple[int, int, int],
               root: str | os.PathLike | None) -> tuple[Path, Path]:
    path = window_path(kind, target, root) / f"{day[0]:04d}-{day[1]:02d}" / f"{day[2]:02d}.log"
    return path, path.with_name(f"{day[2]:02d}.backfill.jsonl")


def _reader_index(directory: Path) -> sqlite3.Connection:
    """Disposable byte-offset view; day files, never SQLite, own the history."""
    database = sqlite3.connect(directory / ".archive-reader.sqlite3")
    database.execute("PRAGMA temp_store=FILE")
    database.execute("PRAGMA cache_size=-2048")
    database.execute("CREATE TABLE IF NOT EXISTS reader_files (day TEXT, source INTEGER, size INTEGER, "
                     "mtime INTEGER, inode INTEGER, PRIMARY KEY (day, source))")
    database.execute("CREATE TABLE IF NOT EXISTS reader_rows (day TEXT, source INTEGER, line INTEGER, "
                     "offset INTEGER, end INTEGER, stamp INTEGER, seq INTEGER, message_id TEXT, "
                     "position INTEGER, visible INTEGER, PRIMARY KEY (day, source, line))")
    database.execute("CREATE INDEX IF NOT EXISTS reader_order ON reader_rows (day, position)")
    database.execute("CREATE INDEX IF NOT EXISTS reader_ids ON reader_rows (day, message_id, visible)")
    return database


def _file_stamp(path: Path) -> tuple[int, int, int] | None:
    if not path.is_file():
        return None
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def _index_day(database: sqlite3.Connection, kind: str, target: int | str | None,
               day: tuple[int, int, int], root: str | os.PathLike | None) -> str:
    """Rebuild a changed day as a disk-backed merge, never a day-sized Python list."""
    ordinary, sidecar = _day_paths(kind, target, day, root)
    day_key = f"{day[0]:04d}-{day[1]:02d}-{day[2]:02d}"
    paths = (ordinary, sidecar)
    stamps = (_file_stamp(ordinary), _file_stamp(sidecar) if kind != "bot" else None)
    indexed = {source: (size, mtime, inode) for source, size, mtime, inode in database.execute(
        "SELECT source, size, mtime, inode FROM reader_files WHERE day=?", (day_key,))}
    if indexed == {source: stamp for source, stamp in enumerate(stamps) if stamp is not None}:
        return day_key
    with database:
        database.execute("DELETE FROM reader_rows WHERE day=?", (day_key,))
        database.execute("DELETE FROM reader_files WHERE day=?", (day_key,))
        if stamps[0] is not None:
            with ordinary.open("rb") as file:
                line_number = 0
                head_line = None
                head_offset = None
                head_text = None
                while True:
                    offset = file.tell()
                    chunk = file.readline()
                    if not chunk:
                        break
                    line_number += 1
                    if chunk.startswith(b"    ") or not chunk.strip():
                        continue
                    if head_line is not None:
                        _index_ordinary(database, day_key, kind, target, day, head_line,
                                        head_offset, offset, head_text)
                    head_line, head_offset = line_number, offset
                    head_text = chunk.decode("utf-8")
                if head_line is not None:
                    _index_ordinary(database, day_key, kind, target, day, head_line,
                                    head_offset, file.tell(), head_text)
        if stamps[1] is not None:
            for line, offset, message in _backfill_archive._iter_sidecar(sidecar, rootfile if root is None else root):
                database.execute(
                    "INSERT INTO reader_rows VALUES (?, 0, ?, ?, NULL, ?, ?, ?, NULL, 1)",
                    (day_key, line, offset, int(message["time"]), int(message["message_seq"]),
                     recall_key(message["message_id"])),
                )
        ordinary_rows = iter(database.execute(
            "SELECT source, line, stamp, message_id FROM reader_rows WHERE day=? AND source=1 ORDER BY line",
            (day_key,)))
        sidecar_rows = iter(database.execute(
            "SELECT source, line, stamp, message_id FROM reader_rows WHERE day=? AND source=0 "
            "ORDER BY stamp, seq, CAST(line AS TEXT)", (day_key,)))
        additional = next(sidecar_rows, None)
        position = 0
        for row in ordinary_rows:
            while additional is not None and row[2] is not None and additional[2] <= row[2]:
                position = _place_row(database, day_key, additional, position)
                additional = next(sidecar_rows, None)
            position = _place_row(database, day_key, row, position)
        while additional is not None:
            position = _place_row(database, day_key, additional, position)
            additional = next(sidecar_rows, None)
        for source, path in enumerate(paths):
            stamp = _file_stamp(path) if source == 0 or kind != "bot" else None
            if stamp is not None:
                database.execute("INSERT INTO reader_files VALUES (?, ?, ?, ?, ?)", (day_key, source, *stamp))
    return day_key


def _index_ordinary(database: sqlite3.Connection, day_key: str, kind: str,
                    target: int | str | None, day: tuple[int, int, int], line: int,
                    offset: int, end: int, head: str) -> None:
    parsed = parse_log(head, kind=kind, target=None if kind == "bot" else int(target), day=day)
    if not parsed:
        return
    record = parsed[0]
    # WHY: A recall notice names the message it withdrew; it is not another
    # stored copy of that message and must not hide it from archive reads.
    message_id = record.get("message_id") if record.get("_kind") != "recall" else None
    database.execute("INSERT INTO reader_rows VALUES (?, 1, ?, ?, ?, ?, NULL, ?, NULL, 1)",
                     (day_key, line, offset, end, record.get("time"),
                      recall_key(message_id) if message_id is not None else None))


def _place_row(database: sqlite3.Connection, day_key: str, row: tuple, position: int) -> int:
    source, line, _stamp, message_id = row
    if message_id is not None:
        database.execute("UPDATE reader_rows SET visible=0 WHERE day=? AND message_id=? AND visible=1",
                         (day_key, message_id))
    database.execute("UPDATE reader_rows SET position=?, visible=1 WHERE day=? AND source=? AND line=?",
                     (position, day_key, source, line))
    return position + 1


def _read_indexed_record(kind: str, target: int | str | None, day: tuple[int, int, int],
                         row: tuple, bot_id: int | None, switch: int | None,
                         bot_names: dict[str, int], names_complete: bool,
                         root: str | os.PathLike | None) -> dict[str, Any]:
    source, line, offset, end = row
    ordinary, sidecar = _day_paths(kind, target, day, root)
    base = Path(rootfile if root is None else root)
    path = sidecar if source == 0 else ordinary
    with path.open("rb") as file:
        file.seek(offset)
        chunk = file.readline() if end is None else file.read(end - offset)
    origin = f"{path.relative_to(base)}:{line}"
    if source == 1:
        start, finish = _day_bounds(day)
        records = parse_log(chunk.decode("utf-8"), kind=kind,
                            target=None if kind == "bot" else int(target), day=day,
                            bot_id=bot_id, version=_day_version(start, finish, switch),
                            bot_names=bot_names, names_complete=names_complete)
        record = records[0]
        record["_log_origin"] = origin
        return record
    message = json.loads(chunk)
    _backfill_archive._validate(message)
    parts = (kind, str(target), f"{day[0]:04d}-{day[1]:02d}", sidecar.name)
    record = _backfill_archive._project(message, sidecar, line, parts, base)
    if isinstance(record.get("raw_message"), str):
        record["message"] = record["raw_message"]
    bot_ids = set(bot_names.values()) | ({bot_id} if bot_id is not None else set())
    if "post_type" not in record and bot_ids:
        record["post_type"] = "message_sent" if int(record["user_id"]) in bot_ids else "message"
        record["_derived"].append("post_type")
        record["_missing"].remove("post_type")
    return record


def _locate_origin(kind: str, target: int | str | None, origin: str,
                   root: str | os.PathLike | None,
                   database: sqlite3.Connection) -> tuple[tuple[int, int, int], int, tuple]:
    directory = window_path(kind, target, root)
    base = Path(rootfile if root is None else root)
    if not isinstance(origin, str):
        raise ValueError("chatlog 游标不是记录定位")
    origin_path, separator, line_text = origin.rpartition(":")
    relative = Path(origin_path)
    if (not separator or not line_text.isdecimal() or str(int(line_text)) != line_text
            or relative.is_absolute() or str(relative) != origin_path
            or relative.parent.parent != directory.relative_to(base)):
        raise ValueError("chatlog 游标不属于当前窗口")
    path = base / relative
    day = _archive_day(path)
    if (day is None or not path.is_file()
            or not path.resolve().is_relative_to(directory.resolve())):
        raise ValueError("chatlog 游标不是记录定位")
    day_key = _index_day(database, kind, target, day, root)
    source = 0 if path.name.endswith(".backfill.jsonl") else 1
    row = database.execute("SELECT source, line, offset, end, position FROM reader_rows "
                           "WHERE day=? AND source=? AND line=?",
                           (day_key, source, int(line_text))).fetchone()
    if row is None:
        raise ValueError("chatlog 游标不是记录定位")
    return day, row[4], row[:4]


def read_origin(
    kind: str,
    target: int | str | None,
    origin: str,
    *,
    bot_id: int | None = None,
    root: str | os.PathLike | None = None,
) -> dict[str, Any]:
    """Read exactly one existing record by its stable window-local origin."""
    directory = window_path(kind, target, root)
    if not directory.is_dir():
        raise ValueError("chatlog 游标不是记录定位")
    bot_names, names_complete = _bot_identities()
    with _append_lock, _backfill_archive._lock, _reader_lock, closing(_reader_index(directory)) as database:
        day, _, row = _locate_origin(kind, target, origin, root, database)
        return _read_indexed_record(kind, target, day, row, _bot_id() if bot_id is None else bot_id,
                                    _switch_moment(), bot_names, names_complete, root)


def read_range(
    kind: str,
    target: int | str | None = None,
    *,
    since: int | None = None,
    until: int | None = None,
    limit: int | None = None,
    before: str | None = None,
    bot_id: int | None = None,
    root: str | os.PathLike | None = None,
) -> list[dict[str, Any]]:
    """Rebuild one window's records for a time range, newest first.

    Ordinary log records retain append order; independently archived NapCat
    messages merge by time and sequence, with a stable same-second tie rule.
    Bounds are inclusive epoch seconds;
    ``None`` means open.  Records the parser
    could not place in time (an unstamped legacy line) are dropped as soon as
    either bound is set -- a time range cannot answer for them -- and kept when
    both are open, which applies no filter at all.

    Everything returned carries ``_source``/``_derived``/``_missing``, so a
    caller can tell a rebuilt record from a live event.  Two differences from
    ``history.getlog()`` are inherent to reading files rather than memory:
    recalled messages are still here (the tree is append-only; the recall is a
    separate notice line), and notices are the opaque prose ``write`` produced.

    ``limit`` caps the result at the newest that many records, and the day walk
    stops as soon as enough are collected -- that is what makes backfilling past
    the in-memory cap (``history.MAX_LEN``) affordable instead of reading the
    whole tree.  ``None`` means no cap.  ``before`` is the ``_log_origin`` of a
    record in this window; that record is excluded and the walk continues from
    the preceding record in merged order.  A copy hidden by a later duplicate
    remains a valid locator.  Invalid or foreign cursors fail rather than
    silently returning an unrelated page.
    """
    directory = window_path(kind, target, root)
    if not directory.is_dir():
        if before is not None:
            raise ValueError("chatlog 游标不是记录定位")
        return []
    if bot_id is None:
        bot_id = _bot_id()
    switch = _switch_moment()
    bot_names, names_complete = _bot_identities()
    if limit is not None and limit <= 0 and before is None:
        return []
    records: list[dict[str, Any]] = []
    with _append_lock, _backfill_archive._lock, _reader_lock, closing(_reader_index(directory)) as database:
        cursor_day = cursor_position = None
        if before is not None:
            cursor_day, cursor_position, _ = _locate_origin(kind, target, before, root, database)
        for day in _archive_days(directory):
            if cursor_day is not None and day > cursor_day:
                continue
            start, end = _day_bounds(day)
            if since is not None and end <= since:
                break
            if until is not None and start > until:
                continue
            day_key = _index_day(database, kind, target, day, root)
            maximum = cursor_position if day == cursor_day else None
            for row in database.execute(
                    "SELECT source, line, offset, end, stamp FROM reader_rows WHERE day=? AND visible=1 "
                    "AND (? IS NULL OR position<?) AND (? IS NULL OR stamp>=?) "
                    "AND (? IS NULL OR stamp<=?) ORDER BY position DESC",
                    (day_key, maximum, maximum, since, since, until, until)):
                record = _read_indexed_record(kind, target, day, row[:4], bot_id, switch,
                                              bot_names, names_complete, root)
                records.append(record)
                if limit is not None and len(records) >= limit:
                    return records[:limit]
    return records


def known_windows() -> list[tuple[str, int]]:
    """Only windows already represented in the local archive are boot candidates."""
    windows = []
    for kind in ("group", "private"):
        base = Path(rootfile) / kind
        if base.is_dir():
            windows.extend((kind, int(path.name)) for path in base.iterdir()
                           if path.is_dir() and path.name.isdecimal())
    return windows


def last_message_anchor(kind: str, target: int) -> str | None:
    """Freeze the latest reliable QQ message identity before live ingress begins."""
    for day in _archive_days(window_path(kind, target)):
        directory = window_path(kind, target) / f"{day[0]:04d}-{day[1]:02d}"
        ordinary = directory / f"{day[2]:02d}.log"
        sidecar = directory / f"{day[2]:02d}.backfill.jsonl"
        latest: tuple[int, int, int, str] | None = None
        if ordinary.is_file():
            with ordinary.open(encoding="utf-8") as file:
                for line_number, line in enumerate(file):
                    if line.startswith("    "):
                        continue
                    head = _split_head(line.rstrip("\r\n"))
                    message_id = head.get("message_id") if head is not None else None
                    if message_id is not None and message_id.lstrip("-").isdigit():
                        candidate = (_epoch(day, head["stamp"]), 1, line_number,
                                     recall_key(message_id))
                        if latest is None or candidate[:3] > latest[:3]:
                            latest = candidate
        if sidecar.is_file():
            for record in _backfill_archive.iter_day(sidecar, root=rootfile):
                message_id = record.get("message_id")
                if message_id is None or not str(message_id).lstrip("-").isdigit():
                    continue
                candidate = (int(record["time"]), 0, int(record["message_seq"]),
                             recall_key(message_id))
                if latest is None or candidate[:3] > latest[:3]:
                    latest = candidate
        if latest is not None:
            return latest[3]
    return None


def freeze_boot_anchors() -> dict[tuple[str, int], str | None]:
    """Freeze local anchors before the OneBot listener accepts a live message."""
    global _boot_anchors
    with _append_lock:
        if _boot_anchors is None:
            _boot_anchors = {window: last_message_anchor(*window) for window in known_windows()}
        return dict(_boot_anchors)


def recalled_ids(kind: str, target: int | str) -> set[str]:
    """Index recall notices from the chatlog authority, then follow live writes."""
    key = kind, int(target)
    with _recall_lock:
        if key in _recalls:
            return set(_recalls[key])
    found: set[str] = set()
    for path in sorted(window_path(*key).rglob("*.log")):
        day = day_of(path)
        if day is None:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            logger.exception("读取 chatlog 撤回记录失败")
            raise
        for line in content.splitlines():
            if not line.startswith(": "):
                continue
            stamp = _TIME_SUFFIX.search(line)
            shown = line[2:stamp.start()].rstrip() if stamp else line[2:].rstrip()
            recall = _RECALL.search(shown)
            if recall is not None:
                found.add(recall_key(recall["message_id"]))
    with _recall_lock:
        found.update(_live_recalls.get(key, ()))
        if key in _recalls:
            _recalls[key].update(found)
        else:
            _recalls[key] = found
        return set(_recalls[key])


# --- rebuilding recent history at boot --------------------------------------
#
# This replaces ``data/cache_msgs``, which was a second write authority for the
# same events and cost about a second of ``literal_eval`` at every boot -- more
# than rebuilding the whole tree.
#
# What goes back in is **v1 message records and pokes**.  A v0 body is a display
# projection and a v0 private record has no sender at all, so ``chat.msg2chat``
# would read the Bot's own past lines as the peer's -- hence the floor at the
# switch moment, which also bounds the walk: it stops at the first day file
# entirely older than the switch instead of walking the archive.  A poke has no
# such gap, because its only consumer re-renders it through ``format_poke``.
#
# Recalled messages are dropped.  Walking backwards means the recall notice is
# always read before the message it took back, so one pass is enough.  This is
# the rebuild only: ``read_range`` still returns them, because the archive's
# answer to "what happened" includes what was later taken back.


def _restore_window(kind: str, target: int, count: int, floor: int) -> list[dict[str, Any]]:
    """The newest *count* rebuildable events of one window, newest first."""
    if count <= 0:
        return []
    got: list[dict[str, Any]] = []
    directory = window_path(kind, target)
    if not directory.is_dir():
        return got
    with _append_lock, _backfill_archive._lock, _reader_lock, closing(_reader_index(directory)) as database:
        database.execute("CREATE TEMP TABLE reader_recalls (message_id TEXT PRIMARY KEY)")
        for day in _archive_days(directory):
            start, end = _day_bounds(day)
            if end <= floor:
                break
            day_key = _index_day(database, kind, target, day, None)
            for row in database.execute(
                    "SELECT source, line, offset, end FROM reader_rows WHERE day=? AND visible=1 "
                    "ORDER BY position DESC", (day_key,)):
                record = _read_indexed_record(kind, target, day, row, _bot_id(), floor, {}, False, None)
                if record.get("_kind") == "recall":
                    database.execute("INSERT OR IGNORE INTO reader_recalls VALUES (?)",
                                     (recall_key(record["message_id"]),))
                message_id = record.get("message_id")
                recalled = message_id is not None and database.execute(
                    "SELECT 1 FROM reader_recalls WHERE message_id=?",
                    (recall_key(message_id),)).fetchone() is not None
                if ((record.get("_version") in (V1, "raw") and record.get("post_type")
                     and not recalled)
                        or record.get("_kind") == "poke"):
                    got.append(record)
                    if len(got) >= count:
                        return got
    return got


def _restore_history() -> tuple[int, int]:
    """Fill ``history.msgs`` from the files, and report how much was restored.

    Nothing is restored before the switch has any days behind it, which is the
    honest outcome rather than a failure: the older records exist and stay
    readable through the range query, they are just not faithful enough to be
    handed to consumers that expect live events.
    """
    floor = _switch_moment()
    if floor is None:
        return 0, 0
    windows = records = 0
    for kind in ("group", "private"):
        base = Path(rootfile) / kind
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if not (entry.is_dir() and entry.name.isdigit()):
                continue
            got = _restore_window(kind, int(entry.name), history.MAX_LEN, floor)
            if not got:
                continue
            with history.lock():
                history.msgs[kind][int(entry.name)] = got
            windows += 1
            records += len(got)
    return windows, records
