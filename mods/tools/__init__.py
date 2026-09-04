"""Registry and per-Chat binding for Python and Markdown tool modules."""

# WHY: 这一整套是从更早的 tool 系统迁移过来的，迁移由 GPT 执行，所以文件里有若干形状
# 并未经过维护者裁决（见下面几处指向本注释的标记）。维护者对这套东西的期望是：
#
# 1. tool 与 skill 本质二合一：Python 的模块 docstring 就相当于 Markdown 全文，首行始终
#    显示用于索引，激活后展开全部，展开后还能按需继续索引子文件夹内容。
#    _split_description、_render_context、_source_paths 合起来已经是这个形状。
# 2. 让模型能随时改自己的工具，并主动察觉到工具可更新；更新后立即可用，失败则拿到错误栈。
#    "更新后立即可用/拿到错误栈"由 reload_tools + registry._failures 覆盖，结果以追加
#    的方式进上下文，见 _announce。"主动察觉磁盘变了"由 _drift_hint 覆盖：它是末尾 hint，
#    每次子请求重算、不进历史。两者别混——_announce 是显式 reload/load 的结果，_drift_hint
#    是磁盘状态的探测，而且只报告不加载。
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
        # WHY: 只 iterdir 顶层是有意的分层，不是漏了递归。模块目录是常驻上下文，递归扫描
        # 会让子文件夹里的东西一开局就全部占位；只列顶层，子文件夹的内容就变成"展开之后
        # 按需索引"的一层——由激活后的模块正文引用，或由 Python 正常 import 取用。
        # 这与首行/全文的分层是同一个道理，往下再多一级而已。加递归会破坏这个性质。
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


_REMINDER_CLOSE = "</system-reminder>"


def _framed(body: str) -> str:
    """Wrap one announcement in the frame this module owns.

    WHY: 模块正文是模型自己写的——reload_tools 应用的就是它刚写进磁盘的文件。所以正文里
    字面的结束标记必须转义，否则模型可以提前关掉这层框架，让后面它自己写的内容看起来像
    是系统说的。框架归本模块所有，被框住的内容不许碰它。这一条抄自 deepseek-harness 的
    agent-instructions：那里同样是"插件拥有框架、工作区文本中的结束标记被转义"。
    """
    escaped = body.replace(_REMINDER_CLOSE, "<\\/system-reminder>")
    return f"<system-reminder>\n{escaped}\n{_REMINDER_CLOSE}"


