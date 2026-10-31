"""Shared last-good Python/Markdown tools and ordinary per-run binding dictionaries.

Callers own durable active stamps and schema order: pass persist for Bot-global
activation and persist_schema for the current view. Request boundaries call
reconcile, state_text and state_snapshot; only the caller records announcements.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar, copy_context
from functools import wraps
import hashlib
from importlib.machinery import FileFinder, SOURCE_SUFFIXES, SourceFileLoader
import inspect
import json
import logging
from pathlib import Path
import sys
import threading
import time
import traceback as traceback_module
from types import ModuleType
from typing import get_type_hints

from mods.llm.tools import Tool


_log = logging.getLogger(__name__)
_SOURCE_SUFFIXES = frozenset({".py", ".md"})
_BASE_MODULE_NAME = "meta"
# WHY: 这四个是动态工具损坏后的恢复入口；meta 可以有更多导出，不能少了其中一个。
_BASE_TOOL_NAMES = ("exec_code", "list_tools", "reload_tools", "load_tools")
_IDLE_RECLAIM_SECONDS = 3600.0
_source_dir = Path(__file__).parent
_modules: dict[str, dict] = {}
_failures: dict[str, str] = {}
_initialized = False
_catalog_lock = threading.RLock()
_IMPORT_PACKAGE = __name__ + "._sources"
_current_binding_var: ContextVar[dict | None] = ContextVar("tool_session_binding", default=None)


def current_binding() -> dict:
    """Return this tool call's binding, including its session and run-local state."""
    binding = _current_binding_var.get()
    if binding is None:
        raise RuntimeError("tool called outside a bound Chat session")
    return binding


def initialize(source_dir: str | Path | None = None) -> None:
    """Load the one process catalog once; an explicit directory is for isolated runs."""
    global _initialized, _source_dir
    with _catalog_lock:
        if source_dir is not None:
            selected = Path(source_dir)
            if _initialized and selected != _source_dir:
                raise RuntimeError("tool catalog is already initialized from another directory")
            _source_dir = selected
        if _initialized:
            return
        for name, paths in _source_paths().items():
            try:
                _modules[name] = _load_candidate(name, paths)
            except Exception:
                error = traceback_module.format_exc()
                _failures[name] = error
                _log.error("failed to initialize tool module %r\n%s", name, error)
        _initialized = True


def catalog() -> dict[str, dict]:
    """Return the sorted last-good catalog without implicitly reloading changes."""
    initialize()
    with _catalog_lock:
        return dict(sorted(_modules.items()))


def failures() -> dict[str, str]:
    """Return full tracebacks of the most recent failed candidates."""
    initialize()
    with _catalog_lock:
        return dict(sorted(_failures.items()))


def scan() -> dict[str, list[str]]:
    """Report source changes; deciding to apply them belongs to reload."""
    initialize()
    with _catalog_lock:
        paths = _source_paths()
        modified = []
        for name in sorted(paths.keys() & _modules.keys()):
            module = _modules[name]
            candidates = paths[name]
            try:
                unchanged = (len(candidates) == 1
                             and candidates[0].suffix.lower() == module["source_suffix"]
                             and candidates[0].read_bytes() == module["source"])
            except Exception:
                unchanged = False
            if not unchanged:
                modified.append(name)
        return {"added": sorted(paths.keys() - _modules.keys()),
                "modified": modified, "deleted": sorted(_modules.keys() - paths.keys())}


def _source_paths() -> dict[str, list[Path]]:
    # WHY: 只列顶层使目录常驻、正文按需展开，子文件夹由正文指路或普通 import 取用。
    # 递归扫描会让下一层在开局全部占位，破坏 tool 与 skill 共用的首行/全文层次。
    if not _source_dir.is_dir():
        return {}
    grouped: dict[str, list[Path]] = {}
    for path in _source_dir.iterdir():
        if path.is_file() and not path.name.startswith("_") and path.suffix.lower() in _SOURCE_SUFFIXES:
            grouped.setdefault(path.stem, []).append(path)
    return {name: sorted(paths) for name, paths in sorted(grouped.items())}


