"""Settling: when a stretch of experience in one window ends, keep what is worth keeping.

Each window has a settled position (a time) in storage ``agent.settled``; windows
she acted in since then are listed in ``agent.unsettled`` with their last act time.
A window is due when it has been quiet for ``quiet`` seconds since that act, when
``backlog`` chat records have piled up past its position, or at night. Settling
runs on the central line only when nothing else waits, and yields at a round
boundary as soon as new work arrives; notes are rewritten whole, so a rerun is safe.

WHY: 2026-11-01 维护者同意的无状态设计（docs/working/proposals/one-purpose-stateless-core.md）
用沉淀取代交接：没有一段会被移除的长上下文了，记忆在一段经历结束时留下。位置要落盘，因为
它是记忆的进度；计时器只在内存里，重启时从 unsettled 重新排上（attention 的等待不落盘是
另一回事，那是唤醒，2026-10-31 裁定）。
"""

from __future__ import annotations

import threading
import time

from mods import identity, log, storage

from . import journal, notes as _notes, roles as _roles, screen as _screen, segment as _segment, windows as _windows

DEFAULT_CONFIG = {"quiet": 1200, "backlog": 200}
EPISODE_MAX = 300
ACTS_MAX_CHARS = 60000
RESULT_MAX_CHARS = 600
PEOPLE_MAX = 5
RETRY_AFTER = 600
_stream = log.stream("agent")
_lock = threading.RLock()
_timer: threading.Timer | None = None
_kick = None
_retry: dict[str, float] = {}


def diary_day(moment: float) -> str:
    """The diary a moment belongs to: the day starts at 06:00."""
    return time.strftime("%Y-%m-%d", time.localtime(moment - 6 * 3600))


def config() -> dict:
    data = storage.get("agent", "config")
    value = data.setdefault("settle", dict(DEFAULT_CONFIG))
    if not isinstance(value, dict):
        value = data["settle"] = dict(DEFAULT_CONFIG)
    for key, default in DEFAULT_CONFIG.items():
        value.setdefault(key, default)
    return value


def save_config(cfg: dict) -> None:
    data = storage.get("agent", "config")
    data["settle"] = cfg
    storage.save()
    arm()


def _books() -> tuple[dict, dict]:
    data = storage.get("agent", "settle")
    if "settled" not in data and "unsettled" not in data:
        old_agent = storage.get("", "agent")
        if "settled" in old_agent:
            data["settled"] = old_agent.pop("settled")
        if "unsettled" in old_agent:
            data["unsettled"] = old_agent.pop("unsettled")
        if "settled" in data or "unsettled" in data:
            storage.save()
    settled = data.setdefault("settled", {})
    unsettled = data.setdefault("unsettled", {})
    return settled, unsettled


def pending() -> dict[str, float]:
    """Windows with unsettled acts and their last act time."""
    with _lock:
        return dict(_books()[1])


def touch(windows) -> None:
    """She acted in these windows just now."""
    now = time.time()
    with _lock:
        unsettled = _books()[1]
        for window in windows:
            unsettled[_windows.label(tuple(window))] = now
        storage.save()
    arm()


def start(kick) -> None:
    """Remember how to wake the central reader, and arm the earliest deadline."""
    global _kick
    _kick = kick
    arm()


def stop() -> None:
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None


def arm() -> None:
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
        quiet = float(config()["quiet"])
        deadlines = [max(last + quiet, _retry.get(label, 0)) for label, last in _books()[1].items()]
        if not deadlines or _kick is None:
            return
        delay = max(1.0, min(deadlines) - time.time())
        _timer = threading.Timer(delay, _fire)
        _timer.daemon = True
        _timer.start()


def _fire() -> None:
    if _kick is not None:
        _kick()


def _since(window: tuple, mark: float) -> list[dict]:
    rows = _screen.records(window, 1_000_000, since=mark)
    return [row for row in rows if row["time"] is None or row["time"] > mark]


def _mark(label: str, settled: dict) -> float:
    # A window never settled before starts a day back: enough to cover the
    # switch-over day without rereading its whole history.
    return float(settled.get(label) or time.time() - 86400)


def due(now: float | None = None) -> list[str]:
    """Windows due for settling, longest quiet first."""
    now = time.time() if now is None else now
    found = []
    with _lock:
        settled, unsettled = _books()
        quiet, backlog = float(config()["quiet"]), int(config()["backlog"])
        for label, last in sorted(unsettled.items(), key=lambda pair: pair[1]):
            if _retry.get(label, 0) > now:
                continue
            if now - last >= quiet:
                found.append(label)
                continue
            try:
                window = _windows.parse(label)
            except ValueError:
                continue
            recent = _screen.records(window, backlog + 1, since=_mark(label, settled))
            if sum(row["time"] is None or row["time"] > _mark(label, settled) for row in recent) > backlog:
                found.append(label)
    return found


