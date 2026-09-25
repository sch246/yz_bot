"""QQ-window chat context, settings, tools, and the ``.chat`` command."""

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


LOAD_AFTER = ("history", "identity", "image", "llm", "oplog", "storage")

IMAGE_MODES = ("off", "lazy", "eager")
IMAGE_MODE_ALIASES = {"0": "off", "1": "lazy", "2": "eager"}

# WHY: keep=原样带回思考内容(默认，满足 DeepSeek thinking mode 的工具调用协议)，
# drop=在工具循环内把它换成空串省 token。只影响一次工具循环之内，思考本来就不跨轮。
REASONING_MODES = ("keep", "drop")
REASONING_ALIASES = {"on": "keep", "off": "drop", "1": "keep", "0": "drop"}

# WHY: append=工具变动追加进上下文(默认，进历史、可回放、不打断前缀缓存)，
# ui=整个工具状态作为一整块 hint 挂在末尾(只有一个权威副本，且明确位于所有修改之后，
# 代价是每次子请求都是未命中缓存的新 token)。两者的取舍见 tools._state_hint。
TOOLS_MODES = ("append", "ui")
TOOLS_MODE_ALIASES = {"0": "append", "1": "ui", "hint": "ui"}

settings: list = []
prompts: dict = {}
chat_groups: list = []
description_cache: dict = {}
llm_config: dict = {}
# WHY: 两个上限的默认值写死在这里，不再读 llm_system/config.json。一是那份配置只在
# on_load 读一次，改它必须重启才生效，而它描述的本来就是"每窗口配置的缺省"、不是全局
# 开关；二是那两个数从 LLM 刚出现时就没回头调过，当时的理由（上限本身就小）早不成立了。
# WHY: 宁可低。事件数现在包含输出与返回，默认 20 可能比旧消息数更早截断；
# 真需要更长历史的窗口可以自己写覆盖值（见 WINDOW_SETTINGS 与 #limit），
# 这比让所有窗口默默付大账单好。token 反过来给得宽：卡在预算里会让模型说到一半没法思考，
# 而一个纯聊天的会话本来就远用不满，所以它的默认值是"够用"而不是"尽量小"。
DEFAULT_MAX_EVENTS = 20
DEFAULT_MAX_TOKEN = 50000
AGENT_WINDOW = oplog.AGENT_WINDOW
MAIL_PAGE_EVENTS = 8
MAIL_PAGE_TOKENS = 4000
RESULT_PAGE_EVENTS = 8
RESULT_ITEM_TOKENS = MAIL_PAGE_TOKENS
NOTICE_TOKENS = 500
_PRESSURE_PERCENT = 75
_cost_lock = threading.Lock()
# Eager capture is image work reported on the image stream, not chat traffic.
_image_stream = log.stream("image")
# 自言自语走 msg 流：和收发消息的回显共用同一把行租约，见 get_handler。
_self_talk = log.stream("msg")
# hint 求值失败只记日志，所以它有自己的流，不混进聊天流量。
_hint_stream = log.stream("hint")
_offline_scope: ContextVar[dict | None] = ContextVar("chat_offline_scope", default=None)


def getchatstorage(event: dict | None = None) -> dict:
    if context.agent_mode():
        return storage.get("", "agent")
    event = context.current() if event is None else event
    if event is None:
        raise RuntimeError("当前没有聊天窗口")
    if event.get("group_id") is not None:
        return storage.get("groups", str(event["group_id"]))
    # 私聊窗口是对端（`target_id`）；`user_id` 是作者，只在窗口缺失时兜底。
    return storage.get("users", str(event.get("target_id") or event.get("user_id")))


def normalize_image_mode(value) -> str:
    if value is True:
        return "lazy"
    if value is False or value is None:
        return "off"
    normalized = IMAGE_MODE_ALIASES.get(str(value).lower(), str(value).lower())
    return normalized if normalized in IMAGE_MODES else "off"


def window_setting(name: str, data: dict | None = None):
    """本窗口生效的窗口级配置：窗口里写过的合法值优先，否则回到默认值。

    WHY: 只有这一处合并，没有别的间接层。窗口层住在 `getchatstorage()` 的平铺键里
    （与 `#image`/`#tools` 一系），缺省写死在 WINDOW_SETTINGS；合法值判断交给归一化
    函数，所以读取端永远拿得到能用的值，旧存储里的遗留值也不会让聊天崩掉。
    """
    key, _default, normalize = WINDOW_SETTINGS[name]
    return normalize((getchatstorage() if data is None else data).get(key))


def limit(event: dict | None = None) -> tuple[int, int]:
    """本窗口生效的 `(可见事件数上限, 上下文 token 上限)`。

    两个值都从 WINDOW_SETTINGS 取，窗口没写就用默认。没有窗口（没有 group_id 也
    没有 user_id）时直接给默认值——`#hint` 的默认代码要拿它显示，不该因此抛出去。
    """
    if context.agent_mode():
        data = getchatstorage()
        return (WINDOW_SETTINGS["max_events"][2](data.get("max_events")),
                WINDOW_SETTINGS["max_token"][2](data.get("max_token")))
    event = context.current() if event is None else event
    if event is None or history.window(event) is None:
        return DEFAULT_MAX_EVENTS, DEFAULT_MAX_TOKEN
    data = getchatstorage(event)
    value = data.get("max_events", data.get("max_msg"))
    return WINDOW_SETTINGS["max_events"][2](value), window_setting("max_token", data)


def get_image_mode(data: dict | None = None) -> str:
    return window_setting("image", data)


# WHY: 下面两组照 image 那一套写：normalize 负责把存坏的值拉回默认，读取端永远拿得到
# 合法值，所以旧存储里的遗留值不会让聊天崩掉。别改成直接读原值。
def normalize_reasoning_mode(value) -> str:
    normalized = REASONING_ALIASES.get(str(value).lower(), str(value).lower())
    return normalized if normalized in REASONING_MODES else "keep"


def get_reasoning_mode(data: dict | None = None) -> str:
    return window_setting("reasoning", data)


def normalize_tools_mode(value) -> str:
    normalized = TOOLS_MODE_ALIASES.get(str(value).lower(), str(value).lower())
    return normalized if normalized in TOOLS_MODES else "append"


def _bounded_int(minimum: int, fallback: int, maximum: int | None = None):
    """归一化成指定范围内的整数，否则回到默认值。"""

    def normalize(value) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return fallback
        return number if number >= minimum and (maximum is None or number <= maximum) else fallback

    return normalize


# 窗口级配置：命令名 -> (storage 键, 默认值, 归一化)。
# WHY: 读取一律走 window_setting，合并就一句话——窗口里写过的合法值优先，否则默认值。
# 表是唯一的清单，加一项配置就是加一行；默认值散在各自动归化函数的兜底分支里，改一处就
# 够。`hint`/`prompt` 不在这张表里：它们是复合值（dict / 列表），缺省来自别的存储，
# 各自的合并也只有一行，塞进来反而要造间接层。
WINDOW_SETTINGS = {
    "image": ("image", "off", normalize_image_mode),
    "reasoning": ("reasoning", "keep", normalize_reasoning_mode),
    "tools": ("tools", "append", normalize_tools_mode),
    "max_events": ("max_events", DEFAULT_MAX_EVENTS, _bounded_int(1, DEFAULT_MAX_EVENTS)),
    "max_token": ("max_token", DEFAULT_MAX_TOKEN, _bounded_int(1, DEFAULT_MAX_TOKEN)),
    "pressure_percent": ("pressure_percent", _PRESSURE_PERCENT, _bounded_int(1, _PRESSURE_PERCENT, 100)),
}


def get_tools_mode(data: dict | None = None) -> str:
    return window_setting("tools", data)


def get_prompt() -> list:
    selected = getchatstorage().get("prompt")
    if not selected:
        return settings
    if isinstance(selected, str):
        selected = prompts.get(selected)
    return selected if isinstance(selected, list) else []


def get_model(data: dict | None = None) -> str:
    data = getchatstorage() if data is None else data
    selection = data.get("model", llm_config.get("default_model", llm.DEFAULT_MODEL))
    try:
        llm.resolve_model(llm_config, selection)
    except ValueError:
        data.pop("model", None)
        selection = llm_config.get("default_model", llm.DEFAULT_MODEL)
    return selection


