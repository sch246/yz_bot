"""Registry and per-Chat binding for Python and Markdown tool modules."""

# WHY: 这一整套是从更早的 tool 系统迁移过来的，迁移由 GPT 执行，所以文件里有若干形状
# 并未经过维护者裁决（见下面几处指向本注释的标记）。维护者对这套东西的期望是：
#
# 1. tool 与 skill 本质二合一：Python 的模块 docstring 就相当于 Markdown 全文，首行始终
#    显示用于索引，激活后展开全部，展开后还能按需继续索引子文件夹内容。
#    _split_description、_render_context、_source_paths 合起来已经是这个形状。
# 2. 让模型能随时改自己的工具，并主动察觉到工具可更新；更新后立即可用，失败则拿到错误栈。
#    "更新后立即可用/拿到错误栈"由 reload_tools + registry._failures 覆盖；中心请求边界
#    对账后把变化写入经历流。"主动察觉磁盘变了"由末尾 _drift_hint 覆盖，只报告不加载。
# 3. meta.py 是这套东西的使用说明书，给模型看的。
#
# 因此判断这里的代码时，标准不是"它已经在这儿而且能跑"，而是 docs/design-principles.md
# 对任何抽象的那个提问：它被观察到解决了哪个问题。整体重写是被允许的。

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import hashlib
import inspect
import json
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
    """Return the binding while a tool of this session is executing.

    WHY: 每个模块的工具都被 `_bind_module` 包了一层，所以任何工具执行期间都拿得到自己所属的
    binding（`binding.session` 就是当前的 `llm.Chat`）；不在工具里调用就抛 RuntimeError。
    """
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
    # WHY: 模块自己声明"只有 Bot 获得 op 权限时可见"。哪些模块受限由模块作者声明，
    # Bot 的固定权限由配置声明，两者不能再借当前消息作者拼成一条按轮变化的规则。
    bot_op_only: bool = False


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
        # 也是这条。meta 还承载聊天、信源和记忆基础能力；若要求精确相等，新增任一基础函数
        # 都会让恢复入口所在模块整体加载失败。
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
            bool(getattr(candidate, "BOT_OP_ONLY", False)),
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


def bot_op_tool_visible(name: str, module: ToolModule) -> bool:
    """Whether Bot's fixed permission allows one module to be shown and loaded."""
    if not getattr(module, "bot_op_only", False):
        return True
    try:
        from mods import op
    except Exception:
        return False
    try:
        return bool(op.bot_is_op())
    except Exception:
        return False


def _visible_catalog(
    registry: ToolRegistry, visible: Callable[[str, ToolModule], bool] | None
) -> dict[str, ToolModule]:
    """The subset of last-good modules this turn is allowed to see.

    WHY: 权限过滤留在投影和加载处，不从进程级 registry 删除模块；配置改变需要重启，
    重启后的所有窗口应看到同一个 Bot 权限结果。
    """
    pick = bot_op_tool_visible if visible is None else visible
    return {
        name: module
        for name, module in registry.modules.items()
        if pick(name, module)
    }


def create_context_message(
    *,
    registry: ToolRegistry | None = None,
    visible: Callable[[str, ToolModule], bool] | None = None,
) -> dict[str, str]:
    """Create the baseline module catalog; later changes are appended, not rewritten."""
    selected = default_registry if registry is None else registry
    return {"role": "system", "content": _render_context(_visible_catalog(selected, visible), {})}