def _load_candidate(name: str, paths: list[Path]) -> dict:
    _validate_module_name(name)
    if len(paths) != 1:
        raise RuntimeError(f"tool module has conflicting sources for {name}: "
                           + ", ".join(path.name for path in paths))
    path = paths[0]
    source = path.read_bytes()
    if path.suffix.lower() == ".md":
        if name == _BASE_MODULE_NAME:
            raise ValueError("meta must be Python and export the recovery tools")
        description, content = _split_description(source.decode("utf-8"), path)
        tools, bot_op_only = {}, False
    else:
        candidate = _load_python(name, path, source)
        description, content = _split_description(candidate.__doc__, path)
        exports = _explicit_exports(candidate, path)
        if name == _BASE_MODULE_NAME:
            absent = [tool_name for tool_name in _BASE_TOOL_NAMES if tool_name not in exports]
            if absent:
                raise ValueError("meta.py.__all__ must contain: " + ", ".join(absent))
        tools = {export if name == _BASE_MODULE_NAME else f"{name}__{export}":
                 _validated_tool(function, export if name == _BASE_MODULE_NAME else f"{name}__{export}")
                 for export, function in exports.items()}
        bot_op_only = bool(getattr(candidate, "BOT_OP_ONLY", False))
    return {"name": name, "description": description, "content": content,
            "tools": tools, "source_suffix": path.suffix.lower(), "source": source,
            "bot_op_only": bot_op_only}


def _load_python(name: str, path: Path, source: bytes) -> ModuleType:
    package = sys.modules.get(_IMPORT_PACKAGE)
    if package is None:
        package = ModuleType(_IMPORT_PACKAGE)
        package.__package__ = _IMPORT_PACKAGE
        package.__path__ = [str(_source_dir)]
        sys.modules[_IMPORT_PACKAGE] = package
    candidate_name = f"{_IMPORT_PACKAGE}._candidate_{name}"
    candidate = ModuleType(candidate_name)
    candidate.__dict__.update({"__file__": str(path), "__package__": _IMPORT_PACKAGE,
                               "__builtins__": __builtins__})
    # WHY: 相对 import 既查 sys.modules，也查包对象上的子模块属性。两处都要清掉，
    # from . import _helper 才会跟 from ._helper import value 一样重新读盘。结束时两处
    # 都还原，失败候选不能留下半成品，也不能改变旧工具持有的 helper。目录只有一个，
    # 不再用对象地址构造包名。
    prefix = _IMPORT_PACKAGE + "."
    previous = {key: value for key, value in sys.modules.copy().items() if key.startswith(prefix)}
    previous_attributes = {
        key: value for key, value in vars(package).copy().items()
        if isinstance(value, ModuleType) and value.__name__.startswith(prefix)
    }
    for key in previous:
        sys.modules.pop(key, None)
    for key in previous_attributes:
        package.__dict__.pop(key, None)
    sys.modules[candidate_name] = candidate
    import_path = str(_source_dir)
    had_finder = import_path in sys.path_importer_cache
    previous_finder = sys.path_importer_cache.get(import_path)
    # WHY: 仅清 sys.modules 仍会读到同秒、同长度编辑留下的旧 pyc。候选的同目录 helper
    # 必须直接编译源码，显式 reload 才不会成功回执却继续执行旧值。只替换该目录的 finder。
    sys.path_importer_cache[import_path] = FileFinder(
        import_path, (_fresh_source_loader, SOURCE_SUFFIXES))
    try:
        # WHY: 执行顶层代码就是既有宿主信任边界，不能用静态校验伪装成沙箱；工具手册要求
        # 顶层只有 import、常量和定义。reload 与 .py、exec_code、宿主操作属于同一信任域。
        exec(compile(source, str(path), "exec"), candidate.__dict__)
    finally:
        for key in tuple(sys.modules):
            if key.startswith(prefix):
                sys.modules.pop(key, None)
        sys.modules.update(previous)
        for key, value in vars(package).copy().items():
            if isinstance(value, ModuleType) and value.__name__.startswith(prefix):
                package.__dict__.pop(key, None)
        package.__dict__.update(previous_attributes)
        if had_finder:
            sys.path_importer_cache[import_path] = previous_finder
        else:
            sys.path_importer_cache.pop(import_path, None)
    return candidate


