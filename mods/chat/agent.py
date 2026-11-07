"""One central reader: attention supplies work; each wake is constructed, then acted.

A wake claims its summons, runs one construct run that decides what each act
sees (``construct.build``), then runs every spec as its own act with a fresh
view. When nothing waits, due windows are settled (``settle``). No context
outlives its run: there is no handoff, pressure, fold or dismiss any more.

WHY: 2026-11-01 维护者推翻 D20（不交接就一直是同一个上下文）、同意无状态设计：每次调用
只有一个目的，上下文由上下文构造（docs/working/proposals/one-purpose-stateless-core.md）。
交接写记忆的那条路由沉淀接替，沉淀按日志编号拿到她亲历时看到的同一批材料。
"""
from __future__ import annotations

from pathlib import Path
import threading
import time

from mods import context, history, identity, shell, storage
import mods.chat as _chat_root
from . import attention, construct as _construct, journal, notes as _notes, roles as _roles, screen as _screen, segment as _segment, settle as _settle, windows as _windows
from .run import current_wake, run, write

# WHY: with no running context the act must see enough of the thread around a new
# message to know who is answering whom; the constructor adds more through log: refs.
_ACT_LEAD_IN = 10
# WHY: 针对 DeepSeek-V4.1 (0910/1017) 架构及强化学习本能设计的后置刹车补丁 (Post-History Directive)。
# 原版英文直接注入 hint（Depth 0），不进入上下文历史，刹住抢词推进、死磕细节、全知视角与过度说教。
# 2026-11-01 接到无状态中心时只给行动（main）：构造不对外说话，沉淀和反思写的是笔记。
_PACING_BRAKE = """[System Directive - Pacing, Epistemology & Anti-Fixation Rules]
1. PACING BRAKE (CRITICAL): Advance the timeline by ONLY ONE immediate reaction or micro-action per turn. STOP writing the exact moment a response, decision, or reaction from {{user}} is naturally required. NEVER resolve a scene, skip time, change locations, or narrate {{user}}'s actions, speech, or internal feelings.
2. STRICT POV & ANTI-OMNIPRESENCE: {{char}} ONLY knows what is explicitly spoken aloud or physically visible in the immediate shared space. {{char}} CANNOT read {{user}}'s mind, internal monologue, or past events where {{char}} was absent.
3. SENSORY & PROP ROTATION: Do NOT repeat any specific environmental detail, lighting description, beverage/food, clothing adjustment, or physical tic that already appeared in the last 2 messages. Introduce fresh sensory angles or focus purely on dialogue and action.
4. ANTI-MELODRAMA: Maintain {{char}}'s grounded baseline personality. Do not artificially escalate conflict, angst, or philosophical lecturing unless directly provoked in-scene."""
_lock = threading.RLock()
_active = None
_reader_running = False
_requested = False


def running_wake():
    with _lock:
        return _active


def cancel_wake(window):
    global _requested
    with _lock:
        wake = _active
        if wake is None or tuple(window) not in wake["associated_windows"]:
            return False
        wake["cancelled"] = True
        _requested = False
    attention.stop_loop("这一轮被叫停了")
    return True


def _new(kind="唤醒"):
    return {"cancelled": False, "associated_windows": set(), "claims": [], "deferred": [], "interlocutors": {},
            "log": {"kind": kind, "start": time.time(), "peak": 0, "acts": 0},
            "release": "", "boxes": storage.get("", "boxes")}


def notify():
    """Attention's callback only starts the sole reader; it does no model work."""
    global _reader_running, _requested
    with _lock:
        _requested = True
        if _reader_running or _active is not None:
            return
        _reader_running = True
    threading.Thread(target=_reader, name="chat-reader", daemon=True).start()


def kick():
    """Start the reader for idle work (settling) without marking new attention work.

    WHY: notify() sets _requested, which is exactly what tells a running settle to
    yield; a settle deadline must not preempt another settle that is already running.
    """
    global _reader_running
    with _lock:
        if _reader_running or _active is not None:
            return
        _reader_running = True
    threading.Thread(target=_reader, name="chat-reader", daemon=True).start()


def _reader():
    global _active, _reader_running
    try:
        _read_work()
    except BaseException:
        _roles._stream.exception("注意力 reader 退出，保留未确认工作")
        with _lock:
            wake, _active = _active, None
            _reader_running = False
        if wake is not None:
            attention.release(wake["claims"] + wake["deferred"])


