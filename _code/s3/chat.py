import os
import json
import requests
import base64
from typing import Optional, Generator, Union, Any, Callable
from dataclasses import dataclass
from openai import OpenAI
import re
from enum import Enum
from termcolor import colored
import traceback

# import html
# import time
# from logging import Logger

# logger = Logger(name='chat')

from main import storage

class MessageRole(Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    THINK = "think"
    TOOL = "tool"

ROLE_TO_COLOR = {
    MessageRole.SYSTEM.value: "red",
    MessageRole.USER.value: "green",
    MessageRole.THINK.value: "yellow",
    MessageRole.ASSISTANT.value: "blue",
    MessageRole.TOOL.value: "magenta",
}

@dataclass
class ModelCapabilities:
    vision: bool = False
    function_calling: bool = False
    prompt_price: float = 0.0
    completion_price: float = 0.0

@dataclass
class ProviderConfig:
    base_url: str
    api_key: str
    models: dict[str, ModelCapabilities]

@dataclass
class LLMResponse:
    content: Union[str, list]
    role: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other:'LLMResponse'):
        # 虽然大模型不会返回消息数组，但是以防万一这里混入了用户的输入
        if isinstance(self.content, list) or isinstance(other.content, list):
            # 如果任一content是列表，则合并为新的列表
            content1 = (self.content if isinstance(self.content, list) else
                        [{"type": "text", "text": self.content}] if self.content else [])
            content2 = (other.content if isinstance(other.content, list) else
                        [{"type": "text", "text": other.content}] if other.content else [])
            return LLMResponse(
                content=content1 + content2,
                role=self.role,
                prompt_tokens=self.prompt_tokens+other.prompt_tokens,
                completion_tokens=self.completion_tokens+other.completion_tokens,
                total_tokens=self.total_tokens+other.total_tokens,
            )
        else:
            con = '\n\n' if self.content else ''
            return LLMResponse(
                content=f"{self.content}{con}{other.content}",
                role=self.role,
                prompt_tokens=self.prompt_tokens+other.prompt_tokens,
                completion_tokens=self.completion_tokens+other.completion_tokens,
                total_tokens=self.total_tokens+other.total_tokens,
            )

@dataclass
class ToolCallResult:
    tool_call_id: str
    name: str
    arguments: str
    content: str


# 默认的URL转base64函数
def default_url_to_base64(url: str) -> str:
    response = requests.get(url, timeout=3)
    if response.status_code != 200:
        raise ValueError(f"Failed to download image from {url}")

    image_data = response.content
    mime_type = response.headers.get('Content-Type', 'image/jpeg')
    base64_data = base64.b64encode(image_data).decode('utf-8')
    return f"data:{mime_type};base64,{base64_data}"

import subprocess
import base64
import magic  # 需要安装 python-magic: pip install python-magic
import mimetypes

def get_image_base64(url):
    """
    下载图片并转换为data URI格式

    Args:
        url: 图片URL

    Returns:
        str: 完整的data URI格式字符串 "data:{mime_type};base64,{base64_data}"
    """
    curl_command = [
        'curl',
        '-k',          # 忽略SSL证书验证
        '-L',          # 跟随重定向
        '-s',          # 静默模式
        '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0',
        url
    ]

    try:
        # 执行curl命令并获取二进制输出
        result = subprocess.run(curl_command, capture_output=True)

        if result.returncode == 0:
            binary_data = result.stdout

            # 检测MIME类型
            mime = magic.Magic(mime=True)
            mime_type = mime.from_buffer(binary_data)

            # 如果magic检测失败，尝试从URL推断
            if not mime_type or mime_type == 'application/octet-stream':
                mime_type = mimetypes.guess_type(url)[0] or 'application/octet-stream'
            if 'json' in mime_type:
                raise ValueError(f"错误的mine格式: {mime_type}")

            # 转换为base64
            base64_data = base64.b64encode(binary_data).decode('utf-8')

            # 构造完整的data URI
            data_uri = f"data:{mime_type};base64,{base64_data}"

            print("✅ 转换成功！")
            print(f"📊 Data URI长度: {len(data_uri)} 字符")
            print(f"🎯 MIME类型: {mime_type}")

            return data_uri
        else:
            print(f"❌ 下载失败")
            if result.stderr:
                print(f"错误信息: {result.stderr.decode()}")
            return None

    except Exception as e:
        print(f"❌ 发生错误: {str(e)}")
        return None

def split_string_with_code_blocks(text: str):
    result = []
    lines = text.split('\n')
    current_block = []
    in_code_block = False

    for line in lines:
        if line.strip().startswith('```'):
            if in_code_block:
                # 结束代码块
                current_block.append(line)
                result.append('\n'.join(current_block))
                current_block = []
                in_code_block = False
            else:
                # 开始代码块，先处理之前的普通文本
                if current_block:
                    # 将普通文本按双换行符分割
                    text_parts = '\n'.join(current_block).split('\n\n')
                    result.extend(text_parts)
                    current_block = []
                current_block.append(line)
                in_code_block = True
        else:
            current_block.append(line)

    # 处理最后剩余的文本
    if current_block:
        if in_code_block:
            result.append('\n'.join(current_block))
        else:
            # 将普通文本按双换行符分割
            text_parts = '\n'.join(current_block).split('\n\n')
            result.extend(text_parts)

    # 过滤掉空字符串
    result = [part for part in result if part.strip()]
    return result


def print_colored(text, role, end='\n', flush=True, with_prefix=False):
    '''
    打印彩色字体
    大模型不会返回消息数组所以这里不需要兼容消息数组，但是为了通用性还是加上了
    '''
    color = ROLE_TO_COLOR.get(role, "white")
    result = f'{role}: ' if with_prefix else ''
    if isinstance(text, list):
        # 处理列表格式的文本
        for item in text:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    result += item['text']
                elif item.get("type") in ["image", "image_url"]:
                    result += "[图片]"
            else:
                result += str(item)
    else:
        result += text
    print(colored(result, color), end=end, flush=flush)



