"""Render chat windows for the model in the chatlog's own record format.

WHY: the model reads exactly what ``chatlog/`` stores, so "read more" is reading
the same files with the same eyes; there is no second message format to keep in
step with the first.  The window and day live in the path, so a screen adds one
``# g群号 YYYY-MM-DD`` line where the path would have been.

Only ordinary ``.log`` day files are read.  Offline backfill sidecars
(``.backfill.jsonl``) are not rendered yet; a recovered gap shows up only once it
is written back as ordinary lines.
"""

from __future__ import annotations

from pathlib import Path
import re
import time
from typing import Iterable

from mods import chatlog, context, identity, oplog, storage

import mods.chat as _chat_root
from . import recognize as _recognize, view as _view, windows as _windows
from .windows import label

# A window shows at least this many lines before its first unseen one.
LEAD_IN = 3
# 一次最多补多少条新消息。WHY: 2026-10-26 维护者：200 条一次塞进来太肥，折到 60。
FRESH_MAX = 60
_SEEN_KEY = "seen"


def _seen() -> dict:
    data = storage.get("", "agent")
    value = data.get(_SEEN_KEY)
    if not isinstance(value, dict):
        value = data[_SEEN_KEY] = {}
    return value


def seen_snapshot() -> dict:
    return dict(_seen())


def restore_seen(labels: list[str], snapshot: dict) -> None:
    seen = _seen()
    for text in labels:
        if snapshot.get(text) is None:
            seen.pop(text, None)
        else:
            seen[text] = snapshot[text]
    storage.save()


def mark_window_read(window: tuple, through=...) -> dict:
    """Mark the ordered unread prefix as read without creating input events.

    *through* is the last arrival to mark, as ``oplog.latest_pending_arrival``
    gave it when the screen was read; left out, it is whatever is unread now.
    """
    with context.window_lock(window):
        if through is ...:
            through = oplog.latest_pending_arrival(window)
        pending = ([entry for entry in oplog.unread(window)
                    if oplog.arrival_before_or_at(entry["arrival"], through)]
                   if through is not None else [])
        marked = oplog.mark_arrivals_read(window, [entry["arrival"] for entry in pending]) if pending else 0
        return {"marked_read": marked,
                "mentions": sum(entry.get("activation_kind") == "mention" for entry in pending),
                "remaining": len(oplog.unread(window))}


def mark_seen(newest: dict[tuple, tuple]) -> None:
    seen = _seen()
    for window, (stamp, through) in newest.items():
        if stamp is not None:
            seen[label(window)] = max(stamp, seen.get(label(window), 0))
        mark_window_read(window, through=through)
    storage.save()


def _day_files(window: tuple) -> list[tuple[tuple[int, int, int], Path]]:
    directory = chatlog.window_path(window[0], window[1])
    found = []
    for day in chatlog._archive_days(directory):
        path, _sidecar = chatlog._day_paths(window[0], window[1], day, None)
        if path.is_file():
            found.append((day, path))
    return found


def _record_time(day: tuple[int, int, int], head: str) -> int | None:
    match = chatlog._TIME_SUFFIX.search(head.rpartition(" | ")[0] or head)
    return chatlog._epoch(day, match) if match else None


def records(window: tuple, count: int, before: int | None = None, since: float | None = None) -> list[dict]:
    """The newest *count* records of one window, oldest first.

    *before* is an exclusive Unix time; it pages backwards through the files.
    *since* stops at day files that end before it.
    """
    if count <= 0:
        return []
    picked: list[dict] = []
    for day, path in _day_files(window):
        if since is not None and chatlog._day_bounds(day)[1] < since:
            break
        lines = path.read_text(encoding="utf-8").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        day_records = []
        for start, end in chatlog._file_records(lines):
            # WHY: `#` 开头的消息是控制与调试输出（子命令结果、traceback、`#hint` 状态），
            # 按跨模块约定不回流进模型，见 docs/llm.md「上下文如何组装」。
            if end - start > 1 and lines[start + 1].startswith("    #"):
                continue
            text = "\n".join(lines[start:end])
            stamp = _record_time(day, lines[start])
            if before is not None and stamp is not None and stamp >= before:
                continue
            day_records.append({"day": day, "time": stamp, "text": text})
        picked[:0] = day_records[-(count - len(picked)):]
        if len(picked) >= count:
            break
    return picked


