"""管理持久认知线与单线记忆：分叉派生、跨线发信、轻量改标、超界交还、原地折叠沉淀、只读看截面与前缀探针。

- **何时分叉（fork）vs 发信（send）**：先看请求末尾关注目录召回——已有同主题线时优先 `send` 续接（先查后分）；确属独立新事项时再用 `fork` 零拷贝共享当前前缀拉出专注线。
- **何时改目标（future）vs 折叠沉淀（compact）**：日常对话无需每轮收尾，没调 `compact` 绝不会丢 `future` 或退订窗口。只想单独立/改未闭环目标而不折叠当前对话时调 `future`；当一段探索告一段落、试错得出结论需要压缩 `present` 时调 `compact`（省略 `future` 或传 `""`/`"none"` 一律安全保留原目标；**当且仅当显式传 `close=True` 时才会清空 `future` 并释放窗口认领**）。
- **何时超界交还（hand_back）**：专注线收到显式回复或续接消息，对照自身 `future` 发现对方已偏题聊起无关新事时，调 `hand_back` 退回 `line:main` 重新分派。
- **只读查看他线进展**：不打扰对方用 `inspect`（零模型调用看截面）或 `probe`（复用对方前缀缓存只读追问一次）。"""

from __future__ import annotations

import json
import re

from mods.tools import current_binding


def _active_line_and_state() -> tuple[str, dict]:
    try:
        state = current_binding().get("run") or {}
    except RuntimeError:
        state = {}
    from mods.chat import line as _line

    name = str(state.get("line_name") or _line.MAIN)
    return _line.normalize_name(name), state


def _default_window_label(state: dict, explicit: str) -> str:
    if str(explicit or "").strip():
        return str(explicit).strip()
    from mods.chat import windows as _windows

    for win in state.get("acting") or ():
        if tuple(win) != _windows.ME:
            return _windows.label(tuple(win))
    return ""


def _mark_delegated(state: dict, window_label: str, target_line: str) -> None:
    if not window_label:
        return
    from mods.chat import windows as _windows

    try:
        win_tuple = _windows.parse(window_label)
    except ValueError:
        return
    state.setdefault("delegated_windows", {})[win_tuple] = target_line


def _has_unacked_claim(state: dict, window_label: str) -> bool:
    if not window_label:
        return False
    from mods.chat import windows as _windows

    try:
        win_tuple = _windows.parse(window_label)
    except ValueError:
        return False
    return any(tuple(job["window"]) == win_tuple for job in (state.get("claims") or ()))


def fork(
    name: str,
    target: str,
    window: str = "",
    aliases: str = "",
    reuse: bool = True,
) -> str:
    """从当前线零拷贝共享前缀派生（或按名复用）一条专注线，立即返回新线句柄并排入唤醒调度。

    @param
    name: 可读线名，如 "line:fix-build"、"person:12345" 或简写 "fix-build"
    target: 初始目标与未闭环空缺描述（同时作为该线的动态订阅声明，不能为空）
    window: 该专注线负责接管的窗口标签（如 g<群号>、u<私聊号>）；留空时若主线正分诊某外部窗口则默认接管该窗口
    aliases: 供精确匹配与 BM25 召回的别名列表，用逗号、顿号或空格分隔
    reuse: 同名线已存在时是否直接续接更新目标（默认 True，遵循先查后分）
    """
    from mods.chat import line as _line

    parent_name, state = _active_line_and_state()
    win_label = _default_window_label(state, window) if parent_name == _line.MAIN else str(window or "").strip()
    alias_list = [x.strip() for x in re.split(r"[,，、;；\s]+", str(aliases or "")) if x.strip()]
    retargeted = bool(win_label and parent_name == _line.MAIN and _has_unacked_claim(state, win_label))
    child_id = _line.fork(
        parent_name,
        name,
        target,
        window=win_label,
        aliases=alias_list,
        reuse=reuse,
        wake=not retargeted,
    )
    if win_label and parent_name == _line.MAIN:
        _mark_delegated(state, win_label, child_id)
        if not state.get("keep_running"):
            state["end"] = True
    return f"已分派至线 {child_id}（目标：{target.strip()}）" + (f"，接管窗口 {win_label}" if win_label else "")


