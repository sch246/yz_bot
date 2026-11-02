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
``replay_from`` marks the first live write's recovery boundary, never a read receipt.
"""

from __future__ import annotations

from collections import OrderedDict
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
# WHY: 工具续轮反复准备同一个窗口，但扫描过不等于模型读过。解析缓存独立于
# 已读状态；只留近期使用的日档，不能把多年档案变成常驻内存。容量按源文件字节计。
_RECORD_CACHE_BYTES = 16 * 1024 * 1024
_RECORD_CACHE_FILES = 64
_record_cache: OrderedDict = OrderedDict()
_record_cache_size = 0
_record_cache_lock = threading.RLock()


def _read() -> dict:
    return storage.get("", "agent").setdefault(_READ_KEY, {})


def _state(window: tuple) -> dict:
    return _read().setdefault(label(window), {"through": None, "at": [], "pending": {}, "excluded": {}})


def read_snapshot() -> dict:
    with _lock:
        return deepcopy(_read())


def begin_write(window: tuple, stamp=None) -> None:
    """Persist a first-write recovery boundary without confirming any reading."""
    with _lock:
        state = _state(window)
        if state.get("through") is None and state.get("replay_from") is None:
            # WHY: 没有已读水位不代表多年档案全是新到达（2026-11-02 CPU 事故）。
            # 在首条实时日志写入前留起点，崩溃后才能补登记，且不把旧档案激活。
            # 不能拿 pending 的最大时间推进：中间一次登记失败后仍可能收到新消息。
            state["replay_from"] = int(time.time() if stamp is None else stamp)
            try:
                storage.save()
            except Exception:
                del state["replay_from"]
                raise


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


def register(window: tuple, event: dict, *, recovered=False, kind="", reason="", persist=True) -> str:
    """Register before queueing; history batches use persist=False then save_read()."""
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
        if persist:
            storage.save()
    return identity_


def save_read() -> None:
    """Persist a restored batch before its summons are queued."""
    with _lock:
        storage.save()


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


def _day_files(window: tuple, days=None) -> list[tuple[tuple[int, int, int], Path]]:
    directory = chatlog.window_path(window[0], window[1])
    found = []
    for day in chatlog._archive_days(directory) if days is None else sorted(set(days), reverse=True):
        path, _sidecar = chatlog._day_paths(window[0], window[1], day, None)
        if path.is_file():
            found.append((day, path))
    return found


def _record_time(day: tuple[int, int, int], head: str) -> int | None:
    match = chatlog._TIME_SUFFIX.search(head.rpartition(" | ")[0] or head)
    return chatlog._epoch(day, match) if match else None


def _file_version(path: Path) -> tuple:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _file_records(path: Path, day: tuple[int, int, int]) -> list[dict]:
    """Cache raw records and lazy parsing; caller holds _record_cache_lock."""
    global _record_cache_size
    key = (path.absolute(), day)
    version = _file_version(path)
    previous = _record_cache.get(key)
    if previous is not None and previous["version"] == version:
        _record_cache.move_to_end(key)
        return previous["records"]

    text = path.read_text(encoding="utf-8")
    after = _file_version(path)
    records, offset = [], 0
    last = None
    if previous is not None:
        del _record_cache[key]
        _record_cache_size -= previous["size"]
        # WHY: inode 相同且变大也可能是外部改写。变化时核对实际前缀，才可
        # 复用旧记录；从末条重新分割，保留无末尾换行、半条正文继续追加的行为。
        if text.startswith(previous["text"]) and previous["records"]:
            records = previous["records"][:-1]
            offset = previous["tail"]
            last = previous["records"][-1]
    lines = text[offset:].split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    spans = chatlog._file_records(lines) if lines else []
    for start, end in spans:
        raw = "\n".join(lines[start:end])
        if last is not None and not start and raw == last["text"]:
            records.append(last)
        else:
            records.append({"text": raw, "hidden": end - start > 1
                            and lines[start + 1].startswith("    #"), "parsed": None})
    tail = offset + sum(len(line) + 1 for line in lines[:spans[-1][0]]) if spans else 0
    # 并发写入时不缓存这次观察，下次重新读；不把半途内容钉成最终版本。
    size = version[2]
    if version == after and size <= _RECORD_CACHE_BYTES:
        _record_cache[key] = {"version": version, "text": text, "records": records,
                              "tail": tail, "size": size}
        _record_cache_size += size
        while (len(_record_cache) > _RECORD_CACHE_FILES
               or _record_cache_size > _RECORD_CACHE_BYTES):
            _, evicted = _record_cache.popitem(last=False)
            _record_cache_size -= evicted["size"]
    return records


def records(window: tuple, count: int, before: int | None = None, since: float | None = None,
            *, include_hidden: bool = False, days=None) -> list[dict]:
    """The newest *count* records of one window, oldest first.

    *before* is an exclusive Unix time; it pages backwards through the files.
    *since* stops at day files that end before it.
    *days* restricts reads to those exact dates, without enumerating the archive.
    *include_hidden* also reads QQ control/debug records for offline source verification.
    """
    if count <= 0:
        return []
    picked: list[dict] = []
    hide_control = not include_hidden and _windows.spec(window)["hide_control"]
    for day, path in _day_files(window, days):
        if since is not None and chatlog._day_bounds(day)[1] < since:
            break
        with _record_cache_lock:
            # WHY: 首屏通常只要几十条；未触及的旧记录不提前解析。缓存保留
            # 原文与解析值，不缓存 unread/shown，它们由每次请求的读状态决定。
            for record in reversed(_file_records(path, day)):
                # WHY: QQ 的 # 控制消息不回流模型；CLI 的 Markdown 标题照常可见。
                if hide_control and record["hidden"]:
                    continue
                item = record["parsed"]
                if item is None:
                    text = record["text"]
                    first = text.partition("\n")[0]
                    stamp = _record_time(day, first)
                    head = chatlog._split_head(first)
                    value = head.get("message_id") if head else None
                    identity_ = str(value) if value else "~" + hashlib.sha256(f"{stamp}\n{text}".encode()).hexdigest()
                    item = record["parsed"] = {"id": identity_, "day": day, "time": stamp, "text": text}
                if before is not None and item["time"] is not None and item["time"] >= before:
                    continue
                # prepare 会给返回值附 unread，调用者也能修改它；不能污染共享解析值。
                picked.append(dict(item))
                if len(picked) >= count:
                    return picked[::-1]
    return picked[::-1]


def _with_pending(window: tuple, rows: list[dict], pending: dict) -> list[dict]:
    """Add exact old exceptions without reading the history between them and now."""
    present = {(item["id"], item["day"]) for item in rows}
    missing = {}
    for identity_, saved in pending.items():
        stamp = saved.get("time")
        if stamp is None:
            stamp = saved.get("event", {}).get("time")
        if stamp is None:
            # 未知日期仍保留在 pending 中，不能猜日期或假称已经读过。
            continue
        day = tuple(time.localtime(stamp)[:3])
        if (identity_, day) not in present:
            missing.setdefault(day, set()).add(identity_)
    for day, identities in missing.items():
        identities.update(item["id"] for item in rows if item["day"] == day)
        extra = [item for item in records(window, 1_000_000, days=[day]) if item["id"] in identities]
        reread = {(item["id"], item["time"], item["text"]) for item in extra}
        if any((item["id"], item["time"], item["text"]) not in reread
               for item in rows if item["day"] == day):
            # 两次读取之间文件被改动时保留已有画面；旧例外仍未确认，下轮再读。
            continue
        # WHY: 补回的旧时刻可能追加在当天末尾；同日按原文件位置合并，
        # 不能按时间重排，否则 shown 的位置和接话范围都会改变。
        rows = [item for item in rows if item["day"] != day] + extra
    return sorted(rows, key=lambda item: item["day"]) if missing else rows


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
    parsed = chatlog.parse_log(item["text"], kind=window[0], target=window[1], day=item["day"],
                               bot_id=bot)
    # WHY: 日档分割保留空行和孤立缩进，而 parse_log 会跳过它们；画面仍需原记录身份。
    event = parsed[0] if parsed else {
        "_source": chatlog.rootfile, "_kind": "unparsed", "text": item["text"],
        "time": item["time"], "_derived": ["time"] if item["time"] is not None else [],
        "_missing": [] if item["time"] is not None else ["time"]}
    event["record_id"] = item["id"]
    event["_record_text"] = item["text"]
    return event


def record_key(item: dict) -> str:
    """Identity of one exact historical version, within its window."""
    # WHY: 消息号跨日会重用，撤回/改写也会保留原号；去重不能只比 id。
    value = repr((item["id"], tuple(item["day"]), item["time"], item["text"]))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def render(window: tuple, items: list[dict], seen: int | None = None, *, annotated=False) -> str:
    """Join records under day headers; mark where unseen records begin."""
    lines: list[str] = []
    texts = [item["text"] for item in items]
    if not annotated:
        texts = _recognize.annotate(window, texts)
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


def prepare(window: tuple, *, shown, count, pending=(), lead_in=None) -> dict:
    """Render new material and return an opaque read patch without confirming it.

    WHY: 已读等第一次输出；展示身份却来自已经保存的 view，失败重试不重复追加画面。
    WHY: 维护者 2026-10-24：follow 期间的记录不截，之前的积压仍最多 60 条；
    刚 follow 一个很久没看的群不能把几周旧消息全倒进来。
    WHY: 2026-10-23：离线点名会插在上次画面之前，必须按身份补给她，不能只接时间水位。
    """
    from . import awaker

    window = tuple(window)
    shown = set(shown)
    lead_in = LEAD_IN if lead_in is None else max(0, int(lead_in))
    with context.window_lock(window), _lock:
        state = deepcopy(_state(window))
        layer = awaker.layer(window)
        follow_since = layer.get("since") if layer and layer["state"] == "follow" else None
        marks = [state["through"], follow_since]
        since = min((stamp for stamp in marks if stamp is not None), default=None)
        # WHY: 旧 pending 是离散例外，不是连续扫描的起点。2026-11-02 实测
        # 一条多年前的未读使每次请求重扫数年日志、占满一个核；只按它所在日
        # 补原文，连续区间由已读边界和 follow 决定，保留跟听期间的完整消息。
        rows = records(window, max(count, lead_in) + FRESH_MAX)
        if since is not None:
            fresh_rows = records(window, 1_000_000, since=since)
            fresh_ids = {item["id"] for item in fresh_rows}
            rows = [item for item in rows if item["id"] not in fresh_ids] + fresh_rows
        rows = _with_pending(window, rows, state["pending"])
        for item in rows:
            item["unread"] = is_unread(state, item["id"], item["time"])
        available = [item for item in rows if item["id"] not in shown]
        since = follow_since
        followed = [item for item in available if item["unread"] and since is not None
                    and item["time"] is not None and item["time"] >= int(since)]
        calls = [item for item in available if item["unread"]
                 and state["pending"].get(item["id"], {}).get("recovered")]
        exceptions = {item["id"] for item in followed + calls}
        positions = [index for index, item in enumerate(rows) if item["id"] in shown]
        if positions:
            # WHY: 普通离线补回不算未读，但插在上一幅画面之后的原文仍要给她看；
            # 展示范围由 view 的位置决定，不能拿未读身份当作展示过滤器。
            fresh = rows[positions[-1] + 1:]
            selected = {item["id"] for item in fresh[-FRESH_MAX:]} | exceptions
        elif state["through"] is None:
            # Never read: count chooses the initial history, even if every line
            # currently has an unread arrival. Follow/offline calls remain explicit exceptions.
            fresh = available
            selected = {item["id"] for item in available[-max(count, lead_in):]} | exceptions
        else:
            # After handoff, display the range after the confirmed boundary and
            # exact older unread exceptions. Excluded recovery still belongs to
            # this visible range even though its patch correctly says unread=False.
            through = state["through"]
            fresh = [item for item in available if item["id"] in state["pending"]
                     or item["time"] is not None and (item["time"] > through
                     or item["time"] == through and item["id"] not in state["at"])]
            selected = {item["id"] for item in fresh[-FRESH_MAX:]} | exceptions
            # WHY: 2026-10-25：交接后只补三条接话，不把刚压缩掉的历史重新倒回来。
            fresh_ids = {item["id"] for item in fresh}
            older = [item for item in available if item["id"] not in fresh_ids]
            selected.update(item["id"] for item in older[-lead_in:] if lead_in)
        skipped = sum(item["id"] not in selected for item in fresh)
        items = [item for item in available if item["id"] in selected]
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
                "record_keys": [record_key(item) for item in items],
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
