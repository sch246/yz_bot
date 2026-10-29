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

WHY: 2026-10-23 维护者要统一触发器：每种窗口有自己的默认触发器（私聊每条都算；群里是 at、
叫名字、戳一戳，即 ``chat.activation_signal``；B 站这类非 QQ 窗口是投递方给的 ``wake=``），窗口上
可以盖一层限时的覆盖层，盖着的时候由它决定叫不叫，不管默认怎么说。覆盖层只管叫不叫：
消息照样登记成未读，她醒着时照样看得见；她自己埋的 ``on``/``wait`` 照样响；``chat.wake``
的显式调用也不经过它。覆盖层有三种状态：

    follow g123 until 2026-10-23 22:30 在跟大家聊游戏      （每条都叫）
    mute bili until 2026-10-24 08:00 先不管 B 站           （都不叫）
    other：代码用 ``override(目标, 函数)`` 设的，函数看这条决定叫不叫

follow/mute 是写进笔记的两种语法糖，``wake_me("follow g123 30m", ...)`` 写（不写多久就是 30
分钟，期限不设上限），同一目标再写一次就替换（follow 换成 mute 也是），``off`` 揭掉，到期由每分钟
检查静静划掉、不叫醒她。目标是一个窗口（``g123``、``u456``、``g123:QQ号`` 只管这个人、
``bili:BV…``）或者一整类（``group``、``private``、``bili``）；同时有好几层时越具体的越优先。
``me`` 不能盖；master（``ops`` 第一位）的话 mute 压不住，管理员的可以。follow 只认 QQ：B 站
这类窗口只收到回复和 @，本来就都叫醒她；维护者不要为 follow 一个评论区单独去轮询它，会被限流。
other 只在内存里，重启就没了，和她用 exec_code 包的钩子一样。

WHY: 2026-10-23 她在群里说完话，别人不 @ 她接话，她看不见（维护者认可的缺口，参照群友讲的麦麦
（MaiBot）回复后观察一会儿）。所以她在群里 send 之后的 5 分钟里，这个群算 follow（显示为"说完话自动听"，
``spoke``）；连着两次 dismiss 掉看过它的唤醒就不听了，再说话重新算。她自己盖的任何一层（包括整类的）
都比它优先，``follow g123 off`` 也能提前揭掉。只在内存里，重启就没了。
同一天试过把覆盖层叫醒的消息攒一批、她不开口就退避，维护者觉得不好（晚了接不上话，痕迹照样留着）：
follow 一个群让她看起来很累，累的是这一段里叠了一遍遍"被叫醒、看一眼、没我的事"，不是叫醒的次数。
现在每条照样当场叫醒，没什么要做的就 dismiss，那几次从上下文里清掉，见 ``agent.request_dismiss``。
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re
import threading
import time

from mods import log, storage

from . import notes as _notes, windows as _windows

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
_LAYER = re.compile(r"^\s*(?:[-*]\s+)?(follow|mute)\s+(\S+)"
                    r"\s+until\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})\s+(.+?)\s*$")
_WHEN = re.compile(r"^\s*(?:[-*]\s+)?when\s+(.+?)\s+->\s+(.+?)\s*$")
# 改笔记的地方（划掉响过的行、wake_me 追加）互相排队；她用 write_note 整份改不经过这里。
_edit = threading.Lock()
# 代码设的覆盖层（other），目标原文 → {"decide": 函数, "moment": 到期时刻或 None, "reason": …}。
_custom: dict[str, dict] = {}
# follow 从什么时候盖上的，目标原文 → 时刻：画面据此把 follow 期间到的消息给全（agent._screen_text）。
# 只在内存里；重启后、或者她直接用 write_note 写的 follow 不知道起点，画面照旧截。
_since: dict[str, float] = {}
# WHY: 2026-10-29 草籽与柚子商定：发言后死板的 5 分钟自动跟听并入 send 的 wait 参数，
# 由她自己在发言时决定等多久（wait="5m"、"20m"）或不等（wait=-1、0），统一走 awaker 调度。
LISTEN = 0
# 窗口 → {"spoke": 她上次在这说话的时刻, "dismissed": 那之后 dismiss 掉看过它的唤醒几次}
_heard: dict[tuple, dict] = {}
_hearing = threading.Lock()
_broken: set[str] = set()
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _interval(text: str) -> int | None:
    text = str(text).strip()
    if text.isdigit():
        return int(text)
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