def _settle_idle():
    """Settle one due window while nothing else waits; False when none is due."""
    global _active
    due = _settle.due()
    if not due:
        return False
    label = due[0]
    wake = _new("沉淀")
    try:
        wake["associated_windows"].add(_windows.parse(label))
    except ValueError:
        pass
    with _lock:
        if _requested:
            return True
        _active = wake
    try:
        _settle.settle(label, stop=lambda: _requested or wake["cancelled"])
    except Exception:
        _roles._stream.exception(f"沉淀 {label} 失败")
    finally:
        with _lock:
            _active = None
    return True


def _read_work():
    global _active, _reader_running, _requested
    while True:
        with _lock:
            _requested = False
            wake = _new()
            _active = wake
        claims = attention.claim()
        if not claims:
            with _lock:
                _active = None
                if _requested:
                    continue
            if _settle_idle():
                continue
            # WHY: a settle deadline that fired while this reader was still busy was
            # ignored by kick(); re-arm before leaving so that deadline is not lost.
            _settle.arm()
            # WHY: unregister and observe arriving notifications under the
            # same lock; an arrival can neither lose its owner nor its wake.
            with _lock:
                if _requested:
                    continue
                _reader_running = False
                return
        wake["claims"] = claims
        typing = threading.Event()
        origin = next((job.get("event") for job in claims if job.get("event")), None)
        context.set_current(origin)
        released = False
        try:
            _adopt(wake, claims)
            threading.Thread(target=_keep_typing, args=(wake, typing), daemon=True).start()
            _work(wake, model=origin.get("_chat_model") if origin else None)
            if wake["cancelled"]:
                attention.stop_loop("这一轮被叫停了")
            else:
                attention.next_loop(acts=wake["log"]["acts"])
        except BaseException:
            _roles._stream.exception("中心唤醒失败，保留未确认召唤")
            # release sets ready, but must not hot-retry the failed request.
            # WHY: release before a retry reader can claim, rather than waiting
            # for finally after notify() has already started it.
            attention.release(wake["claims"] + wake["deferred"])
            released = True
            with _lock:
                _active = None
                _reader_running = False
                retry = _requested
            if retry:
                notify()
            return
        finally:
            typing.set()
            if not released:
                attention.release(wake["claims"] + wake["deferred"])
            if origin is not None:
                window = history.window(origin)
                if window is not None and window[0] in _windows.QQ:
                    _chat_root._run_hint(window, wake)
            context.set_current(None)
        with _lock:
            _active = None
            if wake["cancelled"]:
                _reader_running = False
                retry = _requested
            else:
                retry = False
        if wake["cancelled"]:
            if retry:
                notify()
            return


def _brake_hint() -> str:
    return "" if _chat_root.get_brake() == "off" else _PACING_BRAKE


def _adopt(wake, claims):
    try:
        bot = identity.bot_id()
    except RuntimeError:
        bot = None
    for job in claims:
        window = tuple(job["window"])
        wake["associated_windows"].add(window)
        # send(wait=…) 在群里不写 who 时默认等这个人；只记叫她的人，不记之后路过的人。
        if isinstance(job.get("author"), int) and job["author"] != bot:
            wake["interlocutors"][window] = job["author"]
        if not job["record_ids"]:
            # Clock/command work must leave the same ordinary me record as an
            # external producer. Releasing a failed claim reuses its identity.
            record = "wake_" + job["id"]
            if record not in _screen.unread(_windows.ME):
                _chat_root.push(_windows.ME, job["kind"], job["reason"] or job["kind"],
                                record_id=record, wake="")
            wake["associated_windows"].add(_windows.ME)


def _keep_typing(wake, stop):
    # WHY: QQ only supports this for private peers. Renew every five seconds;
    # stopping renewal ends it, and a broken status endpoint cannot stop work.
    from mods import connect
    failed = False
    while not stop.is_set():
        try:
            for target in tuple(wake["associated_windows"]):
                if target[0] == "private":
                    result = connect.call_api("set_input_status", user_id=str(target[1]), event_type=1)
                    if result.get("retcode") not in (0, None) and not failed:
                        failed = True
                        _roles._stream.warning("设置正在输入失败")
        except Exception:
            if not failed:
                failed = True
                _roles._stream.exception("设置正在输入失败")
        stop.wait(5)