def send(
    recipient: str,
    content: str,
    window: str = "",
    wake: bool = True,
) -> str:
    """向一条既有线投递跨线消息、分派续接或回传结果（非阻塞单向投递，追加在接收方自身前缀末尾）。

    @param
    recipient: 接收方线名，如 "line:main"、"line:fix-build"、"person:12345"
    content: 要投递的消息正文、分派说明或执行结论
    window: 关联的外部窗口标签（可选；主线分诊转发外部消息时填对应窗口名以移交画面切片）
    wake: 是否唤醒或插话通知接收方（默认 True；仅做静默跨线事实沉积时可传 False）
    """
    from mods.chat import line as _line

    sender_name, state = _active_line_and_state()
    target_key = _line.normalize_name(recipient)
    win_label = _default_window_label(state, window) if (sender_name == _line.MAIN and target_key != _line.MAIN) else str(window or "").strip()
    retargeted = bool(win_label and sender_name == _line.MAIN and target_key != _line.MAIN and _has_unacked_claim(state, win_label))
    atom_id = _line.send(sender_name, target_key, content, window=win_label, wake=wake and not retargeted)
    if win_label and sender_name == _line.MAIN and target_key != _line.MAIN:
        _mark_delegated(state, win_label, target_key)
        if not state.get("keep_running"):
            state["end"] = True
    return f"已投递给 {target_key}（atom={atom_id}）" + (f"，移交窗口 {win_label}" if win_label else "")


def hand_back(reason: str, window: str = "") -> str:
    """当直达本专注线的追问已偏离本线 future 职责或切换了新话题时，释放窗口认领并退回 line:main 重新分诊。

    @param
    reason: 超界或退回的简短原因说明
    window: 要退回的窗口标签；留空默认退回当前负责的外部窗口
    """
    from mods.chat import line as _line

    line_name, state = _active_line_and_state()
    if line_name == _line.MAIN:
        return "当前已在 line:main，无需 hand_back；请直接用 line__fork 或 line__send 分派"
    win_label = _default_window_label(state, window)
    retargeted = _has_unacked_claim(state, win_label)
    atom_id = _line.hand_back(line_name, reason, window=win_label, wake=not retargeted)
    _mark_delegated(state, win_label, _line.MAIN)
    state["handed_back_window"] = True
    state["end"] = True
    return f"已将窗口 {win_label or '（当前认领）'} 退回 {_line.MAIN} 重分诊（atom={atom_id}）"


