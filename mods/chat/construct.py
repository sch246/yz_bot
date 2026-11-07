"""Construction: before each act, a short run of her own decides what that act sees.

A wake brings new messages; the blackboard (the ``recent`` note) carries what an
earlier moment prepared for this one. The construct run reads both plus folded
indexes and submits one preparation with ``construct__assemble``: a focus,
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

import logging
import re
import time

from mods import storage, tools as tool_modules

from . import journal, notes as _notes, roles as _roles, screen as _screen, segment as _segment, windows as _windows

DEFAULT_CONFIG = {"on": True, "model": "deepseek/deepseek-flash", "effort": "none", "timeout": 45}
GOAL = "为接下来的自己提供信息、提醒和洞察；用 construct__assemble 提交一份上下文准备，提交后结束。"
DEFAULT_ACT_GOAL = "看看这次唤醒和新到的消息，自主决定做什么；不必为了被叫醒而说话。"
# WHY: 构造运行原本是 observer（没有任何工具）；现在只多给召回和提交规格。发言、写笔记、
# 跑代码都不在其中：它为别人准备上下文，自己不行动。这是新角色的白名单，不是给已有角色加禁令。
# WHY: reframe 可显式清空可选模块，load_tools 让她恢复 assemble/概览；加载仍经过
# 这份只读白名单，不能借此或通过 fork 取得写入与发言能力。
TOOLS = frozenset({"construct__assemble", "search", "read_file", "read_log", "read_actions", "agents__fork",
                   "memory_system__read_context", "reframe", "load_tools"})
# WHY: the construct run must understand the new message in its thread before choosing
# material; twenty earlier lines is enough to see who is answering whom.
LEAD_IN = 20
_REF_LINE = re.compile(r"^\s*@\s*(\S.*?)\s*$")

# WHY: 默认清单来自 observer 的检查清单（2026-10-27 起，2026-10-29 草籽修订）：清单里只放她真的
# 容易漏的。想加一条，先确认她确实会漏；她已经不会漏的就拿掉。它是旁观补盲，不是监工：不做代码
# 审查、操作审计或文风说教。设备上有 data/notes/policy/construct.md 时用那份，这里只是兜底。
# WHY: 2026-11-02 维护者明确构造只提供信息和洞察。接口职责独立于她可修改的
# checklist：旧设备策略可能还写着 goal/自动查引号，不能覆盖新的材料接口。
CONTRACT = """你正在为下一次行动准备上下文，提供信息、提醒和洞察。行动的你会结合这些材料自主判断。
从等着的事里选一件，focus 说明这次关注的情境或问题；note 写观察、依据和仍不确定的联系。
具体说什么、做什么由行动决定。交一份 construct__assemble 后，这次准备结束。

你可以选择：
- refs：每行一条，笔记名、日志编号、运行 ctx、log:窗口@时刻*条数、policy:名字。
  笔记可以选原文件行号，如 topic:线索@L12-28；read_file 可用同一写法或 start/end 预览，目录标字符数和总行数。
  整份与局部都可选；关系、心情和表达所需的材料也在选择范围内。
- tools：行动开场展开的工具/Skill 模块，空格或逗号分隔。meta 基础能力一直有，其余按这次需要选；
  加载会同时提供说明和函数，行动仍可自行 load_tools。工具目录中的 tool_development 是开发手册。
- history：当前窗口新消息之前的接话条数，默认 10，可为 0；更早的聊天用 log: 补。
  当前消息、运行期间同窗口的新消息由程序保证可见；第一次进入陌生窗口仍按历史上限补画面。
- windows：这份关注的窗口，至少包含一个等着的窗口；未选的留到下一次构造。