def drafts() -> str:
    """Unsent text in input boxes, for every act to see.

    WHY: drafts survive wakes and restarts (input boxes live in storage); with no
    running context the act would not otherwise know a draft is waiting to be sent.
    """
    boxes = [f"{name}：{box.get('text', '')}" for name, box in storage.get("", "boxes").items()
             if isinstance(box, dict) and box.get("text")]
    return ("输入框里还没发的字：\n" + "\n".join(boxes)) if boxes else ""


def _prepare(wake, view, windows, lead_in=_ACT_LEAD_IN):
    attention.flush(windows)
    late = attention.claim()
    # WHY: reflection is control work dispatched after this wake, not a screen
    # request that an unrelated assistant output can acknowledge.
    attention.release([job for job in late if job["kind"] == "reflect"])
    late = [job for job in late if job["kind"] != "reflect"]
    mine = [job for job in late if tuple(job["window"]) in windows]
    # Arrivals for other windows wait for the next wake and its own construction.
    wake["deferred"].extend(job for job in late if tuple(job["window"]) not in windows)
    if mine:
        wake["claims"].extend(mine)
        _adopt(wake, mine)
    view["stats"]["windows"] = sorted(_windows.label(window) for window in windows)
    info = _segment.derive(view)
    pending = [row["read_patch"] for row in info["screens"] if row.get("read_patch")]
    material, patches, parts = [], [], []
    count, _threshold = _chat_root.limit()
    for window in sorted(windows, key=_windows.label):
        label = _windows.label(window)
        part = _screen.prepare(window, shown=info["shown"].get(label, []), count=count, pending=pending,
                               lead_in=lead_in)
        patches.append(part["patch"])
        parts.append(part)
        if part["text"]:
            material.append({"kind": "screen",
                             "messages": [{"role": "user", "content": _chat_root._view.screen_content(part["text"])}],
                             "window": label, "shown": part["shown"], "read_patch": part["patch"],
                             "follow_ids": part["follow_ids"], "record_keys": part["record_keys"]})
    wake["request_patches"] = patches
    screens = "\n".join(part["text"] for part in parts if part["text"])
    present = set(info["notes"])
    hits = [scope for scope, _word in _notes.mentioned(screens) if scope not in present]
    wake["breadcrumbs"] = "画面提到的这些你有笔记，用 read_file 召回：" + "、".join(dict.fromkeys(hits)) if hits else ""
    return material


def _confirm(wake):
    patches = wake.get("request_patches", [])
    _screen.commit_read(patches)
    confirmed = {patch["window"]: set(patch["records"]) for patch in patches}
    acting = wake.get("acting", set())
    # WHY: 一步只做一件以后（设计文档第 18 节），没有消息号的召唤（闹钟、循环、命令）只能由
    # 负责它窗口或 me 的行动消掉；以前任何一次行动有输出都会把它们一并消掉，别的事就丢了。
    done = [job for job in wake["claims"]
            if (not job["record_ids"] and (tuple(job["window"]) in acting or _windows.ME in acting))
            or job["record_ids"] and set(job["record_ids"]) <= confirmed.get(_windows.label(job["window"]), set())]
    attention.ack(done)
    ids = {job["id"] for job in done}
    wake["claims"][:] = [job for job in wake["claims"] if job["id"] not in ids]


def _act(wake, spec, model=None):
    """Run one spec: its references, its note, and the screens of its windows."""
    windows = set(spec["windows"])
    wake["acting"] = windows
    items = list(spec["items"])
    extra = [text for text in (
        f"【构造提供的关注情境，供你判断】\n{spec['focus']}",
        f"【准备时的观察与联想，供参考】\n{spec['note']}" if spec.get("note") else "",
    ) if text]
    items += [{"role": "system", "content": text} for text in extra]
    wake["request_patches"] = []
    draft_ctx = None

    def prepare(state):
        nonlocal draft_ctx
        view = state["view"]
        material = []
        if draft_ctx != view["ctx"]:
            if state["segment_index"] == 0:
                view["stats"]["focus"] = spec["focus"]
            # WHY: input boxes are live state, not a snapshot restored from refs.
            # Read even an empty box once per segment; later rounds must not keep
            # appending the same draft or resurrect one that has been sent.
            text = drafts()
            draft_ctx = view["ctx"]
            if text:
                material.append({"kind": "system", "messages": [{"role": "system", "content": text}]})
        material.extend(_prepare(wake, view, windows, state["history"]))
        return material

    try:
        run(items, _construct.DEFAULT_ACT_GOAL, tools=("meta", *spec["tools"]), inherit_tools=False,
            model=model, kind="main", state=wake, allow_reframe=True, history=spec["history"],
            prepare=prepare, on_output=_confirm,
            hints=[lambda: _screen.unread_hint(windows), lambda: shell.running_hint(),
                   lambda: wake.get("breadcrumbs", ""), _brake_hint])
    finally:
        if wake.get("output_count"):
            _settle.touch(windows)


