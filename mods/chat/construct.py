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

from mods import storage, tools as tool_modules, usage

from . import journal, line as _line, notes as _notes, roles as _roles, screen as _screen, segment as _segment, windows as _windows

# WHY: 2026-11-07 维护者：提醒只要占了注意力就是偏向。相近笔记的候选（notes.similar）照算、
# 照记进 usage 的 recall 日志，但先不挂给构造看，等记录说明她自己翻开的笔记常在候选里再打开；
# 挂出来以后“被打开”会自己证明自己，就量不出它到底有没有用了。
SHOW_SIMILAR = False

# line/fold 只在 on 关着时起作用：持久线模式（line.py），单跳热连接 + 按需原地折叠沉淀。
DEFAULT_CONFIG = {"on": True, "line": False, "fold": False, "model": "deepseek/deepseek-flash", "effort": "none", "timeout": 45}
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
  超过 8000 字的整份笔记，行动只看到开头、最近的部分和中间的小标题；要中间的内容就按行号选。
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


def line_enabled(config: dict | None = None) -> bool:
    """Whether persistent line mode (``#construct line`` or alias ``fold``) is active."""
    cfg = get_config() if config is None else config
    return not cfg.get("on", True) and bool(cfg.get("line") or cfg.get("fold"))


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
    rows = _notes.ranked(_notes.listing(limit=10**6))
    # WHY: 选哪些按激活度（读得多、读得近的优先，2026-11-07；以前按修改时间），显示按名字排：
    # 按修改时间显示时每写一次笔记顺序就变，整份目录连同后面的内容都进不了缓存（2026-11-01）。
    shown = sorted(rows[:most], key=lambda item: item["scope"])
    lines = [f"- {item['scope']}（{item['chars']} 字符，{item['lines']} 行）：{item['first']}" for item in shown]
    if len(rows) > most:
        lines.append(f"（还有 {len(rows) - most} 份不常用的没列出；用 search 或 read_file 找）")
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
        msgs = row.get("messages") or []
        if not msgs or not isinstance(msgs[0], dict):
            continue
        message = msgs[0]
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
    query = reasons + "\n\n" + screens
    try:
        close = _notes.similar(query, exclude=mentioned)
    except (OSError, ValueError):
        logging.getLogger(__name__).exception("similar notes failed")
        close = []
    wake["recall"] = {"t": round(now, 3), "mentioned": set(mentioned), "similar": close,
                      "query": query[-_notes._SIMILAR_CHUNK:], "seen": set()}
    usage.log_recall({"t": round(now, 3), "event": "wake",
                      "windows": sorted(_windows.label(tuple(window)) for window in wake["associated_windows"]),
                      "breadcrumbs": list(dict.fromkeys(mentioned)), "similar": close})
    # 接在本次消息末尾：这一条每次唤醒都不同，挂在这里不动前面的缓存前缀。
    if close and SHOW_SIMILAR:
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


def opened(scope: str) -> None:
    """A note was strung into a run: count the use, and log whether this wake had pointed at it.

    Called where a note ref resolves into a run and where read_file reads a note. Only the
    first use of each note within one wake is logged, and only inside the construct run and
    the act that follows it; standing notes are always in front of her and are not logged.
    """
    try:
        _opened(scope)
    except Exception:
        # 只是记账：读笔记和展开引用不能因为它失败。
        logging.getLogger(__name__).exception("note use not recorded")


def _opened(scope: str) -> None:
    from .run import current_run

    scope = scope.split("@L", 1)[0].strip()
    _notes.used(scope)
    run = current_run()
    recall = run.get("recall") if run else None
    if not recall or scope in recall["seen"] or scope in _notes._STANDING:
        return
    recall["seen"].add(scope)
    view = run.get("view") or {}
    ranks = [ref.split("@L", 1)[0] for ref in recall["similar"]]
    row = {"t": round(time.time(), 3), "event": "open", "wake": recall["t"], "ctx": view.get("ctx"),
           "kind": (view.get("stats") or {}).get("kind"), "scope": scope,
           "breadcrumb": scope in recall["mentioned"],
           "similar_rank": ranks.index(scope) + 1 if scope in ranks else None}
    if not row["breadcrumb"] and not scope.startswith("diary:"):
        row["terms"] = _notes.shared_terms(recall["query"], scope)
    usage.log_recall(row)