def future(
    future: str = "",
    close: bool = False,
    name: str = "",
    window: str = "",
    notify_source: bool = True,
) -> str:
    """在不折叠 present 原始对话的前提下，查看、单独立项或更新当前（或指定）线的 future 目标；仅在显式传 close=True 时办结归零并释放窗口认领。

    @param
    future: 新的未闭环目标/势差描述；留空且 close=False 时仅查看并原样保留现有 future（传 "none"/"" 不会误清空）
    close: 事项确已彻底办结、不再接该窗口后续时显式传 True（清空 future 并解除窗口认领）
    name: 目标线名；留空默认操作当前所在之线
    window: 需认领并同步 handoff:<窗口>.md 的窗口标签；留空默认取当前负责的外部窗口
    notify_source: 当 close=True 办结且本线有父源线时，是否向源线发送简短完结回传（默认 True）
    """
    from mods.chat import line as _line

    active_name, state = _active_line_and_state()
    target_name = _line.normalize_name(name) if str(name or "").strip() else active_name
    if not close and not str(future or "").strip():
        cur = _line.load(target_name)
        cur_future = str((cur or {}).get("future") or "")
        wins = "、".join((cur or {}).get("claimed_windows") or []) or "无"
        if cur_future:
            return (
                f"线 {target_name} 当前 future 仍保留为：「{cur_future}」（认领窗口：{wins}；"
                "未传新文本或 close=True，原样不动）"
            )
        return f"线 {target_name} 当前无未闭环 future（认领窗口：{wins}；传 future=\"...\" 立新目标）"

    win_label = (
        str(window or "").strip()
        if (target_name != active_name or target_name == _line.MAIN)
        else _default_window_label(state, window)
    )
    info = _line.set_future(
        target_name,
        future,
        close=close,
        window=win_label,
        notify_source=notify_source,
    )
    if target_name == active_name:
        state["closed_in_run"] = bool(info["closed"])
    guard_note = (
        "（未传 close=True，已安全保留原 future 不清空；若确已办结请传 close=True）"
        if info.get("ignored_placeholder")
        else ""
    )
    if info["closed"]:
        return f"已办结关闭线 {info['name']} 的 future（势差归零，已释放窗口认领）"
    return (
        f"已更新线 {info['name']} 的 future（未折叠 present，当前 future="
        + (f"「{info['future']}」" if info["future"] else "空")
        + f"，认领窗口={'、'.join(info['claimed_windows']) or '无'}）{guard_note}"
    )