# WHY: 这是**基线**，只在 bind 时渲染一次，之后永不改写。工具变动走 _announce 追加到
# 上下文末尾，见那边的说明。
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
    """Create the baseline module catalog; later changes are appended, not rewritten."""
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
        self._announcements: list[str] = []
        self._activate(meta)
        # 基线：bind 时写一次，之后这条消息不再变。
        self.context_message["content"] = _render_context(self.registry, self.active)
        register = getattr(session, "add_context_provider", None)
        if callable(register):
            register(self._take_announcements)
        add_hint = getattr(session, "add_hint", None)
        if callable(add_hint):
            add_hint(self._drift_hint)

    def load(self, names: str | Iterable[str]) -> dict[str, dict]:
        """Activate only in-memory last-good modules in this session."""
        results: dict[str, dict] = {}
        with self._lock:
            before = self._capture()
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
            self._announce(before)
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
            before = self._capture()
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
            self._announce(before)
        return results

    def _drift_hint(self) -> str:
        """Report disk sources that differ from last-good, as an end-of-context hint.

        WHY: 这是"被动提醒"那一层，对应维护者期望里"模型能主动察觉工具可更新"的一半。
        以前 registry.scan() 的差异只有模型**自己调 list_tools** 才看得到，改了文件不说
        就没人知道。

        WHY: 它是 hint 不是 provider——每次子请求重算，不进历史。磁盘差异正是那种"随时
        可以重算、而且只有当前值有意义"的状态：文件改回去，提醒就该消失，而不是在上下文
        里留着一条"曾经改过"。

        WHY: 它只报告，不加载。发现变化与决定应用是两步，理由见 _announce 的 WHY——磁盘
        上的模块随时可能正被写到一半。所以这里也不承载模块正文，只给名字。

        WHY: 每次都真读磁盘，没有节流。一次 scan 是十来个小文件的 read_bytes，相对一次
        模型往返可以忽略；加缓存反而会让"刚改完就问"读到旧值，那正是这条提醒要解决的场景。
        """
        try:
            changes = self.registry.scan()
        except Exception:
            _log.exception("failed to scan tool sources for the drift hint")
            return ""
        labels = (("added", "新增"), ("modified", "修改"), ("deleted", "删除"))
        parts = [
            f"{label} {', '.join(changes[kind])}"
            for kind, label in labels
            if changes.get(kind)
        ]
        if not parts:
            return ""
        return _framed(
            "工具模块的磁盘源与已加载版本不一致，尚未应用：\n"
            + "\n".join(f"- {part}" for part in parts)
            + "\n需要时用 reload_tools 显式应用；不应用则当前生效的仍是上面目录里的版本。"
        )

    # WHY?: UI 模式（可切换，尚未实现）。默认是现在这样：工具变动走 _announce 追加，
    # 进历史、可回放。切换后改成"把整个工具状态作为一整块 hint 放在上下文末尾"——
    # 好处是注意力：工具只剩一个权威副本，而且它明确位于所有修改之后。就地改写做不到
    # 这一点，追加式也做不到（上下文里会同时留着旧正文和新正文，模型可能以为修改之前
    # 的工具就长那样）。代价是那一整块每次子请求都是未命中缓存的新 token，工具循环越长
    # 付得越多，所以要可切换而不是直接替换掉现在的模式。
    # 开关照 chat._subcommand 的 image/use_model 来：`#` 子命令 + getchatstorage() 的
    # 按窗口设置，读取端 normalize + 失效自愈。仓库里已有这个成熟模式，别另起一套。
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

    def _capture(self) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        """Snapshot everything the model can currently see about tool modules."""
        return (
            {name: module.content for name, module in self.active.items()},
            {name: module.description for name, module in self.registry.modules.items()},
            dict(self.registry.failures),
        )

    def _announce(self, before: tuple[dict[str, str], dict[str, str], dict[str, str]]) -> None:
        """Queue one appended notice describing what changed, if anything did.

        WHY: 这里只往队列里放，不直接改 session.messages。工具是在 assistant(tool_calls)
        与 tool result 之间执行的，此刻插一条 user 消息会拆散这一对，供应商会拒。队列由
        llm.Chat 的 context provider 在下一次子请求前取走，那时 tool result 已经补齐。

        WHY: 这是**显式 load/reload 的结果报告**，不是磁盘变化探测器。调用链只有一条：
        模型调 meta 的 reload_tools/load_tools → SessionBinding.reload/load → 这里。
        没有 watcher，改文件本身仍然不生效——这一条不要"顺手补上"：磁盘上的模块随时
        可能正被写到一半（模型自己也在写），自动加载等于把半个文件当成新版本，而
        registry 的 last-good 只在校验通过后才替换，正是为了让这种时刻不影响正在跑的
        会话。想让模型知道磁盘变了，用 list_tools 报告差异，或者将来的 hint 层，都不是
        在这里加扫描。

        WHY: 通告是**累积**的，靠顺序而不是替换生效——上下文里会同时留着某模块的旧正文
        和后来追加的新正文，后者在后面。这是追加式的必然代价，deepseek-harness 的
        baseline+refresh 也是如此。换成回头改写旧消息就等于放弃前缀缓存，而那正是这套
        东西存在的理由。同一轮内的多次变动各自成条、按发生顺序交付，不互相覆盖。
        """
        before_active, before_catalog, before_failures = before
        after_active, after_catalog, after_failures = self._capture()

        def joined(label: str, names) -> str | None:
            listed = sorted(names)
            return f"- {label}：{', '.join(listed)}" if listed else None

        new_failures = {
            name: error
            for name, error in after_failures.items()
            if before_failures.get(name) != error
        }
        lines = [
            joined("目录新增", set(after_catalog) - set(before_catalog)),
            joined("目录移除", set(before_catalog) - set(after_catalog)),
            joined("目录描述更新", {
                name for name in set(after_catalog) & set(before_catalog)
                if after_catalog[name] != before_catalog[name]
            }),
            joined("已激活", set(after_active) - set(before_active)),
            joined("已停用", set(before_active) - set(after_active)),
            joined("已激活模块内容更新", {
                name for name in set(after_active) & set(before_active)
                if after_active[name] != before_active[name]
            }),
            joined("加载失败", new_failures),
        ]
        body = [line for line in lines if line]
        if not body:
            return

        sections = ["工具模块已变化（本条由系统追加，不是用户发言）：", *body]
        for name in sorted(after_active):
            content = after_active[name]
            if content and before_active.get(name) != content:
                sections.append(f"\n## 已激活模块 {name}\n{content}")
        for name, error in sorted(new_failures.items()):
            sections.append(f"\n## 加载失败 {name}\n{error}")
        self._announcements.append(_framed("\n".join(sections)))

    def _take_announcements(self) -> list[dict]:
        """Hand queued notices to llm.Chat as appended user messages."""
        with self._lock:
            queued, self._announcements = self._announcements, []
        return [{"role": "user", "content": text} for text in queued]


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
