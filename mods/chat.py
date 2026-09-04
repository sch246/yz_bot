"""QQ-window chat context, settings, tools, and the ``.chat`` command."""

from __future__ import annotations

import ast
from datetime import datetime
import re
import threading
import time
from typing import Callable

from mods import context, cq, history, identity, image, llm, log, message, msgs, oplog, storage, text, thread, tools as tool_modules
from mods.command import command
from mods.capture import capture


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
# WHY: 这两个是 LLM 刚出现时定的，那时模型上下文上限本身就很小——max_msg 早期是 20
# 甚至更少，因为太容易超限。现在的模型早已宽松得多，这组默认值只是没人回头调过，不是
# 出于省钱的判断。两者都可以被 storage 里的 llm_config 覆盖(见 on_load)，所以要放宽
# 优先改配置而不是改这里的默认值。
max_token = 4000
max_msg = 200
_cost_lock = threading.Lock()
# Eager capture is image work reported on the image stream, not chat traffic.
_image_stream = log.stream("image")


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


def get_image_mode(data: dict | None = None) -> str:
    return normalize_image_mode((getchatstorage() if data is None else data).get("image"))


# WHY: 下面两组照 image 那一套写：normalize 负责把存坏的值拉回默认，读取端永远拿得到
# 合法值，所以旧存储里的遗留值不会让聊天崩掉。别改成直接读原值。
def normalize_reasoning_mode(value) -> str:
    normalized = REASONING_ALIASES.get(str(value).lower(), str(value).lower())
    return normalized if normalized in REASONING_MODES else "keep"


def get_reasoning_mode(data: dict | None = None) -> str:
    return normalize_reasoning_mode((getchatstorage() if data is None else data).get("reasoning"))


def normalize_tools_mode(value) -> str:
    normalized = TOOLS_MODE_ALIASES.get(str(value).lower(), str(value).lower())
    return normalized if normalized in TOOLS_MODES else "append"


def get_tools_mode(data: dict | None = None) -> str:
    return normalize_tools_mode((getchatstorage() if data is None else data).get("tools"))


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


def get_msgs(token_limit: int | None = None, return_token: bool = False):
    token_limit = max_token if token_limit is None else token_limit
    current = context.current() or {}
    in_group = current.get("group_id") is not None
    selected = _selected_events(current, in_group)
    output = []
    used = 0
    for event in selected:
        converted = event2chat(event, in_group)
        used += _message_cost(converted)
        if used > token_limit:
            break
        output.insert(0, converted)
    return (output, used) if return_token else output


def _selected_events(current: dict, in_group: bool) -> list[dict]:
    """Walk recent history newest-first and keep what may enter the model context."""
    selected = []
    for event in history.getlog(current)[:max_msg]:
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


def _usage_entry() -> list:
    event = context.current() or {}
    storage.get("usage", "last_call")["last_call"] = f"{datetime.today().year}-{datetime.today().month}"
    usage = storage.get("usage", str(datetime.today().month))
    return usage.setdefault(str(event.get("user_id")), [0, 0])


def inc_call_count() -> None:
    _usage_entry()[0] += 1


def inc_call_tokens_cost(model: str, tokens: tuple[int, int]) -> None:
    _, _, attributes = llm.resolve_model(llm_config, model)
    prompt_price = attributes.get("prompt_price", 0)
    completion_price = attributes.get("completion_price", 0)
    price = (tokens[0] * prompt_price + tokens[1] * completion_price) / 1_000_000
    inc_usage_cost(price)


def inc_usage_cost(price: float) -> None:
    """Add one externally calculated cost to the current user's usage."""
    # A storage list is the authority; only this read-modify-write needs a lock.
    with _cost_lock:
        _usage_entry()[1] += price