def _scope(text: str) -> tuple | None:
    """A layer's target as ("window", window, author or None) or ("kind", 种类, None); None when unreadable."""
    if re.fullmatch(_TARGET, text):
        return ("window", *_target(text))
    if not re.fullmatch(r"[a-z][a-z0-9_]*(?::\S+)?", text):
        return None
    name, _, target = text.partition(":")
    if name in ("me", "bot") or target and name in _windows.QQ:
        return None
    return ("window", (name, target), None) if target else ("kind", name, None)


def _master() -> int | None:
    from mods import op

    return op.ops[0] if op.ops else None


def _layer_problem(state: str, target: str) -> str:
    """Why this layer cannot be put on *target*; empty when it can."""
    scope = _scope(target)
    if scope is None:
        return "目标写 g群号、u私聊号、g群号:QQ号、一整类（group、private、bili 这样的种类名）或 种类:目标；me 不能盖"
    kind = scope[1] if scope[0] == "kind" else scope[1][0]
    if state == "follow" and kind not in _windows.QQ:
        return "follow 只认 QQ 窗口：B 站这类窗口只收到回复和 @，本来就都叫醒你"
    master = _master()
    if state == "mute" and master is not None and scope[0] == "window" and (
            scope[2] == master or tuple(scope[1]) == ("private", master)):
        return "master 不能静音"
    return ""


def _once(line: str) -> dict | None:
    """The rules beyond ``every``/``daily``/``at``; None when the line is not one."""
    key = line.strip()
    if match := _LAYER.match(line):
        moment = _moment(match[3], match[4], match[5])
        problem = "日期不对" if moment is None else _layer_problem(match[1], match[2])
        if problem:
            return {"key": key, "kind": "bad", "why": problem}
        return {"key": key, "kind": "layer", "state": match[1], "target": match[2], "scope": _scope(match[2]),
                "moment": moment, "until": f"{match[3]} {int(match[4]):02d}:{match[5]}", "reason": match[6]}
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
        if rule["kind"] == "layer":
            if now >= rule["moment"]:
                _claim([rule["key"]])
                _since.pop(rule["target"], None)
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
    """The ``on``/``wait`` rules this message satisfies, struck out of the note."""
    found, _skipped = rules()
    matched = [rule for rule in found if rule["kind"] in ("on", "wait") and rule["window"] == window
               and (rule["author"] is None or rule["author"] == author)]
    if not matched:
        return []
    claimed = set(_claim([rule["key"] for rule in matched]))
    return [rule for rule in matched if rule["key"] in claimed]


def _layers(now: float) -> list[dict]:
    found, _skipped = rules()
    layers = [dict(rule, decide=rule["state"] == "follow",
                   since=_since.get(rule["target"]) if rule["state"] == "follow" else None)
              for rule in found if rule["kind"] == "layer" and now < rule["moment"]]
    for target, custom in list(_custom.items()):
        if custom["moment"] is not None and now >= custom["moment"]:
            _custom.pop(target, None)
            continue
        until = (datetime.fromtimestamp(custom["moment"]).strftime("%Y-%m-%d %H:%M")
                 if custom["moment"] is not None else "")
        layers.append({"state": "other", "target": target, "scope": _scope(target), "moment": custom["moment"],
                       "until": until, "reason": custom["reason"], "decide": custom["decide"]})
    return layers


def spoke(window: tuple) -> None:
    """She just sent something into *window*: listen to it for a while."""
    with _hearing:
        _heard[tuple(window)] = {"spoke": time.time(), "dismissed": 0}


def dismissed(windows: list[tuple]) -> None:
    """She dismissed a stretch that showed these windows; two in a row end listening there."""
    with _hearing:
        for window in windows:
            if tuple(window) in _heard:
                _heard[tuple(window)]["dismissed"] += 1


def _listening(window: tuple, now: float) -> dict | None:
    """The automatic follow on a group she has just spoken in, shaped like a layer; None when it is over."""
    heard = _heard.get(window)
    if window[0] != "group" or heard is None or now >= heard["spoke"] + LISTEN or heard["dismissed"] >= 2:
        return None
    moment = heard["spoke"] + LISTEN
    return {"state": "follow", "target": _windows.label(window), "scope": ("window", window, None),
            "moment": moment, "until": datetime.fromtimestamp(moment).strftime("%Y-%m-%d %H:%M"),
            "reason": "你刚在这说过话，听听接下来的", "decide": True, "auto": True, "since": heard["spoke"]}


