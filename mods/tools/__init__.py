'''指导模型增删查改统一工具与 Skill 模块，并说明 last-good、显式应用和当前会话激活原理。

## 工具模块的三层状态

所有模块源都放在 `mods/tools/` 顶层。`foo.py` 是带函数的工具模块，`foo.md` 是没有函数的 Skill；二者使用同一套生命周期，不能同 stem 共存。

1. 磁盘源码：刚编辑的文件，还不一定生效。
2. 进程级 last-good：最后一次成功初始化或 `reload_tools` 的完整模块版本。
3. 当前 Chat 激活态：经 `load_tools` 加入当前任务的模块内容和函数。

`reload_tools` 从磁盘应用源码；`load_tools` 只激活 last-good，不能混用。没有自动 watcher，也不要等待修改自行生效。

## 查询

先调用 `list_tools()`。它列出 last-good 模块及一句话描述、当前 Chat 已激活模块、磁盘相对 last-good 的新增/修改/删除，以及最近的加载失败 traceback。需要阅读源码时，再用文件能力或 `exec_code` 精确读取 `mods/tools/<name>.py` 或 `.md`。

## 新增 Python 工具模块

选择不以下划线开头、未被占用的模块名，先确认同 stem 的 `.py` 和 `.md` 都不存在。普通模块不需要修改本文件。格式如下：

```python
"""一句话说明这组工具解决什么问题。

这里开始的内容只在模块激活后进入 system 提示，可写使用时机、约束和组合方式。
"""

from some_package import dependency


def lookup(query: str, limit: int = 10) -> str:
    """查询目标并返回文本结果。

    @param
    query: 查询内容
    limit: 最大结果数
    """
    return str(dependency.lookup(query, limit=limit))


__all__ = ["lookup"]
```

一个文件可以通过 `__all__` 导出多个同步函数，也可导出空列表、只提供说明。每个参数都要有类型标注，函数要有 docstring，签名必须能按关键字调用；不要使用位置专用参数、`*args`、`**kwargs` 或异步函数。模型侧函数名带模块命名空间，例如 `foo__lookup`。

Python 模块可以正常 import 第三方依赖、其它 `mods`，也可以 `from ._helper import value` 引用同目录以下划线开头的 helper。候选加载会执行顶层代码，所以顶层只放 import、常量和定义；它与 Bot 处在同一宿主信任域，不是沙箱。

写入后先调用 `reload_tools(["foo"])` 完成整模块校验并建立 last-good，再调用 `load_tools(["foo"])` 把余下说明和整组函数激活到当前 Chat。

## 新增 Markdown Skill

建立 `mods/tools/foo.md`。第一行必须是一句无需展开就能判断用途的 summary，第二行开始全部是 Skill 正文：

```markdown
指导模型审查发布清单并识别遗漏的部署步骤。

## 使用时机
……
```

Markdown 不需要 front matter、额外 summary 字段或同步机制，也不导出函数。目录不递归扫描；Skill 可在正文中引用子目录资源。写完同样先 `reload_tools(["foo"])`，需要在当前任务使用时再 `load_tools(["foo"])`。

## 修改

先精确读取现有源文件，只修改目标模块，再调用 `reload_tools(["foo"])`。成功后 last-good 才替换；如果模块已在当前 Chat 激活，内容和函数会为下一次模型子请求更新。失败时根据返回的完整 traceback 修复并再次 reload，旧 last-good 和旧活动版本继续服务。仅调用 `load_tools` 不会读取刚改的磁盘文件。

下划线 helper 不是独立模块，它的变化不会单独出现在 `list_tools` 中。修改 helper 后要显式 reload 所有 import 它的模块。

## 删除

先确认精确模块名和源文件，再删除对应的单个 `mods/tools/foo.py` 或 `.md`，不要宽泛递归删除。随后调用 `reload_tools(["foo"])`；registry 发现源文件缺失后才删除 last-good，并从当前 Chat 移除模块内容和函数。只删文件但不 reload 时，旧 last-good 仍然有效。删除 helper 前要先检查并 reload 所有受影响模块。

## 原子性与请求边界

每个模块单独校验和提交：任一导出失败，整个模块保留旧版；一次 reload 多个名称时，其它成功模块仍可独立提交。单个 LLM 子请求发送前会冻结工具 schema 与 callable 的同一份快照，所以 load/reload 只从下一次模型子请求起生效，不改变已发请求，也不改变同一响应中的其它工具调用。
'''

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import inspect
import io
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
_BASE_TOOL_NAMES = ("exec_code", "list_tools", "reload_tools", "load_tools")


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
        tools: dict[str, Tool] = {}
        for export_name, function in exports.items():
            schema_name = f"{name}__{export_name}"
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
    lines = [inspect.cleandoc(__doc__ or ""), "", "## 可用工具模块"]
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
        self._install_base_tools()
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

    def _install_base_tools(self) -> None:
        def exec_code(expr: str, code: str = "") -> str:
            """执行实时 Python 代码。

            @param
            expr: 在 code 后求值并返回的表达式
            code: 先执行的 Python 代码
            """
            return _exec_code(expr, code)

        def list_tools() -> str:
            """列出 last-good 工具模块、当前激活状态与磁盘源码变化。"""
            return self.list_text()

        def reload_tools(names: list[str]) -> str:
            """从磁盘重新加载指定模块；失败时继续保留旧 last-good 版本。

            @param
            names: 要重新加载或显式删除的模块名
            """
            return _format_results(self.reload(names))

        def load_tools(names: list[str]) -> str:
            """把指定 last-good 模块激活到当前聊天，不读取磁盘。

            @param
            names: 要在当前聊天中激活的模块名
            """
            return _format_results(self.load(names))

        functions = (exec_code, list_tools, reload_tools, load_tools)
        candidates = {
            name: _validated_tool(function, name)
            for name, function in zip(_BASE_TOOL_NAMES, functions, strict=True)
        }
        conflicts = [name for name in candidates if name in self.session.functions]
        if conflicts:
            raise KeyError("base tool names already exist: " + ", ".join(conflicts))
        self.session.functions.update(candidates)

    def _activate(self, module: ToolModule) -> None:
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


def _exec_code(expr: str, code: str = "") -> str:
    from mods import context, op, py

    if not op.require_op(context.current()):
        return "权限不足"
    buffer = io.StringIO()
    missing = object()
    original = py.loc.get("print", missing)
    py.loc["print"] = lambda *values, sep=" ", end="\n": buffer.write(
        sep.join(map(str, values)) + end
    )
    try:
        exec(code, py.loc)
        result = repr(eval(expr, py.loc))
    finally:
        if original is missing:
            py.loc.pop("print", None)
        else:
            py.loc["print"] = original
    printed = buffer.getvalue().rstrip()
    return f"[print输出]\n{printed}\n[结果] {result}" if printed else result


def _format_results(results: Mapping[str, OperationResult]) -> str:
    action_labels = {
        "loaded": "已加载",
        "reloaded": "已重载",
        "deleted": "已删除",
        "activated": "已激活",
        "replaced": "已替换",
    }
    succeeded = [
        f"- {name}: {action_labels.get(result.action, result.action)}"
        for name, result in results.items()
        if result.ok
    ]
    failed = [
        f"- {name}:\n{result.error or '未知错误'}"
        for name, result in results.items()
        if not result.ok
    ]
    return "\n".join([
        "成功:",
        *(succeeded or ["- (无)"]),
        "失败:",
        *(failed or ["- (无)"]),
    ])


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
    "default_registry",
]
