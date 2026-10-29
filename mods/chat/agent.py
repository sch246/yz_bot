"""The central agent loop: one context, appended to on every wake, until a handoff.

WHY: 2026-10-22 维护者定：只要不交接，就一直是同一个上下文。交接之后第一次被叫醒时
从头组装一次——开头是 self、注意事项和工具状态，接着是笔记、一份只列名字的目录、
这次唤醒的画面；之后每次唤醒只往后追加新消息、第一次出现的人或群的笔记、谁叫醒的她，
前面一个字都不动。这一段存在 ``segment`` 的普通文件里，重启后接着用。

交接的驱动力是"这段上下文要被移除了，确认交接做好"：在同一段末尾追加一句交接指令，
她把各类信息确认到该在的地方（recent、人和群的笔记、todo、当天日记；global 留给夜里反思统一打理），
这段才归档。笔记都是改写成应有的样子，所以交接可以安全地跑很多次。

WHY: 交接由她自己在同一段里做，不另起一个只看到摘要的"图书管理员"会话：总结的人得
看到和她一样多，只是目的不同；同一段上下文还能整段命中前缀缓存。
"""

from __future__ import annotations

from pathlib import Path

import json
import re
import threading
import time

from mods import context, identity, image, llm, oplog, py, shell, storage

import mods.chat as _chat_root
from . import notes as _notes, roles as _roles, screen as _screen, segment as _segment, view as _view, windows as _windows

# WHY: 什么时候交接是可以改的策略（2026-10-22 维护者："唤醒条件本身也配置化"）。
# 条件是 `data/notes/policy/tidy.py` 里的 Python，用 `.py` 那份共享环境求值，末行的值为真
# （或是一段说明原因的文字）就交接；文件不存在时用下面这份默认，和它写成文件后一模一样。
DEFAULT_TIDY = """# 什么时候交接。每次子请求结束（stage == "round"）和每次唤醒往这一段追加之前（stage == "assemble"）
# 各求值一次：前面的行照常执行，最后一行是一个表达式，它的值为真就交接，是字符串时就是交接的原因。
# 可用的名字：stage、used（这一段上下文的 token 数，以接口上次报的为准，之后追加的按字数估）、grown（这次唤醒长了多少）、limit（#limit 设的整理阈值）、
# since_handoff（这一段开始了多少秒）、kind（唤醒／闹钟／反思）、hour。
"上下文太长" if used > limit else None
"""
_KNOWN_PEOPLE = 20
_TYPING_EVERY = 5
# 循环：一轮结束后隔几秒叫醒她；她连着几轮什么都没做时间隔翻倍，最长拉到多久。
_LOOP_GAP = 2.0
_LOOP_IDLE_MAX = 1800.0
# 开着的循环（只在内存里，重启就没了）：在做什么、转到第几轮、下一轮是不是已经排上了、排上的是第几次
# 排的（她 wait 一下，先排上的那一轮就作废）；她 wait 了还有 `due`（下一轮的时刻）和 `waited`（等了几秒）。
_loop: dict | None = None
# 一次唤醒里最多交接几次：防她每一段都调 tidy 时原地打转，不是常态限制。
_HANDOFFS_PER_WAKE = 3
_FORCE_AFTER_FAILURES = 2
_FORCE_OVER_THRESHOLD = 2
# 醒来看见上下文超过整理阈值时，先提醒几次再交接；她自己也提过这个（proposals 第二条）。
_TIDY_GRACE = 2


# 叫醒她那行后面跟一句"谁在管、改哪里"，省得她只看见"闹钟"两个字不知道是谁安排的。
_WHY = (
    ("闹钟", "awaker 的定时规则，改 data/notes/awaker.md"),
    ("循环", "循环的下一轮；要停就 wait 或者关掉总开关"),
    ("命令", "命令跑完了才叫；改 data/storage/agent.json 的 long_command_wake"),
    ("手机", "手机上的动作；改 mods/tools/phone.py"),
)


def _why(kind: str) -> str:
    """Which rule woke her, and where that rule is changed; empty when nothing useful is known."""
    for head, text in _WHY:
        if kind.startswith(head):
            return text
    if kind.startswith("B 站"):
        return "B 站订阅；用 bili__subs 改"
    if kind in ("名字", "戳一戳", "点名", "at"):
        return "有人点了你的名；不想被叫就摘掉这个群的唤醒"
    return ""


def _woken(notice: dict | None, turn) -> tuple[list[tuple], list[str], list[int]]:
    windows: list[tuple] = []
    lines: list[str] = []
    authors: list[int] = []
    if notice is not None:
        for item in notice.get("activations", []):
            if isinstance(item.get("user_id"), int):
                authors.append(item["user_id"])
            window = tuple(item["window"])
            windows.append(window)
            stamp = _screen.clock(item["time"]) if isinstance(item.get("time"), (int, float)) else "?"
            author = f" 作者={item['user_id']}" if item.get("user_id") is not None else ""
            kind = item.get("kind") or "唤醒"
            why = _why(kind)
            lines.append(f"{_screen.label(window)} {kind}{author} 时间={stamp}" + (f"｜{why}" if why else ""))
        for window in notice.get("windows", []):
            windows.append(tuple(window))
    windows.extend(window for window in turn.associated_windows if _windows.known(window))
    return list(dict.fromkeys(windows)), lines, authors


def _note_block(scopes: list[str], title: str) -> dict | None:
    parts = [text for scope in scopes if (text := _notes.render(scope))]
    if not parts:
        return None
    return {"role": "system", "content": title + "\n\n" + "\n\n".join(parts)}


