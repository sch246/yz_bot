"""Registry and per-Chat binding for Python and Markdown tool modules."""

# WHY: 这一整套是从更早的 tool 系统迁移过来的，迁移由 GPT 执行，所以文件里有若干形状
# 并未经过维护者裁决（见下面几处指向本注释的标记）。维护者对这套东西的期望是：
#
# 1. tool 与 skill 本质二合一：Python 的模块 docstring 就相当于 Markdown 全文，首行始终
#    显示用于索引，激活后展开全部。当前 _split_description 与 _render_context 已经是
#    这个形状；尚未做到的是"允许进一步索引子文件夹内的内容"——_source_paths 只 iterdir
#    顶层。
# 2. 让模型能随时改自己的工具，并主动察觉到工具可更新；更新后立即可用，失败则拿到错误栈。
#    reload_tools + registry._failures 已经覆盖"立即可用/拿到错误栈"，
#    "主动察觉"目前只能靠模型自己调 list_tools。
# 3. meta.py 是这套东西的使用说明书，给模型看的。
#
# 因此判断这里的代码时，标准不是"它已经在这儿而且能跑"，而是 docs/design-principles.md
# 对任何抽象的那个提问：它被观察到解决了哪个问题。整体重写是被允许的。

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


class ToolRegistry:
    """Keep validated tool modules as an explicit in-process last-good set."""

    def __init__(self, source_dir: str | Path | None = None) -> None:
        self.source_dir = Path(source_dir) if source_dir is not None else Path(__file__).parent
        self._modules: dict[str, ToolModule] = {}
        self._failures: dict[str, str] = {}
        self._initialized = False
        # A binding holds this for the length of one prepare/commit pair.
        self.lock = threading.RLock()
        # WHY: 用 id(self) 拼包名是为了让多个 registry 的候选互不串扰。查下来不是 bug
        # （活着的两个 registry 地址必不相同；registry 被回收后 sys.modules 里只剩这个包
        # 本身，_prepare_import_package 会把 __path__ 重新指向新的 source_dir），但生产
        # 自始至终只有一个 default_registry——这是迁移带来的形状，不是为解决观察到的问题
        # 而写的。见模块顶部注释；重写时不必保留。
        self._import_package = f"{__name__}._registry_{id(self):x}"

    @property
    def modules(self) -> dict[str, ToolModule]:
        """Return a sorted snapshot of all last-good modules."""
        self._ensure_initialized()
        with self.lock:
            return dict(sorted(self._modules.items()))

    @property
    def failures(self) -> dict[str, str]:
        """Return full tracebacks from the most recent failed loads."""
        self._ensure_initialized()
        with self.lock:
            return dict(sorted(self._failures.items()))

    def get(self, name: str) -> ToolModule | None:
        """Read one last-good module without consulting the disk."""
        self._ensure_initialized()
        with self.lock:
            return self._modules.get(name)

    def scan(self) -> dict[str, list[str]]:
        """Derive source changes without applying any of them."""
        self._ensure_initialized()
        with self.lock:
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

    def prepare(self, name: str) -> ToolModule | None:
        """Validate one module from disk without committing it.

        ``None`` means the source is gone and the last-good entry should go with
        it.  Raising leaves both disk and last-good untouched.
        """
        self._ensure_initialized()
        with self.lock:
            paths = self._source_paths().get(name, [])
            if paths:
                return self._load_candidate(name, paths)
            if name == _BASE_MODULE_NAME:
                raise FileNotFoundError("required tool module source does not exist: meta")
            if self._modules.get(name) is None:
                raise FileNotFoundError(f"tool module source does not exist: {name}")
            return None

    def commit(self, name: str, module: ToolModule | None) -> str:
        """Swap one last-good entry and report the action taken."""
        with self.lock:
            previous = self._modules.get(name)
            if module is None:
                del self._modules[name]
                action = "deleted"
            else:
                self._modules[name] = module
                action = "reloaded" if previous is not None else "loaded"
            self._failures.pop(name, None)
            return action

    def record_failure(self, name: str, error: str) -> None:
        """Keep one full traceback for ``list_tools`` to show."""
        with self.lock:
            self._failures[name] = error

    def _ensure_initialized(self) -> None:
        with self.lock:
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

        # WHY: 这段存取还原让候选模块的下划线 helper 跟着候选一起重新加载。执行前清空
        # package.* 下的所有条目，候选里的 `from ._helper import x` 就必须重新读盘；执行后
        # 再把新产生的条目摘掉、把原有的放回去，进程里不会留下候选的半成品子模块（实测：
        # 改 _helper.py 后 reload_tools 立刻拿到新值，sys.modules 里只剩包本身）。
        # 删掉它，reload_tools 会对已缓存的 helper 视而不见——源码改了却不生效，而且不报错。
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
            # WHY: 这一行就是信任边界本身——校验候选模块的唯一方式是执行它的顶层代码。
            # 没有沙箱，也不打算加：docs/llm.md 的"当前信任边界与维护取舍"记录了维护者
            # 接受这条模型的理由（群白名单是主要运维控制面），meta.py 的模块手册则要求
            # 顶层只放 import、常量和定义。所以 reload_tools 与 .py、exec_code、宿主机
            # 操作同属一个信任域，不要在这里加"先静态检查再执行"之类的半吊子防线：它挡不住
            # 顶层副作用，只会让人误以为这里是安全的。
            exec(compile(source, str(path), "exec"), candidate.__dict__)
        finally:
            for module_name in tuple(sys.modules):
                if module_name.startswith(prefix):
                    sys.modules.pop(module_name, None)
            sys.modules.update(previous_children)

        description, content = _split_description(candidate.__doc__, path)
        exports = _explicit_exports(candidate, path)
        # WHY: 要求完全一致（含顺序）来自迁移，未经裁决。真正承重的只是"meta 的四个恢复
        # 入口必须都在"——少一个模型就没法自救，docs/llm.md 记的也是这条。顺序相等属于
        # 附带收紧。见模块顶部注释；重写时把它放宽成"包含"是安全的。
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