def _work(wake, model=None):
    if any(job["kind"] == "reflect" for job in wake["claims"]):
        jobs = [job for job in wake["claims"] if job["kind"] == "reflect"]
        attention.release([job for job in wake["claims"] if job not in jobs])
        wake["claims"] = jobs
        _reflect(wake, model=model)
        attention.ack(jobs)
        wake["claims"] = []
        return
    for spec in _construct.build(wake, model):
        if wake["cancelled"]:
            return
        _act(wake, spec, model)


def adopt_last_view():
    """Freeze a migrated ``view.json`` once and ask her to catch up on it; returns its ctx.

    WHY: 2026-11-01 设计第 8 节：切换时把最后一段归档，并留一条补记提醒。迁移脚本把旧的
    当前段写成 view.json，那一段没有经过交接，里面可能有还没写进笔记的经历；无状态代码不再
    读 view.json，不封存它就既不在 views/ 里、也搜不到。
    """
    current = _segment.ROOT / "view.json"
    if not current.exists():
        return ""
    view = _segment.load()
    if not view["items"]:
        current.unlink(missing_ok=True)
        (_segment.ROOT / "view.bak.json").unlink(missing_ok=True)
        return ""
    view["stats"]["reason"] = "无状态切换前的最后一段（没有经过交接）"
    name = _segment.archive(view)
    _chat_root.push(_windows.ME, "系统",
                    f"切换到“每次一个目的”之前的最后一段上下文已经封存为 {name}。它没有经过交接，里面可能有还没写进笔记的事："
                    f"用 read_context(\"{name}\") 读一遍，把该留的补进人和群的笔记、日记和 todo，下次可能用得上的写成 recent 里的 @引用。",
                    wake="切换前的上下文要补记")
    return name


def diary_day(moment):
    return _settle.diary_day(moment)


def span(seconds):
    return f"{seconds/3600:g} 小时" if seconds >= 3600 else f"{seconds/60:g} 分钟" if seconds >= 60 else f"{seconds:g} 秒"


def start_loop(note):
    state = attention.start_loop(note)
    wake = current_wake()
    if wake:
        write(wake, _segment.append, wake["view"], "loop_note", note=note, round=state["round"],
              persist=wake.get("persist", False))
    return state


def stop_loop(reason="", rest=False):
    return attention.stop_loop(reason)


def loop_status():
    state = attention.loop_state()
    return f"loop: 第 {state['round']} 轮：{state['note']}" if state else "loop: off"


def loop_history(last=10):
    rows = journal.events(kind="loop_note")
    return "\n".join(f"第 {row.get('round', '?')} 轮 {_screen.clock(row['time'])}：{row.get('note', '')}" for row in rows[-max(1,min(50,int(last))):]) or "还没有改过循环备注"


pick_up_pending = notify


# WHY: 维护者 2026-10-23 起初参照 Claude Code 的 retro（会话复盘），区分环境故障与自身认识（起因是当天
# 她调 attach_image 遇到 #image off 的 bug，误记成"这只眼睛不支持看图"）；2026-10-26 维护者进一步明确：
# 她不是代码项目，反思的目标是让她成为活生生的有感情的个体——校准自我认识（优先减去自我审查的死规矩）、
# 给常驻记忆做新陈代谢（把实操手册从 global 迁去按需文件）、留住对人和事的真实温度；真遇到环境故障仍写进
# proposals 而不记成自己的缺陷。
_REFLECT_PROMPT_PATH = Path("data") / "prompts" / "reflect.md"
_REFLECT_DEFAULT = Path(__file__).with_name("reflect.md")