def render(window: tuple, items: list[dict], seen: int | None = None) -> str:
    """Join records under day headers; mark where unseen records begin."""
    lines: list[str] = []
    texts = _recognize.annotate(window, [item["text"] for item in items])
    current = None
    divided = seen is None
    for index, item in enumerate(items):
        if item["day"] != current:
            current = item["day"]
            lines.append(f"# {label(window)} {current[0]:04d}-{current[1]:02d}-{current[2]:02d}")
        if not divided and item["time"] is not None and item["time"] > seen:
            lines.append("—— 以下是上次之后的新消息 ——")
            divided = True
        lines.append(texts[index])
    return "\n".join(lines)


def latest_time(items: list[dict]) -> int | None:
    stamps = [item["time"] for item in items if item["time"] is not None]
    return max(stamps) if stamps else None


def clock(stamp: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() if stamp is None else stamp))


_HEADER_QQ = re.compile(
    r"^(?:.*)\(([1-9]\d{4,11})\)\s+(?:(?:\d{4}-\d{2}-\d{2}\s+)?\d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2})(?:\.\d+)?(?:\s*(?:\|.*|\.\.\..*))?\s*$"
)
_POKE_QQ = re.compile(
    r"^:?\s*(?:.*?)\(([1-9]\d{4,11})\)戳了戳(?:.*?)\(([1-9]\d{4,11})\)"
)
_CQ_AT = re.compile(r"\[CQ:at,[^\]]*?\bqq=([1-9]\d{4,11})\b[^\]]*?\]")
_TEXT_AT = re.compile(r"@(?:[^\s()@]+)?\(([1-9]\d{4,11})\)")


def extract_qqs(texts: Iterable[str]) -> list[int]:
    """Extract valid user QQ numbers from message headers and mentions, oldest first.

    WHY: 自动挂载人设笔记需要知道当前画面里有哪些人发言或被提及。画面文本为
    chatlog 原生格式，消息头形如 昵称(QQ号) 时刻 或 (QQ号) 日期 ...，消息体内为
    [CQ:at,qq=...] 或 @昵称(QQ号)；过滤 Bot 自身 QQ，保留首次出现顺序去重。
    """
    try:
        bot = identity.bot_id()
    except RuntimeError:
        bot = None
    result: list[int] = []
    seen: set[int] = set()

    def add(qq: int) -> None:
        if bot is not None and qq == bot:
            return
        if qq not in seen:
            seen.add(qq)
            result.append(qq)

    for text in texts:
        for line in text.splitlines():
            line_str = line.rstrip()
            if not line_str or line_str.startswith("#") or line_str.startswith("——"):
                continue
            poke_match = _POKE_QQ.match(line_str)
            if poke_match:
                add(int(poke_match.group(1)))
                add(int(poke_match.group(2)))
                continue
            matches: list[tuple[int, int]] = []
            if not line.startswith("    "):
                header_match = _HEADER_QQ.match(line_str)
                if header_match:
                    matches.append((header_match.start(1), int(header_match.group(1))))
            for m in _CQ_AT.finditer(line_str):
                matches.append((m.start(1), int(m.group(1))))
            for m in _TEXT_AT.finditer(line_str):
                matches.append((m.start(1), int(m.group(1))))
            matches.sort(key=lambda x: x[0])
            for _, qq in matches:
                add(qq)
    return result


def _offline_calls(window: tuple, shown: list[dict]) -> list[dict]:
    """Unread recovered calls to her that the screen's slice leaves out, oldest first.

    WHY: 补回的消息按时间插在开机后实时到的消息前面。这一段已经给过这个窗口时，画面只
    接着"上次给到哪一条"往后给，看到时间也已经过了它们，叫她的那条就不在画面里，第一次
    输出却会把它标成已读（2026-10-23 审查）。所以按消息号把还没读的离线呼叫找出来补上。
    """
    from mods import _backfill

    calls = [entry["event"] for entry in oplog.unread(window)
             if entry.get("activation_kind") == _backfill.OFFLINE_CALL]
    if not calls:
        return []
    wanted = {chatlog.recall_key(event.get("message_id")) for event in calls}
    texts = {item["text"] for item in shown}
    found = []
    for item in records(window, 1_000_000, since=min(int(event["time"]) for event in calls)):
        head = chatlog._split_head(item["text"].partition("\n")[0])
        if (item["text"] not in texts and head is not None and head["message_id"] is not None
                and chatlog.recall_key(head["message_id"]) in wanted):
            found.append(item)
    return found


