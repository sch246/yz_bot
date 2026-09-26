from __future__ import annotations

import ast
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import re
import threading
import time
import traceback
from typing import Callable

from mods import _source_pages, context, cq, history, identity, image, llm, log, message, msgs, op, oplog, py, storage, text, thread, tools as tool_modules
from mods.command import command
from mods.capture import capture
from mods.llm import pricing

import mods.chat as _chat_root
from . import view as _view, reader as _reader, agent as _agent, subcommands as _subcommands


def has_at(user_id: int):
    def predicate(event: dict) -> bool:
        for code in msgs.at_cq(event):
            qq = cq.load(code)["data"].get("qq")
            if qq in (None, "all"):
                continue
            try:
                if int(qq) == int(user_id):
                    return True
            except ValueError:
                pass
        return False

    return predicate


_image_pattern = re.compile(r"(\[CQ:image(?:,[^,=]+=[^,\]]*)*\])")


def msg_split(value: str) -> list[dict]:
    parts = []
    for part in _image_pattern.split(value):
        if not part:
            continue
        if _image_pattern.fullmatch(part):
            try:
                uri = cq.load(part)["data"]["url"]
                parts.append({"type": "image_url", "image_url": {"url": uri}})
            except (KeyError, ValueError):
                parts.append({"type": "text", "text": "[解析失败的图片]"})
        elif part.strip():
            parts.append({"type": "text", "text": part})
    return parts


def msg2chat(event: dict, in_group: bool = True) -> dict:
    """Project one QQ message as an ordinary external input.

    WHY: Bot-authored messages arrive here as ``message_sent`` echoes.  The
    corresponding ``say`` tool call is the model's action; the echo is the same
    kind of window event as anybody else's message.  Rendering it as assistant
    would collapse those two facts back together.  The existing message id and
    ordering are enough for the model to associate the pair, so there is no
    separate self-observation tag.
    """
    when = event.get("time")
    timestamp = (time.strftime("%Y-%m-%d %H:%M", time.localtime(when))
                 if isinstance(when, (int, float)) else "未知")
    window = history.window(event)
    author = event.get("user_id")
    if _chat_root._offline_scope.get() is not None or event.get("_bridge_original"):
        sender = event.get("sender") or {}
        name = sender.get("card") or sender.get("nickname") or str(author or "未知")
    else:
        name = identity.getname(author, event.get("group_id")) if author is not None else "未知"
    metadata = [f"  <window>{window}</window>",
                f"  <user_id>{author}</user_id>",
                f"  <name>{name!r}</name>",
                f"  <time>{timestamp}</time>",
                f"  <message_id>{event.get('message_id', '')}</message_id>"]
    if event.get("_log_origin"):
        metadata.append(f"  <origin>{event['_log_origin']}</origin>")
    source = event.get("_history_source") or ("archive" if event.get("_log_origin") and not event.get("_live") else "live")
    metadata.append(f"  <source>{source}</source>")
    if event.get("_history_seq") is not None:
        metadata.append(f"  <remote_seq>{event['_history_seq']}</remote_seq>")
    content = [{"type": "text", "text": "<metadata>\n" + "\n".join(metadata) + "\n</metadata>"}, *msg_split(event.get("message", ""))]
    return {"role": "user", "content": content}


def _poke_text(event: dict) -> str:
    """The model's view of a poke: the same names it sees on every message.

    Deliberately not ``chatlog.format_poke``.  That one renders the log, where
    the QQ-side identity is right because a record of what happened must not be
    rewritten by a display preference.  Here the opposite holds: ``msg2chat``
    already names people with ``identity.getname``, so a poke rendered any other
    way would be the one place the model sees two names for one person.
    """
    group_id = event.get("group_id")
    user_id, target_id = event.get("user_id"), event.get("target_id")
    name = identity.getname(user_id, group_id)
    target = identity.getname(target_id, group_id)
    return f"{name}({user_id})戳了戳{target}({target_id})"