def _reflect_prompt(**values) -> str:
    """The nightly prompt lives in a file, not here; *values* fill its ``{now}``-style slots.

    WHY: 2026-10-26 维护者：提示词跟代码分开放，改措辞不该动 Python。

    WHY: 2026-10-29 审查：那个文件在 data/ 里，不进 git，新装的机器上没有它，反思就会抛
    FileNotFoundError；所以仓库里带一份默认（同目录的 reflect.md，照那天设备上那份，人名换成了中性的说法），
    文件不在或是空的才用。
    她自己会改这个文件，写个 JSON 例子就会冒出成单的花括号，str.format 会因此报错、整晚的
    反思跟着没了；这时退回只替换认得的那几个名字。
    """
    try:
        text = _REFLECT_PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    if not text.strip():
        _roles._stream.warning(f"{_REFLECT_PROMPT_PATH} 不在或是空的，反思用仓库里的默认说明")
        text = _REFLECT_DEFAULT.read_text(encoding="utf-8")
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        _roles._stream.warning(f"{_REFLECT_PROMPT_PATH} 里有 str.format 认不了的花括号，只替换 {sorted(values)}")
        for key, value in values.items():
            text = text.replace("{" + key + "}", str(value))
        return text



def _tail(text: str, lines: int) -> str:
    return "\n".join(text.strip().split("\n")[-lines:]) if text.strip() else ""



def reflect(model=None, settle=True):
    global _active
    with _lock:
        if _active is not None or _reader_running:
            return False
        wake = _new("反思")
        _active = wake
    try:
        _reflect(wake, model=model, settle=settle)
        return True
    finally:
        with _lock:
            _active = None
        notify()


def _reflect(wake, model=None, settle=True):
    attention.stop_loop("夜里整理")
    data = storage.get("agent", "config")
    if settle:
        # WHY: 2026-11-01 没有交接了；夜里先把白天还没沉淀的经历沉淀完，反思看到的笔记才是齐的。
        _settle.settle_all(model=model, stop=lambda: wake["cancelled"])
        _roll_up(diary_day(wake["log"]["start"]))
    last = data.get("reflected_at", 0)
    since = last if isinstance(last, (int, float)) else 0
    runs = [(name, view) for name, view in _segment.each_archived(since)
            if view["stats"].get("kind") in ("main", "construct", "settle")]
    if runs or not settle:
        counts = {kind: sum(view["stats"].get("kind") == kind for _name, view in runs) for kind in ("main", "construct", "settle")}
        settled = [f"{name}  {view['stats'].get('reason', '')}" for name, view in runs if view["stats"].get("kind") == "settle"]
        items = [{"role": "system", "content": "以下是你的笔记：\n" + "\n\n".join(_notes.render(scope) for scope in ("global", "recent"))},
                 {"role": "system", "content": f"上次反思以来：行动 {counts['main']} 次，构造 {counts['construct']} 次，沉淀 {counts['settle']} 次。\n"
                  "每段经历的沉淀（read_context 读原样，里面有那段聊天原文和你在那里的每次行动）：\n"
                  + ("\n".join(settled[-60:]) or "（没有）")
                  + "\n每次行动之前的构造运行也能用 read_context 按 ctx 读：它准备了什么、后来行动里又召回了什么，"
                  "可以看出构造漏带了哪类材料。构造的说明在 data/notes/policy/construct.md，沉淀的附加说明在 data/notes/policy/settle.md，都用 write_file 改。"},
                 {"role": "system", "content": "策略改动 changes（data/notes/policy/changes.md）：\n" + _tail(_notes.read_policy("changes"), 40)
                  + "\n给维护者的 proposals（data/notes/policy/proposals.md）：\n" + _tail(_notes.read_policy("proposals"), 20)}]
        state = {"associated_windows": wake["associated_windows"]}
        def after(current):
            current["end"] = current["used"] - current["initial"] > _chat_root.limit()[1]
        run(items, _reflect_prompt(now=_screen.clock(), years_rows=len(_notes.years(9999))),
            tools=("meta", "memory_system"), model=model, kind="reflect", state=state,
            stop=lambda: wake["cancelled"], after_round=after)
    if not wake["cancelled"]:
        data["reflected_at"] = wake["log"]["start"]
        storage.save()


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