def missed_since(since: float, most: int = 15) -> str:
    """For the night: notes she opened that the breadcrumbs had not pointed at, with the shared words.

    WHY: 2026-11-07 维护者：能保证“需要时一定被想起”的只有自己声明了触发词的笔记——面包屑
    每次都把所有名字和 `别名：` 逐个比一遍，所以可信，只是词写得少。词靠用来长：她翻开了一份
    面包屑没点到的笔记，就说明它可能少一个词。只在真实使用暴露缺口的地方补，不另起整理；
    加不加、加哪个由她在反思里定，程序不替她写别名。
    """
    from collections import Counter

    listed = {item["scope"] for item in _notes.listing(limit=10**6)}
    counts, words, where = Counter(), {}, {}
    for row in usage.read_recall(since):
        scope = row.get("scope")
        if row.get("event") != "open" or row.get("breadcrumb") or scope not in listed:
            continue
        counts[scope] += 1
        words.setdefault(scope, Counter()).update(row.get("terms") or [])
        if row.get("ctx"):
            where.setdefault(scope, []).append(row["ctx"])
    if not counts:
        return ""
    lines = []
    for scope, count in counts.most_common(most):
        line = f"- {scope}（{count} 次"
        if where.get(scope):
            line += "，最近在 " + "、".join(list(dict.fromkeys(reversed(where[scope])))[:2])
        line += "）"
        if words[scope]:
            line += "：" + "、".join(word for word, _n in words[scope].most_common(5))
        lines.append(line)
    if len(counts) > most:
        lines.append(f"（还有 {len(counts) - most} 份没列出）")
    return ("上次反思以来，这些笔记你翻开过，但当时画面里的词没有点到它们（面包屑只认笔记名和 `别名：` 行）。"
            "觉得以后该被自动点到的，可以在那份笔记里加一行 `别名：词1、词2`；一个词要是平时到处都会出现，"
            "加上反而会乱点，就别加。括号里是最近翻开它的运行（read_context 能看当时的情形）；冒号后面是"
            "当时消息里和这份笔记共有、别处少见的词，可能是名字，也可能只是笔记里引用的原话，只是参考：\n" + "\n".join(lines))


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
            # 长笔记只给开头和最近的部分（notes._brief）；沉淀、反思要全文，不走这里。
            for one in _segment.resolve_refs(lookup, [ref], brief=True):
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
        if line_enabled(config):
            return build_line_specs(wake)
        return [handoff_spec(wake)]
    wake["recall"] = None
    state = {"associated_windows": set(claimed), "specs": [], "cancelled": False}
    effort = chat.normalize_reasoning_effort(config.get("effort", "none"))
    labels = sorted(_windows.label(window) for window in claimed)

    def prepare(current):
        current["view"]["stats"]["windows"] = labels
        return []

    try:
        items = material(wake)
        state["recall"] = wake["recall"]
        chat.run(items, GOAL, tools=("meta", "construct", "agents"), functions=TOOLS,
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


# WHY: 维护者 2026-11-09：构造本质上是一种特殊的交接，只是写的人没干过活，所以她总在重复调查
# 已经查过的事（docs/working/proposals/river-banks-and-voice.md 6.10）。`#construct off` 时不再
# 单独跑一轮构造：每次行动结束由干活的自己写交接，下次醒来按窗口取最近一份加新消息直接行动。
# WHY: 交接就是普通笔记 handoff:<窗口>，唯一特殊的是醒来时自动带上这个窗口的那份（维护者同日）：
# 她中途想看别的窗口的交接，read_file 就行，人也能直接看和改。
HANDOFF_NOTE_MAX = 800
HANDOFF_PROMPT = """这次行动到此结束，给下次醒来的自己写交接：调用 handoff，写完这次就结束了。
下次的你只看得到这份交接和新消息，没写下来的就没了。这次碰了几个窗口的事，就并排调用几次，window 写窗口名；留空写给这次负责的窗口。
- focus：现在在做什么事，想要什么结果。
- note：刚才做了什么、试过什么不行、弄清了什么（结论、数字、名字、编号）；还有什么没闭环。
- refs：下次要直接看的材料，每行一条，笔记名、笔记@L起行-末行、log:窗口@时刻*条数。
- tools：下次要用的工具模块。"""
_HANDOFF_TOOLS = re.compile(r"^\s*工具[:：]\s*(.*?)\s*$")
_HANDOFF_RUN = re.compile(r"^\s*运行[:：]\s*(\S+)\s*$")


AUTO_COMPACT_PROMPT = """当前这条线的上下文已超过整理水位（tidy_token）。程序已记下本轮做过的事：
{done}
请调用一次 line__compact 把已完成的探索过程原地折叠沉淀（写清 summary；未办完的事省略 future 即自动保留原目标，改目标填新 future，只有彻底办结才显式传 close=True；有长期结论写进 past_updates），写完本轮结束。"""

PROCESS_COMPACT_PROMPT = """当前这条线里累计有 {acts} 轮未收起的工作工具过程（约 {kb}k 字，涉及 ctx：{ctxs}）。
判断一下这些事是否大致告一段落：
- 如果还没做完、刚才查出来的原文接下来马上还要接着用：不要调工具，直接简短回一句跳过即可，原文会原样留着；
- 如果大致做完了，以后只需要知道做过这件事和结论：调用一次 line__compact(summary="用你自己的第一人称话记下做过什么、结论或感受", scope="process")。
这样只收起冗长的工具过程并保留 read_context 召回指针，前面的历史、聊天画面和你说过的话都不会动（若有跨时间长期认知可顺手写进 past_updates）。"""

LINE_TRIAGE_GOAL = (
    "作为总控线（line:main）快速分诊外部新到达：先看请求末尾【全局关注目录关联召回】，"
    "已有同主题线时优先用 line__send(..., window=...) 续接，确属独立新事项再用 line__fork(..., window=...) 派生专注线；"
    "仅一句话寒暄或无需开线的简单回应才直接开口回复（勿调 line__send 投给自己），不要在 line:main 原地跑多步重型排查。"
)

LINE_FOCUS_GOAL = (
    "沿着当前专注线推进未闭环目标或回应来信：若对方追问已偏离本线 future 改聊无关新事，用 line__hand_back 退回 line:main；"
    "想单独立/改未闭环目标而不折叠对话时调 line__future；当一段探索告一段落需压缩上下文时调 line__compact（不传 future 默认保留原目标，只有事项彻底办结才显式传 close=True 释放窗口）；"
    "平时正常回复或行动后直接结束，没调 compact 绝不会丢 future。"
)


def done_lines(rows) -> list[str]:
    """What the program saw her do in these rows, one line per call, closing handoffs excluded."""
    lines = []
    for row in rows:
        if row.get("kind") != "assistant":
            continue
        msgs = row.get("messages") or []
        if not msgs or not isinstance(msgs[0], dict):
            continue
        for call in msgs[0].get("tool_calls") or []:
            if call.get("function", {}).get("name") != "handoff":
                lines.append(f"{_screen.clock(row['time'])[11:16]} {_call_line(call)}")
    return lines


def handoff_scope(window: tuple) -> str:
    """The note holding one window's handoff; ``:`` in a window label is not allowed in a note name."""
    return "handoff:" + _windows.label(window).replace(":", "-")


def read_handoff(window: tuple) -> dict | None:
    """Parse a handoff note; only ``@ref``, ``工具：`` and ``运行：`` lines are special, so hand edits still work."""
    text = _notes.read(handoff_scope(window)).strip()
    if not text:
        return None
    refs, tools, run, body = [], [], "", []
    for line in text.splitlines():
        if match := _REF_LINE.match(line):
            refs.append(match[1])
        elif match := _HANDOFF_TOOLS.match(line):
            tools = [value for value in re.split(r"[\s,，、]+", match[1]) if value]
        elif match := _HANDOFF_RUN.match(line):
            run = match[1]
        else:
            body.append(line)
    focus = next((line.strip() for line in body if line.strip()), "")
    remaining = "\n".join(body).strip()[len(focus):].strip() if focus else ""
    return {"focus": focus, "note": remaining, "refs": list(dict.fromkeys(refs)), "tools": tools, "ctx": run}


def _usable_tools(names) -> list[str]:
    catalog = tool_modules.catalog()
    return [name for name in dict.fromkeys(names) if name in catalog and name not in {"baidumap", "dianping"}
            and tool_modules.bot_op_tool_visible(name, catalog[name])]


def closing(state: dict) -> str | None:
    """The closing request of an act when handoffs replace the construct run."""
    config = get_config()
    if config.get("on", True) or not state.get("acting"):
        return None
    if state.get("line"):
        import mods.chat as chat

        if state.get("compacted_in_run") or state.get("handed_back_window"):
            return None
        view = state["view"]
        # §8.1: 硬水位（tidy_token）触发整线原地整理。
        # WHY: 无工具调用的纯回复轮不经过 on_results/after_round，这里同时兜底检查 state["used"]，
        # 避免超水位后只开口回话的轮次一直漏掉自动折叠；hand_back 交还轮不触发本线折叠。
        over_limit = state.get("auto_compact_now") or state.get("used", 0) > chat.limit()[1]
        if over_limit:
            state["closing_mode"] = "hard"
            own = [row for row in _segment._entries(view) if row["ctx"] == view["ctx"]]
            return AUTO_COMPACT_PROMPT.format(done="\n".join(done_lines(own)) or "（没有调用工具）")
        # WHY: 2026-11-19：按累计未收起的工作工具过程水位（PROCESS_FOLD_CHARS）趁热缓存询问局部收束，
        # 既兜住“上轮重活还没完先跳过、下轮轻活才收尾”的场景，又不打扰纯闲聊和主线瞬时分诊委派。
        if not state.get("delegated_windows") and state.get("line_name"):
            proc = _line.pending_process(state["line_name"], view.get("items"))
            if proc["acts"] >= 1 and proc["chars"] >= _line.PROCESS_FOLD_CHARS:
                state["closing_mode"] = "process"
                ctx_list = "、".join(proc["ctxs"][-4:]) or view.get("ctx", "")
                return PROCESS_COMPACT_PROMPT.format(
                    acts=proc["acts"],
                    kb=f"{proc['chars'] / 1000:.1f}",
                    ctxs=ctx_list,
                )
        return None
    return HANDOFF_PROMPT


def _store_handoff(state: dict, targets, focus: str, note: str, refs: list[str], tools: list[str]) -> None:
    note = note.strip()
    if len(note) > HANDOFF_NOTE_MAX:
        note = note[:HANDOFF_NOTE_MAX] + "……（交接太长，后面截掉了）"
    lines = [focus.strip(), "", note, "", *(f"@{ref}" for ref in refs),
             *([f"工具：{' '.join(tools)}"] if tools else []), f"运行：{state['view']['ctx']}"]
    text = "\n".join(lines).replace("\n\n\n", "\n\n").strip() + "\n"
    state.setdefault("handoffs", []).append({"windows": [_windows.label(window) for window in targets],
                                             "focus": focus.strip(), "note": note, "refs": refs, "tools": tools})
    for window in targets:
        # WHY: 每次运行都整份覆写，走 write_file 的 .history 备份会按次数堆文件；旧版本在运行记录里都查得到。
        path = _notes._path(handoff_scope(window))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)