class SessionBinding:
    """Own one chat session's explicitly active tool-module projection."""

    def __init__(
        self,
        session,
        context_message: dict | None,
        *,
        registry: ToolRegistry,
        visible: Callable[[str, ToolModule], bool] | None = None,
        persist: Callable[[Mapping[str, float]], None] | None = None,
        ttl: float | None = _IDLE_RECLAIM_SECONDS,
        schema_modules: Iterable[str] | None = None,
        persist_schema: Callable[[list[str]], None] | None = None,
    ) -> None:
        if (schema_modules is None and
                (not isinstance(context_message, dict) or context_message.get("role") != "system")):
            raise TypeError("context_message must be an existing system message dict")
        if not isinstance(getattr(session, "functions", None), dict):
            raise TypeError("session.functions must be a dict")
        self.session = session
        self.context_message = context_message
        self.registry = registry
        self.visible = bot_op_tool_visible if visible is None else visible
        # 激活集合每变一次就回调一次，写到哪儿由调用方决定：连续聊天写本窗口 storage，
        # 子会话不传。见 restore 与 _save_active。
        self.persist = persist
        # 空闲多久就把模块收回去（秒）；None 或 <=0 表示不收，见 restore。
        self.ttl = ttl
        self.schema_modules = list(dict.fromkeys(schema_modules)) if schema_modules is not None else None
        self.persist_schema = persist_schema
        self.active: dict[str, ToolModule] = {}
        self._loaded: dict[str, ToolModule] = {}
        # 每个激活模块最后一次被调用的时刻，见 touch。先活在内存里，落盘由 _save_active 做。
        self._touched: dict[str, float] = {}
        # WHY: schema 名 -> 拥有它的模块名。它和 `session.functions` 是同一份事实的两面，
        # 所以在写 functions 的**同一处**（`_activate`/`_deactivate`）一起维护，别处不碰。
        # 有了它，`touch` 就不必从 `<模块名>__<函数名>` 里反解模块名——反解是猜：模块 `a`
        # 导出 `b__c` 与模块 `a__b` 导出 `c` 生成同一个 schema 名，靠前缀匹配必然有一种猜错。
        self._owner: dict[str, str] = {}
        # WHY: 因 Bot 固定权限不足而装不回来、但**不该从窗口里删掉**的模块（名 -> 原使用
        # 时刻）。权限配置下次启动可能改变，记录留下来后届时可恢复；原时刻仍参与 ttl 回收。
        self._deferred: dict[str, float] = {}
        # 有没有还没落盘的使用时刻：只是省掉“没有变化也写一次 storage”。
        self._dirty = False
        self._lock = threading.RLock()
        meta = self.registry.get(_BASE_MODULE_NAME)
        if meta is None:
            failure = self.registry.failures.get(_BASE_MODULE_NAME)
            raise RuntimeError("required tool module meta is unavailable" + (f"\n{failure}" if failure else ""))
        self._activate(meta)
        add_hint = getattr(session, "add_hint", None)
        if self.schema_modules is None:
            self.context_message["content"] = _render_context(self._catalog(), self.active)
            self._child_told = self.state_snapshot()
            session.add_context_provider(self._child_provider)
        if callable(add_hint):
            add_hint(self._drift_hint)

    def restore(self, entries: Mapping[str, float] | Iterable[str]) -> list[str]:
        """Re-activate a window's persisted modules at bind time, without announcing.

        WHY: 激活态由调用方持久化；装回本身不写经历，由中心请求边界统一对账。

        WHY: 分两种。**源码没了**的名字就地丢掉并回写，它指向的东西已经不存在。而
        `visible` 挡下的（Bot 没有模块所需权限）仍保留在 `_deferred`，storage 里的记录连同
        原使用时刻一起留下；以后修改权限配置并重启即可装回。留着不会攒垃圾：时刻不刷新，
        超过 `ttl` 照样被下面的空闲回收收走。

        WHY: 超过 `self.ttl` 没有被装入或调用过的也丢掉，这是"只进不出"的解药——不丢的话每次
        `load_tools` 都会永久保持激活。判据放在
        **开局**：轮中间把工具抽走会让模型手上的快照和它下一句要调的名字对不上，而开局
        收掉的模块在下一请求边界作为差异告知。旧格式（只存名字的列表）由调用方补上
        "就是刚才用过"，见 `chat._active_modules`。

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
            for name in requested:
                module = self.registry.get(name)
                stamp = stamps[name]
                if module is None:
                    continue
                # WHY: 空闲回收排在可见性前面。反过来的话，一个一直不可见的模块永远走不到
                # 这一步，`_deferred` 就会把它在 storage 里留成永久居民——而 ttl 正是那份
                # 记录唯一的回收者。
                # WHY: 一条判据，不分模块种类。曾经按"模块导出了函数没有"分过两档，让 `.md`
                # 技能和只给说明的 `.py` 不参与回收，出口交给一个 `unload_tools`——撤掉了。
                # 撤的理由是那扇门的成本不在 token 而在**注意力**：它没有触发时机，于是每轮
                # 都要分神判一次"这个还留着吗"，天天付；而它买到的只是躲开一次收回，罕见且
                # 便宜。收回后可用 `load_tools` 再装入。
                # WHY: 代价是这里**唯一**一处"明知可能还要用也照收"——一个 `.md` 技能在第二
                # 个小时仍被每轮阅读，也会在开局被收掉，因为阅读留不下痕迹。这不是没想到的
                # 副作用，是知情的取舍：函数模块误收会被下一次调用当场打回来，内容模块误收
                # 没有任何动作会撞上它，只能靠目录与状态变化提示重新装入。
                if (
                    self.ttl is not None
                    and self.ttl > 0
                    and now - stamp > self.ttl
                ):
                    _log.debug("reclaiming idle tool module from window: %s", name)
                    continue
                if not self.visible(name, module):
                    self._deferred[name] = stamp
                    continue
                self._activate(module, touched_at=stamp)
                kept.append(name)
            if self.schema_modules is None:
                self.context_message["content"] = _render_context(self._catalog(), self.active)
                self._child_told = self.state_snapshot()
            if kept != requested or self._dirty:
                self._save_active()
            return kept

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
            for requested_name in _requested_names(names):
                try:
                    name = _validate_module_name(requested_name)
                    candidate = self.registry.prepare(name)
                    if candidate is not None and self.schema_modules is not None and name in self.schema_modules:
                        conflicts = [tool_name for tool_name in candidate.tools
                                     if tool_name in self._owner and self._owner[tool_name] != name]
                        if conflicts:
                            raise KeyError("session tool names already exist: " + ", ".join(conflicts))
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
            if self.schema_modules is not None:
                self.restore_schema()
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

        WHY: 它只报告，不加载。发现变化与决定应用是两步——磁盘
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
            + "\n需要时用 reload_tools 显式应用；不应用则当前生效的仍是目录中的版本。"
        )

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
        lines.append("完整当前工具状态:\n" + self.state_text())
        return "\n".join(lines)

    def _catalog(self) -> dict[str, ToolModule]:
        """This turn's visible subset of last-good modules; recomputed at each render."""
        return _visible_catalog(self.registry, self.visible)

    def _activate(self, module: ToolModule, touched_at: float | None = None) -> None:
        """Install ``module``'s tools in this session, stamping when it was last used.

        WHY: `touched_at` 只有 `restore` 会传，而且必须传——它带的是磁盘上那次使用的
        时刻，拿“现在”顶替的话，每轮开局都会把一切刷成刚用过，空闲回收永远不触发。
        """
        original = module
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
            and (self.schema_modules is None or self._owner.get(name) != module.name)
        ]
        if conflicts:
            raise KeyError("session tool names already exist: " + ", ".join(conflicts))

        for name in previous_tools:
            if name not in module.tools:
                functions.pop(name)
                self._owner.pop(name, None)
        functions.update(module.tools)
        for name in module.tools:
            self._owner[name] = module.name
        self.active[module.name] = module
        self._loaded[module.name] = original
        # 装上了就不再是"这一轮装不回来"的那种；两边同时挂着一个名字会让 _save_active
        # 有两个时刻可选。
        self._deferred.pop(module.name, None)
        self._touched[module.name] = time.time() if touched_at is None else touched_at
        self._dirty = True
        if self.schema_modules is not None and module.name != _BASE_MODULE_NAME and module.name not in self.schema_modules:
            self.schema_modules.append(module.name)
            if self.persist_schema is not None:
                self.persist_schema(self.schema_modules)

    def _bind_module(self, module: ToolModule) -> ToolModule:
        """把模块的每个工具包一层"当前 binding"上下文，再装进会话。

        WHY: 工具执行期间要能问到 `current_binding()`——以前只有 meta 的工具包了这一层，别的
        模块想借会话做点事（例如把图片附加进下一次请求，见 mods/tools/_vision.py）只能绕路：
        工具是 `llm` 那层直接 `tool.call(**arguments)` 执行的，它不认识 binding。代价只是一次
        ContextVar 的 set/reset，"哪些模块能用"这种区别没有第二个地方需要，所以统一包。
        """
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
            module.bot_op_only,
        )

    def _deactivate(self, name: str) -> None:
        previous = self.active.get(name)
        if previous is None:
            return
        functions = self.session.functions
        for tool_name, old_tool in previous.tools.items():
            if functions.get(tool_name) is not old_tool:
                raise KeyError(f"active tool ownership changed: {tool_name}")
        if self.schema_modules is None:
            for tool_name in previous.tools:
                functions.pop(tool_name)
                self._owner.pop(tool_name, None)
        else:
            for tool_name, old_tool in previous.tools.items():
                functions[tool_name] = self._unloaded_tool(old_tool, name)
        del self.active[name]
        self._loaded.pop(name, None)
        # 名字都没了，使用时刻留着只会让 _touched 无限长；下次 load 会重新盖上“现在”。
        self._touched.pop(name, None)

    def touch(self, tool_name: str) -> None:
        """Refresh the last-use stamp of the module that owns ``tool_name``.

        WHY: 空闲回收的判据是"用过没有"，而"用过"只有调用那一刻知道。不在这里等调用——
        工具是 `llm` 那层直接 `tool.call(**arguments)` 执行的，它不认识 binding，所以由
        调用方在工具结果回来时把名字递进来（`chat._stream_results`，每个工具结果都经过
        它）。名字是模型面向的那个（`<模块名>__<函数名>`），归属查 `_owner`，不从名字反解；
        `meta` 不记，反正它每轮都在，记了只会在 `_touched` 里多一个谁也不看的条目。
        """
        owner = self._owner.get(tool_name)
        if owner is None or owner == _BASE_MODULE_NAME or owner not in self.active:
            return
        with self._lock:
            self._touched[owner] = time.time()
            self._dirty = True
            self._save_active()

    def restore_schema(self) -> None:
        """Restore the stable module order and guarded definitions from current code."""
        if self.schema_modules is None:
            return
        catalog = self._catalog()
        kept = [name for name in self.schema_modules
                if name != _BASE_MODULE_NAME and name in catalog]
        for name in self.active:
            if name != _BASE_MODULE_NAME and name not in kept:
                kept.append(name)
        if kept != self.schema_modules:
            self.schema_modules = kept
            if self.persist_schema is not None:
                self.persist_schema(kept)
        functions = dict(self.active[_BASE_MODULE_NAME].tools)
        owners = {name: _BASE_MODULE_NAME for name in functions}
        for name in kept:
            module = self.active.get(name) or self._bind_module(catalog[name])
            for tool_name, tool in module.tools.items():
                if tool_name in functions:
                    raise KeyError(f"session tool names already exist: {tool_name}")
                functions[tool_name] = (tool if name in self.active else
                                        self._unloaded_tool(tool, name))
                owners[tool_name] = name
        self.session.functions.clear()
        self.session.functions.update(functions)
        self._owner = owners

    def sync_registry(self) -> None:
        """Make this request's callables match shared last-good before announcing."""
        if self.schema_modules is None:
            return
        changed = False
        with self._lock, self.registry.lock:
            catalog = self._catalog()
            for name in tuple(self.active):
                current = catalog.get(name)
                if current is None:
                    if self.registry.get(name) is not None and name != _BASE_MODULE_NAME:
                        self._deferred[name] = self._touched[name]
                    self._deactivate(name)
                    changed = True
                elif current is not self._loaded[name]:
                    self._activate(current, touched_at=self._touched[name])
                    changed = True
            self.restore_schema()
            if changed:
                self._save_active()

    @staticmethod
    def _unloaded_tool(original: Tool, module_name: str) -> Tool:
        name = original.description["function"]["name"]
        guarded = Tool(lambda **kwargs: f"{module_name} 已卸载；请先调用 load_tools。", name)
        guarded.description = original.description
        return guarded

    def state_snapshot(self) -> dict:
        """Compact durable description of what was told, never a saved schema copy."""
        catalog = self._catalog()
        schemas = {}
        for name in [_BASE_MODULE_NAME, *(self.schema_modules or [
                name for name in self.active if name != _BASE_MODULE_NAME])]:
            definitions = [tool.description for tool_name, tool in self.session.functions.items()
                           if self._owner.get(tool_name) == name]
            if definitions and (name in catalog or name in self.active):
                schemas[name] = hashlib.sha256(json.dumps(definitions, ensure_ascii=False,
                                            sort_keys=True).encode()).hexdigest()
        return {
            "catalog": {name: module.description for name, module in catalog.items()},
            "active": {name: hashlib.sha256(module.content.encode()).hexdigest()
                       for name, module in self.active.items()},
            "schemas": schemas,
        }

    def state_text(self, previous: dict | None = None) -> str:
        """Render a full state or only changes since the last durable announcement."""
        current = self.state_snapshot()
        if previous is None:
            return _framed(_render_context(self._catalog(), self.active))
        lines = ["工具模块状态变化（本条由系统追加，不是用户发言）："]
        labels = {"catalog": "目录", "active": "已激活模块", "schemas": "函数定义"}
        for section, label in labels.items():
            before = previous.get(section, {})
            after = current[section]
            if not isinstance(before, dict):
                before = {}
            for name in sorted(set(before) | set(after)):
                if before.get(name) == after.get(name):
                    continue
                if name not in after:
                    suffix = "已卸载；不要再调用它的函数，需要时先 load_tools" if section == "active" else "已移除"
                    lines.append(f"- {label} {name}：{suffix}")
                elif name not in before:
                    lines.append(f"- {label} {name}：新增")
                else:
                    lines.append(f"- {label} {name}：已更新")
                if section == "catalog" and name in after:
                    lines.append(f"  {after[name]}")
                if section == "active" and name in after and self.active[name].content:
                    lines.append(f"\n## 已激活模块 {name}\n{self.active[name].content}")
        return _framed("\n".join(lines)) if len(lines) > 1 else ""

    def _child_provider(self) -> list[dict]:
        current = self.state_snapshot()
        if current == self._child_told:
            return []
        content = self.state_text(self._child_told)
        self._child_told = current
        return [{"role": "user", "content": content}] if content else []

    def _save_active(self) -> None:
        """Hand this window's activation set, with use stamps, to the storage owner.

        WHY: 记的是**名**不是模块对象：storage 是 JSON，模块对象跨重启不存在，名才是
        下次开局能装回去的东西。`meta` 不记——它按定义每次都在，记了反而多一个"恢复
        一个必需模块失败"的失败面。值是该模块最后一次被调用的时刻，空闲回收要用，见
        `touch` 与 `restore`。

        WHY: `touch` 每次调用后持久写回；否则一次调用结束后重启会丢失续期时刻。

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

default_registry = ToolRegistry()


def bind_session(
    session,
    context_message: dict | None,
    initial_modules: Mapping[str, float] | Iterable[str] = (),
    *,
    registry: ToolRegistry | None = None,
    visible: Callable[[str, ToolModule], bool] | None = None,
    persist: Callable[[Mapping[str, float]], None] | None = None,
    ttl: float | None = _IDLE_RECLAIM_SECONDS,
    schema_modules: Iterable[str] | None = None,
    persist_schema: Callable[[list[str]], None] | None = None,
) -> SessionBinding:
    """Bind base tools, the window's persisted activation, and explicit modules.

    `initial_modules` 走 `restore`：静默装回，装不回来的、以及超过 `ttl` 没被调用过的都
    丢掉；中心会话的变化由请求边界对账，子会话只在开头显示状态。旧格式的纯名字可迭代对象也
    收，一律当成"就是刚才用过"。`persist` 给了的话，此后每次激活集合变化都会回调一次，
    收到的是 `{模块名: 最后使用时刻}`。
    """
    binding = SessionBinding(
        session,
        context_message,
        registry=default_registry if registry is None else registry,
        visible=visible,
        persist=persist,
        ttl=ttl,
        schema_modules=schema_modules,
        persist_schema=persist_schema,
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