class LLMCilent:
    def __init__(self):
        self.config = self._load_or_create_config()
        self.clients = {}  # 缓存不同provider的客户端
        self._init_clients()

    def _load_or_create_config(self) -> dict:
        """加载或创建默认配置"""
        config:dict = storage.get("llm_system", "config")

        if not config:
            config.update({
                "providers": {
                    "openai": {
                        "base_url": "OPENAI_BASE_URL",
                        "api_key": "OPENAI_API_KEY",  # 可以是环境变量名或实际值
                        "models": {
                            "gpt-4o-mini": {
                                "vision": True,
                                "function_calling": True
                            },
                            "gpt-4o": {
                                "vision": True,
                                "function_calling": True
                            },
                            "gpt-3.5-turbo": {
                                "vision": False,
                                "function_calling": True
                            }
                        }
                    },
                    "deepseek": {
                        "base_url": "DEEPSEEK_BASE_URL",
                        "api_key": "DEEPSEEK_API_KEY",  # 可以是环境变量名或实际值
                        "models": {
                            "deepseek-chat": {
                                "vision": False,
                                "function_calling": True
                            },
                            "deepseek-reasoner": {
                                "vision": False,
                                "function_calling": True
                            }
                        }
                    }
                },
                "default_provider": "openai",
                "default_model": "gpt-4o-mini",
                "vision_model": "gpt-4o"
            })

        return config

    def _init_clients(self):
        """初始化各provider的客户端"""
        for provider, config in self.config["providers"].items():
            base_url = self._resolve_config_value(config["base_url"])
            api_key = self._resolve_config_value(config["api_key"])

            client_config = {
                "api_key": api_key,
                "base_url": base_url
            }

            # 移除None值
            client_config = {k: v for k, v in client_config.items() if v is not None}

            self.clients[provider] = OpenAI(**client_config)

    def _resolve_config_value(self, value: str) -> Optional[str]:
        """解析配置值，如果是环境变量名则获取环境变量"""
        if value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            return os.getenv(env_var)
        elif value in os.environ:
            return os.getenv(value)
        return value

    def set_provider(self, provider: str, config: dict):
        """更新provider配置"""
        if provider not in self.config["providers"]:
            self.config["providers"][provider] = config
        else:
            self.config["providers"][provider].update(config)

        # 重新初始化客户端
        self._init_clients()

    def del_provider(self, provider: str, config: dict):
        """删除provider"""
        if provider not in self.config["providers"]:
            raise ValueError(f"Provider {provider} not found")
        else:
            del self.config["providers"][provider]

        # 重新初始化客户端
        self._init_clients()

    def set_model(self, provider: str, model: str, capabilities: dict):
        """添加模型到provider"""
        if provider not in self.config["providers"]:
            raise ValueError(f"Provider {provider} not found")

        self.config["providers"][provider]["models"][model] = capabilities

    def del_model(self, provider: str, model: str):
        """删除模型到provider"""
        if provider not in self.config["providers"]:
            raise ValueError(f"Provider {provider} not found")

        del self.config["providers"][provider]["models"][model]

    def get_model_capabilities(self, provider: str, model: str) -> ModelCapabilities:
        """获取模型能力"""
        if provider not in self.config["providers"]:
            return ModelCapabilities()

        models = self.config["providers"][provider]["models"]
        if model not in models:
            return ModelCapabilities()

        return ModelCapabilities(**models[model])

    def _convert_images(
        self,
        messages: list[dict],
        convert_url: Callable[[str], str]
    ) -> list[dict]:
        """
        处理消息中的图片，将Markdown格式或URL格式的图片转换为适合模型输入的列表格式。
        图片不可用时用文本代替。
        """
        # 定义错误提示文本
        IMAGE_LOAD_FAILED_TEXT = "[图片加载失败]"
        
        processed_messages = []
        markdown_image_pattern = r"!\[(.*?)\]\((.*?)\)"

        for i, message in enumerate(messages):
            new_message = message.copy()

            if "content" in message:
                content: Union[str, list] = message["content"]

                if isinstance(content, list):
                    new_content = []
                    for item in content:
                        if isinstance(item, str):
                            matches = list(re.finditer(markdown_image_pattern, item))
                            if matches:
                                current_pos = 0
                                text_parts = []

                                for match in matches:
                                    if match.start() > current_pos:
                                        text_part = item[current_pos:match.start()]
                                        text_parts.append(text_part)

                                    alt_text, img_url = match.groups()
                                    current_pos = match.end()

                                    try:
                                        if img_url.startswith("data:"):
                                            image_data = img_url
                                        else:
                                            image_data = convert_url(img_url)

                                        # 清空之前积累的文本部分
                                        if text_parts:
                                            new_content.append({"type": "text", "text": "".join(text_parts)})
                                            text_parts = []

                                        new_content.append({
                                            "type": "image_url",
                                            "image_url": {"url": image_data}
                                        })
                                    except Exception as e:
                                        print(f"图片处理失败: {e}")
                                        # 转换失败时，将图片标记替换为错误文本
                                        text_parts.append(IMAGE_LOAD_FAILED_TEXT)

                                # 处理剩余文本
                                if current_pos < len(item):
                                    remaining_text = item[current_pos:]
                                    text_parts.append(remaining_text)

                                if text_parts:
                                    new_content.append({"type": "text", "text": "".join(text_parts)})
                            else:
                                new_content.append({"type": "text", "text": item})

                        elif isinstance(item, dict):
                            item_type = item.get("type")
                            if item_type in ["image", "image_url"]:
                                image_url_data = item.get("image_url", {})
                                url = ""
                                if isinstance(image_url_data, dict):
                                    url = image_url_data.get("url", "")
                                elif isinstance(image_url_data, str):
                                    url = image_url_data
                                    item = {"type": "image_url", "image_url": {"url": url}}

                                if url:
                                    if not url.startswith("data:"):
                                        try:
                                            converted_url = convert_url(url)
                                            item["image_url"]["url"] = converted_url
                                            new_content.append(item)
                                        except Exception as e:
                                            print(f"图片URL转换失败: {e}")
                                            # 替换为错误文本而不是保留原始URL
                                            new_content.append({"type": "text", "text": IMAGE_LOAD_FAILED_TEXT})
                                    else:
                                        new_content.append(item)
                                else:
                                    # URL为空的情况也替换为错误文本
                                    new_content.append({"type": "text", "text": IMAGE_LOAD_FAILED_TEXT})
                            else:
                                new_content.append(item)
                        else:
                            new_content.append({"type": "text", "text": str(item)})

                    new_message["content"] = new_content

                elif isinstance(content, str):
                    matches = list(re.finditer(markdown_image_pattern, content))
                    if matches:
                        new_content = []
                        current_pos = 0

                        for match in matches:
                            if match.start() > current_pos:
                                text_part = content[current_pos:match.start()]
                                new_content.append({'type': 'text', 'text': text_part})

                            alt_text, img_url = match.groups()
                            current_pos = match.end()

                            try:
                                if img_url.startswith("data:"):
                                    image_data = img_url
                                else:
                                    image_data = convert_url(img_url)

                                new_content.append({
                                    "type": "image_url",
                                    "image_url": {"url": image_data}
                                })
                            except Exception as e:
                                print(f"图片处理失败: {e}")
                                # 转换失败时用文本代替
                                new_content.append({"type": "text", "text": IMAGE_LOAD_FAILED_TEXT})

                        if current_pos < len(content):
                            remaining_text = content[current_pos:]
                            new_content.append({'type': 'text', 'text': remaining_text})

                        new_message["content"] = new_content

            processed_messages.append(new_message)

        return processed_messages

    def get_vision_model(self):
        vision_model = self.config.get("vision_model", None)
        if not vision_model:
            return # 如果没有视觉模型，直接返回，不处理
        # 确保 providers 配置存在且是字典
        providers_config = self.config.get("providers", {})
        if isinstance(providers_config, dict):
            for provider, config in providers_config.items():
                # 确保 provider 的配置是字典，且包含 'models' key
                if isinstance(config, dict) and "models" in config and isinstance(config["models"], dict):
                     # 确保模型配置是字典，包含 vision key
                    model_config = config["models"].get(vision_model)
                    if isinstance(model_config, dict) and model_config.get("vision"):
                        vision_provider = provider
                        break
        if not vision_provider:
            return # 如果找不到提供者，也直接返回
        return vision_provider, vision_model

    def _process_images(
            self,
            messages: list[dict],
            convert_url: Callable[[str], str],
            description_cache: dict[str, str],
            vision_model: str = None
    ) -> list[dict]:
        """处理消息中的图片, 并添加缓存命中日志"""
        res = self.get_vision_model()
        if not res:
            return messages
        vision_provider, vision_model = res

        # print(f"使用 '{vision_provider}' 的 '{vision_model}' 处理图片...") # 可以加一个开始处理的日志

        processed_messages = []
        for message in messages:
            if "content" not in message:
                processed_messages.append(message)
                continue

            content: Union[str, list] = message["content"]
            if isinstance(content, str):
                # 检查是否有图片标记
                image_matches = re.findall(r'!\[.*?\]\((.*?)\)', content)
                if not image_matches:
                    processed_messages.append(message)
                    continue

                # 处理图片
                new_content = content
                print(f"  正在处理文本内容中的图片标记...")
                for image_url in image_matches:
                    print(f"    - 检查图片 URL: {image_url}")
                    try:
                        # 调用视觉模型获取图片描述
                        if image_url in description_cache:
                            description = description_cache.get(image_url)
                            # --- 添加缓存命中日志 ---
                            print(f"    ✅ 图片描述缓存命中: {image_url}")
                            print(f"        {description}")
                        else:
                            description = self._describe_image(
                                image_url,
                                vision_provider,
                                vision_model,
                                convert_url
                            )
                            if description: # 只有成功获取描述才缓存
                                description_cache[image_url] = description
                            else:
                                print(f"    ⚠️ 未能为 {image_url} 生成描述，不进行缓存。")

                        replacement = f"[图片: {description}]" if description else "[图片: 图片解析失败]"
                        # 使用 re.escape 避免 URL 中的特殊字符影响替换
                        markdown_tag = f"![]({image_url})" # 假设 alt text 为空
                        new_content = new_content.replace(markdown_tag, replacement, 1) # 每次只替换一个，防止 URL 相同导致问题
                    except Exception as e:
                        print(f"    ❌ 图片处理失败 ({image_url}): {e}")
                        markdown_tag = f"![]({image_url})"
                        new_content = new_content.replace(markdown_tag, "[图片: 图片解析失败]", 1)

                processed_message = message.copy()
                processed_message["content"] = new_content
                processed_messages.append(processed_message)

            elif isinstance(content, list):
                 # 处理多模态数组
                new_content_list = [] # 重命名以避免混淆
                # print(f"  正在处理列表内容...")
                for item in content:
                    if isinstance(item, str):
                         # 处理文本中的markdown图片
                        image_matches = re.findall(r'!\[.*?\]\((.*?)\)', item)
                        if image_matches:
                            temp_content = item
                            print(f"    - 在列表文本项中发现图片标记...")
                            for image_url in image_matches:
                                print(f"      - 检查图片 URL: {image_url}")
                                try:
                                    if image_url in description_cache:
                                        description = description_cache.get(image_url)
                                        # --- 添加缓存命中日志 ---
                                        print(f"      ✅ 图片描述缓存命中: {image_url}")
                                        print(f"          {description}")
                                    else:
                                        description = self._describe_image(
                                            image_url,
                                            vision_provider,
                                            vision_model,
                                            convert_url
                                        )
                                        if description:
                                            description_cache[image_url] = description
                                        else:
                                            print(f"      ⚠️ 未能为 {image_url} 生成描述，不进行缓存。")

                                    replacement = f"[图片: {description}]" if description else "[图片: 图片解析失败]"
                                    markdown_tag = f"![]({image_url})"
                                    temp_content = temp_content.replace(markdown_tag, replacement, 1)
                                except Exception as e:
                                    print(f"      ❌ 图片处理失败 ({image_url}): {e}")
                                    markdown_tag = f"![]({image_url})"
                                    temp_content = temp_content.replace(markdown_tag, "[图片: 图片解析失败]", 1)
                            new_content_list.append({"type": "text", "text": temp_content})
                        else:
                            # 没有图片标记的文本项直接添加
                            new_content_list.append({"type": "text", "text": item})
                    elif isinstance(item, dict):
                        item_type = item.get("type")
                        # 检查 image_url 结构 (兼容 OpenAI 格式)
                        if item_type == "image_url":
                            image_url_data = item.get("image_url", {})
                            if isinstance(image_url_data, dict): # 标准格式
                                image_url = image_url_data.get("url", "")
                            elif isinstance(image_url_data, str): # 可能是旧格式或直接给URL
                                image_url = image_url_data
                            else:
                                image_url = ""

                            if image_url:
                                print(f"    - 处理 image_url 对象: {image_url}")
                                try:
                                    if image_url in description_cache:
                                        description = description_cache.get(image_url)
                                        # --- 添加缓存命中日志 ---
                                        print(f"      ✅ 图片描述缓存命中: {image_url}")
                                        print(f"          {description}")
                                    else:
                                        description = self._describe_image(
                                            image_url,
                                            vision_provider,
                                            vision_model,
                                            convert_url
                                        )
                                        if description:
                                            description_cache[image_url] = description
                                        else:
                                             print(f"      ⚠️ 未能为 {image_url} 生成描述，不进行缓存。")

                                    new_content_list.append({
                                        "type": "text",
                                        "text": f"[图片: {description}]" if description else "[图片: 图片解析失败]"
                                    })
                                except Exception as e:
                                    print(f"      ❌ 图片处理失败 ({image_url}): {e}")
                                    new_content_list.append({
                                        "type": "text",
                                        "text": "[图片: 图片解析失败]"
                                    })
                            else:
                                # 如果 image_url 无效或缺失，可以选择跳过或添加占位符
                                print(f"    ⚠️ 发现 type=image_url 但缺少有效 URL: {item}")
                                # new_content_list.append({"type": "text", "text": "[图片: 无效URL]"})
                                # 或者直接忽略这个 item
                        else:
                            # 保留其他类型的字典对象 (如 text)
                            # print(f"    - 保留非图片字典项: {item.get('type')}")
                            new_content_list.append(item)
                    else:
                         # 保留其他未知类型的内容项
                        print(f"    - 保留未知类型项: {type(item)}")
                        new_content_list.append(item)

                processed_message = message.copy()
                processed_message["content"] = new_content_list # 使用新列表
                processed_messages.append(processed_message)

            else:
                 # 如果 content 不是 str 或 list，直接保留原消息
                processed_messages.append(message)

        # print("图片处理完成。") # 可以加一个结束的日志
        return processed_messages

    def _describe_image(self, image_url: str, provider: str, model: str,
        convert_url: Callable[[str], str]) -> Optional[str]:
        """使用视觉模型描述图片"""
        client = self.clients[provider]

        try:
            try:
                # raise Exception()
                image_data = convert_url(image_url)
            except Exception as e:
                image_data = image_url
            response = client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请描述你能看到的这张图片的一切内容，以及其表达的情感倾向，这将替代图片发送给没有视觉能力的模型"},
                        {"type": "image_url", "image_url": {"url": image_data}}
                    ]
                }],
                max_tokens=300
            )
            description = response.choices[0].message.content
            print(f"      ✅ 视觉模型调用成功: {image_url}")
            print(f"          {description}")

            return description
        except Exception as e:
            print(f"      ❌ 视觉模型调用失败: {e}")
            return None

    def generate_response(
        self,
        messages: list[dict],
        tools: Optional[list[Any]] = None,
        tool_choice: Optional[Union[str, dict]] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        stream: bool = True,
        url_to_base64_func: Optional[Callable[[str], str]] = None,
        description_cache: dict[str, str] = None,
        do_process_image: Optional[bool] = None,
    ) -> Generator[LLMResponse, None, None]:
        """生成LLM响应"""
        if not provider:
            provider = self.config["default_provider"]

        if not model:
            model = self.config["default_model"]

        if provider not in self.clients:
            raise ValueError(f"Provider {provider} not configured")

        client = self.clients[provider]

        # 检查模型能力
        capabilities = self.get_model_capabilities(provider, model)

        # 处理图片
        if url_to_base64_func is None:
            url_to_base64_func = default_url_to_base64

        if do_process_image:
            if not capabilities.vision:
                if description_cache is None:
                    description_cache = {}
                messages = self._process_images(messages, url_to_base64_func, description_cache)
            else:
                messages = self._convert_images(messages, url_to_base64_func)

        # print(messages)

        # 处理工具调用
        final_tools = None
        if capabilities.function_calling and tools:
            final_tools = [tool.description for tool in tools]

        # 准备请求参数
        request_params = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }

        if final_tools:
            request_params["tools"] = final_tools
            if tool_choice:
                request_params["tool_choice"] = tool_choice

        if stream:
            request_params["stream_options"] = {"include_usage": True}

        try:
            if stream:
                return self._stream_response(client, request_params, model)
            else:
                return self._non_stream_response(client, request_params)
        except Exception as e:
            print(f"LLM调用失败: {e}")
            raise

    def _stream_response(
        self,
        client: OpenAI,
        params: dict,
        model: str
    ) -> Generator[LLMResponse, None, None]:
        """
        处理流式响应

        进行一次迭代，将输出按照assistant:content, assistant:tool, assistant:think 来分类
        同时按照双换行符划分

        只调用一次，如果有tool会调用tool，即使tool有返回也不负责再调用

        包含usage的信息必定为assistant，且content为空字符串

        大模型不会返回消息数组所以这里不需要兼容消息数组
        """
        buffer: str = ""
        current_role = None
        tool_calls = []  # 存储正在构建中的工具调用
        completed_tool_calls = set()  # 存储已完成的工具调用
        id_to_index = {}
        usage = None
        status = 0 # 1think, 2content, 3tool

        try:
            stream = client.chat.completions.create(**params)

            for chunk in stream:
                # 如果结束了
                if getattr(chunk, 'finish_reason', None) or not getattr(chunk, 'choices', None):
                    # 处理使用情况数据
                    if hasattr(chunk, 'usage') and chunk.usage:
                        usage = chunk.usage
                    continue # 应该不会还有吧

                delta = chunk.choices[0].delta
                delta_dict = delta.to_dict(exclude_unset=False)

                # 处理角色变化
                if hasattr(delta, 'role') and delta.role:
                    current_role = delta.role

                if reasoning_content:=delta_dict.get('reasoning_content'):
                    if status != 1:
                        status = 1
                        print_colored(f"{current_role}({model}): ", current_role, end='', flush=True)
                    print_colored(reasoning_content, MessageRole.THINK.value, end='', flush=True)

                # 处理普通文本内容
                if content:=delta_dict.get('content'):
                    if status != 2:
                        content = content.lstrip()
                        status = 2
                        print_colored(f"{current_role}({model}): ", current_role, end='', flush=True)

                    buffer += content

                    # 处理分段
                    if "\n\n" in buffer:
                        # *parts, buffer = buffer.split('\n\n')
                        *parts, buffer = split_string_with_code_blocks(buffer)
                        for part in parts:
                            print_colored(part, current_role or MessageRole.ASSISTANT.value, flush=True)
                            yield LLMResponse(
                                content=part,
                                role=current_role or MessageRole.ASSISTANT.value,
                            )
                            status = 0

                # 处理工具调用
                if delta_dict.get('tool_calls'):
                    if status != 3:
                        status = 3
                        print_colored(f"{current_role}({model}): ", current_role, end='', flush=True)
                    # 实时输出增量内容
                    if delta.tool_calls:
                        for tool_call in delta.tool_calls:
                            if tool_call.id is not None: # 假设每个第一次出现肯定带id
                                call_id = tool_call.id
                                if call_id not in id_to_index:
                                    index = len(tool_calls)
                                    id_to_index[call_id] = index
                                    tool_calls.append({
                                        "id": tool_call.id,
                                        "type": "function",
                                        "function": {
                                            "name": '',
                                            "arguments": ''
                                        }
                                    })
                                else:
                                    index = id_to_index[index]
                            elif tool_call.index is not None:
                                index = tool_call.index

                            if tool_call.function.name:
                                tool_calls[index]["function"]["name"] = tool_call.function.name
                                print_colored(tool_call.function.name, MessageRole.TOOL.value, end='', flush=True)
                            if tool_call.function.arguments:
                                tool_calls[index]["function"]["arguments"] += tool_call.function.arguments
                                print_colored(tool_call.function.arguments, MessageRole.TOOL.value, end='', flush=True)

                    # 检查是否有工具调用可以返回
                    for tool_call in tool_calls:
                        if (tool_call["id"] and
                            tool_call["function"]["name"] and
                            tool_call["id"] not in completed_tool_calls and
                            self._is_complete_json(tool_call["function"]["arguments"])):
                            yield LLMResponse(
                                content=json.dumps(tool_call, ensure_ascii=False),
                                role=MessageRole.TOOL.value
                            )

                            completed_tool_calls.add(tool_call["id"])


            # 处理最终剩余的内容
            if buffer.strip():
                # print()  # 添加最终换行
                print_colored(buffer, current_role or MessageRole.ASSISTANT.value, flush=True)
                yield LLMResponse(
                    content=buffer.strip(),
                    role=current_role or MessageRole.ASSISTANT.value,
                )

            for tool_call in tool_calls: #报错也传上去
                if (tool_call["id"] and
                    tool_call["function"]["name"] and
                    tool_call["id"] not in completed_tool_calls):
                    yield LLMResponse(
                        content=json.dumps(tool_call, ensure_ascii=False),
                        role=MessageRole.TOOL.value
                    )

            if usage:
                yield LLMResponse(
                    content='',
                    role=current_role or MessageRole.ASSISTANT.value,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                )


        except Exception as e:
            error_msg = f"流式响应处理失败: {e}"
            print(traceback.format_exc())
            print_colored(f"\n{error_msg}", MessageRole.SYSTEM.value)
            raise

    def _is_complete_json(self, json_str: str) -> bool:
        """检查JSON字符串是否完整"""
        try:
            json.loads(json_str)
            return True
        except json.JSONDecodeError:
            return False

    def _non_stream_response(
        self,
        client: OpenAI,
        params: dict
    ) -> Generator[LLMResponse, None, None]:
        """处理非流式响应"""
        try:
            response = client.chat.completions.create(**params)
            message = response.choices[0].message

            # 处理工具调用
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    print_colored(tool_call.function.arguments, MessageRole.TOOL.value, end='', flush=True)
                    yield LLMResponse(
                        content=json.dumps({
                            "id": tool_call.id,
                            "type": "function",
                            "function": {
                                "name": tool_call.function.name,
                                "arguments": tool_call.function.arguments
                            }
                        }, ensure_ascii=False),
                        role=MessageRole.TOOL.value,
                    )
                    # 单独返回调用消耗
                    yield LLMResponse(
                        content='',
                        role=MessageRole.ASSISTANT.value,
                        prompt_tokens=response.usage.prompt_tokens,
                        completion_tokens=response.usage.completion_tokens,
                        total_tokens=response.usage.total_tokens
                    )
            else:
                print_colored(message.content, message.role or MessageRole.ASSISTANT.value, flush=True)
                yield LLMResponse(
                    content=message.content,
                    role=message.role,
                )
                # 单独返回调用消耗
                yield LLMResponse(
                    content='',
                    role=MessageRole.ASSISTANT.value,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    total_tokens=response.usage.total_tokens
                )

        except Exception as e:
            print(f"非流式响应处理失败: {e}")
            raise

    def print_response(self, response: Generator[LLMResponse, None, None]):
        """打印响应到控制台"""
        # 这个方法已经被重构了，由_stream_response和chat方法直接处理输出
        # 保留此方法但修改为只输出系统和非流式响应内容
        for chunk in response:
            pass
            print(chunk)
            # if chunk.role == MessageRole.SYSTEM.value:
            #     print(colored(f"{chunk.role}: {chunk.content}", ROLE_TO_COLOR.get(chunk.role, "white")))

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[Any]] = None,
        tool_choice: Optional[Union[str, dict]] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        stream: bool = True,
        url_to_base64_func: Optional[Callable[[str], str]] = None,
        description_cache: dict[str,str] = None,
        do_process_image: Optional[bool] = None,
    ) -> Generator[LLMResponse, None, None]:
        """聊天接口"""
        # 应用默认值
        if not provider:
            provider = self.config["default_provider"]
        if not model:
            model = self.config["default_model"]

        while True:
            # 显示等待消息
            print(colored(f"等待{provider}:{model}的响应...", "grey"))

            response = self.generate_response(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                provider=provider,
                model=model,
                stream=stream,
                url_to_base64_func=url_to_base64_func,
                description_cache=description_cache,
                do_process_image = do_process_image,
            )

            tool_mapping = {tool.description["function"]["name"]: tool
                            for tool in (tools or [])}

            # 存储工具调用结果，以便于后续添加到消息中
            tool_results: list[ToolCallResult] = []
            yield from self._process_response(response, tool_mapping, tool_results)

            # print("工具调用：",tool_results)
            # 如果没有tool调用，退出循环
            if not tool_results:
                break

            # 添加助手的工具调用消息
            assistant_message = {"role": "assistant", "tool_calls": []}
            for tool_result in tool_results:
                assistant_message["tool_calls"].append({
                    "id": tool_result.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_result.name,
                        "arguments": tool_result.arguments
                    }
                })
            messages.append(assistant_message)

            # 添加工具调用结果消息
            for tool_result in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_result.tool_call_id,
                    "content": tool_result.content
                })

    def _process_response(self,
        response: Generator[LLMResponse, None, None],
        tool_mapping: dict[str, Any],
        tool_results: list[ToolCallResult],
    ) -> Generator[LLMResponse, Any, Any]:

        for chunk in response:
            # 如果是工具调用，解析并执行
            if chunk.role == MessageRole.TOOL.value:
                try:
                    tool_call = json.loads(chunk.content)

                    if isinstance(tool_call, dict) and tool_call.get("type") == "function":
                        function_name = tool_call["function"]["name"]
                        arguments_str = tool_call["function"]["arguments"]

                        # 查找对应的工具
                        if function_name in tool_mapping:
                            tool = tool_mapping[function_name]
                            # 解析参数
                            arguments = json.loads(arguments_str)

                            # 执行工具调用
                            try:
                                result = tool.call(**arguments)
                                tool_result = ToolCallResult(
                                    tool_call_id=tool_call["id"],
                                    name=function_name,
                                    arguments=arguments_str,
                                    content=str(result)
                                )

                                # 收集工具调用结果
                                tool_results.append(tool_result)

                                # 按照期望格式输出工具调用
                                print(colored(f" -> {result}", ROLE_TO_COLOR[chunk.role]))

                            except Exception as e:
                                # 处理工具调用异常
                                error_result = ToolCallResult(
                                    tool_call_id=tool_call["id"],
                                    name=function_name,
                                    arguments=arguments_str,
                                    content=f"工具调用失败: {str(e)}\n\n{traceback.format_exc()}"
                                )
                                tool_results.append(error_result)

                                # 输出错误信息
                                print(colored(f" -> 错误: {str(e)}", "red"))
                                print(traceback.format_exc())

                                yield LLMResponse(
                                    content=json.dumps(error_result, ensure_ascii=False),
                                    role=MessageRole.TOOL.value
                                )
                        else:
                            # 找不到对应工具
                            print(colored(f" -> 错误: 找不到该工具 {function_name}", "red"))

                except Exception as e:
                    print(f"工具调用解析失败: {e}")
                    print(traceback.format_exc())
                    # 如果解析失败，仍然传递原始响应
                    yield chunk
                    continue
            else:
                # 将其他类型的响应直接传递
                yield chunk



