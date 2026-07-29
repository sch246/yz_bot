"""QQ-window chat context, settings, tools, and the ``.chat`` command."""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime
import io
import os
import re
import threading
import time
from typing import Callable

import requests

from mods import chatlog, connect, context, cq, history, identity, image, llm, message, msgs, op, storage, text, thread
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
_cost_lock = None


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
    # A storage list is the authority; only this read-modify-write needs a lock.
    if _cost_lock is None:
        _usage_entry()[1] += price
    else:
        with _cost_lock:
            _usage_entry()[1] += price


def get_time() -> str:
    """获取当前时间。"""
    return time.strftime("现在是%Y年%m月%d日%H时%M分%S秒")


def exec_code(expr: str, code: str = "") -> str:
    """执行实时 Python 代码。

    @param
    expr: 在 code 后求值并返回的表达式
    code: 先执行的 Python 代码
    """
    event = context.current()
    if not op.require_op(event):
        return "权限不足"
    from mods import py

    buffer = io.StringIO()
    missing = object()
    original = py.loc.get("print", missing)
    py.loc["print"] = lambda *values, sep=" ", end="\n": buffer.write(sep.join(map(str, values)) + end)
    try:
        exec(code, py.loc)
        result = repr(eval(expr, py.loc))
    finally:
        if original is missing:
            py.loc.pop("print", None)
        else:
            py.loc["print"] = original
    printed = buffer.getvalue().rstrip()
    return f"[print输出]\n{printed}\n[结果] {result}" if printed else result


def poke(user_id: int) -> str:
    """戳一戳当前聊天中的用户。

    @param
    user_id: 目标用户 QQ 号
    """
    event = context.current() or {}
    group_id = event.get("group_id")
    if group_id is None and int(user_id) != int(event.get("user_id", -1)):
        return "戳一戳失败：私聊中只能戳当前对话者"
    params = {"user_id": int(user_id)}
    if group_id is not None:
        params["group_id"] = int(group_id)
    result = connect.call_api("send_poke", **params)
    return f"已戳用户 {user_id}" if result.get("retcode") == 0 else f"戳一戳失败：{result.get('wording', '接口失败')}"


def recognize_image(image_uri: str, prompt: str = "") -> str:
    """按要求识别网络或本地图片。

    @param
    image_uri: http://、https:// 或 file:// 图片 URI
    prompt: 希望视觉模型完成的任务
    """
    if not re.match(r"^(?:https?|file)://", image_uri, re.I):
        return "图片识别失败：仅支持 http://、https:// 或 file:// 图片 URI"
    if image_uri.lower().startswith("file://") and not op.require_op(context.current()):
        return "图片识别失败：本地文件需要管理员权限"
    try:
        result = llm.get_client().describe_image(image_uri, prompt)
    except (OSError, ValueError) as error:
        return f"图片识别失败：{error}"
    return result or "图片识别失败：视觉模型未返回描述"


def later_add(time: str, code: str, expr: str) -> str:
    """添加延时任务。

    @param
    time: 相对或绝对时间
    code: 到时先执行的代码
    expr: 到时求值并发送的表达式
    """
    from mods import later

    return later.run(f" add {time} {code}\n{expr}", exec_id=identity.bot_id())


def later_del(seqs: str) -> str:
    """按序号删除延时任务。

    @param
    seqs: 逗号分隔的序号或 *
    """
    from mods import later

    return later.run(f" del {seqs}", exec_id=identity.bot_id())


def _validate_generation(prompt: str, size: str, quality: str, n: int, output_format: str, prefix: str) -> str | None:
    if not isinstance(prompt, str) or not prompt.strip():
        return f"{prefix}失败：prompt 不能为空"
    if size != "1024x1024" or quality not in {"auto", "low", "medium", "high"} or output_format not in {"png", "jpeg", "webp"}:
        return f"{prefix}失败：参数不受支持"
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 10:
        return f"{prefix}失败：n 必须是 1 ~ 10 的整数"
    return None


def _send_generated_images(response: requests.Response) -> str:
    if not response.ok:
        return f"生图失败：API 返回 HTTP {response.status_code}"
    try:
        images = [item["b64_json"] for item in response.json().get("data", []) if isinstance(item, dict) and item.get("b64_json")]
    except (AttributeError, TypeError, ValueError):
        return "生图失败：API 返回了无法解析的结果"
    for value in images:
        message.sendmsg(cq.base64_to_cq(value))
    if images:
        if _cost_lock is None:
            _usage_entry()[1] += 0.13 * len(images)
        else:
            with _cost_lock:
                _usage_entry()[1] += 0.13 * len(images)
    return f"已生成并发送 {len(images)} 张图片" if images else "生图失败：API 未返回图片"