def _is_context_poke(event: dict, in_group: bool) -> bool:
    if not msgs.is_poke(event):
        return False
    return bool(in_group) or event.get("target_id") == identity.bot_id()


def event2chat(event: dict, in_group: bool) -> dict:
    """Convert one history event into the single shape the model sees.

    WHY: 插话与 get_msgs 必须走同一条转换。中途插进来的消息如果换个形状(比如只塞纯
    文本)，模型就会看到同一个人在同一轮里忽然换了说话格式，而且图片、回复引用这些都会
    丢。这里是唯一的转换点。
    """
    if msgs.is_msg(event):
        return msg2chat(event, in_group)
    kind = "群聊事件" if in_group else "私聊事件"
    return {"role": "user", "content": f"【{kind} {history.window(event)}】{_poke_text(event)}"}


def _model_event(event: dict, in_group: bool) -> dict | None:
    """Project a window event only if it belongs in the model's view."""
    if msgs.is_msg(event):
        value = msgs.body(event)
        if value.startswith("#"):
            return None
    elif not _is_context_poke(event, in_group):
        return None
    return event2chat(event, in_group)


def _notification_projection(entry: dict) -> dict:
    details = entry.get("unread", ())
    if details:
        shown = []
        for detail in details:
            candidate = "；".join([*shown, _reader._unread_detail_text(detail, include_wakes=False)])
            if _chat_root.count_tokens(candidate) > _chat_root.NOTICE_TOKENS - 150:
                break
            shown.append(_reader._unread_detail_text(detail, include_wakes=False))
        omitted = len(details) - len(shown)
        listing = "；".join(shown) + (f"；还有 {omitted} 个窗口未列出，用 status() 查看"
                                   if omitted else "")
    else:
        listing = "、".join(f"{window[0]}:{window[1]}" for window in entry["windows"])
    activations = entry.get("activations", ())
    activation_listing = ("；".join(_reader._activation_text(item) for item in activations)
                          if activations else "旧版通知未记录逐条唤醒")
    content = (f"[{entry['id']}] 新召唤通知（创建时快照，未读序号可能已变化）。当时全部未读唤醒：{activation_listing}。"
               f"未读概况：{listing}。"
               "正文仍在未读信源；普通消息本身不激活。"
               "可用 take(source, start, count) 按执行时未读序号选范围正式阅读，mentions(source) 正式读入未读提及，"
               "read_messages 按 message_id 选择档案。通知已看见不等于消息已读；"
               "未读红点不会自行反复唤醒，之后的新唤醒仍会再次带上这份完整未读集合。")
    return {"role": "user", "content": content}


def _pending_hint() -> str:
    rows = _reader.unread_details()
    all_sources = oplog.sources()
    latest = {tuple(source["window"]): source["key"] for source in all_sources
              if source["source_type"] in ("napcat_boot", "napcat_history")}
    independent = [source for source in all_sources
                   if source["source_type"] == "napcat_history"
                   and (source["remaining"] or source["state"] != "complete")
                   and (source["remaining"] or latest[tuple(source["window"])] == source["key"])]
    if not rows and not independent:
        return ""
    shown = []
    for detail in rows:
        if len(shown) >= 10 or _chat_root.count_tokens("；".join(_reader._unread_detail_text(item, include_wakes=False)
                                                 for item in [*shown, detail])) > _chat_root.NOTICE_TOKENS - 150:
            break
        shown.append(detail)
    tail = f"；另有 {len(rows) - len(shown)} 个窗口，用 status() 查看" if len(rows) > len(shown) else ""
    source_lines = []
    for source in independent[:3]:
        mentions = [f"{ordinal}@{member.get('time')}"
                    for ordinal, (_page, _offset, member) in enumerate(
                        _reader._source_unread_members(source), 1) if member.get("mentioned")]
        source_lines.append(f"{source['name']} key={source['key']} 未读={source['remaining']} "
                            f"提及={source['mention_count'] - source['read_mention_count']} "
                            f"提及未读序号@时间={','.join(mentions)} 状态={source['state']}"
                            + (f" 缺口={source['gap']}" if source['gap'] else ""))
    if len(independent) > 3:
        source_lines.append(f"另有 {len(independent) - 3} 个信源，用 status() 查看")
    parts = [*(_reader._unread_detail_text(detail) for detail in shown), tail, *source_lines]
    return _chat_root.bounded_excerpt("待正式读取（当前快照；take 执行时重算序号）："
                           + "；".join(part for part in parts if part), _chat_root.NOTICE_TOKENS)


