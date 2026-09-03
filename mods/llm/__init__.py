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
    DEFAULT_REQUEST_TIMEOUT,
    default_config as _default_config,
    resolve_model,
    split_model_selection,
)
from .tools import Tool
from .types import LLMResponse, ModelCapabilities, ToolCallResult


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

    def chat(self, messages: list[dict], tools: list[Tool] | Callable[[], list[Tool]] | None = None, tool_choice: str | dict | None = None, model: str | None = None, stream: bool = True, description_cache: dict | None = None, do_process_image: bool | None = None) -> Generator[LLMResponse, None, None]:
        # Every message appended below is printed live as it happens, so each
        # further round only logs what it has not shown yet -- usually nothing.
        logged = 0
        # WHY: 工具循环有意没有最大轮数、总时限、确认步骤或副作用回滚，恢复手段是
        # .reboot。取舍与它暴露在同一条提示注入路径上的能力都记在 docs/llm.md 的
        # 「当前信任边界与维护取舍」一节——加限制前先读那里，这不是漏了。
        #
        # WHY?: 缺插话与 ^C 打断。方向已定，尚未实现——实现时按下面这套来，别另起一套。
        #
        # 先记清现状，免得被误判成"收不到消息"：入站没有被生成挡住，link.dispatch 是
        # thread.to_thread(None)，每条消息都在自己的线程上派发。现在的问题是第二条 at
        # 会**再起一轮并发的 chat()**，两轮各自读 get_msgs()、各自发言。
        #
        # 已定的设计：
        # - 按**窗口**登记，不按 (窗口, 用户)。插话和 ^C 都是任何人可用：LLM 上下文本来
        #   就是整个窗口共享的，只让触发者能停会让群里其他人无法制止一轮跑偏的生成。
        #   注意这与 context.interaction_key 的粒度不同，需要单独的窗口级登记。
        # - 插话走队列：中途到达的消息进队尾，在本轮返回后、或下一次调用模型前(例如现在
        #   工具返回后自动续问的那一步)一并带入，而不是打断当前请求。
        # - 分级：只有原本就会触发聊天的消息(at、link 命中)才**触发**一次新的插话调用；
        #   普通消息只**进队列**不触发。这样既让模型看到更多上下文，又不会让普通闲聊无限
        #   续聊下去。
        # - ^C 打断：本循环需要在每轮之间检查窗口级取消标记(InteractionCancelled 已存在)，
        #   理想情况下还要能中断流式读取。bot._route 的 ^C 分支本身在生成期间是能跑的，
        #   缺的只是这个循环没有登记可被 context.cancel 唤醒的东西。
        while True:
            # Freeze one tool snapshot for both the request schema and the
            # calls returned by that request. A tool may mutate this Chat's
            # mapping; the new snapshot is observed by the next iteration.
            tools_snapshot = tools() if callable(tools) else list(tools or [])
            console.notice(f"等待 {model or self.config['default_model']} 的响应…")
            response = iter(self.generate_response(messages, tools_snapshot, tool_choice, model, stream, description_cache, do_process_image, logged))
            mapping = {tool.description["function"]["name"]: tool for tool in tools_snapshot}
            pending_calls = []
            # Yielded chunks remain the live display/tool stream.  The return
            # value supplies response-wide content fields; this loop combines
            # them with collected tool calls into the one history message.
            while True:
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
                    console.message(f" -> {content}", "tool")
                results.append(ToolCallResult(call["id"], function["name"], function["arguments"], content))
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
