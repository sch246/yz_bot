"""Explicitly load model-callable functions from device-owned Python files.

The in-process active mapping is authoritative.  Merely changing a source
file has no effect until :meth:`SelfToolLoader.load` successfully validates
that file and replaces its one active entry.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import inspect
from pathlib import Path
import threading
import traceback as traceback_module
from types import UnionType
from typing import get_args, get_origin, get_type_hints, Union

from mods.llm.tools import Tool


EnvironmentFactory = Callable[[], Mapping[str, object]]


def _default_environment() -> Mapping[str, object]:
    # Import lazily: mods.py installs loc during the LATE lifecycle phase,
    # while this module is discovered earlier with the other public mods.
    from mods import py

    return py.loc


@dataclass(frozen=True)
class LoadResult:
    """Outcome for one requested tool name."""

    ok: bool
    action: str
    function: Callable | None = None
    error: str | None = None


@dataclass(frozen=True)
class _ActiveTool:
    function: Callable
    source: bytes


class SelfToolLoader:
    """Manage explicitly loaded tools from one flat source directory."""

    def __init__(
        self,
        source_dir: str | Path = "data/tools",
        *,
        environment_factory: EnvironmentFactory = _default_environment,
        reserved_names: Iterable[str] = (),
    ) -> None:
        self.source_dir = Path(source_dir)
        self._environment_factory = environment_factory
        self._reserved_names = frozenset(reserved_names)
        self._active: dict[str, _ActiveTool] = {}
        self._lock = threading.RLock()

    def list(self) -> dict[str, Callable]:
        """Return a name-to-function snapshot of the active versions."""
        with self._lock:
            return {
                name: active.function
                for name, active in sorted(self._active.items())
            }

    def scan(self) -> dict[str, builtins.list[str]]:
        """Report source changes relative to the active in-process versions."""
        with self._lock:
            files = self._source_files()
            active_names = set(self._active)
            file_names = set(files)
            return {
                "added": sorted(file_names - active_names),
                "modified": sorted(
                    name
                    for name in file_names & active_names
                    if files[name].read_bytes() != self._active[name].source
                ),
                "deleted": sorted(active_names - file_names),
            }

    def load(
        self,
        names: str | Iterable[str],
        *,
        reserved_names: Iterable[str] = (),
    ) -> dict[str, LoadResult]:
        """Explicitly load or unload each requested name independently.

        A missing active source is an unload.  A missing inactive source and
        every compile, top-level execution, or validation error are failures.
        Failures keep the previous active function and include a full
        traceback for diagnosis.
        """
        requested = (names,) if isinstance(names, str) else tuple(names)
        caller_reserved = frozenset(reserved_names)
        results: dict[str, LoadResult] = {}
        with self._lock:
            for name in requested:
                try:
                    self._validate_requested_name(name)
                    if name in self._reserved_names or name in caller_reserved:
                        raise ValueError(f"tool name is reserved: {name}")
                    path = self.source_dir / f"{name}.py"
                    if not path.is_file():
                        if name in self._active:
                            del self._active[name]
                            results[name] = LoadResult(True, "unloaded")
                            continue
                        raise FileNotFoundError(f"tool source does not exist: {path}")

                    source = path.read_bytes()
                    function = self._load_candidate(name, path, source)
                    # This assignment is the only activation write.  Nothing
                    # from a failed candidate can enter the authoritative map.
                    self._active[name] = _ActiveTool(function, source)
                    results[name] = LoadResult(True, "loaded", function=function)
                except Exception:
                    previous = self._active.get(str(name))
                    results[str(name)] = LoadResult(
                        False,
                        "failed",
                        function=previous.function if previous is not None else None,
                        error=traceback_module.format_exc(),
                    )
        return results

    def _source_files(self) -> dict[str, Path]:
        if not self.source_dir.is_dir():
            return {}
        return {
            path.stem: path
            for path in self.source_dir.glob("*.py")
            if path.is_file()
        }

    @staticmethod
    def _validate_requested_name(name: object) -> None:
        if (
            not isinstance(name, str)
            or not name
            or not name.isidentifier()
            or name.startswith("_")
        ):
            raise ValueError(f"invalid tool name: {name!r}")

    def _load_candidate(self, name: str, path: Path, source: bytes) -> Callable:
        environment = self._environment_factory()
        if not isinstance(environment, Mapping):
            raise TypeError("environment_factory must return a mapping")
        globals_ = dict(environment)
        globals_.update({
            "__file__": str(path),
            "__name__": f"self_tool_{name}",
            "__package__": None,
        })
        before = dict(globals_)
        exec(compile(source, str(path), "exec"), globals_)

        public_functions = {
            public_name: value
            for public_name, value in globals_.items()
            if not public_name.startswith("_")
            and inspect.isroutine(value)
            and (public_name not in before or value is not before[public_name])
        }
        if set(public_functions) != {name}:
            found = ", ".join(sorted(public_functions)) or "none"
            raise ValueError(
                f"{path.name} must export exactly one public function named "
                f"{name}; found: {found}"
            )
        function = public_functions[name]
        if function.__name__ != name:
            raise ValueError(
                f"exported function name {function.__name__!r} does not match {name!r}"
            )
        self._validate_tool(function, name)
        return function

    @staticmethod
    def _validate_tool(function: Callable, name: str) -> None:
        if inspect.iscoroutinefunction(function) or inspect.isasyncgenfunction(function):
            raise TypeError("tool function must execute synchronously")
        signature = inspect.signature(function)
        invalid_kinds = {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }
        invalid = [
            parameter.name
            for parameter in signature.parameters.values()
            if parameter.kind in invalid_kinds
        ]
        if invalid:
            raise TypeError(
                "tool parameters must be callable by model-supplied keywords: "
                + ", ".join(invalid)
            )

        hints = get_type_hints(function)
        missing = [
            parameter
            for parameter in signature.parameters
            if parameter not in hints
        ]
        if missing:
            raise TypeError("tool parameters require type annotations: " + ", ".join(missing))
        for parameter in signature.parameters:
            _validate_annotation(hints[parameter], parameter)

        if not inspect.getdoc(function):
            raise ValueError("tool function requires a docstring")

        # Tool remains the schema authority.  These checks reject a partially
        # formed schema instead of maintaining a second schema generator here.
        tool = Tool(function, name)
        schema = tool.description
        function_schema = schema.get("function")
        if schema.get("type") != "function" or not isinstance(function_schema, dict):
            raise ValueError("Tool produced an invalid function schema")
        parameters = function_schema.get("parameters")
        expected_required = [
            parameter.name
            for parameter in signature.parameters.values()
            if parameter.default is inspect.Parameter.empty
        ]
        if (
            function_schema.get("name") != name
            or not function_schema.get("description")
            or not isinstance(parameters, dict)
            or parameters.get("type") != "object"
            or set(parameters.get("properties", {})) != set(signature.parameters)
            or parameters.get("required") != expected_required
        ):
            raise ValueError("Tool produced an incomplete function schema")


def _validate_annotation(annotation: object, parameter: str) -> None:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        values = get_args(annotation)
        if not values:
            raise TypeError(f"unsupported annotation for {parameter}: {annotation!r}")
        for value in values:
            if value is not type(None):
                _validate_annotation(value, parameter)
        return
    if origin in (builtins.list, tuple, set):
        values = get_args(annotation)
        if values:
            _validate_annotation(values[0], parameter)
        return
    if origin is dict:
        return
    if annotation not in (str, int, float, bool, builtins.list, dict):
        raise TypeError(f"unsupported annotation for {parameter}: {annotation!r}")


_default_loader = SelfToolLoader()


def list() -> dict[str, Callable]:
    """Return the default loader's active tools."""
    return _default_loader.list()


def scan() -> dict[str, builtins.list[str]]:
    """Scan the default ``data/tools`` directory without loading changes."""
    return _default_loader.scan()


def load(
    names: str | Iterable[str],
    *,
    reserved_names: Iterable[str] = (),
) -> dict[str, LoadResult]:
    """Explicitly update names in the default loader."""
    return _default_loader.load(names, reserved_names=reserved_names)
