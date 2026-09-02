"""QQ-window chat context, settings, tools, and the ``.chat`` command."""

from __future__ import annotations

import ast
from datetime import datetime
import re
import threading
import time
from typing import Callable

from mods import chatlog, context, cq, history, identity, image, llm, log, message, msgs, storage, text, thread, tools as tool_modules
from mods.command import command
from mods.capture import capture


LOAD_AFTER = ("history", "identity", "image", "llm", "storage")

IMAGE_MODES = ("off", "lazy", "eager")
IMAGE_MODE_ALIASES = {"0": "off", "1": "lazy", "2": "eager"}

settings: list = []
prompts: dict = {}
chat_groups: list = []
description_cache: dict = {}
llm_config: dict = {}
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
        if not msgs.is_msg(event):
            return False
        for code in cq.find_all(event.get("message", "")):
            parsed = cq.load(code)
            if parsed["type"] == "at" and parsed["data"].get("qq") not in (None, "all"):
                try:
                    if int(parsed["data"]["qq"]) == int(user_id):
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


def _is_context_poke(event: dict, in_group: bool) -> bool:
    if not msgs.is_poke(event):
        return False
    return bool(in_group) or event.get("target_id") == identity.bot_id()


def get_msgs(token_limit: int | None = None, return_token: bool = False):
    token_limit = max_token if token_limit is None else token_limit
    current = context.current() or {}
    in_group = current.get("group_id") is not None
    selected = []
    for event in history.getlog(current)[:max_msg]:
        if msgs.is_msg(event):
            value = event.get("message", "")
            if value.startswith("#"):
                continue
            if value in ("聊天开始", "聊天结束"):
                break
            selected.append(event)
        elif _is_context_poke(event, in_group):
            selected.append(event)

    output = []
    used = 0
    for event in selected:
        if msgs.is_msg(event):
            converted = msg2chat(event, in_group)
            content = converted["content"]
            cost = sum(count_tokens(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text")
        else:
            kind = "群聊事件" if in_group else "私聊事件"
            converted = {"role": "user", "content": f"【{kind}】{chatlog.format_poke(event)}"}
            cost = count_tokens(converted["content"])
        used += cost
        if used > token_limit:
            break
        output.insert(0, converted)
    return (output, used) if return_token else output


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
- 聊天中可能不会有明显的问题，扮演好角色即可
- 如无特殊要求，请用中文回复"""}]


def init_chat(session: llm.Chat, messages: list | None = None) -> None:
    inc_call_count()
    prompts["base"] = _base_prompt()
    group = context.current().get("group_id") if context.current() else None
    state = {"role": "system", "content": f"当前所在群聊:{identity.getgroupname(group)}({group})"} if group is not None else {"role": "system", "content": f"当前在私聊:{identity.getname()}({context.current().get('user_id')})"}
    tool_context = tool_modules.create_context_message()
    session.set_messages([*get_prompt(), *prompts["base"], tool_context, state, *(messages or [])])
    tool_modules.bind_session(session, tool_context)
    session.do_process_image = get_image_mode() != "off"


def get_handler(session: llm.Chat):
    def handle(chunk: llm.LLMResponse) -> None:
        if chunk.role == "assistant" and chunk.content:
            message.sendmsg(chunk.content)
        if chunk.total_tokens:
            inc_call_tokens_cost(session.model, (chunk.prompt_tokens, chunk.completion_tokens))

    return handle


def chat(model: str | None = None) -> None:
    session = llm.Chat(model=model or get_model(), chat_client=llm.get_client())
    init_chat(session, get_msgs())
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
        value = event.get("message", "")
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
        return "#" + cq.escape(str(data()))
    return chat()


@capture(before="chatstart")
def capture_chat(_event: dict) -> bool:
    matched = cond()
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
        cq.unescape(event.get("message", "")),
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