def _with_calls(window: tuple, calls: list[dict], text: str) -> str:
    if not calls:
        return text
    return ("（下面是离线期间叫你的消息，补回时插在了更早的位置）\n" + render(window, calls)
            + ("\n" + text if text else ""))


def slice_window(window: tuple, count: int, last: str | None) -> tuple[str, tuple, str | None]:
    """One window's chatlog lines for this wake, the (time, arrival) to mark seen, and its last record.

    *last* is the last record of this window already shown earlier in this segment;
    then only the records after it are new.
    """
    mark = _seen().get(label(window))
    # WHY: 标已读只标到读画面这一刻为止的未读（2026-10-22 审查发现：生成期间同窗口
    # 再来的 at 会在第一次输出时一起被标掉，收尾不再接续）。chatlog 写入与登记到达
    # 在同一把窗口锁里、先写后登记，所以先取边界再读文件，边界内的都在画面能读到的文件里。
    through = oplog.latest_pending_arrival(window)
    # WHY: 维护者 2026-10-24：follow 期间（包括说完话自动听）到的消息不截，之前的照旧最多 _FRESH_MAX
    # 条。她选了要跟上，dismiss 掉又回来的那批要原样给全，不靠 read_log 往前翻；一个群一天的消息放得下。
    # 只按"现在盖着 follow"不行：follow 一个很久没看的群，第一屏会把几周的积压全倒出来。
    layer = _chat_root._awaker.layer(window)
    since = layer.get("since") if layer is not None and layer["state"] == "follow" else None
    # 记录时间只到秒：起点取整，同一秒里到的也算 follow 期间。
    during = (sum(1 for item in records(window, 1_000_000, since=since)
                  if item["time"] is not None and item["time"] >= int(since)) if since is not None else 0)
    cap = FRESH_MAX + during
    items = records(window, max(count, 1) + cap)
    texts = [item["text"] for item in items]
    if last is not None and last in texts:
        # WHY: 这一段里已经给过这个窗口：按"上次给到哪一条"接着给，不按看到时间——看到
        # 时间只精确到秒，同一秒里后到的那条按时间会被当成看过。
        after = items[len(texts) - texts[::-1].index(last):]
        shown = after[-cap:]
        calls = _offline_calls(window, shown)
        if not shown:
            return _with_calls(window, calls, ""), (None, through), last
        text = render(window, shown, mark)
        if len(after) > cap:
            text = (f"（{label(window)} 又有 {len(after)} 条新消息，这里只展示最新的 {len(shown)} 条；"
                    f"更早的用 read_log(\"{label(window)}\", before=\"{clock(shown[0]['time'])}\") 往前翻）\n" + text)
        return _with_calls(window, calls, text), (latest_time(shown), through), shown[-1]["text"]
    fresh = [item for item in items if mark is not None and item["time"] is not None and item["time"] > mark]
    # WHY: 维护者 2026-10-25：交接后 last_shown 清空、看到位置还在，按 count 补旧消息会把
    # 刚压缩掉的原文倒回来。有 mark 时只带几条接话，也兜住看到时间只精确到秒的边界；
    # 从没看过的窗口才按 count 补历史，新消息仍按原来的上限给。
    lead = LEAD_IN if mark is not None else max(count - len(fresh), LEAD_IN)
    older = [item for item in items if item not in fresh][-lead:]
    shown = older + fresh if mark is not None else items[-max(count, LEAD_IN):]
    skipped = 0
    if len(fresh) > cap:
        # WHY: 维护者 2026-10-22 定：新消息太多（比如离线一晚）只展示最新的 _FRESH_MAX 条，
        # 但要明说跳过了多少、从哪里接着翻，看到位置照样推进；不静默丢，也不撑爆上下文。
        total = sum(1 for item in records(window, 1_000_000, since=mark)
                    if item["time"] is not None and item["time"] > mark)
        shown = fresh[-cap:]
        skipped = total - len(shown)
    if not shown:
        return "", (None, through), last
    text = render(window, shown, mark)
    if skipped:
        text = (f"（{label(window)} 上次之后有 {skipped + len(shown)} 条新消息，这里只展示最新的 "
                f"{len(shown)} 条；更早的 {skipped} 条要看就用 read_log(\"{label(window)}\", "
                f"before=\"{clock(shown[0]['time'])}\") 往前翻）\n" + text)
    return _with_calls(window, _offline_calls(window, shown), text), (latest_time(shown), through), shown[-1]["text"]