def _head(woken: list[tuple], segment: dict) -> list[dict]:
    """What a new segment starts with: her standing notes and a directory of the rest.

    WHY: 做法（global）、交接下来的 recent、昨天的日记和当天的日记在每一段开头；叫醒她的窗口和
    叫她的人的笔记跟着画面一起出现（见 `_people`）。待办每个子请求都挂在末尾。别的都由
    她自己召回，目录只列名字。
    """
    days = [key for key in _notes.diaries() if len(key) == 10]
    today = _screen.clock()[:10]
    past_days = [d for d in days if d != today]
    # WHY: 2026-10-29 草籽：每天都默认能看到当天的日记（中途写了或跨天也自动载入）；
    # 昨天的日记（若有）作为前置背景一起带上，白天随时能看到最新的今天日记。
    scopes = ["global", "recent"]
    # WHY: 2026-10-29 草籽：两天的日记太少，先放到 7 天（要在眼前才叫记得住）。
    for day in past_days[-6:]:
        scopes.append(f"diary:{day}")
    if today in days or _notes.render(f"diary:{today}"):
        scopes.append(f"diary:{today}")
    segment["scopes"] = list(scopes)
    hashes = segment.setdefault("note_hashes", {})
    for s in scopes:
        hashes[s] = _notes.note_hash(s)
    messages = []
    block = _note_block(scopes, "以下是你的笔记（普通文件，你随时能改，可能不完整）：")
    if block:
        messages.append(block)
    index = _notes.years()
    if index:
        messages.append({"role": "system", "content":
                         "之前交接过的段（年鉴，一行一段，最近 8 段；要看原样用 read_context(\"段名\")）：\n"
                         + "\n".join(index)})
    messages.append({"role": "system", "content": "还能召回的东西（用 search 找，read_log、read_actions、read_note 读）：\n"
                     + _roles.directory(woken)})
    return messages


def _people(windows: list[tuple], authors: list[int], segment: dict) -> tuple[list[dict], list[str]]:
    """Notes for windows and people appearing in this segment for the first time, and their scopes."""
    scopes = [_notes.scope_of(window) for window in windows if window[0] in _windows.QQ]
    scopes.extend(f"person:{author}" for author in authors)
    seen = set(segment.get("scopes", ()))
    new = [scope for scope in dict.fromkeys(scopes) if scope not in seen and _notes.render(scope)][:_KNOWN_PEOPLE]
    hashes = segment.setdefault("note_hashes", {})
    for s in new:
        hashes[s] = _notes.note_hash(s)
    block = _note_block(new, "这一段里第一次出现的人或群，你的笔记：")
    return ([block] if block else []), new


def _box_note(boxes: dict) -> str:
    """Unsent input boxes, told once after a handoff.

    WHY: 维护者 2026-10-23：type_word 的返回已经在上下文里，每次请求末尾再列一遍是重复。
    只有交接后要说：输入框存入 storage 跨唤醒与交接都在，打字那次的返回却随上一段归档了。
    """
    unsent = [f"{label}：{box['text'] if isinstance(box, dict) else box}"
              for label, box in boxes.items()
              if (box.get("text") if isinstance(box, dict) else str(box).strip())]
    if not unsent:
        return ""
    return ("交接前你在输入框里打了还没发的字：\n"
            + "\n".join(unsent)
            + "\n要发就 send，不发就不管它。")


def _did_something(messages: list[dict]) -> bool:
    return any(message.get("role") == "assistant" and not _view.is_incoming(message) for message in messages)


def _tidy_reason(turn, segment: dict, stage: str, used: int, grown: int = 0) -> str:
    """Evaluate the handoff condition; a non-empty string means hand off now, for that reason."""
    _max_events, threshold = _chat_root.limit()
    namespace = dict(py.loc)
    namespace.update(stage=stage, used=used, grown=grown, limit=threshold,
                     since_handoff=time.time() - segment["started"], kind=turn.log["kind"],
                     hour=time.localtime().tm_hour)
    code = _notes.read_policy("tidy").strip() or DEFAULT_TIDY
    try:
        result = py.eval_last(code, namespace)
    except Exception:
        # 她或维护者写坏了条件：这次按默认判断，终端里留下 traceback。
        _roles._stream.exception("交接条件求值失败，按默认判断")
        result = py.eval_last(DEFAULT_TIDY, namespace) if code != DEFAULT_TIDY else None
    if not result:
        return ""
    return result if isinstance(result, str) else "交接条件成立"


def _grace(turn, segment: dict, reason: str, used: int) -> str:
    """Hold the handoff back a few wakes and remind her instead; "" means only reminded this time.

    WHY: 2026-10-26 维护者："到了先插 hint 提示交接，连续几次不交接才强制。" 原来一过阈值
    就在下一次唤醒开头交接，不管她手上在做什么；现在先让她自己挑时机，逾期才交接。
    """
    if not reason or segment.get("typed"):
        return reason
    _max_events, threshold = _chat_root.limit()
    if used <= threshold:
        if segment.get("grace"):
            # 已经退回来了（收过一段），宽限重新算。
            segment["grace"] = 0
            _segment.save(segment)
        return reason
    grace = int(segment.get("grace") or 0)
    if grace >= _TIDY_GRACE:
        return reason
    segment["grace"] = grace + 1
    _segment.save(segment)
    left = _TIDY_GRACE - segment["grace"]
    turn.tidy_hint = (f"这一段已经 {used} token，超过整理阈值 {threshold}；"
                      + ("这次唤醒结束前用 tidy 交接一次，不然下次醒来看见就会直接交接。" if left <= 0
                         else f"再做 {left} 次唤醒还没交接，下次醒来看见就会直接交接——可以现在就交接。"))
    return ""


def diary_day(moment: float) -> str:
    """The day a handoff writes about: until 6 a.m. it is still the day before."""
    return time.strftime("%Y-%m-%d", time.localtime(moment - 6 * 3600))