def _message_cost(converted: dict) -> int:
    content = converted["content"]
    if isinstance(content, str):
        cost = _chat_root.count_tokens(content)
    elif content is None:
        cost = 0
    else:
        cost = sum(_chat_root.count_tokens(part.get("text", "")) for part in content
                   if isinstance(part, dict) and part.get("type") == "text")
    if isinstance(converted.get("reasoning_content"), str):
        cost += _chat_root.count_tokens(converted["reasoning_content"])
    if converted.get("tool_calls"):
        cost += _chat_root.count_tokens(json.dumps(converted["tool_calls"], ensure_ascii=False))
    return cost


def _native_assistant(entry: dict, model: str) -> dict | None:
    if (not model.startswith("deepseek/") or entry.get("model") != model
            or entry.get("protocol") != "openai_chat_completions"):
        return None
    assistant = entry.get("assistant")
    if not isinstance(assistant, dict) or assistant.get("role") != "assistant":
        return None
    if not isinstance(assistant.get("content"), (str, type(None))):
        return None
    reasoning = assistant.get("reasoning_content")
    if reasoning is not None and not isinstance(reasoning, str):
        return None
    calls = assistant.get("tool_calls", [])
    if not isinstance(calls, list) or len(calls) != len(entry["actions"]):
        return None
    if any(not isinstance(call, dict) or not isinstance(call.get("id"), str)
           or not call["id"] or call.get("type") != "function"
           or not isinstance(call.get("function"), dict)
           or not isinstance(call["function"].get("name"), str)
           or not isinstance(call["function"].get("arguments"), str)
           for call in calls):
        return None
    if len({call["id"] for call in calls}) != len(calls):
        return None
    return {key: assistant[key] for key in
            ("role", "content", "reasoning_content", "tool_calls") if key in assistant}


def _native_pair(output: dict, result: dict, model: str) -> list[tuple[dict, dict]] | None:
    assistant = _native_assistant(output, model)
    if assistant is None or not assistant.get("tool_calls") or result.get("source") != output["id"]:
        return None
    calls = assistant["tool_calls"]
    returned = result.get("returns", [])
    if (len(returned) != len(calls)
            or [item.get("position") for item in returned] != list(range(len(calls)))):
        return None
    for call, item in zip(calls, returned):
        if (item.get("tool_call_id") != call["id"]
                or item.get("name") != call["function"]["name"]
                or item.get("arguments") != call["function"]["arguments"]
                or not isinstance(item.get("content"), str)):
            return None
    projected = [(output, assistant)]
    projected.extend((result, {"role": "tool", "tool_call_id": call["id"],
                               "content": item["content"]})
                     for call, item in zip(calls, returned))
    return projected


def _event_refs(output: dict, result: dict | None = None) -> dict:
    lines = [f"O {output['id']}"]
    lines.extend(f"{output['id']}#{position + 1} {action['name']}"
                 for position, action in enumerate(output["actions"]))
    if result is not None:
        lines.append(f"R {result['id']}")
    return {"role": "user", "content": "<event_refs>\n" + "\n".join(lines) + "\n</event_refs>"}


