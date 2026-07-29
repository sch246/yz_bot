"""Value types shared by LLM clients, tools, and chat callers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MessageRole(Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    THINK = "think"
    TOOL = "tool"


@dataclass
class ModelCapabilities:
    vision: bool = False
    function_calling: bool = False
    prompt_price: float = 0.0
    completion_price: float = 0.0


@dataclass
class LLMResponse:
    content: str | list
    role: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "LLMResponse") -> "LLMResponse":
        if isinstance(self.content, list) or isinstance(other.content, list):
            left = self.content if isinstance(self.content, list) else ([{"type": "text", "text": self.content}] if self.content else [])
            right = other.content if isinstance(other.content, list) else ([{"type": "text", "text": other.content}] if other.content else [])
            content: str | list = left + right
        else:
            separator = "\n\n" if self.content and other.content else ""
            content = f"{self.content}{separator}{other.content}"
        return LLMResponse(
            content=content,
            role=self.role,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass
class ToolCallResult:
    tool_call_id: str
    name: str
    arguments: str
    content: str
