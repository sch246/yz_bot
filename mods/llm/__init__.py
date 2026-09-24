"""Provider clients, image-aware chat completion, and function tools."""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable
import json
import logging
import os
import re
import threading

from openai import OpenAI

from mods import image, watchdog
from . import console
from .models import (
    BYTECAT_PROVIDER_CONFIG,
    DEFAULT_MODEL,
    DEFAULT_VISION_MODEL,
    DEFAULT_REQUEST_TIMEOUT,
    default_config as _default_config,
    provider_config,
    resolve_model,
    split_model_selection,
)
from .tools import Tool
from .types import LLMResponse, ModelCapabilities, ToolCallResult


LOAD_AFTER = ("image", "storage", "watchdog")
_log = logging.getLogger(__name__)

# WHY: 自动图片描述路径固定使用这段 prompt（_get_image_description 调 describe_image 时
# 不传 prompt），结果按内容摘要长期缓存。改这里必须同时提升
# image.AUTO_IMAGE_DESCRIPTION_VERSION，理由见那边的注释。
# tools/image.py 的 recognize_image 走的是带自定义 prompt 的分支，不读写这份缓存。
DEFAULT_IMAGE_DESCRIPTION_PROMPT = """请详细描述图片内容，作为无视觉能力模型的上下文替代：

- 主体与文字：指出图片类型，并完整准确地转录图中所有清晰可见的文字。
- 画面细节：描述主体、人物动作、表情、关键物体及要素间关系。
- 情感与意图：若是表情包或梗图，说明其核心情感或潜在梗意。

直接输出客观描述结果，不要添加前言、总结或后续建议。"""

def build_image_description_prompt(prompt: str = "") -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        return DEFAULT_IMAGE_DESCRIPTION_PROMPT
    return f"请按以下任务识别图片：\n{prompt.strip()}\n\n只输出任务要求的图片识别结果，不要添加元话术。"


def format_image_reference(uri: str, label: str = "图片") -> str:
    return f"[{label}]" if not uri or uri.startswith("data:image/") else f"[{label}({uri})]"


def format_image_description(uri: str, description: str | None = None) -> str:
    detail = description or "图片解析失败"
    return f"[图片识别结果：{detail}]" if not uri or uri.startswith("data:image/") else f"[图片({uri})识别结果：{detail}]"


def usage_cached_tokens(usage) -> int:
    """一次响应的 usage 里，prompt 中命中缓存的那部分 token 数。

    WHY: 两家字段名不同——DeepSeek 直接给 `prompt_cache_hit_tokens`，OpenAI 一系放在
    `prompt_tokens_details.cached_tokens`。都读不到就返回 0，那时整段 prompt 按未命中价
    算：宁可高估也不漏算，命中数只有供应商能报，本地猜不出来。
    """
    value = getattr(usage, "prompt_cache_hit_tokens", None)
    if value is None:
        details = getattr(usage, "prompt_tokens_details", None)
        value = getattr(details, "cached_tokens", None) if details is not None else None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


_MAX_LISTED_TOOLS = 40


def _unavailable_message(name: str, available: list[str], problem: str | None = None) -> str:
    """一次没能执行的工具调用要回给模型什么，见 UnavailableTool。

    @param
    name: 模型叫的那个名字
    available: 本轮快照里真正可用的工具名
    problem: 非 None 时说明坏在哪一步，而不是"名字不存在"
    """
    shown = ", ".join(available[:_MAX_LISTED_TOOLS])
    if len(available) > _MAX_LISTED_TOOLS:
        shown += f"，等共 {len(available)} 个"
    head = f"工具 {name} 这次调用没能执行：{problem}。" if problem else f"工具 {name} 不在本轮可用工具里。"
    return (
        head
        + f"本轮可用：{shown}。"
        + "如果它属于某个还没激活的模块，先调用 load_tools 激活那个模块再重试；"
        + "如果名字写错了，改用上面列出的名字。"
    )


class UnavailableTool:
    """兜住一次"本轮调不到"的工具调用，让模型能在同一轮里自己改正。

    WHY: 未知名字原先在解析那段抛 KeyError，被同一个 except 连同这次调用一起吞掉：
    pending_calls 变空后那条 assistant 消息既无 tool_calls 也无内容，chat 循环直接
    return，整轮静默结束，模型连"这个名字不存在"都看不到。这里合成一条正常的 tool
    结果——assistant.tool_calls 与 tool 消息依然成套，协议合法，模型看到提示后可以
    改名字、或者先 load_tools 把模块激活起来。
    """

    def __init__(self, name: str, available: list[str], problem: str | None = None) -> None:
        self.name = name
        self._content = _unavailable_message(name, available, problem)

    def call(self, **arguments) -> str:
        return self._content


class RequiredContextError(RuntimeError):
    """A mandatory mail read failed; never send the next provider request without it."""


_MARKDOWN_IMAGE = re.compile(r"!\[.*?\]\((.*?)\)")


def _text_part(value: str) -> dict:
    return {"type": "text", "text": value}


def _image_part_uri(part: dict) -> str:
    value = part.get("image_url", part.get("image", ""))
    return value.get("url", "") if isinstance(value, dict) else str(value)


def _rewrite_text_images(value: str, replace: Callable[[str], list[dict]]) -> tuple[list[dict], int]:
    """Split one text block at its markdown images; also report how many it replaced."""
    parts: list[dict] = []
    position = 0
    replaced = 0
    for match in _MARKDOWN_IMAGE.finditer(value):
        if match.start() > position:
            parts.append(_text_part(value[position:match.start()]))
        parts.extend(replace(match.group(1)))
        replaced += 1
        position = match.end()
    if position < len(value) or not parts:
        parts.append(_text_part(value[position:]))
    if all(part.get("type") == "text" for part in parts):
        return [_text_part("".join(part["text"] for part in parts))], replaced
    return parts, replaced