def record_handoff(state: dict, window: str, focus: str, note: str = "", refs: str = "", tools: str = "") -> str:
    """Write one window's handoff (default: the windows this act answers for)."""
    focus = str(focus).strip()
    if not focus:
        return "focus 不能空：写现在在做什么事"
    if str(window).strip():
        try:
            targets = [_windows.parse(str(window).strip())]
        except ValueError as error:
            return f"window 写错了：{error}"
    else:
        targets = list(state.get("acting") or ())
        if not targets:
            return "这次运行没有负责的窗口；用 window 写给哪个窗口"
    ref_lines = list(dict.fromkeys(line.strip().lstrip("@ ") for line in str(refs).splitlines() if line.strip()))
    usable = _usable_tools(value for value in re.split(r"[\s,，、]+", str(tools)) if value)
    _store_handoff(state, targets, focus, str(note), ref_lines, usable)
    if state.get("line") and state.get("line_name"):
        view = state.get("view")
        current_line = _line.normalize_name(state["line_name"])
        has_me = _windows.ME in targets
        ext_target = next((t for t in targets if t != _windows.ME), None)
        win_label = _windows.label(ext_target) if ext_target is not None else (_windows.label(targets[0]) if targets else "")
        owner_line = _line.claimed_line_for_window(ext_target) if ext_target is not None else None
        if state.get("closing_now"):
            if isinstance(view, dict):
                folded_before = _line.flush_before_compact(
                    current_line, view, current_act=state.get("act"), opening=state.get("line_opening")
                )
                state.setdefault("compacted_ids", set()).update(folded_before)
            apply_to_self = (current_line == _line.MAIN and has_me) or (
                current_line != _line.MAIN and not has_me and owner_line in (None, current_line)
            )
            info = _line.compact(
                current_line,
                str(note).strip() or focus,
                future=focus if apply_to_self else None,
                close=False,
                window=win_label if (current_line != _line.MAIN and apply_to_self) else "",
                ctx=view.get("ctx", "") if isinstance(view, dict) else "",
            )
            state["compacted_in_run"] = True
            if apply_to_self:
                state["closed_in_run"] = bool(info.get("closed"))
            if not apply_to_self and owner_line and owner_line != current_line:
                _line.set_future(
                    owner_line,
                    focus,
                    close=False,
                    window=win_label,
                    note=str(note).strip(),
                    sync_handoff=False,
                )
        else:
            # WHY: 2026-11-13 柚子指出线模式里 handoff 顺手 flush+compact 会把未聊完的 present 原文折掉；
            # 普通轮次调 handoff 只轻量同步对应线的 future 与窗口认领，且主线写外部窗口交接绝不覆盖主线自身 future。
            sync_line = ""
            if has_me and ext_target is None:
                sync_line = _line.MAIN
            elif owner_line:
                sync_line = owner_line
            elif current_line != _line.MAIN and ext_target is not None:
                acting_set = set(state.get("acting") or ())
                cur_claimed = (_line.load(current_line) or {}).get("claimed_windows") or ()
                if not acting_set or ext_target in acting_set or win_label in cur_claimed:
                    sync_line = current_line
            if sync_line:
                info = _line.set_future(
                    sync_line,
                    focus,
                    close=False,
                    window=win_label if sync_line != _line.MAIN else "",
                    note=str(note).strip(),
                    sync_handoff=False,
                )
                if sync_line == current_line:
                    state["closed_in_run"] = bool(info.get("closed"))
    if state.get("closing_now"):
        state["handed_off"] = True
        state["end"] = True  # 同一批并排的调用都会跑完，这批之后收尾结束
        return "交接好了"
    return "交接记下了；这次行动继续"


