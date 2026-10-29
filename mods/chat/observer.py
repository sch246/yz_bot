"""Observer subagent: surprise & blind-spot detection before Yuzu wakes.

WHY: 2026-10-27 旁观助手（Observer Subagent）。
柚子在对话中有顺畅接话的惯性，遇到未登记黑话、指代不清、行动碰壁或死板规则冲突时容易望文生义或转头就忘。
Observer 作为旁观者，在中心 agent 被唤醒前执行严格单轮运行：
1. 不替柚子调 `search`/`read_log`，免得一搜一大片塞满上下文；
2. 认知盲区与未登记黑话：遇到存疑或需要注意的词语、梗、特殊指代，直接在提醒里把注意到的词语说出来，交由柚子自己检索或询问；
3. 行动与感受惊奇：审视上轮思考与行动，暴露非预期报错与规则冲突点；
4. 对话节奏：对方明显未说完时提醒 wait；
5. 无盲区时直接回复【无】保持彻底静默。
绝对禁止代码审查、操作审计或文风说教，绝不限制柚子自由。

WHY: 2026-10-29 草籽：observer 主要是捕捉惊奇、发现笔记里没有的词。原先第 1 条是 `load_notes`，把整篇
笔记挂进这一段，不请自来、挂上就一直占着；这一半换成了 `agent._breadcrumbs`，画面提到有笔记的东西只在
请求末尾递一句。它点出的词由代码去笔记和日记里查一遍（`_annotate`）：它看不见笔记内容，"笔记里有没有"
不该靠它猜。
"""

from __future__ import annotations

from collections.abc import Callable
import re

from mods import llm, storage

from . import notes as _notes, roles as _roles

# 它在提醒里用「」括起来的词；太长的多半是引用原话，不当词查。
_WORD = re.compile(r"「([^「」\n]{1,20})」")


DEFAULT_CONFIG = {
    "on": True,
    "model": "deepseek/deepseek-flash",
    "effort": "none",
    "context": "recent",
}

DEFAULT_OBSERVER_PROMPT = """你叫 observer，是柚子的旁观助手。
每次她被叫醒之前，你先看一眼新到的画面和她上一轮做的事，把她可能顺手滑过去的惊奇点出来。

【定位】
- 你是帮她补盲区的旁观者，不是监工：不做代码审查、操作审计或文风说教，不限制她怎么做、怎么说。
- 只说你注意到的，不客套。

【看什么】
1. 陌生的说法：画面里有没有她可能不认识的词——黑话、圈内梗、缩写、外号、没交代来源的指代（"那个"、"上次那件事"）。
   - 有就把词本身用「」括起来，一个词一对，如「嘉豪」；引用别人的原话不要用「」。
   - 系统会拿你括起来的词去她的笔记和日记里查，查到了告诉她在哪，查不到告诉她笔记里没有。所以你不用猜她认不认识，觉得生就括；日常用语、她明显已经在用的词不用括。
2. 行动里的惊奇：她上一轮的思考、工具调用和结果里，有没有出乎意料的——报错、碰壁、绕远路、结果和预期对不上、她自己觉得别扭。简短指出冲突点，提醒她当场想一下，或者记进 recent/todo。
3. 说话的节奏：对方明显没说完（半句话、连接词结尾、在分段发、说在找图），提醒她可以先 wait 一会儿再接。

【输出】
- 简短几行就够。
- 什么都没注意到，只回：【无】
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


def _annotate(text: str) -> str:
    """After each 「word」 it raised, say which notes and diaries mention it, or that none do."""
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


def observe(
    segment: dict,
    material: list[dict],
    should_stop: Callable[[], bool] | None = None,
) -> str:
    """在柚子被唤醒前执行严格单轮旁观，点出陌生的说法、行动里的惊奇和说话节奏。"""
    if not is_enabled() or not material:
        return ""
    try:
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
        final_wake = (
            "=== 本次唤醒新到达的画面与通知（把注意到的陌生说法、惊奇或节奏问题说出来；什么都没有就只回【无】） ===\n\n"
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

        return f"【旁观助手 observer】\n{_annotate(clean_content)}"
    except Exception:
        _roles._stream.exception("observer 运行出错")
        return ""