def _acts(label: str, since: float, until: float) -> str:
    """What she did in this window between since and until, from her frozen act views."""
    parts = []
    for name, view in _segment.each_archived(since):
        stats = view["stats"]
        if stats.get("kind") != "main" or label not in stats.get("windows", []) or view["started"] > until:
            continue
        lines = []
        for item in _segment.source_ids(view):
            row = journal.get(item)
            # WHY: 2026-11-01 一次沉淀输入五万多 token、几乎全不命中缓存。借来的行是构造给的材料，
            # 不是她在这里做的事；工具结果（读到的笔记、搜到的记录）占大头，沉淀要的是她做了什么，
            # 结果截到开头一段，要看全文用 read_context。
            if row["ctx"] != view["ctx"]:
                continue
            if row["kind"] == "goal":
                lines.append(f"目的：{row.get('goal', '')}")
            elif row["kind"] in ("assistant", "tool", "input"):
                messages = [dict(message, content=str(message["content"])[:RESULT_MAX_CHARS]
                                 + f"……（共 {len(str(message['content']))} 字，后面略）")
                            if message.get("role") == "tool" and len(str(message.get("content") or "")) > RESULT_MAX_CHARS
                            else message for message in row["messages"]]
                lines.append(_segment.readable_messages(messages, compact=True))
        parts.append(f"### {_screen.clock(view['started'])[5:19]} 行动 {name}\n" + "\n".join(lines))
    text = "\n\n".join(parts)
    if len(text) > ACTS_MAX_CHARS:
        cut = text[-ACTS_MAX_CHARS:]
        text = "（更早的几次行动太长，省略了；需要时用 read_context 读）\n" + cut[cut.find("### "):]
    return text or "（这段时间你在这里没有行动记录）"


def _people(rows: list[dict]) -> list[str]:
    try:
        bot = identity.bot_id()
    except RuntimeError:
        bot = None
    found = []
    for qq in _screen.extract_qqs([row["text"] for row in rows]):
        scope = f"person:{qq}"
        if qq != bot and scope not in found and _notes.render(scope):
            found.append(scope)
        if len(found) >= PEOPLE_MAX:
            break
    return found


def settle(label: str, *, model: str | None = None, stop=None) -> bool:
    """Run one settle for a window; True when it finished and the position moved."""
    import mods.chat as chat

    window = _windows.parse(label)
    started = time.time()
    with _lock:
        settled, _unsettled = _books()
        mark = _mark(label, settled)
    rows = _since(window, mark)
    more = len(rows) > EPISODE_MAX
    rows = rows[:EPISODE_MAX]
    stamps = [row["time"] for row in rows if row["time"]]
    until = max(stamps) if more and stamps else started
    title = _windows.title(window)
    span = f"{_screen.clock(mark)[5:16]} 至 {_screen.clock(until)[5:16]}"
    day = diary_day(started)
    scopes = ["recent"]
    if window[0] in _windows.QQ:
        scopes.append(_notes.scope_of(window))
    scopes += [f"diary:{day}", "todo", *_people(rows)]
    lookup = _segment.fresh()
    items = []
    for scope in dict.fromkeys(scopes):
        try:
            items.extend(one for one in _segment.resolve_refs(lookup, [scope]) if one not in items)
        except (ValueError, KeyError):
            pass  # an empty or missing note simply is not shown
    episode = _screen.render(window, rows) if rows else "（这段时间这里没有新的聊天记录）"
    items += [{"role": "user", "content": f"【这段经历：{title}，{span}】\n{episode}"},
              {"role": "system", "content": "【你在这里的行动】\n" + _acts(label, mark, until)}]
    state = {"associated_windows": {window}, "cancelled": False}

    def prepare(current):
        current["view"]["stats"].update(windows=[label], reason=f"沉淀 {title} {span}")
        return []

    def after(current):
        if current["used"] - current["initial"] > chat.limit()[1]:
            current["end"] = True

    try:
        chat.run(items, _roles.settle_prompt(title, span, day), tools=("meta",), model=model,
                 kind="settle", state=state, stop=stop, prepare=prepare, after_round=after)
    except Exception:
        if not (stop is not None and stop()):
            _stream.exception(f"沉淀 {label} 出错，{RETRY_AFTER // 60} 分钟后再试")
            _retry[label] = time.time() + RETRY_AFTER
            arm()
        return False
    final = state.get("last") or {}
    complete = bool(state.get("output_count")) and not final.get("tool_calls") and not state.get("cancelled")
    if not complete:
        if not (stop is not None and stop()):
            _retry[label] = time.time() + RETRY_AFTER
            arm()
        return False
    with _lock:
        settled, unsettled = _books()
        settled[label] = until
        _retry.pop(label, None)
        if not more and unsettled.get(label, 0) <= started:
            unsettled.pop(label, None)
        storage.save()
    arm()
    return True


def settle_all(*, model: str | None = None, stop=None) -> None:
    """Settle every window with unsettled acts, regardless of quiet time (nightly)."""
    for label in list(pending()):
        if stop is not None and stop():
            return
        _retry.pop(label, None)
        for _ in range(10):  # a long backlog settles in several bounded passes
            if not settle(label, model=model, stop=stop) or label not in pending() or (stop is not None and stop()):
                break
