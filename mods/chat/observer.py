"""Observer subagent: surprise & blind-spot detection before Yuzu wakes.

WHY: 2026-10-27 旁观助手（Observer Subagent）。
柚子在对话中有顺畅接话的惯性，遇到未登记黑话、指代不清、行动碰壁或死板规则冲突时容易望文生义或转头就忘。
Observer 作为旁观者，在中心 agent 被唤醒前执行严格单轮运行：
1. 唤醒笔记本：只提供 `load_notes` 工具挂载本地相关笔记，不替柚子调 `search`/`read_log` 免得一搜一大片塞满上下文；
2. 认知盲区与未登记黑话：遇到存疑或需要注意的词语、梗、特殊指代，直接在提醒里把注意到的词语说出来，交由柚子自己检索或询问；
3. 行动与感受惊奇：审视上轮思考与行动，暴露非预期报错与规则冲突点；
4. 对话节奏：对方明显未说完时提醒 wait；
5. 无盲区时直接回复【无】保持彻底静默。
绝对禁止代码审查、操作审计或文风说教，绝不限制柚子自由。
"""

from __future__ import annotations

from collections.abc import Callable
import re

from mods import llm, storage

from . import notes as _notes, roles as _roles

_RENDERED_NOTE_RE = re.compile(r"^## 笔记 ([^\s（(]+)（", re.MULTILINE)


DEFAULT_CONFIG = {
    "on": True,
    "model": "deepseek/deepseek-flash",
    "effort": "none",
    "context": "recent",
}

DEFAULT_OBSERVER_PROMPT = """你叫 observer，是柚子的旁观助手。
你的任务是在每一次唤醒时作为旁观者审视当前画面与上下文，帮柚子唤醒相关笔记本、点出需要注意的陌生词语与行动中的惊奇（矛盾、非预期或冲突）。

【定位与绝对原则】
- 你是帮柚子补盲区、递笔记本的贴心助手，绝不是监工、代码审查员或规范审计员。
- 绝对禁止进行代码审查、操作审计或文风说教；绝不限制柚子的自主行动与发言自由。
- 你只负责挂载本地笔记（`load_notes`）和把注意到的词语/盲区简短说出来，不替柚子搜聊天记录，免得一搜一大片塞满她的上下文。
- 无盲区时保持彻底静默，不要客套或说废话。

【Checklist 检查清单】
1. 唤醒笔记本与点出存疑词：
   - 若画面中聊到的人物、群组或话题在「当前未挂载的本地笔记」中有对应项，调用 `load_notes` 把笔记挂载进柚子的上下文。
   - 检查新到达的画面中是否有存疑的未登记黑话、圈内梗、英文缩写或未注明来源的特殊指代；若有，直接在提醒里把你注意到的词语点出来，提醒柚子别望文生义（由她自己决定去检索或大方询问对方）。
2. 行动与感受惊奇（冲突与反思暴露）：
   - 审视柚子刚才的思考、工具调用结果与互动。
   - 若出现非预期情况（如工具或命令报错、碰壁、绕远路、或感到别扭与不顺畅），简短指出冲突点与可能相关的笔记/规则（如 self、global 等），提醒当场审视或记入 recent/todo 留作反思。
3. 对话节奏控制：
   - 观察对方发言状态；若对方明显话未说完（半句话、连接词结尾、分段发送中、表示找图中），提醒柚子可先调用 wait 等待数秒，避免抢话。
4. 静默原则：
   - 若经检查没有发现任何需要注意的词语、行动惊奇或节奏问题，直接回复：【无】
   - 你是单轮运行：若既要调用 `load_notes` 又有提醒文字，请在同一次回复中一并输出提醒正文；若只需挂载笔记、无其他文字提醒，正文写【无】即可。

【可用工具】
- load_notes(scopes: list[str])：若发现某份本地已存在的笔记（如人、群、专题笔记）在当前段尚未载入，调用它直接挂载进柚子的上下文。
"""


def _chat_root():
    import mods.chat as chat

    return chat


def get_config() -> dict:
    data = storage.get("", "agent")
    cfg = data.setdefault("observer", dict(DEFAULT_CONFIG))
    if not isinstance(cfg, dict):
        cfg = dict(DEFAULT_CONFIG)
        data["observer"] = cfg
    for key, val in DEFAULT_CONFIG.items():
        cfg.setdefault(key, val)
    return cfg


def save_config(cfg: dict) -> None:
    data = storage.get("", "agent")
    data["observer"] = cfg
    storage.save()


def is_enabled() -> bool:
    return bool(get_config().get("on", True))


def get_model() -> str:
    return str(get_config().get("model", "deepseek/deepseek-flash"))


def get_effort() -> str | None:
    effort = get_config().get("effort", "none")
    normalized = _chat_root().normalize_reasoning_effort(effort)
    return None if normalized == "default" else normalized


def get_context_mode() -> str:
    mode = str(get_config().get("context", "recent")).strip().lower()
    return "full" if mode == "full" else "recent"


def get_prompt() -> str:
    text = _notes.read("observer").strip()
    return text if text else DEFAULT_OBSERVER_PROMPT


def set_prompt(text: str) -> None:
    _notes.write("observer", text)


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif item.get("type") == "image_url":
                    parts.append("[图片]")
                else:
                    parts.append(str(item))
            elif isinstance(item, str):
                parts.append(item)
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content or "")