def _base_prompt() -> list[dict]:
    return [{"role": "system", "content": f"""## 注意事项
- 你的昵称: {identity.bot_name()}
- 你的QQ号: {identity.bot_id()}；群聊 at 格式为 [CQ:at,qq=qq号]，reply 格式为 [CQ:reply,id=message_id]
- 你收到的消息原样带着这两种 CQ 码。reply 里的 message_id 与上文各条消息 <metadata> 中的 <message_id> 对应，据此判断对方在回复哪一条
- 聊天中可能不会有明显的问题，扮演好角色即可
- 如无特殊要求，请用中文回复"""}]


def _round_cost(batch: list[dict]) -> int:
    """Token cost of one rebuilt round, tool_calls arguments included."""
    total = 0
    for message in batch:
        for call in message.get("tool_calls") or ():
            function = call["function"]
            total += count_tokens(function["name"]) + count_tokens(function["arguments"])
        if isinstance(message.get("content"), str):
            total += count_tokens(message["content"])
    return total


def build_context(token_limit: int | None = None) -> list:
    """Assemble chat history and rebuilt tool rounds under one shared budget.

    WHY: 一份预算，不是两份。聊天消息和重建出的工具调用记录进的是同一个上下文，各算各的
    等于没算——两边都在限额内，加起来照样超。所以归并发生在预算**之内**：按时间从新到旧
    走一条合并后的时间轴，谁先够到边界谁被丢。

    WHY: 归并单位是一轮。assistant(tool_calls) 和它的 tool 结果之间插进一条聊天消息就拆散
    了这一对，请求会被拒；预算也按整轮扣，半轮进上下文同样是非法的。

    WHY: 同一时刻聊天排在工具轮前面。工具调用是被某条消息触发的，触发它的那句话在它之前；
    秒级时间戳里两者常常相等，靠这个平手规则维持因果。

    WHY: 早于最老那条聊天消息的工具轮不进上下文。没有对应聊天做锚点的操作，模型无从判断
    它当时在回应什么，孤零零摆在最前面只会误导。丢掉不等于丢失——它们仍在轨道里，最长
    保留 7 天，知道 cid 就能用 recall_ops 取回。

    WHY: 顺序不保证与当时完全一致：一轮里几个并发调用完成时间不同，工具执行期间到达的
    消息其真实先后也无法从一个时间点还原。这是明知的近似。
    """
    token_limit = max_token if token_limit is None else token_limit
    current = context.current() or {}
    in_group = current.get("group_id") is not None
    window = history.window(current)
    # 每项：(时间, 平手序, token 成本, 这一项整体落位的消息)
    items: list[tuple[float, int, int, list]] = []
    for event in _selected_events(current, in_group):
        converted = event2chat(event, in_group)
        items.append((float(event.get("time") or 0.0), 0, _message_cost(converted), [converted]))
    for at, batch in oplog.build_rounds(window):
        items.append((at, 1, _round_cost(batch), batch))
    items.sort(key=lambda item: (item[0], item[1]))

    kept: list[tuple[float, int, int, list]] = []
    used = 0
    for item in reversed(items):
        used += item[2]
        if used > token_limit:
            break
        kept.append(item)
    kept.reverse()

    anchor = next((index for index, item in enumerate(kept) if item[1] == 0), None)
    kept = kept[anchor:] if anchor is not None else []
    # WHY: 没载入的部分不列清单。曾经这里插过一行"更早还有 N 次工具调用未载入（op1–op12）"，
    # 拆掉了：保留期是 7 天，那个 N 会大到这行本身变成噪音，而且绝大多数没载入的调用模型
    # 本来也不需要回头看。真正需要重新打开的是被自己收缩掉的那些，它们的 cid 就写在
    # condense_ops 调用的 arguments 里、跟着重建回到上下文中——入口已经在了，不用再指一次。
    return [message for item in kept for message in item[3]]


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
    tool_modules.bind_session(session, tool_context, ui_mode=ui_mode)
    session.do_process_image = get_image_mode() != "off"
    session.keep_reasoning = get_reasoning_mode() == "keep"
    session.on_tool_result = _oplog_recorder(window)