def unread_hint(on_screen: set[tuple]) -> str:
    rows = []
    try:
        bot = identity.bot_id()
    except RuntimeError:
        bot = None
    for detail in oplog.pending_details():
        window = tuple(detail["window"])
        if not _windows.known(window) or window in on_screen:
            continue
        # WHY: 2026-10-28 维护者：排除 Bot 自身发言回显，只统计他人发来的未读消息。
        # 避免她自己刚 send 完几条消息就被当成对方有新消息催她，害她虚惊一场。
        entries = oplog.unread(window)
        others = [e for e in entries if bot is None or (
            e.get("event", {}).get("user_id") != bot and
            str(e.get("event", {}).get("author")) != str(bot)
        )]
        if not others:
            continue
        mentions = sum(1 for e in others if e.get("activated") and e.get("activation_kind") == "mention")
        rows.append(f"{_windows.title(window)} {len(others)} 条"
                    + (f"（@你 {mentions}）" if mentions else "")
                    + "".join(f"（{badge}）" for badge in _chat_root._awaker.badges(window)))
    return ("【其它窗口】还有没看过的消息：" + "；".join(rows[:10])
            + "。要看就用 read_log 读原文。") if rows else ""


def late_arrivals(newest: dict[tuple, tuple], last_shown: dict[str, str],
                  count: int) -> tuple[list[dict], dict[tuple, tuple]]:
    """Messages that reached the windows on screen since she read them, to append to this segment.

    Advances *newest* and *last_shown* now; the marks it returns are applied
    (seen time, read) only once she has produced output after seeing them.

    WHY: 维护者 2026-10-23：私聊每条都叫醒她以后，人分几条发的话会在她干活时陆续到，
    不用延迟、也不打断请求，下一次子请求之前写进这一段，由她自己决定接不接（参照 Cortico
    在轮次边界投递）。以前挂在请求末尾的提示里，每次重算、不进这一段，收尾还要为它们再叫
    一次。

    WHY: 已读要等她看过以后有了一次输出（哪怕只是思考）才标，维护者说这样最稳：请求失败
    时它们仍是未读，叫她的那条照样会再叫醒她。`last_shown` 却在追加时就推进，它和这一段
    一起存盘，这一段里有了它们，下次的画面就不会再给一遍。

    WHY: 只是她自己发出去的回显不单独插一条：她刚 send 的话不用再看一遍，它们留着未读，
    下次和别的新消息一起出现。
    """
    added: list[dict] = []
    marks: dict[tuple, tuple] = {}
    bot = identity.bot_id()
    for window, (_stamp, through) in list(newest.items()):
        late = [entry for entry in oplog.unread(window)
                if through is None or not oplog.arrival_before_or_at(entry["arrival"], through)]
        if not any(entry["event"].get("user_id") != bot for entry in late):
            continue
        window_label = label(window)
        text, mark, last = slice_window(window, count, last_shown.get(window_label))
        newest[window] = marks[window] = mark
        if not text:
            continue
        last_shown[window_label] = last
        calls = [f"{entry.get('activation_kind')}"
                 + (f" 作者={entry['event']['user_id']}" if entry["event"].get("user_id") is not None else "")
                 for entry in late if entry.get("activated")]
        facts = ["你干活时刚到的", clock(), *([f"叫你的：{'、'.join(calls)}"] if calls else []),
                 *_chat_root._awaker.badges(window)]
        added.append(_view.outside(window_label, facts, text))
    return added, marks


def piled(window: tuple, mark: dict) -> int:
    """Follow-time lines of *window* that would come back unread if the stretch since *mark* went."""
    layer = _chat_root._awaker.layer(window)
    since = layer.get("since") if layer is not None and layer["state"] == "follow" else None
    if since is None:
        return 0
    seen = mark.get("seen", {}).get(label(window))
    return sum(1 for item in records(window, 1_000_000, since=since)
               if item["time"] is not None and item["time"] >= int(since) and (seen is None or item["time"] > seen))