from typing import Optional, Union, Callable, Any, Generator
import json
from dataclasses import dataclass, field
from enum import Enum
from copy import deepcopy
from tool import Tool

class Chat:
    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        messages: Optional[list[Union[str, dict, Callable, LLMResponse]]] = None,
        functions: Optional[Union[dict[str, Union[Tool, Callable]], list[Union[Tool, Callable]]]] = None,
        chat_client: Optional[LLMCilent] = None,
        recall_func: Callable = None,
        url_to_base64_func: Optional[Callable[[str], str]] = None,
        description_cache: dict[str,str] = None,
        do_process_image: Optional[bool] = None
    ):
        """初始化聊天会话

        Args:
            provider: 供应商名称，默认为"openai"
            model: 模型名称，默认为"gpt-4o-mini"
            messages: 初始消息列表，可以包含字符串、字典、可调用对象或者LLMResponse
            functions: 初始函数集合，可以是函数字典或函数列表
            chat_client: 可选的聊天客户端实例
            recall_func: 可选的回忆函数，用于获取历史消息
            url_to_base64_func: 可选的URL转base64函数
            description_cache: 可选的描述缓存字典
        """
        # 基本属性初始化
        self.provider = provider
        self.model = model
        self.chat_client = chat_client
        self.do_process_image = do_process_image

        # 工具函数初始化
        self.recall_func = recall_func
        self.url_to_base64_func = url_to_base64_func
        self.description_cache = description_cache

        # 消息列表初始化
        self.messages = []
        if messages is not None:
            self.set_messages(messages)

        # 函数集合初始化
        self.functions = {}
        if functions is not None:
            self.set_tools(functions)


    def add_message(self, content: Union[str, dict, Callable, LLMResponse], role: str='user', **kwargs):
        """
        添加消息到会话

        Args:
            content: 消息内容
            role: 消息角色 (system/user/assistant)
            **kwargs: 其他消息属性
        """
        if isinstance(content, Callable):
            content = content(self)
        if isinstance(content, str) or isinstance(content, list):
            content = {'role': role, 'content': content, **kwargs}
        if isinstance(content, LLMResponse):
            content = {'role': content.role, 'content': content.content}
        if isinstance(content, dict):
            self.messages.append(content)
            return content
        else:
            raise TypeError(f"消息格式错误，期望Union[str, dict, Callable]，得到: {type(content)}")

    def set_messages(self, messages: list[Union[str, dict, Callable, LLMResponse]]):
        """
        重新初始化消息

        Args:
            messages: 新的消息列表
        """
        self.messages = []
        for message in messages:
            self.add_message(message)

    def add_tool(self, function: Union[Callable, Tool], name: str = None) -> Union[Callable, Tool]:
        """添加函数到会话，可作为装饰器使用

        Args:
            function: 要添加的函数或Tool对象
            name: 函数名称，默认为None时使用函数原名

        Returns:
            原函数或Tool对象

        Raises:
            KeyError: 当尝试添加与已存在函数不同的同名函数时
        """
        # 1. 获取函数名
        if name is None:
            name = (function.description['function']['name']
                    if isinstance(function, Tool)
                    else function.__name__)
            # 处理lambda函数的特殊情况
            if name == '<lambda>':
                name = f"{name}_{id(function)}"

        # 2. 获取实际的函数对象
        func = function.call if isinstance(function, Tool) else function

        # 3. 检查是否存在同名但不同的函数
        if name in self.functions and func != self.functions[name].call:
            raise KeyError(f"同名函数 {name} 已存在！")

        # 4. 添加到函数字典
        self.functions[name] = function if isinstance(function, Tool) else Tool(function, name)

        return function

    def set_tools(self, functions: Union[dict[str, Union[Tool, Callable]], list[Union[Tool, Callable]]]) -> None:
        """重新设置工具函数集合

        Args:
            functions: 要设置的函数集合，可以是以下格式之一：
                - 字典：{函数名: 函数/Tool对象}
                - 列表：[函数/Tool对象]

        Raises:
            KeyError: 当列表中存在同名但不同的函数时
        """
        self.functions = {}

        if isinstance(functions, dict):
            # 处理字典输入：直接使用提供的键作为函数名
            for name, func in functions.items():
                self.add_tool(func, name)
        else:
            # 处理列表输入：让add_tool自动处理函数名
            for func in functions:
                self.add_tool(func)

    def change_model(self, provider: str = None, model: str = None):
        """
        更改供应商和模型

        Args:
            provider: 新的供应商名称
            model: 新的模型名称
        """
        if isinstance(provider, str):
            self.provider = provider
        if isinstance(model, str):
            self.model = model

    def print_messages(self):
        # 输出消息
        for message in self.messages:
            if message['role'] == 'assistant' and message.get('tool_calls'):
                role = message["role"]
                content = '\n'.join(tool_call['function']['name']+tool_call['function']['arguments']
                                    for tool_call in message["tool_calls"])
                print(colored(f"{role}: ", ROLE_TO_COLOR.get("assistant", "white")), end="", flush=True)
                print(colored(content, ROLE_TO_COLOR.get("tool", "white")))
            elif message["role"] == 'tool':
                role = message["role"]
                content = message["content"]
                print(colored(f" -> {content}", ROLE_TO_COLOR.get(role, "white")))
            else:
                role = message["role"]
                content = message["content"]
                print_colored(content, role, with_prefix=True)

    def chat(
        self,
        user_message: Optional[Union[str, dict, Callable, LLMResponse]] = None,
        recall_func: Optional[Callable[[LLMResponse], None]] = None,
        stream: bool = True,
        tool_choice: Union[str, dict, None] = 'auto',
        url_to_base64_func: Optional[Callable[[str], str]] = None,
        description_cache: Optional[dict[str, str]] = None,
        do_process_image: Optional[bool] = None,
        **kwargs
    ) -> list[LLMResponse]:
        """执行聊天交互并返回响应

        Args:
            user_message: 用户输入的消息，如果为None则直接使用现有消息历史
            recall_func: 回调函数，用于处理每个响应块，优先级高于实例的recall_func
            stream: 是否使用流式响应
            tool_choice: 工具调用选择策略
                - 'auto': 自动选择
                - 'none': 禁用工具调用
                - dict: 指定具体的工具调用配置
            url_to_base64_func: URL转base64的函数，优先级高于实例的url_to_base64_func
            description_cache: 描述缓存字典，优先级高于实例的description_cache
            **kwargs: 传递给聊天客户端的额外参数

        Returns:
            包含所有响应块的列表

        Raises:
            ValueError: 当chat_client未配置时
            TypeError: 当参数类型不正确时
        """
        if not self.chat_client:
            raise ValueError("聊天客户端未配置，请先设置chat_client")

        # 添加用户消息（如果有）
        if user_message is not None:
            message = self.add_message(user_message)
            role = message["role"]
            content = message["content"]
            # print(colored(f"{role}: {content}", ROLE_TO_COLOR.get(role, "white")))
            print_colored(content, role, with_prefix=True)

        # 准备工具函数列表
        tools = list(self.functions.values())

        # 使用提供的函数或回退到实例的函数
        effective_url_func = url_to_base64_func or self.url_to_base64_func
        if not description_cache is None:
            effective_cache = description_cache
        elif not self.description_cache is None:
            effective_cache = self.description_cache
        else:
            effective_cache = {}

        try:
            # 调用聊天客户端
            response = self.chat_client.chat(
                messages=self.messages,
                tools=tools,
                tool_choice=tool_choice,
                provider=self.provider,
                model=self.model,
                stream=stream,
                url_to_base64_func=effective_url_func,
                description_cache=effective_cache,
                do_process_image=do_process_image if do_process_image is not None else self.do_process_image,
                **kwargs
            )

            # 处理响应
            responses: list[LLMResponse] = []
            effective_recall = recall_func or self.recall_func

            for chunk in response:
                if not isinstance(chunk, LLMResponse):
                    raise TypeError(f"预期响应类型为LLMResponse，实际得到：{type(chunk)}")

                if chunk.role=='assistant' and chunk.content:
                    self.add_message(chunk)

                if effective_recall:
                    effective_recall(chunk)
                responses.append(chunk)

            return responses

        except Exception as e:
            # 这里可以添加日志记录
            response = LLMResponse(
                content=f'# {e}',
                role='assistant',
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            )
            print(traceback.format_exc())
            effective_recall(response)
            return [response]
            # raise RuntimeError(f"聊天过程中发生错误: {str(e)}") from e

    def __repr__(self) -> str:
        """返回会话的字符串表示"""
        return (
            f"Chat("
            f"provider='{self.provider}', "
            f"model='{self.model}', "
            f"messages={len(self.messages)}, "
            f"functions={len(self.functions)}"
            f")"
        )

    def __str__(self) -> str:
        """返回会话的用户友好字符串表示"""
        return (
            f"聊天会话 ["
            f"供应商: {self.provider}, "
            f"模型: {self.model}, "
            f"消息数: {len(self.messages)}, "
            f"函数数: {len(self.functions)}"
            f"]"
        )