def closing_done(state: dict) -> None:
    """A closing request that answered in words still leaves those words for next time."""
    if state.get("line") and state.get("closing_start_idx") is not None:
        view = state.get("view")
        if isinstance(view, dict):
            # WHY: 2026-11-19：线模式收尾询问属于元控制轮（无论模型选择局部收束还是跳过），
            # 不把收尾提示词和跳过/工具回执追加进 line["present"]，保持业务上下文和前缀干净（原文仍留 journal）。
            start_idx = int(state["closing_start_idx"])
            meta_ids = [item for item in (view.get("items") or [])[start_idx:] if isinstance(item, str)]
            if meta_ids:
                state.setdefault("compacted_ids", set()).update(meta_ids)
    if state.get("handed_off") or state.get("compacted_in_run"):
        return
    if state.get("line") and state.get("closing_mode") == "process":
        # 软水位局部过程收束允许模型显式跳过；不强行折掉未完成的原文。
        return
    last = state.get("last") or {}
    text = str(last.get("content") or "").strip()
    if state.get("line") and state.get("line_name"):
        if text:
            view = state.get("view")
            if isinstance(view, dict):
                folded_before = _line.flush_before_compact(
                    state["line_name"],
                    view,
                    current_act=None,
                    opening=state.get("line_opening"),
                    stop_before_idx=state.get("closing_start_idx"),
                )
                compacted = state.setdefault("compacted_ids", set())
                compacted.update(folded_before)
                if state.get("act"):
                    compacted.add(state["act"])
            # WHY: 2026-11-13 柚子指出自动收尾没调 compact 时若原线无 future，仅折进 past 会丢未办完的事；
            # 这里 close=False 绝不清空已有 future，若专注线原本尚无 future 则用首行保底立住 future。
            cur_line = _line.load(state["line_name"])
            fallback_future = None
            if (cur_line is None or not cur_line.get("future")) and _line.normalize_name(state["line_name"]) != _line.MAIN:
                first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:120]
                if first_line and not _line.is_placeholder_future(first_line):
                    fallback_future = first_line
            _line.compact(
                state["line_name"],
                text,
                future=fallback_future,
                close=False,
                ctx=view.get("ctx", "") if isinstance(view, dict) else "",
            )
            state["compacted_in_run"] = True
        return
    logging.getLogger(__name__).warning("行动收尾没有调用 handoff，%s", "存下它的话" if text else "这次没有交接")
    if text:
        _store_handoff(state, list(state["acting"]), "（上次没有写交接，这是它结束时说的话）", text, [], [])


