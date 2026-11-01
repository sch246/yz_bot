"""Construction: before each act, a short run of her own decides what that act sees.

A wake brings new messages; the blackboard (the ``recent`` note) carries what an
earlier moment prepared for this one. The construct run reads both plus folded
indexes and submits one or more specs with ``construct__assemble``: a goal,
references and a note to the act. The program only guarantees that the
summoning messages and their windows are visible, and expands references.

WHY: 2026-11-01 维护者裁定（docs/working/proposals/one-purpose-stateless-core.md）：
每次唤醒的上下文由上下文构造，不由预设程序拼装；每次调用只有一个目的。observer 并入
这里：同一个位置（act 之前）、同一套模型配置，产出从一段提醒变成规格，提醒放进规格的 note。
10-29 去掉 observer 的 load_notes，理由是挂上的笔记会一直占着那一段；每次 act 都是新
上下文以后，这条理由不再成立，所以构造可以按需带笔记。

WHY (kept from observer, 2026-10-29): a slow or hung model must never block the
wake; a timeout or failure falls back to blackboard + messages, and says so.
"""

from __future__ import annotations

import re
import time

from mods import storage

from . import journal, notes as _notes, roles as _roles, screen as _screen, segment as _segment, windows as _windows

DEFAULT_CONFIG = {"on": True, "model": "deepseek/deepseek-flash", "effort": "none", "timeout": 45}
GOAL = "为接下来的自己构造这次要看的上下文：用 construct__assemble 提交一份或几份规格，提交完就结束。"
DEFAULT_ACT_GOAL = "看看这次唤醒和新到的消息，自主决定做什么；不必为了被叫醒而说话。"
# WHY: 构造运行原本是 observer（没有任何工具）；现在只多给召回和提交规格。发言、写笔记、
# 跑代码都不在其中：它为别人准备上下文，自己不行动。这是新角色的白名单，不是给已有角色加禁令。
TOOLS = frozenset({"construct__assemble", "search", "read_note", "read_log", "read_actions", "agents__fork"})
# WHY: the construct run must understand the new message in its thread before choosing
# material; twenty earlier lines is enough to see who is answering whom.
LEAD_IN = 20
_WORD = re.compile(r"「([^「」\n]{1,20})」")
_REF_LINE = re.compile(r"^\s*@\s*(\S.*?)\s*$")

# WHY: 第 4 条来自 observer 的检查清单（2026-10-27 起，2026-10-29 草籽修订）：清单里只放她真的
# 容易漏的。想加一条，先确认她确实会漏；她已经不会漏的就拿掉。它是旁观补盲，不是监工：不做代码
# 审查、操作审计或文风说教。设备上有 data/notes/policy/construct.md 时用那份，这里只是兜底。
DEFAULT_POLICY = """你现在不回应任何人，只为接下来的自己准备这一次要看的上下文。
接下来真正行动的那个你，只看得到你用 construct__assemble 交过去的东西，加上程序保证的这次消息原文。

1. 先看清这次是谁、为什么叫醒你；黑板（recent）记着此刻的心情、手头的事和上一刻为此刻准备的引用。
2. 想清楚这次要做的是什么，写成 goal：一句具体的话，例如“接着回答某人关于某事的追问”“继续循环里在做的那件事”。
3. 选材料 refs，每行一个：
   - 笔记名：global、recent、todo、person:QQ号、group:群号、diary:日期、专题笔记名；
   - 日志编号（YYYYMMDD-N）或某次运行的 ctx：之前那次行动、那次的结果；
   - 某个窗口的一段聊天：log:窗口@时刻*条数，例如 log:g123@2026-10-30 21:00*40 是 21:00 之前的 40 条；
   - 策略：policy:名字。
   recent 一般都要带，它记着心情和手头的事；global 是你待人接物的直觉，多数时候也要带。
   拿不准某份材料有没有用，先用 read_note、search、read_log 看一眼再决定。找线索可以用 fork 分几路同时找。
   只带这件事需要的，不为了保险把东西都塞进去。
4. note 写给行动的你，没有就留空：
   - 陌生的说法：画面里你可能不认识的词（黑话、梗、外号、没交代来源的指代），用「」括起来，一个词一对，系统会去笔记和日记里查有没有；
   - 矛盾：画面和你之前说的、笔记里写的对不上，或者两条规矩打架；
   - 情绪：对方话里的情绪，你自己的情绪；
   - 意外：上一轮的报错、碰壁、结果和预期不一样。
   只说注意到的，不说教，不替行动的你决定怎么做。
5. 一次唤醒里有几件互不相干的事（比如两个窗口各有人找你），就调用几次 assemble，各交一份；同一件事交一份。
   windows 写这份规格负责回应的窗口（g群号、u对端号；闹钟、循环、命令写 me）。漏掉的被叫醒窗口，系统会补一份没经过构造的默认规格。
6. 提交完就结束。你在这里不发言、不写笔记。
"""


