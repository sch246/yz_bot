"""Value types shared by LLM clients, tools, and chat callers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelCapabilities:
    vision: bool = False
    # WHY: 图片能不能放在 **tool** 消息里，由对端决定，不由协议文档决定。OpenAI 与 DeepSeek
    # 的文档都写着"图片仅支持出现在 user 消息中"，但 DeepSeek 的 chat/completions 实收
    # tool 消息 content 数组里的 image_url——2026-09-18 实测 deepseek-flash 读对了工具结果
    # 那张图里的随机码。所以它按模型登记，未登记一律 False，退回"把图换成占位文字"那条老路：
    # 那条路不会 400，代价只是模型看不到图，比让整次请求失败好。
    tool_images: bool = False
    function_calling: bool = False
    prompt_price: float = 0.0
    prompt_cached_price: float = 0.0
    completion_price: float = 0.0


@dataclass
class LLMResponse:
    content: str | list
    role: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # prompt_tokens 里命中供应商缓存的那部分——它和未命中部分的单价不同，所以计费必须
    # 分开看，不能只留一个 prompt_tokens（见 llm.pricing）。默认 0 表示"对端没报"，
    # 于是整段 prompt 按未命中价算。
    cached_tokens: int = 0
    reasoning_content: str | None = None

    def __add__(self, other: "LLMResponse") -> "LLMResponse":
        if isinstance(self.content, list) or isinstance(other.content, list):
            left = self.content if isinstance(self.content, list) else ([{"type": "text", "text": self.content}] if self.content else [])
            right = other.content if isinstance(other.content, list) else ([{"type": "text", "text": other.content}] if other.content else [])
            content: str | list = left + right
        else:
            separator = "\n\n" if self.content and other.content else ""
            content = f"{self.content}{separator}{other.content}"
        reasoning_content = None
        if self.reasoning_content is not None or other.reasoning_content is not None:
            reasoning_content = (
                (self.reasoning_content or "")
                + (other.reasoning_content or "")
            )
        return LLMResponse(
            content=content,
            role=self.role,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            reasoning_content=reasoning_content,
        )


@dataclass
class ToolCallResult:
    tool_call_id: str
    name: str
    arguments: str
    content: str