def compact(
    summary: str = "",
    future: str | None = None,
    past_updates: str = "",
    water: int = 0,
    peak_quote: str = "",
    entity_sends: str = "",
    window: str = "",
    close: bool = False,
    keep_future: bool = False,
    notify_source: bool = True,
    scope: str = "all",
) -> str:
    """在当前线上原地执行折叠与记忆归约（Fold + Sediment + Retarget），并同步更新窗口 handoff 笔记。

    支持两种范围：``scope="process"`` 只收起最近未折叠的工具探索过程（保留前面的历史、聊天画面、来信和 ``send`` 发言，并自动附 ``read_context`` 召回指针）；``scope="all"``（默认）执行整线折叠与沉淀。省略 future 或误传空串 "" / "none" / "无" 时一律默认保留原 future 不清空；只有显式传 close=True 才会将 future 归零并解除窗口认领。

    @param
    summary: 刚做了什么、排除了什么、得到了什么结论或感受（折叠时必填；若留空且传了新 future 或 close=True，则仅更新 future 不折叠 present）
    future: 更新后的当前目标/未闭环空缺；省略则保留原 future 不变（传 "" 或 "none" 同样保留原值，不会误清空）
    past_updates: 需要长期沉入本线 past 的事实/印象，每行一条 "键：结论内容"；留空则仅折叠 present（整线超阈值自动沉淀）
    water: 情绪水位 0 到 3（0 为纯逻辑事实，1-3 为情绪驱动强度）
    peak_quote: 若有高情绪时刻，保留一句峰值原话片段避免摘要失真
    entity_sends: 需同步发往人物/实体线单写者沉积的事实，JSON 对象字符串（如 '{"person:123": "偏好..."}'）或每行 "线名=>内容"
    window: 同步写出 handoff:<窗口>.md 的窗口标签；留空自动同步本线关联窗口
    close: 事项确已彻底办结时显式传 True（清空 future 令势差归零、解除窗口认领）；默认 False 绝不清空原 future
    keep_future: 兼容旧参数：传 True 且未 close 时强制忽略本次 future 参数、保留原 future
    notify_source: 当 close=True 办结且本线有父源线时，是否自动向源线发送简短完结回传（默认 True）
    scope: 折叠范围："process" 仅局部收起最近工具探索过程并保留对话画面与发言；"all" 整线折叠（默认 "all"）
    """
    from mods.chat import construct as _construct, line as _line, segment as _segment

    line_name, state = _active_line_and_state()
    clean_sum = str(summary or "").strip()
    has_past = bool(str(past_updates or "").strip())
    norm_scope = "process" if str(scope or "all").strip().lower() == "process" and not close else "all"
    done_list: list[str] = []
    view = state.get("view")
    # 仅在确实要折叠 present（给了 summary 或 past_updates）时才先把当轮原子刷入 present；
    # 若 summary="" 仅做轻量 future/close 更新，绝不触碰或折叠 present。
    if isinstance(view, dict) and (clean_sum or has_past):
        folded_before = _line.flush_before_compact(
            line_name,
            view,
            current_act=state.get("act"),
            opening=state.get("line_opening"),
            stop_before_idx=state.get("closing_start_idx"),
        )
        state.setdefault("compacted_ids", set()).update(folded_before)
        if norm_scope != "process":
            own_rows = [row for row in _segment._entries(view) if row.get("ctx") == view.get("ctx")]
            done_list = [d for d in _construct.done_lines(own_rows) if "line__compact" not in d]

    parsed_entities = None
    raw_ent = str(entity_sends or "").strip()
    if raw_ent:
        try:
            parsed_entities = json.loads(raw_ent)
        except ValueError:
            pairs = []
            for item in raw_ent.splitlines():
                if "=>" in item:
                    k, _, v = item.partition("=>")
                    if k.strip() and v.strip():
                        pairs.append((k.strip(), v.strip()))
            parsed_entities = pairs

    # WHY: line:main 自己在分诊/委派外部窗口后若紧接着自动折叠，未显式传 window 时应同步 handoff:me.md，
    # 不得借用 state["acting"] 里的外部窗口标签去覆盖专注线的 handoff:<win>.md。
    win_label = str(window or "").strip() if line_name == _line.MAIN else _default_window_label(state, window)
    info = _line.compact(
        line_name,
        summary,
        scope=norm_scope,
        done=done_list,
        future=None if (keep_future and not close) else future,
        close=close,
        past_updates=past_updates,
        water=water,
        peak_quote=peak_quote,
        entity_sends=parsed_entities,
        window=win_label,
        ctx=view.get("ctx", "") if isinstance(view, dict) else "",
        notify_source=notify_source,
    )
    if info.get("fold_atom"):
        state["compacted_in_run"] = True
    state["closed_in_run"] = bool(info.get("closed"))
    if state.get("closing_now"):
        state["handed_off"] = True
        state["end"] = True
    guard_note = (
        "（未传 close=True，已安全保留原 future；若确已办结请传 close=True）"
        if info.get("ignored_placeholder")
        else ""
    )
    future_desc = (
        "已办结归零"
        if info.get("closed")
        else (f"「{info['future']}」" if info.get("future") else "（未设显式 future）")
    )
    if not info.get("fold_atom"):
        return f"已更新线 {info['name']} 的 future（未折叠 present，future={future_desc}）{guard_note}"
    if info.get("scope") == "process":
        return (
            f"已局部收起线 {info['name']} 的工具过程（fold={info['fold_atom']}，保留聊天画面与发言，"
            f"沉淀past={info['sedimented']}，future={future_desc}）{guard_note}"
        )
    return (
        f"已折叠线 {info['name']}（fold={info['fold_atom']}，沉淀past={info['sedimented']}，"
        f"future={future_desc}）{guard_note}"
    )


def inspect(name: str) -> str:
    """只读查看另一条线的截面（名称、别名、过去认知索引、最近折叠印象与当前 future 目标），零模型调用、不打扰对方。

    @param
    name: 要查看的线名，如 "line:main"、"line:fix-build"、"person:12345"
    """
    from mods.chat import line as _line

    return _line.inspect(name)


def probe(name: str, question: str) -> str:
    """复用目标线已缓存的前缀引用临时追问一个细节问题，只读返回答案，不修改目标线的持久状态、不产生死锁。

    @param
    name: 要探针追问的线名
    question: 要向该线上下文核实的具体短问题
    """
    from mods.chat import line as _line

    return _line.probe(name, question)


__all__ = ["fork", "send", "future", "hand_back", "compact", "inspect", "probe"]