def _targets(wake: dict) -> list[tuple]:
    claims = wake["claims"]
    primary = next((tuple(job["window"]) for job in claims if tuple(job["window"]) != _windows.ME), _windows.ME)
    return [primary] + ([_windows.ME] if primary != _windows.ME and _windows.ME in wake["associated_windows"] else [])


def handoff_spec(wake: dict) -> dict:
    """One step without a construct run: the oldest waiting window, its last handoff, an index."""
    targets = _targets(wake)
    primary = targets[0]
    record = read_handoff(primary)
    text, board_refs = blackboard()
    refs = list(dict.fromkeys((["recent"] if text else []) + board_refs + list((record or {}).get("refs") or [])))
    resolved, bad = _resolve(refs, _segment.fresh())
    own = {handoff_scope(window) for window in targets}
    others = [item for item in _notes.listing(limit=10**6) if item["scope"].startswith("handoff:") and item["scope"] not in own][:5]
    index = ("【笔记目录（要用就 read_file）】\n" + directory() + "\n\n【最近你做过的事】\n" + timeline(most=15)
             + ("\n\n【别的窗口的交接（要细看就 read_file）】\n" + "\n".join(
                 f"{item['scope']}（{time.strftime('%m-%d %H:%M', time.localtime(item['changed']))}）：{item['first']}"
                 for item in others) if others else ""))
    note = str((record or {}).get("note") or "")
    if record and record.get("ctx"):
        note = (note + "\n" if note else "") + f"（上次运行 {record['ctx']}，要看原始过程用 memory_system 的 read_context）"
    if bad:
        note = (note + "\n" if note else "") + "这些引用读不出来：" + "、".join(bad)
    return {"focus": str((record or {}).get("focus") or "这次唤醒和新到的消息"), "items": [{"role": "system", "content": index}, *resolved],
            "note": note, "windows": targets, "tools": _usable_tools((record or {}).get("tools") or []), "history": 10,
            "refs": refs, "ctx": "", "fallback": "",
            "labels": ("【上次交接：当时在做的事】", "【上次交接：进展与未闭环】") if record else ("【这次醒来】", "【提醒】")}