def _stream_rows(window: tuple | None, token_limit: int | None,
                 event_limit: int | None, native_model: str | None = None,
                 show_thought: bool = True
                 ) -> tuple[list[tuple[dict, dict]], int, bool]:
    """Select one visible suffix by event count and projected token cost."""
    entries = oplog.events(window)
    recalled_by_window: dict[tuple, set[str]] = {}
    links = oplog.say_links(window)
    picked: list[tuple[dict, dict]] = []
    used = 0
    picked_events = 0
    blocked = False
    skipped: set[str] = set()
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if entry["id"] in skipped:
            continue
        if entry["kind"] == "notification" and not entry.get("acknowledged"):
            continue
        native = None
        if (native_model and entry["kind"] == "result" and index > 0
                and entries[index - 1]["kind"] == "output"):
            native = _native_pair(entries[index - 1], entry, native_model)
        if native is not None:
            native.append((entries[index - 1], _event_refs(entries[index - 1], entry)))
            amount = sum(_message_cost(message) for _source, message in native)
            if token_limit is not None and used + amount > token_limit:
                # WHY: A pair which no longer fits as original messages can
                # still carry its facts in the bounded text view. Both events
                # enter together; no provider sees an orphaned tool message.
                native = [(entries[index - 1], _output_projection(entries[index - 1],
                                                                  show_thought=show_thought)),
                          (entry, _result_projection(entry, links))]
                amount = sum(_message_cost(message) for _source, message in native)
            if ((event_limit is not None and picked_events + 2 > event_limit)
                    or (token_limit is not None and used + amount > token_limit)):
                blocked = True
                break
            picked.extend(reversed(native))
            skipped.add(entries[index - 1]["id"])
            picked_events += 2
            used += amount
            continue
        if event_limit is not None and picked_events >= event_limit:
            blocked = True
            break
        if entry["kind"] == "input":
            projection = entry.get("projection")
            if projection is None:
                continue
            message_id = entry["event"].get("message_id")
            if message_id is not None and window is not None:
                from mods import chatlog

                source_window = tuple(entry.get("source_window") or window)
                if source_window not in recalled_by_window:
                    recalled_by_window[source_window] = chatlog.recalled_ids(*source_window)
                recalled = recalled_by_window[source_window]
                if chatlog.recall_key(message_id) in recalled:
                    continue
            converted = _numbered(projection, entry["id"])
            converted = _echo_relation(converted, entry, links)
        elif entry["kind"] == "output":
            if not entry["actions"] and not entry["body"]:
                if not native_model or _native_assistant(entry, native_model) is None:
                    continue
            native_output = (_native_assistant(entry, native_model)
                             if native_model and not entry["actions"] else None)
            if native_output is not None:
                native_rows = [(entry, native_output), (entry, _event_refs(entry))]
                amount = sum(_message_cost(message) for _source, message in native_rows)
                if token_limit is not None and used + amount > token_limit:
                    native_rows = [(entry, _output_projection(entry, show_thought=show_thought))]
                    amount = _message_cost(native_rows[0][1])
                if token_limit is not None and used + amount > token_limit:
                    blocked = True
                    break
                picked.extend(reversed(native_rows))
                picked_events += 1
                used += amount
                continue
            converted = _output_projection(entry, show_thought=show_thought)
        elif entry["kind"] == "notification":
            converted = _notification_projection(entry)
        else:
            converted = _result_projection(entry, links)
        amount = _message_cost(converted)
        if token_limit is not None and used + amount > token_limit:
            blocked = True
            break
        picked.append((entry, converted))
        picked_events += 1
        used += amount
    picked.reverse()
    return picked, used, blocked


def get_msgs(token_limit: int | None = None, return_token: bool = False):
    current = context.current() or {}
    max_events, max_tokens = _chat_root.limit(current)
    selected_limit = max_tokens if token_limit is None else token_limit
    rows, used, _blocked = _stream_rows(history.window(current), selected_limit, max_events)
    output = [converted for _entry, converted in rows]
    return (output, used) if return_token else output


def _chat_msgs() -> list[dict]:
    current = context.current() or {}
    max_events, max_tokens = _chat_root.limit(current)
    rows, _used, _blocked = _stream_rows(history.window(current), max_tokens, max_events)
    return [converted for entry, converted in rows if entry["kind"] == "input"]


