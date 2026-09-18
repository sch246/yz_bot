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
import time
import traceback as traceback_module
from types import MappingProxyType, ModuleType
from typing import get_type_hints

from mods.llm.tools import Tool


_log = logging.getLogger(__name__)
_SOURCE_SUFFIXES = frozenset({".py", ".md"})
_BASE_MODULE_NAME = "meta"
# 恢复入口：meta 必须导出这四个，少一个模型就没法自救。可以多导出别的。
_BASE_TOOL_NAMES = ("exec_code", "list_tools", "reload_tools", "load_tools")
# 窗口里多久没被调用过的工具模块就不再装回去（秒）。见 SessionBinding.restore 与 touch。
_IDLE_RECLAIM_SECONDS = 3600.0


def _human_time(seconds: float) -> str:
    """Render a duration like 3600.0 as `1小时` for the reclaim notice."""
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds / 3600:g}小时"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds / 60:g}分钟"
    return f"{seconds:g}秒"
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
    # WHY: 模块自己声明"只在 op 发起的轮里可见"。它是模块的属性而不是门控本身——门控是
    # op_tool_visible，两边分开，因为"哪些模块受限"是模块作者的事，"这一轮算不算 op 轮"
    # 是运行期的判断。见 op_tool_visible 与 docs/working/proposals/op-toolbox.md 决定三。
    op_only: bool = False


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
                False,
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
        # WHY: 只要求四个恢复入口都在，不再要求 __all__ 与它完全相等。原先是相等（含顺序），
        # 那是迁移带来的附带收紧；承重的一直只有"少一个模型就没法自救"，docs/llm.md 记的
        # 也是这条。放宽是给 meta 加 condense_ops 时逼出来的：相等的写法让 meta 多导出一个
        # 函数就整个加载失败，而那正是恢复入口所在的模块，失败等于全盘瘫痪。
        if name == _BASE_MODULE_NAME:
            absent = [tool_name for tool_name in _BASE_TOOL_NAMES if tool_name not in exports]
            if absent:
                raise ValueError(
                    "meta.py.__all__ must contain: " + ", ".join(absent)
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
            bool(getattr(candidate, "OP_ONLY", False)),
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
    catalog: Mapping[str, ToolModule],
    active: Mapping[str, ToolModule],
) -> str:
    lines = ["## 可用工具模块"]
    if catalog:
        lines.extend(f"- {name}: {module.description}" for name, module in catalog.items())
    else:
        lines.append("- (无)")
    result = "\n".join(lines)
    for name in sorted(active):
        content = active[name].content
        if content:
            result += f"\n\n## 已激活模块 {name}\n{content}"
    return result


# WHY: UI 模式下这条 system 消息**不放**工具状态，只放一个指路条。整套 UI 模式的
# 卖点就是"工具只有一个权威副本，而且它明确位于所有修改之后"；这里再留一份目录，
# 上下文里就又有两个说法了，等于白切。
_UI_POINTER = (
    "## 可用工具模块\n"
    "工具状态不在这里。当前的模块目录、已激活模块正文和磁盘变化统一放在上下文**末尾**的"
    "工具状态块里，那里是唯一权威，并且位于你所有修改之后。"
)


def op_tool_visible(name: str, module: ToolModule) -> bool:
    """Whether one module may be shown and loaded in the turn running right now.

    WHY: 只有声明了 ``OP_ONLY = True`` 的模块受限，判据是**当轮触发者**是不是 op。窗口
    相同、轮次不同，答案可以不同（同一个群里这轮是管理员、下轮是普通成员），所以它每次
    现算，结果不进目录缓存。
    """
    if not getattr(module, "op_only", False):
        return True
    try:
        from mods import context, op
    except Exception:
        return False
    try:
        return bool(op.is_op(context.current() or {}))
    except Exception:
        return False