def _rewrite_message_images(
    message: dict,
    replace: Callable[[str], list[dict]],
    *,
    collapse_text: bool = True,
) -> dict:
    """Copy one message with every image, markdown or part, replaced by ``replace(uri)``.

    ``collapse_text`` keeps string content a string when the result is only text,
    which is what a text-for-image substitution wants.  The vision path passes
    False so that a message which did contain an image stays in parts form even
    when preparing one of them failed.
    """
    result = message.copy()
    content = message.get("content")
    if not isinstance(content, (str, list)):
        return result
    parts: list[dict] = []
    replaced = 0
    for source in content if isinstance(content, list) else [content]:
        if isinstance(source, str):
            text_parts, count = _rewrite_text_images(source, replace)
            parts.extend(text_parts)
            replaced += count
        elif isinstance(source, dict) and source.get("type") in ("image", "image_url"):
            parts.extend(replace(_image_part_uri(source)))
            replaced += 1
        else:
            parts.append(source)
    keep_string = (
        isinstance(content, str)
        and len(parts) == 1
        and parts[0].get("type") == "text"
        and (collapse_text or not replaced)
    )
    result["content"] = parts[0]["text"] if keep_string else parts
    return result


def split_string_with_code_blocks(value: str) -> list[str]:
    result: list[str] = []
    current: list[str] = []
    in_code = False
    for line in value.split("\n"):
        if line.strip().startswith("```"):
            current.append(line)
            in_code = not in_code
            if not in_code:
                result.append("\n".join(current))
                current = []
        elif in_code:
            current.append(line)
        else:
            current.append(line)
            joined = "\n".join(current)
            if "\n\n" in joined:
                *parts, tail = joined.split("\n\n")
                result.extend(part for part in parts if part.strip())
                current = [tail]
    if current and "\n".join(current).strip():
        result.append("\n".join(current))
    return result


class _DescriptionTask:
    """One in-flight vision description, and its result for the waiters.

    WHY: 结果挂在任务上而不是让等待方回读自己的缓存。去重键因此不需要缓存身份，
    见 LLMClient._get_image_description 里的说明。
    """

    __slots__ = ("event", "description")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.description: str | None = None


