"""Provider clients, image-aware chat completion, and function tools."""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable
import json
import logging
import os
import re
import threading

from openai import OpenAI

from mods import image
from . import console
from .models import (
    BYTECAT_PROVIDER_CONFIG,
    DEFAULT_MODEL,
    DEFAULT_VISION_MODEL,
    DEFAULT_REQUEST_TIMEOUT,
    default_config as _default_config,
    resolve_model,
    split_model_selection,
)
from .tools import Tool
from .types import LLMResponse, ModelCapabilities, ToolCallResult


LOAD_AFTER = ("image", "storage")
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
            "function_calling": False,
            "prompt_price": 0.0,
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

    @staticmethod
    def _replace_images_with_text(messages: list[dict], description: str = "") -> list[dict]:
        def replace(uri: str) -> list[dict]:
            if description:
                return [_text_part(format_image_description(uri, description))]
            return [_text_part(format_image_reference(uri))]

        return [_rewrite_message_images(message, replace) for message in messages]

    @staticmethod
    def _convert_images(messages: list[dict], convert_url: Callable[[str], str]) -> list[dict]:
        def replace(uri: str) -> list[dict]:
            parts: list[dict] = []
            try:
                console.notice(f"🖼️ 正在准备视觉图片：{console.format_uri(uri)}")
                data_uri = uri if uri.startswith("data:") else convert_url(uri)
                if uri and not uri.startswith("data:"):
                    parts.append(_text_part(f"[下方图片的原始链接: {uri}]"))
                slices, split = image.split_long_image_data_uri(data_uri)
                parts.extend({"type": "image_url", "image_url": {"url": value}} for value in slices)
                if split:
                    parts.append(_text_part(image.AUTO_IMAGE_SPLIT_PROMPT))
                    console.notice(f"✅ 长图已切分为 {len(slices)} 张视觉输入")
                else:
                    console.notice("✅ 视觉图片已准备")
            except Exception as error:
                console.error(
                    f"❌ 图片处理失败（{console.format_uri(uri)}）：{error}"
                )
                parts.append(_text_part(format_image_description(uri)))
            return parts

        result = []
        for message in messages:
            # Only user messages may carry real image parts to the provider.
            if message.get("role") != "user":
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
            console.notice(f"🖼️ 检查图片：{console.format_uri(uri)}")
            try:
                description = self._get_image_description(uri, vision_model, cache)
            except Exception as error:
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
            "vision": False, "function_calling": False, "prompt_price": 0.0, "completion_price": 0.0,
        }.items()})
        if do_process_image:
            messages = self._convert_images(messages, image.image_uri_to_data_uri) if capabilities.vision else self._describe_images(messages, description_cache or {})
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
        output = console.StreamPrinter(model)
        try:
            for chunk in client.chat.completions.create(**params):
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
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
                yield LLMResponse("", role, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
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
            yield LLMResponse("", "assistant", response.usage.prompt_tokens, response.usage.completion_tokens, response.usage.total_tokens)
        return LLMResponse(
            message.content or "",
            message.role or "assistant",
            reasoning_content=reasoning_content,
        )

    def chat(self, messages: list[dict], tools: list[Tool] | Callable[[], list[Tool]] | None = None, tool_choice: str | dict | None = None, model: str | None = None, stream: bool = True, description_cache: dict | None = None, do_process_image: bool | None = None, on_round: Callable[[], list[dict]] | None = None, should_stop: Callable[[], bool] | None = None, hints: Callable[[], list[dict]] | None = None, keep_reasoning: bool = True, on_tool_result: Callable[[ToolCallResult, str], str | None] | None = None) -> Generator[LLMResponse, None, None]:
        # Every message appended below is printed live as it happens, so each
        # further round only logs what it has not shown yet -- usually nothing.
        logged = 0
        # WHY: 工具循环有意没有最大轮数、总时限、确认步骤或副作用回滚，恢复手段是
        # .reboot。取舍与它暴露在同一条提示注入路径上的能力都记在 docs/llm.md 的
        # 「当前信任边界与维护取舍」一节——加限制前先读那里，这不是漏了。
        #
        # WHY: on_tool_result 是操作历史那条平行轨道的采集点，见 mods/oplog。
        # 要解决的是这个：本轮的 assistant(tool_calls) 与 tool result 只活在这个 messages
        # 列表里，不写 chatlog，chat.get_msgs 也不重建它们，所以下一轮开始时模型不知道自己
        # 上一轮干了什么。轨道单独存，不混进 chatlog 的聊天消息。
        # 钩子返回该次调用的 cid，返回值会加到 tool result 内容前面——模型必须看得见 cid，
        # 否则它无法指名要收缩哪几条。收缩本身是 Chat.condense_calls 加 meta.condense_ops。
        # 不要退化成"到 N 轮就自动截断"：那会在结论产出之前把前提砍掉。压缩由模型在得出
        # 结论时显式发起，这正是主流 agent 用子代理绕开、而没有正面解决的那件事。
        #
        # WHY: on_round 是"追加式上下文"的唯一入口，在每次子请求**之前**调用。放这里
        # 而不是让调用方直接改 messages，是因为工具执行发生在 assistant(tool_calls) 与
        # tool result 之间：那时候插一条 user 消息会拆散这一对，供应商会拒。等到这里，
        # tool result 已经追加完毕，新内容落在一个合法的边界上。
        # 两个生产者都走它：mods/tools 的模块变化通告，和 mods/chat 的插话队列。
        # 它只追加，从不改写已有消息——前缀缓存因此不会被打断。
        while True:
            if should_stop is not None and should_stop():
                return
            if on_round is not None:
                # WHY: 追加进去的是 role="user"（末尾 hint 同样用 user），于是线上会
                # 出现 tool 消息紧跟 user 消息、中间没有 assistant 的序列。按 OpenAI 的
                # 工具协议这是合法的——硬性要求只是 assistant.tool_calls 的每个 id 都要有
                # 对应的 tool 消息，而它们在这之前就已经补齐。
                # 不只是"读规范应该合法"：已实跑验证（deepseek-v4-flash, 2026-09-04），
                # 拿模型真实产出的 assistant 消息回放，「带 reasoning + tool -> user」这个
                # 生产组合被接受，普通与流式都是 200。
                # 两条退路也一并验证可用，真出问题时才改，别提前换：
                #   1. 换成 role="system"；
                #   2. 只对工具通告，把内容并进最后一条 tool result 的 content，消息序列
                #      完全不变——插话没法这么办，它不依附于任何 tool_call。
                # 只测了 deepseek。openai / bytecat 没测过，换供应商后这里出 400 先看这条。
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
                    tool = mapping[function["name"]]
                    arguments = json.loads(function["arguments"] or "{}")
                    pending_calls.append((call, tool, arguments))
                except Exception as error:
                    _log.exception("failed to process LLM tool call")
                    console.error(f"工具调用处理失败：{error}")
                    yield chunk

            assistant_message = {"role": assistant.role, "content": assistant.content}
            # WHY: keep_reasoning=False 时置成空字符串而不是删掉字段。两种写法都实跑
            # 验证过被接受（deepseek-v4-flash, 2026-09-04），选空串是因为它诚实：这一轮
            # 确实思考过，只是没往下带；删掉字段看起来像"根本没思考"。
            # 保留 reasoning 满足 DeepSeek thinking mode 的工具调用协议，所以默认是 True，
            # 关掉只为省一次工具循环内反复重发思考的 token。
            # 范围只在一次工具循环之内——思考本来就不跨轮：chat.get_msgs 从 chatlog 重建，
            # chatlog 里没有 reasoning。开关按窗口存，见 chat.get_reasoning_mode。
            if assistant.reasoning_content is not None:
                assistant_message["reasoning_content"] = assistant.reasoning_content if keep_reasoning else ""
            if pending_calls:
                assistant_message["tool_calls"] = [call for call, _, _ in pending_calls]
            messages.append(assistant_message)

            if not pending_calls:
                return
            results: list[ToolCallResult] = []
            # WHY: 同一条 assistant 消息里并发的调用共用一个 round 标识，用这批里第一个
            # tool_call 的 id 当它。轨道靠它还原分组——重建时一轮的 tool_calls 必须和它的
            # tool 消息成套出现，否则供应商拒。
            round_id = pending_calls[0][0]["id"]
            for call, tool, arguments in pending_calls:
                function = call["function"]
                try:
                    content = str(tool.call(**arguments))
                except Exception as error:
                    content = f"工具调用失败: {type(error).__name__}: {error}"
                    console.error(f" -> {content}")
                else:
                    console.message(f" -> {content}", "tool")
                result = ToolCallResult(call["id"], function["name"], function["arguments"], content)
                if on_tool_result is not None:
                    # WHY: 先记录原始 content，再把 cid 前缀加到发给模型的那一份上。轨道里
                    # 存的是这次调用真正返回了什么，不该混进只为模型寻址而加的前缀。
                    try:
                        cid = on_tool_result(result, round_id)
                    except Exception:
                        # 记账坏掉不该让工具调用失败：轨道是补充，不是主体。
                        _log.exception("operation log hook failed")
                        cid = None
                    if cid:
                        result = ToolCallResult(result.tool_call_id, result.name, result.arguments, f"[{cid}] {content}")
                results.append(result)
            messages.extend({"role": "tool", "tool_call_id": result.tool_call_id, "content": result.content} for result in results)
            logged = len(messages)


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
        # WHY: hint 与 context_providers 是两层，别合并。provider 追加进 messages——进
        # 历史、留下来；hint 每次子请求重新渲染并挂在末尾，不进 messages。判据是这条：
        # 频繁变化、且随时可以重算的状态放 hint；"发生过一次"的事实放 provider。
        # 它和 system 提示词一样支持用函数生成，只是重置时机不同：system 在建会话时定一次，
        # hint 每个子请求重来一次。
        self.hints: list[Callable[[], str] | str] = []
        # WHY: 默认原样带回思考内容（DeepSeek thinking mode 的工具调用协议这么要求）。
        # 置 False 会把它换成空串，只影响一次工具循环之内，见 LLMClient.chat 里的说明。
        self.keep_reasoning: bool = True
        # 每次工具调用出结果时回调一次，返回该次调用的 cid（没有就返回 None）。
        # 操作历史轨道靠它采集，见 mods/oplog。
        self.on_tool_result: Callable[[ToolCallResult, str], str | None] | None = None
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
            except Exception:
                # 一个 provider 坏掉不该让整轮聊天失败：它提供的是补充上下文，不是主体。
                _log.exception("context provider failed")
                continue
            for value in produced:
                collected.append(value if isinstance(value, dict) else {"role": "user", "content": str(value)})
        return collected

    def condense_calls(self, tool_call_ids: Iterable[str]) -> int:
        """Drop finished tool-call rounds from this context; return messages removed.

        WHY: 这是"结论收缩"真正生效的地方。工具调用往往是为某个目的服务的，结论一旦得出，
        中间过程就只剩噪音。主流 agent 用子代理绕开这件事——让子代理跑完只回一句结论，
        于是主上下文从没见过中间过程。这里是正面解决：让模型自己在得出结论时把过程收掉。

        WHY: 只删，不在原地补一条结论消息。结论已经在模型那条 condense_ops 调用的
        arguments 里了，而那条 assistant 消息此刻就在 messages 中（工具是在它之后执行的），
        再插一条就是同一句话的第二个副本。

        WHY: 按**整轮**收缩，不按单条。一条 assistant 消息可以并发多个 tool_calls，而协议
        要求它的每个 id 都有对应 tool 消息。只删其中一条会拆散这一对，请求直接被拒。所以
        某轮只要有一个 id 被点名，这一轮的 id 就必须全部被点名，否则宁可报错也不动手。

        WHY: 拒绝收缩正在进行的那一轮——tool result 还没补齐的轮次。此刻工具正在执行，
        assistant(tool_calls) 已在 messages 里、结果还没追加；把它删掉，调用返回时那些
        结果就会挂在一条不存在的 assistant 上。

        WHY: 这会让前缀缓存从收缩点之后全部失效——删消息必然如此。这是自愿付的代价：
        收缩换来的是后面每一轮都更短。追加式上下文那套"绝不回头改写"的纪律是为了保住
        缓存，而这里是明确地用缓存换长度，两者不矛盾，但别把它变成自动行为。
        """
        requested = {str(value) for value in tool_call_ids if str(value)}
        if not requested:
            return 0
        groups: list[tuple[int, set[str]]] = []
        for index, message in enumerate(self.messages):
            calls = message.get("tool_calls") if isinstance(message, dict) else None
            if not calls:
                continue
            ids = {str(call.get("id")) for call in calls}
            if ids & requested:
                groups.append((index, ids))
        # 点名的调用可能全部来自更早的轮次——那时它们本就不在这一轮的 messages 里。
        # 这不是错误，只是当前上下文没什么可删的，轨道那边照常收缩。
        if not groups:
            return 0

        missing = {name for _index, ids in groups for name in ids} - requested
        if missing:
            raise ValueError("同一轮里的调用必须一起收缩，还差: " + ", ".join(sorted(missing)))

        drop: set[int] = set()
        for index, ids in groups:
            answered = {
                position
                for position, message in enumerate(self.messages)
                if isinstance(message, dict)
                and message.get("role") == "tool"
                and str(message.get("tool_call_id")) in ids
            }
            if len(answered) != len(ids):
                raise ValueError("这一轮还没执行完，不能收缩正在进行的调用")
            drop.add(index)
            drop.update(answered)

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
                self.on_tool_result,
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