def _base_prompt() -> list[dict]:
    return [{"role": "system", "content": f"""## 注意事项
- 你的昵称: {identity.bot_name()}
- 你的QQ号: {identity.bot_id()}；群聊 at 格式为 [CQ:at,qq=qq号]，reply 格式为 [CQ:reply,id=message_id]
- 你收到的消息原样带着这两种 CQ 码。reply 里的 message_id 与上文各条消息 <metadata> 中的 <message_id> 对应，据此判断对方在回复哪一条
- 聊天中可能不会有明显的问题，扮演好角色即可
- 如无特殊要求，请用中文回复
- **说话要调 `say`**。直接写在回复正文里的内容不会发出去，只会留在你自己的输出轨迹里
- 眼前历史是唯一全局已读信息流按预算选出的可见部分，不是全部记录。通知不等于读取；通知是创建时快照，尾部 hint 给当前未读提及的时间和未读序号（所有未读都计数，已读/跳过不计数）。take(source, start, count) 在工具执行时按当前未读序号选范围；已读/跳过桥会随新 input 展示，原已读正式号不变。正式输入的 read_by 是发起工具的输出号，read_via 是公开工具名；mark_read 只跳过并留下 skipped_by，不伪造 input。mentions 正式消费至多 500 条未读提及。红点本身不会反复启动你，后来有新唤醒时才再叫一次并重列完整集合。read_messages 从聊天档案选消息并在下一请求正式阅读
- 想积累经验就实际写入以后会用的载体：可复用做法写 Markdown Skill 并按需加载，全局待办用 `edit_hint` 保存；只在回复里说“记住了”不会保存它
- 对外发送必须在 say 里明确写目标 g群号 或 u私聊对端号；没有默认接收窗口
- `say` 返回这条消息的 message_id；它默认 `final_call=true`，说完这一轮就结束，要接着干活就传 `final_call=false`"""}]


def _build_context_snapshot(token_limit: int | None = None) -> list:
    """Project the selected visible stream without consuming unread mail."""
    current = context.current() or {}
    max_events, max_tokens = _chat_root.limit(current)
    selected_limit = max_tokens if token_limit is None else token_limit
    rows, _used, _blocked = _stream_rows(history.window(current), selected_limit, max_events)
    return _close_with_user([converted for _entry, converted in rows])


def _numbered(converted: dict, event_id: str) -> dict:
    content = converted["content"]
    prefix = f"[{event_id}] "
    if isinstance(content, str):
        return {**converted, "content": prefix + content}
    return {**converted, "content": [{"type": "text", "text": prefix}, *content]}


def _provenance_projection(converted: dict | None, actor: str | None,
                           via: str | None, *, skipped: bool = False) -> dict | None:
    if converted is None or not actor or not via:
        return converted
    prefix = "skipped" if skipped else "read"
    tags = f"  <{prefix}_by>{actor}</{prefix}_by>\n  <{prefix}_via>{via}</{prefix}_via>"
    content = converted["content"]
    if isinstance(content, list):
        first = content[0]
        if first.get("type") == "text" and "</metadata>" in first.get("text", ""):
            first = {**first, "text": first["text"].replace("</metadata>", tags + "\n</metadata>", 1)}
            return {**converted, "content": [first, *content[1:]]}
        return {**converted, "content": [{"type": "text", "text":
                 f"<provenance>\n{tags}\n</provenance>"}, *content]}
    return {**converted, "content": content + f"\n<provenance>\n{tags}\n</provenance>"}


def _read_projection(converted: dict | None, read_by: str | None,
                     read_via: str | None) -> dict | None:
    return _provenance_projection(converted, read_by, read_via)