def _handoff(turn, reason: str, model: str | None = None) -> bool:
    """Confirm the handoff at the end of the current segment, then archive it.

    WHY: 交接没跑完（出错、被打断、没有输出）就不归档：这一段原样留着，下次再交接一遍。
    笔记都是改写成应有的样子、内容没变就不写，所以重跑不会重复。"跑完"指她最后一次回复
    不再调用工具：2026-10-23 审查发现，只看"有过输出"时，中途出错、被打断、被阈值停下
    都会被当成交接完成而归档，没来得及记下的东西就跟着这段一起离开了眼前。
    """
    segment = _segment.load()
    if not _did_something(segment["messages"]):
        return False
    if turn.handoffs >= _HANDOFFS_PER_WAKE:
        _roles._stream.warning(f"这次唤醒已经交接了 {turn.handoffs} 次，不再交接（原因：{reason}）")
        turn.handoff_failed = True
        return False
    # 强制归档也算一次：续做时每段都长过两倍阈值的话，不能借它绕过上限原地打转。
    turn.handoffs += 1
    _max_events, threshold = _chat_root.limit()
    forced = _forced_reason(segment, threshold)
    if forced:
        name = _archive(turn, segment, f"未交接，强制归档（{forced}；本来要交接的原因：{reason}）")
        _roles._stream.warning(f"这一段{forced}，没有交接就归档为 {name}")
        # WHY: 这句提醒不能只挂在这次唤醒上：接下来的请求要是失败、没有输出，新的一段不会保存，
        # 她就再也不知道该去补哪一段（2026-10-23 审查）。所以写进新的一段里，第一次输出时才消掉。
        fresh = _segment.fresh()
        fresh["notice"] = (f"上一段上下文没能交接（{forced}），已经原样归档为 {name}。recent 和其它笔记可能没记到最后；"
                           f"需要时加载 memory_system 用 read_context(\"{name}\") 读那一段，把漏下的补进笔记。")
        _segment.save(fresh)
        turn.handoff_note = ""
        return True
    messages = [*segment["messages"],
                {"role": "system", "content": _roles.handoff_prompt(reason, diary_day(time.time()))}]
    grew = []

    def stop_when(used: int, initial: int) -> bool:
        if used - initial > threshold:
            grew.append(used - initial)
            return True
        return False

    session, failure = None, ""
    try:
        session = _run_session(model, turn, segment, messages, lambda: None, stop_when,
                               quiet=True, persist=False, fail_fast=True, known=len(segment["messages"]))
    except Exception as error:
        _roles._stream.exception("交接出错")
        failure = f"出错：{type(error).__name__}: {error}"
    turn.release = ""
    if not failure:
        added = session.messages[_system_count(session) + len(messages):]
        last = session.messages[-1] if session.messages else {}
        if turn.cancelled:
            failure = "被打断"
        elif grew:
            failure = f"交接时又长了 {grew[0]} token，超过整理阈值，被停下"
        elif not any(message.get("role") == "assistant" and (str(message.get("content") or "").strip()
                                                             or message.get("tool_calls")) for message in added):
            failure = "没有输出"
        elif last.get("role") != "assistant" or last.get("tool_calls"):
            failure = "没有正常结束"
    if failure:
        turn.handoff_failed = True
        if failure != "被打断":
            # 被打断是有人 ^C，不算交接本身的毛病；其余的记在这一段里，连着失败就强制归档。
            segment["handoff_failures"] = segment.get("handoff_failures", 0) + 1
            _segment.save(segment)
        _roles._stream.warning(f"交接没完成（{failure}），这一段（{_screen.clock(segment['started'])} 开始）原样留着；"
                               f"已经连着失败 {segment.get('handoff_failures', 0)} 次")
        return False
    segment["messages"] = _body(session)
    name = _archive(turn, segment, reason)
    _roles._stream.info(f"交接完成（{reason}），归档为 {name}")
    turn.handoff_note = f"你刚把上一段上下文交接掉了（原因：{reason}）。接着做刚才的事，做到哪儿见 recent。"
    return True


def _forced_reason(segment: dict, threshold: int) -> str:
    """Why this segment must be archived without a handoff, or empty.

    WHY: 维护者 2026-10-23：整理阈值设在模型上下文的一半左右，正常不会走到这里；真走到了
    （交接连着失败，或者这一段已经是阈值的两倍、连交接请求本身都发不出去），就别卡死，
    原样归档、告诉她，由她自己去读那一段补笔记。
    """
    failures = segment.get("handoff_failures", 0)
    if failures >= _FORCE_AFTER_FAILURES:
        return f"交接连着失败了 {failures} 次"
    used = _segment.context_size(segment.get("tokens"), segment["messages"])
    if used > threshold * _FORCE_OVER_THRESHOLD:
        return f"已经长到 {used} token，超过整理阈值的 {_FORCE_OVER_THRESHOLD} 倍"
    return ""


def _archive(turn, segment: dict, reason: str) -> str:
    segment["reason"] = reason
    name = _segment.archive(segment)
    turn.handed_off = reason
    return name


def _system_count(session: llm.Chat) -> int:
    """How many leading messages are the system prompt rebuilt on every wake.

    WHY: 2026-10-23 起这一段里也有 system 消息（系统自己说的话），不能再按"第一条不是
    system 的消息"找边界，否则这一段开头的笔记会被当成系统提示、存的时候丢掉。
    """
    return session.prompt_count


def _begin(turn, window: tuple | None, kind: str) -> None:
    turn._chat_usage_tokens = 0
    turn.requested_reads = []
    turn.associated_windows = {window} if window is not None else set()
    turn.screens = {}
    turn.authors = []
    turn.release = ""
    turn.handed_off = ""
    turn.handoff_note = ""
    turn.handoff_failed = False
    turn.handoffs = 0
    turn.tidy_hint = ""
    # WHY: 维护者 2026-10-28：输入框草稿像真实 QQ 一样保留在各个窗口里，发了才消失；
    # 存入 storage（内存权威+延迟刷盘），平时纯内存读写无 IO 开销，关机重启也不丢草稿。
    turn.boxes = storage.get("", "boxes")
    turn.log = {"kind": kind, "start": time.time(), "peak": 0, "acts": 0}
    # 她这次唤醒里自己关了循环：总开关开着也不在这次唤醒结束时再起一串。
    turn.loop_stopped = False


def _keep_typing(turn, window: tuple | None, stop: threading.Event) -> None:
    """Show "对方正在输入…" in the private chats she is working on until the wake ends.

    WHY: 维护者 2026-10-22 想要循环进行时有"正在输入"。NapCat 的 set_input_status 只支持
    私聊（C2C），QQ 群没有这个状态。没有明确的"取消"调用，所以隔几秒续一次，唤醒结束
    就不再续。失败只记一次，不影响她干活。
    """
    from mods import connect

    failed = False
    while not stop.is_set():
        try:
            targets = {window} if window is not None and window[0] == "private" else set()
            targets.update(_windows.parse_target(label) for label in tuple(turn.screens) if label.startswith("u"))
            for target in targets:
                reply = connect.call_api("set_input_status", user_id=str(target[1]), event_type=1)
                if reply.get("retcode") not in (0, None) and not failed:
                    failed = True
                    _roles._stream.warning(f"设置正在输入失败：{reply.get('wording') or reply.get('retcode')}")
        except Exception:
            if not failed:
                failed = True
                _roles._stream.exception("设置正在输入失败")
        stop.wait(_TYPING_EVERY)


