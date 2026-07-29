"""Discover, load, and stop the flat :mod:`mods` module set.

Importing this package performs the one application load pass.  Business
modules remain ordinary Python modules; this file only owns lifecycle state.
"""

from __future__ import annotations

import heapq
import importlib
import logging
from pathlib import Path
import threading
import traceback
from types import ModuleType
from typing import Final


INFRA: Final = "INFRA"
FEATURE: Final = "FEATURE"
LATE: Final = "LATE"

PHASES: Final = (INFRA, FEATURE, LATE)
REQUIRED_MODULES: Final = {
    "bot",
    "command",
    "connect",
    "context",
    "message",
    "storage",
}

# Import success and load success are deliberately separate facts.
ctx: dict[str, ModuleType] = {}
available: set[str] = set()
import_failures: dict[str, str] = {}
load_failures: dict[str, str] = {}
load_order: list[str] = []

_log = logging.getLogger(__name__)
_exit_lock = threading.Lock()
_exited = False


def is_available(module: str | ModuleType) -> bool:
    """Return whether *module* completed its load step."""
    name = module if isinstance(module, str) else module.__name__.rsplit(".", 1)[-1]
    return name in available


def get_available(name: str) -> ModuleType | None:
    """Return a loaded module without turning ``mods`` into a name proxy."""
    return ctx.get(name) if name in available else None


def _module_names() -> list[str]:
    root = Path(__file__).parent
    files = {
        path.stem
        for path in root.glob("*.py")
        if path.name != "__init__.py" and not path.name.startswith("_")
    }
    packages = {
        path.name
        for path in root.iterdir()
        if path.is_dir()
        and not path.name.startswith("_")
        and (path / "__init__.py").is_file()
    }
    collisions = sorted(files & packages)
    if collisions:
        raise RuntimeError(
            "mods contain both file and package modules: " + ", ".join(collisions)
        )
    return sorted(files | packages)


def _import_all() -> None:
    for name in _module_names():
        try:
            ctx[name] = importlib.import_module(f"{__name__}.{name}")
        except Exception:
            import_failures[name] = traceback.format_exc()
            _log.exception("failed to import mod %s", name)

    # A decorator may have run before its module later failed to import.
    for registry_name in ("command", "capture"):
        registry = ctx.get(registry_name)
        discard = getattr(registry, "discard_module", None)
        if callable(discard):
            for name in import_failures:
                discard(name)


def _relations(module: ModuleType, attr: str) -> tuple[str, ...]:
    value = getattr(module, attr, ())
    if isinstance(value, str):
        return (value,)
    try:
        relations = tuple(value)
    except TypeError as error:
        raise TypeError(f"{module.__name__}.{attr} must be an iterable of names") from error
    if not all(isinstance(name, str) and name for name in relations):
        raise TypeError(f"{module.__name__}.{attr} contains an invalid module name")
    return relations


def _calculate_load_order() -> list[str]:
    phases: dict[str, str] = {}
    errors: list[str] = []
    for name, module in ctx.items():
        phase = getattr(module, "PHASE", FEATURE)
        if phase not in PHASES:
            errors.append(f"{name}: unknown PHASE {phase!r}")
        else:
            phases[name] = phase

    edges: dict[str, set[str]] = {name: set() for name in ctx}
    for name, module in ctx.items():
        if name not in phases:
            continue
        try:
            after = _relations(module, "LOAD_AFTER")
            before = _relations(module, "LOAD_BEFORE")
        except (TypeError, ValueError) as error:
            errors.append(str(error))
            continue

        for dependency, source, target in (
            *((other, other, name) for other in after),
            *((other, name, other) for other in before),
        ):
            if dependency not in ctx:
                _log.warning(
                    "%s lifecycle relation references unavailable mod %s; ignored",
                    name,
                    dependency,
                )
                continue
            if dependency not in phases:
                continue
            source_phase = PHASES.index(phases[source])
            target_phase = PHASES.index(phases[target])
            if source_phase > target_phase:
                errors.append(
                    f"{name}: lifecycle relation {source} -> {target} reverses "
                    f"{phases[source]} -> {phases[target]}"
                )
            elif source_phase == target_phase:
                edges[source].add(target)

    if errors:
        raise RuntimeError("invalid mods lifecycle:\n" + "\n".join(errors))

    result: list[str] = []
    for phase in PHASES:
        names = {name for name, value in phases.items() if value == phase}
        indegree = {name: 0 for name in names}
        for source in names:
            for target in edges[source]:
                if target in names:
                    indegree[target] += 1
        ready = [name for name, degree in indegree.items() if degree == 0]
        heapq.heapify(ready)
        ordered: list[str] = []
        while ready:
            source = heapq.heappop(ready)
            ordered.append(source)
            for target in sorted(edges[source]):
                if target not in indegree:
                    continue
                indegree[target] -= 1
                if indegree[target] == 0:
                    heapq.heappush(ready, target)
        if len(ordered) != len(names):
            cycle = sorted(name for name, degree in indegree.items() if degree)
            raise RuntimeError(
                f"lifecycle cycle in {phase}: {', '.join(cycle)}"
            )
        result.extend(ordered)
    return result


def _load_all(order: list[str]) -> None:
    for name in order:
        module = ctx[name]
        hook = getattr(module, "on_load", None)
        try:
            if hook is not None:
                if not callable(hook):
                    raise TypeError(f"mods.{name}.on_load is not callable")
                hook(ctx)
        except Exception:
            load_failures[name] = traceback.format_exc()
            _log.exception("failed to load mod %s", name)
            continue
        available.add(name)
        load_order.append(name)
        capture_module = ctx.get("capture")
        activate = getattr(capture_module, "activate", None)
        if callable(activate):
            activate(name)


def _boot() -> None:
    _import_all()
    order = _calculate_load_order()
    _load_all(order)
    missing = sorted(REQUIRED_MODULES - available)
    if missing:
        failed_imports = ", ".join(sorted(import_failures)) or "none"
        failed_loads = ", ".join(sorted(load_failures)) or "none"
        raise RuntimeError(
            "required mods unavailable: "
            + ", ".join(missing)
            + f" (import failures: {failed_imports}; load failures: {failed_loads})"
        )


def exit() -> None:
    """Run successful modules' exit hooks once, in reverse load order."""
    global _exited
    with _exit_lock:
        if _exited:
            return
        _exited = True

    for name in reversed(load_order):
        hook = getattr(ctx[name], "on_exit", None)
        if hook is None:
            continue
        try:
            hook()
        except Exception:
            _log.exception("failed to exit mod %s", name)


try:
    _boot()
except BaseException:
    exit()
    raise
