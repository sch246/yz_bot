"""Render chat windows for the model in the chatlog's own record format.

WHY: the model reads exactly what ``chatlog/`` stores, so "read more" is reading
the same files with the same eyes; there is no second message format to keep in
step with the first.  The window and day live in the path, so a screen adds one
``# g群号 YYYY-MM-DD`` line where the path would have been.

Only ordinary ``.log`` day files are read.  Offline backfill sidecars
(``.backfill.jsonl``) are not rendered yet; a recovered gap shows up only once it
is written back as ordinary lines.

``agent.read`` is the only persistent reading authority. Each window label maps
``through`` (epoch or None), ``at`` (confirmed ids at that second), ``pending``
(exact unread exceptions, including old-time arrivals), and ``excluded``
(ordinary recovered ids not yet covered by the watermark). Pending values carry
``time, event, kind, reason, recovered, unread``; only a nonempty ``kind`` means
an unconsumed summon. ``source_kind`` preserves a consumed summon's provenance.
A migration may set through to the latest known historical second and represent
all genuinely unread records explicitly in pending; no timestamp rollback is
needed for dismiss. Patches are opaque view metadata and must retain their
per-record ``unread`` facts when passed back to restore_read.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import threading
import re
import time
from typing import Iterable

from mods import chatlog, context, identity, storage

from . import recognize as _recognize, windows as _windows
from .windows import label

# A window shows at least this many lines before its first unseen one.
LEAD_IN = 3
# 一次最多补多少条新消息。WHY: 2026-10-26 维护者：200 条一次塞进来太肥，折到 60。
FRESH_MAX = 60
# One persistent owner, including exceptions that precede the continuous watermark.
_READ_KEY = "read"
_lock = threading.RLock()


def _read() -> dict:
    return storage.get("", "agent").setdefault(_READ_KEY, {})


def _state(window: tuple) -> dict:
    return _read().setdefault(label(window), {"through": None, "at": [], "pending": {}, "excluded": {}})


def read_snapshot() -> dict:
    with _lock:
        return deepcopy(_read())


def record_id(event: dict) -> str:
    """The same identity as the ordinary chatlog record, local to one window."""
    value = (event.get("message_id") if event.get("post_type") in ("message", "message_sent")
             else event.get("id") if event.get("post_type") == "push" else None)
    if value is not None and str(value):
        return str(value)
    if event.get("record_id"):
        return str(event["record_id"])
    # Notices without message ids use their exact stored record text.
    text = event.get("_record_text")
    if text is not None:
        text = str(text).rstrip("\n")
    if text is None:
        if event.get("sub_type") == "poke":
            text = chatlog._notice_str(event["time"], chatlog.format_poke(event)).rstrip("\n")
        else:
            raise ValueError("a notice without a message id needs its stored _record_text")
    return "~" + hashlib.sha256(f"{event.get('time')}\n{text}".encode()).hexdigest()


def is_unread(state: dict, identity_: str, stamp) -> bool:
    if identity_ in state.get("pending", {}):
        return True
    if identity_ in state.get("excluded", {}):
        return False
    through = state.get("through")
    return (through is None or stamp is not None and
            (stamp > through or stamp == through and identity_ not in state.get("at", [])))


def register(window: tuple, event: dict, *, recovered=False, kind="", reason="") -> str:
    """Persist an arrival before attention queues it; ordinary recovery is explicitly excluded."""
    identity_ = record_id(event)
    with _lock:
        state = _state(window)
        if recovered and not kind:
            # WHY: 普通离线补回只补历史，不能因为晚插到水位之后而在下次启动变成未读。
            stamp, through = event.get("time"), state["through"]
            if through is None or stamp is None or stamp >= through:
                state["excluded"][identity_] = stamp
        else:
            state["excluded"].pop(identity_, None)
            state["pending"].setdefault(identity_, {
                "time": event.get("time"), "event": deepcopy(event), "kind": kind,
                "reason": reason, "recovered": bool(recovered), "unread": True})
            if kind:
                state["pending"][identity_].update(kind=kind, reason=reason)
        storage.save()
    return identity_


def unread(window: tuple) -> dict:
    with _lock:
        return deepcopy(_state(window)["pending"])


def consume(window: tuple, identities) -> None:
    """Acknowledge summons separately from reading their record bodies."""
    with _lock:
        pending = _state(window)["pending"]
        for identity_ in identities:
            if identity_ in pending:
                if pending[identity_].get("kind"):
                    pending[identity_]["source_kind"] = pending[identity_]["kind"]
                pending[identity_]["kind"] = ""
        storage.save()


def commit_read(patches) -> None:
    """Confirm exactly the records in a request, only after its first model output.

    WHY: 2026-10-22 审查发现生成期间同窗口再来的 at 会被第一次输出一起标掉；
    patch 因而保存确切身份，不在输出时重新求整个窗口的未读边界。失败请求不调用这里。
    """
    if isinstance(patches, dict):
        patches = [patches]
    with _lock:
        for patch in patches:
            state = _state(_windows.parse(patch["window"]))
            for identity_, item in patch.get("records", {}).items():
                state["pending"].pop(identity_, None)
                stamp = item.get("time")
                through = state["through"]
                if stamp is not None and (through is None or stamp > through):
                    state["through"], state["at"] = stamp, [identity_]
                elif stamp is not None and stamp == through and identity_ not in state["at"]:
                    state["at"].append(identity_)
            through = state["through"]
            if through is not None:
                state["excluded"] = {i: t for i, t in state["excluded"].items()
                                     if t is None or t >= through}
        storage.save()


def restore_read(snapshot: dict, windows: dict, *, patches=()) -> None:
    """Undo only removed screens' confirmations; do not replace a window with an old snapshot.

    ``windows`` maps labels to removed record ids. ``patches`` are those screens'
    opaque patches, including arrivals first seen after the wake snapshot.
    """
    material = {}
    for patch in patches:
        rows = material.setdefault(patch["window"], {})
        for identity_, item in patch.get("records", {}).items():
            if item.get("unread"):
                rows.setdefault(identity_, item)
    with _lock:
        for text, identities in windows.items():
            state = _state(_windows.parse(text))
            old = snapshot.get(text, {})
            for identity_ in identities:
                item = material.get(text, {}).get(identity_) or old.get("pending", {}).get(identity_)
                if item is not None and item.get("unread", True):
                    # Already handled summons must not repeat forever after a dismiss.
                    restored = deepcopy(item)
                    restored["source_kind"] = restored.get("kind", "")
                    restored["kind"] = ""
                    state["pending"].setdefault(identity_, restored)
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
            head = chatlog._split_head(lines[start])
            value = head.get("message_id") if head else None
            identity_ = str(value) if value else "~" + hashlib.sha256(f"{stamp}\n{text}".encode()).hexdigest()
            day_records.append({"id": identity_, "day": day, "time": stamp, "text": text})
        picked[:0] = day_records[-(count - len(picked)):]
        if len(picked) >= count:
            break
    return picked


def event_for_record(window: tuple, item: dict) -> dict:
    """Recover only protocol fields the stored record actually provides."""
    head = chatlog._split_head(item["text"].partition("\n")[0])
    if window[0] not in _windows.QQ:
        body = "\n".join(line[4:] for line in item["text"].splitlines()[1:])
        return {"post_type": "push", "time": item["time"], "author": head["name"] if head else "?",
                "text": body, "id": item["id"]}
    try:
        bot = identity.bot_id()
    except RuntimeError:
        bot = None
    event = chatlog.parse_log(item["text"], kind=window[0], target=window[1], day=item["day"],
                              bot_id=bot)[0]
    event["record_id"] = item["id"]
    event["_record_text"] = item["text"]
    return event


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
        if not divided and (item.get("unread") if "unread" in item else
                            item["time"] is not None and item["time"] > seen):
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


def prepare(window: tuple, *, shown, count, pending=()) -> dict:
    """Render new material and return an opaque read patch without confirming it.

    WHY: 已读等第一次输出；展示身份却来自已经保存的 view，失败重试不重复追加画面。
    WHY: 维护者 2026-10-24：follow 期间的记录不截，之前的积压仍最多 60 条；
    刚 follow 一个很久没看的群不能把几周旧消息全倒进来。
    WHY: 2026-10-23：离线点名会插在上次画面之前，必须按身份补给她，不能只接时间水位。
    """
    from . import awaker

    window = tuple(window)
    shown = set(shown)
    with context.window_lock(window), _lock:
        state = deepcopy(_state(window))
        marks = [state["through"], *(item.get("time") for item in state["pending"].values())]
        since = min((stamp for stamp in marks if stamp is not None), default=None)
        # Ordinary requests walk only the unread range and a short tail for lead-in.
        rows = records(window, max(count, LEAD_IN) + FRESH_MAX)
        if since is not None:
            fresh_rows = records(window, 1_000_000, since=since)
            fresh_ids = {item["id"] for item in fresh_rows}
            rows = [item for item in rows if item["id"] not in fresh_ids] + fresh_rows
        for item in rows:
            item["unread"] = is_unread(state, item["id"], item["time"])
        available = [item for item in rows if item["id"] not in shown]
        fresh = [item for item in available if item["unread"]]
        layer = awaker.layer(window)
        since = layer.get("since") if layer and layer["state"] == "follow" else None
        followed = [item for item in fresh if since is not None and item["time"] is not None
                    and item["time"] >= int(since)]
        calls = [item for item in fresh if state["pending"].get(item["id"], {}).get("recovered")]
        # Newest 60 plus exact follow/offline-call exceptions; keep ordinary log order.
        selected = {item["id"] for item in fresh[-FRESH_MAX:] + followed + calls}
        skipped = len(fresh) - len(selected)
        if shown:
            items = [item for item in available if item["id"] in selected]
        elif state["through"] is not None:
            # WHY: 2026-10-25：交接后只补三条接话，不把刚压缩掉的历史重新倒回来。
            lead = [item for item in available if not item["unread"]][-LEAD_IN:]
            ids = selected | {item["id"] for item in lead}
            items = [item for item in available if item["id"] in ids]
        else:
            ids = selected | {item["id"] for item in available[-max(count, LEAD_IN):]}
            items = [item for item in available if item["id"] in ids]
        by_id = {item["id"]: item for item in rows}
        metadata = {}
        for identity_ in shown | {item["id"] for item in items}:
            item = by_id.get(identity_)
            saved = state["pending"].get(identity_)
            if item is not None:
                if identity_ in shown and not item["unread"]:
                    continue
                metadata[identity_] = dict(saved or {}, time=item["time"], unread=item["unread"])
                if "event" not in metadata[identity_]:
                    metadata[identity_]["event"] = event_for_record(window, item)
            elif saved is not None:
                metadata[identity_] = saved
        for patch in pending:
            if patch.get("window") == label(window):
                for identity_, item in patch.get("records", {}).items():
                    if identity_ in shown and is_unread(state, identity_, item.get("time")):
                        metadata.setdefault(identity_, item)
        text = render(window, items, state["through"])
        if skipped and items:
            text = (f"（{label(window)} 还有 {skipped} 条新消息未展示；更早的用 "
                    f'read_log("{label(window)}", before="{clock(items[0]["time"])}") 往前翻）\n' + text)
        return {"text": text, "shown": [item["id"] for item in items],
                "patch": {"window": label(window), "records": metadata},
                "follow_ids": [item["id"] for item in followed]}


def unread_hint(on_screen: set[tuple]) -> str:
    from . import awaker

    try:
        bot = identity.bot_id()
    except RuntimeError:
        bot = None
    rows = []
    for text, state in read_snapshot().items():
        try:
            window = _windows.parse(text)
        except ValueError:
            continue
        if window in on_screen:
            continue
        # WHY: 2026-10-28：自己的发送回声不算“其它窗口”里别人的未读。
        others = [item for item in state["pending"].values()
                  if bot is None or (item.get("event", {}).get("user_id") != bot
                                     and str(item.get("event", {}).get("author")) != str(bot))]
        if others:
            mentions = sum(item.get("kind") == "mention" for item in others)
            rows.append(f"{_windows.title(window)} {len(others)} 条"
                        + (f"（@你 {mentions}）" if mentions else "")
                        + "".join(f"（{badge}）" for badge in awaker.badges(window)))
    return ("【其它窗口】还有没看过的消息：" + "；".join(rows[:10])
            + "。要看就用 read_log 读原文。") if rows else ""