def get_config() -> dict:
    """Construct settings in storage ``agent.construct``; the old observer settings move here once."""
    data = storage.get("", "agent")
    config = data.get("construct")
    if not isinstance(config, dict):
        old = data.pop("observer", None)
        config = {key: old[key] for key in ("on", "model", "effort") if isinstance(old, dict) and key in old}
        data["construct"] = config
        storage.save()
    for key, value in DEFAULT_CONFIG.items():
        config.setdefault(key, value)
    return config


def save_config(config: dict) -> None:
    storage.get("", "agent")["construct"] = config
    storage.save()


def policy() -> str:
    return _notes.read_policy("construct").strip() or DEFAULT_POLICY


def blackboard() -> tuple[str, list[str]]:
    """The blackboard note's text and its ``@ref`` lines, in order."""
    try:
        text = _notes.read("recent")
    except ValueError:
        return "", []
    refs = [match[1] for match in map(_REF_LINE.match, text.splitlines()) if match]
    return text.strip(), list(dict.fromkeys(refs))


def fold(ref: str) -> str:
    """One line describing what a reference would expand to, without expanding it."""
    try:
        if journal._ID.fullmatch(ref):
            row = journal.get(ref)
            if row["kind"] == "goal":
                return f"{row.get('run_kind', '运行')}的目的：{str(row.get('goal', ''))[:60]}"
            text = " ".join(" ".join(part.get("text", "[图片]") for part in message["content"])
                            if isinstance(message.get("content"), list) else str(message.get("content") or "")
                            for message in row["messages"]).strip()
            calls = [call.get("function", {}).get("name", "?") for message in row["messages"]
                     for call in message.get("tool_calls") or []]
            return f"{row['kind']} {_screen.clock(row['time'])[5:16]}" + (f" 调用 {'、'.join(calls)}" if calls else "") + (f"：{text[:50]}" if text else "")
        if ref.startswith(("policy:", "log:")):
            return "策略文件" if ref.startswith("policy:") else "聊天记录区间"
        view = _segment.read_archived(ref)
        if view is not None:
            stats = view.get("stats", {})
            return f"{stats.get('kind') or '旧段'}运行，窗口 {'、'.join(stats.get('windows', [])) or '—'}"
        first = _notes.read(ref).strip().split("\n", 1)[0]
        return first[:60] if first else "（空笔记或不存在）"
    except (ValueError, KeyError, OSError):
        return "（认不出这条引用）"


def annotate(text: str) -> str:
    """After each 「word」, say which notes and diaries mention it, or that none do.

    WHY (observer, 2026-10-29): the constructor may not know what the notes hold;
    whether a word is in them is looked up by code, not guessed by the model.
    """
    words = list(dict.fromkeys(match.strip() for match in _WORD.findall(text) if match.strip()))
    if not words:
        return text
    found = _notes.where(words)
    done: set[str] = set()

    def mark(match: re.Match) -> str:
        word = match[1].strip()
        if word not in found or word in done:
            return match[0]
        done.add(word)
        scopes = found[word]
        if not scopes:
            return f"{match[0]}（笔记和日记里都没有）"
        more = f" 等 {len(scopes)} 处" if len(scopes) > 2 else ""
        return f"{match[0]}（{'、'.join(scopes[:2])}{more} 里有）"

    return _WORD.sub(mark, text)


def directory(most: int = 80) -> str:
    rows = _notes.listing(limit=10**6)
    # WHY: 选哪些仍按修改时间（最近动过的优先），显示按名字排：按修改时间显示时每写一次
    # 笔记顺序就变，整份目录连同后面的内容都进不了缓存（2026-11-01）。
    shown = sorted(rows[:most], key=lambda item: item["scope"])
    lines = [f"- {item['scope']}：{item['first']}" for item in shown]
    if len(rows) > most:
        lines.append(f"（还有 {len(rows) - most} 份不常动的没列出；用 search 或 read_note 找）")
    return "\n".join(lines) or "（还没有笔记）"