def _result_name(requested_name: object) -> str:
    return requested_name if isinstance(requested_name, str) else repr(requested_name)


def _failure(log_message: str, requested_name: object) -> dict:
    """Log the current exception and describe it for the calling model."""
    error = traceback_module.format_exc()
    _log.error(log_message + "\n%s", requested_name, error)
    return {"action": "failed", "error": error}


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

    # WHY: 下面这段把 Tool 刚从同一个 signature 生成的 schema 又逐项校验了一遍。它防的
    # 不是模块作者写错（上面的检查已覆盖），而是 Tool._load 自身回归。这是迁移留下的
    # 形状，没有对应的真实事故。见模块顶部注释；重写时可以删。
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


# WHY?: 缺"末尾 hint"这一层，方向已定、尚未实现——实现时按这套来，别另起一套。
# 现状：整个模块目录塞在一条 system 消息里，由 SessionBinding._render 就地改写。就地改写
# 意味着每次 reload/load 都会让它后面的全部上下文失去前缀缓存，而模块目录恰恰是最爱变的
# 那部分；同时模型只有主动调 list_tools 才知道磁盘变了。
# 目标形状（可参考 deepseek-harness 注入 context 的做法）：按缓存原理分层——频繁变化且
# 不需要进历史的内容就是 hint，始终追加到上下文末尾但不写入历史，像 system 提示词一样
# 支持用函数生成，只是重置时机不同。工具变化由 hint 提示，模型不必主动 list，还能省掉
# 一个工具。这是 llm 侧的一层通用能力，不属于 tools 包，所以要连带改 Chat 的消息组装。
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

    def load(self, names: str | Iterable[str]) -> dict[str, dict]:
        """Activate only in-memory last-good modules in this session."""
        results: dict[str, dict] = {}
        with self._lock:
            for requested_name in _requested_names(names):
                try:
                    name = _validate_module_name(requested_name)
                    module = self.registry.get(name)
                    if module is None:
                        raise KeyError(f"no last-good tool module: {name}")
                    previous = self.active.get(name)
                    self._activate(module)
                    results[name] = {"action": "replaced" if previous is not None else "activated"}
                except Exception:
                    results[_result_name(requested_name)] = _failure(
                        "failed to activate tool module %r", requested_name
                    )
            self._render()
        return results

    def reload(self, names: str | Iterable[str]) -> dict[str, dict]:
        """Apply each module's disk source, keeping this session's projection in step.

        A module is committed to last-good only after an already-active copy of
        it has been replaced here, so a Chat never keeps tools the registry no
        longer has.  Every module is independent: one failure leaves that
        module's old last-good and old active version serving.
        """
        results: dict[str, dict] = {}
        with self._lock, self.registry.lock:
            for requested_name in _requested_names(names):
                try:
                    name = _validate_module_name(requested_name)
                    candidate = self.registry.prepare(name)
                    if name in self.active:
                        if candidate is None:
                            self._deactivate(name)
                        else:
                            self._activate(candidate)
                    results[name] = {"action": self.registry.commit(name, candidate)}
                except Exception:
                    result_name = _result_name(requested_name)
                    failure = _failure("failed to reload tool module %r", requested_name)
                    self.registry.record_failure(result_name, failure["error"])
                    results[result_name] = failure
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
    "SessionBinding",
    "ToolModule",
    "ToolRegistry",
    "bind_session",
    "create_context_message",
    "current_binding",
    "default_registry",
]
