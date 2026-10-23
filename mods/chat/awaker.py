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

WHY: 2026-10-23 维护者要她有更强的唤醒自己的能力，于是又有四种，都是一次性的，响过就从
笔记里划掉：

    on g123 有人在群里说话就看一眼            （g123:30001 只等这个人，u456 是私聊）
    wait g123:30001 until 2026-10-23 10:30 他刚才话说了一半
    when mcversion.ver["release"] != "1.21.4" -> MC 出新版本了
    at 2026-10-23 08:00 叫维护者起床          （at 也是响过就划掉）

``wait`` 是「等这个人的后续」：后续来了就由那条消息叫醒她，到点还没来也叫醒她，只叫一次
——死板的延迟唤醒做不到这个，人接上了话，延迟到点还会再叫一遍。``on``/``wait`` 由
``chat.capture_chat`` 在消息到达时认领（``take_waiting``），只认开了聊天的窗口。``when`` 每分钟
用 ``.py`` 那份共享环境求值一次。她用 ``wake_me`` 工具写这些行，相对时间在写的时候换成时刻。

WHY: 2026-10-23 维护者要她能聚焦一个群：感兴趣或正在聊的时候，这个群每条消息都像私聊一样
叫醒她，直到她自己退出。不另设模式，就是一条响了不划掉的 ``on``，但必须带期限：

    follow g123 until 2026-10-23 22:30 在跟大家聊游戏

``wake_me("follow g123 30m", ...)`` 写它（不写多久就是 30 分钟），再写一次是续期，替换同一目标
的旧行；``follow g123 off`` 或删掉这一行是退出；到期由每分钟检查静静划掉，不叫醒她。期限必填
是因为忘了退出的代价：热闹的群每条都花一次模型调用，话题早过去了她还挂着；有期限最坏也就
多花到期为止。
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
_TARGET = r"([gu][1-9][0-9]*(?::[1-9][0-9]*)?)"
_ON = re.compile(r"^\s*(?:[-*]\s+)?on\s+" + _TARGET + r"\s+(.+?)\s*$")
_WAIT = re.compile(r"^\s*(?:[-*]\s+)?wait\s+" + _TARGET
                   + r"\s+until\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})\s+(.+?)\s*$")
_FOLLOW = re.compile(r"^\s*(?:[-*]\s+)?follow\s+" + _TARGET
                     + r"\s+until\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})\s+(.+?)\s*$")
_WHEN = re.compile(r"^\s*(?:[-*]\s+)?when\s+(.+?)\s+->\s+(.+?)\s*$")
# 改笔记的地方（划掉响过的行、wake_me 追加）互相排队；她用 write_note 整份改不经过这里。
_edit = threading.Lock()
_broken: set[str] = set()
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _interval(text: str) -> int | None:
    parts = re.findall(r"(\d+)([smhd])", text)
    if not parts or "".join(f"{number}{unit}" for number, unit in parts) != text:
        return None
    return sum(int(number) * _UNIT[unit] for number, unit in parts)


