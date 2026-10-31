"""One central reader: attention supplies work, journal views supply memory.

WHY: 2026-10-22: without a handoff this remains the same context. Handoff
happens in that context, with everything she saw, rather than in a librarian
session that has less evidence. Notes are rewritten, making retries safe.
"""
from __future__ import annotations

from pathlib import Path
import threading
import time

from mods import context, history, py, shell, storage
import mods.chat as _chat_root
from . import attention, journal, notes as _notes, roles as _roles, screen as _screen, segment as _segment, windows as _windows
from .run import current_wake, run, write

DEFAULT_TIDY = '''# stage: assemble / round; used/grown/limit are token estimates.
# since_handoff is seconds; kind is the wake reason; hour is local time.
"上下文太长" if used > limit else None
'''
_KNOWN_PEOPLE = 20
_HANDOFFS_PER_WAKE = 3
_TIDY_GRACE = 2
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
    return {"cancelled": False, "associated_windows": set(), "claims": [],
            "log": {"kind": kind, "start": time.time(), "peak": 0, "acts": 0},
            "handoffs": 0, "release": "", "boxes": storage.get("", "boxes")}


def notify():
    """Attention's callback only starts the sole reader; it does no model work."""
    global _reader_running, _requested
    with _lock:
        _requested = True
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
            attention.release(wake["claims"])


def _read_work():
    global _active, _reader_running, _requested
    while True:
        with _lock:
            _requested = False
            wake = _new()
            _active = wake
        claims = attention.claim()
        if not claims:
            # WHY: unregister and observe arriving notifications under the
            # same lock; an arrival can neither lose its owner nor its wake.
            with _lock:
                _active = None
                if _requested:
                    continue
                _reader_running = False
                return
        wake["claims"] = claims
        typing = threading.Event()
        origin = next((job.get("event") for job in claims if job.get("event")), None)
        context.set_current(origin)
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
            attention.release(wake["claims"])
            with _lock:
                _active = None
                _reader_running = False
                retry = _requested
            if retry:
                notify()
            return
        finally:
            typing.set()
            attention.release(wake["claims"])
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


def _adopt(wake, claims):
    for job in claims:
        window = tuple(job["window"])
        wake["associated_windows"].add(window)
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


def _note(view, scope, title=""):
    text = _notes.render(scope)
    _segment.append(view, "note", messages=[{"role": "system", "content":
                    (title + "\n" if title else "") + (text or f"【笔记 {scope} 已清空】")}],
                    scope=scope, name=scope, version=_notes.note_hash(scope))


def _opening(view, windows):
    if any(journal.get(item).get("opening") for item in view["items"]):
        return
    path = _notes._policy_path("opening")
    if path.exists():
        for line in _notes.read_policy("opening").splitlines():
            ref = line.strip()
            if not ref or ref.startswith("//"):
                continue
            try:
                _segment.extend(view, _segment.resolve_refs(view, [ref]))
            except (ValueError, FileNotFoundError) as error:
                _segment.append(view, "system", messages=[{"role": "system", "content":
                    f"opening 引用 {ref!r} 无法载入：{error}；请改 policy/opening。"}])
    else:
        # WHY: seven existing diaries remain available even before today's
        # entry is written; today's version also refreshes after edits/midnight.
        today = _screen.clock()[:10]
        days = sorted(key for key in _notes.diaries() if len(key) == 10 and key <= today
                      and _notes.render(f"diary:{key}").strip())[-7:]
        scopes = ["global", "recent", *(f"diary:{day}" for day in days)]
        for scope in scopes:
            if _notes.render(scope):
                _note(view, scope, "以下是你的笔记：")
        years = _notes.years()
        if years:
            _segment.append(view, "system", messages=[{"role": "system", "content":
                "年鉴（用 read_context 读原样）：\n" + "\n".join(years)}])
        _segment.append(view, "system", messages=[{"role": "system", "content":
            "还能召回的东西（用 search、read_log、read_actions、read_note 读）：\n" + _roles.directory(windows)}])
    _segment.append(view, "system", opening=True)
    boxes = [f"{name}：{box.get('text', '')}" for name, box in storage.get("", "boxes").items()
             if isinstance(box, dict) and box.get("text")]
    if boxes:
        # WHY: drafts survive handoff; the old type_word return no longer does.
        _segment.append(view, "system", messages=[{"role": "system", "content":
            "交接前输入框里还没发的字：\n" + "\n".join(boxes)}])