def create_image(prompt: str, size: str = "1024x1024", quality: str = "auto", n: int = 1, output_format: str = "png") -> str:
    """根据文字描述生成图片并发送。

    @param
    prompt: 图像描述
    size: 图片尺寸 enum: ["1024x1024"]
    quality: 质量 enum: ["auto", "low", "medium", "high"]
    n: 图片数量
    output_format: 格式 enum: ["png", "jpeg", "webp"]
    """
    if error := _validate_generation(prompt, size, quality, n, output_format, "生图"):
        return error
    base_url, api_key = os.getenv("BYTECAT_BASE_URL", "").rstrip("/"), os.getenv("BYTECAT_IMAGE_API_KEY")
    if not base_url or not api_key:
        return "生图失败：未配置图片 API"
    try:
        response = requests.post(f"{base_url}/images/generations", headers={"Authorization": f"Bearer {api_key}"}, json={"model": "gpt-image-2", "prompt": prompt.strip(), "size": size, "quality": quality, "n": n, "output_format": output_format}, timeout=(10, 300))
    except requests.RequestException as error:
        return f"生图失败：请求异常（{type(error).__name__}）"
    return _send_generated_images(response)


def create_image_from_references(prompt: str, image_uris: str, size: str = "1024x1024", quality: str = "auto", n: int = 1, output_format: str = "png") -> str:
    """使用参考图片生成新图并发送。

    @param
    prompt: 新图描述
    image_uris: 每行一个图片 URI
    size: 图片尺寸 enum: ["1024x1024"]
    quality: 质量 enum: ["auto", "low", "medium", "high"]
    n: 图片数量
    output_format: 格式 enum: ["png", "jpeg", "webp"]
    """
    if error := _validate_generation(prompt, size, quality, n, output_format, "参考图生图"):
        return error
    uris = list(dict.fromkeys(uri.strip() for uri in image_uris.splitlines() if uri.strip()))
    if not uris:
        return "参考图生图失败：至少需要一个图片 URI"
    if any(uri.lower().startswith("file://") for uri in uris) and not op.require_op(context.current()):
        return "参考图生图失败：本地文件需要管理员权限"
    base_url, api_key = os.getenv("BYTECAT_BASE_URL", "").rstrip("/"), os.getenv("BYTECAT_IMAGE_API_KEY")
    if not base_url or not api_key:
        return "参考图生图失败：未配置图片 API"
    try:
        with ExitStack() as stack:
            files = []
            for index, uri in enumerate(uris, 1):
                path, mime = image.resolve_image_uri(uri)
                stream = stack.enter_context(open(path, "rb"))
                files.append(("image[]", (os.path.basename(path) or f"reference-{index}", stream, mime)))
            response = requests.post(f"{base_url}/images/edits", headers={"Authorization": f"Bearer {api_key}"}, data={"model": "gpt-image-2", "prompt": prompt.strip(), "size": size, "quality": quality, "n": n, "output_format": output_format}, files=files, timeout=(10, 300))
    except (requests.RequestException, OSError, ValueError) as error:
        return f"参考图生图失败：{error}"
    return _send_generated_images(response)


def get_user_data(user_id: int) -> str:
    """查询用户数据。

    @param
    user_id: 用户 QQ 号
    """
    return str(identity.getstorage(user_id))


def set_user_data(user_id: int, key: str, value: str) -> str:
    """编辑用户数据。

    @param
    user_id: 用户 QQ 号
    key: 数据键
    value: Python 表达式，del 表示删除
    """
    current = context.current() or {}
    if int(user_id) != int(current.get("user_id", -1)) and not op.require_op(current):
        return "权限不足"
    target = identity.getstorage(user_id)
    if value == "del":
        target.pop(key, None)
    else:
        import ast

        target[key] = ast.literal_eval(value)
    return "done"


def search_mc_mod(name: str) -> str:
    """搜索 Minecraft Mod。

    @param
    name: Mod 名称关键字
    """
    from bs4 import BeautifulSoup

    response = requests.get(f"https://search.mcmod.cn/s?key={name}", timeout=15)
    result = "\n".join(item.get_text().strip() for item in BeautifulSoup(response.text, "html.parser").find_all(class_="search-result-list"))
    return result[:1000] + ("..." if len(result) > 1000 else "")