def sum_res(res_list: list[LLMResponse], role='assistant', content=''):
    return sum(res_list, start=LLMResponse(role=role, content=content))


def get_weather(location: str, format: str = "celsius"):
    '''
    Get the current weather (on solar system)

    @param
    location: The city and state, e.g. San Francisco, CA

    format: The temperature unit to use. Infer this from the users location.
        enum: ["celsius"]
    '''
    return f"{location}: 晴天, 27 {format}"

def test_vision(model, attr):
    chat = Chat(
        chat_client=llm_client,
        model=model,
        url_to_base64_func=get_image_base64,
    )
    try:
        chat.chat(user_message=[{'type':'text','text':'你看这里面是什么'},{'type': 'image_url', 'image_url': {'url': '''data:image/png;base64,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'''}}] , stream=True)
    except:
        attr["vision"] = False

def test_func(model, attr):
    chat = Chat(
        chat_client=llm_client,
        model=model,
        url_to_base64_func=get_image_base64,
    )
    chat.add_tool(get_weather)
    try:
        chat.chat(user_message=[{'type':'text','text':'北京的天气怎么样'}])
    except:
        attr["function_calling"] = False

def test(model, attr):
    chat = Chat(
        chat_client=llm_client,
        model=model,
        url_to_base64_func=get_image_base64,
    )
    try:
        chat.chat(user_message=[{'type':'text','text':'你好呀'}])
        return True
    except:
        attr["use"] = False