def _line_ephemeral_hint() -> str:
    """Ephemeral request-tail hint for line runs (Axiom A2: never persisted into ``present``)."""
    text, _refs = blackboard()
    parts = ["【笔记目录（要用就 read_file，仅尾部瞬态展示）】\n" + directory(most=50)]
    if text:
        parts.append("【黑板 recent】\n" + text)
    return "\n\n".join(parts)


def _seed_line_from_handoff(line_name: str, primary_window: tuple) -> list[str]:
    """Round-trip seed when switching ``#construct off -> #construct line`` so existing handoff notes carry over."""
    canonical = _line.normalize_name(line_name)
    seed_window = _windows.ME if canonical == _line.MAIN else primary_window
    seed_record = read_handoff(seed_window) if (canonical == _line.MAIN or seed_window != _windows.ME) else None
    if seed_record:
        existing = _line.load(canonical)
        if existing is None or (not existing["present"] and not existing["past"] and not existing["future"]):
            raw_focus = str(seed_record.get("focus") or "").strip()
            clean_focus = (
                ""
                if (_line.is_placeholder_future(raw_focus) or _line.is_closed_handoff_focus(raw_focus))
                else raw_focus
            )
            seeded = _line.ensure(canonical, future=clean_focus)
            if seed_record.get("note"):
                _line.merge_past_bounded(seeded, [{
                    "key": f"交接-{_windows.label(seed_window)}",
                    "text": str(seed_record["note"]).strip(),
                    "atoms": [],
                    "water": 0,
                    "peak_quote": "",
                    "updated": time.time(),
                }])
                _line.ensure_past_atom(seeded)
            if not seeded.get("future") and clean_focus:
                seeded["future"] = clean_focus
            _line.save(seeded)
    tool_record = read_handoff(primary_window) if primary_window != _windows.ME else seed_record
    return _usable_tools((tool_record or seed_record or {}).get("tools") or [])