def _moment(day: str, hour: str, minute: str) -> float | None:
    try:
        return datetime.strptime(f"{day} {int(hour):02d}:{minute}", "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        return None


def _target(text: str) -> tuple[tuple, int | None]:
    """``g123`` / ``g123:30001`` / ``u456`` → (window, author or None)."""
    head, _, author = text.partition(":")
    window = ("group" if head[0] == "g" else "private", int(head[1:]))
    return window, int(author) if author else None


def _once(line: str) -> dict | None:
    """The rules beyond ``every``/``daily``/``at``; None when the line is not one."""
    key = line.strip()
    if match := _FOLLOW.match(line):
        moment = _moment(match[2], match[3], match[4])
        if moment is None:
            return {"key": key, "kind": "bad", "why": "日期不对"}
        window, author = _target(match[1])
        return {"key": key, "kind": "follow", "target": match[1], "window": window, "author": author,
                "moment": moment, "until": f"{match[2]} {int(match[3]):02d}:{match[4]}", "reason": match[5]}
    if match := _ON.match(line):
        window, author = _target(match[1])
        return {"key": key, "kind": "on", "target": match[1], "window": window, "author": author, "reason": match[2]}
    if match := _WAIT.match(line):
        moment = _moment(match[2], match[3], match[4])
        if moment is None:
            return {"key": key, "kind": "bad", "why": "日期不对"}
        window, author = _target(match[1])
        return {"key": key, "kind": "wait", "target": match[1], "window": window, "author": author,
                "moment": moment, "until": f"{match[2]} {int(match[3]):02d}:{match[4]}", "reason": match[5]}
    if match := _WHEN.match(line):
        try:
            compile(match[1], "<awaker>", "eval")
        except SyntaxError:
            return {"key": key, "kind": "bad", "why": "表达式写错了"}
        return {"key": key, "kind": "when", "expression": match[1], "reason": match[2]}
    return None


def parse(text: str) -> tuple[list[dict], list[str]]:
    """Rules and the non-empty lines that are not rules."""
    rules: list[dict] = []
    skipped: list[str] = []
    for line in text.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        once = _once(line)
        if once is not None:
            if once["kind"] == "bad":
                skipped.append(line.strip() + f"（{once['why']}）")
            else:
                rules.append(once)
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


def _claim(keys: list[str]) -> list[str]:
    """Strike these one-shot lines out of the note; only the ones still there are returned.

    消息到达和每分钟检查可能同时看到同一行，谁划掉了谁响，于是只叫一次。
    """
    with _edit:
        lines = _notes.read("awaker").split("\n")
        present = [key for key in keys if key in {line.strip() for line in lines}]
        if present:
            kept = [line for line in lines if line.strip() not in present]
            _notes.write("awaker", "\n".join(kept).strip("\n") + "\n" if any(x.strip() for x in kept) else "")
    return present


def _holds(expression: str) -> bool:
    """Evaluate a ``when`` condition in a copy of the shared ``.py`` environment.

    WHY: 这是每分钟自动跑的任意代码，和 exec_code 同一个信任域，所以同样要 Bot 自己有 op；
    否则 wake_me 或 write_note 写一行 when 就绕过了 exec_code 的门。
    """
    from mods import op, py

    if not op.bot_is_op():
        raise PermissionError("Bot 没有 op，when 规则不求值")
    return bool(eval(expression, dict(py.loc)))


def _ring(rule: dict, reason: str) -> bool:
    """Write the reason into her ``me`` window and wake her; it is on disk before she is asked."""
    import mods.chat as chat

    chat.push(chat._windows.ME, f"awaker「{rule['key']}」", reason, wake="闹钟")
    return True


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
        if rule["kind"] == "follow":
            if now >= rule["moment"]:
                _claim([rule["key"]])
            continue
        if rule["kind"] == "on":
            continue
        if rule["kind"] in ("wait", "when"):
            if rule["kind"] == "wait":
                due = now >= rule["moment"]
                reason = f"等到 {rule['until']}，{rule['target']} 都没有新消息。当时想的是：{rule['reason']}"
            else:
                try:
                    due = _holds(rule["expression"])
                except Exception:
                    if rule["key"] not in _broken:
                        _broken.add(rule["key"])
                        _stream.exception(f"唤醒条件求值失败：{rule['key']}")
                    continue
                reason = rule["reason"]
            if due and _claim([rule["key"]]):
                try:
                    _ring(rule, reason)
                except Exception:
                    _stream.exception(f"唤醒规则执行失败（已划掉）：{rule['key']}")
            continue
        if rule["key"] not in last and rule["kind"] == "every":
            last[rule["key"]] = now
            continue
        moment = _due(rule, last.get(rule["key"]), now)
        if moment is None:
            continue
        try:
            done = (_agent.reflect() if rule["reason"].strip() == "reflect"
                    else _ring(rule, rule["reason"]))
        except Exception:
            _stream.exception(f"唤醒规则执行失败：{rule['key']}")
            done = True
        # 正在反思时她醒着却不能接：间隔规则这次算过，定点规则留到下一分钟再试。
        if done or rule["kind"] == "every":
            last[rule["key"]] = now if rule["kind"] == "every" else moment
            if done and rule["kind"] == "at":
                _claim([rule["key"]])
    storage.save()


def take_waiting(window: tuple, author) -> list[dict]:
    """The ``on``/``wait``/``follow`` rules this message satisfies; ``on``/``wait`` are struck out of the note."""
    found, _skipped = rules()
    now = time.time()
    matched = [rule for rule in found if rule["kind"] in ("on", "wait", "follow") and rule["window"] == window
               and (rule["author"] is None or rule["author"] == author)
               and not (rule["kind"] == "follow" and now >= rule["moment"])]
    if not matched:
        return []
    claimed = set(_claim([rule["key"] for rule in matched if rule["kind"] != "follow"]))
    return [rule for rule in matched if rule["kind"] == "follow" or rule["key"] in claimed]


def _follows(line: str, target: str) -> bool:
    return bool((match := _FOLLOW.match(line)) and match[1] == target)


def unfollow(target: str) -> bool:
    """Strike the ``follow`` lines for *target*; False when there were none."""
    with _edit:
        lines = _notes.read("awaker").split("\n")
        kept = [line for line in lines if not _follows(line, target)]
        if len(kept) == len(lines):
            return False
        _notes.write("awaker", "\n".join(kept).strip("\n") + "\n" if any(x.strip() for x in kept) else "")
    return True


def plant(when: str, reason: str, now: float | None = None) -> str:
    """Turn what `wake_me` was given into one awaker line and append it; ValueError when unreadable."""
    now = time.time() if now is None else now
    when, reason = " ".join(when.split()), " ".join(str(reason).split())
    if not reason:
        raise ValueError("reason 不能空：写给到时候的自己，说明为什么叫醒")

    def stamp(seconds: float) -> str:
        # 规则只记到分钟：往后取整，宁晚不早。
        return datetime.fromtimestamp(-(-int(now + seconds) // 60) * 60).strftime("%Y-%m-%d %H:%M")

    if (seconds := _interval(when)) is not None:
        line = f"at {stamp(seconds)} {reason}"
    elif match := re.fullmatch(r"wait\s+" + _TARGET + r"\s+(\S+)", when):
        seconds = _interval(match[2])
        if seconds is None:
            raise ValueError("wait 后面写目标和等多久，例如 wait g123:30001 10m")
        line = f"wait {match[1]} until {stamp(seconds)} {reason}"
    elif match := re.fullmatch(r"on\s+" + _TARGET, when):
        line = f"on {match[1]} {reason}"
    elif match := re.fullmatch(r"follow\s+" + _TARGET + r"(?:\s+(\S+))?", when):
        seconds = _interval(match[2] or "30m")
        if not seconds:
            raise ValueError("follow 后面写目标和聚焦多久，例如 follow g123 30m；退出写 follow g123 off")
        line = f"follow {match[1]} until {stamp(seconds)} {reason}"
    elif when.startswith("when "):
        from mods import op

        if not op.bot_is_op():
            raise ValueError("when 规则要 Bot 有 op 权限（和 exec_code 一样），现在没有")
        line = f"when {when[5:].strip()} -> {reason}"
    else:
        line = f"at {when} {reason}" if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}", when) else f"{when} {reason}"
    found, skipped = parse(line)
    if not found:
        raise ValueError(f"看不懂：{skipped[0] if skipped else line}")
    with _edit:
        text = _notes.read("awaker")
        base = text if text.strip() else DEFAULT + "\n"
        if found[0]["kind"] == "follow":
            # 续期：同一目标只留一行，期限以这次为准。
            base = "\n".join(old for old in base.split("\n") if not _follows(old, found[0]["target"]))
        _notes.write("awaker", base.rstrip("\n") + "\n" + line + "\n")
    return line


def report() -> str:
    found, skipped = rules()
    last = _last()
    lines = [("（awaker 笔记是空的，用默认）" if not _notes.read("awaker").strip() else "awaker 规则：")]
    for rule in found:
        if rule["kind"] == "follow":
            lines.append(f"{rule['key']}  ← 聚焦中，期间每条都叫醒，到期划掉")
            continue
        if rule["kind"] in ("on", "wait", "when"):
            lines.append(f"{rule['key']}  ← 等着，响过就划掉")
            continue
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