def _call_line(call: dict) -> str:
    function = call.get("function", {})
    name = function.get("name", "?")
    try:
        import json

        args = json.loads(function.get("arguments") or "{}")
    except ValueError:
        args = {}
    if name == "type_word":
        return f"在 {args.get('target', '?')} 打字：{str(args.get('text', ''))[:50]}"
    if name == "send":
        return f"发到 {args.get('target', '?')}"
    if name == "write_note":
        return f"写 {args.get('scope', '?')}"
    if name in ("search", "read_note", "read_log"):
        key = args.get("keyword") or args.get("scope") or args.get("window") or ""
        return f"{name}({str(key)[:30]})"
    return name


def timeline(hours: float = 3, most: int = 40) -> str:
    """What she did lately across windows, one line per output, newest last, with ids."""
    since = time.time() - hours * 3600
    lines = []
    for row in journal.events(kind="assistant", after=since):
        if row.get("run_kind") not in ("main", "settle"):
            continue
        message = row["messages"][0]
        calls = [_call_line(call) for call in message.get("tool_calls") or []]
        said = str(message.get("content") or "").strip().replace("\n", " ")
        if not calls and not said:
            continue
        lines.append(f"{_screen.clock(row['time'])[11:16]} {row['id']} "
                     + ("；".join(calls) if calls else f"想：{said[:50]}"))
    return "\n".join(lines[-most:]) or f"（最近 {hours:g} 小时没有行动）"


def recent_runs(most: int = 8) -> str:
    rows = [row for row in journal.events(kind="goal", after=time.time() - 12 * 3600)
            if row.get("run_kind") in ("main", "settle", "reflect")]
    return "\n".join(f"{_screen.clock(row['time'])[5:16]} {row.get('run_kind')} {row['ctx']}：{str(row.get('goal', ''))[:60]}"
                     for row in rows[-most:]) or "（最近没有运行）"


def windows_status(skip=()) -> str:
    """Unread and @ counts of windows not on this wake, from the read state."""
    return _screen.unread_hint(set(skip)) or "（别的窗口没有未读）"


def _screens(windows, count) -> str:
    parts = []
    for window in sorted(windows, key=_windows.label):
        part = _screen.prepare(window, shown=[], count=count, lead_in=LEAD_IN)
        if part["text"]:
            parts.append(part["text"])
    return "\n\n".join(parts)


def material(wake: dict) -> list[dict]:
    """The construct run's input: policy, folded blackboard, indexes, and this wake."""
    import mods.chat as chat

    text, refs = blackboard()
    board = text or "（黑板是空的）"
    if refs:
        board += "\n\n引用展开前是这样：\n" + "\n".join(f"@{ref} —— {fold(ref)}" for ref in refs)
    reasons = "\n".join(f"{_windows.title(tuple(job['window']))} {job['kind']}：{job['reason']}"
                        for job in wake["claims"]) or "（没有记录原因）"
    screens = _screens(wake["associated_windows"], chat.limit()[0])
    mentioned = [scope for scope, _word in _notes.mentioned(screens)]
    crumbs = ("\n\n画面提到了这些有笔记的东西：" + "、".join(dict.fromkeys(mentioned))) if mentioned else ""
    # WHY: 2026-11-01 缓存按字节前缀命中，按变化快慢排：策略和目录几乎不变，黑板随沉淀改写，
    # 时间线和运行列表每次都变。以前黑板排在目录前面，构造首轮只命中两成。
    return [
        {"role": "system", "content": policy()},
        {"role": "system", "content": "【笔记目录（折叠的，要用就写进 refs）】\n" + directory()},
        {"role": "system", "content": "【黑板 recent】\n" + board},
        {"role": "system", "content": "【最近（折叠的，要用就写进 refs）】\n"
                                      "最近你做过的事：\n" + timeline()
                                      + "\n\n最近几次运行：\n" + recent_runs()
                                      + "\n\n" + windows_status(wake["associated_windows"])},
        {"role": "user", "content": "【这次唤醒】\n" + reasons + "\n\n" + (screens or "（没有新消息）") + crumbs},
    ]