def _drive_agent(model: str | None, window: tuple | None) -> bool:
    """Hold the agent's turn until nothing is left to do; False when someone else holds it."""
    turn, owner = context.begin_turn(_chat_root.AGENT_WINDOW)
    if not owner:
        return False
    _begin(turn, window, "闹钟" if window == _windows.ME else "唤醒")
    typing = threading.Event()
    threading.Thread(target=_keep_typing, args=(turn, window, typing), name="chat-typing", daemon=True).start()
    # WHY: 一次对话 = 这次持有的全过程（多轮 + 插话续写，直到 finally），图片检查台账就
    # 活在这段里：同一张图不重复下载/解析，对话结束即清掉，下次再聊重新检查一遍。
    image_ledger = image.begin_conversation()
    origin = context.current()
    try:
        while True:
            context.set_current(None)
            context.set_agent_mode(True)
            try:
                if not _run_agent(model, turn):
                    return True
                if turn.release and not turn.cancelled:
                    # 她叫了 tidy，或者这一段涨过阈值：先交接，再用新的一段接着干。
                    if not turn.handoff_failed:
                        _handoff(turn, turn.release, model)
                    turn.release = ""
                    continue
            finally:
                context.set_agent_mode(False)
                context.set_current(origin)
            if turn.cancelled:
                return True
            turn.associated_windows.intersection_update(
                {window for window, _count, active in oplog.pending_summary() if active})
            # 先排下一轮循环：它隔几秒才到，期间来的召唤由下面 finish_turn 接上。
            _next_loop(turn)
            if not context.finish_turn(_chat_root.AGENT_WINDOW, turn, oplog.has_unnotified):
                return True
    finally:
        if turn.cancelled:
            # 被叫停的那一轮，循环也一起停：维护者 ^C 就是要她停下。
            stop_loop("这一轮被叫停了")
        typing.set()
        image.end_conversation(image_ledger)
        context.end_turn(_chat_root.AGENT_WINDOW, turn)
        # WHY: 循环停下的唯一信号就在这里，见 _run_hint。`#hint` 只有 QQ 窗口有。
        if origin is not None and window is not None and window[0] in _windows.QQ:
            _chat_root._run_hint(window, turn)


def loop_on() -> bool:
    """`#loop` 总开关，默认开。"""
    return bool(storage.get("", "agent").get("loop", True))


_LOOP_LOG = Path("data") / "context" / "loop.jsonl"


def _log_loop(note: str, round_: int) -> None:
    """把这次备注原样记下来，她要能反查到是哪一轮写的（维护者 2026-10-26）。"""
    if not note.strip():
        return
    try:
        _LOOP_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _LOOP_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": time.time(), "round": round_, "note": note},
                                    ensure_ascii=False) + "\n")
    except OSError:
        pass  # 只是索引，写不进去不该拦住她开循环


def start_loop(note: str) -> dict:
    """Wake her again after every wake until she stops it; returns the loop.

    WHY: 维护者 2026-10-24：她玩游戏时常常不记得接着做，一轮结束会话就停了。循环开着时每轮结束
    隔 `_LOOP_GAP` 秒，往她自己的 me 窗口推一条"循环第几轮：在做什么"叫醒她，直到她想起来关掉。
    不设时长（维护者：不知道什么时候需要停下来）；没人干预就一直转到夜里 4 点整理（`reflect`
    开头关掉它）。维护者叫停她那一轮时也一起停。

    WHY: 维护者 2026-10-24 又定：做成总开关、默认开（`loop_on`）——循环是常态，结束才要刻意。

    WHY: 维护者 2026-10-26 改掉上面那条的一部分：唤醒结束不再自己起循环，只有她在这里写下在做什么
    才开一串。以前总开关开着时，每次被人叫醒、闹钟到点都会在结束时起一轮空循环，她回回都要再关一次；
    现在 `loop(stop=True)` 停了就是真停，下一次被别人叫醒也不会自己转回去。没写 note 的循环不续轮。
    """
    global _loop
    if _loop is not None:
        _loop["note"] = note
        _loop["since"] = time.time()
        _loop["note_round"] = _loop["round"]
        _log_loop(note, _loop["round"])
        return dict(_loop)
    _loop = {"note": note, "round": 0, "pending": False, "token": 0,
             "since": time.time(), "note_round": 0}
    _log_loop(note, 0)
    return dict(_loop)


def stop_loop(reason: str, rest: bool = False) -> dict | None:
    """Stop the loop; returns what it was, or None when none was running.

    *rest* is her own stop: with the switch on, no new loop starts when this wake ends.
    """
    global _loop
    stopped, _loop = _loop, None
    turn = context.get_turn(_chat_root.AGENT_WINDOW)
    if rest and turn is not None:
        turn.loop_stopped = True
    if stopped:
        _roles._stream.info("循环关了（转了 %d 轮）：%s", stopped["round"], reason)
    return stopped


def _start_quietly() -> dict:
    global _loop
    _loop = {"note": "", "round": 0, "pending": False, "token": 0}
    return _loop


def wait_loop(seconds: float) -> dict | None:
    """Called by the `wait` tool: the next round comes *seconds* from now instead of right after this wake.

    WHY: 维护者 2026-10-24：循环开着时 wait 不阻塞，就是"多久之后叫醒我"。要等别人回、等东西加载、
    等体力恢复时她用它，不必为了等一会儿把循环关掉、再记着重开。已经排上的那一轮作废（换个排号），
    这次唤醒结束时按 `due` 重排；期间来了消息照样叫醒她，那次唤醒结束也还是按 `due` 排。
    """
    loop = _loop
    if loop is None:
        if not loop_on():
            return None
        loop = _start_quietly()
    loop["due"], loop["waited"] = time.time() + seconds, seconds
    loop["token"] += 1
    loop["pending"] = False
    return dict(loop)