def _refresh_notes(view):
    info = _segment.derive(view)
    for scope, source in info["notes"].items():
        if source["version"] != _notes.note_hash(scope):
            _note(view, scope, "【笔记更新】")
    today = "diary:" + _screen.clock()[:10]
    if today not in info["notes"] and _notes.render(today):
        _note(view, today, "【今天的日记】")


def _people(wake, material):
    view = wake["view"]
    scopes = [_notes.scope_of(window) for window in wake["associated_windows"] if window[0] in _windows.QQ]
    scopes += [f"person:{job['author']}" for job in wake["claims"] if str(job.get("author", "")).isdigit()]
    scopes += [f"person:{who}" for who in _screen.extract_qqs([row["messages"][0]["content"] for row in material])]
    known = set(_segment.derive(view)["notes"])
    new = [scope for scope in dict.fromkeys(scopes) if scope not in known and _notes.render(scope)]
    room = max(0, _KNOWN_PEOPLE - wake.get("people_count", 0))
    for scope in new[:room]:
        _note(view, scope, "这一段里第一次出现的人或群，你的笔记：")
    wake["people_count"] = wake.get("people_count", 0) + min(room, len(new))
    omitted = set(new[room:]) - wake.setdefault("omitted_people", set())
    if omitted:
        wake["omitted_people"].update(omitted)
        _segment.append(view, "system", messages=[{"role": "system", "content":
            f"人物/群笔记本次最多自动载入 20 份；另有 {len(omitted)} 份未载入："
            + "、".join(sorted(omitted)) + "。用 read_note(scope) 或 search 自己召回。"}])


def _prepare(wake):
    late = attention.claim()
    # WHY: reflection is control work dispatched after this wake, not a screen
    # request that an unrelated assistant output can acknowledge.
    attention.release([job for job in late if job["kind"] == "reflect"])
    late = [job for job in late if job["kind"] != "reflect"]
    if late:
        wake["claims"].extend(late)
        _adopt(wake, late)
    _refresh_notes(wake["view"])
    info = _segment.derive(wake["view"])
    pending = [row["read_patch"] for row in info["screens"] if row.get("read_patch")]
    material, patches = [], []
    count, _threshold = _chat_root.limit()
    for window in sorted(wake["associated_windows"], key=_windows.label):
        label = _windows.label(window)
        part = _screen.prepare(window, shown=info["shown"].get(label, []), count=count, pending=pending)
        patches.append(part["patch"])
        if part["text"]:
            material.append({"kind": "screen", "messages": [{"role": "user", "content": part["text"]}],
                             "window": label, "shown": part["shown"], "read_patch": part["patch"],
                             "follow_ids": part["follow_ids"]})
    wake["request_patches"] = patches
    _people(wake, material)
    screens = "\n".join(row["messages"][0]["content"] for row in material)
    present = set(_segment.derive(wake["view"])["notes"])
    hits = [scope for scope, _word in _notes.mentioned(screens) if scope not in present]
    wake["breadcrumbs"] = "画面提到的这些你有笔记，用 read_note 召回：" + "、".join(dict.fromkeys(hits)) if hits else ""
    return material


def _confirm(wake):
    patches = wake.get("request_patches", [])
    _screen.commit_read(patches)
    confirmed = {patch["window"]: set(patch["records"]) for patch in patches}
    done = [job for job in wake["claims"] if not job["record_ids"] or
            set(job["record_ids"]) <= confirmed.get(_windows.label(job["window"]), set())]
    attention.ack(done)
    ids = {job["id"] for job in done}
    wake["claims"][:] = [job for job in wake["claims"] if job["id"] not in ids]


