"""When she wakes herself: rules in a plain note, checked once a minute.

WHY: 2026-10-22 维护者想要一个专门管"什么条件下叫醒自己"的地方，她或维护者都能
写，比如每 10 分钟醒一次。规则就是笔记 ``awaker``（``data/notes/awaker.md``），每行一条：

    every 10m 看看群里有没有要接的话
    daily 04:00 reflect
    daily 23:30 催维护者去睡觉
    at 2026-10-23 08:00 叫维护者起床

``every`` 的间隔写 ``30s``/``10m``/``2h``，最短一分钟；``reflect`` 这个理由表示每晚的
整理与反思，其它理由原样作为闹钟交给她。不认识的行忽略，``check`` 把它们列出来。
文件不存在时只有一条 ``daily 04:00 reflect``（维护者 2026-10-22 定的凌晨 4 点）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re
import threading
import time

from mods import log, storage

from . import notes as _notes

DEFAULT = "daily 04:00 reflect"
JOB = "chat-awaker"
_LAST_KEY = "awaker_last"
# 定点规则错过多久之内仍补一次（比如忙着聊天、刚好重启）；再晚就等下一次。
_GRACE = 3 * 3600
_stream = log.stream("agent")
_running = threading.Lock()
_LINE = re.compile(r"^\s*(?:[-*]\s+)?(?:(every)\s+(\S+)|(daily)\s+(\d{1,2}):(\d{2})"
                   r"|(at)\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2}))\s+(.+?)\s*$")
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _interval(text: str) -> int | None:
    parts = re.findall(r"(\d+)([smhd])", text)
    if not parts or "".join(f"{number}{unit}" for number, unit in parts) != text:
        return None
    return sum(int(number) * _UNIT[unit] for number, unit in parts)


def parse(text: str) -> tuple[list[dict], list[str]]:
    """Rules and the non-empty lines that are not rules."""
    rules: list[dict] = []
    skipped: list[str] = []
    for line in text.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _LINE.match(line)
        if match is None:
            skipped.append(line.strip())
            continue
        reason = match[10]
        if match[1]:
            seconds = _interval(match[2])
            if seconds is None or seconds < 60:
                skipped.append(line.strip() + "（间隔写 30m、2h 这样，最短 1m）")
                continue
            rules.append({"key": line.strip(), "kind": "every", "seconds": seconds, "reason": reason})
        elif match[3]:
            if int(match[4]) > 23 or int(match[5]) > 59:
                skipped.append(line.strip() + "（时刻不对）")
                continue
            rules.append({"key": line.strip(), "kind": "daily", "hour": int(match[4]),
                          "minute": int(match[5]), "reason": reason})
        else:
            try:
                moment = datetime.strptime(f"{match[7]} {int(match[8]):02d}:{match[9]}", "%Y-%m-%d %H:%M")
            except ValueError:
                skipped.append(line.strip() + "（日期不对）")
                continue
            rules.append({"key": line.strip(), "kind": "at", "moment": moment.timestamp(), "reason": reason})
    return rules, skipped


def rules() -> tuple[list[dict], list[str]]:
    text = _notes.read("awaker")
    return parse(text if text.strip() else DEFAULT)


def _last() -> dict:
    data = storage.get("", "agent")
    value = data.get(_LAST_KEY)
    if not isinstance(value, dict):
        value = data[_LAST_KEY] = {}
    return value


def _due(rule: dict, last: float | None, now: float) -> float | None:
    """The moment this rule is due for, or None."""
    if rule["kind"] == "every":
        # 第一次见到一条间隔规则时从现在起算，不在启动或刚写下时立刻响。
        return None if last is None or now - last < rule["seconds"] else now
    if rule["kind"] == "daily":
        today = datetime.fromtimestamp(now).replace(hour=rule["hour"], minute=rule["minute"],
                                                    second=0, microsecond=0).timestamp()
        moment = today if today <= now else today - 86400
    else:
        moment = rule["moment"]
    if moment > now or now - moment > _GRACE or (last is not None and last >= moment):
        return None
    return moment


def check(now: float | None = None) -> None:
    """Fire every due rule once; a rule whose wake could not run stays due."""
    from . import agent as _agent

    now = time.time() if now is None else now
    found, _skipped = rules()
    last = _last()
    live = {rule["key"] for rule in found}
    for key in [key for key in last if key not in live]:
        del last[key]
    for rule in found:
        if rule["key"] not in last and rule["kind"] == "every":
            last[rule["key"]] = now
            continue
        moment = _due(rule, last.get(rule["key"]), now)
        if moment is None:
            continue
        try:
            done = (_agent.reflect() if rule["reason"].strip() == "reflect"
                    else _agent.alarm(rule["reason"], rule["key"]))
        except Exception:
            _stream.exception(f"唤醒规则执行失败：{rule['key']}")
            done = True
        # 正在聊天时她本来就醒着：间隔规则这次算过，定点规则留到下一分钟再试。
        if done or rule["kind"] == "every":
            last[rule["key"]] = now if rule["kind"] == "every" else moment
    storage.save()


def report() -> str:
    found, skipped = rules()
    last = _last()
    lines = [("（awaker 笔记是空的，用默认）" if not _notes.read("awaker").strip() else "awaker 规则：")]
    for rule in found:
        stamp = last.get(rule["key"])
        shown = datetime.fromtimestamp(stamp).strftime("%m-%d %H:%M") if isinstance(stamp, (int, float)) else "还没响过"
        lines.append(f"{rule['key']}  ← 上次 {shown}")
    lines.extend(f"不认识：{line}" for line in skipped)
    return "\n".join(lines)


def schedule() -> None:
    """Register the once-a-minute check on the shared scheduler."""
    from mods import scheduler

    try:
        instance = scheduler.get_scheduler()
    except RuntimeError:
        return
    instance.add_job(_tick, "interval", minutes=1, id=JOB, replace_existing=True,
                     coalesce=True, max_instances=1, next_run_time=datetime.now() + timedelta(seconds=5))


def _tick() -> None:
    # 一次检查可能叫醒她很久：放到自己的线程里跑，上一次没跑完就跳过这一分钟。
    if not _running.acquire(blocking=False):
        return

    def run() -> None:
        try:
            check()
        except Exception:
            _stream.exception("awaker 检查失败")
        finally:
            _running.release()

    threading.Thread(target=run, name="chat-awaker", daemon=True).start()