def _fresh_source_loader(fullname: str, path: str):
    loader = SourceFileLoader(fullname, path)
    loader.get_code = lambda name: compile(Path(path).read_bytes(), path, "exec")
    return loader


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

    return Tool(function, schema_name)


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
    catalog: Mapping[str, dict],
    active: Mapping[str, dict],
) -> str:
    lines = ["## 可用工具模块"]
    if catalog:
        lines.extend(f"- {name}: {module['description']}" for name, module in catalog.items())
    else:
        lines.append("- (无)")
    result = "\n".join(lines)
    for name in sorted(active):
        content = active[name]["content"]
        if content:
            result += f"\n\n## 已激活模块 {name}\n{content}"
    return result


def bot_op_tool_visible(name: str, module: dict) -> bool:
    """Use Bot's fixed permission, independent of who caused this run."""
    # WHY: 模块声明是否只给 op Bot；不能把混合上下文里当前作者变成逐轮权限来源。
    if not module["bot_op_only"]:
        return True
    try:
        from mods import op

        return bool(op.bot_is_op())
    except Exception:
        return False


def _catalog(binding: dict) -> dict[str, dict]:
    # WHY: 过滤发生在投影和加载处，共享 last-good 不因一次运行不可见而被删除。
    return {name: module for name, module in catalog().items() if binding["visible"](name, module)}


def _function_visible(binding: dict, name: str, tool: Tool) -> bool:
    leaf = name.rsplit("__", 1)[-1]
    granted = name in binding["grant"] or leaf in binding["grant"]
    # WHY: 这里只限制 fork 的默认暴露。交接/反思不封嘴是维护者已裁定的行为，
    # 不能把角色名顺手扩成禁发言规则（见 meta 的 _box_window/send WHY）。
    if binding["kind"] == "fork" and leaf in {"send", "type_word"} and not granted:
        return False
    pick = binding["function_visible"]
    return pick is None or bool(pick(name, tool))


def _bind_module(binding: dict, module: dict) -> dict:
    tools = {}
    for name, original in module["tools"].items():
        if not _function_visible(binding, name, original):
            continue
        bound = Tool(original.call, name)
        default = getattr(original.call, "back", None)
        parameters = bound.description["function"]["parameters"]["properties"]
        # WHY: 函数属性 back 才增加这一参数；显式 back 由函数自己处理（如 exec_code）。
        if default is not None and "back" not in parameters:
            parameters["back"] = {
                "type": "string", "enum": ["wait", "wake", "quiet"],
                "description": f"结果怎么回来：wait 等做完；wake 后台做完叫醒；quiet 后台只记下。不写是 {default}",
            }
        else:
            default = None

        @wraps(original.call)
        def bound_call(*args, __call=original.call, __name=name, __default=default, **kwargs):
            mode = (kwargs.pop("back", None) or __default) if __default is not None else "wait"
            token = _current_binding_var.set(binding)
            try:
                if mode == "wait":
                    return __call(*args, **kwargs)
                from mods.tools import _tasks

                if mode not in _tasks.MODES:
                    return f"back 只能是 {'、'.join(_tasks.MODES)}"
                # WHY: 每个模块的每个函数都设绑定；拷贝在 set 之后，后台线程也能附图和取会话。
                return _tasks.submit(__name, copy_context(), lambda: __call(*args, **kwargs), mode)
            finally:
                _current_binding_var.reset(token)

        bound.call = bound_call
        tools[name] = bound
    return {**module, "tools": tools}


