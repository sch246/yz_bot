"""QQ-window chat context, settings, tools, and the ``.chat`` command."""

from __future__ import annotations

import ast
from datetime import datetime
import re
import threading
import time
import traceback
from typing import Callable

from mods import context, cq, history, identity, image, llm, log, message, msgs, op, oplog, py, storage, text, thread, tools as tool_modules
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
# WHY: 宁可低。条数只按普通聊天给——正常聊天不会编程，几十条就够；默认值高会让每次重建
# 上下文都更贵，而真需要更长历史的窗口可以自己写覆盖值（见 WINDOW_SETTINGS 与 #limit），
# 这比让所有窗口默默付大账单好。token 反过来给得宽：卡在预算里会让模型说到一半没法思考，
# 而一个纯聊天的会话本来就远用不满，所以它的默认值是"够用"而不是"尽量小"。
DEFAULT_MAX_MSG = 20
DEFAULT_MAX_TOKEN = 50000
_cost_lock = threading.Lock()
# Eager capture is image work reported on the image stream, not chat traffic.
_image_stream = log.stream("image")
# hint 求值失败只记日志，所以它有自己的流，不混进聊天流量。
_hint_stream = log.stream("hint")


def getchatstorage(event: dict | None = None) -> dict:
    event = context.current() if event is None else event
    if event is None:
        raise RuntimeError("当前没有聊天窗口")
    if event.get("group_id") is not None:
        return storage.get("groups", str(event["group_id"]))
    return storage.get("users", str(event["user_id"]))


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
    """本窗口生效的 `(消息条数上限, 上下文 token 上限)`。

    两个值都从 WINDOW_SETTINGS 取，窗口没写就用默认。没有窗口（没有 group_id 也
    没有 user_id）时直接给默认值——`#hint` 的默认代码要拿它显示，不该因此抛出去。
    """
    event = context.current() if event is None else event
    if event is None or history.window(event) is None:
        return DEFAULT_MAX_MSG, DEFAULT_MAX_TOKEN
    data = getchatstorage(event)
    return window_setting("max_msg", data), window_setting("max_token", data)


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


def _bounded_int(minimum: int, fallback: int):
    """归一化成一个不小于 *minimum* 的整数，否则回到默认值。"""

    def normalize(value) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return fallback
        return number if number >= minimum else fallback

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
    "max_msg": ("max_msg", DEFAULT_MAX_MSG, _bounded_int(1, DEFAULT_MAX_MSG)),
    "max_token": ("max_token", DEFAULT_MAX_TOKEN, _bounded_int(1, DEFAULT_MAX_TOKEN)),
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
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    try:
        sent_by_bot = int(sender.get("user_id")) == identity.bot_id()
    except (TypeError, ValueError):
        sent_by_bot = False
    if sent_by_bot:
        role = "assistant"
        content = msg_split(event.get("message", ""))
    else:
        role = "user"
        timestamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(event.get("time", time.time())))
        metadata = [f"  <time>{timestamp}</time>", f"  <message_id>{event.get('message_id', '')}</message_id>"]
        if in_group:
            metadata[0:0] = [
                f"  <user_id>{event.get('user_id')}</user_id>",
                f"  <name>{identity.getname(event.get('user_id'), event.get('group_id'))!r}</name>",
            ]
        content = [{"type": "text", "text": "<metadata>\n" + "\n".join(metadata) + "\n</metadata>"}, *msg_split(event.get("message", ""))]
    return {"role": role, "content": content}


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
    return {"role": "user", "content": f"【{kind}】{_poke_text(event)}"}