def layer(window: tuple, author=None, now: float | None = None) -> dict | None:
    """The layer over *window* for something by *author*, the most specific one; None when there is none."""
    window = tuple(window)
    if window == _windows.ME:
        return None
    now = time.time() if now is None else now
    ranked = []
    for item in _layers(now):
        scope = item["scope"]
        if scope[0] == "kind":
            if window[0] == scope[1]:
                ranked.append((0, item))
        elif tuple(scope[1]) == window and (scope[2] is None or scope[2] == author):
            ranked.append((1 if scope[2] is None else 2, item))
    return max(ranked, key=lambda pair: pair[0])[1] if ranked else _listening(window, now)


def triggered(window: tuple, event: dict | None, author, default: bool) -> tuple[bool, dict | None]:
    """Whether this arrival wakes her, and the layer that decided; *default* is the window kind's own trigger.

    *event* is the QQ event, or None for a ``chat.push`` record, which a layer can only hold back.
    """
    item = layer(window, author)
    if item is None:
        return default, None
    try:
        wanted = item["decide"] if isinstance(item["decide"], bool) else bool(item["decide"](event))
    except Exception:
        _stream.exception(f"覆盖层判断出错，按默认触发器：{item['target']}")
        return default, None
    if not wanted and default and author is not None and author == _master():
        return True, None
    return wanted, item


def describe(item: dict) -> str:
    """``mute 到 2026-10-24 08:00`` / ``follow（只看 30001）到 …`` / ``other（整类 bili）到重启为止``."""
    scope = item["scope"]
    where = ("（说完话自动听）" if item.get("auto") else f"（整类 {scope[1]}）" if scope[0] == "kind"
             else f"（只看 {scope[2]}）" if scope[2] is not None else "")
    return f"{item['state']}{where}" + (f" 到 {item['until']}" if item["until"] else " 到重启为止")


def badges(window: tuple) -> list[str]:
    """Every layer lying on *window* (whole kind, whole window, one person), for her to see on the screen."""
    window = tuple(window)
    now = time.time()
    found = [describe(item) for item in _layers(now)
             if item["scope"][0] == "kind" and window[0] == item["scope"][1]
             or item["scope"][0] == "window" and tuple(item["scope"][1]) == window]
    all_rules, _ = rules()
    for rule in all_rules:
        if rule["kind"] == "wait" and rule["window"] == window and now < rule["moment"]:
            target_desc = f"只看 {rule['author']}" if rule.get("author") is not None else "等回复"
            found.append(f"{target_desc}到 {rule['until']}")
    auto = layer(window, None, now)
    return found + ([describe(auto)] if auto is not None and auto.get("auto") else [])


def _strip_layer(text: str, target: str) -> str:
    return "\n".join(line for line in text.split("\n")
                     if not ((match := _LAYER.match(line)) and match[2] == target))


def _strip_wait(text: str, target: str) -> str:
    """Strip waiting rules for the same window as target (keep only the newest wait)."""
    try:
        target_window = _target(target)[0]
    except Exception:
        return text
    kept = []
    for line in text.split("\n"):
        match = _WAIT.match(line)
        if match:
            try:
                if _target(match[1])[0] == target_window:
                    continue
            except Exception:
                pass
        kept.append(line)
    return "\n".join(kept)


def lift_wait(target: str) -> bool:
    """Take any waiting rule off *target*'s window; True if any was removed."""
    try:
        scope = _scope(target)
        if scope is None or scope[0] != "window":
            return False
        window = tuple(scope[1])
    except Exception:
        return False
    with _edit:
        text = _notes.read("awaker")
        lines = text.split("\n")
        kept = []
        found = False
        for line in lines:
            match = _WAIT.match(line)
            if match:
                try:
                    if _target(match[1])[0] == window:
                        found = True
                        continue
                except Exception:
                    pass
            kept.append(line)
        if found:
            _notes.write("awaker", "\n".join(kept).strip("\n") + "\n" if any(x.strip() for x in kept) else "")
    return found