def pressure(view, stage, wake=None):
    """One pressure policy for grace, growth hints, handoff and forced fallback."""
    body = _segment.render(view)
    used = _segment.context_size(view["stats"].get("tokens"), body)
    threshold = _chat_root.limit()[1]
    state = view["stats"].setdefault("pressure", {})
    failures = state.get("failures", 0)
    forced = f"交接连着失败了 {failures} 次" if failures >= 2 else (
        f"已经长到 {used} token，超过整理阈值的 2 倍" if used > threshold * 2 else "")
    initial = (wake or {}).get("initial", used)
    names = dict(py.loc)
    names.update(stage=stage, used=used, grown=used-initial, limit=threshold,
                 since_handoff=time.time()-view["started"], kind=(wake or {}).get("log", {}).get("kind", "唤醒"),
                 hour=time.localtime().tm_hour)
    code = _notes.read_policy("tidy").strip() or DEFAULT_TIDY
    try:
        result = py.eval_last(code, names)
    except Exception:
        _roles._stream.exception("交接条件求值失败，按默认判断")
        result = used > threshold
    reason = forced or (result if isinstance(result, str) else "交接条件成立" if result else "")
    hint = ""
    if used <= threshold:
        state["grace"] = 0
    elif reason and not forced and wake is not None and not wake.get("release"):
        if stage == "assemble":
            state["grace"] = state.get("grace", 0) + 1
        if state.get("grace", 0) <= _TIDY_GRACE:
            # WHY: over the threshold she gets two wakes to pick her own moment.
            hint, reason = f"这一段 {used} token，整理阈值 {threshold}；请用 tidy 交接，宽限后会自动交接。", ""
    settled = state.get("settled")
    if not isinstance(settled, int) or used < settled:
        state["settled"] = used
    elif used - settled >= 50_000:
        info = _segment.derive(view)
        older = info["wakes"][1:-1]
        if older:
            listing = []
            for item in older:
                size = _segment.text_tokens(_segment.raw_messages({"items": item["items"]}))
                listing.append(f"#{item['number']}（{item['at']}，约 {_segment.amount(size)} token）")
            heavy = []
            for step in info["steps"]:
                if not step["complete"]:
                    continue
                size = _segment.text_tokens(_segment.raw_messages({"items": step["items"]}))
                if size >= 10_000:
                    heavy.append(f"{step['id']} {','.join(step['tools'])} 约 {_segment.amount(size)} token")
            hint += "\n上下文又长了五万 token。已了结的旧唤醒可用 compress 收起：\n" + "\n".join(listing)
            if heavy:
                hint += "\n较大的已完成步骤（也可单独 compress）：\n" + "\n".join(heavy[:5])
            age = time.time() - _notes.mtime("recent") if _notes.mtime("recent") > 0 else 0
            hint += (f"\nrecent 已约 {int(age//3600)} 小时未更新；手头的事变了可用 write_note 刷新。" if age >= 7200
                     else "\n手头的事变了也可顺手 write_note('recent', ...) 刷新。")
            state["settled"], state["nudged_at"] = used, time.time()
    _segment.save(view)
    return {"used": used, "reason": reason, "forced": forced, "hint": hint}


def request_tidy(note):
    wake = current_wake()
    if wake is None:
        return False
    wake["release"], wake["end"] = note or "她自己要求交接", True
    return True


def _dismiss_args(wake):
    return dict(boxes=storage.get("", "boxes"), last_sent=wake["view"]["stats"].get("last_sent", 0))


def request_dismiss(since="", *, validate_only=False):
    wake = current_wake()
    if wake is None:
        return "这次运行不是主唤醒，不能清主上下文"
    wake_id = wake["wake_id"]
    if str(since).strip():
        text = str(since).strip()
        wakes = _segment.derive(wake["view"])["wakes"]
        found = [item for item in wakes if text in {item["id"], str(item["number"]), "#"+str(item["number"]), item["at"], item["at"].split(" ")[-1]}]
        if not found:
            return "没找到那次唤醒；用 #编号、日志号或完整唤醒时刻"
        wake_id = found[-1]["id"]
    try:
        _segment.validate_dismiss(wake["view"], wake_id, **_dismiss_args(wake))
    except ValueError as error:
        return str(error)
    if not validate_only:
        wake["dismiss"] = wake_id
        wake["end"] = True
    return ""