def _echo_relation(converted: dict, entry: dict, links: dict[str, tuple[str, str]]) -> dict:
    event = entry["event"]
    if event.get("post_type") != "message_sent" or event.get("message_id") is None:
        return converted
    linked = links.get(str(event["message_id"]))
    if linked is None or linked[0] != entry["id"]:
        return converted
    relation = f"（已确认由 {linked[1]} say 发出）"
    content = converted["content"]
    if isinstance(content, list):
        return {**converted, "content": [content[0], {"type": "text", "text": relation}, *content[1:]]}
    return {**converted, "content": content + relation}


def _output_projection(entry: dict, *, show_thought: bool = True) -> dict:
    actions = "\n".join(f"{entry['id']}#{position + 1} {action['name']}({action['arguments']})"
                        for position, action in enumerate(entry["actions"]))
    body = entry["body"] if isinstance(entry["body"], str) else str(entry["body"])
    thought = entry.get("thought") if show_thought else None
    if show_thought and thought is None and isinstance(entry.get("assistant"), dict):
        thought = entry["assistant"].get("reasoning_content")
    thought_text = f"\n自己的思考：{thought}" if isinstance(thought, str) and thought else ""
    return {"role": "user", "content": f"[{entry['id']}] 自己的输出：{body}{thought_text}\n{actions}"}


def _result_projection(entry: dict, links: dict[str, tuple[str, str]]) -> dict:
    lines = []
    for result in entry["returns"]:
        reference = f"{entry['source']}#{result['position'] + 1}"
        relation = ""
        if result["name"] == "say" and str(result["content"]).lstrip("-").isdecimal():
            linked = links.get(str(result["content"]))
            if linked and linked[1] == reference:
                relation = f" (已确认回声 {linked[0]})"
        lines.append(f"{reference} {result['name']} -> {result['content']}{relation}")
    content = f"[{entry['id']}] 行动返回：\n" + "\n".join(lines)
    return {"role": "user", "content": content}


def build_context(token_limit: int | None = None) -> list:
    """Build the current window context without consuming its mailbox."""
    return _build_context_snapshot(token_limit)


_CLOSING_NOTE = "<system-reminder>\n会话已自动接续。\n</system-reminder>"


def _close_with_user(messages: list) -> list:
    """Make sure the assembled context ends with a user message.

    WHY: DeepSeek 在请求带 `tools` 时要求**最后一条 user 之后的每条 assistant** 都带
    `reasoning_content`，缺一条就 400（"The reasoning_content in the thinking mode must
    be passed back to the API."）。2026-09-17 用最小报文实测的边界：同一条没有 reasoning
    的 assistant 只要**后面还有 user** 就没关系；补一个空串也能过；而把 `tools` 去掉整条
    校验就消失。也就是说被拒与否取决于**位置**，不是取决于那条消息是谁造的。

    WHY: 于是这里只保一件事——上下文以一条 user 消息收尾。这样尾段的 assistant 集合天然是
    空的，规则无从触发，而**不需要**去给重建出来的历史编造 `reasoning_content`：那个字段是
    DeepSeek 专有的，别的供应商并不要求（草籽 2026-09-17），替它们发明一个字段是拿一个供应
    商的规矩去改所有人的请求。

    WHY: 平时不会走到这里——正常聊天最后一条总是触发它的那条 user 消息，`.chat` 单句自带
    一条。只有"没有新消息的那一轮"（重启后接着聊，`reboot.resume_chat`）会以 assistant
    收尾，那正是 2026-09-17 两次 400 的现场。

    WHY: 追加的是一句极短的**声明**，不是假装有人说了一句话。形状抄 `tools._announce` 的系统
    追加：`role="user"` 加 `<system-reminder>` 框架——那条路径实跑过很多轮，说明"系统追加的
    user 消息"这个形状本身是被接受的。它只活在发出去的那一份里，不进 chatlog、不发 QQ。

    WHY: 空 content 的 assistant 也算数。原生 O 可以只有思考与行动、没有正文，
    它照样是 assistant，照样要算进尾段。
    """
    if messages and messages[-1].get("role") == "user":
        return messages
    return [*messages, {"role": "user", "content": _CLOSING_NOTE}]