def _unloaded_tool(original: Tool, module_name: str) -> Tool:
    name = original.description["function"]["name"]
    guarded = Tool(lambda **kwargs: f"{module_name} 已卸载；请先调用 load_tools。", name)
    guarded.description = original.description
    return guarded


def _project(binding: dict, modules: Mapping[str, dict]) -> tuple[dict, dict, dict, list[str]]:
    """Build and validate the entire projection before changing a binding or catalog."""
    visible = {name: module for name, module in modules.items() if binding["visible"](name, module)}
    meta = modules.get(_BASE_MODULE_NAME)
    if meta is None:
        failure = failures().get(_BASE_MODULE_NAME, "")
        raise RuntimeError("required tool module meta is unavailable" + (f"\n{failure}" if failure else ""))
    wanted = {_BASE_MODULE_NAME: meta}
    wanted.update({name: module for name, module in visible.items() if name in binding["stamps"]})
    active = {name: _bind_module(binding, module) for name, module in wanted.items()}
    order = list(dict.fromkeys(name for name in binding["schema_modules"]
                               if name != _BASE_MODULE_NAME and name in visible))
    order.extend(name for name in active if name != _BASE_MODULE_NAME and name not in order)
    functions = {name: tool for name, tool in binding["session"].functions.items()
                 if name not in binding["owners"]}
    owners = {}
    for name in [_BASE_MODULE_NAME, *order]:
        module = active.get(name)
        if module is None:
            module = _bind_module(binding, visible[name])
        for tool_name, tool in module["tools"].items():
            if tool_name in functions:
                raise KeyError(f"session tool names already exist: {tool_name}")
            functions[tool_name] = tool if name in active else _unloaded_tool(tool, name)
            # WHY: 不从 schema 名猜模块；a 导出 b__c 与 a__b 导出 c 会撞名，归属必须在安装处写。
            owners[tool_name] = name
    return active, functions, owners, order


def _apply(binding: dict, projection: tuple) -> None:
    active, functions, owners, order = projection
    changed_order = binding["schema_modules"] != order
    # The view writer can fail. Call it before installing candidate callables so
    # an unsuccessful reload cannot leave a new active version with old last-good.
    if changed_order and binding["persist_schema"] is not None:
        binding["persist_schema"](list(order))
    binding["session"].functions.clear()
    binding["session"].functions.update(functions)
    binding["active"] = active
    binding["owners"] = owners
    if changed_order:
        binding["schema_modules"][:] = order


def _save_active(binding: dict) -> None:
    # WHY: 名和最后使用时刻跨重启存在；模块对象不行。meta 必需常在，不保存它。
    # stamps 是激活意图的唯一集合，权限暂不可见的同样保留原时刻，由开局 TTL 统一回收。
    # touch 每次结果后写回，否则调用结束后重启会丢续期。
    if binding["persist"] is not None:
        binding["persist"](dict(sorted(binding["stamps"].items())))


def bind_session(
    session,
    context_message: dict | None = None,
    initial_modules: Mapping[str, float] | Iterable[str] = (),
    *,
    visible: Callable[[str, dict], bool] | None = None,
    persist: Callable[[Mapping[str, float]], None] | None = None,
    ttl: float | None = _IDLE_RECLAIM_SECONDS,
    schema_modules: Iterable[str] = (),
    persist_schema: Callable[[list[str]], None] | None = None,
    kind: str = "fork",
    function_visible: Callable[[str, Tool], bool] | None = None,
    grant: Iterable[str] = (),
) -> dict:
    """Bind one run; main persists global stamps, while schema order belongs to its view.

    A fork gets independent stamps, schema order and grants, and excludes send and
    type_word unless explicitly granted. function_visible applies after that rule
    on every load/reload/reconcile. It controls exposure, not the host trust domain.
    context_message, if supplied, is filled once; callers record later state
    changes explicitly. No context provider or session pocket is installed.
    """
    if not isinstance(getattr(session, "functions", None), dict):
        raise TypeError("session.functions must be a dict")
    if context_message is not None and (not isinstance(context_message, dict)
                                        or context_message.get("role") != "system"):
        raise TypeError("context_message must be an existing system message dict")
    binding = {
        "session": session, "visible": bot_op_tool_visible if visible is None else visible,
        "persist": persist, "ttl": ttl, "schema_modules": list(dict.fromkeys(schema_modules)),
        "persist_schema": persist_schema, "kind": kind, "function_visible": function_visible,
        "grant": frozenset(_requested_names(grant)), "active": {}, "stamps": {}, "owners": {},
        "lock": threading.RLock(),
    }
    with _catalog_lock:
        _apply(binding, _project(binding, catalog()))
    if initial_modules:
        restore(binding, initial_modules)
    if context_message is not None:
        context_message["content"] = _render_context(_catalog(binding), binding["active"])
    add_hint = getattr(session, "add_hint", None)
    if callable(add_hint):
        add_hint(lambda: drift_hint(binding))
    return binding