def count_tokens(value: str) -> int:
    try:
        import tiktoken

        return len(tiktoken.encoding_for_model("gpt-4").encode(value))
    except Exception:
        return max(1, len(value) // 3)


def bounded_excerpt(value: str, max_tokens: int) -> str:
    """Take the longest leading character span within a token budget."""
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if count_tokens(value[:middle]) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    return value[:low]


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
    if _offline_scope.get() is not None:
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


def parse_target(target: str) -> tuple[str, int]:
    """Accept only an explicit QQ group or private-peer destination."""
    matched = re.fullmatch(r"([gu])([1-9][0-9]*)", str(target).strip())
    if matched is None:
        raise ValueError("目标必须是 g<群号> 或 u<私聊对端号>")
    return ("group" if matched[1] == "g" else "private"), int(matched[2])


def _unread_detail_text(detail: dict) -> str:
    window = detail["window"]
    target = ("g" if window[0] == "group" else "u") + str(window[1])
    sources = ", ".join(f"{item['kind']} 作者={item['user_id']} 时间={item['time']}"
                        for item in detail["wake_sources"])
    recovery = detail.get("recovery")
    extra = ((f" 补回未读={recovery['remaining']} 补回状态={recovery['state']}"
              + (f" 缺口={recovery['gap']}" if recovery.get("gap") else ""))
             if recovery else "")
    return (f"{target} 未读={detail['unread']} 普通={detail['ordinary']} "
            f"@/提及={detail['mentions']} 其他唤醒={detail['other_wakes']}"
            + (f" 唤醒来源：{sources}" if sources else "") + extra)


def unread_details() -> list[dict]:
    details = {tuple(item["window"]): item for item in oplog.pending_details()
               if item["window"][0] in ("group", "private")}
    all_sources = oplog.sources()
    boot_sources = {tuple(source["window"]): source for source in all_sources
                    if source["source_type"] == "napcat_boot"}
    boot_remaining: dict[tuple, tuple[int, int]] = {}
    for source in all_sources:
        if source["source_type"] == "napcat_boot":
            window = tuple(source["window"])
            remaining, mentions = boot_remaining.get(window, (0, 0))
            boot_remaining[window] = (remaining + source["remaining"],
                                      mentions + source["mention_count"] - source["read_mention_count"])
    for window, source in boot_sources.items():
        if window[0] not in ("group", "private"):
            continue
        remaining, mentions = boot_remaining[window]
        if not remaining and source["state"] == "complete":
            continue
        detail = details.setdefault(window, {"window": list(window), "unread": 0,
                                             "ordinary": 0, "mentions": 0,
                                             "other_wakes": 0, "wake_sources": []})
        recovery = detail.setdefault("recovery", {"remaining": 0, "state": "complete", "gap": None})
        recovery["remaining"] = remaining
        detail["mentions"] += mentions
        if source["state"] == "fetching":
            recovery["state"] = "fetching"
        elif source["state"] != "complete" and recovery["state"] != "fetching":
            recovery["state"] = source["state"]
        if source["gap"]:
            recovery["gap"] = source["gap"]
    return list(details.values())


def _notification_projection(entry: dict) -> dict:
    details = entry.get("unread", ())
    if details:
        shown = []
        for detail in details:
            candidate = "；".join([*shown, _unread_detail_text(detail)])
            if count_tokens(candidate) > NOTICE_TOKENS - 150:
                break
            shown.append(_unread_detail_text(detail))
        omitted = len(details) - len(shown)
        listing = "；".join(shown) + (f"；还有 {omitted} 个窗口未列出，用 list_unread(offset={len(shown)}) 查看"
                                   if omitted else "")
    else:
        listing = "、".join(f"{window[0]}:{window[1]}" for window in entry["windows"])
    content = (f"[{entry['id']}] 新召唤通知：{listing}。"
               "正文仍在未读队列；普通消息本身不激活；可先 peek，再指定目标 say，随后逐页 pull_mail。")
    return {"role": "user", "content": bounded_excerpt(content, NOTICE_TOKENS)}


def _pending_hint() -> str:
    rows = unread_details()
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
        if len(shown) >= 10 or count_tokens("；".join(_unread_detail_text(item)
                                                 for item in [*shown, detail])) > NOTICE_TOKENS - 150:
            break
        shown.append(detail)
    tail = f"；另有 {len(rows) - len(shown)} 个窗口，用 list_unread(offset={len(shown)}) 查看" if len(rows) > len(shown) else ""
    source_lines = [f"{source['name']} key={source['key']} 未读={source['remaining']} "
                    f"提及={source['mention_count'] - source['read_mention_count']} "
                    f"状态={source['state']}" + (f" 缺口={source['gap']}" if source['gap'] else "")
                    for source in independent[:3]]
    if len(independent) > 3:
        source_lines.append(f"另有 {len(independent) - 3} 个信源，用 source_status 查看")
    parts = [*(_unread_detail_text(detail) for detail in shown), tail, *source_lines]
    return bounded_excerpt("待正式读取：" + "；".join(part for part in parts if part), NOTICE_TOKENS)


def _message_cost(converted: dict) -> int:
    content = converted["content"]
    if isinstance(content, str):
        return count_tokens(content)
    return sum(count_tokens(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text")


def _stream_rows(window: tuple | None, token_limit: int | None,
                 event_limit: int | None) -> tuple[list[tuple[dict, dict]], int, bool]:
    """Select one visible suffix by event count and projected token cost."""
    entries = oplog.events(window)
    recalled_by_window: dict[tuple, set[str]] = {}
    links = oplog.say_links(window)
    picked: list[tuple[dict, dict]] = []
    used = 0
    blocked = False
    for entry in reversed(entries):
        if entry["kind"] == "notification" and not entry.get("acknowledged"):
            continue
        if event_limit is not None and len(picked) >= event_limit:
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
            if not entry["actions"]:
                continue
            converted = _output_projection(entry, include_body=False)
        elif entry["kind"] == "notification":
            converted = _notification_projection(entry)
        else:
            converted = _result_projection(entry, links)
        amount = _message_cost(converted)
        if token_limit is not None and used + amount > token_limit:
            blocked = True
            break
        picked.append((entry, converted))
        used += amount
    picked.reverse()
    return picked, used, blocked


def get_msgs(token_limit: int | None = None, return_token: bool = False):
    current = context.current() or {}
    max_events, max_tokens = limit(current)
    selected_limit = max_tokens if token_limit is None else token_limit
    rows, used, _blocked = _stream_rows(history.window(current), selected_limit, max_events)
    output = [converted for _entry, converted in rows]
    return (output, used) if return_token else output


def _chat_msgs() -> list[dict]:
    current = context.current() or {}
    max_events, max_tokens = limit(current)
    rows, _used, _blocked = _stream_rows(history.window(current), max_tokens, max_events)
    return [converted for entry, converted in rows if entry["kind"] == "input"]


def context_usage(turn=None) -> int:
    """Estimate the last request's actual stream view when a reader owns it."""
    if turn is None:
        turn = context.get_turn(history.window(context.current() or {}))
    captured = getattr(turn, "_chat_usage_tokens", None)
    if captured is not None:
        return captured
    return get_msgs(return_token=True)[1]


def usage_name(when: datetime | None = None) -> str:
    """The storage name for one month's usage: ``YYYY-MM``.

    WHY: 键必须带年份。裸月份把每一年的同一个月并进同一个文件，"去年九月"无从
    查起，多年数据还会被加在一起——这是 usage 数字失真的直接来源之一。
    """
    moment = when or datetime.today()
    return f"{moment.year}-{moment.month:02d}"


def _usage_entry() -> list | None:
    """The current LLM actor's ``[calls, cost]`` for the month.

    WHY: 中心 reader 的行动属于 Bot，不能记给刚好唤醒它的群友；独立 `.chat`
    仍是人类发起的单句请求，保留原作者账目。Bot 的 QQ 号作为中心账本键，
    旧月份的人类账目不迁移或重写。

    WHY: 私聊的顶层 `user_id` 是窗口对端，不一定是发起者；私有会话仍用
    `history.author` 判归属。没有可确定作者时不写入 "None" 键，否则 `.chattop`
    无法读回这笔费用。
    """
    if getattr(context, "agent_mode", lambda: False)():
        user_id = identity.bot_id()
    else:
        user_id = history.author(context.current() or {})
    if user_id is None:
        return None
    usage = storage.get("usage", usage_name())
    return usage.setdefault(str(user_id), [0, 0])


def inc_call_count() -> None:
    entry = _usage_entry()
    if entry is not None:
        entry[0] += 1


def inc_call_cost(model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0,
                  requested_at: datetime | None = None) -> None:
    """把一次调用的费用记到当前 LLM 主体账目。

    WHY: 单价、缓存命中价和峰谷档位全部来自模型/供应商元数据（见 llm.pricing），这里只
    负责取元数据、算钱、记账三件事。命中缓存的那部分必须单独算——聊天的 prompt 大多是
    重复上下文，一律按未命中价算会把费用高估一个数量级（实测同一段上下文第二次调用，
    845 个 prompt token 里有 640 是命中）。
    """
    _, _, attributes = llm.resolve_model(llm_config, model)
    provider = llm.provider_config(llm_config, model)
    inc_usage_cost(pricing.token_cost(provider, attributes, prompt_tokens, completion_tokens, cached_tokens, requested_at))


def inc_usage_cost(price: float) -> None:
    """Add one externally calculated cost to the current LLM actor's usage."""
    # A storage list is the authority; only this read-modify-write needs a lock.
    with _cost_lock:
        entry = _usage_entry()
        if entry is not None:
            entry[1] += price


def _base_prompt() -> list[dict]:
    return [{"role": "system", "content": f"""## 注意事项
- 你的昵称: {identity.bot_name()}
- 你的QQ号: {identity.bot_id()}；群聊 at 格式为 [CQ:at,qq=qq号]，reply 格式为 [CQ:reply,id=message_id]
- 你收到的消息原样带着这两种 CQ 码。reply 里的 message_id 与上文各条消息 <metadata> 中的 <message_id> 对应，据此判断对方在回复哪一条
- 聊天中可能不会有明显的问题，扮演好角色即可
- 如无特殊要求，请用中文回复
- **说话要调 `say`**。直接写在回复正文里的内容不会发出去，那是你这一轮的自言自语
- 眼前历史是唯一全局已读信息流按预算选出的可见部分，不是全部记录。通知不等于读取；先用 peek 预览，或用 pull_mail 逐页正式读取
- 想积累经验就实际写入以后会用的载体：可复用做法写 Markdown Skill 并按需加载，全局待办用 `edit_hint` 保存；只在回复里说“记住了”不会保存它
- 对外发送必须在 say 里明确写目标 g群号 或 u私聊对端号；没有默认接收窗口
- `say` 返回这条消息的 message_id；它默认 `final_call=true`，说完这一轮就结束，要接着干活就传 `final_call=false`"""}]


def _build_context_snapshot(token_limit: int | None = None) -> list:
    """Project the selected visible stream without consuming unread mail."""
    current = context.current() or {}
    max_events, max_tokens = limit(current)
    selected_limit = max_tokens if token_limit is None else token_limit
    rows, _used, _blocked = _stream_rows(history.window(current), selected_limit, max_events)
    return _close_with_user([converted for _entry, converted in rows])


def _numbered(converted: dict, event_id: str) -> dict:
    content = converted["content"]
    prefix = f"[{event_id}] "
    if isinstance(content, str):
        return {**converted, "content": prefix + content}
    return {**converted, "content": [{"type": "text", "text": prefix}, *content]}


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


def _output_projection(entry: dict, *, include_body: bool = True) -> dict:
    actions = "\n".join(f"{entry['id']}#{position + 1} {action['name']}({action['arguments']})"
                        for position, action in enumerate(entry["actions"]))
    body = entry["body"] if isinstance(entry["body"], str) else str(entry["body"])
    if not include_body:
        body = "（正文已省略）"
    return {"role": "user", "content": f"[{entry['id']}] 自己的输出：{body}\n{actions}"}


def _result_projection(entry: dict, links: dict[str, tuple[str, str]],
                       max_tokens: int = MAIL_PAGE_TOKENS) -> dict:
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
    if count_tokens(content) > max_tokens:
        marker = (f"\n结果未读完；用 recall_events(ids={[entry['id']]!r}, offset=0) "
                  "按字符偏移续读持久化的完整结果")
        content = bounded_excerpt(content, max(1, max_tokens - count_tokens(marker) - 20)) + marker
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

    WHY: 空 content 的 assistant 不算数。重建出来的工具轮 `content` 一律是空串（可见正文
    另在 chatlog 里），它照样是 assistant，照样要算进尾段。
    """
    if messages and messages[-1].get("role") == "user":
        return messages
    return [*messages, {"role": "user", "content": _CLOSING_NOTE}]


def init_chat(
    session: llm.Chat,
    messages: list | None = None,
) -> tuple[tool_modules.SessionBinding, tuple | None]:
    """Assemble one Chat and return its tool binding and window."""
    prompts["base"] = _base_prompt()
    group = context.current().get("group_id") if context.current() else None
    state = ({"role": "system", "content": "你是唯一的中心 agent；窗口只是来源与明确发送目标。"}
             if context.agent_mode() else
             ({"role": "system", "content": f"当前所在群聊:{identity.getgroupname(group)}({group})"}
              if group is not None else {"role": "system", "content": f"当前在私聊:{identity.getname()}({context.current().get('user_id')})"}))
    ui_mode = get_tools_mode() == "ui"
    offline = _offline_scope.get()
    tool_context = tool_modules.create_context_message(
        ui_mode=ui_mode, registry=offline["registry"] if offline else None)
    window = AGENT_WINDOW if context.agent_mode() else history.window(context.current() or {})
    session.set_messages([
        *get_prompt(),
        *prompts["base"],
        *([{"role": "system", "content": offline["fact"]}] if offline else []),
        tool_context,
        state,
        *(messages or []),
    ])
    # WHY: 激活态属于会话主体：主 reader 存全局 agent，独立 .chat 存窗口。每轮
    # 都新建一个 `llm.Chat`，激活只在内存里活着的话，下一轮模型就拿着上一轮
    # 装载过的名字去调用，而快照里没有——那个调用被丢掉、整轮直接结束，模型连自救的机会
    # 都没有（2026-09-17 `browser__open_page`）。读写在 `_active_modules`／
    # `_persist_modules`，理由写在那里。
    # WHY: 装回不是无限的：超过时限没用过的模块会在 `restore` 里被收掉，并给模型一条
    # 通告——"只进不出"会让每次 `load_tools` 都永久占着基线消息。判据用的是每个模块最后
    # 一次被调用的时刻，所以 bind 出来的那个对象要一直拿着，供 `_stream_results` 上报。
    # WHY: image's generation functions still infer an implicit current window;
    # the central agent has no such destination. Keep the whole module hidden
    # here without changing what independent .chat can explicitly load.
    binding = tool_modules.bind_session(
        session,
        tool_context,
        registry=offline["registry"] if offline else None,
        ui_mode=ui_mode,
        visible=(lambda name, module: name not in {
            "agents", "amap", "baidumap", "dianping", "image", "later"
        } and tool_modules.bot_op_tool_visible(name, module)) if context.agent_mode() and not offline else None,
        persist=_persist_modules(window) if window is not None else None,
    )
    return binding, window


def _activate_chat(
    session: llm.Chat,
    messages: list | None = None,
    *,
    read_mail: bool = False,
) -> tool_modules.SessionBinding:
    """Run the two lifecycle effects owned by one top-level activation."""
    # WHY: 调用计数与窗口工具恢复描述的是「Bot 被激活一次」，不是「有人调用了上下文装配
    # 函数」。把两者放在同一个入口后，mail 续读、`.chat` 与重启接续都明确经过它，单纯
    # 构造 Chat 则不产生生命周期副作用。顺序仍与迁移前一致：先计数，再装配，再恢复工具。
    inc_call_count()
    binding, window = init_chat(session, messages)
    session.reads_window_mail = read_mail
    _restore_window_tools(binding, window)
    if window is not None:
        session.add_hint(lambda: _agent_hint(window))
    session.add_hint("对外说话必须实际调用 say；回复正文只是自言自语，不会发送到聊天窗口。")
    # WHY: 工具恢复可能持久化 ttl 回收；它必须先于其余窗口设置读取，避免无关的配置异常
    # 改变这一轮是否完成回收。
    session.do_process_image = get_image_mode() != "off"
    session.keep_reasoning = get_reasoning_mode() == "keep"
    # WHY: `.chat` 只接受单句输入，不读取窗口 mail；它的下次请求必须从原生
    # 工具配对得到同步返回。只有装了 mail reader 的主会话才能安全撤掉原生载体。
    session.on_output = (lambda assistant, calls: _record_output(window, assistant, calls, session)
                         if read_mail else oplog.output(window, assistant, calls))
    session.on_results = _stream_results(window, binding) if read_mail else _private_results(window, binding)
    return binding


def _restore_window_tools(binding, window: tuple | None) -> None:
    """把本窗口已激活的工具模块装回本次激活；空闲回收挂在同一个动作上。

    WHY: 这一步**不**再交给 `bind_session` 的 `initial_modules` 参数顺带做，虽然那样少一
    行。装回是一个**生命周期动作**，不是装配的一部分：它发生在「顶层激活」这个时刻，而
    空闲回收——超过 ttl 没被装入或调用过的模块在这里被收掉，见 `tools.SessionBinding.
    restore`——挂的是同一个时刻。`_activate_chat` 是唯一调用点，`init_chat` 只负责装配。

    WHY: `tools/agents.py` 那条路仍然走 `bind_session(initial_modules=...)`，不跟着改，
    因为它传的是**名字列表**而不是 `{名字: 时刻}`：`restore` 于是把每个名字的时刻都当成
    now，空闲回收在那条路上恒为空操作。子代理只借用「静默装回、不发通告」，没有生命周期
    含义，把它也卷进来只会让接管时机的那一步多一个不相干的调用点。

    WHY: 空映射时不调用，**这个条件是照搬的**，不是新加的判断。核实过它此刻
    并不承重：刚 bind 完 `_dirty` 是 False，空输入下 `kept == requested == []`，所以
    `restore` 既不会 `_save_active` 也不会 `_queue_reclaimed`，只是把 `_render_context`
    幂等地重算一遍。继续保留这个条件，是为了只迁移副作用的归属，不同时改变空名单语义。
    """
    modules = _active_modules(window) if window is not None else {}
    if modules:
        binding.restore(modules)


def get_handler(session: llm.Chat):
    """The per-chunk sink: self-talk to the terminal, cost to the ledger.

    WHY: 模型写在回复正文里的内容**不再发进聊天**。发言是一次 `say` 调用（见
    `tools/meta.py`），正文因此退化成这一轮的自言自语：同轮可见，完整输出事件
    留存以供反查，但后续轮次的模型视图不载入正文，也不进 chatlog。模型下一轮
    看不到自己想过什么，是刻意的。

    WHY: 但它要打到终端。人得看得见模型在想什么，尤其是在它**忘了调 `say`**的时候——那
    种轮对聊天窗口是完全静默的，终端这一行是唯一的痕迹。用 msg 流而不是另开一个，是为了
    和 `bot._route`、`message._chatlog_write` 的回显共用同一把行租约，终端顺序才不会乱。

    WHY: 这里不做兜底发送。"正文非空却没调 say 就替它发出去"会把刚删掉的那条旁路原样装
    回来，而且是隐式的——模型会学会不调 say 照样能说话，`final_call` 那套终止语义随之失效。
    宁可静默一轮、在终端留下证据。
    """
    def handle(chunk: llm.LLMResponse) -> None:
        offline = _offline_scope.get()
        if offline is not None:
            offline["on_chunk"](chunk)
        if chunk.role == "assistant" and chunk.content:
            _self_talk.info(f'[{time.strftime("%H:%M:%S")}]【自言自语】{chunk.content}')
        if chunk.total_tokens:
            inc_call_cost(session.model, chunk.prompt_tokens, chunk.completion_tokens, chunk.cached_tokens, chunk.requested_at)

    return handle


def _stream_results(window, binding):
    def record(source: str, results: list[llm.ToolCallResult]) -> None:
        for result in results:
            binding.touch(result.name)
        if window is not None:
            for position, result in enumerate(results):
                context.mailbox(window).add({"_stream_results": {
                    "source": source,
                    "returns": [{"position": position, "name": result.name,
                                 "arguments": result.arguments, "content": result.content,
                                 "tool_call_id": result.tool_call_id}],
                }})
    return record


def _private_results(window, binding):
    def record(source: str, results: list[llm.ToolCallResult]) -> None:
        for position, result in enumerate(results):
            binding.touch(result.name)
            if window is not None:
                oplog.result(window, source, [{"position": position, "name": result.name,
                                              "arguments": result.arguments, "content": result.content,
                                              "tool_call_id": result.tool_call_id}], "")
    return record


def _record_output(window, assistant: dict, calls: list[dict], session=None) -> tuple[str, dict] | None:
    source = oplog.output(window, assistant, calls)
    if source is None:
        return None
    entry, missing = oplog.recall_events(window, [source])
    if missing:
        raise RuntimeError("刚写入的模型输出无法反查")
    projection = _output_projection(entry[0])
    if session is not None:
        _remember_stream(session, projection, source)
    return source, projection


def _condense_projection(messages: list[dict], window: tuple, sources: set[str]) -> int:
    prefixes = tuple(f"[{source}] 自己的输出：" for source in sources)
    result_prefixes = tuple(f"[{entry['id']}] 行动返回：" for entry in oplog.events(window, True)
                            if entry["kind"] == "result" and entry["source"] in sources)
    prefixes += result_prefixes
    kept = [message for message in messages if not (isinstance(message.get("content"), str)
            and message["content"].startswith(prefixes))]
    removed = len(messages) - len(kept)
    messages[:] = kept
    return removed


def _trusted_stream_ids(session: llm.Chat) -> set[str]:
    _prune_stream_ids(session)
    return {event_id for message in session.messages
            if (event_id := _stream_id(session, message)) is not None}


def _remember_stream(session: llm.Chat, message: dict, event_id: str) -> None:
    session.stream_ids[id(message)] = (message, event_id)


def _stream_id(session: llm.Chat, message: dict) -> str | None:
    remembered = session.stream_ids.get(id(message))
    return remembered[1] if remembered is not None and remembered[0] is message else None


def _prune_stream_ids(session: llm.Chat) -> None:
    live = {id(message) for message in session.messages}
    session.stream_ids = {key: pair for key, pair in session.stream_ids.items()
                          if key in live}


def _cover_agent_projection(session: llm.Chat, members: set[str]) -> None:
    session.messages[:] = [message for message in session.messages
                          if _stream_id(session, message) not in members]
    _prune_stream_ids(session)


def _visible_stream_ids(messages: list[dict]) -> set[str]:
    visible = set()
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        first = content[0].get("text", "") if isinstance(content, list) and content and isinstance(content[0], dict) else content
        match = re.match(r"^\[(\d{8}-[1-9]\d*)\] ", first) if isinstance(first, str) else None
        if match:
            visible.add(match[1])
    return visible


def _pressure_hint(used_tokens: int, max_tokens: int, threshold: int) -> str:
    if used_tokens * 100 <= max_tokens * threshold:
        return ""
    return f"上下文占用 {used_tokens}/{max_tokens} token"


def _cover_projection(messages: list[dict], members: set[str]) -> None:
    messages[:] = [message for message in messages
                   if not (_visible_stream_ids([message]) & members)]


def _mail_context(entries: list[context.MailEntry], in_group: bool, window: tuple,
                  max_tokens: int = MAIL_PAGE_TOKENS) -> list[dict]:
    """Project a global mailbox of tool results, never a window's chat mail."""
    output = []
    for entry in entries:
        event = entry.event
        values = event["_stream_results"]
        recorded = oplog.read(entry.arrival) or oplog.result(window, values["source"], values["returns"], entry.arrival)
        if recorded is not None and recorded["id"] not in oplog.covered(window) and not recorded.get("condensed"):
            output.append(_result_projection(recorded, oplog.say_links(window), max_tokens))
    return output


_boot_sources: list[str] = []


def prepare_recovery_sources() -> None:
    """Freeze archive anchors and FIFO boundaries before live ingress opens."""
    from mods import chatlog

    if _boot_sources:
        return
    for window, anchor in chatlog.freeze_boot_anchors().items():
        anchor_time = None
        try:
            latest = chatlog.read_range(*window, limit=1) if anchor is not None else []
            anchor_time = (latest[0].get("time") if latest and
                           str(latest[0].get("message_id")) == str(anchor) and
                           type(latest[0].get("time")) is int else None)
        except Exception:
            traceback.print_exc()
        try:
            source = oplog.start_source("NapCat " + str(window), window, "napcat_boot",
                                        anchor=anchor, anchor_time=anchor_time)
            _boot_sources.append(source["key"])
        except Exception:
            traceback.print_exc()


def _recovery_sources(window: tuple) -> list[dict]:
    return [source for source in oplog.sources()
            if tuple(source["queue_window"]) == window
            and source["source_type"] == "napcat_boot"]


def fetch_remote_source(window: tuple) -> dict:
    """Extend an unpulled gap or open a separate historical FIFO."""
    from mods import _backfill, connect

    candidates = [source for source in oplog.sources()
                  if tuple(source["window"]) == window
                  and source["source_type"] in ("napcat_boot", "napcat_history")]
    latest = candidates[-1] if candidates else None
    if latest is not None and latest["state"] == "fetching":
        return latest
    if latest is not None and latest["state"] in ("gap", "failed") and not latest["pulled"]:
        source = oplog.reopen_source(latest["key"], fetch_anchor=latest["fetch_anchor"])
    else:
        target = ("g" if window[0] == "group" else "u") + str(window[1])
        source = oplog.start_source("NapCat 历史 " + target, window, "napcat_history",
                                    queue_window="new",
                                    start_seq=latest["stop_cursor"] if latest else None)

    def fetch() -> None:
        try:
            _backfill.recover_source(source, connect.call_api, manual=True)
        except Exception:
            traceback.print_exc()
            try:
                state = oplog.resolve_source(source["key"])
                oplog.finish_source(source["key"], gap="本地归档或入列失败；可重试",
                                    stop_cursor=state["cursor"])
            except Exception:
                traceback.print_exc()

    threading.Thread(target=fetch, name="napcat-source-fetch", daemon=True).start()
    return source


def _read_source_page(window: tuple, source: dict, session: llm.Chat | None) -> tuple[list[dict], str | None]:
    from mods import chatlog

    page_number = source["read_page"]
    if page_number is None or page_number < 0:
        return [], None
    members = _source_pages.read_page(oplog.source_page_root(), source["key"], page_number)
    output = []
    used = 0
    for offset in range(source["read_offset"], len(members)):
        if offset - source["read_offset"] >= MAIL_PAGE_EVENTS:
            break
        member = members[offset]
        origin = member["origin"]
        event = chatlog.read_origin(*window, origin)
        if event is None:
            return output, f"补回档案位置已丢失：{origin}"
        event["_history_source"] = "napcat_backfill"
        event["_history_seq"] = member.get("message_seq")
        converted = _model_event(event, window[0] == "group")
        amount = _message_cost(converted) if converted is not None else 0
        if used + amount > MAIL_PAGE_TOKENS - 300:
            if output:
                break
            if converted is None:
                return [], "队首补回记录无法投影"
            excerpt = bounded_excerpt(json.dumps(converted["content"], ensure_ascii=False),
                                      MAIL_PAGE_TOKENS - 600)
            converted = {"role": "user", "content":
                         f"来源窗口={window} origin={origin}；补回消息过长，先读片段：{excerpt}；"
                         f"用 peek(origin={origin!r}) 继续"}
        if offset + 1 < len(members):
            next_page, next_offset = page_number, offset + 1
        else:
            next_page = next((page for page in range(page_number - 1, -1, -1)
                              if source["page_counts"][page]), -1)
            next_offset = 0
        recorded = context.mailbox(window).commit_recovered(
            member["message_id"], member["time"],
            lambda arrival: oplog.input_source(AGENT_WINDOW, event, converted, source["key"],
                                               page_number, offset, next_page, next_offset,
                                               origin, window, arrival=arrival,
                                               mentioned=member.get("mentioned", False)),
            event_seq=member.get("message_seq"))
        if converted is not None:
            projected = _echo_relation(_numbered(converted, recorded["id"]), recorded,
                                       oplog.say_links(AGENT_WINDOW))
            output.append(projected)
            if session is not None:
                _remember_stream(session, projected, recorded["id"])
        used += _message_cost(converted) if converted is not None else 0
    return output, None


def _read_mail_page(window: tuple, session: llm.Chat | None = None,
                    through: str | None = None) -> tuple[list[dict], str | None]:
    """Read one bounded FIFO page, projecting an oversized archived head by prefix."""
    box = context.mailbox(window)
    sources = _recovery_sources(window)
    if not sources and _offline_scope.get() is None:
        box.prepare_initial_page(MAIL_PAGE_EVENTS)
    elif any(source["state"] == "fetching" for source in sources):
        return [], "该窗口离线补回仍在进行；mail 尚未开放正式拉取"
    prefix = []
    for source in sources:
        prefix = box.unread_through(source["pending_boundary"], MAIL_PAGE_EVENTS)
        if prefix:
            break
        if source["remaining"]:
            return _read_source_page(window, source, session)

    def project(entries: list[context.MailEntry]) -> list[dict]:
        selected = []
        used = 0
        for entry in entries:
            event = entry.event
            if "_stream_results" in event:
                converted = {"role": "user", "content": str(event["_stream_results"]["returns"])}
            else:
                located = {**event, "_log_origin": oplog.arrival_origin(entry.arrival), "_live": True}
                converted = _model_event(located, window[0] == "group")
            if converted is None:
                selected.append((entry, None))
                continue
            amount = _message_cost(converted)
            if used + amount > MAIL_PAGE_TOKENS - 300:
                break
            selected.append((entry, converted))
            used += amount
        if not selected and entries:
            raise ValueError("队首消息超过单页 token 上限；未消费，请用有界 peek 检查原文")
        projections = []
        for entry, converted in selected:
            event = entry.event
            if "_stream_results" in event:
                values = event["_stream_results"]
                recorded = oplog.read(entry.arrival) or oplog.result(
                    AGENT_WINDOW, values["source"], values["returns"], entry.arrival)
                projected = _result_projection(recorded, oplog.say_links(AGENT_WINDOW),
                                               RESULT_ITEM_TOKENS)
                projections.append(projected)
                if session is not None:
                    _remember_stream(session, projected, recorded["id"])
                continue
            recorded = oplog.read(entry.arrival) or oplog.input(
                AGENT_WINDOW, event, converted, entry.arrival, source_window=window)
            if converted is not None and recorded is not None:
                projected = _echo_relation(_numbered(converted, recorded["id"]), recorded,
                                           oplog.say_links(AGENT_WINDOW))
                projections.append(projected)
                if session is not None:
                    _remember_stream(session, projected, recorded["id"])
        return projections, len(selected)

    try:
        entries = prefix if prefix else box.unread(MAIL_PAGE_EVENTS)
        if through is not None:
            entries = [entry for entry in entries if oplog.arrival_before_or_at(entry.arrival, through)]
        if not entries:
            return [], None
        selected = []
        used = 0
        for entry in entries:
            if "_stream_results" in entry.event:
                converted = {"role": "user", "content": str(entry.event["_stream_results"]["returns"])}
            else:
                located = {**entry.event, "_log_origin": oplog.arrival_origin(entry.arrival), "_live": True}
                converted = _model_event(located, window[0] == "group")
            amount = _message_cost(converted) if converted is not None else 0
            if used + amount > MAIL_PAGE_TOKENS - 300:
                break
            selected.append(entry)
            used += amount
        if not selected:
            from mods import chatlog

            head = entries[0]
            origin = oplog.arrival_origin(head.arrival)
            if "_stream_results" in head.event or not origin:
                return [], "队首消息超过单页上限且缺少可续读档案；未消费"
            archive = chatlog.read_origin(*window, origin)
            if archive is None:
                return [], "队首消息的精确档案不可读取；未消费"
            converted = _model_event(archive, window[0] == "group")
            if converted is None:
                return [], "队首消息的档案投影不可读取；未消费"
            rendered = json.dumps(converted["content"], ensure_ascii=False)
            excerpt = bounded_excerpt(rendered, MAIL_PAGE_TOKENS - 400)
            if not excerpt:
                return [], "队首消息的档案位置过长；未消费"
            target = ("g" if window[0] == "group" else "u") + str(window[1])
            next_offset = len(excerpt)
            continuation = (f"内容未读完；用 peek(target={target!r}, origin={origin!r}, "
                            f"offset={next_offset}) 继续" if next_offset < len(rendered)
                            else "该归档投影已经读完")
            partial = {"role": "user", "content":
                       f"来源窗口={target} origin={origin} arrival={head.arrival}；"
                       f"队首超长消息的正式归档读取片段：{excerpt}\n{continuation}"}
            if _message_cost(partial) > MAIL_PAGE_TOKENS - 100:
                return [], "队首消息的档案位置过长；未消费"

            def project_partial(crossed: list[context.MailEntry]) -> list[dict]:
                if len(crossed) != 1 or crossed[0].arrival != head.arrival:
                    raise ValueError("待读队首已变化；未消费")
                recorded = oplog.input(AGENT_WINDOW, head.event, partial, head.arrival,
                                       source_window=window)
                if recorded is None:
                    raise ValueError("队首归档读取未能持久化；未消费")
                projected = _echo_relation(_numbered(partial, recorded["id"]), recorded,
                                           oplog.say_links(AGENT_WINDOW))
                if session is not None:
                    _remember_stream(session, projected, recorded["id"])
                return [projected]

            return box.pull(1, project_partial), None
        projected, _count = box.pull(len(selected), project)
        return projected, None
    except ValueError as error:
        return [], str(error)


def _window_storage(window: tuple) -> dict:
    """取本窗口自己的 chat storage，键就是 `history.window(...)`（`#hint` 和工具激活共用）。

    WHY: 不经过 `context.current()`——hint 在 `chat` 的 `finally` 里跑，那个窗口就是调用方
    手上的实参；由实参决定"哪个窗口"，触发点就不依赖线程局部的当前事件，也不跟捕获、派发
    的细节绑在一起。工具激活走同一个理由：`_activate_chat` 手上的 window 就是它的窗口。
    命名空间与 getchatstorage 同一套。
    """
    if window == AGENT_WINDOW:
        return storage.get("", "agent")
    kind, key = window
    return storage.get("groups" if kind == "group" else "users", str(key))


_AGENT_HINT_KEY = "agent_hint"


def _agent_hint(window: tuple) -> str:
    value = _window_storage(window).get(_AGENT_HINT_KEY)
    if not isinstance(value, str) or not value.strip():
        return ""
    return f"待办（你用 edit_hint 保存，可整体替换或清空）：\n{value}"


def set_agent_hint(window: tuple, text: str) -> None:
    data = _window_storage(window)
    if text.strip():
        data[_AGENT_HINT_KEY] = text.strip()
    else:
        data.pop(_AGENT_HINT_KEY, None)
    storage.save()


# 会话主体持久激活的工具模块名，以及各自最后一次被调用的时刻。别和 WINDOW_SETTINGS 里的
# "tools"（工具状态呈现方式）混用，两者住在同一个 storage 字典里。
_ACTIVE_MODULES_KEY = "active_tools"


def _active_modules(window: tuple) -> dict[str, float]:
    """本会话主体上次装着哪些工具模块、各自最后一次被调用是什么时候。

    WHY: 值是使用时刻，`tools.SessionBinding.restore` 靠它决定哪些模块已经空闲太久、
    该在这一轮收掉。旧格式（只存名字的列表）一律当成"就是刚才用过"——那是这份格式之前
    留下的，给它一个完整时限比让它立刻消失更不容易误伤。
    """
    value = _window_storage(window).get(_ACTIVE_MODULES_KEY)
    if isinstance(value, dict):
        return {
            name: float(stamp)
            for name, stamp in value.items()
            if isinstance(name, str) and isinstance(stamp, (int, float))
        }
    if isinstance(value, list):
        return {name: time.time() for name in value if isinstance(name, str)}
    return {}


def _persist_modules(window: tuple):
    """给 `SessionBinding` 的回调：把会话主体的激活集合写回 storage。

    WHY: 主 reader 的激活是**全局主体**状态，私有 .chat 才按窗口存；都不是单轮状态。
    每轮都新建一个 `llm.Chat`，
    激活如果只活在内存里，模型下一轮会照上一轮装载过的名字去调用（操作历史轨道把那几次
    `load_tools` 原样重建进了上下文），而那一轮的快照里没有这个名字——`llm` 解析时
    `mapping[name]` 抛 KeyError，整个调用被丢掉，那一轮连一条 tool 结果都没有就结束了
    （2026-09-17 群里 `browser__open_page` 那次）。所以要写在这里：模型改一次，这一
    份就更新一次，下一轮开局原样装回去。

    WHY: 空字典就删键，不留 `{}`。storage 里没有这个键就是"没激活过"，和空字典是一回事，
    少一个需要解释的状态。

    WHY: 值是使用时刻而不是只有名字，见 `_active_modules`；空闲回收在 `tools` 那层判，
    这里只负责如实来回搬。
    """
    def save(stamps: dict[str, float]) -> None:
        data = _window_storage(window)
        if stamps:
            data[_ACTIVE_MODULES_KEY] = {name: float(stamp) for name, stamp in stamps.items()}
        else:
            data.pop(_ACTIVE_MODULES_KEY, None)
    return save


def _hint_effective(default: dict, chat_hint: dict | None) -> dict:
    """生效配置：`{**default, **chat_hint}`——窗口的覆盖默认的，只读 `code`/`on`。

    WHY: 就这一句合并，没有别的间接层。窗口只写 `on` 也是合法配置——那样它仍继承默认的
    `code`，只是把自己单独关掉；`on` 缺省当作 False，所以只写了 `code` 的配置不会生效。
    """
    return {**default, **(chat_hint if isinstance(chat_hint, dict) else {})}


def _run_hint(window: tuple, turn=None) -> None:
    """求值本窗口的结束提示，并把结果发出去；触发点写在 `chat` 的 `finally`。

    WHY: 唯一信号是"循环停下"：`while` 里每个 `return` 和异常都经过 `finally`，而每轮
    `_run_agent` 返回时不经过，所以不会每句都刷；`if not owner:` 的早退和没有来源窗口的
    恢复轮也不发送窗口结束提示，于是"真的结束"只算一次、也只由这一轮的持有者来做。

    WHY: 求值与发送的任何异常都吞掉、只写日志，绝不抛回 `finally`——这段代码是用户自己
    写的、每次聊天都自动跑，让它抛出去就等于一段烂代码能污染聊天主流程的返回路径。
    """
    try:
        merged = _hint_effective(storage.get("", "hint"), _window_storage(window).get("hint"))
        code = merged.get("code")
        if not merged.get("on", False) or not isinstance(code, str) or not code.strip():
            return
        result = _hint_evaluate(code, window, turn)
        if result is not None:
            # `#` 前缀让结束提示不回流进 LLM 上下文，见 get_msgs 的说明。
            message.sendmsg("#" + cq.escape(str(result)))
    except Exception:
        _report_hint_failure()


def _hint_evaluate(code: str, window: tuple, turn=None):
    """在 `py.loc` 的一份私用副本里跑一次 *code*，返回末行的值。

    WHY: 名字要照旧认（`sendmsg`/`storage`/…都在），痕迹不能留——副本 + 单次求值就够了：
    `window`/`usage` 是这一次临时的，代码里的赋值也只落进副本，`py.loc` 一个键都不会多。
    仍走 `py.eval_last`，于是「末行是表达式才发」和 Traceback 指回作者那几行都不变。
    """
    namespace = dict(py.loc)
    namespace["window"] = window
    namespace["usage"] = context_usage(turn)
    return py.eval_last(code, namespace)


def _report_hint_failure() -> None:
    """照 link._report_error 的惯例，把 traceback 用 `#` 前缀发出去。"""
    _hint_stream.exception("hint 执行失败")
    try:
        # `#` 前缀让 traceback 不回流进 LLM 上下文，见 get_msgs 的说明。
        message.sendmsg("#" + "".join(traceback.format_exc().splitlines(True)[3:]).strip())
    except Exception:
        _hint_stream.exception("hint 的错误报告也发不出去")


def chat(model: str | None = None) -> None:
    event = context.current() or {}
    window = history.window(event)
    if window is None:
        return
    _drive_agent(model, window)


def _drive_agent(model: str | None, window: tuple) -> None:
    turn, owner = context.begin_turn(AGENT_WINDOW)
    if not owner:
        return
    turn._chat_usage_tokens = 0
    turn.requested_pulls = []
    turn.blocked_windows = set()
    turn.associated_windows = {window}
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
                    return
            finally:
                context.set_agent_mode(False)
                context.set_current(origin)
            if turn.cancelled:
                return
            turn.associated_windows.intersection_update(
                set(oplog.work_windows(AGENT_WINDOW))
                | {window for window, _count, active in oplog.pending_summary() if active}
                | {window for window, _through in turn.requested_pulls})
            if not context.finish_turn(AGENT_WINDOW, turn,
                                       lambda: bool(turn.mail.unread()) or oplog.has_unnotified()
                                       or bool(turn.requested_pulls)
                                       or any(window not in turn.blocked_windows
                                              for window in oplog.work_windows(AGENT_WINDOW))):
                return
    finally:
        image.end_conversation(image_ledger)
        context.end_turn(AGENT_WINDOW, turn)
        # WHY: 循环停下的唯一信号就在这里，见 _run_hint。
        if origin is not None:
            _run_hint(window, turn)


def _run_agent(model: str | None, turn) -> bool:
    offline = _offline_scope.get()
    if offline is not None and model != offline["model"]:
        raise RuntimeError("offline replay model changed")
    session = llm.Chat(model=model or get_model(), chat_client=llm.get_client())
    if offline is not None:
        session.fail_fast = True
    session.stream_ids = {}
    session.recall_offsets = {}
    session.recalled_legacy_ids = set()
    session.requested_pulls = turn.requested_pulls
    session.associated_windows = turn.associated_windows
    session.notification_ids = []
    max_events, max_tokens = limit()
    # WHY: 离线回放从空种子开始，正式读到的内容只能由模型 cover；若每次重建 Chat
    # 仍取近期后缀，实验会被滑窗偷偷救活，测到的就不是自主整理。生产路径继续使用两个
    # 上限，只有显式 offline scope 投影全部仍可见事件，直到模型整理或真实请求失败。
    rows, _used, _blocked = _stream_rows(
        AGENT_WINDOW,
        None if offline is not None else max_tokens,
        None if offline is not None else max_events,
    )
    messages = []
    for entry, projection in rows:
        messages.append(projection)
        _remember_stream(session, projection, entry["id"])
    notice = oplog.deliver_notifications(AGENT_WINDOW)
    if notice is not None:
        session.notification_ids.append(notice["id"])
        turn.associated_windows.update(map(tuple, notice["windows"]))
        projection = _notification_projection(notice)
        messages.append(projection)
        _remember_stream(session, projection, notice["id"])
    if not turn.requested_pulls:
        work = [(window, through) for window, through in oplog.work_targets(AGENT_WINDOW).items()
                if window not in turn.blocked_windows]
        if notice is None and work:
            turn.requested_pulls.append(work[0])
    if not messages and not turn.requested_pulls and not turn.mail.unread():
        return True
    _activate_chat(session, _close_with_user(messages), read_mail=True)
    session.output_recorded = False

    def record_output(assistant, calls):
        recorded = _record_output(AGENT_WINDOW, assistant, calls, session)
        for event_id in session.notification_ids:
            oplog.acknowledge_notification(event_id)
        session.notification_ids.clear()
        session.output_recorded = True
        return recorded

    session.on_output = record_output
    session.add_context_provider(_agent_provider(turn, session))
    session.add_hint(_pending_hint)
    session.add_hint(lambda: _pressure_hint(turn._chat_usage_tokens, limit()[1],
                                            window_setting("pressure_percent")))
    session.should_stop = lambda: turn.cancelled
    session.chat(recall_func=get_handler(session), description_cache=description_cache)
    return session.output_recorded


def _credit_recall(session: llm.Chat, result: dict, projection: dict) -> None:
    if result["name"] != "recall_events" or result["content"] not in projection["content"]:
        return
    try:
        arguments = json.loads(result["arguments"] or "{}")
        ids = arguments["ids"]
        offset = arguments.get("offset", 0)
        if not isinstance(ids, list) or not isinstance(offset, int) or offset < 0:
            return
        key = tuple(str(event_id) for event_id in ids)
        if offset == 0:
            session.recall_offsets[key] = 0
        if session.recall_offsets.get(key) != offset:
            return
        continuation = re.search(r"\n结果未读完；相同 ids 继续 recall_events\(offset=(\d+)\)$",
                                 result["content"])
        if continuation is not None:
            session.recall_offsets[key] = int(continuation.group(1))
            return
        found, _missing = oplog.recall_events(AGENT_WINDOW, key)
        session.recalled_legacy_ids.update(item["id"] for item in found)
    except (KeyError, TypeError, ValueError):
        return


def _agent_provider(turn, session: llm.Chat):
    def provide() -> list[dict]:
        try:
            def project_results(entries: list[context.MailEntry]) -> list[dict]:
                projections = []
                item_budget = max(100, (MAIL_PAGE_TOKENS - 300) // len(entries))
                for entry in entries:
                    for projection in _mail_context([entry], False, AGENT_WINDOW,
                                                    item_budget):
                        recorded = oplog.read(entry.arrival)
                        if recorded is not None:
                            _remember_stream(session, projection, recorded["id"])
                        for result in entry.event["_stream_results"]["returns"]:
                            _credit_recall(session, result, projection)
                        projections.append(projection)
                return projections

            # WHY: Batch only results already present at this request boundary;
            # never wait for future tools, and reserve one shared page budget.
            unread_results = turn.mail.unread(RESULT_PAGE_EVENTS)
            produced = (turn.mail.pull(len(unread_results), project_results)
                        if unread_results else [])
            notice = oplog.deliver_notifications(AGENT_WINDOW)
            new_notice = notice is not None and notice["id"] not in session.notification_ids
            if new_notice:
                session.notification_ids.append(notice["id"])
                turn.associated_windows.update(map(tuple, notice["windows"]))
                projection = _notification_projection(notice)
                produced.append(projection)
                _remember_stream(session, projection, notice["id"])
            # WHY: pull_mail 的短结果本身也会先进 result mail；若要求 result 与通知都为空
            # 才兑现请求，模型每次重试都会再制造一个结果，承诺的“下一次请求前读取”便会
            # 永久饥饿。三类输入各自已有页上限，所以同一 boundary 先交付结果/通知、再兑现
            # 至多一页 pull，仍然有界。
            if turn.requested_pulls:
                window, through = turn.requested_pulls.pop(0)
                if window[0] == "source":
                    source = oplog.resolve_source(window[1])
                    if source is None:
                        page, error = [], "信源不存在"
                    elif source["state"] == "fetching":
                        page, error = [], "信源仍在拉取；FIFO 前端尚未固定"
                    else:
                        original_window = tuple(source["window"])
                        turn.associated_windows.add(original_window)
                        page, error = _read_source_page(original_window, source, session)
                        if error is None and oplog.resolve_source(source["key"])["read_count"] > source["read_count"]:
                            oplog.mark_source_pulled(source["key"])
                else:
                    turn.associated_windows.add(window)
                    page, error = _read_mail_page(window, session, through)
                produced.extend(page)
                if error:
                    if _offline_scope.get() is not None:
                        raise llm.RequiredContextError(f"离线回放读取失败：{error}")
                    turn.blocked_windows.add(window)
                    produced.append({"role": "user", "content": f"{window} 拉取失败：{error}"})
            turn._chat_usage_tokens = sum(
                _message_cost(message) for message in [*session.messages, *produced]
                if _stream_id(session, message) is not None
            )
            return produced
        except Exception as error:
            raise llm.RequiredContextError("读取中心信息流失败，已停止后续行动") from error
    return provide


_SUBCOMMAND_HELP = (
    ("help [name]", "显示子命令目录或某条子命令的完整说明。\n格式：#help | #help <名称>"),
    ("model", "查看当前模型"),
    ("model <selection>", "查看指定模型信息"),
    ("models", "列出当前供应商的模型（优先在线列表）"),
    ("use_model [selection]", "设置或重置当前模型"),
    ("agent [model|use_model|limit|use_setting|ops]", "查看或设置中心 agent 的全局模型、预算、设定与操作记录（管理员）；旧窗口覆盖不自动并入"),
    ("prompt", "查看当前提示词"),
    ("add_prompt [count|list]", "追加聊天或给定提示词"),
    ("setting [name]", "列出或查看设定"),
    ("use_setting [name]", "应用或重置设定"),
    ("set_setting <name> [list]", "保存当前或给定设定"),
    ("del_setting <name>", "删除设定"),
    ("image [off|lazy|eager]", "查看或设置图片读取档位"),
    ("reasoning [keep|drop]", "查看或设置工具循环内是否带回思考内容"),
    ("tools [append|ui]", "查看或设置工具状态的呈现方式"),
    ("ops [clear]", "查看或清空本窗口的操作历史"),
    ("limit [<事件数> <token> [提醒百分比]|reset]", """查看或设置本窗口的可见事件数、上下文 token 上限与提醒阈值（管理员）。

格式：#limit | #limit <事件数> <token> [提醒百分比] | #limit reset
两个上限共同裁剪近期已读输入、输出与工具返回；提醒百分比只决定模型末尾何时显示上下文 token 用量（已用/上限），不改变显示格式。默认值分别为 20、50000、75%；旧 max_msg 值仅在没有 max_events 时读取。
#limit                  显示两个上限和提醒百分比，并标出值来自本窗口还是默认
#limit <事件数> <token> [提醒百分比] 写入本窗口的上限；省略百分比则保留原设置
#limit reset            清掉本窗口的上限与提醒百分比，回落到默认
未读 mail 不受历史限额丢弃；本设置是旧窗口配置，不控制中心 agent。"""),
    ("hint [get|set|default]", """查看、编写或开关本窗口的结束提示（管理员）。

格式：#hint | #hint get | #hint set <代码> | #hint set | #hint default [get|set <代码>]
本窗口的配置在 chat storage 的 hint 键，全局默认在 storage 的 "" 命名空间；生效的是两者按 {**默认, **窗口} 合并之后 code 非空、on 为真的那份。
聊天循环停下时求值一次，非 None 的结果作为一条消息发出（自带 # 前缀，不进模型上下文）。
求值环境是共享动态环境，另外注入 window（触发窗口）与 usage（中心 reader 最近一次子请求实际收到的已编号事件文本 token 估算；不含系统提示与工具 schema）。
#hint              切换本窗口的开关（只写本窗口）
#hint get          显示合并后生效的代码与开关，并标出代码来自哪里
#hint set <代码>   写入本窗口的代码并打开开关；set 之后第一个换行起即为源码
#hint set          清掉本窗口配置，回落到全局默认
#hint default      切换全局默认的开关
#hint default get  显示全局默认的代码与开关
#hint default set <代码>  写入全局默认的代码并打开开关"""),
)
_SUBCOMMAND_NAMES = {pattern.partition(" ")[0] for pattern, _description in _SUBCOMMAND_HELP}


def _subcommand_help(name: str = "") -> str:
    if not name:
        # WHY: 每行自带 `#`，用户可以直接照抄；`call()` 又会给整条消息补一个 `#`，所以
        # 把生成的第一个字符空出来，免得渲染成 `##help`。
        lines = [
            f"#{pattern} — {description.splitlines()[0]}"
            for pattern, description in _SUBCOMMAND_HELP
        ]
        lines[0] = lines[0][1:]
        return "\n".join(lines)
    matched = [
        description
        for pattern, description in _SUBCOMMAND_HELP
        if pattern.partition(" ")[0] == name
    ]
    if not matched:
        return "该命令不存在！"
    return "\n".join(matched)


_MODEL_TABLE_HEADER = "模型 输入(未命中/命中) 输出 (单位: 元/(1m token)，当前价) 视觉识别 函数调用"


def _format_model(selection: str, attributes: dict, when: datetime | None = None) -> str:
    if any(key in attributes for key in pricing.PRICE_KEYS):
        provider = llm.provider_config(llm_config, selection)
        prices = pricing.format_prices(pricing.unit_prices(provider, attributes, when))
    else:
        # 本地没有这条模型的元数据，不替对端猜价格（见 UNKNOWN_MODEL_CAPABILITIES）。
        prices = " / ".join("-" for _ in pricing.PRICE_KEYS)
    return f"{selection}\n    {prices} {'👀' if attributes.get('vision') else ''} {'⚙️' if attributes.get('function_calling') else ''}"


def _models_report(data: dict) -> str:
    """当前 provider 的模型列表：先问对端，取不到就用本地配置。

    WHY: 清单的权威在对端，本地 models 只是价格与能力元数据。对端列出而本地没有元数据的
    行只显示名字（价格为 ``-``、无能力标记），不替对端猜能力。
    """
    provider = llm.resolve_model(llm_config, get_model(data))[0]
    local = llm_config.get("providers", {}).get(provider, {}).get("models", {})
    online = llm.get_client().list_models(provider)
    if online is None:
        names = list(local)
        note = f"（未取到 {provider} 的在线模型列表，以上为本地配置）"
    else:
        names = list(online) + [name for name in local if name not in online]
        note = f"（{provider} 的在线模型列表；本地没有元数据的行只显示名字）"
    priced_at = datetime.now(timezone.utc)
    rows = [_MODEL_TABLE_HEADER]
    for name in names:
        selection = f"{provider}/{name}"
        attributes = local.get(name) or {}
        rows.append(_format_model(selection, attributes, priced_at))
    rows.append(note)
    return "\n".join(rows)


def _first_argument(value: str) -> tuple[str, str]:
    if not value.strip():
        return "", ""
    return text.read_params(" " + value.strip(), read_str=True)


def _list_argument(value: str) -> list:
    parsed = ast.literal_eval(value)
    if not isinstance(parsed, list):
        raise ValueError("参数必须是 list")
    return parsed


def _after_tokens(line: str, count: int) -> str:
    """*line* 里前 *count* 个以空白分隔的 token 之后的内容。"""
    position = 0
    for _ in range(count):
        match = re.search(r"\S+", line[position:])
        if match is None:
            return ""
        position += match.end()
    return line[position:].lstrip(" \t")


def _hint_request(raw: str) -> tuple[str, str]:
    """把一次 `#hint` 调用切成 `(动词, 源码)`；动词认不出时给 `"?"`。

    WHY: 源码要整段原样取，所以不能先 strip 再切——那会吃掉作者写的缩进。切分只有一条规则：
    认动词只看第一行（`default` 后面再看一个词），认完把动词那几个 token 去掉，剩下的整段
    就是源码、首尾各 strip 一次。行内换行照旧保留，于是「同一行写 `set x`」「只换行再写」
    「两处都写」三种写法都不丢内容。（`_after_tokens` 按空白取词、不跨行，所以它天然按整段工作。）
    """
    body = raw.lstrip()[len("hint"):].lstrip()
    words = body.split("\n", 1)[0].split()
    if not words:
        return "", ""
    if words[0] == "default":
        if len(words) > 1 and words[1] in ("get", "set"):
            verb, taken = f"default {words[1]}", 2
        else:
            verb, taken = "default", 1
    elif words[0] in ("get", "set"):
        verb, taken = words[0], 1
    else:
        return "?", ""
    return verb, _after_tokens(body, taken).strip()


def _hint_origin(chat_hint: dict | None) -> str:
    """合并后生效的那个 `code` 是从窗口来的，还是从默认来的。"""
    return "本窗口" if isinstance(chat_hint, dict) and "code" in chat_hint else "默认"


def _hint_report(config: dict, origin: str) -> str:
    """`#hint get` 要看的两样：开关，加上那段代码和它的来处。"""
    state = "on" if config.get("on", False) else "off"
    code = config.get("code")
    if not isinstance(code, str) or not code:
        return f"hint: {state}\ncode（{origin}）: （空）"
    return f"hint: {state}\ncode（{origin}）:\n{code}"


def _hint_subcommand(raw: str) -> str:
    """处理一次 `#hint`；op 判权在 `cond`，不在这里。

    WHY: 命令面只管文本——写、看、开关，和 `#prompt` 一系；"聊天循环停下时自动求值"是
    `_run_hint` 那一半，不混进命令语义里。因此没有 del：源码是劳动成果，不用了就
    `#hint set` 回落默认、或把开关切到关。
    WHY: 改完立刻 `storage.save()`，不等后台扫描——hint 是用户手写的配置，紧接着一次重启
    就该还在（cave、link 也是这么落盘的）。
    """
    data = getchatstorage()
    default = storage.get("", "hint")
    chat_hint = data.get("hint")
    verb, source = _hint_request(raw)
    if verb in ("get", "default", "default get") and source.strip():
        return f"hint {verb} 参数过多"
    if verb == "set":
        if source.strip():
            data["hint"] = {"code": cq.unescape(source), "on": True}
            storage.save()
            return "提示已开启"
        data.pop("hint", None)
        storage.save()
        return "已设为默认"
    if verb == "default set":
        if not source.strip():
            return "hint default set 需要代码"
        default["code"] = cq.unescape(source)
        default["on"] = True
        storage.save()
        return "默认已开启"
    if verb == "get":
        return _hint_report(_hint_effective(default, chat_hint), _hint_origin(chat_hint))
    if verb == "default get":
        return _hint_report(default, "默认")
    if verb == "default":
        default["on"] = not bool(default.get("on", False))
        storage.save()
        return "默认已开启" if default["on"] else "默认已关闭"
    if verb == "":
        if not isinstance(chat_hint, dict):
            chat_hint = {}
            data["hint"] = chat_hint
        chat_hint["on"] = not bool(chat_hint.get("on", False))
        storage.save()
        return "提示已开启" if chat_hint["on"] else "提示已关闭"
    return "hint 参数错误，可用 #help hint 查看"


def _limit_report() -> str:
    """`#limit` 查看窗口上限与提醒阈值。"""
    data = getchatstorage()
    lines = []
    for name in ("max_events", "max_token", "pressure_percent"):
        key, _default, _normalize = WINDOW_SETTINGS[name]
        origin = "本窗口" if key in data or name == "max_events" and "max_msg" in data else "默认"
        value = limit(context.current())[0] if name == "max_events" else window_setting(name, data)
        lines.append(f"{name}: {value}（{origin}）")
    return "\n".join(lines)


def _limit_set(tail: str) -> str:
    """`#limit <事件数> <token> [提醒百分比]` 写窗口设置；`reset` 回落默认。

    WHY: 两个历史上限仍一起写，避免只改其中一个造成难解释的半份配置。提醒百分比
    可选，不写就保留原设置；老的两参数命令因此不会意外重置它。
    """
    data = getchatstorage()
    if tail.strip() == "reset":
        for name in ("max_events", "max_token", "pressure_percent"):
            data.pop(WINDOW_SETTINGS[name][0], None)
        data.pop("max_msg", None)
        storage.save()
        return "已重置上限，回落到默认"
    parts = tail.split()
    if len(parts) not in (2, 3):
        return "limit 参数错误，可用 #help limit 查看"
    written = []
    for name, raw_value in zip(("max_events", "max_token", "pressure_percent"), parts):
        try:
            number = int(raw_value)
        except ValueError:
            return "limit 参数错误，可用 #help limit 查看"
        if number < 1 or name == "pressure_percent" and number > 100:
            return "limit 参数错误，可用 #help limit 查看"
        written.append((name, number))
    for name, number in written:
        data[WINDOW_SETTINGS[name][0]] = number
    storage.save()
    return "\n".join(f"{name}: {number}" for name, number in written)


def _agent_subcommand(tail: str) -> str:
    """Only the op-gated #agent entry writes the main agent's global settings."""
    data = storage.get("", "agent")
    parts = tail.split()
    if not parts:
        events, tokens = (WINDOW_SETTINGS["max_events"][2](data.get("max_events")),
                          WINDOW_SETTINGS["max_token"][2](data.get("max_token")))
        pressure = WINDOW_SETTINGS["pressure_percent"][2](data.get("pressure_percent"))
        return (f"model: {get_model(data)}\nlimit: {events} {tokens} {pressure}%\n"
                f"image: {get_image_mode(data)}\nreasoning: {get_reasoning_mode(data)}\n"
                f"tools: {get_tools_mode(data)}\nprompt: {data.get('prompt', '(默认)')}")
    verb, *arguments = parts
    if verb == "ops" and arguments == ["clear"]:
        oplog.clear(AGENT_WINDOW)
        return "已清空中心 agent 的操作视图；正式号未复用"
    if verb == "ops" and not arguments:
        return oplog.render(AGENT_WINDOW) or "中心 agent 还没有操作历史"
    if verb == "use_model" and len(arguments) <= 1:
        if arguments:
            try:
                llm.resolve_model(llm_config, arguments[0])
            except ValueError as error:
                return str(error)
            data["model"] = arguments[0]
        else:
            data.pop("model", None)
    elif (verb == "limit" and len(arguments) in (2, 3)
          and all(value.isdecimal() and int(value) > 0 for value in arguments)
          and (len(arguments) == 2 or int(arguments[2]) <= 100)):
        data["max_events"], data["max_token"] = map(int, arguments[:2])
        if len(arguments) == 3:
            data["pressure_percent"] = int(arguments[2])
    elif verb == "use_setting" and len(arguments) <= 1:
        if arguments and arguments[0] not in prompts:
            return "未找到设定"
        if arguments:
            data["prompt"] = arguments[0]
        else:
            data.pop("prompt", None)
    elif verb in ("image", "reasoning", "tools") and len(arguments) == 1:
        modes = {"image": IMAGE_MODES, "reasoning": REASONING_MODES, "tools": TOOLS_MODES}
        aliases = {"image": IMAGE_MODE_ALIASES, "reasoning": REASONING_ALIASES,
                   "tools": TOOLS_MODE_ALIASES}
        raw = arguments[0].lower()
        choice = aliases[verb].get(raw, raw)
        if choice not in modes[verb]:
            return "设置值不受支持"
        data[verb] = choice
    else:
        return "用法：#agent [use_model [selection]|limit <events> <tokens> [提醒百分比]|use_setting [name]|image/reasoning/tools <mode>|ops [clear]]"
    storage.save()
    return _agent_subcommand("")


def _subcommand(value: str):
    # WHY: hint 的源码要求原样取（含缩进与换行），所以先留一份没 strip 的原文。
    raw = value
    value = value.strip()
    name, _, tail = value.partition(" ")
    tail = tail.strip()
    data = getchatstorage()
    if name == "agent":
        return _agent_subcommand(tail)
    if name == "help" and not tail:
        return _subcommand_help()
    if name == "help" and tail:
        argument, remaining = _first_argument(tail)
        if remaining.strip():
            return "help 参数过多"
        return _subcommand_help(argument)
    if name == "model" and not tail:
        return get_model(data)
    if name == "model" and tail:
        selection, remaining = _first_argument(tail)
        if remaining.strip():
            return "model 参数过多"
        try:
            _provider, _api_model, attributes = llm.resolve_model(llm_config, selection)
        except ValueError as error:
            return str(error)
        return "\n".join((_MODEL_TABLE_HEADER, _format_model(selection, attributes)))
    if name == "models" and not tail:
        return _models_report(data)
    if name == "use_model" and tail:
        selection, remaining = _first_argument(tail)
        if remaining.strip():
            return "use_model 参数过多"
        try:
            llm.resolve_model(llm_config, selection)
        except ValueError as error:
            return str(error)
        data["model"] = selection
        return f"模型设置为 {selection}"
    if name == "use_model" and not tail:
        data.pop("model", None)
        return "已重置模型"
    if name == "image" and not tail:
        return f"image: {get_image_mode(data)}"
    if name == "image" and tail:
        mode, remaining = _first_argument(tail)
        if remaining.strip():
            return "image 参数过多"
        mode = IMAGE_MODE_ALIASES.get(mode.lower(), mode.lower())
        if mode not in IMAGE_MODES:
            return "图片读取档位必须是 off/0、lazy/1 或 eager/2"
        data["image"] = mode
        return f"image: {mode}"
    if name == "reasoning" and not tail:
        return f"reasoning: {get_reasoning_mode(data)}"
    if name == "reasoning" and tail:
        mode, remaining = _first_argument(tail)
        if remaining.strip():
            return "reasoning 参数过多"
        mode = REASONING_ALIASES.get(mode.lower(), mode.lower())
        if mode not in REASONING_MODES:
            return "reasoning 必须是 keep/on 或 drop/off"
        data["reasoning"] = mode
        return f"reasoning: {mode}"
    if name == "tools" and not tail:
        return f"tools: {get_tools_mode(data)}"
    if name == "tools" and tail:
        mode, remaining = _first_argument(tail)
        if remaining.strip():
            return "tools 参数过多"
        mode = TOOLS_MODE_ALIASES.get(mode.lower(), mode.lower())
        if mode not in TOOLS_MODES:
            return "tools 必须是 append 或 ui"
        data["tools"] = mode
        return f"tools: {mode}"
    if name == "ops" and not tail:
        # WHY: 操作历史是新加的一份持久存储，人必须能看见它、也能重置它。轨道写歪了
        # (记进了不该记的东西、或者收缩坏了)时，这是不用改代码就能恢复的入口。
        # WHY: sweep 已拆除，事件流不会因历史预算或聊天中的边界字样物理删除。
        # clear 仍是人手动清除操作视图的恢复入口，正式号不因此复用。
        window = history.window(context.current() or {})
        return oplog.render(window) or "本窗口还没有操作历史"
    if name == "ops" and tail.strip() == "clear":
        oplog.clear(history.window(context.current() or {}))
        return "已清空本窗口的操作历史"
    if name == "prompt" and not tail:
        selected = data.get("prompt")
        if selected is None:
            return f"{settings}\n(默认)"
        if isinstance(selected, str):
            return f"{prompts.get(selected, [])}\n({selected})"
        return str(selected)
    if name == "add_prompt":
        try:
            if not tail:
                addition = _chat_msgs()[-1:]
                result = "上一句聊天已追加到提示词"
            elif re.fullmatch(r"-?\d+", tail):
                count = int(tail)
                messages = _chat_msgs()
                addition = messages[-count:] if count else messages
                result = "当前聊天已追加到提示词(注意重复)"
            else:
                addition = _list_argument(tail)
                result = "提示词已追加"
        except (SyntaxError, ValueError) as error:
            return f"add_prompt 参数错误: {error}"
        data["prompt"] = [*get_prompt(), *addition]
        return result
    if name == "setting":
        if not tail:
            return "\n".join(prompts)
        setting_name, remaining = _first_argument(tail)
        if remaining.strip():
            return "setting 参数过多"
        return str(prompts.get(setting_name, "未找到设定，你可能需要先创建设定"))
    if name == "use_setting":
        if not tail:
            data.pop("prompt", None)
            return "已重置提示词"
        setting_name, remaining = _first_argument(tail)
        if remaining.strip() or setting_name not in prompts:
            return "未找到设定，你可能需要先创建设定"
        data["prompt"] = setting_name
        return "设定已应用"
    if name == "del_setting" and tail:
        setting_name, remaining = _first_argument(tail)
        if remaining.strip() or setting_name not in prompts:
            return "未找到设定"
        del prompts[setting_name]
        return "设定已删除"
    if name == "set_setting" and tail:
        setting_name, remaining = _first_argument(tail)
        if not setting_name:
            return "set_setting 需要设定名"
        if remaining.strip():
            try:
                prompt = _list_argument(remaining.strip())
            except (SyntaxError, ValueError) as error:
                return f"set_setting 参数错误: {error}"
        elif "prompt" in data:
            prompt = data["prompt"]
        else:
            return "当前没有可保存的自定义提示词"
        prompts[setting_name] = prompt
        return "设定已保存"
    if name == "limit" and not tail:
        return _limit_report()
    if name == "limit" and tail:
        return _limit_set(tail)
    if name == "hint":
        return _hint_subcommand(raw)
    return "子命令格式错误，可用 #help 查看"


def _in_chat_scope(event: dict) -> bool:
    """这个窗口开了聊天吗——群要在白名单里，私聊一律算开。"""
    group_id = event.get("group_id")
    return group_id is None or group_id in chat_groups


def _mail_candidate(event: dict) -> bool:
    """Whether history may project this event into an enabled chat window."""
    if not _in_chat_scope(event):
        return False
    if msgs.is_msg(event):
        return True
    return _is_context_poke(event, event.get("group_id") is not None)


def record_event(event: dict, write: Callable[[], object]) -> object:
    """Write chat history and enqueue the same event as one window transaction."""
    window = history.window(event)
    if window is None or not _mail_candidate(event):
        return write()
    return context.mailbox(window).record(event, write)


def _addressed(event: dict, value: str) -> bool:
    """这条消息是冲着 Bot 说的吗：at、`<名字>，`、或者 `柚子，`。"""
    return (has_at(identity.bot_id())(event)
            or value.startswith(f"{identity.bot_name()}，")
            or value.startswith("柚子，"))


def activation_signal(event: dict) -> bool:
    """这条事件该不该把柚子叫醒——**只问这一件事**，不回答「它进不进上下文」。

    WHY: 这是「红点」那一位，从 `cond` 里摘出来的。`cond` 一直在同时回答两个问题：
    「这条要不要激活一轮」和「这条是不是一条就地执行的 `#` 子命令」，靠返回值的类型
    （bool 还是 callable）区分。两个问题的答案本来就不该共用一个出口——子命令那一支
    既不激活、也不进上下文，它和聊天循环唯一的关系就是「不要碰它」。
    见 docs/working/proposals/mail-and-activation.md 3.0。

    WHY: 四个判据一个不少，顺序也照搬：at／名字开头优先于 `#`，所以 `@Bot #help`
    是激活而不是子命令；`#poke` 是唯一一个长得像子命令的激活信号；最后那行的戳一戳
    判据留在**函数末尾**而不是提前 `return False`，因为今天 `#` 未知子命令那条路就是
    落到它上面的——提前返回要先证明「一个事件不可能同时 is_msg 和 is_poke」，
    而那条证明现在没人做过。
    """
    if not _in_chat_scope(event):
        return False
    if msgs.is_msg(event):
        value = msgs.body(event)
        if _addressed(event, value):
            return True
        if value.startswith("#"):
            if value == "#poke":
                return True
            if value[1:].strip().partition(" ")[0] in _SUBCOMMAND_NAMES:
                # 子命令那一支：就地执行，不激活。谁来执行见 _subcommand_call。
                return False
    return msgs.is_poke(event) and event.get("target_id") == identity.bot_id()


def _subcommand_call(event: dict) -> Callable | None:
    """`#` 子命令那一支：返回就地执行它的那个闭包，不是子命令就返回 None。

    WHY: 和 `activation_signal` 是**互斥**的两支，合起来正好是老 `cond` 的全部返回值。
    判据的先后必须与那边一致，否则 `@Bot #help` 会同时被两边认领。
    """
    if not _in_chat_scope(event) or not msgs.is_msg(event):
        return None
    value = msgs.body(event)
    if _addressed(event, value) or not value.startswith("#") or value == "#poke":
        return None
    subcommand = value[1:].strip().partition(" ")[0]
    if subcommand not in _SUBCOMMAND_NAMES:
        return None
    if subcommand in ("hint", "agent") and not op.require_op(event, pattern=r"^#\s*(hint|agent)"):
        # WHY: hint 是用户可写的代码、跑在特权环境、还每次聊天自动执行，权限
        # 与 .py/.link 同级，所以非 op 连命令面都不给；require_op 已经按节流
        # 约定（同一个人的同类重试）给过提醒，这里只要不接管这条消息。
        return None
    return lambda value=value: _subcommand(value[1:])


def cond() -> Callable | bool:
    """老入口，保持原样返回 callable／bool。

    WHY: 没有删，因为它是公开名字——`.py`、link 动作、`#hint` 里的代码都可能在运行期
    按名字引用它，而那些引用 grep 不到。新代码请直接用 `activation_signal` 与
    `_subcommand_call`，这两个各自只回答一个问题。
    """
    event = context.current() or {}
    handler = _subcommand_call(event)
    return handler if handler is not None else activation_signal(event)


def call(data: Callable | bool):
    if callable(data):
        # `#` 前缀让子命令的输出不回流进 LLM 上下文，见 get_msgs 的说明。
        return "#" + cq.escape(str(data()))
    return chat()


@capture(before="chatstart")
def capture_chat(event: dict) -> bool:
    # WHY: 两个问题分两次问，不再靠一个返回值的类型来区分。顺序是承重的：子命令先问，
    # 因为它就地执行、既不激活也不进上下文；剩下的才轮到「红点亮不亮」。
    handler = _subcommand_call(event)
    if handler is not None:
        # `#` 子命令不调模型也不进上下文，跟插话无关，照旧就地执行。
        result = call(handler)
        if result is not None:
            message.sendmsg(result)
        return True
    matched = activation_signal(event)
    window = history.window(event)
    if window is None or not _mail_candidate(event):
        return False
    box = context.mailbox(window)
    box.ensure(event)
    if not matched:
        return False
    # 红点就是「未读里有激活元素」。若这一项已经被 reader 读过，激活也已经得到处理，
    # 不再为了保留旧 trigger 状态额外开一轮；否则只需确保窗口有一个 reader。
    kind = ("mention" if msgs.is_msg(event) and _addressed(event, msgs.body(event))
            else "poke" if msgs.is_poke(event) else "wake")
    if not box.activate(event, kind):
        return True
    chat()
    return True


@capture(before="name加复读")
def capture_addressed_fallback(event: dict) -> bool:
    """Preserve the old addressed fallback outside chat-enabled groups."""
    if not msgs.is_msg(event):
        return False
    captures = text.stc_get(r"{:identity.names}[,，\s]+{Text}")(
        cq.unescape(msgs.body(event)),
        {"identity": identity},
    )
    if captures is None:
        return False
    group_id = event.get("group_id")
    if group_id is None or group_id in chat_groups:
        chat()
        return True
    value = captures["Text"].rstrip()
    value = value.rstrip("？").rstrip("?").rstrip("吗")
    value = value.replace("你", identity.bot_name()).replace("我", "你") + "！"
    message.sendmsg(value)
    return True


def _message_image_urls(event: dict) -> list[str]:
    return [part["image_url"]["url"] for part in msg_split(event.get("message", "")) if part.get("type") == "image_url"]


@thread.to_thread(None)
def _eager_cache_images(event: dict, model: str) -> None:
    capabilities = llm.get_client().get_model_capabilities(model)
    vision_model = llm.get_client().get_vision_model()
    for uri in _message_image_urls(event):
        try:
            if capabilities.vision or not vision_model:
                image.image_uri_to_data_uri(uri)
            else:
                llm.get_client()._get_image_description(uri, vision_model, description_cache)
        except Exception as error:
            _image_stream.info(f"❌ eager 图片捕获失败：{error}")


def eager_cache_images(event: dict) -> None:
    if msgs.is_msg(event) and "[CQ:image" in event.get("message", ""):
        data = storage.get("", "agent")
        if get_image_mode(data) == "eager":
            model = get_model(data)
            count = len(_message_image_urls(event))
            _image_stream.info(f"🖼️ eager 图片捕获：{count} 张，目标模型 {model}")
            _eager_cache_images(event.copy(), model)


@command
@thread.to_thread
def run(body: str, model: str | None = None):
    """向当前窗口配置的模型发送一次单句请求。

    格式：.chat <内容>
    使用当前窗口的模型、提示词和图片模式；连续聊天与 # 设置由聊天捕获入口管理。
    """
    if not body.strip():
        return run.__doc__
    session = llm.Chat(model=model or get_model(), chat_client=llm.get_client())
    _activate_chat(session, [{"role": "user", "content": body.lstrip()}])
    # WHY: 单句请求里的工具轮同样会反复经过图片处理，所以也按一次对话记台账。
    image_ledger = image.begin_conversation()
    try:
        session.chat(recall_func=get_handler(session), description_cache=description_cache)
    finally:
        image.end_conversation(image_ledger)


def on_load(ctx) -> None:
    global settings, prompts, chat_groups, description_cache, llm_config
    from mods import is_available

    missing = [name for name in ("identity", "image", "llm", "storage") if not is_available(name)]
    if missing:
        raise RuntimeError("chat requires available mods: " + ", ".join(missing))

    settings = storage.get("", "settings", list)
    prompts = storage.get("llm_system", "prompts")
    chat_groups = storage.get("", "chat_groups", list)
    description_cache = storage.get("llm_system", "description_cache")
    llm_config = llm.get_client().config

    def resume_recovery() -> None:
        from mods import _backfill, connect, wait_booted

        if not wait_booted(120):
            return
        waiting: dict[tuple, list[dict]] = {}
        for source in oplog.sources():
            if source["source_type"] == "napcat_boot" and source["state"] == "fetching":
                waiting.setdefault(tuple(source["window"]), []).append(source)
        tasks = list(waiting.values())
        task_lock = threading.Lock()

        def recover() -> None:
            while True:
                with task_lock:
                    if not tasks:
                        return
                    sources = tasks.pop()
                for source in sources:
                    try:
                        _backfill.recover_source(source, connect.call_api)
                        window = tuple(source["window"])

                        def wake_finished(target: tuple) -> None:
                            context.wait_turn_end(AGENT_WINDOW)
                            if target in oplog.work_windows(AGENT_WINDOW):
                                try:
                                    _drive_agent(None, target)
                                except Exception:
                                    traceback.print_exc()

                        threading.Thread(target=wake_finished, args=(window,),
                                         name="napcat-backfill-wake", daemon=True).start()
                    except Exception:
                        traceback.print_exc()
                        try:
                            state = oplog.resolve_source(source["key"])
                            oplog.finish_source(source["key"], gap="本地归档或入列失败；可重试",
                                                stop_cursor=state["cursor"])
                        except Exception:
                            traceback.print_exc()
                        break

        for index in range(min(2, len(tasks))):
            threading.Thread(target=recover, name=f"napcat-backfill-{index}", daemon=True).start()

    threading.Thread(target=resume_recovery, name="napcat-backfill-start", daemon=True).start()

    def resume_pending() -> None:
        from mods import wait_booted

        if not wait_booted(120):
            return
        work = oplog.work_windows(AGENT_WINDOW)
        results_pending = bool(context.mailbox(AGENT_WINDOW).unread())
        if not work and not oplog.has_unnotified() and not results_pending:
            return
        if work:
            window = work[0]
        else:
            window = next(iter(oplog.unacknowledged_windows()), None)
            if window is None:
                window = next((window for window, _count, active in oplog.pending_summary()
                               if active and window[0] in ("group", "private")), None)
            if window is None and results_pending:
                window = AGENT_WINDOW
        if window is not None:
            try:
                _drive_agent(None, window)
            except Exception:
                traceback.print_exc()

    threading.Thread(target=resume_pending, name="chat-agent-resume", daemon=True).start()
