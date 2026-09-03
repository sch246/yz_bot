"""Memory-authoritative JSON storage with editable disk projections."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from queue import Empty, Queue
import tempfile
from threading import Event, RLock, Thread
import time
from typing import Any, Callable

from mods import INFRA, log
from .codec import digest as _digest
from .codec import phase as _phase
from .codec import read_file as _read_file
from .codec import serialize as _serialize


PHASE = INFRA

logger = log.stream("storage")
root_path = "data/storage/"
DELETE_MARKER = "DELETE"

# WHY: 只有两个是维护者手定的，都是经验值而不是算出来的，但含义明确：
# MEMORY_SCAN_WINDOW 是"非正常终止最多丢多少改动"——正常退出走 on_exit -> save()
# 不丢，所以这个窗口只在 SIGKILL、断电、fatal error 时兑现。调它是产品决定。
# FILE_SETTLE_DELAY 是文件事件后等多久再读：编辑器常常多次写或写临时文件再 rename，
# 立刻读会读到半个文件。
# WHY?: 其余四个没有出处，维护者没有选过它们，很可能和 phase()/due_memory[0] 一样
# 来自同一次辅助重构。它们目前没有已知问题，但也没有理由；改动前不必当成经过判断的值。
MEMORY_SCAN_WINDOW = 10 * 60.0   # 手定：非正常终止的最大丢失窗口
FILE_SCAN_WINDOW = 2.0
DISCOVERY_INTERVAL = 30.0
FILE_SETTLE_DELAY = 1.0          # 手定：等编辑器写完
WORKER_TICK = 0.2
RETRY_DELAY = 30.0


@dataclass
class _EntryState:
    baseline_digest: str | None
    disk_signature: tuple[int, int] | None
    next_memory_check: float
    next_file_check: float
    repair_disk: bool = False


storage: dict[str, dict[str, Any]] = {}
load_errors: dict[tuple[str, str], Exception] = {}
_states: dict[tuple[str, str], _EntryState] = {}
_lock = RLock()
_file_events: Queue[str] = Queue()
_pending_file_events: dict[str, float] = {}
_stop_event = Event()
_worker: Thread | None = None
_observer: Any = None
_shutdown_started = False


def _report(message: str, level: int = logging.WARNING) -> None:
    # One record, one exit: the ``[storage]`` tag it used to print by hand is
    # now the stream name, and the terminal decides whether to show it.
    logger.log(level, message)


def _key_label(namespace: str, name: str) -> str:
    return f"{namespace}/{name}" if namespace else name


def _path(namespace: str, name: str) -> str:
    return os.path.join(root_path, namespace, name + ".json")


def _disk_signature(path: str) -> tuple[int, int] | None:
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _replace(namespace: str, name: str, value: Any) -> Any:
    """Refill the existing container instead of rebinding the name.

    WHY: 承重不变量，别改成 ``values[name] = value``。模块持有的是活引用——
    ``cave.Cave.__init__`` 就把 ``storage.get("", "cave")`` 存进了 ``self.msgs``——
    重新绑定只会换掉这里的字典，所有持有者继续看着旧对象，磁盘上的改动对它们永远不
    可见，而且它们的写入还会被下一轮同步当成"内存变了"再写回去。
    原地 clear+update 让"文件改了，运行中的功能立刻看到"这件事成立，这正是文件可编辑
    的意义所在。类型不同(dict 换成 list)时才退回绑定，因为那时本来就没有容器可复用。
    """
    values = storage.setdefault(namespace, {})
    current = values.get(name)
    if isinstance(current, dict) and isinstance(value, dict):
        current.clear()
        current.update(value)
        return current
    if isinstance(current, list) and isinstance(value, list):
        current.clear()
        current.extend(value)
        return current
    values[name] = value
    return value


def _ensure_state(
    namespace: str,
    name: str,
    *,
    baseline_digest: str | None = None,
    signature: tuple[int, int] | None = None,
) -> _EntryState:
    key = namespace, name
    state = _states.get(key)
    if state is None:
        now = time.monotonic()
        state = _EntryState(
            baseline_digest,
            signature,
            _phase(key, MEMORY_SCAN_WINDOW, now),
            _phase(key, FILE_SCAN_WINDOW, now),
        )
        _states[key] = state
    return state


def _write_snapshot(namespace: str, name: str, text: str, digest: str) -> None:
    path = _path(namespace, name)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=directory,
        prefix=f".{name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    with _lock:
        state = _ensure_state(namespace, name)
        state.baseline_digest = digest
        state.disk_signature = _disk_signature(path)
        state.repair_disk = False
        load_errors.pop((namespace, name), None)


def _serialize_reporting(value: Any) -> tuple[str, str, dict[str, int]]:
    """``_serialize`` plus what it silently turned into ``null``."""
    dropped: dict[str, int] = {}

    def note(obj: Any) -> None:
        label = type(obj).__name__
        dropped[label] = dropped.get(label, 0) + 1

    text, value_digest = _serialize(value, on_drop=note)
    return text, value_digest, dropped


def _report_dropped(namespace: str, name: str, dropped: dict[str, int]) -> None:
    # 只在真正写盘时报，不在每轮变化检测时报——否则同一个坏值每个窗口都会刷一条。
    if not dropped:
        return
    detail = "、".join(f"{label}×{count}" for label, count in sorted(dropped.items()))
    _report(
        f'保存"{_key_label(namespace, name)}"时有值无法序列化，已写成 null：{detail}',
        logging.WARNING,
    )


def _write_one(namespace: str, name: str) -> Any:
    with _lock:
        try:
            value = storage[namespace][name]
        except KeyError:
            raise KeyError(f'storage "{_key_label(namespace, name)}" 不存在')
        text, digest, dropped = _serialize_reporting(value)
    _report_dropped(namespace, name, dropped)
    _write_snapshot(namespace, name, text, digest)
    return value


def _delete_one(namespace: str, name: str, *, remove_file: bool) -> None:
    key = namespace, name
    with _lock:
        values = storage.get(namespace)
        if values is not None and name in values:
            current = values.pop(name)
            if isinstance(current, (dict, list)):
                current.clear()
        _states.pop(key, None)
        load_errors.pop(key, None)
    if remove_file:
        try:
            os.unlink(_path(namespace, name))
        except FileNotFoundError:
            pass


def delete(namespace: str, name: str) -> None:
    if not isinstance(namespace, str) or not isinstance(name, str):
        raise TypeError("namespace 和 name 必须是字符串")
    _delete_one(namespace, name, remove_file=True)


def _install_loaded_value(namespace: str, name: str, value: Any, path: str) -> Any:
    digest = _digest(value)
    with _lock:
        result = _replace(namespace, name, value)
        state = _ensure_state(namespace, name)
        state.baseline_digest = digest
        state.disk_signature = _disk_signature(path)
        state.repair_disk = False
        load_errors.pop((namespace, name), None)
    return result


def _load_one(namespace: str, name: str) -> Any:
    path = _path(namespace, name)
    kind, value = _read_file(path)
    if kind == "delete":
        _delete_one(namespace, name, remove_file=True)
        return None
    if kind == "empty":
        with _lock:
            exists = name in storage.get(namespace, {})
        if not exists:
            raise ValueError(f'空文件"{path}"无法拉取：内存中没有对应 storage')
        _report(f'空文件触发从内存回写："{_key_label(namespace, name)}"', logging.INFO)
        return _write_one(namespace, name)
    return _install_loaded_value(namespace, name, value, path)


def load(namespace: str | None = None, name: str | None = None) -> Any:
    """Load from disk; two arguments are the explicit disk-wins escape hatch."""
    if namespace is not None or name is not None:
        if not isinstance(namespace, str) or not isinstance(name, str):
            raise TypeError("namespace 和 name 必须同时传入字符串")
        try:
            return _load_one(namespace, name)
        except Exception as error:
            _report(f'显式加载"{_key_label(namespace, name)}"失败：{error}', logging.ERROR)
            raise

    os.makedirs(root_path, exist_ok=True)
    for root, _, files in os.walk(root_path):
        current_namespace = os.path.relpath(root, root_path)
        current_namespace = "" if current_namespace == "." else current_namespace.replace(os.sep, "/")
        storage.setdefault(current_namespace, {})
        for filename in files:
            if not filename.endswith(".json"):
                continue
            current_name = filename[:-5]
            key = current_namespace, current_name
            try:
                _load_one(*key)
            except Exception as error:
                load_errors[key] = error
                _ensure_state(*key, signature=_disk_signature(os.path.join(root, filename)))
                _report(
                    f'启动加载"{_key_label(*key)}"失败，未替换内存：{error}',
                    logging.ERROR,
                )


def save() -> None:
    with _lock:
        snapshot = [(namespace, list(values)) for namespace, values in storage.items()]
    for namespace, names in snapshot:
        for name in names:
            try:
                _write_one(namespace, name)
            except Exception as error:
                _report(f'保存"{_key_label(namespace, name)}"失败：{error}', logging.ERROR)


def get_namespace(namespace: str) -> dict[str, Any]:
    with _lock:
        return storage.setdefault(namespace, {})


def get(namespace: str, name: str, default: Callable[[], Any] = dict) -> Any:
    key = namespace, name
    with _lock:
        # WHY: 文件加载失败且内存里也没有时，宁可抛异常也不返回一个新的空默认值。
        # 返回默认值看起来更友好，但下一次 save() 就会把这个空值写回去，把"文件损坏但
        # 内容还在"变成"内容真的没了"。抛异常让损坏停在可修的状态：文件还在原地，
        # 人可以去看、去改、去恢复。别为了"让 get 不抛异常"把这条去掉。
        if key in load_errors and name not in storage.get(namespace, {}):
            raise ValueError(
                f'storage "{_key_label(*key)}" 的文件加载失败，拒绝用默认值覆盖'
            ) from load_errors[key]
        values = storage.setdefault(namespace, {})
        if name not in values:
            values[name] = default()
        _ensure_state(namespace, name)
        return values[name]


def _path_to_key(path: str) -> tuple[str, str] | None:
    root = os.path.abspath(root_path)
    absolute = os.path.abspath(path)
    try:
        if os.path.commonpath((root, absolute)) != root:
            return None
    except ValueError:
        return None
    relative = os.path.relpath(absolute, root)
    if not relative.endswith(".json"):
        return None
    parent, filename = os.path.split(relative)
    namespace = "" if parent == "." else parent.replace(os.sep, "/")
    return namespace, filename[:-5]


def _process_file_path(path: str) -> None:
    key = _path_to_key(path)
    if key is None:
        return
    namespace, name = key
    label = _key_label(*key)
    if not os.path.exists(path):
        with _lock:
            if name in storage.get(namespace, {}):
                state = _ensure_state(*key)
                state.disk_signature = None
                state.repair_disk = True
                _report(f'文件"{label}"被删除；保留内存并等待重建')
        return
    try:
        kind, file_value = _read_file(path)
    except Exception as error:
        with _lock:
            load_errors.setdefault(key, error)
            _ensure_state(*key).disk_signature = _disk_signature(path)
        _report(f'文件"{label}"不是合法 JSON，未替换内存：{error}', logging.ERROR)
        return
    if kind == "delete":
        _delete_one(*key, remove_file=True)
        _report(f'文件"{label}"请求业务删除，已删除内存与文件', logging.INFO)
        return
    if kind == "empty":
        with _lock:
            exists = name in storage.get(namespace, {})
        if not exists:
            error = ValueError("内存中没有对应 storage")
            with _lock:
                load_errors[key] = error
                _ensure_state(*key).disk_signature = _disk_signature(path)
            _report(f'空文件"{label}"无法拉取：内存中没有对应 storage', logging.ERROR)
            return
        _write_one(*key)
        return
    file_digest = _digest(file_value)
    with _lock:
        values = storage.get(namespace, {})
        if name not in values:
            _install_loaded_value(namespace, name, file_value, path)
            return
        state = _ensure_state(*key)
        memory_digest = _digest(values[name])
        if file_digest == state.baseline_digest:
            state.disk_signature = _disk_signature(path)
            return
        if memory_digest != state.baseline_digest:
            state.disk_signature = _disk_signature(path)
            state.repair_disk = True
            _report(f'文件覆盖内存失败："{label}"的内存已修改，将以内存为准')
            return
        if _digest(values[name]) != memory_digest:
            _report(f'文件覆盖内存失败："{label}"在同步期间发生并发修改')
            return
        _replace(namespace, name, file_value)
        state.baseline_digest = file_digest
        state.disk_signature = _disk_signature(path)
        state.repair_disk = False
        load_errors.pop(key, None)


def _discover() -> None:
    with _lock:
        for namespace, values in list(storage.items()):
            for name in list(values):
                if isinstance(name, str):
                    _ensure_state(namespace, name)
    for root, _, files in os.walk(root_path):
        for filename in files:
            if filename.endswith(".json"):
                path = os.path.join(root, filename)
                key = _path_to_key(path)
                with _lock:
                    known = key in _states
                if key is not None and not known:
                    _file_events.put(path)


def _sync_memory_key(key: tuple[str, str], now: float) -> None:
    with _lock:
        state = _states.get(key)
        if state is None:
            return
        state.next_memory_check = _phase(key, MEMORY_SCAN_WINDOW, now + 0.001)
        try:
            text, digest, dropped = _serialize_reporting(storage[key[0]][key[1]])
        except Exception as error:
            state.next_memory_check = now + RETRY_DELAY
            _report(f'检查"{_key_label(*key)}"内存变化失败：{error}', logging.ERROR)
            return
        should_write = digest != state.baseline_digest or state.repair_disk
    if should_write:
        _report_dropped(*key, dropped)
        try:
            _write_snapshot(*key, text, digest)
        except Exception as error:
            _report(f'同步"{_key_label(*key)}"到文件失败：{error}', logging.ERROR)


def _worker_loop() -> None:
    next_discovery = 0.0
    while not _stop_event.is_set():
        now = time.monotonic()
        if now >= next_discovery:
            _discover()
            next_discovery = now + DISCOVERY_INTERVAL
        while True:
            try:
                changed = _file_events.get_nowait()
            except Empty:
                break
            _pending_file_events[os.path.abspath(changed)] = now + FILE_SETTLE_DELAY
        with _lock:
            for key, state in list(_states.items()):
                if state.next_file_check <= now:
                    state.next_file_check = _phase(key, FILE_SCAN_WINDOW, now + 0.001)
                    path = _path(*key)
                    if _disk_signature(path) != state.disk_signature:
                        _pending_file_events.setdefault(os.path.abspath(path), now + FILE_SETTLE_DELAY)
            due_memory = [key for key, state in _states.items() if state.next_memory_check <= now]
        for path, due in list(_pending_file_events.items()):
            if due <= now:
                _pending_file_events.pop(path, None)
                try:
                    _process_file_path(path)
                except Exception as error:
                    _report(f'处理文件变化"{path}"失败：{error}', logging.ERROR)
        if due_memory:
            # WHY?: 同 codec.phase——辅助重构引入，维护者没有判断过。due_memory 是列表
            # 却只取第一个，于是每个 tick 最多同步一个 key，配合 WORKER_TICK=0.2 就是
            # 5 key/秒的上限。正常情况下靠 phase() 错峰，同时到期的很少，所以看不出来；
            # 但积压时(比如刚启动、或一批 key 同时被改)排空速率就是这个数，而这个上限
            # 不是谁定的。要改的话这里应该是循环，不是取 [0]。
            _sync_memory_key(due_memory[0], now)
        _stop_event.wait(WORKER_TICK)


def _watchdog_handler() -> Any:
    try:
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        return None

    class Handler(FileSystemEventHandler):
        @staticmethod
        def _queue(event: Any) -> None:
            if not event.is_directory:
                _file_events.put(event.src_path)

        on_created = _queue
        on_modified = _queue
        on_deleted = _queue

        def on_moved(self, event: Any) -> None:
            self._queue(event)
            if getattr(event, "dest_path", None):
                _file_events.put(event.dest_path)

    return Handler()


def _start_worker() -> None:
    global _worker, _observer
    os.makedirs(root_path, exist_ok=True)
    handler = _watchdog_handler()
    if handler is not None:
        try:
            from watchdog.observers import Observer

            observer = Observer()
            observer.schedule(handler, root_path, recursive=True)
            observer.start()
            _observer = observer
        except Exception as error:
            _report(f"启动 watchdog 失败，改用 stat 轮询：{error}", logging.ERROR)
    _worker = Thread(target=_worker_loop, name="storage-sync", daemon=True)
    _worker.start()


def shutdown(*, flush: bool = True) -> None:
    global _shutdown_started
    with _lock:
        if _shutdown_started:
            return
        _shutdown_started = True
    _stop_event.set()
    if _observer is not None:
        _observer.stop()
        _observer.join(timeout=5)
    if _worker is not None and _worker.is_alive():
        _worker.join(timeout=5)
    if flush:
        save()


def on_load(_ctx: dict[str, Any] | None = None) -> None:
    global _shutdown_started
    _shutdown_started = False
    _stop_event.clear()
    load()
    _start_worker()


def on_exit() -> None:
    shutdown(flush=True)