def restore(binding: dict, entries: Mapping[str, float] | Iterable[str]) -> list[str]:
    """Restore activation stamps at run opening and reclaim idle or missing modules."""
    now = time.time()
    stamps = ({name: float(stamp) for name, stamp in entries.items()
               if isinstance(name, str) and isinstance(stamp, (int, float))}
              if isinstance(entries, Mapping) else
              {name: now for name in _requested_names(entries) if isinstance(name, str)})
    with binding["lock"], _catalog_lock:
        modules = catalog()
        ttl = binding["ttl"]
        # WHY: 原时刻不能换成现在，否则每次开局都续期，TTL 永远不触发。回收只在开局，
        # 轮中抽走工具会让模型手上的快照对不上。先查 TTL 再查权限，不可见项也得能回收。
        # Markdown 和无函数 Python 也用同一判据，阅读不续期是接受的代价；收回可再 load。
        kept = {name: stamp for name, stamp in stamps.items()
                if name != _BASE_MODULE_NAME and name in modules
                and not (ttl is not None and ttl > 0 and now - stamp > ttl)}
        previous = binding["stamps"]
        binding["stamps"] = {**previous, **kept}
        try:
            projection = _project(binding, modules)
            _apply(binding, projection)
        except Exception:
            binding["stamps"] = previous
            raise
        _save_active(binding)
        return [name for name in kept if name in binding["active"]]


def load(binding: dict, names: str | Iterable[str]) -> dict[str, dict]:
    """Activate last-good modules only, without reading their disk source."""
    results = {}
    with binding["lock"], _catalog_lock:
        for requested in _requested_names(names):
            previous = dict(binding["stamps"])
            try:
                name = _validate_module_name(requested)
                module = catalog().get(name)
                if module is None:
                    raise KeyError(f"no last-good tool module: {name}")
                if not binding["visible"](name, module):
                    # WHY: 名字可能从旧上下文记得，激活处必须再拦，目录隐藏本身不限制能力。
                    raise PermissionError(f"tool module not available in this run: {name}")
                replaced = name in binding["active"]
                if name != _BASE_MODULE_NAME:
                    binding["stamps"][name] = time.time()
                projection = _project(binding, catalog())
                _apply(binding, projection)
                results[name] = {"action": "replaced" if replaced else "activated"}
            except Exception:
                binding["stamps"] = previous
                results[_result_name(requested)] = _failure("failed to activate tool module %r", requested)
        _save_active(binding)
    return results


def unload(binding: dict, names: str | Iterable[str]) -> dict[str, dict]:
    """Remove activation while retaining the view's schema slots as guarded stubs."""
    results = {}
    with binding["lock"], _catalog_lock:
        for requested in _requested_names(names):
            previous = dict(binding["stamps"])
            try:
                name = _validate_module_name(requested)
                if name == _BASE_MODULE_NAME:
                    raise ValueError("cannot unload required tool module meta")
                binding["stamps"].pop(name, None)
                projection = _project(binding, catalog())
                _apply(binding, projection)
                results[name] = {"action": "unloaded"}
            except Exception:
                binding["stamps"] = previous
                results[_result_name(requested)] = _failure("failed to unload tool module %r", requested)
        _save_active(binding)
    return results