def _tier(ref: str) -> int:
    """Where a ref goes in an act's context: things that rarely change first.

    WHY: 2026-11-01 缓存按字节前缀命中。构造写 refs 的顺序随手，两次行动带了同样的
    global 和人物笔记，顺序不同也对不上前缀。笔记之间先后没有含义，按变化快慢排；同一档
    保持她写的顺序，日志、运行和聊天记录排最后且不打乱，它们的先后可能就是时间顺序。
    """
    if ref == "global":
        return 0
    if (journal._ID.fullmatch(ref) or ref.startswith("log:") or re.fullmatch(r"#?\d+(\.\d+)?", ref)
            or re.match(r"\d{8}-\d{6}", ref)):  # a run's ctx
        return 4
    if ref in ("recent", "todo") or ref.startswith("diary:"):
        return 3
    if ref.startswith(("person:", "group:")):
        return 2
    return 1


def _resolve(refs, lookup) -> tuple[list[str], list[str]]:
    items, bad = [], []
    for ref in refs:
        try:
            for one in _segment.resolve_refs(lookup, [ref]):
                if one not in items:
                    items.append(one)
        except (ValueError, KeyError, FileNotFoundError):
            bad.append(ref)
    return items, bad


def fallback(windows, reason: str) -> dict:
    """Blackboard + the summoning messages, marked as not constructed."""
    text, refs = blackboard()
    items, bad = _resolve((["recent"] if text else []) + refs, _segment.fresh())
    note = f"（这次没有经过构造：{reason}。眼前只有黑板和这次的消息，缺什么就自己召回。）"
    if bad:
        note += "\n黑板里这些引用读不出来，记得改 recent：" + "、".join(bad)
    return {"goal": DEFAULT_ACT_GOAL, "items": items, "note": note, "windows": list(windows),
            "refs": refs, "ctx": "", "fallback": reason}


def build(wake: dict, model: str | None = None) -> list[dict]:
    """Run the construct step for this wake and return the act specs, falling back on failure."""
    import mods.chat as chat

    claimed = list(wake["associated_windows"])
    config = get_config()
    if not config.get("on", True):
        return [fallback(claimed, "构造运行关着")]
    state = {"associated_windows": set(claimed), "specs": [], "cancelled": False}
    effort = chat.normalize_reasoning_effort(config.get("effort", "none"))
    labels = sorted(_windows.label(window) for window in claimed)

    def prepare(current):
        current["view"]["stats"]["windows"] = labels
        return []

    try:
        chat.run(material(wake), GOAL, tools=("meta", "construct", "agents"), functions=TOOLS,
                 model=str(config.get("model") or "") or model, kind="construct", state=state,
                 stop=lambda: wake["cancelled"], timeout=float(config.get("timeout", 45)),
                 effort=None if effort == "default" else effort, prepare=prepare)
    except TimeoutError:
        _roles._stream.warning("构造运行超时，这次按黑板和消息直接行动")
        return [fallback(claimed, "构造运行超时")] if state["specs"] == [] else _cover(state["specs"], claimed, wake)
    except Exception as error:
        if wake["cancelled"]:
            return []
        _roles._stream.exception("构造运行出错，这次按黑板和消息直接行动")
        return [fallback(claimed, f"构造运行出错（{type(error).__name__}）")]
    if wake["cancelled"]:
        return []
    if not state["specs"]:
        return [fallback(claimed, "构造运行没有提交规格")]
    return _cover(state["specs"], claimed, wake)


def _cover(specs: list[dict], claimed: list, wake: dict) -> list[dict]:
    covered = {window for spec in specs for window in spec["windows"]}
    missing = [window for window in claimed if window not in covered]
    if missing:
        specs = [*specs, fallback(missing, "构造运行没有为这些窗口交规格")]
    # 被 @、戳一戳和私聊的先回应；其余保持构造运行提交的先后（sorted 是稳定的）。
    called = {tuple(job["window"]) for job in wake["claims"] if job["kind"] in ("mention", "private", "poke")}
    return sorted(specs, key=lambda spec: not called.intersection(spec["windows"]))


