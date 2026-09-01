"""Registry and per-Chat binding for Python and Markdown tool modules."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import inspect
import logging
from pathlib import Path
import sys
import threading
import traceback as traceback_module
from types import MappingProxyType, ModuleType
from typing import get_type_hints

from mods.llm.tools import Tool


_log = logging.getLogger(__name__)
_SOURCE_SUFFIXES = frozenset({".py", ".md"})
_BASE_MODULE_NAME = "meta"
_BASE_TOOL_NAMES = ("exec_code", "list_tools", "reload_tools", "load_tools")
_current_binding_var: ContextVar[SessionBinding | None] = ContextVar(
    "tool_session_binding", default=None
)


def current_binding() -> SessionBinding:
    """Return the binding only while one bound meta tool is executing."""
    binding = _current_binding_var.get()
    if binding is None:
        raise RuntimeError("tool called outside a bound Chat session")
    return binding


@dataclass(frozen=True)
class ToolModule:
    """One fully validated, in-process last-good module."""

    name: str
    description: str
    content: str
    tools: Mapping[str, Tool]
    source_suffix: str
    source: bytes


@dataclass(frozen=True)
class OperationResult:
    """Outcome of one independently committed module operation."""

    ok: bool
    action: str
    module: ToolModule | None = None
    error: str | None = None


class ToolRegistry:
    """Keep validated tool modules as an explicit in-process last-good set."""

    def __init__(self, source_dir: str | Path | None = None) -> None:
        self.source_dir = Path(source_dir) if source_dir is not None else Path(__file__).parent
        self._modules: dict[str, ToolModule] = {}
        self._failures: dict[str, str] = {}
        self._initialized = False
        self._lock = threading.RLock()
        self._import_package = f"{__name__}._registry_{id(self):x}"

    @property
    def modules(self) -> dict[str, ToolModule]:
        """Return a sorted snapshot of all last-good modules."""
        self._ensure_initialized()
        with self._lock:
            return dict(sorted(self._modules.items()))

    @property
    def failures(self) -> dict[str, str]:
        """Return full tracebacks from the most recent failed loads."""
        self._ensure_initialized()
        with self._lock:
            return dict(sorted(self._failures.items()))

    def get(self, name: str) -> ToolModule | None:
        """Read one last-good module without consulting the disk."""
        self._ensure_initialized()
        with self._lock:
            return self._modules.get(name)

    def scan(self) -> dict[str, list[str]]:
        """Derive source changes without applying any of them."""
        self._ensure_initialized()
        with self._lock:
            paths = self._source_paths()
            disk_names = set(paths)
            loaded_names = set(self._modules)
            modified = []
            for name in sorted(disk_names & loaded_names):
                module = self._modules[name]
                candidates = paths[name]
                if len(candidates) != 1:
                    modified.append(name)
                    continue
                path = candidates[0]
                try:
                    unchanged = (
                        path.suffix == module.source_suffix
                        and path.read_bytes() == module.source
                    )
                except Exception:
                    unchanged = False
                if not unchanged:
                    modified.append(name)
            return {
                "added": sorted(disk_names - loaded_names),
                "modified": modified,
                "deleted": sorted(loaded_names - disk_names),
            }

    def reload(
        self,
        names: str | Iterable[str],
        *,
        before_replace: Callable[[str, ToolModule | None], None] | None = None,
    ) -> dict[str, OperationResult]:
        """Reload or delete requested modules, committing each independently.

        ``before_replace`` participates in the per-module commit.  A binding
        uses it to prove that an already-active module can be replaced before
        the registry changes its last-good entry.
        """
        self._ensure_initialized()
        requested = _requested_names(names)
        results: dict[str, OperationResult] = {}
        with self._lock:
            for requested_name in requested:
                result_name = requested_name if isinstance(requested_name, str) else repr(requested_name)
                try:
                    name = _validate_module_name(requested_name)
                    paths = self._source_paths().get(name, [])
                    previous = self._modules.get(name)
                    if not paths:
                        if name == _BASE_MODULE_NAME:
                            raise FileNotFoundError("required tool module source does not exist: meta")
                        if previous is None:
                            raise FileNotFoundError(f"tool module source does not exist: {name}")
                        if before_replace is not None:
                            before_replace(name, None)
                        del self._modules[name]
                        self._failures.pop(name, None)
                        results[name] = OperationResult(True, "deleted")
                        continue

                    candidate = self._load_candidate(name, paths)
                    if before_replace is not None:
                        before_replace(name, candidate)
                    self._modules[name] = candidate
                    self._failures.pop(name, None)
                    action = "reloaded" if previous is not None else "loaded"
                    results[name] = OperationResult(True, action, candidate)
                except Exception:
                    error = traceback_module.format_exc()
                    _log.error("failed to reload tool module %r\n%s", requested_name, error)
                    previous = self._modules.get(requested_name) if isinstance(requested_name, str) else None
                    self._failures[result_name] = error
                    results[result_name] = OperationResult(False, "failed", previous, error)
        return results

    def _ensure_initialized(self) -> None:
        with self._lock:
            if self._initialized:
                return
            for name, paths in self._source_paths().items():
                try:
                    self._modules[name] = self._load_candidate(name, paths)
                except Exception:
                    error = traceback_module.format_exc()
                    self._failures[name] = error
                    _log.error("failed to initialize tool module %r\n%s", name, error)
            self._initialized = True

    def _source_paths(self) -> dict[str, list[Path]]:
        if not self.source_dir.is_dir():
            return {}
        grouped: dict[str, list[Path]] = {}
        for path in self.source_dir.iterdir():
            if (
                path.is_file()
                and not path.name.startswith("_")
                and path.suffix.lower() in _SOURCE_SUFFIXES
            ):
                grouped.setdefault(path.stem, []).append(path)
        return {
            name: sorted(paths, key=lambda path: path.name)
            for name, paths in sorted(grouped.items())
        }

    def _load_candidate(self, name: str, paths: list[Path]) -> ToolModule:
        _validate_module_name(name)
        if len(paths) != 1:
            names = ", ".join(path.name for path in paths)
            raise RuntimeError(f"tool module has conflicting sources for {name}: {names}")
        path = paths[0]
        source = path.read_bytes()
        if path.suffix.lower() == ".md":
            description, content = _split_description(
                source.decode("utf-8"), path
            )
            return ToolModule(
                name,
                description,
                content,
                MappingProxyType({}),
                ".md",
                source,
            )
        return self._load_python(name, path, source)

    def _load_python(self, name: str, path: Path, source: bytes) -> ToolModule:
        package = self._prepare_import_package()
        candidate_name = f"{package}._candidate_{name}"
        candidate = ModuleType(candidate_name)
        candidate.__dict__.update({
            "__file__": str(path),
            "__package__": package,
            "__builtins__": __builtins__,
        })

        prefix = package + "."
        previous_children = {
            module_name: module
            for module_name, module in sys.modules.items()
            if module_name.startswith(prefix)
        }
        for module_name in previous_children:
            sys.modules.pop(module_name, None)
        sys.modules[candidate_name] = candidate
        try:
            exec(compile(source, str(path), "exec"), candidate.__dict__)
        finally:
            for module_name in tuple(sys.modules):
                if module_name.startswith(prefix):
                    sys.modules.pop(module_name, None)
            sys.modules.update(previous_children)

        description, content = _split_description(candidate.__doc__, path)
        exports = _explicit_exports(candidate, path)
        if name == _BASE_MODULE_NAME and tuple(exports) != _BASE_TOOL_NAMES:
            raise ValueError(
                "meta.py.__all__ must be exactly: " + ", ".join(_BASE_TOOL_NAMES)
            )
        tools: dict[str, Tool] = {}
        for export_name, function in exports.items():
            schema_name = export_name if name == _BASE_MODULE_NAME else f"{name}__{export_name}"
            tools[schema_name] = _validated_tool(function, schema_name)
        return ToolModule(
            name,
            description,
            content,
            MappingProxyType(tools),
            ".py",
            source,
        )

    def _prepare_import_package(self) -> str:
        package = sys.modules.get(self._import_package)
        if package is None:
            package = ModuleType(self._import_package)
            package.__package__ = self._import_package
            package.__path__ = [str(self.source_dir)]
            sys.modules[self._import_package] = package
        else:
            package.__path__ = [str(self.source_dir)]
        return self._import_package


def _requested_names(names: str | Iterable[str]) -> tuple[object, ...]:
    if isinstance(names, str):
        return (names,)
    requested = []
    for name in names:
        if name not in requested:
            requested.append(name)
    return tuple(requested)


def _validate_module_name(name: object) -> str:
    if (
        not isinstance(name, str)
        or not name
        or name.startswith("_")
    ):
        raise ValueError(f"invalid tool module name: {name!r}")
    return name


def _split_description(value: object, path: Path) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError(f"{path.name} requires a description on its first line")
    description, separator, content = value.partition("\n")
    description = description.removesuffix("\r")
    if not description.strip():
        raise ValueError(f"{path.name} requires a non-empty first-line description")
    return description, content if separator else ""


def _explicit_exports(module: ModuleType, path: Path) -> dict[str, Callable]:
    if "__all__" not in module.__dict__:
        raise ValueError(f"{path.name} must define __all__ explicitly")
    raw = module.__dict__["__all__"]
    if isinstance(raw, (str, bytes)):
        raise TypeError(f"{path.name}.__all__ must be a sequence of function names")
    try:
        names = tuple(raw)
    except TypeError as error:
        raise TypeError(f"{path.name}.__all__ must be iterable") from error
    if len(names) != len(set(names)):
        raise ValueError(f"{path.name}.__all__ contains duplicate names")

    exports: dict[str, Callable] = {}
    for name in names:
        if not isinstance(name, str) or not name.isidentifier() or name.startswith("_"):
            raise ValueError(f"{path.name}.__all__ contains an invalid function name: {name!r}")
        value = getattr(module, name, None)
        if not inspect.isroutine(value):
            raise TypeError(f"{path.name} export {name!r} is not a function")
        exports[name] = value
    return exports


def _validated_tool(function: Callable, schema_name: str) -> Tool:
    if inspect.iscoroutinefunction(function) or inspect.isasyncgenfunction(function):
        raise TypeError(f"tool {schema_name} must execute synchronously")
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
            f"tool {schema_name} parameters must accept model keywords: "
            + ", ".join(invalid)
        )
    hints = get_type_hints(function)
    missing = [name for name in signature.parameters if name not in hints]
    if missing:
        raise TypeError(
            f"tool {schema_name} parameters require annotations: " + ", ".join(missing)
        )
    if not inspect.getdoc(function):
        raise ValueError(f"tool {schema_name} requires a docstring")

    tool = Tool(function, schema_name)
    schema = tool.description
    function_schema = schema.get("function")
    parameters = function_schema.get("parameters") if isinstance(function_schema, dict) else None
    required = [
        parameter.name
        for parameter in signature.parameters.values()
        if parameter.default is inspect.Parameter.empty
    ]
    if (
        schema.get("type") != "function"
        or not isinstance(function_schema, dict)
        or function_schema.get("name") != schema_name
        or not function_schema.get("description")
        or not isinstance(parameters, dict)
        or parameters.get("type") != "object"
        or set(parameters.get("properties", {})) != set(signature.parameters)
        or parameters.get("required") != required
    ):
        raise ValueError(f"Tool produced an incomplete schema for {schema_name}")
    return tool


def _render_context(
    registry: ToolRegistry,
    active: Mapping[str, ToolModule],
) -> str:
    modules = registry.modules
    lines = ["## 可用工具模块"]
    if modules:
        lines.extend(f"- {name}: {module.description}" for name, module in modules.items())
    else:
        lines.append("- (无)")
    result = "\n".join(lines)
    for name in sorted(active):
        content = active[name].content
        if content:
            result += f"\n\n## 已激活模块 {name}\n{content}"
    return result


def create_context_message(
    *, registry: ToolRegistry | None = None
) -> dict[str, str]:
    """Create the one system message later updated in place by a binding."""
    selected = default_registry if registry is None else registry
    return {"role": "system", "content": _render_context(selected, {})}


class SessionBinding:
    """Own one chat session's explicitly active tool-module projection."""

    def __init__(
        self,
        session,
        context_message: dict,
        *,
        registry: ToolRegistry,
    ) -> None:
        if not isinstance(context_message, dict) or context_message.get("role") != "system":
            raise TypeError("context_message must be an existing system message dict")
        if not isinstance(getattr(session, "functions", None), dict):
            raise TypeError("session.functions must be a dict")
        self.session = session
        self.context_message = context_message
        self.registry = registry
        self.active: dict[str, ToolModule] = {}
        self._lock = threading.RLock()
        meta = self.registry.get(_BASE_MODULE_NAME)
        if meta is None:
            failure = self.registry.failures.get(_BASE_MODULE_NAME)
            raise RuntimeError("required tool module meta is unavailable" + (f"\n{failure}" if failure else ""))
        self._activate(meta)
        self._render()

    def load(self, names: str | Iterable[str]) -> dict[str, OperationResult]:
        """Activate only in-memory last-good modules in this session."""
        requested = _requested_names(names)
        results: dict[str, OperationResult] = {}
        with self._lock:
            for requested_name in requested:
                result_name = requested_name if isinstance(requested_name, str) else repr(requested_name)
                try:
                    name = _validate_module_name(requested_name)
                    module = self.registry.get(name)
                    if module is None:
                        raise KeyError(f"no last-good tool module: {name}")
                    previous = self.active.get(name)
                    self._activate(module)
                    results[name] = OperationResult(
                        True,
                        "replaced" if previous is not None else "activated",
                        module,
                    )
                except Exception:
                    error = traceback_module.format_exc()
                    _log.error("failed to activate tool module %r\n%s", requested_name, error)
                    previous = self.active.get(requested_name) if isinstance(requested_name, str) else None
                    results[result_name] = OperationResult(False, "failed", previous, error)
            self._render()
        return results

    def reload(self, names: str | Iterable[str]) -> dict[str, OperationResult]:
        """Reload last-good modules and refresh those active in this session."""
        with self._lock:
            results = self.registry.reload(names, before_replace=self._replace_if_active)
            self._render()
            return results

    def list_text(self) -> str:
        """Describe last-good, active, failed, and changed modules."""
        modules = self.registry.modules
        changes = self.registry.scan()
        failures = self.registry.failures
        lines = ["可用模块:"]
        lines.extend(
            f"- {name}: {module.description}"
            + ("（已激活）" if name in self.active else "")
            for name, module in modules.items()
        )
        if not modules:
            lines.append("- (无)")
        lines.append("源码变化:")
        labels = {"added": "新增", "modified": "修改", "deleted": "删除"}
        changed = False
        for kind in ("added", "modified", "deleted"):
            if changes[kind]:
                changed = True
                lines.append(f"- {labels[kind]}: {', '.join(changes[kind])}")
        if not changed:
            lines.append("- (无)")
        if failures:
            lines.append("加载失败:")
            lines.extend(f"- {name}:\n{error}" for name, error in failures.items())
        return "\n".join(lines)

    def _activate(self, module: ToolModule) -> None:
        module = self._bind_module(module)
        previous = self.active.get(module.name)
        previous_tools = dict(previous.tools) if previous is not None else {}
        functions = self.session.functions
        for name, old_tool in previous_tools.items():
            if functions.get(name) is not old_tool:
                raise KeyError(f"active tool ownership changed: {name}")
        conflicts = [
            name
            for name in module.tools
            if name in functions and name not in previous_tools
        ]
        if conflicts:
            raise KeyError("session tool names already exist: " + ", ".join(conflicts))

        for name in previous_tools:
            functions.pop(name)
        functions.update(module.tools)
        self.active[module.name] = module

    def _bind_module(self, module: ToolModule) -> ToolModule:
        if module.name != _BASE_MODULE_NAME:
            return module
        tools = {}
        for name, original in module.tools.items():
            bound = Tool(original.call, name)

            @wraps(original.call)
            def bound_call(*args, __call=original.call, **kwargs):
                token = _current_binding_var.set(self)
                try:
                    return __call(*args, **kwargs)
                finally:
                    _current_binding_var.reset(token)

            bound.call = bound_call
            tools[name] = bound
        return ToolModule(
            module.name,
            module.description,
            module.content,
            MappingProxyType(tools),
            module.source_suffix,
            module.source,
        )

    def _deactivate(self, name: str) -> None:
        previous = self.active.get(name)
        if previous is None:
            return
        functions = self.session.functions
        for tool_name, old_tool in previous.tools.items():
            if functions.get(tool_name) is not old_tool:
                raise KeyError(f"active tool ownership changed: {tool_name}")
        for tool_name in previous.tools:
            functions.pop(tool_name)
        del self.active[name]

    def _replace_if_active(self, name: str, module: ToolModule | None) -> None:
        if name not in self.active:
            return
        if module is None:
            self._deactivate(name)
        else:
            self._activate(module)

    def _render(self) -> None:
        self.context_message["content"] = _render_context(self.registry, self.active)


default_registry = ToolRegistry()


def bind_session(
    session,
    context_message: dict,
    initial_modules: Iterable[str] = (),
    *,
    registry: ToolRegistry | None = None,
) -> SessionBinding:
    """Bind base tools and explicit module activation to an existing Chat."""
    binding = SessionBinding(
        session,
        context_message,
        registry=default_registry if registry is None else registry,
    )
    initial = tuple(initial_modules)
    if initial:
        binding.load(initial)
    return binding


__all__ = [
    "OperationResult",
    "SessionBinding",
    "ToolModule",
    "ToolRegistry",
    "bind_session",
    "create_context_message",
    "current_binding",
    "default_registry",
]