def reload(binding: dict, names: str | Iterable[str]) -> dict[str, dict]:
    """Validate each disk candidate and replace last-good only after projection succeeds."""
    results = {}
    with binding["lock"], _catalog_lock:
        modules = catalog()
        for requested in _requested_names(names):
            try:
                name = _validate_module_name(requested)
                paths = _source_paths().get(name, [])
                if paths:
                    candidate = _load_candidate(name, paths)
                else:
                    if name == _BASE_MODULE_NAME or name not in modules:
                        raise FileNotFoundError(f"tool module source does not exist: {name}")
                    candidate = None
                updated = dict(modules)
                if candidate is None:
                    del updated[name]
                else:
                    updated[name] = candidate
                # Schema collisions and the same per-run filter are checked before last-good changes.
                projection = _project(binding, updated)
                action = "deleted" if candidate is None else "reloaded" if name in modules else "loaded"
                _apply(binding, projection)
                if candidate is None:
                    _modules.pop(name)
                    binding["stamps"].pop(name, None)
                else:
                    _modules[name] = candidate
                modules = updated
                _failures.pop(name, None)
                results[name] = {"action": action}
            except Exception:
                result_name = _result_name(requested)
                failure = _failure("failed to reload tool module %r", requested)
                _failures[result_name] = failure["error"]
                results[result_name] = failure
        _save_active(binding)
    return results


def reconcile(binding: dict) -> None:
    """Match this run's callables to shared last-good before its next request."""
    with binding["lock"], _catalog_lock:
        modules = catalog()
        previous = binding["stamps"]
        stamps = {name: stamp for name, stamp in previous.items() if name in modules}
        projection = _project(binding, modules)
        _apply(binding, projection)
        binding["stamps"] = stamps
        if stamps != previous:
            _save_active(binding)


def restore_schema(binding: dict) -> None:
    """Rebuild the view's ordered schema slots using current filtered definitions."""
    reconcile(binding)


def touch(binding: dict, tool_name: str) -> None:
    """Result callback: renew the owning module's stamp and persist it immediately."""
    with binding["lock"]:
        owner = binding["owners"].get(tool_name)
        if owner is not None and owner != _BASE_MODULE_NAME and owner in binding["active"]:
            binding["stamps"][owner] = time.time()
            _save_active(binding)


def state_snapshot(binding: dict) -> dict:
    """Describe announced content and schemas by digest, without saving a schema copy."""
    with binding["lock"], _catalog_lock:
        modules = _catalog(binding)
        schemas = {}
        for name in [_BASE_MODULE_NAME, *binding["schema_modules"]]:
            definitions = [tool.description for tool_name, tool in binding["session"].functions.items()
                           if binding["owners"].get(tool_name) == name]
            if definitions and (name in modules or name in binding["active"]):
                schemas[name] = hashlib.sha256(json.dumps(definitions, ensure_ascii=False,
                                                          sort_keys=True).encode()).hexdigest()
        return {
            "catalog": {name: module["description"] for name, module in modules.items()},
            "active": {name: hashlib.sha256(module["content"].encode()).hexdigest()
                       for name, module in binding["active"].items()},
            "schemas": schemas,
        }