def submit(state: dict, goal: str, refs: str, note: str, windows: str) -> str:
    """Validate one spec inside a construct run and keep it on the run state."""
    goal = str(goal).strip()
    if not goal:
        return "goal 不能空：写这一份要做的具体的事"
    labels = [value for value in re.split(r"[\s,，、]+", str(windows)) if value]
    try:
        targets = [_windows.parse(value) for value in labels]
    except ValueError as error:
        return f"windows 写错了：{error}"
    claimed = state.get("associated_windows") or set()
    if not targets:
        targets = [window for window in claimed
                   if window not in {one for spec in state["specs"] for one in spec["windows"]}]
    ref_lines = sorted((line.strip() for line in str(refs).splitlines() if line.strip()), key=_tier)
    items, bad = _resolve(ref_lines, state["view"])
    if bad:
        return "这些引用读不出来，改好再交一次（这份没有记下）：" + "、".join(bad)
    state["specs"].append({"goal": goal, "items": items, "note": annotate(str(note).strip()),
                           "windows": targets, "refs": ref_lines, "ctx": state["view"]["ctx"], "fallback": ""})
    return (f"已记下第 {len(state['specs'])} 份：{goal}（{len(items)} 条材料，负责 "
            + ("、".join(_windows.label(window) for window in targets) or "无窗口") + "）")


def _submitted(view: dict) -> list[str]:
    """The assemble calls a frozen construct run made, as readable lines."""
    import json

    lines = []
    for item in view["items"]:
        row = journal.get(item)
        if row["kind"] != "assistant":
            continue
        for call in row["messages"][0].get("tool_calls") or []:
            function = call.get("function", {})
            if function.get("name") != "construct__assemble":
                continue
            try:
                args = json.loads(function.get("arguments") or "{}")
            except ValueError:
                args = {}
            lines.append(_spec_text(args.get("goal", ""), str(args.get("refs", "")).splitlines(),
                                    args.get("note", ""), args.get("windows", "")))
    return lines


def _spec_text(goal, refs, note, windows) -> str:
    return (f"- 目的：{goal}\n  窗口：{windows or '（默认）'}\n  材料：{'、'.join(r.strip() for r in refs if r.strip()) or '无'}"
            + (f"\n  注意：{note}" if str(note).strip() else ""))


def replay(name: str, policy_text: str | None = None, model: str | None = None) -> str:
    """Rerun a frozen construct run with another construct policy; compare the specs. No act runs.

    WHY: 2026-11-01 同相上下文的判据要求“编辑操作并重放会相应改变结果”。构造规则就是那个
    操作：拿过去某次唤醒的原样输入，换一版说明重跑构造，看准备出来的规格怎样变。重放不给
    fork（分身会带着 meta 去做事），也不跑 act，所以除了日志里多一次构造记录，不留副作用。
    """
    import mods.chat as chat

    view = _segment.read_archived(str(name).strip())
    if view is None or view["stats"].get("kind") != "construct":
        raise ValueError(f"{name} 不是一次封存的构造运行")
    messages = []
    for item in view["items"]:
        row = journal.get(item)
        if row["kind"] == "goal":
            break
        if row["kind"] == "input":
            messages.extend(journal.clone(row["messages"]))
    if not messages:
        raise ValueError(f"{name} 没有可重放的输入")
    if policy_text is not None:
        messages[0] = {"role": "system", "content": str(policy_text).strip() or DEFAULT_POLICY}
    windows = set()
    for label in view["stats"].get("windows", []):
        try:
            windows.add(_windows.parse(label))
        except ValueError:
            pass
    config = get_config()
    effort = chat.normalize_reasoning_effort(config.get("effort", "none"))
    state = {"associated_windows": windows, "specs": [], "cancelled": False}
    ctx, _text = chat.run(messages, GOAL, tools=("meta", "construct"), functions=TOOLS - {"agents__fork"},
                          model=model or str(config.get("model") or "") or None, kind="construct", state=state,
                          timeout=float(config.get("timeout", 45)), effort=None if effort == "default" else effort)
    before = _submitted(view) or ["（原来没有提交规格）"]
    after = [_spec_text(spec["goal"], spec["refs"], spec["note"], " ".join(_windows.label(w) for w in spec["windows"]))
             for spec in state["specs"]] or ["（重放没有提交规格）"]
    return f"原来（{name}）：\n" + "\n".join(before) + f"\n\n重放（{ctx}）：\n" + "\n".join(after)