def _message_cost(converted: dict) -> int:
    content = converted["content"]
    if isinstance(content, str):
        return count_tokens(content)
    return sum(count_tokens(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text")


def _within_budget(events: list[dict], in_group: bool, token_limit: int) -> tuple[list[tuple[float, dict]], int]:
    """The chat messages that fit, oldest first, each with its timestamp.

    WHY: 截断只有这一处实现。get_msgs 和 build_context 都从这里取，操作记录再依附到它的
    结果上——三处各写一遍"留多少"，改一处就会静默地分叉。
    """
    picked: list[tuple[float, dict]] = []
    used = 0
    for event in events:
        converted = event2chat(event, in_group)
        used += _message_cost(converted)
        if used > token_limit:
            break
        picked.insert(0, (float(event.get("time") or 0.0), converted))
    return picked, used


def get_msgs(token_limit: int | None = None, return_token: bool = False):
    current = context.current() or {}
    in_group = current.get("group_id") is not None
    if token_limit is None:
        token_limit = limit(current)[1]
    picked, used = _within_budget(_selected_events(current, in_group), in_group, token_limit)
    output = [converted for _at, converted in picked]
    return (output, used) if return_token else output


def context_usage() -> int:
    """已进上下文的聊天文本 token 估算——`#hint` 里的 `usage` 用的就是它。

    WHY: 用量本来只在 `get_handler` 里临时算一次就丢，这里给它一个出口；hint 只调它、不
    自己算。它是**下界**：只算重建上下文时那些聊天消息的文本，系统提示、工具 schema 和
    本轮的生成都不在内。
    """
    return get_msgs(return_token=True)[1]


def _selected_events(current: dict, in_group: bool) -> list[dict]:
    """Walk recent history newest-first and keep what may enter the model context."""
    message_limit = limit(current)[0]
    events = history.getlog(current)[:message_limit]
    if len(events) < message_limit:
        # WHY: 内存里的窗口只有 history.MAX_LEN 条，max_msg 调过它就得回 chatlog 文件取，
        # 否则 `#limit 500` 会静默地只给 256 条。read_range 按天倒走、读够就停，所以这条
        # 只在窗口真的写了大上限时才贵。读文件失败就用手上那份，聊天不该因此中断。
        try:
            events = history.getlog(current, limit=message_limit)
        except OSError:
            pass
    selected = []
    for event in events:
        if msgs.is_msg(event):
            value = msgs.body(event)
            # WHY: `#` 开头的消息一律不进 LLM 上下文。这是一条跨模块的约定，且这里是
            # 唯一的消费端——所有生产端都指回这里：
            #   llm.Chat.chat      LLM 失败信息  f"# {error}"
            #   py.run             .py 的 traceback
            #   link._traceback_text  link action 的 traceback
            #   chat.call          #子命令的输出
            # 目的是让调试输出不回流进模型：它们对模型无意义，占 token，还会让模型看到
            # 自己的错误堆栈然后试图"解释"它。过滤对所有发送者一视同仁，Bot 自己发的也
            # 一样被排除；用户发的 `#help` 等子命令因此也不进上下文，这同样是想要的。
            # 改任何一个生产端的前缀(比如统一成 console 的 ❌ 图标)都会让那类输出开始
            # 回流，而且不会报错——只会悄悄变贵变糟。
            # 注意别和 py/link 里"最后一行以 # 开头就不 eval"混为一谈：那是 Python 的
            # 注释语义，只是恰好同一个字符。
            if value.startswith("#"):
                continue
            if value in ("聊天开始", "聊天结束"):
                break
            selected.append(event)
        elif _is_context_poke(event, in_group):
            selected.append(event)
    return selected


def usage_name(when: datetime | None = None) -> str:
    """The storage name for one month's usage: ``YYYY-MM``.

    WHY: 键必须带年份。裸月份把每一年的同一个月并进同一个文件，"去年九月"无从
    查起，多年数据还会被加在一起——这是 usage 数字失真的直接来源之一。
    """
    moment = when or datetime.today()
    return f"{moment.year}-{moment.month:02d}"


def _usage_entry() -> list | None:
    """The acting user's ``[calls, cost]`` for the current month, or ``None``.

    WHY: 归属用 ``history.author`` 而不是顶层 ``user_id``。私聊窗口的 ``user_id`` 是
    **窗口对端**，Bot 自己发起的那一轮（比如注入的命令）会因此把费用记到对端头上——
    这和 ``history.same_author``、``op.is_op`` 修的是同一处混淆。群聊两者本来相同，
    所以改动只在私聊、且只在 Bot 自己是作者时生效。

    WHY: 没有 user_id 时返回 None，而不是写入一个 "None" 键——那种键 .chattop
    读不出来（int() 会炸），费用也就永久记丢。宁可这次不计，也不落一个查不到的条目。
    """
    event = context.current() or {}
    user_id = history.author(event)
    if user_id is None:
        return None
    usage = storage.get("usage", usage_name())
    return usage.setdefault(str(user_id), [0, 0])


def inc_call_count() -> None:
    entry = _usage_entry()
    if entry is not None:
        entry[0] += 1


def inc_call_cost(model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> None:
    """把一次调用的费用记到当前发言者名下。

    WHY: 单价、缓存命中价和峰谷档位全部来自模型/供应商元数据（见 llm.pricing），这里只
    负责取元数据、算钱、记账三件事。命中缓存的那部分必须单独算——聊天的 prompt 大多是
    重复上下文，一律按未命中价算会把费用高估一个数量级（实测同一段上下文第二次调用，
    845 个 prompt token 里有 640 是命中）。
    """
    _, _, attributes = llm.resolve_model(llm_config, model)
    provider = llm.provider_config(llm_config, model)
    inc_usage_cost(pricing.token_cost(provider, attributes, prompt_tokens, completion_tokens, cached_tokens))


def inc_usage_cost(price: float) -> None:
    """Add one externally calculated cost to the current user's usage."""
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
- 如无特殊要求，请用中文回复"""}]


def build_context(token_limit: int | None = None) -> list:
    """Chat history, with the rebuilt tool rounds that belong inside it.

    WHY: 只有**一套**截断规则，就是聊天消息那套（max_msg + max_token）。操作记录不再自己
    算预算、也没有自己的保留期，它依附于聊天窗口：留下来的最老那条消息之后发生的工具轮才
    进上下文。两套规则曾经并存过，拆了——它们描述的是同一件事"多早以前的事情还算数"，
    答案有两个就意味着两处调参、两处解释，而且总有一处会先漂。

    WHY: 代价是工具轮不占预算，调用密集时上下文会超出 max_token。这是知情的选择：压住它
    的是 condense_ops，由模型在得出结论时自己收缩，而不是由这里按大小乱砍——按大小砍会在
    结论产出之前砍掉前提。真要封顶的话，是在这里给工具轮也记一份成本，别去给它加保留期。

    WHY: 归并单位是一轮。assistant(tool_calls) 和它的 tool 结果之间插进一条聊天消息就拆散
    了这一对，请求会被拒，所以每轮带一个时间、整体落位。

    WHY: 同一时刻聊天排在工具轮前面。工具调用是被某条消息触发的，触发它的那句话在它之前；
    秒级时间戳里两者常常相等，靠这个平手规则维持因果。

    WHY: 顺序不保证与当时完全一致：一轮里几个并发调用完成时间不同，工具执行期间到达的
    消息其真实先后也无法从一个时间点还原。这是明知的近似。

    WHY: 没载入的部分不列清单。曾经这里插过一行"更早还有 N 次工具调用未载入（op1–op12）"，
    拆掉了：真正需要重新打开的是被自己收缩掉的那些，它们的 cid 就写在 condense_ops 调用的
    arguments 里、跟着重建回到上下文中——入口已经在了，不用再指一次。
    """
    current = context.current() or {}
    if token_limit is None:
        token_limit = limit(current)[1]
    in_group = current.get("group_id") is not None
    window = history.window(current)
    events = _selected_events(current, in_group)
    picked, _used = _within_budget(events, in_group, token_limit)
    if not picked:
        # 没有聊天做锚点时不载入任何操作记录：孤零零摆着，模型无从判断它当时在回应什么。
        return []
    # WHY: 回收也依附于聊天。events 已经按 max_msg 截过，比它最老那条还早、又没人引用的
    # 操作再也够不着了，这里顺手让 oplog 扫掉——门槛用 max_msg 窗口而不是本轮预算，因为
    # 预算每轮可松可紧（`.chat` 就传别的值），拿它当删除门槛会删掉下一轮还够得着的记录。
    # "又没人引用"那半句在 oplog.sweep 里：被窗口内的 condense_ops 点过名的调用要留着，
    # 否则上下文里那个 cid 就成了指向空处的断号。
    oplog.sweep(window, float(events[-1].get("time") or 0.0))
    items: list[tuple[float, int, list]] = [(at, 0, [converted]) for at, converted in picked]
    floor = picked[0][0]
    items.extend((at, 1, batch) for at, batch in oplog.build_rounds(window) if at >= floor)
    items.sort(key=lambda item: (item[0], item[1]))
    return _close_with_user([message for item in items for message in item[2]])


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

def init_chat(session: llm.Chat, messages: list | None = None) -> None:
    inc_call_count()
    prompts["base"] = _base_prompt()
    group = context.current().get("group_id") if context.current() else None
    state = {"role": "system", "content": f"当前所在群聊:{identity.getgroupname(group)}({group})"} if group is not None else {"role": "system", "content": f"当前在私聊:{identity.getname()}({context.current().get('user_id')})"}
    ui_mode = get_tools_mode() == "ui"
    tool_context = tool_modules.create_context_message(ui_mode=ui_mode)
    window = history.window(context.current() or {})
    session.set_messages([
        *get_prompt(),
        *prompts["base"],
        tool_context,
        state,
        *(messages or []),
    ])
    # WHY: 已激活的工具模块属于**窗口**，要在这一轮开局装回去，改的时候也写回去。每轮
    # `_run_chat` 都新建一个 `llm.Chat`，激活只在内存里活着的话，下一轮模型就拿着上一轮
    # 装载过的名字去调用，而快照里没有——那个调用被丢掉、整轮直接结束，模型连自救的机会
    # 都没有（2026-09-17 `browser__open_page`）。读写在 `_active_modules`／
    # `_persist_modules`，理由写在那里。
    # WHY: 装回不是无限的：超过时限没用过的模块会在 `restore` 里被收掉，并给模型一条
    # 通告——"只进不出"会让每次 `load_tools` 都永久占着基线消息。判据用的是每个模块最后
    # 一次被调用的时刻，所以 bind 出来的那个对象要一直拿着，供 `_oplog_recorder` 上报。
    binding = tool_modules.bind_session(
        session,
        tool_context,
        _active_modules(window) if window is not None else {},
        ui_mode=ui_mode,
        persist=_persist_modules(window) if window is not None else None,
    )
    session.do_process_image = get_image_mode() != "off"
    session.keep_reasoning = get_reasoning_mode() == "keep"
    session.on_tool_result = _oplog_recorder(window, binding)


def get_handler(session: llm.Chat):
    def handle(chunk: llm.LLMResponse) -> None:
        if chunk.role == "assistant" and chunk.content:
            message.sendmsg(chunk.content)
        if chunk.total_tokens:
            inc_call_cost(session.model, chunk.prompt_tokens, chunk.completion_tokens, chunk.cached_tokens)

    return handle


def _oplog_recorder(window, binding=None):
    """Record each finished tool call and hand back the cid the model can name.

    WHY: 顺手把"这个模块刚被用过"告诉 binding（`touch`）。空闲回收唯一的判据就是这个时刻，
    而工具是 `llm` 那层直接 `tool.call(**arguments)` 执行的，它不认识 binding——所以借这个
    每个工具结果都会经过的钩子把名字递过去。
    """
    def record(result, round_id: str) -> str | None:
        if binding is not None:
            binding.touch(result.name)
        return oplog.record(window, result.name, result.arguments, result.content, result.tool_call_id, round_id)
    return record


def _interject_provider(turn, in_group: bool):
    """Drain the window's queue into messages appended before the next request."""
    def provide() -> list[dict]:
        return [event2chat(event, in_group) for event in turn.take_pending()]
    return provide


def _window_storage(window: tuple) -> dict:
    """取本窗口自己的 chat storage，键就是 `history.window(...)`（`#hint` 和工具激活共用）。

    WHY: 不经过 `context.current()`——hint 在 `chat` 的 `finally` 里跑，那个窗口就是调用方
    手上的实参；由实参决定"哪个窗口"，触发点就不依赖线程局部的当前事件，也不跟捕获、派发
    的细节绑在一起。工具激活走同一个理由：`init_chat` 手上的 window 就是它的窗口。
    命名空间与 getchatstorage 同一套。
    """
    kind, key = window
    return storage.get("groups" if kind == "group" else "users", str(key))


# 本窗口持久激活的工具模块名，以及各自最后一次被调用的时刻。别和 WINDOW_SETTINGS 里的
# "tools"（工具状态呈现方式）混用，两者住在同一个 storage 字典里。
_ACTIVE_MODULES_KEY = "active_tools"


def _active_modules(window: tuple) -> dict[str, float]:
    """本窗口上次装着哪些工具模块、各自最后一次被调用是什么时候（`init_chat` 开局装回去）。

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
    """给 `SessionBinding` 的回调：把本窗口的激活集合写回 storage。

    WHY: 激活是**窗口级**状态，不是单轮状态。`_run_chat` 每轮都新建一个 `llm.Chat`，
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


def _run_hint(window: tuple) -> None:
    """求值本窗口的结束提示，并把结果发出去；触发点写在 `chat` 的 `finally`。

    WHY: 唯一信号是"循环停下"：`while` 里每个 `return` 和异常都经过 `finally`，而每轮
    `_run_chat` 返回时不经过，所以不会每句都刷；`if not owner:` 的早退和不带窗口的单轮
    chat 也走不到这里，于是"真的结束"只算一次、也只由这一轮的持有者来做。

    WHY: 求值与发送的任何异常都吞掉、只写日志，绝不抛回 `finally`——这段代码是用户自己
    写的、每次聊天都自动跑，让它抛出去就等于一段烂代码能污染聊天主流程的返回路径。
    """
    try:
        merged = _hint_effective(storage.get("", "hint"), _window_storage(window).get("hint"))
        code = merged.get("code")
        if not merged.get("on", False) or not isinstance(code, str) or not code.strip():
            return
        result = _hint_evaluate(code, window)
        if result is not None:
            # `#` 前缀让结束提示不回流进 LLM 上下文，见 get_msgs 的说明。
            message.sendmsg("#" + cq.escape(str(result)))
    except Exception:
        _report_hint_failure()


def _hint_evaluate(code: str, window: tuple):
    """在 `py.loc` 的一份私用副本里跑一次 *code*，返回末行的值。

    WHY: 名字要照旧认（`sendmsg`/`storage`/…都在），痕迹不能留——副本 + 单次求值就够了：
    `window`/`usage` 是这一次临时的，代码里的赋值也只落进副本，`py.loc` 一个键都不会多。
    仍走 `py.eval_last`，于是「末行是表达式才发」和 Traceback 指回作者那几行都不变。
    """
    namespace = dict(py.loc)
    namespace["window"] = window
    namespace["usage"] = context_usage()
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
        _run_chat(model, None, event.get("group_id") is not None)
        return
    turn, owner = context.begin_turn(window)
    if not owner:
        # WHY: 一个窗口同时只跑一轮。以前第二条 at 会再起一轮并发的 chat()，两轮各自读
        # get_msgs()、各自发言，像两个人抢着回答。现在只登记"还要再跑一轮"，事件本身已经
        # 在 history 里，下一轮重建上下文时自然会读到。
        # WHY: 带上事件本身。续跑的那一轮属于要求它的人，而不是开轮的人——判据见 finish_turn
        # 之后的 set_current。
        turn.mark_trigger(event)
        return
    in_group = event.get("group_id") is not None
    # WHY: 一次对话 = 这次持有的全过程（多轮 + 插话续写，直到 finally），图片检查台账就
    # 活在这段里：同一张图不重复下载/解析，对话结束即清掉，下次再聊重新检查一遍。
    image_ledger = image.begin_conversation()
    try:
        while True:
            _run_chat(model, turn, in_group)
            if turn.cancelled:
                return
            triggered = context.finish_turn(window, turn)
            if triggered is None:
                return
            # WHY: 续跑的这一轮属于**要求它的那个人**，所以当前事件换成它。按窗口登记的
            # 那些东西（storage、oplog、hint）本来就只看 window，换不换都一样；换的是"这
            # 一轮谁在说话"，而 op 门（tools.op_tool_visible、SessionBinding.load 的
            # visible、op 工具执行时自己那次 is_op）问的正是这个。不换的话三层问的都是开
            # 轮那个人：管理员开一轮、普通群友接着 at 一句，那一轮就带着管理员的身份为群友
            # 跑，op 专属模块在里面可见、可加载、可执行。
            # WHY: 判据只认**触发事件**，不认"上下文里出现过 op"。后者会把权限绑在
            # max_msg/max_token 上——同一段聊天，预算调大就有权限、调小就没有，既没法跟人
            # 解释，也没法事后复现。插话（不置 trigger 的那些）因此不改变身份：它们是上下
            # 文，不是授权。
            context.set_current(triggered)
    finally:
        image.end_conversation(image_ledger)
        context.end_turn(window, turn)
        # WHY: 循环停下的唯一信号就在这里，见 _run_hint。
        _run_hint(window)


def _run_chat(model: str | None, turn, in_group: bool) -> None:
    session = llm.Chat(model=model or get_model(), chat_client=llm.get_client())
    # WHY: 聊天历史与重建出的工具调用记录由 build_context 一起装配，共用一份 token
    # 预算。`.chat` 单句请求走的是另一条路：它本来就不读聊天历史，也就不载入工具记录。
    init_chat(session, build_context())
    if turn is not None:
        # WHY: 先建上下文再清队列，顺序不能反。get_msgs 已经从 history 读到了此刻为止
        # 的全部消息，队列里同一批就是重复；反过来先清再读，则清掉之后、读到之前到达的
        # 消息会两头落空。这个方向漏掉的消息只是本轮不追加——它仍在 history 里，而且
        # take_pending 不动 trigger 标记，该再跑一轮还是会跑。
        turn.take_pending()
        session.add_context_provider(_interject_provider(turn, in_group))
        session.should_stop = lambda: turn.cancelled
    session.chat(recall_func=get_handler(session), description_cache=description_cache)


_SUBCOMMAND_HELP = (
    ("help [name]", "显示子命令目录或某条子命令的完整说明。\n格式：#help | #help <名称>"),
    ("model", "查看当前模型"),
    ("model <selection>", "查看指定模型信息"),
    ("models", "列出当前供应商的模型（优先在线列表）"),
    ("use_model [selection]", "设置或重置当前模型"),
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
    ("limit [<条数> <token>|reset]", """查看或设置本窗口的消息条数与上下文 token 上限（管理员）。

格式：#limit | #limit <条数> <token> | #limit reset
两个上限决定重建上下文时最多取多少条聊天消息、估算多少 token；默认值写死在代码里（现在 max_msg=20、max_token=50000），本窗口写过的值优先。
#limit                  显示两个上限，并标出值来自本窗口还是默认
#limit <条数> <token>   写入本窗口的两个上限，都必须是正整数
#limit reset            清掉本窗口的值，回落到默认
条数超过 history 的内存窗口（256）时会从 chatlog 按天倒读补足，读够就停。"""),
    ("hint [get|set|default]", """查看、编写或开关本窗口的结束提示（管理员）。

格式：#hint | #hint get | #hint set <代码> | #hint set | #hint default [get|set <代码>]
本窗口的配置在 chat storage 的 hint 键，全局默认在 storage 的 "" 命名空间；生效的是两者按 {**默认, **窗口} 合并之后 code 非空、on 为真的那份。
聊天循环停下时求值一次，非 None 的结果作为一条消息发出（自带 # 前缀，不进模型上下文）。
求值环境是共享动态环境，另外注入 window（本窗口）与 usage（已进上下文的聊天文本 token 估算，下界）。
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


_MODEL_TABLE_HEADER = "模型 输入(未命中/命中) 输出 (单位: 元/(1m token)，高峰价) 视觉识别 函数调用"


def _format_model(selection: str, attributes: dict) -> str:
    # WHY: 表里列的是**高峰价**（provider 传空字典即"不在空闲时段"），峰谷规则由
    # `_price_note` 另起一行说明。一张表只放一套数字，比每行分高峰/空闲两栏好读。
    if any(key in attributes for key in pricing.PRICE_KEYS):
        prices = pricing.format_prices(pricing.unit_prices({}, attributes))
    else:
        # 本地没有这条模型的元数据，不替对端猜价格（见 UNKNOWN_MODEL_CAPABILITIES）。
        prices = " / ".join("-" for _ in pricing.PRICE_KEYS)
    return f"{selection}\n    {prices} {'👀' if attributes.get('vision') else ''} {'⚙️' if attributes.get('function_calling') else ''}"


def _price_note(selection: str) -> str:
    """峰谷说明；该 provider 没有 `off_peak` 规则时是空串。"""
    return pricing.describe_off_peak(llm.provider_config(llm_config, selection)) or ""


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
    rows = [_MODEL_TABLE_HEADER, *(_format_model(f"{provider}/{name}", local.get(name) or {}) for name in names), note]
    if price_note := _price_note(get_model(data)):
        rows.append(price_note)
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
    """`#limit` 看两个上限，并标出这个值来自本窗口还是默认。"""
    data = getchatstorage()
    lines = []
    for name in ("max_msg", "max_token"):
        key, _default, _normalize = WINDOW_SETTINGS[name]
        origin = "本窗口" if key in data else "默认"
        lines.append(f"{name}: {window_setting(name, data)}（{origin}）")
    return "\n".join(lines)


def _limit_set(tail: str) -> str:
    """`#limit <条数> <token>` 写本窗口的两个上限；`#limit reset` 清掉、回落默认。

    WHY: 两个值一起写、都必须是正整数——窗口配置要么整份生效、要么整份没有，半份
    （只改了条数、token 还是上一版）比拒绝更难解释。清掉本窗口的值就等于回落默认，
    所以不需要"删除"这个动作。
    """
    data = getchatstorage()
    if tail.strip() == "reset":
        for name in ("max_msg", "max_token"):
            data.pop(WINDOW_SETTINGS[name][0], None)
        storage.save()
        return "已重置上限，回落到默认"
    parts = tail.split()
    if len(parts) != 2:
        return "limit 参数错误，可用 #help limit 查看"
    written = []
    for name, raw_value in zip(("max_msg", "max_token"), parts):
        try:
            number = int(raw_value)
        except ValueError:
            return "limit 参数错误，可用 #help limit 查看"
        if number < 1:
            return "limit 参数错误，可用 #help limit 查看"
        written.append((name, number))
    for name, number in written:
        data[WINDOW_SETTINGS[name][0]] = number
    storage.save()
    return "\n".join(f"{name}: {number}" for name, number in written)


def _subcommand(value: str):
    # WHY: hint 的源码要求原样取（含缩进与换行），所以先留一份没 strip 的原文。
    raw = value
    value = value.strip()
    name, _, tail = value.partition(" ")
    tail = tail.strip()
    data = getchatstorage()
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
        return "\n".join(part for part in (_MODEL_TABLE_HEADER, _format_model(selection, attributes), _price_note(selection)) if part)
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
        # WHY: clear 的适用面比它看起来窄。操作记录依附聊天窗口，而 _selected_events 撞上
        # 「聊天开始」/「聊天结束」就 break——所以重开一次聊天，地板抬到新起点，旧记录下
        # 一轮自然被 sweep 扫掉。clear 真正管的是**聊天进行中途**要清轨道又不想断对话的
        # 情况。别因为"反正重开聊天也能清"就删掉它，那是另一个代价。
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
                addition = get_msgs()[-1:]
                result = "上一句聊天已追加到提示词"
            elif re.fullmatch(r"-?\d+", tail):
                count = int(tail)
                messages = get_msgs()
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


def cond() -> Callable | bool:
    event = context.current() or {}
    group_id = event.get("group_id")
    if group_id is not None and group_id not in chat_groups:
        return False
    if msgs.is_msg(event):
        value = msgs.body(event)
        if has_at(identity.bot_id())(event) or value.startswith(f"{identity.bot_name()}，") or value.startswith("柚子，"):
            return True
        if value.startswith("#"):
            subcommand = value[1:].strip().partition(" ")[0]
            if value == "#poke":
                return True
            if subcommand in _SUBCOMMAND_NAMES:
                if subcommand == "hint" and not op.require_op(event, pattern=r"^#\s*hint"):
                    # WHY: hint 是用户可写的代码、跑在特权环境、还每次聊天自动执行，权限
                    # 与 .py/.link 同级，所以非 op 连命令面都不给；require_op 已经按节流
                    # 约定（同一个人的同类重试）给过提醒，这里只要不接管这条消息。
                    return False
                return lambda value=value: _subcommand(value[1:])
    return msgs.is_poke(event) and event.get("target_id") == identity.bot_id()


def call(data: Callable | bool):
    if callable(data):
        # `#` 前缀让子命令的输出不回流进 LLM 上下文，见 get_msgs 的说明。
        return "#" + cq.escape(str(data()))
    return chat()


@capture(before="chatstart")
def capture_chat(event: dict) -> bool:
    matched = cond()
    if callable(matched):
        # `#` 子命令不调模型也不进上下文，跟插话无关，照旧就地执行。
        result = call(matched)
        if result is not None:
            message.sendmsg(result)
        return True
    window = history.window(event)
    turn = context.get_turn(window) if window is not None else None
    if turn is not None:
        # WHY: 分级在这里。这个窗口正跑着一轮，任何进得了上下文的消息都入队(让模型看到
        # 更多，而不是等这轮结束才发现群里已经聊了十句)，但只有原本就会触发聊天的那种
        # 才置 trigger 让它再跑一轮。否则普通闲聊会把 Bot 拖进无限续聊。
        # `#` 开头的一律不入队，理由与 get_msgs 的过滤完全相同。
        if msgs.is_msg(event) and not msgs.body(event).startswith("#"):
            turn.interject(event, trigger=bool(matched))
        elif matched:
            turn.interject(event, trigger=True)
        return bool(matched)
    if not matched:
        return False
    result = call(matched)
    if result is not None:
        message.sendmsg(result)
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
        data = getchatstorage(event)
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
    init_chat(session, [{"role": "user", "content": body.lstrip()}])
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