def state_text(binding: dict, previous: dict | None = None) -> str:
    """Render a full state or changes since the caller's last durable announcement."""
    with binding["lock"], _catalog_lock:
        current = state_snapshot(binding)
        if previous is None:
            return _framed(_render_context(_catalog(binding), binding["active"]))
        lines = ["工具模块状态变化（本条由系统追加，不是用户发言）："]
        labels = {"catalog": "目录", "active": "已激活模块", "schemas": "函数定义"}
        for section, label in labels.items():
            before = previous.get(section, {})
            after = current[section]
            if not isinstance(before, dict):
                before = {}
            for name in sorted(before.keys() | after.keys()):
                if before.get(name) == after.get(name):
                    continue
                if name not in after:
                    suffix = "已卸载；不要再调用它的函数，需要时先 load_tools" if section == "active" else "已移除"
                else:
                    suffix = "新增" if name not in before else "已更新"
                lines.append(f"- {label} {name}：{suffix}")
                if section == "catalog" and name in after:
                    lines.append(f"  {after[name]}")
                if section == "active" and name in after and binding["active"][name]["content"]:
                    lines.append(f"\n## 已激活模块 {name}\n{binding['active'][name]['content']}")
        return _framed("\n".join(lines)) if len(lines) > 1 else ""


def drift_hint(binding: dict) -> str:
    """Report current disk drift without loading sources or adding durable history."""
    # WHY: 每次子请求真读盘，不节流；十来个小文件相对模型往返可以忽略。文件改回去提醒就消失，
    # 所以只做 hint，不是 provider。只报名字不自动加载，磁盘可能正在写到一半。
    try:
        changes = scan()
    except Exception:
        _log.exception("failed to scan tool sources for the drift hint")
        return ""
    labels = (("added", "新增"), ("modified", "修改"), ("deleted", "删除"))
    parts = [f"{label} {', '.join(changes[kind])}" for kind, label in labels if changes[kind]]
    if not parts:
        return ""
    return _framed("工具模块的磁盘源与已加载版本不一致，尚未应用：\n"
                   + "\n".join(f"- {part}" for part in parts)
                   + "\n需要时用 reload_tools 显式应用；不应用则当前生效的仍是目录中的版本。")


def _human_time(seconds: float) -> str:
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds / 3600:g}小时"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds / 60:g}分钟"
    return f"{seconds:g}秒"


def list_text(binding: dict) -> str:
    """List available, active, failed and changed modules with complete state."""
    with binding["lock"], _catalog_lock:
        modules = _catalog(binding)
        lines = ["可用模块:"]
        lines.extend(f"- {name}: {module['description']}"
                     + ("（已激活）" if name in binding["active"] else "")
                     for name, module in modules.items())
        if not modules:
            lines.append("- (无)")
        # WHY: 时限在这里从常量计算，不让 meta 为一句说明多一个顶层依赖和加载失败面。
        ttl = binding["ttl"]
        lines.append("空闲回收:")
        lines.append(f"- 超过{_human_time(ttl)}没有被装入或调用过的模块，下一轮开局不再装回；"
                     "装入本身算一次用过，没有导出函数的模块因此按装入时刻计时"
                     if ttl is not None and ttl > 0 else "- 关闭")
        lines.append("源码变化:")
        changes = scan()
        labels = {"added": "新增", "modified": "修改", "deleted": "删除"}
        lines.extend(f"- {labels[kind]}: {', '.join(names)}" for kind, names in changes.items() if names)
        if not any(changes.values()):
            lines.append("- (无)")
        errors = failures()
        if errors:
            lines.append("加载失败:")
            lines.extend(f"- {name}:\n{error}" for name, error in errors.items())
        lines.append("完整当前工具状态:\n" + state_text(binding))
        return "\n".join(lines)


def create_context_message(*, visible: Callable[[str, dict], bool] | None = None) -> dict[str, str]:
    """Create a catalog message; bind_session can fill it with the run's initial state."""
    pick = bot_op_tool_visible if visible is None else visible
    return {"role": "system", "content": _render_context(
        {name: module for name, module in catalog().items() if pick(name, module)}, {})}


__all__ = [
    "bind_session", "bot_op_tool_visible", "catalog", "create_context_message", "current_binding",
    "drift_hint", "failures", "initialize", "list_text", "load", "reconcile", "reload", "restore",
    "restore_schema", "scan", "state_snapshot", "state_text", "touch", "unload",
]