def get_handler(session: llm.Chat):
    def handle(chunk: llm.LLMResponse) -> None:
        if chunk.role == "assistant" and chunk.content:
            message.sendmsg(chunk.content)
        if chunk.total_tokens:
            inc_call_tokens_cost(session.model, (chunk.prompt_tokens, chunk.completion_tokens))

    return handle


def _oplog_recorder(window):
    """Record each finished tool call and hand back the cid the model can name."""
    def record(result, round_id: str) -> str | None:
        return oplog.record(window, result.name, result.arguments, result.content, result.tool_call_id, round_id)
    return record


def _interject_provider(turn, in_group: bool):
    """Drain the window's queue into messages appended before the next request."""
    def provide() -> list[dict]:
        return [event2chat(event, in_group) for event in turn.take_pending()]
    return provide


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
        turn.mark_trigger()
        return
    in_group = event.get("group_id") is not None
    try:
        while True:
            _run_chat(model, turn, in_group)
            if turn.cancelled:
                return
            if not context.finish_turn(window, turn):
                return
    finally:
        context.end_turn(window, turn)


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
    ("help", "显示这份帮助"),
    ("model", "查看当前模型"),
    ("model <selection>", "查看指定模型信息"),
    ("models", "列出当前供应商模型"),
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
)
_SUBCOMMAND_NAMES = {pattern.partition(" ")[0] for pattern, _description in _SUBCOMMAND_HELP}


def _subcommand_help() -> str:
    return "\n".join(f"{pattern}\n    {description}" for pattern, description in _SUBCOMMAND_HELP)


def _format_model(selection: str, attributes: dict) -> str:
    return f"{selection}\n    {attributes.get('prompt_price', '-')} {attributes.get('completion_price', '-')} {'👀' if attributes.get('vision') else ''} {'⚙️' if attributes.get('function_calling') else ''}"


def _first_argument(value: str) -> tuple[str, str]:
    if not value.strip():
        return "", ""
    return text.read_params(" " + value.strip(), read_str=True)


def _list_argument(value: str) -> list:
    parsed = ast.literal_eval(value)
    if not isinstance(parsed, list):
        raise ValueError("参数必须是 list")
    return parsed


def _subcommand(value: str):
    value = value.strip()
    name, _, tail = value.partition(" ")
    tail = tail.strip()
    data = getchatstorage()
    if name == "help" and not tail:
        return _subcommand_help()
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
        return "模型 输入价格 输出价格 (单位: 元/(1m token)) 视觉识别 函数调用\n" + _format_model(selection, attributes)
    if name == "models" and not tail:
        provider = llm.resolve_model(llm_config, get_model(data))[0]
        models = llm_config.get("providers", {}).get(provider, {}).get("models", {})
        return "\n".join(["模型 输入价格 输出价格 (单位: 元/(1m token)) 视觉识别 函数调用", *(_format_model(f"{provider}/{model}", attributes) for model, attributes in models.items())])
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
        # (记进了不该记的东西、或者收缩坏了)时，这是唯一不用改代码就能恢复的入口。
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
    session.chat(recall_func=get_handler(session), description_cache=description_cache)


def on_load(ctx) -> None:
    global settings, prompts, chat_groups, description_cache, llm_config, max_token, max_msg
    from mods import is_available

    missing = [name for name in ("identity", "image", "llm", "storage") if not is_available(name)]
    if missing:
        raise RuntimeError("chat requires available mods: " + ", ".join(missing))

    settings = storage.get("", "settings", list)
    prompts = storage.get("llm_system", "prompts")
    chat_groups = storage.get("", "chat_groups", list)
    description_cache = storage.get("llm_system", "description_cache")
    llm_config = llm.get_client().config
    max_token = int(llm_config.get("max_token", 4000))
    max_msg = int(llm_config.get("max_msg", 200))