# 示例用法
if __name__ == "__main__":
    from tool import Tool
    from dotenv import load_dotenv

    load_dotenv()



    # 创建LLM客户端
    llm_client = LLMCilent()

    # 示例聊天
    messages = [
        {"role": "system", "content": "你是一个有帮助的助手"},
        # {"role": "assistant", "content": "北京当前是晴天，温度为25摄氏度。"},
        # {"role": "user", "content": "哇哦"},
    ]

    # print(Tool(get_weather).description)

    # response = llm_client.chat(
    #     messages=messages,
    #     tools=[Tool(get_weather)],
    #     tool_choice="auto",
    #     provider="openai",
    #     model="gpt-4o",
    #     # stream=False
    # )

    # # 打印响应
    # llm_client.print_response(response)

    chat = Chat(
        chat_client=llm_client,
        model='gpt-3.5-turbo',
        url_to_base64_func=get_image_base64,
    )
    chat.messages = messages
    chat.add_tool(get_weather)
    chat.print_messages()
    models = storage.get("llm_system", "config")['providers']['openai']['models']
    for model, attr in models.items():
        if test(model, attr):
            test_func(model, attr)
            test_vision(model, attr)
    # chat.print_messages()
    # print(chat.chat(user_message='我超，好低的温度', stream=True))
