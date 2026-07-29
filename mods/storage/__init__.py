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

from mods import INFRA
from .codec import digest as _digest
from .codec import phase as _phase
from .codec import read_file as _read_file
from .codec import serialize as _serialize


PHASE = INFRA

logger = logging.getLogger(__name__)
root_path = "data/storage/"
DELETE_MARKER = "DELETE"

MEMORY_SCAN_WINDOW = 10 * 60.0
FILE_SCAN_WINDOW = 2.0
DISCOVERY_INTERVAL = 30.0
FILE_SETTLE_DELAY = 1.0
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
    logger.log(level, message)
    print(f"[storage] {message}")


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


def _write_one(namespace: str, name: str) -> Any:
    with _lock:
        try:
            value = storage[namespace][name]
        except KeyError:
            raise KeyError(f'storage "{_key_label(namespace, name)}" 不存在')
        text, digest = _serialize(value)
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
            text, digest = _serialize(storage[key[0]][key[1]])
        except Exception as error:
            state.next_memory_check = now + RETRY_DELAY
            _report(f'检查"{_key_label(*key)}"内存变化失败：{error}', logging.ERROR)
            return
        should_write = digest != state.baseline_digest or state.repair_disk
    if should_write:
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
