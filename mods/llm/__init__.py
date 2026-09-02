"""Provider clients, image-aware chat completion, and function tools."""

from __future__ import annotations

from collections.abc import Callable, Generator
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
    default_config as _default_config,
    resolve_model,
    split_model_selection,
)
from .tools import Tool
from .types import LLMResponse, MessageRole, ModelCapabilities, ToolCallResult


LOAD_AFTER = ("image", "storage")
_log = logging.getLogger(__name__)

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


class LLMClient:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.clients: dict[str, OpenAI] = {}
        self._description_inflight: dict[tuple, threading.Event] = {}
        self._description_lock = threading.Lock()
        self.reload_clients()

    @staticmethod
    def _resolve_config_value(value) -> str | None:
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
        markdown = re.compile(r"!\[.*?\]\((.*?)\)")
        result = []

        def replace(match: re.Match) -> str:
            uri = match.group(1)
            return format_image_description(uri, description) if description else format_image_reference(uri)

        for source in messages:
            message = source.copy()
            content = source.get("content")
            if isinstance(content, str):
                message["content"] = markdown.sub(replace, content)
            elif isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, str):
                        parts.append({"type": "text", "text": markdown.sub(replace, part)})
                    elif isinstance(part, dict) and part.get("type") in ("image", "image_url"):
                        value = part.get("image_url", part.get("image", ""))
                        uri = value.get("url", "") if isinstance(value, dict) else str(value)
                        parts.append({"type": "text", "text": format_image_description(uri, description) if description else format_image_reference(uri)})
                    else:
                        parts.append(part)
                message["content"] = parts
            result.append(message)
        return result

    @staticmethod
    def _convert_images(messages: list[dict], convert_url: Callable[[str], str]) -> list[dict]:
        markdown = re.compile(r"!\[(.*?)\]\((.*?)\)")

        def append_image(parts: list, uri: str) -> None:
            try:
                console.notice(f"🖼️ 正在准备视觉图片：{console.format_uri(uri)}")
                data_uri = uri if uri.startswith("data:") else convert_url(uri)
                if uri and not uri.startswith("data:"):
                    parts.append({"type": "text", "text": f"[下方图片的原始链接: {uri}]"})
                slices, split = image.split_long_image_data_uri(data_uri)
                parts.extend({"type": "image_url", "image_url": {"url": value}} for value in slices)
                if split:
                    parts.append({"type": "text", "text": image.AUTO_IMAGE_SPLIT_PROMPT})
                    console.notice(f"✅ 长图已切分为 {len(slices)} 张视觉输入")
                else:
                    console.notice("✅ 视觉图片已准备")
            except Exception as error:
                console.error(
                    f"❌ 图片处理失败（{console.format_uri(uri)}）：{error}"
                )
                parts.append({"type": "text", "text": format_image_description(uri)})

        result = []
        for source in messages:
            if source.get("role") != "user":
                result.extend(LLMClient._replace_images_with_text([source]))
                continue
            message = source.copy()
            if "content" not in source:
                result.append(message)
                continue
            content = source.get("content")
            source_parts = content if isinstance(content, list) else [content]
            output = []
            changed = False
            for part in source_parts:
                if isinstance(part, str):
                    position = 0
                    matches = list(markdown.finditer(part))
                    for match in matches:
                        if match.start() > position:
                            output.append({"type": "text", "text": part[position:match.start()]})
                        append_image(output, match.group(2))
                        position = match.end()
                    if matches:
                        changed = True
                        if position < len(part):
                            output.append({"type": "text", "text": part[position:]})
                    else:
                        output.append({"type": "text", "text": part})
                elif isinstance(part, dict) and part.get("type") in ("image", "image_url"):
                    value = part.get("image_url", part.get("image", ""))
                    uri = value.get("url", "") if isinstance(value, dict) else str(value)
                    append_image(output, uri)
                    changed = True
                else:
                    output.append(part)
            if isinstance(content, list) or changed:
                message["content"] = output
            result.append(message)
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
        key = (id(description_cache), digest, image.AUTO_IMAGE_DESCRIPTION_VERSION)
        with self._description_lock:
            for identity in identities:
                cached = image.get_cached_description(description_cache, identity)
                if cached is not None:
                    algorithm = identity.partition(":")[0] if ":" in identity else "sha256"
                    console.notice(f"✅ 图片描述缓存命中：{algorithm}")
                    console.notice(f"    {cached}")
                    return cached
            event = self._description_inflight.get(key)
            owner = event is None
            if owner:
                event = threading.Event()
                self._description_inflight[key] = event
        if not owner:
            console.notice(f"⏳ 等待同一图片的描述任务：sha256:{digest[:12]}")
            event.wait()
            with self._description_lock:
                description = image.get_cached_description(description_cache, digest)
            if description is not None:
                console.notice(f"✅ 等待中的图片描述已缓存：sha256:{digest[:12]}")
                console.notice(f"    {description}")
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
            return description
        finally:
            with self._description_lock:
                self._description_inflight.pop(key, None)
                event.set()

    def _describe_images(self, messages: list[dict], cache: dict) -> list[dict]:
        vision_model = self.get_vision_model()
        if not vision_model:
            console.error("⚠️ 目标模型不支持视觉，且未配置可用的图片描述模型")
            return self._replace_images_with_text(messages, "图片，未配置可用的视觉模型")
        markdown = re.compile(r"!\[.*?\]\((.*?)\)")
        result = []
        for source in messages:
            message = source.copy()
            if "content" not in source:
                result.append(message)
                continue
            content = source.get("content")
            if content is None:
                result.append(message)
                continue
            parts = content if isinstance(content, list) else [content]
            output = []
            for part in parts:
                if isinstance(part, str):
                    def replace(match: re.Match) -> str:
                        uri = match.group(1)
                        console.notice(f"🖼️ 检查图片：{console.format_uri(uri)}")
                        try:
                            return format_image_description(uri, self._get_image_description(uri, vision_model, cache))
                        except Exception as error:
                            console.error(f"❌ 图片描述失败：{error}")
                            return format_image_description(uri)
                    output.append({"type": "text", "text": markdown.sub(replace, part)})
                elif isinstance(part, dict) and part.get("type") in ("image", "image_url"):
                    value = part.get("image_url", part.get("image", ""))
                    uri = value.get("url", "") if isinstance(value, dict) else str(value)
                    console.notice(f"🖼️ 检查图片：{console.format_uri(uri)}")
                    try:
                        description = self._get_image_description(uri, vision_model, cache)
                    except Exception as error:
                        console.error(f"❌ 图片描述失败：{error}")
                        description = None
                    output.append({"type": "text", "text": format_image_description(uri, description)})
                else:
                    output.append(part)
            message["content"] = output if isinstance(content, list) else output[0]["text"]
            result.append(message)
        return result

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

    def generate_response(self, messages: list[dict], tools: list[Tool] | None = None, tool_choice: str | dict | None = None, model: str | None = None, stream: bool = True, description_cache: dict | None = None, do_process_image: bool | None = None):
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
        console.print_request(selection, messages)
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
        role = MessageRole.ASSISTANT.value
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
                        output.chunk(reasoning, role, MessageRole.THINK.value)
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
                        MessageRole.TOOL.value,
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
            console.write(f"{message.role or MessageRole.ASSISTANT.value}({model}): ", message.role or MessageRole.ASSISTANT.value, end="")
            console.write(call.function.name + call.function.arguments, MessageRole.TOOL.value)
            yield LLMResponse(
                json.dumps({"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}}, ensure_ascii=False),
                MessageRole.TOOL.value,
            )
        if message.content:
            console.write(f"{message.role or MessageRole.ASSISTANT.value}({model}): ", message.role or MessageRole.ASSISTANT.value, end="")
            console.write(message.content, message.role or MessageRole.ASSISTANT.value)
            yield LLMResponse(message.content, message.role or MessageRole.ASSISTANT.value)
        if response.usage:
            yield LLMResponse("", MessageRole.ASSISTANT.value, response.usage.prompt_tokens, response.usage.completion_tokens, response.usage.total_tokens)
        return LLMResponse(
            message.content or "",
            message.role or MessageRole.ASSISTANT.value,
            reasoning_content=reasoning_content,
        )

    def chat(self, messages: list[dict], tools: list[Tool] | Callable[[], list[Tool]] | None = None, tool_choice: str | dict | None = None, model: str | None = None, stream: bool = True, description_cache: dict | None = None, do_process_image: bool | None = None) -> Generator[LLMResponse, None, None]:
        while True:
            # Freeze one tool snapshot for both the request schema and the
            # calls returned by that request. A tool may mutate this Chat's
            # mapping; the new snapshot is observed by the next iteration.
            tools_snapshot = tools() if callable(tools) else list(tools or [])
            console.notice(f"等待 {model or self.config['default_model']} 的响应…")
            response = iter(self.generate_response(messages, tools_snapshot, tool_choice, model, stream, description_cache, do_process_image))
            mapping = {tool.description["function"]["name"]: tool for tool in tools_snapshot}
            pending_calls = []
            # Yielded chunks remain the live display/tool stream.  The return
            # value supplies response-wide content fields; this loop combines
            # them with collected tool calls into the one history message.
            while True:
                try:
                    chunk = next(response)
                except StopIteration as completed:
                    assistant = completed.value or LLMResponse("", MessageRole.ASSISTANT.value)
                    break
                if chunk.role != MessageRole.TOOL.value:
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
            if assistant.reasoning_content is not None:
                assistant_message["reasoning_content"] = assistant.reasoning_content
            if pending_calls:
                assistant_message["tool_calls"] = [call for call, _, _ in pending_calls]
            messages.append(assistant_message)

            if not pending_calls:
                return
            results: list[ToolCallResult] = []
            for call, tool, arguments in pending_calls:
                function = call["function"]
                try:
                    content = str(tool.call(**arguments))
                except Exception as error:
                    content = f"工具调用失败: {type(error).__name__}: {error}"
                    console.error(f" -> {content}")
                else:
                    console.write(f" -> {content}", MessageRole.TOOL.value)
                results.append(ToolCallResult(call["id"], function["name"], function["arguments"], content))
            messages.extend({"role": "tool", "tool_call_id": result.tool_call_id, "content": result.content} for result in results)


class Chat:
    def __init__(self, model: str | None = None, messages: list | None = None, functions: dict | list | None = None, chat_client: LLMClient | None = None, recall_func: Callable | None = None, description_cache: dict | None = None, do_process_image: bool | None = None) -> None:
        self.chat_client = chat_client
        self.model = model or (chat_client.config["default_model"] if chat_client else None)
        self.recall_func = recall_func
        self.description_cache = description_cache
        self.do_process_image = do_process_image
        self.messages: list[dict] = []
        self.functions: dict[str, Tool] = {}
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
            )
            results = []
            for chunk in response:
                if callback:
                    callback(chunk)
                results.append(chunk)
            return results
        except Exception as error:
            _log.exception("LLM chat failed")
            console.error(f"LLM 聊天失败：{error}")
            result = LLMResponse(f"# {error}", MessageRole.ASSISTANT.value)
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
