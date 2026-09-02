"""Human-readable terminal output for live LLM calls.

Whole messages are records on the ``llm`` streams, so the terminal can drop the
request stack without losing it.  The delta stream stays a direct write: live
typing is a terminal effect rather than a record, and buffering it to a line
would destroy the one thing worth watching in real time.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urlunsplit

from termcolor import colored

from mods.log import stream


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

_notices = stream("llm")
_requests = stream("llm.request")
_messages = stream("llm.message")


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


def paint(value, role: str, *, prefix: bool = False) -> str:
    label = f"{role}: " if prefix else ""
    return colored(label + format_content(value), ROLE_COLORS.get(role, "white"))


def write(value, role: str, *, end: str = "\n") -> None:
    """Paint straight to the terminal; only the live delta stream uses this."""
    print(paint(value, role), end=end, flush=True)


def notice(value) -> None:
    _notices.info(colored(format_value(value), "light_grey"))


def error(value) -> None:
    # A handled failure the caller recovered from: red, but the same stream and
    # the same level.  Real incidents still go through ``logger.exception``.
    _notices.info(colored(format_value(value), "red"))


def message(value, role: str, *, label: str = "", label_role: str = "") -> None:
    """Emit one complete, already finished message as a single record."""
    head = paint(label, label_role or role) if label else ""
    _messages.info(head + paint(value, role))


def print_request(model: str, messages: list[dict], start: int = 0) -> None:
    """Log the request; ``start`` skips what an earlier round already logged."""
    pending = messages[start:]
    if not pending:
        return
    lines = [colored(f"发送给 {model} 的{'新增' if start else ''}消息：", "light_grey")]
    for entry in pending:
        role = entry.get("role", "system")
        if role == "assistant" and entry.get("tool_calls"):
            content = "\n".join(
                call["function"]["name"] + call["function"].get("arguments", "")
                for call in entry["tool_calls"]
            )
            lines.append(paint(f"{role}: ", role) + paint(content, "tool"))
        elif role == "tool":
            lines.append(paint(f" -> {entry.get('content', '')}", role))
        else:
            lines.append(paint(entry.get("content", ""), role, prefix=True))
    # One request is one record: the whole stack goes out or none of it does.
    _requests.info("\n".join(lines))


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
        # Already complete when the provider hands it over, so it is a message
        # record rather than part of the delta stream.
        self.finish()
        message(name + arguments, "tool", label=f"{role}({self.model}): ", label_role=role)