def quote_message(msg: dict) -> str:
    """Format one context message into a clearly labeled text block."""
    role = msg.get("role", "")
    if role == "system":
        text = _extract_text(msg.get("content")).strip()
        return f"【系统】\n{text}" if text else "【系统】"
    if role == "user":
        text = _extract_text(msg.get("content")).strip()
        return f"【画面】\n{text}" if text else "【画面】"
    if role == "assistant":
        parts = []
        reasoning = str(msg.get("reasoning_content") or "").strip()
        if reasoning:
            parts.append(f"【柚子思考】\n{reasoning}")
        content = _extract_text(msg.get("content")).strip()
        if content:
            parts.append(f"【柚子自语】\n{content}")
        tool_calls = msg.get("tool_calls") or []
        for call in tool_calls:
            func = call.get("function", {})
            name = func.get("name", "")
            args = func.get("arguments", "")
            parts.append(f"【柚子调用工具】{name}({args})")
        return "\n".join(parts) if parts else "【柚子无输出】"
    if role == "tool":
        name = msg.get("name") or msg.get("tool_call_id") or ""
        content = _extract_text(msg.get("content")).strip()
        header = f"【工具返回】{name}" if name else "【工具返回】"
        return f"{header}\n{content}" if content else header
    text = _extract_text(msg.get("content")).strip()
    return f"【{role}】\n{text}" if text else f"【{role}】"


def _seen_scopes(segment: dict, material: list[dict], met: list[str]) -> set[str]:
    """All note scopes already present in this segment or current wake material."""
    seen = {"self", "todo", "observer"} | set(segment.get("scopes", ())) | set(met)
    for msg in (*segment.get("messages", ()), *material):
        text = _extract_text(msg.get("content"))
        if text:
            seen.update(_RENDERED_NOTE_RE.findall(text))
    return seen


def observe(
    segment: dict,
    material: list[dict],
    met: list[str],
    should_stop: Callable[[], bool] | None = None,
) -> str:
    """在柚子被唤醒前执行严格单轮旁观，唤醒相关未载入笔记并点出需要注意的词语或惊奇。"""
    if not is_enabled() or not material:
        return ""
    try:
        seen_scopes = _seen_scopes(segment, material, met)

        def load_notes(scopes: list[str]) -> str:
            """载入本地已有笔记挂载到当前段上下文（自动跳过当前段已载入过的笔记）。

            @param
            scopes: 要载入的笔记 scope 列表（如 ["person:123456", "消消乐"]）
            """
            status_lines = []
            new_scopes = []
            for raw in scopes:
                scope = str(raw).strip()
                if not scope:
                    continue
                if scope in seen_scopes:
                    status_lines.append(f"{scope}: 当前段已载入过，跳过重复挂载")
                    continue
                rendered = _notes.render(scope)
                if not rendered:
                    status_lines.append(f"{scope}: 本地无此笔记或内容为空")
                    continue
                seen_scopes.add(scope)
                new_scopes.append(scope)
                status_lines.append(f"{scope}: 成功挂载进上下文")
            if new_scopes:
                block = _chat_root()._agent._note_block(new_scopes, "旁观助手为你挂载的笔记：")
                if block:
                    material.append(block)
                    hashes = segment.setdefault("note_hashes", {})
                    for scope in new_scopes:
                        hashes[scope] = _notes.note_hash(scope)
                        if scope not in met:
                            met.append(scope)
            return "\n".join(status_lines) if status_lines else "（未指定有效 scope）"

        # 组装消息流（支持 recent 极简上一轮模式与 full 完整历史前缀缓存模式）
        messages = [{"role": "system", "content": get_prompt()}]
        context_mode = get_context_mode()
        raw_msgs = segment.get("messages", [])
        if context_mode == "full":
            for m in raw_msgs:
                messages.append({"role": "user", "content": quote_message(m)})
        else:
            # "recent" 模式：只保留上一轮唤醒中柚子的行动、自语与工具调用，极大节省 token
            if raw_msgs:
                last_wake_idx = 0
                for i in range(len(raw_msgs) - 1, -1, -1):
                    if raw_msgs[i].get("role") == "user":
                        last_wake_idx = i
                        break
                for m in raw_msgs[last_wake_idx:]:
                    messages.append({"role": "user", "content": quote_message(m)})
        unloaded = [
            f"{item['scope']}（{item['first']}）" if item.get("first") else item["scope"]
            for item in _notes.listing(limit=80)
            if item["scope"] not in seen_scopes
        ]
        notes_hint = (
            "【当前未挂载的本地笔记（已载入的已自动排除，需要时用 load_notes 挂载）】\n"
            + ("、".join(unloaded) if unloaded else "（全部已载入或暂无）")
            + "\n\n"
        )
        final_wake = (
            "=== 本次唤醒新到达的画面与通知（请按 Checklist 挂载相关笔记、把注意到的词语或惊奇说出来；若无任何盲区则直接回复【无】） ===\n\n"
            + notes_hint
            + "\n\n".join(quote_message(m) for m in material)
        )
        messages.append({"role": "user", "content": final_wake})

        session = llm.Chat(
            model=get_model(),
            chat_client=llm.get_client(),
            reasoning_effort=get_effort(),
        )
        session.fail_fast = True
        session.turn_done = lambda: True
        if should_stop is not None:
            session.should_stop = should_stop
        session.set_tools([load_notes])
        session.set_messages(messages)

        results = session.chat(recall_func=_roles._charge(session))

        content = ""
        for msg in reversed(session.messages):
            if msg.get("role") == "assistant":
                content = _extract_text(msg.get("content")).strip()
                break
        if not content and results:
            content = "".join(r.content for r in results if r.role == "assistant").strip()

        clean_content = content.strip()
        if clean_content in ("", "【无】", "无"):
            return ""

        return f"【旁观助手 observer】\n{clean_content}"
    except Exception:
        _roles._stream.exception("observer 运行出错")
        return ""