def _apply_dismiss(wake):
    if not wake.get("dismiss"):
        return ""
    try:
        removed = _segment.dismiss(wake["view"], wake["dismiss"], **_dismiss_args(wake))
    except ValueError as error:
        problem = f"dismiss 未生效：{error}。已经设好的等待仍保留。"
        _segment.append(wake["view"], "system", messages=[{"role": "system", "content": problem}])
        _roles._stream.warning(problem)
        return problem
    _screen.restore_read(removed["snapshot"], removed["windows"], patches=removed["patches"])
    return ""


def request_compress(first, last, summary):
    wake = current_wake()
    if wake is None:
        return "只有主唤醒能收起当前上下文"
    try:
        return "已收起：" + write(wake, _segment.fold, wake["view"], str(first), str(last), summary)
    except ValueError as error:
        return str(error)


def decompress(name=""):
    view = (running_wake() or {}).get("view") or _segment.load()
    folds = _segment.derive(view)["folds"]
    if not folds:
        return "（没有收起的片段）"
    if not name:
        return "\n".join(f"{index+1}: {row['id']} {row.get('summary', '')}" for index, row in enumerate(folds))
    key = folds[-1]["id"] if name in {"latest", "last"} else str(name)
    if key.isdigit() and 1 <= int(key) <= len(folds):
        key = folds[int(key)-1]["id"]
    try:
        write(running_wake() or {}, _segment.unfold, view, key)
        return "已展开 " + key
    except (ValueError, FileNotFoundError) as error:
        return str(error)


def diary_day(moment):
    return time.strftime("%Y-%m-%d", time.localtime(moment - 6 * 3600))


def _handoff(wake, reason, model=None):
    view = wake.get("view") or _segment.load()
    if not any(row.get("role") == "assistant" for row in _segment.raw_messages(view)):
        return False
    if wake["handoffs"] >= _HANDOFFS_PER_WAKE:
        wake["handoff_failed"] = True
        return False
    wake["handoffs"] += 1
    verdict = pressure(view, "handoff", wake)
    forced = verdict["forced"]
    if forced:
        view["stats"]["reason"] = f"未交接，强制归档（{forced}；本来要交接：{reason}）"
        name = _segment.archive(view)
        fresh = _segment.fresh()
        # WHY: durable warning survives failure of the first request in this view.
        _segment.append(fresh, "system", messages=[{"role": "system", "content":
            f"上一段 {name} 因 {forced} 未能交接就归档；用 read_context 读原样，补上 recent 等笔记。"}])
        wake["view"] = fresh
        return True
    state = {"associated_windows": wake["associated_windows"], "cancelled": False}
    def stop():
        return wake["cancelled"]
    def after(current):
        if current["used"] - current["initial"] > _chat_root.limit()[1]:
            current["end"] = True
    try:
        run([], _roles.handoff_prompt(reason, diary_day(time.time())), tools=("meta",),
            model=model, kind="handoff", view=view, state=state, stop=stop, after_round=after)
        final = state.get("last") or {}
        complete = state.get("output_count") and not final.get("tool_calls") and not wake["cancelled"]
    except Exception:
        _roles._stream.exception("交接出错，当前上下文保留")
        complete = False
    if not complete:
        if not wake["cancelled"]:
            view["stats"].setdefault("pressure", {})["failures"] = verdict.get("failures", view["stats"]["pressure"].get("failures", 0)) + 1
            _segment.save(view)
        wake["handoff_failed"] = True
        return False
    view["stats"]["reason"] = reason
    _segment.archive(view)
    wake["view"] = _segment.fresh()
    _segment.save(wake["view"])
    return True


