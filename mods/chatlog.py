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
LOAD_AFTER = ("storage", "history", "identity")
rootfile = "chatlog"
logger = logging.getLogger(__name__)


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
                    shown = display(line)
                    if expression.search(shown):
                        matches.append(f"{path}:{line_number}:{shown.rstrip()}")
        except OSError:
            continue
    return "\n".join(matches)


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
    """Render a poke, with ids in both windows.

    A private line used to carry names only, which is the same asymmetry v1
    closed for private message records: with no ids the line cannot say which
    side poked, and the two candidates are only distinguishable by a nickname
    anyone can change.  Now both windows render alike, and ``_recognise_notice``
    reads this back exactly -- which is all a rebuilt poke has to be, because
    ``chat.get_msgs`` hands the event straight back to this function.
    """
    user_id, target_id = int(msg["user_id"]), int(msg["target_id"])
    return f"{identity.getname(user_id)}({user_id})戳了戳{identity.getname(target_id)}({target_id})"


def _message(msg: dict[str, Any]) -> str:
    timestamp = msg["time"]
    sender = msg.get("sender")
    if not isinstance(sender, dict):
        sender = {"user_id": msg["user_id"]}
        msg["sender"] = sender
    identity.update(msg)
    sender_id = int(sender.get("user_id", msg["user_id"]))
    kind, target = history.window(msg) or ("private", sender_id)
    if kind == "group":
        group_id = int(target)
        title, display = identity.get_group_user_info(group_id, sender_id)
        return _group_write(msg, group_id, format_message(msg, display, title))
    window_user = int(target)
    display = identity.get_user_name(sender_id)
    # v1: a private record carries the sender the way a group record always did.
    # Without it the Bot's own line and the peer's line differ only by a nickname
    # anyone can change, so a private window has no reliable author at all.
    return _private_write(msg, window_user, format_message(msg, display))


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


def _message_record(head: dict[str, Any], body: str, kind: str, target: int, day, bot_id: int | None, version: str) -> dict[str, Any]:
    derived = ["message_type", "time"]
    missing: list[str] = []
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
    if sender_id is None:
        missing.append("sender")
        record["user_id"] = target
        derived.append("user_id")
    else:
        record["user_id"] = sender_id if kind == "group" else target
        sender: dict[str, Any] = {"user_id": sender_id, "nickname": head["name"]}
        if kind == "group":
            sender["card"] = head["name"]
            sender["title"] = head["title"]
        record["sender"] = sender
        derived.append("sender")
        if bot_id is None:
            missing.append("post_type")
        else:
            record["post_type"] = "message_sent" if sender_id == bot_id else "message"
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
) -> list[dict[str, Any]]:
    """Read one day's log back into records, marking what is fact and what is not.

    Every record carries ``_source``, ``_derived`` (computed from the path, the
    body or ``bot_id``) and ``_missing`` (never written down, so unrecoverable).
    Consumers read raw OneBot fields, so a record that quietly lacked one would
    degrade instead of failing; the marks are what keep that visible.

    Notices stay the prose ``write`` produced.  Reversing a localized sentence
    back into ``notice_type``/``sub_type``/``duration`` would break silently on
    the next wording change, so they are timestamped opaque text by definition.
    """
    records: list[dict[str, Any]] = []
    lines = content.split("\n")
    index = 0
    while index < len(lines):
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
            records.append(record)
            continue
        head = _split_head(line)
        if head is None:
            records.append({"_source": rootfile, "_kind": "unparsed", "_derived": [], "_missing": ["time"], "text": line})
            continue
        body: list[str] = []
        while index < len(lines) and (lines[index] == "" or lines[index].startswith("    ")):
            body.append(lines[index])
            index += 1
        if body != [""]:
            while body and body[-1] == "":
                body.pop()
        records.append(
            _message_record(head, _deltab("\n".join(body)), kind, target, day, bot_id, _version_of(head, kind, version))
        )
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


def read_range(
    kind: str,
    target: int | str | None = None,
    *,
    since: int | None = None,
    until: int | None = None,
    bot_id: int | None = None,
    root: str | os.PathLike | None = None,
) -> list[dict[str, Any]]:
    """Rebuild one window's records for a time range, newest first.

    The order is the append order reversed, not a sort by ``time``: the log
    means "this was written after that", and a record the parser could not
    timestamp still has a place in it.  Bounds are inclusive epoch seconds;
    ``None`` means open.  Records the parser
    could not place in time (an unstamped legacy line) are dropped as soon as
    either bound is set -- a time range cannot answer for them -- and kept when
    both are open, which applies no filter at all.

    Everything returned carries ``_source``/``_derived``/``_missing``, so a
    caller can tell a rebuilt record from a live event.  Two differences from
    ``history.getlog()`` are inherent to reading files rather than memory:
    recalled messages are still here (the tree is append-only; the recall is a
    separate notice line), and notices are the opaque prose ``write`` produced.
    """
    directory = window_path(kind, target, root)
    if not directory.is_dir():
        return []
    if bot_id is None:
        bot_id = _bot_id()
    switch = _switch_moment()
    records: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*.log")):
        day = day_of(path)
        if day is None:
            continue
        start, end = _day_bounds(day)
        if since is not None and end <= since:
            continue
        if until is not None and start > until:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            logger.exception("读取 chatlog 文件失败：%s", path)
            continue
        parsed = parse_log(
            content,
            kind=kind,
            target=None if kind == "bot" else int(target),
            day=day,
            bot_id=bot_id,
            version=_day_version(start, end, switch),
        )
        for record in parsed:
            when = record.get("time")
            if since is not None and (when is None or when < since):
                continue
            if until is not None and (when is None or when > until):
                continue
            records.append(record)
    records.reverse()
    return records


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
    got: list[dict[str, Any]] = []
    recalled: set[int] = set()
    for path in sorted(window_path(kind, target).rglob("*.log"), reverse=True):
        day = day_of(path)
        if day is None:
            continue
        start, end = _day_bounds(day)
        if end <= floor:
            break
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            logger.exception("重建近期窗口时读取失败：%s", path)
            continue
        parsed = parse_log(
            content,
            kind=kind,
            target=target,
            day=day,
            bot_id=_bot_id(),
            version=_day_version(start, end, floor),
        )
        recalled.update(record["message_id"] for record in parsed if record.get("_kind") == "recall")
        got = [
            record
            for record in parsed
            if (record.get("_version") == V1 and record.get("message_id") not in recalled)
            or record.get("_kind") == "poke"
        ] + got
        if len(got) >= count:
            break
    return list(reversed(got[-count:]))


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