def _next_loop(turn) -> None:
    """At the end of a wake: queue the next round of a loop that is already running.

    WHY: 循环只在她在 `loop(note=…)` 里写下在做什么时才存在，唤醒结束不再自己起一串。这次唤醒里她
    一个动作都没做（`dismiss` 不算）时，下一轮的间隔翻倍，最长 `_LOOP_IDLE_MAX`；做了任何事就回到
    `_LOOP_GAP`。这不是自动结束，只是没人理时转慢一点。
    """
    acts, turn.log["acts"] = turn.log.get("acts", 0), 0
    global _loop
    loop = _loop
    if loop is None:
        # WHY: 2026-10-26 定：外部唤醒（有人说话、闹钟、白醒）结束时不再自己起循环。
        # 以前只要总开关开着、这轮没亲手停，就叫醒后立刻起一轮空循环；她 loop(stop=True)
        # 之后下一次被叫醒又转一轮「这一轮没有指定的事」，一天白烧十几次。
        return
    if not loop["note"] and "due" not in loop:
        # WHY: 没写下在做什么的循环没有内容可续，接着转只会每轮重问一遍“要做什么”。
        # 带 `due` 的那种是 wait 排的——她明确说了到点再叫，照常排。
        _loop = None
        return
    if loop["pending"]:
        return
    loop["idle"] = 0 if acts else loop.get("idle", 0) + 1
    gap = min(_LOOP_GAP * 2 ** loop["idle"], _LOOP_IDLE_MAX)
    loop["pending"] = True
    loop["token"] += 1
    delay = max(gap, loop.get("due", 0) - time.time())
    threading.Timer(delay, _loop_round, args=(loop, loop["token"])).start()


def _loop_round(loop: dict, token: int) -> None:
    if _loop is not loop or loop["token"] != token:
        return  # 这期间她关掉、重开了，或者 wait 换了时间
    loop["pending"] = False
    loop.pop("due", None)
    waited = loop.pop("waited", None)
    loop["round"] += 1
    after = f"（等了 {_span(waited)}）" if waited else ""
    if loop["note"]:
        doing = loop["note"]
        since = loop.get("since")
        if since:
            age = time.time() - since
            rounds = loop["round"] - int(loop.get("note_round") or 0)
            if age >= 1800 or rounds >= 5:  # 维护者 2026-10-26：30 分钟或 5 轮之后就算旧了
                doing += f"（这条 {_span(age)}前、第 {loop.get('note_round', 0)} 轮留下的）"
        tail = f"接着做——{doing}"
    else:  # 维护者 2026-10-26 选 B：怕她没接着干，空备注要提示她回头看看有没有没收尾的
        doing = "这一轮没有指定的事"
        tail = "你上一轮没留下要做什么，看看手头还有没有没收尾的"
    try:
        _chat_root.push(_windows.ME, "循环", f"循环第 {loop['round']} 轮{after}：{doing}",
                        wake=f"循环第 {loop['round']} 轮{after}：{tail}；"
                             "要等一会儿、或者暂时没事，就 wait(秒数)，到时候再叫你；这件事彻底做完了才 loop(stop=True)")
    except Exception:
        _roles._stream.exception("循环叫醒她失败，循环停了")
        stop_loop("叫醒失败")


def span(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:g} 小时"
    if seconds >= 60:
        return f"{seconds / 60:g} 分钟"
    return f"{seconds:g} 秒"


_span = span


def loop_status() -> str:
    current = _loop
    state = (f"，这一串转到第 {current['round']} 轮"
             + (f"：{current['note']}" if current.get("note") else "") if current else "")
    return f"loop: {'on' if loop_on() else 'off'}{state}"


