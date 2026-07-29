"""Load-activated, process-local fallback captures for the link pipeline."""

from __future__ import annotations

from typing import Callable


_declared: dict[str, tuple[str, Callable, str | None]] = {}
_active: dict[str, tuple[Callable, str | None]] = {}


def _owner(function: Callable) -> str:
    parts = function.__module__.split(".")
    return parts[1] if len(parts) >= 2 and parts[0] == "mods" else parts[-1]


def _declare(function: Callable, before: str | None):
    if not callable(function):
        raise TypeError("capture target must be callable")
    if before is not None and (not isinstance(before, str) or not before):
        raise TypeError("capture before must be a non-empty link name")
    owner = _owner(function)
    name = owner if function.__name__ == "run" else f"{owner}.{function.__name__}"
    if name in _declared and _declared[name][1] is not function:
        raise ValueError(f"capture {name!r} is already declared")
    _declared[name] = owner, function, before
    return function


def capture(function: Callable | None = None, *, before: str | None = None):
    """Declare a process-local node, optionally before one persisted link."""
    if function is None:
        return lambda target: _declare(target, before)
    return _declare(function, before)


def activate(module_name: str) -> None:
    """Activate declarations only after their owner completed Load."""
    for name, (owner, function, before) in _declared.items():
        if owner == module_name:
            _active[name] = function, before


def discard_module(module_name: str) -> None:
    for name, (owner, _function, _before) in list(_declared.items()):
        if owner == module_name:
            _declared.pop(name, None)
            _active.pop(name, None)


def items() -> tuple[tuple[str, Callable, str | None], ...]:
    return tuple(
        (name, function, before)
        for name, (function, before) in _active.items()
    )


def on_load(_ctx) -> None:
    from mods import load_order

    for module_name in load_order:
        activate(module_name)