def check_mod(id: int) -> str:
    """按 mcmod ID 查询 Mod。

    @param
    id: mcmod class ID
    """
    from bs4 import BeautifulSoup

    response = requests.get(f"https://www.mcmod.cn/class/{id}.html", timeout=15)
    return "\n".join(item.get_text().strip() for item in BeautifulSoup(response.text, "html.parser").find_all(class_="text-area"))


def assign_tasks(prompt: str, tasks: str, tools: str, model: str = "deepseek/deepseek-v4-flash", max_workers: int = 5) -> str:
    """并发分派互不依赖的 LLM 子任务。

    @param
    prompt: 每个子任务共享的提示
    tasks: 每行一个任务
    tools: 每行一个允许的工具名
    model: provider/model 模型名
    max_workers: 最大并发数
    """
    task_list = [value.strip() for value in tasks.splitlines() if value.strip()]
    requested = list(dict.fromkeys(value.strip() for value in tools.splitlines() if value.strip()))
    workers = max(1, min(int(max_workers), 5))
    origin = context.current()
    available = {function.__name__: function for function in _tools()}

    def execute(task_value: str) -> tuple[str, str]:
        context.set_current(origin)
        worker_id = threading.get_ident()
        print(f"线程 {worker_id}: 开始处理 LLM 子任务", flush=True)
        try:
            session = llm.Chat(model=model, chat_client=llm.get_client())
            for name in requested:
                if name in available:
                    session.add_tool(available[name])
            session.set_messages([f"{prompt}\n{task_value}"])
            pieces = []

            def collect(chunk: llm.LLMResponse) -> None:
                if chunk.role == "assistant" and chunk.content:
                    pieces.append(str(chunk.content))
                if chunk.total_tokens:
                    inc_call_tokens_cost(model, (chunk.prompt_tokens, chunk.completion_tokens))

            session.chat(recall_func=collect)
            print(f"线程 {worker_id}: LLM 子任务完成", flush=True)
            return task_value, "".join(pieces).strip()
        except Exception as error:
            print(f"线程 {worker_id}: LLM 子任务失败：{error}", flush=True)
            return task_value, f"ERROR: {error}"
        finally:
            context.clear_current()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(execute, task_list))
    return repr(results)


def _tools() -> list[Callable]:
    tools = [get_time, exec_code, poke, recognize_image, later_add, later_del, create_image, create_image_from_references, get_user_data, set_user_data, assign_tasks, search_mc_mod, check_mod]
    from mods import get_available

    weather = get_available("weather")
    if weather is not None:
        tools.extend([weather.search_city, weather.get_realtime_weather, weather.get_daily_forecast, weather.get_hourly_forecast])
    return tools


def _base_prompt() -> list[dict]:
    return [{"role": "system", "content": f"""## 注意事项
- 你的昵称: {identity.bot_name()}
- 你的QQ号: {identity.bot_id()}；群聊 at 格式为 [CQ:at,qq=qq号]，reply 格式为 [CQ:reply,id=message_id]
- 聊天中可能不会有明显的问题，扮演好角色即可
- 如无特殊要求，请用中文回复"""}]


def init_chat(session: llm.Chat, messages: list | None = None) -> None:
    inc_call_count()
    for tool_function in _tools():
        session.add_tool(tool_function)
    prompts["base"] = _base_prompt()
    group = context.current().get("group_id") if context.current() else None
    state = {"role": "system", "content": f"当前所在群聊:{identity.getgroupname(group)}({group})"} if group is not None else {"role": "system", "content": f"当前在私聊:{identity.getname()}({context.current().get('user_id')})"}
    session.set_messages([*get_prompt(), *prompts["base"], state, *(messages or [])])
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
            print(f"❌ eager 图片捕获失败：{error}", flush=True)


def eager_cache_images(event: dict) -> None:
    if msgs.is_msg(event) and "[CQ:image" in event.get("message", ""):
        data = getchatstorage(event)
        if get_image_mode(data) == "eager":
            model = get_model(data)
            count = len(_message_image_urls(event))
            print(f"🖼️ eager 图片捕获：{count} 张，目标模型 {model}", flush=True)
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
    global settings, prompts, chat_groups, description_cache, llm_config, max_token, max_msg, _cost_lock
    import threading
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
    _cost_lock = threading.Lock()