def lift(target: str) -> bool:
    """Take every layer off *target*, written or set by code, and stop listening there; False when there was none."""
    scope = _scope(target)
    window = tuple(scope[1]) if scope is not None and scope[0] == "window" and scope[2] is None else None
    found = window is not None and _listening(window, time.time()) is not None
    if window is not None:
        with _hearing:
            _heard.pop(window, None)
    found = _custom.pop(target, None) is not None or found
    _since.pop(target, None)
    with _edit:
        text = _notes.read("awaker")
        kept = _strip_layer(text, target)
        if kept != text:
            found = True
            _notes.write("awaker", kept.strip("\n") + "\n" if kept.strip() else "")
    return found


def override(target: str, decide, until: float | None = None, reason: str = "") -> str:
    """Put an ``other`` layer on *target*: ``decide(event)`` says whether it wakes her (event is None for a push).

    *until* is a timestamp, or None to keep it until it is lifted or the Bot restarts; *decide* None lifts.
    It replaces a follow/mute line on the same target. Returns what she will see on that window.
    """
    target = str(target).strip()
    if decide is None:
        lift(target)
        return f"已揭掉 {target} 的覆盖层"
    if not callable(decide) and not isinstance(decide, bool):
        raise TypeError("decide 要是函数（收到这条事件，返回叫不叫）或者 True/False")
    problem = _layer_problem("other", target)
    if problem:
        raise ValueError(problem)
    with _edit:
        text = _notes.read("awaker")
        kept = _strip_layer(text, target)
        if kept != text:
            _notes.write("awaker", kept.strip("\n") + "\n" if kept.strip() else "")
    _custom[target] = {"decide": decide, "moment": until, "reason": str(reason)}
    _since.pop(target, None)
    item = next(item for item in _layers(time.time()) if item["state"] == "other" and item["target"] == target)
    return f"{target}：{describe(item)}"


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
    elif match := re.fullmatch(r"(follow|mute)\s+(\S+)(?:\s+(\S+))?", when):
        problem = _layer_problem(match[1], match[2])
        if problem:
            raise ValueError(problem)
        seconds = _interval(match[3] or "30m")
        if not seconds:
            raise ValueError(f"{match[1]} 后面写目标和多久，例如 {match[1]} g123 30m；揭掉写 {match[1]} g123 off")
        line = f"{match[1]} {match[2]} until {stamp(seconds)} {reason}"
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
        if found[0]["kind"] == "layer":
            # 同一目标只留一层，以这次为准：续期，或者 follow 换成 mute。
            base = _strip_layer(base, found[0]["target"])
            _custom.pop(found[0]["target"], None)
        elif found[0]["kind"] == "wait":
            # 同一窗口只留一条 wait：续期或更新等待目标/理由
            base = _strip_wait(base, found[0]["target"])
        _notes.write("awaker", base.rstrip("\n") + "\n" + line + "\n")
    if found[0]["kind"] == "layer":
        # 续期不改起点；换成 mute 就没有起点了。
        if found[0]["state"] == "follow":
            _since.setdefault(found[0]["target"], now)
        else:
            _since.pop(found[0]["target"], None)
    return line


def report() -> str:
    found, skipped = rules()
    last = _last()
    lines = [("（awaker 笔记是空的，用默认）" if not _notes.read("awaker").strip() else "awaker 规则：")]
    for rule in found:
        if rule["kind"] == "layer":
            lines.append(f"{rule['key']}  ← 覆盖层 {rule['state']}，到期划掉")
            continue
        if rule["kind"] in ("on", "wait", "when"):
            lines.append(f"{rule['key']}  ← 等着，响过就划掉")
            continue
        stamp = last.get(rule["key"])
        shown = datetime.fromtimestamp(stamp).strftime("%m-%d %H:%M") if isinstance(stamp, (int, float)) else "还没响过"
        lines.append(f"{rule['key']}  ← 上次 {shown}")
    lines.extend(f"{item['target']} 覆盖层 {describe(item)}  ← 代码设的，重启就没了"
                 for item in _layers(time.time()) if item["state"] == "other")
    lines.extend(f"{item['target']} 覆盖层 {describe(item)}  ← 她刚在这说过话，连着两次 dismiss 就停"
                 for window in list(_heard) if (item := layer(window)) is not None and item.get("auto"))
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