def build_line_specs(wake: dict) -> list[dict]:
    """Dual-track inbound routing (`§6.2`): 1-hop hot path to focused lines vs batched cold triage on ``line:main``."""
    claims = list(wake.get("claims") or [])
    if not claims:
        return [line_spec(wake)]

    routed = []
    for job in claims:
        win = tuple(job["window"])
        ev = job.get("event")
        preview = str(job.get("reason") or "")[:120]
        target_line, route_kind = _line.route_arrival(win, ev, preview=preview)
        routed.append((job, target_line, route_kind))

    hot = [(job, target_line, route_kind) for job, target_line, route_kind in routed if route_kind != "cold"]
    if hot:
        chosen_line = hot[0][1]
        selected = [
            (job, route_kind) for job, target_line, route_kind in routed
            if target_line == chosen_line
            or (chosen_line == _line.MAIN and tuple(job["window"]) == _windows.ME and not job.get("record_ids"))
        ]
    else:
        chosen_line = _line.MAIN
        selected = [(job, route_kind) for job, _target_line, route_kind in routed]

    selected_ids = {job["id"] for job, _rk in selected}
    deferred = [job for job in claims if job["id"] not in selected_ids]
    if deferred:
        wake["deferred"].extend(deferred)
        wake["claims"] = [job for job in claims if job["id"] in selected_ids]

    targets = list(dict.fromkeys(tuple(job["window"]) for job, _rk in selected)) or [_windows.ME]
    external = [win for win in targets if win != _windows.ME]
    primary = external[0] if external else targets[0]
    extra_tools = _seed_line_from_handoff(chosen_line, primary)
    triage_only = chosen_line == _line.MAIN and bool(external)
    goal = (LINE_TRIAGE_GOAL if triage_only
            else LINE_FOCUS_GOAL if chosen_line != _line.MAIN
            else DEFAULT_ACT_GOAL)
    return [{
        "focus": "",
        "items": [],
        "note": "",
        "windows": targets,
        "history": 10,
        "refs": [],
        "ctx": "",
        "fallback": "",
        "tools": extra_tools,
        "line": True,
        "line_name": chosen_line,
        "triage_only": triage_only,
        "goal": goal,
        "ephemeral_hint": _line_ephemeral_hint,
    }]


def line_spec(wake: dict, line_name: str = _line.MAIN) -> dict:
    """Single-step spec for *line_name* without polluting ``items`` with volatile indexes."""
    targets = _targets(wake)
    external = [win for win in targets if win != _windows.ME]
    primary = external[0] if external else targets[0]
    extra_tools = _seed_line_from_handoff(line_name, primary)
    triage_only = line_name == _line.MAIN and bool(external)
    return {
        "focus": "",
        "items": [],
        "note": "",
        "windows": targets,
        "history": 10,
        "refs": [],
        "ctx": "",
        "fallback": "",
        "tools": extra_tools,
        "line": True,
        "line_name": line_name,
        "triage_only": triage_only,
        "goal": LINE_TRIAGE_GOAL if triage_only else (LINE_FOCUS_GOAL if line_name != _line.MAIN else DEFAULT_ACT_GOAL),
        "ephemeral_hint": _line_ephemeral_hint,
    }


def fold_text(view: dict, rows, state: dict) -> str:
    """The fold row that replaces one act's process on the line: what was done, then what she wrote next."""
    rows = list(rows)
    times = [row["time"] for row in rows] or [view["started"]]
    parts = [f"【折叠 {_screen.clock(min(times))[5:16]}–{_screen.clock(max(times))[11:16]}，"
             f"原过程用 read_context(\"{view['ctx']}\") 读】",
             "做过的（程序记录）：", *(done_lines(rows) or ["（没有调用工具）"])]
    handoffs = state.get("handoffs") or []
    for item in handoffs:
        parts += ["", f"接下来（写给 {'、'.join(item['windows'])}）：{item['focus']}"]
        if item["note"]:
            parts.append(item["note"])
        if item["refs"]:
            parts.append("要看的：" + "、".join(item["refs"]))
    if not handoffs:
        parts += ["", "（这次没有写接下来的事" + ("，行动被打断了）" if state.get("cancelled") else "）")]
    return "\n".join(parts)


def _submitted(view: dict) -> list[str]:
    """The assemble calls a frozen construct run made, as readable lines."""
    import json

    lines = []
    for item in view["items"]:
        row = journal.get(item)
        if row["kind"] != "assistant":
            continue
        msgs = row.get("messages") or []
        if not msgs or not isinstance(msgs[0], dict):
            continue
        for call in msgs[0].get("tool_calls") or []:
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