def _work(wake, model=None):
    if any(job["kind"] == "reflect" for job in wake["claims"]):
        jobs = [job for job in wake["claims"] if job["kind"] == "reflect"]
        attention.release([job for job in wake["claims"] if job not in jobs])
        wake["claims"] = jobs
        _reflect(wake, model=model)
        attention.ack(jobs)
        wake["claims"] = []
        return
    wake["view"] = _segment.load()
    while not wake["cancelled"]:
        view = wake["view"]
        verdict = pressure(view, "assemble", wake)
        if verdict["reason"] and not wake.get("handoff_failed"):
            if _handoff(wake, verdict["reason"], model):
                continue
        _opening(view, list(wake["associated_windows"]))
        wake["wake_id"] = _segment.begin_wake(view, boxes={key: value.get("text", "") if isinstance(value, dict) else value for key, value in wake["boxes"].items()},
                                              read_snapshot=_screen.read_snapshot())
        number = journal.get(wake["wake_id"])["number"]
        lines = [f"{_windows.label(job['window'])} {job['kind']}：{job['reason']}" for job in wake["claims"]]
        _segment.append(view, "system", messages=[{"role": "system", "content": f"【唤醒 #{number}】\n" + "\n".join(lines)}])
        wake["pressure_hint"] = verdict["hint"]
        def after(state):
            next_ = pressure(view, "round", state)
            state["pressure_hint"] = next_["hint"]
            if next_["reason"]:
                state["release"], state["end"] = next_["reason"], True
        observer_hint = ""
        if _chat_root._observer.is_enabled():
            material = _prepare(wake)
            # The same sources will be added by run before its request.
            wake["prepared"] = material
            observer_hint = _chat_root._observer.observe(view, [message for row in material for message in row["messages"]], should_stop=lambda: wake["cancelled"])
        def prepare(state):
            return state.pop("prepared") if "prepared" in state else _prepare(state)
        run([], "看看这次唤醒和新到的消息，自主决定做什么；不必为了被叫醒而说话。",
            tools=("meta",), model=model, kind="main", view=view, state=wake,
            prepare=prepare, on_output=_confirm, after_round=after,
            hints=[lambda: _screen.unread_hint(wake["associated_windows"]), lambda: shell.running_hint(),
                   lambda: wake.get("pressure_hint", ""), lambda: wake.get("breadcrumbs", ""), lambda: observer_hint])
        if wake["cancelled"]:
            return
        # WHY: 2026-10-26: thinking once without any action needn't linger.
        if not wake.get("dismiss") and not wake["log"]["acts"]:
            try:
                _segment.validate_dismiss(view, wake["wake_id"], **_dismiss_args(wake))
                wake["dismiss"] = wake["wake_id"]
            except ValueError:
                pass
        _apply_dismiss(wake)
        if wake.get("release") and not wake.get("handoff_failed"):
            reason, wake["release"] = wake["release"], ""
            if _handoff(wake, reason, model):
                wake.pop("dismiss", None)
                wake.pop("initial", None)
                continue
        return


def span(seconds):
    return f"{seconds/3600:g} 小时" if seconds >= 3600 else f"{seconds/60:g} 分钟" if seconds >= 60 else f"{seconds:g} 秒"


def start_loop(note):
    state = attention.start_loop(note)
    wake = current_wake()
    if wake:
        write(wake, _segment.append, wake["view"], "loop_note", note=note, round=state["round"])
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



def reflect(model=None, handoff=True):
    global _active
    with _lock:
        if _active is not None or _reader_running:
            return False
        wake = _new("反思")
        _active = wake
    try:
        _reflect(wake, model=model, handoff=handoff)
        return True
    finally:
        with _lock:
            _active = None
        notify()


def _reflect(wake, model=None, handoff=True):
    attention.stop_loop("夜里整理")
    wake["view"] = _segment.load()
    data = storage.get("", "agent")
    if handoff:
        _handoff(wake, "夜里了", model)
        _roll_up(diary_day(wake["log"]["start"]))
    last = data.get("reflected_at", 0)
    archived = [(name, view) for name, view in _segment.each_archived(last if isinstance(last, (int,float)) else 0)
                if not view["stats"].get("kind")]
    if archived or not handoff:
        items = [{"role": "system", "content": "以下是你的笔记：\n" + "\n\n".join(_notes.render(scope) for scope in ("global", "recent"))},
                 {"role": "system", "content": "上次反思以来归档的上下文（read_context 可读原样）：\n" + "\n".join(name for name, _view in archived)
                  + "\ncurrent 是仍在使用的主上下文。"},
                 {"role": "system", "content": "策略改动 changes：\n" + _tail(_notes.read_policy("changes"),40)
                  + "\n给维护者的 proposals：\n" + _tail(_notes.read_policy("proposals"),20)}]
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
