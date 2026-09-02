"""Human-readable, streaming terminal output for live LLM calls."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urlunsplit

from termcolor import colored


ROLE_COLORS = {
    "system": "red",
    "user": "green",
    "think": "yellow",
    "assistant": "blue",
    "tool": "magenta",
}

_DATA_URI = re.compile(
    r"data:[^;\s,]+;base64,[A-Za-z0-9+/=_-]+",
    re.IGNORECASE,
)
_DATA_URI_PREFIX = 80


def format_value(value) -> str:
    """Keep request logs readable when an image was expanded to a Data URI."""
    text = str(value)

    def truncate(match: re.Match) -> str:
        data_uri = match.group(0)
        if len(data_uri) <= _DATA_URI_PREFIX:
            return data_uri
        omitted = len(data_uri) - _DATA_URI_PREFIX
        return f"{data_uri[:_DATA_URI_PREFIX]}…<省略 {omitted} 字符>"

    return _DATA_URI.sub(truncate, text)


def format_uri(uri: str, limit: int = 180) -> str:
    if uri.startswith("data:"):
        media_type = uri.partition(";")[0] or "data:image"
        return f"{media_type};base64,… ({len(uri)} 字符)"
    parsed = urlsplit(uri)
    value = (
        urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        if parsed.scheme in {"http", "https"}
        else uri
    )
    return value if len(value) <= limit else value[:limit] + "…"


def format_content(content) -> str:
    if not isinstance(content, list):
        return format_value(content)
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            parts.append(str(item))
        elif item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        elif item.get("type") in ("image", "image_url"):
            value = item.get("image_url", item.get("image", ""))
            uri = value.get("url", "") if isinstance(value, dict) else value
            parts.append(f"[图片({uri})]" if uri else "[图片]")
        else:
            parts.append(json.dumps(item, ensure_ascii=False, default=str))
    return format_value("".join(parts))


def write(value, role: str, *, end: str = "\n", prefix: bool = False) -> None:
    label = f"{role}: " if prefix else ""
    print(
        colored(label + format_content(value), ROLE_COLORS.get(role, "white")),
        end=end,
        flush=True,
    )


def notice(value) -> None:
    print(colored(format_value(value), "light_grey"), flush=True)


def error(value) -> None:
    print(colored(format_value(value), "red"), flush=True)


def print_request(model: str, messages: list[dict]) -> None:
    notice(f"发送给 {model} 的消息：")
    for message in messages:
        role = message.get("role", "system")
        if role == "assistant" and message.get("tool_calls"):
            content = "\n".join(
                call["function"]["name"] + call["function"].get("arguments", "")
                for call in message["tool_calls"]
            )
            write(f"{role}: ", role, end="")
            write(content, "tool")
        elif role == "tool":
            write(f" -> {message.get('content', '')}", role)
        else:
            write(message.get("content", ""), role, prefix=True)


class StreamPrinter:
    """Print provider deltas immediately while the caller keeps its own buffer."""

    def __init__(self, model: str) -> None:
        self.model = model
        self._display_role: str | None = None

    def chunk(self, value: str, role: str, display_role: str | None = None) -> None:
        display_role = display_role or role
        if self._display_role != display_role:
            self.finish()
            write(f"{role}({self.model}): ", role, end="")
            self._display_role = display_role
        write(value, display_role, end="")

    def finish(self) -> None:
        if self._display_role is not None:
            print(flush=True)
            self._display_role = None

    def tool_call(self, role: str, name: str, arguments: str) -> None:
        self.finish()
        write(f"{role}({self.model}): ", role, end="")
        write(name + arguments, "tool")