行动还会看到 self、身份与记忆说明、基础工具、输入框草稿、时间及运行状态提示。
todo 全文在这次准备中供你参考，行动只带你选进 refs 的全文或片段；没有另外一份末尾全文。
开场已包含 recent、todo 和等着你的新消息，不必调 read_file/read_log 重复读取眼前的材料；需要确认陌生指代时用 search/read_file 查，查到的材料写进 refs 供行动直接查看，避免行动重复查询。引号只表示引用，不触发自动检索。
以下策略是你的补充检查清单；提交接口和职责以本说明为准。
"""

DEFAULT_POLICY = """先看这次是谁、为什么叫醒你，以及 recent 中的心情、手头的事和留给此刻的引用。
准备材料时留意这些容易漏的地方；有发现再交给行动，没有发现就留空：
- 陌生说法、梗、外号或指代：查到的含义与出处，或尚未查清的部分。
- 矛盾：画面、旧经历和笔记哪里对不上；旧结论的时间和适用条件。
- 情绪：对方和自己的感受，有哪些关系经历能帮助理解；把猜测和原话分开。
- 意外：报错、碰壁、结果与预期的落差，目前知道什么、还缺什么。
清单用来补盲和发现联系，不逐项交作业。只留下这次有帮助的信息；表达方式和行动由接下来的自己决定。
"""


def get_config() -> dict:
    """Construct settings in storage ``agent.construct``; the old observer settings move here once."""
    data = storage.get("agent", "config")
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
    storage.get("agent", "config")["construct"] = config
    storage.save()


def policy() -> str:
    return CONTRACT + "\n" + (_notes.read_policy("construct").strip() or DEFAULT_POLICY)


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


# WHY: 删除 observer 的引号查词：普通原话也会带引号，文本命中不等于相关记忆。
# 构造已有 search/read_file 与目录，需要验证的联系由它显式查询（2026-11-02）。
def directory(most: int = 80) -> str:
    rows = _notes.listing(limit=10**6)
    # WHY: 选哪些仍按修改时间（最近动过的优先），显示按名字排：按修改时间显示时每写一次
    # 笔记顺序就变，整份目录连同后面的内容都进不了缓存（2026-11-01）。
    shown = sorted(rows[:most], key=lambda item: item["scope"])
    lines = [f"- {item['scope']}（{item['chars']} 字符，{item['lines']} 行）：{item['first']}" for item in shown]
    if len(rows) > most:
        lines.append(f"（还有 {len(rows) - most} 份不常动的没列出；用 search 或 read_file 找）")
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
        target = args.get('target', '?')
        text = str(args.get('text', '')).strip()
        if text:
            return f"发到 {target}：{text[:50]}"
        return f"发到 {target}"
    if name in ("write_file", "write_note"):
        return f"写 {args.get('path') or args.get('scope') or '?'}"
    if name in ("search", "read_file", "read_note", "read_log"):
        key = args.get("keyword") or args.get("path") or args.get("scope") or args.get("window") or ""
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
    now = time.time()
    reasons = "\n".join(f"{_windows.title(tuple(job['window']))} {job['kind']}：{job['reason']}"
                        + (f"（等了 {_waited(now - job['event']['time'])}）"
                           if isinstance((job.get("event") or {}).get("time"), (int, float)) else "")
                        for job in wake["claims"]) or "（没有记录原因）"
    screens = _screens(wake["associated_windows"], chat.limit()[0])
    mentioned = [scope for scope, _word in _notes.mentioned(screens)]
    crumbs = ("\n\n画面提到了这些有笔记的东西：" + "、".join(dict.fromkeys(mentioned))) if mentioned else ""
    # 接在本次消息末尾：这一条每次唤醒都不同，挂在这里不动前面的缓存前缀。
    try:
        close = _notes.similar(reasons + "\n\n" + screens, exclude=mentioned)
    except (OSError, ValueError):
        logging.getLogger(__name__).exception("similar notes failed")
        close = []
    if close:
        crumbs += ("\n\n和这些消息用词相近的笔记（程序按字面重合排的，不一定相关；要用就写进 refs）："
                   + "、".join(close))
    # WHY: 缓存按字节前缀命中；2026-11-01 黑板随沉淀改写，排在目录前使构造首轮只命中两成。
    # 目录现含笔记字数和行数，任何笔记改写也可能让它变；相对稳定的 todo 提到目录和黑板前，
    # 时间线和运行列表仍在后面。todo 自身改写仍会截断前缀，这是保留当前内容的取舍。
    return [
        {"role": "system", "content": policy()},
        {"role": "system", "content": chat.todo_material() or "（todo 是空的）"},
        {"role": "system", "content": "【笔记目录（折叠的，要用就写进 refs）】\n" + directory()},
        {"role": "system", "content": "【黑板 recent】\n" + board},
        {"role": "system", "content": "【最近（折叠的，要用就写进 refs）】\n"
                                      "最近你做过的事：\n" + timeline()
                                      + "\n\n最近几次运行：\n" + recent_runs()
                                      + "\n\n" + windows_status(wake["associated_windows"])},
        {"role": "user", "content": "【等着你的（这一步只选一件做，其余留到下一步）】\n" + reasons + "\n\n"
                                    + (screens or "（没有新消息）") + crumbs},
    ]


def _waited(seconds: float) -> str:
    minutes = int(max(seconds, 0) // 60)
    return f"{minutes // 60} 小时 {minutes % 60} 分" if minutes >= 60 else f"{minutes} 分钟" if minutes else "不到一分钟"


def _tier(ref: str) -> int:
    """Where a ref goes in an act's context: things that rarely change first.

    WHY: 2026-11-01 缓存按字节前缀命中。构造写 refs 的顺序随手，两次行动带了同样的
    global 和人物笔记，顺序不同也对不上前缀。笔记之间先后没有含义，按变化快慢排；同一档
    保持她写的顺序，日志、运行和聊天记录排最后且不打乱，它们的先后可能就是时间顺序。
    """
    ref = ref.split("@L", 1)[0]
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
    return {"focus": "这次唤醒和新到的消息", "items": items, "note": note, "windows": list(windows),
            "tools": [], "history": 10,
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
                 effort=None if effort == "default" else effort, prepare=prepare, allow_reframe=True)
    except TimeoutError:
        if wake["cancelled"]:
            return []
        _roles._stream.warning("构造运行超时，这次按黑板和消息直接行动")
        return [fallback(claimed, "构造运行超时")] if state["specs"] == [] else state["specs"][:1]
    except Exception as error:
        if wake["cancelled"]:
            return []
        _roles._stream.exception("构造运行出错，这次按黑板和消息直接行动")
        return [fallback(claimed, f"构造运行出错（{type(error).__name__}）")]
    if wake["cancelled"]:
        return []
    if not state["specs"]:
        return [fallback(claimed, "构造运行没有提交规格")]
    # WHY: 2026-11-01 维护者：每一步首先是“现在该干什么”，选先做哪件也是她的一个操作，
    # 而且是全局串行的（设计文档第 18 节）。没选中的召唤不补兜底规格，这一步结束时放回
    # 队列，读线程再起一次构造重新选；以前一次排完、按代码定的顺序跑到底。
    return state["specs"][:1]


def submit(state: dict, focus: str, refs: str, note: str, windows: str, tools: str = "", history: int = 10) -> str:
    """Validate one spec inside a construct run and keep it on the run state."""
    focus = str(focus).strip()
    if not focus:
        return "focus 不能空：说明这次关注的情境或问题"
    if isinstance(history, bool) or not isinstance(history, int) or history < 0:
        return "history 写非负整数：新消息之前需要几条接话"
    modules = list(dict.fromkeys(value for value in re.split(r"[\s,，、]+", str(tools)) if value))
    catalog = tool_modules.catalog()
    bad_tools = [name for name in modules if name not in catalog or name in {"baidumap", "dianping"}
                 or not tool_modules.bot_op_tool_visible(name, catalog[name])]
    if bad_tools:
        return "这些工具模块不可用：" + "、".join(bad_tools)
    labels = [value for value in re.split(r"[\s,，、]+", str(windows)) if value]
    try:
        targets = [_windows.parse(value) for value in labels]
    except ValueError as error:
        return f"windows 写错了：{error}"
    if state["specs"]:
        return "这一步已经交过一份了。只做一件事；其余的留在队里，做完会再叫你选"
    claimed = state.get("associated_windows") or set()
    if not targets:
        targets = list(claimed)
    if _windows.ME in claimed and _windows.ME not in targets:
        # 闹钟/定时等唤醒叫醒她去处理别的窗口时，me 顺带归入本次行动负责的范围消掉。
        targets.append(_windows.ME)
    if claimed and not claimed.intersection(targets):
        # 每一步都要处理掉一件等着的事，否则队列不会变短，会一直被叫起来重选同一批。
        claimed_str = "、".join(_windows.label(w) for w in claimed)
        return f"这份规格没有负责任何等着你的窗口（当前等着你的窗口有：{claimed_str}）；从等着的事里选一件，windows 写它的窗口"
    ref_lines = sorted((line.strip() for line in str(refs).splitlines() if line.strip()), key=_tier)
    items, bad = _resolve(ref_lines, state["view"])
    if bad:
        return "这些引用读不出来，改好再交一次（这份没有记下）：" + "、".join(bad)
    state["specs"].append({"focus": focus, "items": items, "note": str(note).strip(), "tools": modules, "history": history,
                           "windows": targets, "refs": ref_lines, "ctx": state["view"]["ctx"], "fallback": ""})
    state["end"] = True  # one spec per step: the construct run ends with this batch of calls
    return (f"已记下关注：{focus}（{len(items)} 条材料，负责 "
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
            lines.append(_spec_text(args.get("focus", args.get("goal", "")), str(args.get("refs", "")).splitlines(),
                                    args.get("note", ""), args.get("windows", ""),
                                    args.get("tools", "（旧规格未选择）"), args.get("history", 10)))
    return lines


def _spec_text(focus, refs, note, windows, tools="", history=10) -> str:
    return (f"- 关注：{focus}\n  窗口：{windows or '（默认）'}\n  材料：{'、'.join(r.strip() for r in refs if r.strip()) or '无'}"
            + f"\n  工具：{tools or 'meta'}；接话历史：{history} 条"
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
    opening = view
    origin = view["stats"].get("origin", {}).get("ctx")
    if origin and origin != view["ctx"]:
        # WHY: assemble returns the current ctx after reframe, but replay needs
        # the same wake's original input, not just the last segment's selection.
        opening = _segment.read_archived(origin)
        if opening is None or opening["stats"].get("kind") != "construct":
            raise ValueError(f"{name} 的构造开场 {origin} 不可读")
    messages = []
    for item in opening["items"]:
        row = journal.get(item)
        if row["kind"] == "goal":
            break
        if row["kind"] == "input":
            messages.extend(journal.clone(row["messages"]))
    if not messages:
        raise ValueError(f"{name} 没有可重放的输入")
    if policy_text is not None:
        messages[0] = {"role": "system", "content": CONTRACT + "\n" + (str(policy_text).strip() or DEFAULT_POLICY)}
    elif not str(messages[0].get("content", "")).startswith(CONTRACT):
        # Replay uses today's tool interface even when its evidence predates it.
        messages[0] = {"role": "system", "content": CONTRACT + "\n" + str(messages[0].get("content", ""))}
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
                          timeout=float(config.get("timeout", 45)), effort=None if effort == "default" else effort,
                          allow_reframe=True)
    before = _submitted(view) or ["（原来没有提交规格）"]
    after = [_spec_text(spec["focus"], spec["refs"], spec["note"], " ".join(_windows.label(w) for w in spec["windows"]),
                       " ".join(spec["tools"]), spec["history"])
             for spec in state["specs"]] or ["（重放没有提交规格）"]
    return f"原来（{name}）：\n" + "\n".join(before) + f"\n\n重放（{ctx}）：\n" + "\n".join(after)
