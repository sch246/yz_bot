"""Low-ceremony command registration and dispatch."""

from __future__ import annotations

from functools import wraps
from inspect import signature
import re
from typing import Callable

from mods import context
from mods import cq
from mods import text


_commands: dict[str, Callable] = {}
_owners: dict[str, str] = {}
_command_text = re.compile(r"^(\S+)([\s\S]*)$")


def _owner(function: Callable) -> str:
    parts = function.__module__.split(".")
    if len(parts) >= 2 and parts[0] == "mods":
        return parts[1]
    return parts[-1]


def command(function: Callable):
    """Register by module and function name; the decorator takes no arguments."""
    if not callable(function):
        raise TypeError("@command does not accept arguments")
    owner = _owner(function)
    command_name = owner if function.__name__ == "run" else f"{owner}.{function.__name__}"
    if re.search(r"\s", command_name):
        raise ValueError(f"invalid command name: {command_name!r}")
    existing = _commands.get(command_name)
    if existing is not None and existing is not function:
        raise ValueError(f"command {command_name!r} is already registered")
    _commands[command_name] = function
    _owners[command_name] = owner
    return function


def discard_module(module_name: str) -> None:
    """Remove registrations left by a module which failed during import."""
    module_name = module_name.rsplit(".", 1)[-1]
    for name in [name for name, owner in _owners.items() if owner == module_name]:
        _commands.pop(name, None)
        _owners.pop(name, None)


def _is_available(name: str) -> bool:
    from mods import is_available

    return is_available(_owners[name])


def available_commands() -> tuple[str, ...]:
    return tuple(sorted(name for name in _commands if _is_available(name)))


def items() -> tuple[tuple[str, Callable], ...]:
    return tuple((name, _commands[name]) for name in available_commands())


def get(name: str) -> Callable | None:
    function = _commands.get(name)
    if function is None or not _is_available(name):
        return None
    return function


def match(value: str) -> tuple[str, str] | None:
    """Match text after the leading dot, preserving whitespace in ``body``."""
    result = _command_text.match(value)
    if result is None:
        return None
    name, body = result.groups()
    return (name, body) if get(name) is not None else None


def run(name: str, body: str):
    function = get(name)
    if function is None:
        raise KeyError(f"command {name!r} is unavailable")
    return function(body)


def params(function: Callable):
    """Adapt the established ``(msg, params..., line, extra_lines)`` shape."""
    @wraps(function)
    def call(body: str):
        event = context.current()
        if event is None:
            raise RuntimeError("command has no current event")
        lines = cq.unescape(body).splitlines()
        while len(lines) < 2:
            lines.append("")
        first, *extra_lines = lines
        arguments = [event]
        while len(arguments) < len(signature(function).parameters) - 2:
            value, first = text.read_params(first)
            arguments.append(value)
        return function(*arguments, first, extra_lines)

    return call


def grouponly(function: Callable):
    @wraps(function)
    def call(*args, **kwargs):
        event = context.current()
        if event is None or event.get("group_id") is None:
            return "此命令仅群内可用!"
        return function(*args, **kwargs)

    return call