class LLMClient:
    def __init__(self, config: dict) -> None:
        # WHY: 这里是**活引用**，不是副本：on_load 传进来的正是 storage 的
        # ``llm_system/config`` 本身，靠它实现"文件改了，运行中的客户端立刻看得见"（见
        # storage._replace 的 WHY）。别为了"安全"改成 deepcopy，那会切断这条唯一的
        # 配置热更新路径。
        # 代价是一个自指写法会静默清空整个配置：``client.config.clear()`` 之后
        # ``client.config.update(新配置)``，只要那个"新配置"就是 client.config 本身，
        # 就是先清空再拿清空后的自己更新自己——配置变成 {}，接着一次 _write_one 把 {}
        # 写进磁盘，reload_clients() 又会把 clients 建成空的，进程从此不能说一句话
        # （2026-09-17 就是这么崩的）。热换配置要改那个**共享字典**（storage.load、
        # 或就地 update 一份独立构造的内容），再调 reload_clients()。
        self.config = config
        self.clients: dict[str, OpenAI] = {}
        self._description_inflight: dict[tuple[str, object], _DescriptionTask] = {}
        self._description_lock = threading.Lock()
        self.reload_clients()

    @staticmethod
    def _resolve_config_value(value) -> str | None:
        """Read a config value which may name an environment variable.

        WHY: 裸字符串先当环境变量名查，是**主机制**而不是宽松兜底——models.py 的
        default_config() 里每个 provider 写的都是 "OPENAI_BASE_URL" 这样的裸名字，
        ``${VAR}`` 只是需要显式区分时的备用写法。所以密钥和地址从来不进版本控制。
        代价是一个恰好与环境变量同名的字面值会被静默替换。这个风险有意接受：撞车概率
        很低，而且现有配置很可能已经在用裸名字这个特性了，改掉会直接弄坏它们。
        """
        if not isinstance(value, str):
            return value
        if value.startswith("${") and value.endswith("}"):
            return os.getenv(value[2:-1])
        return os.getenv(value, value)

    def reload_clients(self) -> None:
        self.clients.clear()
        for provider, config in self.config.get("providers", {}).items():
            base_url = self._resolve_config_value(config.get("base_url"))
            api_key = self._resolve_config_value(config.get("api_key"))
            options = {key: value for key, value in {"base_url": base_url, "api_key": api_key}.items() if value is not None}
            # 超时设在 client 上，所以 generate_response、流式读取和 describe_image
            # 全都继承它；见 models.DEFAULT_REQUEST_TIMEOUT。
            options["timeout"] = self.config.get("request_timeout") or DEFAULT_REQUEST_TIMEOUT
            self.clients[provider] = OpenAI(**options)

    def get_model_capabilities(self, model: str) -> ModelCapabilities:
        try:
            capabilities = resolve_model(self.config, model)[2]
        except ValueError:
            return ModelCapabilities()
        known = {field: capabilities.get(field, default) for field, default in {
            "vision": False,
            "tool_images": False,
            "function_calling": False,
            "prompt_price": 0.0,
            "prompt_cached_price": 0.0,
            "completion_price": 0.0,
        }.items()}
        return ModelCapabilities(**known)

    def get_vision_model(self) -> str | None:
        selection = self.config.get("vision_model")
        if not selection:
            return None
        try:
            _, _, capabilities = resolve_model(self.config, selection)
        except ValueError:
            return None
        return selection if capabilities.get("vision") else None

    def list_models(self, provider: str) -> list[str] | None:
        """问 provider 要它当前提供的模型 id 列表；取不到返回 None。

        WHY: 模型清单的权威在对端，本地 models 只是价格与能力元数据，所以能取到就以对端
        为准。取不到时退回本地配置而不是抛给调用方——一条查看用的命令不该因为网络或密钥
        失败变成报错。失败原因进 llm 流，聊天里只说已回退，见 chat._models_report。
        """
        client = self.clients.get(provider)
        if client is None:
            return None
        try:
            page = client.models.list()
        except Exception as error:
            # 调用方回退到本地列表，这是一次被恢复的失败：红字进 llm 流。
            console.error(f"取 {provider} 的在线模型列表失败：{error}")
            return None
        items = getattr(page, "data", page)
        return [str(item.id) for item in items if getattr(item, "id", None)]

    @staticmethod
    def _replace_images_with_text(messages: list[dict], description: str = "") -> list[dict]:
        def replace(uri: str) -> list[dict]:
            if description:
                return [_text_part(format_image_description(uri, description))]
            return [_text_part(format_image_reference(uri))]

        return [_rewrite_message_images(message, replace) for message in messages]

    @staticmethod
    def _convert_images(messages: list[dict], convert_url: Callable[[str], str], allow_tool_images: bool = False) -> list[dict]:
        def replace(uri: str) -> list[dict]:
            # WHY: 一次对话里同一张图只走一遍这条路径。台账判定失败的直接给占位文本，
            # 不再重复下载、也不再每轮报一次错；已经查过的成功图静默复用，不刷日志。
            if image.checked_failed(uri):
                return [_text_part(format_image_description(uri))]
            quiet = image.checked_in_conversation(uri)
            parts: list[dict] = []
            try:
                if not quiet:
                    console.notice(f"🖼️ 正在准备视觉图片：{console.format_uri(uri)}")
                data_uri = uri if uri.startswith("data:") else convert_url(uri)
                if uri and not uri.startswith("data:"):
                    parts.append(_text_part(f"[下方图片的原始链接: {uri}]"))
                slices, split = image.split_long_image_data_uri(data_uri)
                parts.extend({"type": "image_url", "image_url": {"url": value}} for value in slices)
                if split:
                    parts.append(_text_part(image.AUTO_IMAGE_SPLIT_PROMPT))
                    if not quiet:
                        console.notice(f"✅ 长图已切分为 {len(slices)} 张视觉输入")
                elif not quiet:
                    console.notice("✅ 视觉图片已准备")
            except Exception as error:
                if not quiet:
                    console.error(
                        f"❌ 图片处理失败（{console.format_uri(uri)}）：{error}"
                    )
                parts.append(_text_part(format_image_description(uri)))
            return parts

        result = []
        # WHY: 除 user 之外还放行 tool，是因为"图片只能出现在 user 消息里"是文档的说法，不是
        # 对端的行为：DeepSeek 的 chat/completions 实收 tool 消息里的 image_url（见
        # ModelCapabilities.tool_images）。放不放行由能力位按模型登记，未登记的仍旧降级成
        # 占位文字。
        roles = {"user", "tool"} if allow_tool_images else {"user"}
        for message in messages:
            if message.get("role") not in roles:
                result.extend(LLMClient._replace_images_with_text([message]))
                continue
            result.append(_rewrite_message_images(message, replace, collapse_text=False))
        return result

    def _get_image_description(self, uri: str, vision_model: str, description_cache: dict) -> str | None:
        identities = []
        digest = image.get_cached_image_digest(uri)
        if digest:
            identities.append(digest)
        intrinsic = image.intrinsic_image_description_identity(uri)
        if intrinsic and intrinsic not in identities:
            identities.append(intrinsic)
        with self._description_lock:
            removed = image.maybe_prune_description_cache(description_cache)
            if removed:
                console.notice(f"🧹 已清理 {removed} 条过期图片描述缓存")
            for identity in identities:
                cached = image.get_cached_description(description_cache, identity)
                if cached is not None:
                    algorithm = identity.partition(":")[0] if ":" in identity else "sha256"
                    console.notice(f"✅ 图片描述缓存命中：{algorithm}")
                    console.notice(f"    {cached}")
                    return cached
        if digest is None:
            console.notice(f"🔎 正在解析图片内容：{console.format_uri(uri)}")
            _, _, digest = image.resolve_image_with_digest(uri)
            identities.insert(0, digest)
        # WHY: 去重键只有内容摘要和描述版本，不含缓存身份。原先是
        # (id(description_cache), digest, version)——用字典的内存地址做键，而 id() 在
        # 对象被回收后会被复用，新的 cache 可能拿到同一个地址并撞上残留键。
        # 同一个仓库里有正确写法可对照：image.maybe_prune_description_cache 也按 id(cache)
        # 索引，但它把 cache 对象本身一起存下来并用 `is` 校验，所以能识破地址复用。
        # 这里选的是更直接的路：既然键里不再区分缓存，去重就跨聊天空间生效——同一张图
        # 在两个群同时出现只调一次视觉模型。代价是等待方的缓存里没有这一条，所以结果由
        # _DescriptionTask 持有，等待方拿到后各自写进自己的缓存，而不是回读。
        key = (digest, image.AUTO_IMAGE_DESCRIPTION_VERSION)
        with self._description_lock:
            for identity in identities:
                cached = image.get_cached_description(description_cache, identity)
                if cached is not None:
                    algorithm = identity.partition(":")[0] if ":" in identity else "sha256"
                    console.notice(f"✅ 图片描述缓存命中：{algorithm}")
                    console.notice(f"    {cached}")
                    return cached
            task = self._description_inflight.get(key)
            owner = task is None
            if owner:
                task = _DescriptionTask()
                self._description_inflight[key] = task
        if not owner:
            console.notice(f"⏳ 等待同一图片的描述任务：sha256:{digest[:12]}")
            task.event.wait()
            description = task.description
            if description is None:
                return None
            with self._description_lock:
                removed = image.cache_description(description_cache, digest, description)
            console.notice(f"✅ 等待中的图片描述已缓存：sha256:{digest[:12]}")
            console.notice(f"    {description}")
            if removed:
                console.notice(f"🧹 已清理 {removed} 条过期图片描述缓存")
            return description
        try:
            console.notice(f"👁️ 使用 {vision_model} 生成图片描述…")
            description = self.describe_image(uri, model=vision_model)
            if description:
                with self._description_lock:
                    removed = image.cache_description(description_cache, digest, description)
                console.notice(f"✅ 图片描述已缓存：sha256:{digest[:12]}")
                if removed:
                    console.notice(f"🧹 已清理 {removed} 条过期图片描述缓存")
            else:
                console.error("⚠️ 视觉模型未返回图片描述，本次不缓存")
            task.description = description
            return description
        finally:
            # 先摘掉登记再放行：新来的请求会开一个新任务，而不是拿到这个已完成的。
            # 异常路径同样走到这里，此时 description 仍是 None，等待方据此回退。
            with self._description_lock:
                self._description_inflight.pop(key, None)
                task.event.set()

    def _describe_images(self, messages: list[dict], cache: dict) -> list[dict]:
        vision_model = self.get_vision_model()
        if not vision_model:
            console.error("⚠️ 目标模型不支持视觉，且未配置可用的图片描述模型")
            return self._replace_images_with_text(messages, "图片，未配置可用的视觉模型")

        def replace(uri: str) -> list[dict]:
            # WHY: 同 _convert_images——一次对话里同一张图只检查一遍。已判定失败的直接给
            # 占位文本（不再重试、不再报错），已查过的成功图静默复用那段描述。
            if image.checked_failed(uri):
                return [_text_part(format_image_description(uri))]
            quiet = image.checked_in_conversation(uri)
            if not quiet:
                console.notice(f"🖼️ 检查图片：{console.format_uri(uri)}")
            try:
                description = self._get_image_description(uri, vision_model, cache)
            except Exception as error:
                if not quiet:
                    console.error(f"❌ 图片描述失败：{error}")
                description = None
            return [_text_part(format_image_description(uri, description))]

        return [_rewrite_message_images(message, replace) for message in messages]

    def describe_image(self, uri: str, prompt: str = "", model: str | None = None) -> str | None:
        selection = model or self.get_vision_model()
        if not selection:
            return None
        provider, api_model, _ = resolve_model(self.config, selection)
        client = self.clients.get(provider)
        if client is None:
            raise ValueError(f"Provider {provider} not configured")
        data_uri = uri if uri.startswith("data:image/") else image.image_uri_to_data_uri(uri)
        slices, split = image.split_long_image_data_uri(data_uri)
        text = build_image_description_prompt(prompt)
        if split:
            text += "\n\n" + image.AUTO_IMAGE_SPLIT_PROMPT
            console.notice(f"🧩 图片较长，将以 {len(slices)} 个切片请求 {selection}")
        console.notice(f"等待 {selection} 的图片识别响应…")
        response = client.chat.completions.create(
            model=api_model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": text},
                *({"type": "image_url", "image_url": {"url": value}} for value in slices),
            ]}],
        )
        description = response.choices[0].message.content
        if description:
            console.notice(f"✅ 视觉模型调用成功：{console.format_uri(uri)}")
            console.notice(f"    {description}")
        return description

    def generate_response(self, messages: list[dict], tools: list[Tool] | None = None, tool_choice: str | dict | None = None, model: str | None = None, stream: bool = True, description_cache: dict | None = None, do_process_image: bool | None = None, logged: int = 0):
        selection = model or self.config["default_model"]
        provider, api_model, raw_capabilities = resolve_model(self.config, selection)
        client = self.clients.get(provider)
        if client is None:
            raise ValueError(f"Provider {provider} not configured")
        capabilities = ModelCapabilities(**{key: raw_capabilities.get(key, default) for key, default in {
            "vision": False, "function_calling": False, "tool_images": False,
            "prompt_price": 0.0, "prompt_cached_price": 0.0, "completion_price": 0.0,
        }.items()})
        if do_process_image:
            messages = self._convert_images(messages, image.image_uri_to_data_uri, capabilities.tool_images) if capabilities.vision else self._describe_images(messages, description_cache or {})
        else:
            messages = self._replace_images_with_text(messages)
        # Image conversion is one output message per input message, so the
        # caller's count still lines up with what is about to be sent.
        console.print_request(selection, messages, logged)
        params = {"model": api_model, "messages": messages, "stream": stream}
        if capabilities.function_calling and tools:
            params["tools"] = [tool.description for tool in tools]
            if tool_choice:
                params["tool_choice"] = tool_choice
        if stream:
            params["stream_options"] = {"include_usage": True}
            return self._stream_response(client, params, selection)
        return self._non_stream_response(client, params, selection)

    @staticmethod
    def _stream_response(client: OpenAI, params: dict, model: str) -> Generator[LLMResponse, None, LLMResponse]:
        buffer = ""
        assistant_content = ""
        reasoning_content: str | None = None
        role = "assistant"
        tool_calls: list[dict] = []
        usage = None
        finished = False
        output = console.StreamPrinter(model)
        try:
            for chunk in client.chat.completions.create(**params):
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                if not getattr(chunk, "choices", None):
                    continue
                choice = chunk.choices[0]
                finish_reason = getattr(choice, "finish_reason", None)
                if finish_reason is not None:
                    if finished or finish_reason not in ("stop", "tool_calls", "function_call"):
                        raise RuntimeError(f"模型流未完整结束: {finish_reason}")
                    finished = True
                elif finished:
                    raise RuntimeError("模型在结束标记后继续生成")
                delta = choice.delta
                if getattr(delta, "role", None):
                    role = delta.role
                data = delta.to_dict(exclude_unset=False)
                reasoning = data.get("reasoning_content")
                if reasoning is not None:
                    reasoning_content = (reasoning_content or "") + reasoning
                    if reasoning:
                        output.chunk(reasoning, role, "think")
                if content_delta := data.get("content"):
                    output.chunk(content_delta, role)
                    assistant_content += content_delta
                    buffer += content_delta
                    if "\n\n" in buffer:
                        parts = split_string_with_code_blocks(buffer)
                        if len(parts) > 1:
                            for part in parts[:-1]:
                                yield LLMResponse(part, role)
                            buffer = parts[-1]
                for call in getattr(delta, "tool_calls", None) or []:
                    index = call.index if call.index is not None else len(tool_calls)
                    while len(tool_calls) <= index:
                        tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    target = tool_calls[index]
                    if call.id:
                        target["id"] = call.id
                    if call.function.name:
                        target["function"]["name"] = call.function.name
                    if call.function.arguments:
                        target["function"]["arguments"] += call.function.arguments
            if not finished:
                raise RuntimeError("模型流缺少结束标记，拒绝派发行动")
            output.finish()
            if buffer.strip():
                yield LLMResponse(buffer.strip(), role)
            for call in tool_calls:
                if call["id"] and call["function"]["name"]:
                    output.tool_call(
                        role,
                        call["function"]["name"],
                        call["function"]["arguments"],
                    )
                    yield LLMResponse(
                        json.dumps(call, ensure_ascii=False),
                        "tool",
                    )
            if usage:
                yield LLMResponse("", role, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens, cached_tokens=usage_cached_tokens(usage))
            return LLMResponse(
                assistant_content,
                role,
                reasoning_content=reasoning_content,
            )
        except Exception as error:
            output.finish()
            console.error(f"流式响应处理失败：{error}")
            raise

    @staticmethod
    def _non_stream_response(client: OpenAI, params: dict, model: str) -> Generator[LLMResponse, None, LLMResponse]:
        response = client.chat.completions.create(**params)
        finish_reason = getattr(response.choices[0], "finish_reason", None)
        if finish_reason not in ("stop", "tool_calls", "function_call"):
            raise RuntimeError(f"模型响应未完整结束: {finish_reason}")
        message = response.choices[0].message
        reasoning_content = getattr(message, "reasoning_content", None)
        for call in message.tool_calls or []:
            role = message.role or "assistant"
            console.message(
                call.function.name + call.function.arguments,
                "tool",
                label=f"{role}({model}): ",
                label_role=role,
            )
            yield LLMResponse(
                json.dumps({"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}}, ensure_ascii=False),
                "tool",
            )
        if message.content:
            role = message.role or "assistant"
            console.message(message.content, role, label=f"{role}({model}): ", label_role=role)
            yield LLMResponse(message.content, role)
        if response.usage:
            yield LLMResponse("", "assistant", response.usage.prompt_tokens, response.usage.completion_tokens, response.usage.total_tokens, cached_tokens=usage_cached_tokens(response.usage))
        return LLMResponse(
            message.content or "",
            message.role or "assistant",
            reasoning_content=reasoning_content,
        )

    def chat(self, messages: list[dict], tools: list[Tool] | Callable[[], list[Tool]] | None = None, tool_choice: str | dict | None = None, model: str | None = None, stream: bool = True, description_cache: dict | None = None, do_process_image: bool | None = None, on_round: Callable[[], list[dict]] | None = None, should_stop: Callable[[], bool] | None = None, hints: Callable[[], list[dict]] | None = None, keep_reasoning: bool = True, on_output: Callable[[dict, list[dict]], str | tuple[str, dict] | None] | None = None, on_results: Callable[[str, list[ToolCallResult]], None] | None = None, turn_done: Callable[[], bool] | None = None, on_action: Callable[[str | None], None] | None = None) -> Generator[LLMResponse, None, None]:
        # Every message appended below is printed live as it happens, so each
        # further round only logs what it has not shown yet -- usually nothing.
        logged = 0
        # WHY: 工具循环有意没有最大轮数、总时限、确认步骤或副作用回滚，恢复手段是
        # .reboot。取舍与它暴露在同一条提示注入路径上的能力都记在 docs/llm.md 的
        # 「当前信任边界与维护取舍」一节——加限制前先读那里，这不是漏了。
        #
        # WHY: 输出写入失败必须阻止工具派发，否则模型行动已经发生，却没有可反查的来源。
        # 不要退化成"到 N 轮就自动截断"：那会在结论产出之前把前提砍掉。压缩由模型在得出
        # 结论时显式发起，这正是主流 agent 用子代理绕开、而没有正面解决的那件事。
        #
        # WHY: on_round 在每次请求前读 mail；执行时暂存的原生工具配对必须先替换成
        # 单条输出投影，才能按顺序读出夹在输出和结果之间的插话。
        while True:
            if should_stop is not None and should_stop():
                return
            if on_round is not None:
                messages.extend(on_round())
            # Freeze one tool snapshot for both the request schema and the
            # calls returned by that request. A tool may mutate this Chat's
            # mapping; the new snapshot is observed by the next iteration.
            tools_snapshot = tools() if callable(tools) else list(tools or [])
            console.notice(f"等待 {model or self.config['default_model']} 的响应…")
            # WHY: hint 只挂在**发出去的那一份**上，永远不写回 messages。这是它与
            # on_round 的唯一区别，也是它存在的理由：on_round 追加的东西进历史、可回放、
            # 会一直留着；hint 是"当前状态"，每次子请求重新生成一次，旧的自然消失，不会
            # 在上下文里堆出几代互相矛盾的副本。因此 hint 里只放随时可重算的东西，不放
            # 任何"只此一次、错过就没有"的信息——那种必须走 on_round。
            outgoing = (messages + hints()) if hints is not None else messages
            response = iter(self.generate_response(outgoing, tools_snapshot, tool_choice, model, stream, description_cache, do_process_image, logged))
            mapping = {tool.description["function"]["name"]: tool for tool in tools_snapshot}
            pending_calls = []
            # Yielded chunks remain the live display/tool stream.  The return
            # value supplies response-wide content fields; this loop combines
            # them with collected tool calls into the one history message.
            while True:
                if should_stop is not None and should_stop():
                    # WHY: 逐 chunk 检查是 ^C 能真正打断生成的地方；close() 关掉底层
                    # HTTP 流，不然请求会一直读到模型自己说完。已经 yield 出去的段落
                    # 已经发进 QQ 了，收不回来；这里剩下的半条 assistant 消息直接丢弃，
                    # 因为 chat.chat 每轮都从 history 重建 messages，本轮的列表是一次性的。
                    # 仍有一个够不到的窗口：卡在等待第一个 chunk 时无法打断，要等它到达。
                    response.close()
                    return
                try:
                    chunk = next(response)
                except StopIteration as completed:
                    assistant = completed.value or LLMResponse("", "assistant")
                    break
                if chunk.role != "tool":
                    yield chunk
                    continue
                try:
                    call = json.loads(chunk.content)
                    function = call["function"]
                except Exception as error:
                    # WHY: 这里剩下的只有"协议层就坏了"的情形，没有可自救的东西，
                    # 记录并跳过这一次；"名字不在快照里"已经不算失败，见 UnavailableTool。
                    _log.exception("failed to parse LLM tool call")
                    console.error(f"工具调用解析失败：{error}")
                    yield chunk
                    continue
                name = function.get("name") or ""
                tool = mapping.get(name)
                if tool is None:
                    # WHY: 多半是模型照上一轮的印象直呼（激活态已经变了），或者自己写错。
                    # 丢掉它会让整轮静默结束，所以换成一个会说话的占位工具留在这批调用里。
                    tool = UnavailableTool(name, sorted(mapping))
                    arguments: dict = {}
                else:
                    try:
                        arguments = json.loads(function["arguments"] or "{}")
                    except Exception as error:
                        _log.exception("failed to parse LLM tool arguments")
                        console.error(f"工具参数解析失败：{error}")
                        tool = UnavailableTool(name, sorted(mapping), f"参数不是合法 JSON（{error}）")
                        arguments = {}
                pending_calls.append((call, tool, arguments))

            assistant_message = {"role": assistant.role, "content": assistant.content}
            # WHY: 原生载体在执行行动时仍保留思考字段，供不读取 mail 的单句请求和
            # 独立子代理使用；连续聊天完成行动后撤掉载体，不再传思考原文。
            # drop 仍置空串而非删字段，避免向 DeepSeek 编造「从未思考」。
            if assistant.reasoning_content is not None:
                assistant_message["reasoning_content"] = assistant.reasoning_content if keep_reasoning else ""
            if pending_calls:
                assistant_message["tool_calls"] = [call for call, _, _ in pending_calls]
            recorded_output = {**assistant_message, "reasoning_content": assistant.reasoning_content}
            recorded = on_output(recorded_output, assistant_message.get("tool_calls", [])) if on_output else None
            output_id = recorded[0] if isinstance(recorded, tuple) else recorded
            if isinstance(recorded, str) and pending_calls:
                references = ", ".join(f"{recorded}#{position + 1}" for position in range(len(pending_calls)))
                assistant_message["content"] = f"{assistant_message['content']}\n行动引用：{references}"
            messages.append(assistant_message)

            if not pending_calls:
                if isinstance(recorded, tuple):
                    messages[-1:] = [recorded[1]]
                return
            results: list[ToolCallResult] = []
            for position, (call, tool, arguments) in enumerate(pending_calls):
                if should_stop is not None and should_stop():
                    # WHY: 这是 ^C 够得到的最后一个检查点。工具是同步执行的，原先只在这一批
                    # 全部跑完之后才有机会看这个标记——于是这批里只要有一个调用卡住，排在它
                    # 后面的每一个都照跑，^C 迟迟不生效。已经补齐的 tool 消息就留在那儿：这一轮
                    # 的 messages 随轮次结束丢弃（每轮都从 history 重建），不做半轮修补。
                    if results and on_results is not None and output_id is not None:
                        on_results(output_id, results)
                    return
                function = call["function"]
                # WHY: 登记这次调用期间 spawn 的子进程，^C 才能真的把它们 kill 掉（卡住的
                # grep 就是这一类）。有意**不**登记本线程：工具写到一半时被掀翻，换来的不是
                # "停住了"而是"半成品"。见 mods/watchdog。
                job = watchdog.begin()
                try:
                    if on_action is not None:
                        on_action(f"{output_id}#{position + 1}" if output_id is not None else None)
                    content = str(tool.call(**arguments))
                except Exception as error:
                    content = f"工具调用失败: {type(error).__name__}: {error}"
                    console.error(f" -> {content}")
                else:
                    console.message(f" -> {content}", "tool")
                finally:
                    if on_action is not None:
                        on_action(None)
                    watchdog.end(job)
                result = ToolCallResult(call["id"], function["name"], function["arguments"], content)
                results.append(result)
            native_results = [{"role": "tool", "tool_call_id": result.tool_call_id, "content": result.content}
                              for result in results]
            messages.extend(native_results)
            if on_results is not None and output_id is not None:
                on_results(output_id, results)
            if isinstance(recorded, tuple):
                # WHY: The native pair is only an execution carrier. Its result would
                # disclose C before unread mail B in the next provider request.
                if not any(message is assistant_message for message in messages):
                    raise RuntimeError("执行行动期间丢失模型输出载体，拒绝后续请求")
                native_ids = {id(message) for message in native_results}
                messages[:] = [recorded[1] if message is assistant_message else message
                               for message in messages if id(message) not in native_ids]
            logged = len(messages)
            # WHY: 这是「这一轮的活干完了」的检查点，和 should_stop 是**两件事**，别合并。
            # should_stop 是 ^C：外面要求立刻停，停下来这一轮就算被取消了；turn_done 是
            # 工具自己说「我做的这件事就是本轮的最后一件」——轮正常结束。混用会让
            # chat.chat 外层那句 `if turn.cancelled: return` 把正常结束误判成被取消，于是
            # 不再去看 trigger，等着续跑的那一轮就被丢掉。
            # WHY: 位置在这一批 tool 结果**全部追加之后**。一批里有几个并发调用，只要其中
            # 一个声明了结束，这一批的结果都已经进了 messages——它们会跟着 oplog 轨道在
            # 下一轮重建回来，不会因为提前 return 而丢账。代价是这一批里别的工具的结果，
            # 模型这一轮看不到了；那是它自己的选择（声明结束的是它）。
            # WHY: 这一层不认识任何具体工具。谁有资格结束一轮、凭什么结束，全在调用方，
            # 见 mods/chat 给它的那个闭包。这里只问一句「完了吗」。
            if turn_done is not None and turn_done():
                return


class Chat:
    def __init__(self, model: str | None = None, messages: list | None = None, functions: dict | list | None = None, chat_client: LLMClient | None = None, recall_func: Callable | None = None, description_cache: dict | None = None, do_process_image: bool | None = None) -> None:
        self.chat_client = chat_client
        self.model = model or (chat_client.config["default_model"] if chat_client else None)
        self.recall_func = recall_func
        self.description_cache = description_cache
        self.do_process_image = do_process_image
        self.messages: list[dict] = []
        self.functions: dict[str, Tool] = {}
        # WHY: 追加式上下文。每个 provider 在每次模型子请求前被调用一次，返回要追加到
        # 末尾的消息；前面的消息一个字都不改，所以前缀缓存不会失效。这是模块目录、插话
        # 这类"会中途变化的东西"进入上下文的唯一正路——不要回到就地改写头部消息的老做法。
        self.context_providers: list[Callable[[], list[dict]]] = []
        # 返回 True 表示这一轮应当就地停下（^C 打断）。
        self.should_stop: Callable[[], bool] | None = None
        # 返回 True 表示这一轮的活已经干完，可以正常结束（不是被打断）。见 LLMClient.chat
        # 里那个检查点的 WHY——它和 should_stop 的区别是"完成"与"取消"，后果不同。
        self.turn_done: Callable[[], bool] | None = None
        # WHY: hint 与 context_providers 是两层，别合并。provider 追加进 messages——进
        # 历史、留下来；hint 每次子请求重新渲染并挂在末尾，不进 messages。判据是这条：
        # 频繁变化、且随时可以重算的状态放 hint；"发生过一次"的事实放 provider。
        # 它和 system 提示词一样支持用函数生成，只是重置时机不同：system 在建会话时定一次，
        # hint 每个子请求重来一次。
        self.hints: list[Callable[[], str] | str] = []
        # WHY: 默认保留原生载体的思考，供单句请求和独立子代理工具循环；连续
        # 聊天在下一次子请求前把载体换成统一投影，keep/drop 均不保留全文。
        self.keep_reasoning: bool = True
        self.on_output: Callable[[dict, list[dict]], str | tuple[str, dict] | None] | None = None
        self.on_results: Callable[[str, list[ToolCallResult]], None] | None = None
        # WHY: 私有模型已通过原生结果读到的行动，主窗口此时仍可能没读 mail。
        # 这里只存该 Chat 的已读引用，用于限定待编号结果的反查，不复制结果正文或赋号。
        self.native_seen_calls: set[str] = set()
        self.active_action: str | None = None
        self.reads_window_mail = False
        if messages is not None:
            self.set_messages(messages)
        if functions is not None:
            self.set_tools(functions)

    def add_message(self, content, role: str = "user", **values):
        if callable(content):
            content = content(self)
        if isinstance(content, (str, list)):
            content = {"role": role, "content": content, **values}
        elif isinstance(content, LLMResponse):
            content = {"role": content.role, "content": content.content}
        if not isinstance(content, dict):
            raise TypeError(f"消息格式错误: {type(content)}")
        self.messages.append(content)
        return content

    def set_messages(self, messages: list) -> None:
        self.messages = []
        self.native_seen_calls.clear()
        for value in messages:
            self.add_message(value)

    def add_tool(self, function: Callable | Tool, name: str | None = None):
        name = name or (function.description["function"]["name"] if isinstance(function, Tool) else function.__name__)
        tool = function if isinstance(function, Tool) else Tool(function, name)
        if name in self.functions and self.functions[name].call is not tool.call:
            raise KeyError(f"同名函数 {name} 已存在")
        self.functions[name] = tool
        return function

    def set_tools(self, functions: dict | list) -> None:
        self.functions = {}
        for name, function in functions.items() if isinstance(functions, dict) else ((None, value) for value in functions):
            self.add_tool(function, name)

    def add_hint(self, source: Callable[[], str] | str):
        """Register one always-at-the-end, never-in-history block."""
        self.hints.append(source)
        return source

    def render_hints(self) -> list[dict]:
        parts: list[str] = []
        for source in self.hints:
            try:
                value = source() if callable(source) else source
            except Exception:
                # 提醒坏掉不该让整轮聊天失败：它按定义是可有可无的补充。
                _log.exception("hint source failed")
                continue
            if value and str(value).strip():
                parts.append(str(value).strip())
        # 合成一条：多个 hint 之间没有先后语义，拆成多条只会占更多消息位。
        return [{"role": "user", "content": "\n\n".join(parts)}] if parts else []

    def add_context_provider(self, provider: Callable[[], list[dict]]):
        """Register one source of messages appended before each sub-request."""
        self.context_providers.append(provider)
        return provider

    def _collect_context(self) -> list[dict]:
        collected: list[dict] = []
        for provider in self.context_providers:
            try:
                produced = provider() or []
            except RequiredContextError:
                raise
            except Exception:
                # 一个 provider 坏掉不该让整轮聊天失败：它提供的是补充上下文，不是主体。
                _log.exception("context provider failed")
                continue
            for value in produced:
                collected.append(value if isinstance(value, dict) else {"role": "user", "content": str(value)})
        return collected

    def condense_native_calls(self, tool_call_ids: Iterable[str], *, sources: set[str] | None = None,
                              apply: bool = True) -> int:
        """Remove completed native execution pairs from a private Chat's live view."""
        requested = {str(value) for value in tool_call_ids if value}
        if not requested:
            return 0
        drop: set[int] = set()
        for index, message in enumerate(self.messages):
            calls = message.get("tool_calls")
            if not calls:
                continue
            if sources and not any(f"{source}#" in str(message.get("content", "")) for source in sources):
                continue
            group = {str(call["id"]) for call in calls}
            if not group & requested:
                continue
            if group - requested:
                raise ValueError("同一输出里的行动必须一起收缩")
            answered = {position for position, value in enumerate(self.messages)
                        if value.get("role") == "tool" and str(value.get("tool_call_id")) in group}
            if len(answered) != len(group):
                raise ValueError("这一输出仍有行动未返回，不能收缩")
            drop.add(index)
            drop.update(answered)
        if apply:
            self.messages[:] = [message for index, message in enumerate(self.messages) if index not in drop]
        return len(drop)

    def get_tools(self) -> list[Tool]:
        """Build the current request's frozen tool snapshot."""
        return list(self.functions.values())

    def change_model(self, model: str) -> None:
        split_model_selection(model)
        self.model = model

    def chat(self, user_message=None, recall_func: Callable | None = None, stream: bool = True, tool_choice: str | dict | None = "auto", description_cache: dict | None = None, do_process_image: bool | None = None) -> list[LLMResponse]:
        if self.chat_client is None:
            raise ValueError("聊天客户端未配置")
        if user_message is not None:
            self.add_message(user_message)
        callback = recall_func or self.recall_func
        native_sources: set[str] = set()

        def record_output(assistant: dict, calls: list[dict]):
            recorded = self.on_output(assistant, calls)
            if isinstance(recorded, str):
                native_sources.add(recorded)
            return recorded

        def record_results(source: str, results: list[ToolCallResult]) -> None:
            self.on_results(source, results)
            if source in native_sources:
                self.native_seen_calls.update(f"{source}#{position + 1}" for position in range(len(results)))

        try:
            response = self.chat_client.chat(
                self.messages,
                self.get_tools,
                tool_choice,
                self.model,
                stream,
                self.description_cache if description_cache is None else description_cache,
                self.do_process_image if do_process_image is None else do_process_image,
                self._collect_context,
                self.should_stop,
                self.render_hints,
                self.keep_reasoning,
                record_output if self.on_output is not None else None,
                record_results if self.on_results is not None else None,
                # WHY: 工具在这次生成器已经开始以后才会声明完成；直接传 self.turn_done 会把
                # 开始时的 None 按值交进去，say 后来安装的回调永远到不了检查点。
                lambda: self.turn_done() if self.turn_done is not None else False,
                lambda reference: setattr(self, "active_action", reference),
            )
            results = []
            for chunk in response:
                if callback:
                    callback(chunk)
                results.append(chunk)
            return results
        except Exception as error:
            # WHY: 有意吞掉所有异常并把错误变成一条 assistant 消息。Bot 在聊天里必须
            # 说点什么——静默死掉是最糟的失败方式，群里没人知道发生了什么。完整
            # traceback 进 _log，聊天里只留一行；那一行带 `#` 所以不会回流进上下文。
            _log.exception("LLM chat failed")
            console.error(f"LLM 聊天失败：{error}")
            # `#` 前缀让这条错误不回流进 LLM 上下文，见 chat.get_msgs 的说明。
            result = LLMResponse(f"# {error}", "assistant")
            if callback:
                callback(result)
            return [result]


client: LLMClient | None = None


def get_client() -> LLMClient:
    if client is None:
        raise RuntimeError("LLM client 尚未加载")
    return client


def on_load(ctx) -> None:
    global client
    from mods import storage

    config = storage.get("llm_system", "config")
    if not config:
        config.update(_default_config())
    client = LLMClient(config)