def loop_history(last: int = 10) -> str:
    try:
        rows = [json.loads(line) for line in _LOOP_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return "还没有改过循环备注"
    if not rows:
        return "还没有改过循环备注"
    picked = rows[-max(1, min(50, int(last))):]
    lines = []
    for row in picked:
        when = time.strftime("%m-%d %H:%M", time.localtime(float(row.get("t") or 0)))
        lines.append(f"- 第 {row.get('round', '?')} 轮 {when}：{row.get('note', '')}")
    return f"你最近 {len(picked)} 次写下的循环备注（一共 {len(rows)} 条）：\n" + "\n".join(lines)


def clear_loop_history(confirm: bool = False) -> str:
    if not confirm:
        return "这会清掉全部备注历史，确定就再调一次 clear_loop_note(confirm=True)"
    try:
        _LOOP_LOG.unlink(missing_ok=True)
    except OSError as error:
        return f"没清掉：{error}"
    return "循环备注历史清空了"


def request_tidy(note: str) -> bool:
    """Called by the `tidy` tool: hand off this segment once this round ends."""
    turn = context.get_turn(_chat_root.AGENT_WINDOW)
    if turn is None or not hasattr(turn, "release"):
        return False
    turn.release = note or "她自己要求交接"
    return True


def _run_agent(model: str | None, turn) -> bool:
    max_events, _threshold = _chat_root.limit()
    notice = oplog.deliver_notifications(_chat_root.AGENT_WINDOW)
    woken, activation_lines, authors = _woken(notice, turn)
    turn.authors = list(dict.fromkeys([*turn.authors, *authors]))
    continuing = turn.handed_off
    if notice is None and not woken and not continuing:
        return True
    segment = _segment.load()
    if _did_something(segment["messages"]) and not turn.handoff_failed:
        used = _segment.context_size(segment.get("tokens"), segment["messages"])
        reason = _grace(turn, segment, _tidy_reason(turn, segment, "assemble", used), used)
        if reason:
            if _handoff(turn, reason, model):
                continuing = turn.handed_off
            # 没交接成功时文件里多了失败次数，也要重读，否则这次唤醒的保存会把它盖掉。
            segment = _segment.load()
    if _chat_root.get_reasoning_mode() == "drop":
        # 以前几次唤醒的思考不再带回；这次唤醒之内照旧原样保留（DeepSeek 带工具时要求）。
        for message in segment["messages"]:
            if not _view.is_incoming(message):
                message.pop("reasoning_content", None)
    material: list[dict] = []
    if not segment["messages"]:
        material.extend(_head(woken, segment))
    # 交接后续做时没有新唤醒，沿用刚才看着的窗口。
    focus = woken or _windows.parse_all(turn.screens)
    screens: dict[str, str] = {}
    newest: dict[tuple, tuple] = {}
    shown_last: dict[str, str] = {}
    screen_blocks: list[dict] = []
    for window in focus:
        label = _screen.label(window)
        text, mark, last = _screen.slice_window(window, max_events, segment.setdefault("last_shown", {}).get(label))
        newest[window] = mark
        box = turn.boxes.get(label)
        box_text = box.get("text") if isinstance(box, dict) else (box if isinstance(box, str) else "")
        if box_text:
            box_render = f"\n\n【当前输入框】{box_text}"
            text = (text + box_render) if text else f"（{label} 暂无新消息）{box_render}"
        if text:
            screens[label] = text
            shown_last[label] = last
            screen_blocks.append(_view.outside(label, ["画面", _screen.clock(), *_chat_root._awaker.badges(window)], text))
    related_authors = list(dict.fromkeys([*turn.authors, *_screen.extract_qqs(screens.values())]))
    # 记成"这一段出现过"要等第一次输出，和画面位置一样：dismiss 退回去时笔记也跟着回来。
    people, met = _people(focus, related_authors, segment)
    material.extend(people)

    # 检查已载入笔记的变动：若中途笔记被改动（柚子 write_note 或磁盘修改），追加最新版。
    # 既不破坏历史前缀缓存，也保证早期思考依赖早期版本的因果历史完整。
    note_updates: list[str] = []
    hashes = segment.setdefault("note_hashes", {})

    # WHY: 2026-10-29 草籽：每天默认能看到当天的日记。若当天日记中途建起或跨过零点，自动载入。
    today_scope = f"diary:{_screen.clock()[:10]}"
    if today_scope not in segment.setdefault("scopes", []):
        rendered_today = _notes.render(today_scope)
        if rendered_today:
            segment["scopes"].append(today_scope)
            hashes[today_scope] = _notes.note_hash(today_scope)
            note_updates.append(rendered_today)

    for s in segment.get("scopes", []):
        if s in ("self", "todo", "observer"):
            continue
        old_h = hashes.get(s)
        curr_h = _notes.note_hash(s)
        if old_h is not None and curr_h and curr_h != old_h:
            rendered = _notes.render(s)
            if rendered:
                note_updates.append(rendered)
                hashes[s] = curr_h
        elif old_h is None and curr_h:
            hashes[s] = curr_h
    if note_updates:
        material.append({"role": "system", "content": "【笔记更新】以下笔记有更新（以最新内容为准）：\n\n" + "\n\n".join(note_updates)})

    material.extend(screen_blocks)
    now = _screen.clock()
    mark = (_segment.make_mark(segment, now, turn.boxes, _screen.seen_snapshot())
            if notice is not None else None)
    if notice is not None:
        wake_tag = f"#{mark['number']} · " if mark and mark.get("number") else ""
        material.append({"role": "system", "content": f"这次唤醒你的是（{wake_tag}{now}）：\n" + "\n".join(activation_lines)})
    if segment.get("notice"):
        material.append({"role": "system", "content": segment["notice"]})
    if continuing:
        if turn.handoff_note:
            material.append({"role": "system", "content": turn.handoff_note})
        if turn.boxes:
            box_note = _box_note(turn.boxes)
            if box_note:
                material.append({"role": "system", "content": box_note})
        turn.handed_off = ""
    if not material:
        return True

    def first_output() -> None:
        started.append(True)
        if notice is not None:
            oplog.acknowledge_notification(notice["id"])
        if mark is not None:
            _segment.record_mark(segment, mark)
        _screen.mark_seen(newest)
        turn.screens.update(screens)
        segment["windows"] = list(dict.fromkeys([*segment["windows"], *screens]))
        segment["scopes"] = list(dict.fromkeys([*segment["scopes"], *met]))
        segment["last_shown"].update(shown_last)
        segment.pop("notice", None)

    def stop_when(used: int, initial: int) -> bool:
        if turn.release:
            return True
        reason = _grace(turn, segment, _tidy_reason(turn, segment, "round", used, used - initial), used)
        if reason:
            turn.release = reason
            return True
        return False

    started: list[bool] = []
    unconfirmed: dict[tuple, tuple] = {}

    def late() -> list[dict]:
        # 第一次输出之前画面还没标已读，刚到的留给下一次请求一起给。
        if not started:
            return []
        added, marks = _screen.late_arrivals(newest, segment["last_shown"], max_events)
        unconfirmed.update(marks)
        return added

    def seen_late() -> None:
        if unconfirmed:
            _screen.mark_seen(dict(unconfirmed))
            unconfirmed.clear()

    observer_hint = ""
    if _chat_root._observer.is_enabled():
        observer_hint = _chat_root._observer.observe(segment, material, met, should_stop=lambda: turn.cancelled)

    session = _run_session(model, turn, segment, [*segment["messages"], *material], first_output, stop_when,
                           providers=[late],
                           hints=[lambda: _screen.unread_hint(set(newest)), lambda: _segment.nudge(segment),
                                  lambda: shell.running_hint(), lambda: turn.tidy_hint,
                                  lambda: observer_hint],
                           known=len(segment["messages"]), each_output=seen_late)
    if (getattr(session, "dismiss", None) is None and not turn.log.get("acts")
            and getattr(session, "segment", None) and session.segment.get("marks")):
        # 这一轮一个工具都没调（dismiss 不算动作）：只是被叫醒看了一眼，痕迹不用留。
        # WHY: 2026-10-26 维护者：她常常想完就算了，忘了自己调 dismiss，白醒的那次就挂在上下文里。
        _segment.request_dismiss(session)
    if getattr(session, "dismiss", None) is not None:
        # 工具里查过一次；同一次输出里排在它后面的 send、type_word 可能又改了情况，这里再查。
        problem = _segment.apply_dismiss(session, segment)
        if problem:
            _roles._stream.warning(f"dismiss 没有生效：{problem}")
    return session.output_recorded


request_dismiss = _segment.request_dismiss
request_compress = _segment.request_compress


def _body(session: llm.Chat) -> list[dict]:
    """Everything after the system prompt, which is rebuilt on every wake."""
    return session.messages[_system_count(session):]


def _state_index(messages: list[dict]) -> int:
    """Where the tool state goes in *messages*: after the notes, before the first screen.

    WHY: 2026-10-26 维护者：工具状态是参考手册，不该排在她的笔记前面。它在这一段里只写一次、
    之后不再动，挪到哪都保持前缀稳定；放在第一条画面之前，新段开头读到的顺序是
    人设 → 身份 → 笔记 → 目录 → 相关人的笔记 → 工具状态 → 画面。
    """
    for offset, message in enumerate(messages):
        if message.get("role") == "user":
            return offset
    return len(messages)


def _run_session(model: str | None, turn, segment: dict, messages: list[dict], first_output, stop_when,
                 *, providers=(), hints=(), load: tuple[str, ...] = (), quiet: bool = False, persist: bool = True,
                 fail_fast: bool = False, known: int = 0, each_output=None) -> llm.Chat:
    """One model session over the segment plus this wake's material; outputs and results go to the oplog.

    With *persist*, the segment file follows the session after every round, but only
    once she has produced output: a request that fails before that leaves the file
    as it was, so the same material is not appended twice.  The first *known* of
    *messages* are already in the segment, so the request log skips them.
    """
    session = llm.Chat(model=model or _chat_root.get_model(), chat_client=llm.get_client())
    session.requested_reads = turn.requested_reads
    session.associated_windows = turn.associated_windows
    session.boxes = turn.boxes
    session.segment = segment
    session.last_sent = segment.get("last_sent", 0.0)
    session.quiet = quiet
    session.fail_fast = fail_fast
    binding = _chat_root._activate_chat(session, messages)
    session.prompt_count = len(session.messages) - len(messages)
    # WHY: 维护者 2026-10-23：别人说的话放 user，系统自己说的话（唤醒原因、笔记、交接、工具
    # 状态、末尾提示）放对话中间的 system。DeepSeek 官方文档说 Chat Completions 支持在对话
    # 中间插 system、不支持插工具调用，所以先前那对合成的 incoming 工具调用换成了这样；实测
    # （仓库外的脚本）对话中间的 system 在思考模式、带工具、前面的思考被删掉时都被接受，四个
    # 行为任务上不比合成框差。
    session.hint_role = "system"
    # WHY: 原生 assistant/tool 配对原样留在 messages（含思考，DeepSeek 带工具时要求回传），
    # 并随这一段一起存下：下次唤醒看到的就是这一路下来的原样。
    session.preserve_native = True
    session.keep_reasoning = True
    if load:
        binding.load(list(load))
    binding.sync_registry()
    told = [segment.get("told")]
    if told[0] is None:
        # 新的一段：完整工具状态写一次，位置在笔记之后、第一条画面之前，这一段里不再变。
        state = binding.state_text(None)
        if state:
            index = session.prompt_count + _state_index(messages)
            session.messages.insert(index, {"role": "system", "content": state})
        told[0] = binding.state_snapshot()
    # WHY: 请求日志以前每次唤醒都从头打印，同一段越长、醒得越勤，llm.log 里同样的前缀就重复
    # 越多遍（2026-10-23 审查）。这一段已有的部分在 current.json 和归档里都有原样；新的一段
    # 从头打印，系统提示因此每段记一次。
    session.logged = _system_count(session) + known if known else 0

    def tool_changes() -> list[dict]:
        binding.sync_registry()
        current = binding.state_snapshot()
        if current == told[0]:
            return []
        text = binding.state_text(told[0])
        told[0] = current
        return [{"role": "system", "content": text}] if text else []
    session.output_recorded = False
    session.last_usage = None
    reported = [segment.get("tokens")]
    initial = _segment.context_size(reported[0], _body(session))
    turn._chat_usage_tokens = initial
    turn.log["peak"] = max(turn.log["peak"], initial)

    def save() -> None:
        if persist and session.output_recorded:
            segment["messages"] = _body(session)
            segment["told"] = told[0]
            if session.last_sent:
                segment["last_sent"] = session.last_sent
            _segment.save(segment)

    def record_output(assistant, calls):
        if session.last_usage:
            # 报的是刚发出去的整份请求加这次回答；回答在这之后才追加进 messages。
            reported[0] = [session.last_usage, len(_body(session)) + 1]
            session.last_usage = None
            if persist:
                segment["tokens"] = reported[0]
        source = oplog.output(_chat_root.AGENT_WINDOW, assistant, calls)
        turn.log["acts"] = turn.log.get("acts", 0) + sum(
            1 for call in calls or [] if call.get("function", {}).get("name") != "dismiss")
        if not session.output_recorded:
            first_output()
        session.output_recorded = True
        if each_output is not None:
            each_output()
        return source

    def record_results(source, results, _native):
        for result in results:
            binding.touch(result.name)
        if results:
            oplog.result(_chat_root.AGENT_WINDOW, source, [
                {"position": position, "name": result.name, "arguments": result.arguments,
                 "content": result.content, "tool_call_id": result.tool_call_id}
                for position, result in enumerate(results)], "")
        if getattr(session, "pending_compress", None) is not None:
            _segment.finish_compress(session, results)

    def turn_done() -> bool:
        used = _segment.context_size(reported[0], _body(session))
        turn._chat_usage_tokens = used
        turn.log["peak"] = max(turn.log["peak"], used)
        save()
        return stop_when(used, initial)

    session.on_output = record_output
    session.on_results = record_results
    session.turn_done = turn_done
    session.add_context_provider(tool_changes)
    for provider in providers:
        session.add_context_provider(provider)
    for hint in hints:
        session.add_hint(hint)
    session.add_hint(lambda: f"现在是 {_screen.clock()}。")
    session.should_stop = lambda: turn.cancelled
    session.chat(recall_func=_chat_root.get_handler(session), description_cache=_chat_root.description_cache)
    save()
    return session


# WHY: 维护者 2026-10-23 起初参照 Claude Code 的 retro（会话复盘），区分环境故障与自身认识（起因是当天
# 她调 attach_image 遇到 #image off 的 bug，误记成"这只眼睛不支持看图"）；2026-10-26 维护者进一步明确：
# 她不是代码项目，反思的目标是让她成为活生生的有感情的个体——校准自我认识（优先减去自我审查的死规矩）、
# 给常驻记忆做新陈代谢（把实操手册从 global 迁去按需文件）、留住对人和事的真实温度；真遇到环境故障仍写进
# proposals 而不记成自己的缺陷。
_REFLECT_PROMPT_PATH = Path("data") / "prompts" / "reflect.md"


def _reflect_prompt() -> str:
    """The nightly prompt lives in a file, not here.

    WHY: 2026-10-26 维护者：提示词跟代码分开放，改措辞不该动 Python。
    """
    return _REFLECT_PROMPT_PATH.read_text(encoding="utf-8")



def _tail(text: str, lines: int) -> str:
    return "\n".join(text.strip().split("\n")[-lines:]) if text.strip() else ""


def reflect(model: str | None = None) -> bool:
    """The nightly pass: hand off the day's segment, roll up diaries, then look back.  False when busy.

    WHY: 维护者 2026-10-22 / 2026-10-26：夜里先交接封存当天的最后一段（只收拢 recent、人和群笔记、
    todo 与日记，不动 global），再在独立的反思段里回看原始记录（归档的上下文、聊天记录、行动记录、
    笔记历史），体会今天哪一刻最像自己、给常驻记忆做新陈代谢。
    占住 AGENT_WINDOW，期间来的召唤照常登记，结束后由 pick_up_pending 接上。
    """
    # 她开着循环时几乎一直在忙，先关掉，这一轮做完反思就接得上（awaker 下一分钟再来）。
    stop_loop("夜里整理")
    turn, owner = context.begin_turn(_chat_root.AGENT_WINDOW)
    if not owner:
        return False
    _begin(turn, None, "反思")
    data = storage.get("", "agent")
    last = data.get("reflected_at")
    image_ledger = image.begin_conversation()
    try:
        context.set_current(None)
        context.set_agent_mode(True)
        try:
            _handoff(turn, "夜里了", model)
            _roll_up(diary_day(turn.log["start"]))
            segments = _segment.archived(last if isinstance(last, (int, float)) else 0)
            # 收起时另存的片段和潜意识自己的那条不算她的段：片段在她的段里有记号指着。
            main_segments = [item for item in segments
                             if not any(f"-{kind}" in item["name"] for kind in ("reflect", "folded", "subconscious"))]
            # 容灾兜底：正常情况下片段在主段里有记号指着。若因崩溃或意外没生成主段、或有片段未被
            # 任何现存主段收纳（孤儿段），也补进反思列表，避免当天的经历彻底失忆。
            referenced_folds = set()
            for main in main_segments:
                seg_data = _segment.read_archived(main["name"])
                if seg_data and isinstance(seg_data.get("messages"), list):
                    for msg in seg_data["messages"]:
                        text = str(msg.get("content") or "")
                        if "-folded" in text:
                            for fold_name in re.findall(r"\b\d{8}-\d{6}-folded(?:-\d+)?\b", text):
                                referenced_folds.add(fold_name)
            current_seg = _segment.load()
            if current_seg and isinstance(current_seg.get("messages"), list):
                for msg in current_seg["messages"]:
                    text = str(msg.get("content") or "")
                    if "-folded" in text:
                        for fold_name in re.findall(r"\b\d{8}-\d{6}-folded(?:-\d+)?\b", text):
                            referenced_folds.add(fold_name)
            orphan_folds = [item for item in segments
                            if "-folded" in item["name"] and item["name"] not in referenced_folds]
            segments = sorted([*main_segments, *orphan_folds], key=lambda item: item.get("started", 0))
            if segments:
                _run_reflection(model, turn, segments)
        finally:
            context.set_agent_mode(False)
        data["reflected_at"] = turn.log["start"]
        storage.save()
    finally:
        image.end_conversation(image_ledger)
        context.end_turn(_chat_root.AGENT_WINDOW, turn)
    pick_up_pending()
    return True


def _roll_up(day: str) -> None:
    """On a month's or a year's last night, rewrite its summary from the diaries it covers."""
    following = time.localtime(time.mktime(time.strptime(day, "%Y-%m-%d")) + 36 * 3600)
    periods = []
    if following.tm_mday == 1:
        periods.append(day[:7])
        if following.tm_mon == 1:
            periods.append(day[:4])
    for period in periods:
        try:
            _roles.summarize(period)
        except Exception:
            _roles._stream.exception(f"汇总 {period} 失败")


def _run_reflection(model: str | None, turn, segments: list[dict]) -> None:
    _max_events, threshold = _chat_root.limit()
    notes = [text for scope in ("global", "recent") if (text := _notes.render(scope))]
    listing = "\n".join(
        f"{item['name']}  {_screen.clock(item['started'])[5:16]} 至 {_screen.clock(item['ended'])[5:16]}  "
        f"{item['messages']} 条消息  窗口 {'、'.join(item['windows']) or '无'}"
        + (f"  {item['reason']}" if item.get("reason") else "") for item in segments)
    changes = _tail(_notes.read_policy("changes"), 40)
    proposals = _tail(_notes.read_policy("proposals"), 20)
    messages = [
        {"role": "system", "content": "以下是你的笔记：\n\n" + ("\n\n".join(notes) or "（还没有）")},
        {"role": "system", "content": "## 上次反思以来归档的上下文（用 read_context 读原样）\n" + listing},
        {"role": "system", "content": "## 策略改动记录（changes，最近的）\n" + (changes or "（还没有）")
         + "\n\n## 给维护者的提案（proposals，最近的）\n" + (proposals or "（还没有）")},
        {"role": "system", "content": _reflect_prompt().format(
            now=_screen.clock(), years_rows=len(_notes.years(9999)))},
    ]
    reflection = _segment.fresh()
    # 反思材料本身可能就很大：只限制它之后长出来的部分。
    session = _run_session(model, turn, reflection, messages, lambda: None,
                           lambda used, initial: used - initial > threshold,
                           load=("memory_system",), quiet=True, persist=False)
    # 反思这一段也留下来，排查"她昨晚为什么改了这个"时要看。
    reflection["messages"] = _body(session)
    _segment.archive(reflection, "reflect")


def pick_up_pending() -> None:
    """Start a wake for calls that arrived while nobody was there to take them."""
    if not oplog.has_unnotified():
        return
    window = next(iter(oplog.unacknowledged_windows()), None)
    if window is None:
        window = next((window for window, _count, active in oplog.pending_summary()
                       if active and _windows.known(window)), None)
    if window is not None:
        _drive_agent(None, window)