def _visible_catalog(
    registry: ToolRegistry, visible: Callable[[str, ToolModule], bool] | None
) -> dict[str, ToolModule]:
    """The subset of last-good modules this turn is allowed to see.

    WHY: 目录来自**进程全局**的 registry，而"这一轮谁在说话"是**按窗口、按轮**的。两个
    维度不同，所以过滤发生在每次渲染，而不是在 registry 里删模块——删掉会连非 op 的窗口
    一起失去能力（op-toolbox 提案的决定三）。
    """
    pick = op_tool_visible if visible is None else visible
    return {
        name: module
        for name, module in registry.modules.items()
        if pick(name, module)
    }


def create_context_message(
    *,
    registry: ToolRegistry | None = None,
    ui_mode: bool = False,
    visible: Callable[[str, ToolModule], bool] | None = None,
) -> dict[str, str]:
    """Create the baseline module catalog; later changes are appended, not rewritten."""
    selected = default_registry if registry is None else registry
    if ui_mode:
        return {"role": "system", "content": _UI_POINTER}
    return {"role": "system", "content": _render_context(_visible_catalog(selected, visible), {})}


class SessionBinding:
    """Own one chat session's explicitly active tool-module projection."""

    def __init__(
        self,
        session,
        context_message: dict,
        *,
        registry: ToolRegistry,
        ui_mode: bool = False,
        visible: Callable[[str, ToolModule], bool] | None = None,
        persist: Callable[[Mapping[str, float]], None] | None = None,
        ttl: float | None = _IDLE_RECLAIM_SECONDS,
    ) -> None:
        if not isinstance(context_message, dict) or context_message.get("role") != "system":
            raise TypeError("context_message must be an existing system message dict")
        if not isinstance(getattr(session, "functions", None), dict):
            raise TypeError("session.functions must be a dict")
        self.session = session
        self.context_message = context_message
        self.registry = registry
        self.visible = op_tool_visible if visible is None else visible
        # 激活集合每变一次就回调一次，写到哪儿由调用方决定：连续聊天写本窗口 storage，
        # 子会话不传。见 restore 与 _save_active。
        self.persist = persist
        # 空闲多久就把模块收回去（秒）；None 或 <=0 表示不收，见 restore。
        self.ttl = ttl
        self.active: dict[str, ToolModule] = {}
        # 每个激活模块最后一次被调用的时刻，见 touch。先活在内存里，落盘由 _save_active 做。
        self._touched: dict[str, float] = {}
        # WHY: schema 名 -> 拥有它的模块名。它和 `session.functions` 是同一份事实的两面，
        # 所以在写 functions 的**同一处**（`_activate`/`_deactivate`）一起维护，别处不碰。
        # 有了它，`touch` 就不必从 `<模块名>__<函数名>` 里反解模块名——反解是猜：模块 `a`
        # 导出 `b__c` 与模块 `a__b` 导出 `c` 生成同一个 schema 名，靠前缀匹配必然有一种猜错。
        self._owner: dict[str, str] = {}
        # WHY: 这一轮装不回来、但**不该从窗口里删掉**的模块（名 -> 它原来的使用时刻）。
        # 目前只有一种来源：OP_ONLY 模块在非 op 的轮里不可见。它在这一轮确实不存在，可是
        # 窗口并没有停用它——下一次 op 自己开的轮里它就该回来。落盘时与 active 一起写回，
        # 见 _save_active；时刻用**原来**那个，所以它照样会被 restore 的空闲回收收走，不需要
        # 第二套回收规则。
        self._deferred: dict[str, float] = {}
        # 有没有还没落盘的使用时刻：只是省掉“没有变化也写一次 storage”。
        self._dirty = False
        self._lock = threading.RLock()
        meta = self.registry.get(_BASE_MODULE_NAME)
        if meta is None:
            failure = self.registry.failures.get(_BASE_MODULE_NAME)
            raise RuntimeError("required tool module meta is unavailable" + (f"\n{failure}" if failure else ""))
        self._announcements: list[str] = []
        self.ui_mode = bool(ui_mode)
        self._activate(meta)
        add_hint = getattr(session, "add_hint", None)
        if self.ui_mode:
            # UI 模式：整块状态挂末尾，前面那条 system 只留指路条。
            self.context_message["content"] = _UI_POINTER
            if callable(add_hint):
                add_hint(self._state_hint)
            return
        # 追加模式：基线在 bind 时写一次，之后这条消息不再变，变动走 _announce 追加。
        self.context_message["content"] = _render_context(self._catalog(), self.active)
        register = getattr(session, "add_context_provider", None)
        if callable(register):
            register(self._take_announcements)
        if callable(add_hint):
            add_hint(self._drift_hint)

    def restore(self, entries: Mapping[str, float] | Iterable[str]) -> list[str]:
        """Re-activate a window's persisted modules at bind time, without announcing.

        WHY: 激活是**窗口级**状态。每次变化都由 `_save_active` 交给调用方持久化（连续聊天
        写本窗口 storage），所以每个新 `Chat` 开局都要把这些模块装回来，装回本身就是这一
        层存在的理由，见 `chat._persist_modules`。

        WHY: 装回**本身**不发 `_announce`。此刻还没有任何模型请求，对模型来说什么都没
        "发生"，把已激活模块的正文直接渲染进基线那条目录消息就够了；而 `_announce` 比的是
        前后全量，开局时"前"只有 meta，于是每轮都会把这次装回的模块报成"已激活"并各附一份
        正文副本。`load` 仍然只用于"模型刚要求激活"，那里的通告才是它要的反馈。

        WHY: 分两种。**源码没了**的名字就地丢掉并回写，它指向的东西已经不存在。而
        `visible` 挡下的（op-only 模块落在非 op 的轮里）只是**这一轮**装不回来，窗口并没有
        停用它——把它记进 `_deferred`，storage 里的记录连同原来的使用时刻一起留着，下一次
        op 自己开的轮里照常装回。原先这两种一起删，于是管理员窗口里只要有普通成员接着说过
        一句话，管理员下一轮就得重新 `load_tools`；而通告里那句"在它自己的轮里会重新出现"
        也就成了假话。留着不会攒垃圾：时刻是原来那个，超过 `ttl` 照样被下面的空闲回收收走。

        WHY: 超过 `self.ttl` 没有被装入或调用过的也丢掉，这是"只进不出"的解药——不丢的话每次
        `load_tools` 都会永久留在这个窗口里，每轮都往基线消息里渲染一份正文。判据放在
        **开局**：轮中间把工具抽走会让模型手上的快照和它下一句要调的名字对不上，而开局
        收掉的模块，从这一轮的目录消息起就不在了。旧格式（只存名字的列表）由调用方补上
        "就是刚才用过"，见 `chat._active_modules`。

        WHY: 被丢掉的要发一条通告，装回的不发，见 `_queue_reclaimed`。
        """
        now = time.time()
        if isinstance(entries, Mapping):
            stamps = {
                name: float(stamp)
                for name, stamp in entries.items()
                if isinstance(name, str) and isinstance(stamp, (int, float))
            }
        else:
            stamps = {name: now for name in _requested_names(entries) if isinstance(name, str)}
        with self._lock:
            requested = [name for name in stamps if name and name != _BASE_MODULE_NAME]
            kept: list[str] = []
            reclaimed: list[tuple[str, str]] = []
            for name in requested:
                module = self.registry.get(name)
                stamp = stamps[name]
                if module is None:
                    reclaimed.append((name, "已不存在"))
                    continue
                # WHY: 空闲回收排在可见性前面。反过来的话，一个一直不可见的模块永远走不到
                # 这一步，`_deferred` 就会把它在 storage 里留成永久居民——而 ttl 正是那份
                # 记录唯一的回收者。
                # WHY: 一条判据，不分模块种类。曾经按"模块导出了函数没有"分过两档，让 `.md`
                # 技能和只给说明的 `.py` 不参与回收，出口交给一个 `unload_tools`——撤掉了。
                # 撤的理由是那扇门的成本不在 token 而在**注意力**：它没有触发时机，于是每轮
                # 都要分神判一次"这个还留着吗"，天天付；而它买到的只是躲开一次收回，罕见且
                # 便宜。收回本身是有界的：通告是追加的不会消失，目录里那一行每轮都在，重新
                # `load_tools` 就是一次往返。
                # WHY: 代价是这里**唯一**一处"明知可能还要用也照收"——一个 `.md` 技能在第二
                # 个小时仍被每轮阅读，也会在开局被收掉，因为阅读留不下痕迹。这不是没想到的
                # 副作用，是知情的取舍：函数模块误收会被下一次调用当场打回来，内容模块误收
                # 没有任何动作会撞上它，靠的是目录首行那个被动钩子，外加通告里那句说明（见
                # `_queue_reclaimed`，它带上首行描述正是为了补这一口）。哪天发现模型反复漏掉
                # 某个技能里写着的约束，回来看这一条。
                if (
                    self.ttl is not None
                    and self.ttl > 0
                    and now - stamp > self.ttl
                ):
                    _log.debug("reclaiming idle tool module from window: %s", name)
                    reclaimed.append((name, "空闲收回"))
                    continue
                if not self.visible(name, module):
                    self._deferred[name] = stamp
                    reclaimed.append((name, "本轮不可用"))
                    continue
                self._activate(module, touched_at=stamp)
                kept.append(name)
            if not self.ui_mode:
                # UI 模式的工具状态整块挂在末尾，每次子请求重算，这里不用碰。
                self.context_message["content"] = _render_context(self._catalog(), self.active)
            if kept != requested or self._dirty:
                self._save_active()
            if reclaimed:
                self._queue_reclaimed(reclaimed)
            return kept

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
                    if not self.visible(name, module):
                        # WHY: 目录里不列它，但模型可能记得名字直接 load。门控必须在**激活**
                        # 这一层再拦一次，否则目录只是"没提示"，不是"没能力"。理由见决定三。
                        raise PermissionError(f"tool module not available in this turn: {name}")
                    previous = self.active.get(name)
                    self._activate(module)
                    results[name] = {"action": "replaced" if previous is not None else "activated"}
                except Exception:
                    results[_result_name(requested_name)] = _failure(
                        "failed to activate tool module %r", requested_name
                    )
            self._announce(before)
            self._save_active()
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
            self._save_active()
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

    def _state_hint(self) -> str:
        """Render the whole tool state as one end-of-context block (UI mode).

        WHY: UI 模式的全部意义是注意力：工具只剩**一个**权威副本，而且它明确位于所有
        修改之后。就地改写头部做不到这一点（会丢掉"改过"这件事，还打断前缀缓存），
        追加式也做不到——上下文里同时留着某模块的旧正文和新正文，模型可能以为修改之前
        的工具就长那样。整块挂末尾则没有歧义：末尾这一份就是现在的样子。

        WHY: 代价是这一整块每次子请求都是未命中缓存的新 token，工具循环越长付得越多。
        所以它是**可切换**的而不是替换掉追加模式，默认仍走追加。开关见
        chat.get_tools_mode，按窗口存。

        WHY: 它连磁盘变化一起报，所以 UI 模式下不再单独挂 _drift_hint——那会是同一件事
        的第二个说法。仍然只报告不加载，理由和 _announce 那条完全相同。
        """
        try:
            changes = self.registry.scan()
        except Exception:
            _log.exception("failed to scan tool sources for the state hint")
            changes = {}
        modules = self._catalog()
        lines = ["当前工具状态（本块位于你所有修改之后，是唯一权威）：", "", "## 可用工具模块"]
        if modules:
            lines.extend(
                f"- {name}: {module.description}" + ("（已激活）" if name in self.active else "")
                for name, module in modules.items()
            )
        else:
            lines.append("- (无)")
        for name in sorted(self.active):
            content = self.active[name].content
            if content:
                lines.append(f"\n## 已激活模块 {name}\n{content}")
        drift = [
            f"- {label} {', '.join(changes[kind])}"
            for kind, label in (("added", "新增"), ("modified", "修改"), ("deleted", "删除"))
            if changes.get(kind)
        ]
        if drift:
            lines.append("\n## 磁盘源与已加载版本不一致（尚未应用）")
            lines.extend(drift)
            lines.append("需要时用 reload_tools 显式应用。")
        for name, error in sorted(self.registry.failures.items()):
            lines.append(f"\n## 加载失败 {name}\n{error}")
        return _framed("\n".join(lines))

    def list_text(self) -> str:
        """Describe last-good, active, failed, and changed modules."""
        modules = self._catalog()
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
        # WHY: 确切时限只写在这里，不写进 meta 的说明书。说明书是那个一旦加载失败就全盘瘫痪
        # 的文件，为一句话让它的顶层多一个 import 不划算；而这段本来就是算出来的，改常量就
        # 跟着变，不会像写死的数字那样漂。
        lines.append("空闲回收:")
        if self.ttl is not None and self.ttl > 0:
            lines.append(
                f"- 超过{_human_time(self.ttl)}没有被装入或调用过的模块，下一轮开局不再装回；"
                "装入本身算一次用过，没有导出函数的模块因此按装入时刻计时"
            )
        else:
            lines.append("- 关闭")
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

    def _catalog(self) -> dict[str, ToolModule]:
        """This turn's visible subset of last-good modules; recomputed at each render."""
        return _visible_catalog(self.registry, self.visible)

    def _activate(self, module: ToolModule, touched_at: float | None = None) -> None:
        """Install ``module``'s tools in this session, stamping when it was last used.

        WHY: `touched_at` 只有 `restore` 会传，而且必须传——它带的是磁盘上那次使用的
        时刻，拿“现在”顶替的话，每轮开局都会把一切刷成刚用过，空闲回收永远不触发。
        """
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
            self._owner.pop(name, None)
        functions.update(module.tools)
        for name in module.tools:
            self._owner[name] = module.name
        self.active[module.name] = module
        # 装上了就不再是"这一轮装不回来"的那种；两边同时挂着一个名字会让 _save_active
        # 有两个时刻可选。
        self._deferred.pop(module.name, None)
        self._touched[module.name] = time.time() if touched_at is None else touched_at
        self._dirty = True

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
            module.op_only,
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
            self._owner.pop(tool_name, None)
        del self.active[name]
        # 名字都没了，使用时刻留着只会让 _touched 无限长；下次 load 会重新盖上“现在”。
        self._touched.pop(name, None)

    def touch(self, tool_name: str) -> None:
        """Refresh the last-use stamp of the module that owns ``tool_name``.

        WHY: 空闲回收的判据是"用过没有"，而"用过"只有调用那一刻知道。不在这里等调用——
        工具是 `llm` 那层直接 `tool.call(**arguments)` 执行的，它不认识 binding，所以由
        调用方在工具结果回来时把名字递进来（`chat._oplog_recorder`，每个工具结果都经过
        它）。名字是模型面向的那个（`<模块名>__<函数名>`），归属查 `_owner`，不从名字反解；
        `meta` 不记，反正它每轮都在，记了只会在 `_touched` 里多一个谁也不看的条目。
        """
        owner = self._owner.get(tool_name)
        if owner is None or owner == _BASE_MODULE_NAME:
            return
        with self._lock:
            self._touched[owner] = time.time()
            self._dirty = True

    def _summary(self, name: str) -> str:
        """That module's first line, for a notice that says what was taken away.

        WHY: 通告只报名字是不够的。函数模块被误收会被下一次调用当场打回来，内容模块不会
        ——没有任何动作会撞上它，被拿走的恰恰是"这件事要注意什么"的那段文字，模型连自己
        少了什么都不知道。带上首行就把通告从"拿走了一个东西"变成"拿走的是干这个用的"，
        钩子从目录里那一行（被动、要自己去看）挪到通告里（就在眼前）。
        """
        module = self.registry.get(name)
        description = getattr(module, "description", "")
        return f" — {description}" if description else ""

    def _queue_reclaimed(self, reclaimed: list[tuple[str, str]]) -> None:
        """Queue one appended notice about modules this window lost with nobody talking.

        WHY: 收回必须让模型知道，理由和 `_announce` 那条一字不差：操作历史轨道里留着上一
        轮那几次 `load_tools`，模型据此以为模块还在；它照着那个印象调名，而这一轮的快照里
        没有这个名字，`llm` 解析时 `mapping[name]` 抛 KeyError，整个调用被丢掉，那一轮连
        一条 tool 结果都没有就结束了（2026-09-17 `browser__open_page` 那次）。收回发生在
        模型没说话的时候，所以这条通告是它**唯一**的信息来源：基线目录消息里少了一行，而
        模型不会把那行和"我上一轮明明装载过"对上。

        WHY: 不走 `_announce`。开局时"前"只有 meta，全量比较会把这次装回的模块全报成
        "已激活"并各附一份正文副本，每轮都来一遍。这里只报丢掉的那几个。

        WHY: UI 模式不发，和 `_announce` 同一条理由。整块状态挂在末尾、每次子请求重算，
        本来就是最新的，再追加一条"变了什么"就又是两个副本并存。
        """
        if self.ui_mode:
            return
        grouped: dict[str, list[str]] = {}
        for name, reason in reclaimed:
            grouped.setdefault(reason, []).append(name)
        lines = ["工具模块已变化（本条由系统追加，不是用户发言）："]
        for reason, names in grouped.items():
            for name in sorted(names):
                lines.append(f"- 已停用（{reason}）：{name}{self._summary(name)}")
        if grouped.get("空闲收回"):
            limit = _human_time(self.ttl) if self.ttl else ""
            lines.append(
                f"空闲收回只按时限判断：超过{limit}没有被装入或调用过的模块，新一轮开局就不再"
                "装回来（你上一轮装载过它，这一轮它不在了）。**阅读不留痕迹**，所以只靠读正文的"
                "模块也会按装入时刻到期——还要用就 `load_tools` 把它装回来，这是下一步，不是"
                "以后再说。"
            )
        if grouped.get("本轮不可用"):
            lines.append(
                "标记为「本轮不可用」的模块在它自己的轮里会重新出现，不用重复装载——窗口里的"
                "激活记录还留着，只是这一轮的发言者看不到它。"
            )
        if grouped.get("已不存在"):
            lines.append("标记为「已不存在」的模块源码已经没了，窗口里的记录也一并清掉了。")
        self._announcements.append(_framed("\n".join(lines)))

    def _save_active(self) -> None:
        """Hand this window's activation set, with use stamps, to the storage owner.

        WHY: 记的是**名**不是模块对象：storage 是 JSON，模块对象跨重启不存在，名才是
        下次开局能装回去的东西。`meta` 不记——它按定义每次都在，记了反而多一个"恢复
        一个必需模块失败"的失败面。值是该模块最后一次被调用的时刻，空闲回收要用，见
        `touch` 与 `restore`。

        WHY: 使用时刻在 `touch` 里只更新内存，不落盘——一次工具调用配一次 storage 写没有
        必要。落盘的机会是装载/重载之后，以及下一轮开局 `restore`（那里 `_dirty` 为真就
        写一次）。代价是进程正好在这中间重启会丢最后一轮的时刻，最坏让某个模块早一轮被
        收回，可以接受。

        WHY: 写回的是 `active` **加上** `_deferred`。后者这一轮没装、因此不在 `active` 里，
        但它仍然属于这个窗口；只写 `active` 就等于让一轮普通聊天把管理员的激活记录删掉。见
        `_deferred` 与 `restore`。
        """
        if self.persist is None:
            return
        now = time.time()
        self._touched = {name: stamp for name, stamp in self._touched.items() if name in self.active}
        stamps = {name: stamp for name, stamp in self._deferred.items() if name not in self.active}
        for name in self.active:
            stamps[name] = float(self._touched.get(name, now))
        self.persist({
            name: float(stamp)
            for name, stamp in sorted(stamps.items())
            if name != _BASE_MODULE_NAME
        })
        self._dirty = False

    def _capture(self) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        """Snapshot everything the model can currently see about tool modules."""
        return (
            {name: module.content for name, module in self.active.items()},
            {name: module.description for name, module in self._catalog().items()},
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
        会话。想让模型知道磁盘变了，用 list_tools 报告差异，或者末尾的 _drift_hint
        （UI 模式下是 _state_hint），都不是在这里加扫描。

        WHY: 通告是**累积**的，靠顺序而不是替换生效——上下文里会同时留着某模块的旧正文
        和后来追加的新正文，后者在后面。这是追加式的必然代价，deepseek-harness 的
        baseline+refresh 也是如此。换成回头改写旧消息就等于放弃前缀缓存，而那正是这套
        东西存在的理由。同一轮内的多次变动各自成条、按发生顺序交付，不互相覆盖。
        """
        if self.ui_mode:
            # UI 模式不产生通告：状态整块挂在末尾，每次子请求重新渲染，本来就是最新的。
            # 再追加一条"变了什么"就又回到了两个副本并存。
            return
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
        stopped = set(before_active) - set(after_active)
        lines = [
            joined("目录新增", set(after_catalog) - set(before_catalog)),
            joined("目录移除", set(before_catalog) - set(after_catalog)),
            joined("目录描述更新", {
                name for name in set(after_catalog) & set(before_catalog)
                if after_catalog[name] != before_catalog[name]
            }),
            joined("已激活", set(after_active) - set(before_active)),
            joined("已停用", stopped),
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
        if stopped:
            # 走到这里的停用只有一条路：reload_tools 发现源码没了。它紧跟着 _save_active，
            # 窗口记录确实一起没了。（开局的空闲回收不走这里，它有自己的通告，见
            # _queue_reclaimed——那条比的是前后全量，开局报出来的会是一堆"已激活"。）
            sections.append(
                "已停用的模块同时从本窗口的激活记录里清掉了，下一轮开局不会再装回来；"
                "还要用就重新 `load_tools`。"
            )
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
    initial_modules: Mapping[str, float] | Iterable[str] = (),
    *,
    registry: ToolRegistry | None = None,
    ui_mode: bool = False,
    visible: Callable[[str, ToolModule], bool] | None = None,
    persist: Callable[[Mapping[str, float]], None] | None = None,
    ttl: float | None = _IDLE_RECLAIM_SECONDS,
) -> SessionBinding:
    """Bind base tools, the window's persisted activation, and explicit modules.

    `initial_modules` 走 `restore`：静默装回，装不回来的、以及超过 `ttl` 没被调用过的都
    丢掉，都不发"已激活"通告（被丢掉的会收到一条收回通告）。旧格式的纯名字可迭代对象也
    收，一律当成"就是刚才用过"。`persist` 给了的话，此后每次激活集合变化都会回调一次，
    收到的是 `{模块名: 最后使用时刻}`。
    """
    binding = SessionBinding(
        session,
        context_message,
        registry=default_registry if registry is None else registry,
        ui_mode=ui_mode,
        visible=visible,
        persist=persist,
        ttl=ttl,
    )
    if initial_modules:
        binding.restore(initial_modules)
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
