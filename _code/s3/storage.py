"""JSON storage with memory-authoritative hot synchronization.

Callers keep receiving ordinary mutable objects from :func:`get`.  A background
worker spreads memory snapshots over ten minutes and only writes changed files.
External file changes are loaded immediately when memory still matches the last
shared baseline.  Concurrent direct mutations remain intentionally best effort.
"""

from __future__ import annotations

import atexit
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from queue import Empty, Queue
import tempfile
from threading import Event, RLock, Thread
import time
from typing import Any, Callable


logger = logging.getLogger(__name__)

root_path = "data/storage/"

# A dirty object is found within this window.  Checks are assigned a stable phase
# so serialization and writes do not form a ten-minute traffic spike.
MEMORY_SCAN_WINDOW = 10 * 60.0

# watchdog supplies prompt events when installed.  These lightweight stat checks
# are both its lost-event reconciliation and the dependency-free fallback.
FILE_SCAN_WINDOW = 2.0
DISCOVERY_INTERVAL = 30.0
FILE_SETTLE_DELAY = 1.0
WORKER_TICK = 0.2
MAX_MEMORY_SYNCS_PER_TICK = 1
MAX_FILE_STATS_PER_TICK = 32
MAX_FILE_EVENTS_PER_TICK = 8
RETRY_DELAY = 30.0

DELETE_MARKER = "DELETE"


@dataclass
class _EntryState:
    baseline_digest: str | None
    disk_signature: tuple[int, int] | None
    next_memory_check: float
    next_file_check: float
    repair_disk: bool = False


_is_reload = "storage" in globals()
if not _is_reload:
    storage: dict[str, dict[str, Any]] = {}
    _states: dict[tuple[str, str], _EntryState] = {}
    load_errors: dict[tuple[str, str], Exception] = {}
    _lock = RLock()
    _file_events: Queue[str] = Queue()
    _pending_file_events: dict[str, float] = {}
    _stop_event = Event()
    _worker: Thread | None = None
    _observer = None
    _shutdown_started = False


def _report(message: str, level: int = logging.WARNING) -> None:
    """Make synchronization failures visible in both app.log and the terminal."""
    if logger.hasHandlers():
        logger.log(level, message)
    print(f"[storage] {message}")


def _key_label(name_space: str, name: str) -> str:
    return f"{name_space}/{name}" if name_space else name


def _path(name_space: str, name: str) -> str:
    return os.path.join(root_path, name_space, name + ".json")


def _disk_signature(path: str) -> tuple[int, int] | None:
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _phase(key: tuple[str, str], window: float, now: float) -> float:
    if window <= 0:
        return now
    raw = hashlib.sha256("\0".join(key).encode("utf-8")).digest()[:8]
    offset = int.from_bytes(raw, "big") / (2**64) * window
    start = now - now % window
    due = start + offset
    return due if due > now else due + window


def _serialize(value: Any) -> tuple[str, str]:
    """Return disk text and a digest of the JSON-representable value.

    The compatibility flags match the old storage writer.  Digesting the parsed
    form makes whitespace and object key order irrelevant to synchronization.
    """
    text = json.dumps(
        value,
        indent=4,
        ensure_ascii=False,
        skipkeys=True,
        default=lambda _: None,
    )
    normalized = json.dumps(
        json.loads(text),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return text, hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _digest(value: Any) -> str:
    return _serialize(value)[1]


def _replace(name_space: str, name: str, value: Any) -> Any:
    """Replace a root container in place so existing module references stay live."""
    namespace = storage.setdefault(name_space, {})
    current = namespace.get(name)
    if isinstance(current, dict) and isinstance(value, dict):
        current.clear()
        current.update(value)
        return current
    if isinstance(current, list) and isinstance(value, list):
        current.clear()
        current.extend(value)
        return current
    namespace[name] = value
    return value


def _ensure_state(
    name_space: str,
    name: str,
    *,
    baseline_digest: str | None = None,
    signature: tuple[int, int] | None = None,
) -> _EntryState:
    key = name_space, name
    state = _states.get(key)
    if state is None:
        now = time.monotonic()
        state = _EntryState(
            baseline_digest=baseline_digest,
            disk_signature=signature,
            next_memory_check=_phase(key, MEMORY_SCAN_WINDOW, now),
            next_file_check=_phase(key, FILE_SCAN_WINDOW, now),
        )
        _states[key] = state
    return state


def _read_file(path: str) -> tuple[str, Any | None]:
    """Return ``json``, ``empty`` or ``delete`` and the parsed value."""
    with open(path, "r", encoding="utf-8") as file:
        text = file.read()
    stripped = text.strip()
    if not stripped:
        return "empty", None
    if stripped == DELETE_MARKER:
        return "delete", None
    return "json", json.loads(text)


def _write_snapshot(
    name_space: str,
    name: str,
    value: Any,
    text: str,
    digest: str,
) -> None:
    """Atomically replace one JSON file and advance its baseline on success."""
    path = _path(name_space, name)
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
        state = _ensure_state(name_space, name)
        state.baseline_digest = digest
        state.disk_signature = _disk_signature(path)
        state.repair_disk = False
        load_errors.pop((name_space, name), None)


def _write_one(name_space: str, name: str) -> Any:
    with _lock:
        try:
            value = storage[name_space][name]
        except KeyError:
            raise KeyError(f'storage "{_key_label(name_space, name)}" 不存在')
        text, digest = _serialize(value)
    _write_snapshot(name_space, name, value, text, digest)
    return value


def _delete_one(name_space: str, name: str, *, remove_file: bool) -> None:
    key = name_space, name
    with _lock:
        namespace = storage.get(name_space)
        if namespace is not None and name in namespace:
            current = namespace.pop(name)
            # Held root references should observe that their storage entry died.
            if isinstance(current, (dict, list)):
                current.clear()
        _states.pop(key, None)
        load_errors.pop(key, None)
    if remove_file:
        try:
            os.unlink(_path(name_space, name))
        except FileNotFoundError:
            pass


def delete(name_space: str, name: str) -> None:
    """Explicitly delete one storage value from both memory and disk."""
    if not isinstance(name_space, str) or not isinstance(name, str):
        raise TypeError("name_space 和 name 必须是字符串")
    _delete_one(name_space, name, remove_file=True)


def _install_loaded_value(name_space: str, name: str, value: Any, path: str) -> Any:
    digest = _digest(value)
    with _lock:
        result = _replace(name_space, name, value)
        state = _ensure_state(name_space, name)
        state.baseline_digest = digest
        state.disk_signature = _disk_signature(path)
        state.repair_disk = False
        load_errors.pop((name_space, name), None)
    return result


def _load_one(name_space: str, name: str) -> Any:
    """Explicit load: the caller deliberately chooses disk over memory."""
    path = _path(name_space, name)
    kind, value = _read_file(path)
    if kind == "delete":
        _delete_one(name_space, name, remove_file=True)
        return None
    if kind == "empty":
        with _lock:
            exists = name in storage.get(name_space, {})
        if not exists:
            raise ValueError(
                f'空文件"{path}"无法拉取：内存中没有对应 storage'
            )
        _report(f'空文件触发从内存回写："{_key_label(name_space, name)}"', logging.INFO)
        return _write_one(name_space, name)
    return _install_loaded_value(name_space, name, value, path)


def load(name_space: str | None = None, name: str | None = None) -> Any:
    """Load JSON from disk.

    ``load(namespace, name)`` remains the explicit force-load escape hatch.  With
    no arguments it performs startup discovery and establishes initial baselines.
    """
    if name_space is not None or name is not None:
        if not isinstance(name_space, str) or not isinstance(name, str):
            raise TypeError("name_space 和 name 必须同时传入字符串")
        try:
            return _load_one(name_space, name)
        except Exception as error:
            _report(
                f'显式加载"{_key_label(name_space, name)}"失败：{error}',
                logging.ERROR,
            )
            raise

    os.makedirs(root_path, exist_ok=True)
    for root, _, files in os.walk(root_path):
        current_namespace = root[len(root_path) :].replace("\\", "/")
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
                _ensure_state(
                    *key,
                    signature=_disk_signature(os.path.join(root, filename)),
                )
                _report(
                    f'启动加载"{_key_label(*key)}"失败，未替换内存：{error}',
                    logging.ERROR,
                )


def save() -> None:
    """Force all current storage values to disk through the atomic writer."""
    with _lock:
        namespaces = list(storage.items())
    for name_space, values in namespaces:
        try:
            items = list(values)
        except RuntimeError as error:
            _report(f'保存命名空间"{name_space}"失败，将在下次重试：{error}', logging.ERROR)
            continue
        for name in items:
            try:
                _write_one(name_space, name)
            except Exception as error:
                _report(
                    f'保存"{_key_label(name_space, name)}"失败，将在下次重试：{error}',
                    logging.ERROR,
                )


def get_namespace(name_space: str) -> dict[str, Any]:
    with _lock:
        return storage.setdefault(name_space, {})


def get(name_space: str, name: str, default: Callable[[], Any] = dict) -> Any:
    key = name_space, name
    with _lock:
        if key in load_errors and name not in storage.get(name_space, {}):
            raise ValueError(
                f'storage "{_key_label(*key)}" 的文件加载失败，拒绝用默认值覆盖'
            ) from load_errors[key]
        namespace = storage.setdefault(name_space, {})
        if name not in namespace:
            namespace[name] = default()
        _ensure_state(name_space, name)
        return namespace[name]


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
    name_space = "" if parent == "." else parent.replace(os.sep, "/")
    return name_space, filename[:-5]


def _process_file_path(path: str) -> None:
    key = _path_to_key(path)
    if key is None:
        return
    name_space, name = key
    label = _key_label(*key)

    if not os.path.exists(path):
        with _lock:
            if name in storage.get(name_space, {}):
                state = _ensure_state(*key)
                state.disk_signature = None
                state.repair_disk = True
                _report(f'文件"{label}"被删除；保留内存并等待重建')
            elif key in _states:
                _states[key].disk_signature = None
        return

    try:
        kind, file_value = _read_file(path)
    except Exception as error:
        _report(f'文件"{label}"不是合法 JSON，未替换内存：{error}', logging.ERROR)
        with _lock:
            state = _states.get(key)
            if state is None:
                load_errors[key] = error
                state = _ensure_state(*key)
            state.disk_signature = _disk_signature(path)
        return

    if kind == "delete":
        _delete_one(*key, remove_file=True)
        _report(f'文件"{label}"请求业务删除，已删除内存与文件', logging.INFO)
        return

    if kind == "empty":
        with _lock:
            exists = name in storage.get(name_space, {})
        if not exists:
            error = ValueError("内存中没有对应 storage")
            with _lock:
                load_errors[key] = error
                state = _ensure_state(*key)
                state.disk_signature = _disk_signature(path)
            _report(f'空文件"{label}"无法拉取：内存中没有对应 storage', logging.ERROR)
            return
        try:
            _write_one(*key)
            _report(f'空文件"{label}"已从内存恢复', logging.INFO)
        except Exception as error:
            _report(f'空文件"{label}"从内存恢复失败：{error}', logging.ERROR)
        return

    try:
        file_digest = _digest(file_value)
    except Exception as error:
        _report(f'文件"{label}"无法规范化，未替换内存：{error}', logging.ERROR)
        return

    with _lock:
        namespace = storage.get(name_space, {})
        if name not in namespace:
            _install_loaded_value(name_space, name, file_value, path)
            return
        state = _ensure_state(*key)
        try:
            memory_digest = _digest(namespace[name])
        except Exception as error:
            _report(f'检查"{label}"内存快照失败，本轮未载入文件：{error}', logging.ERROR)
            return

        if file_digest == state.baseline_digest:
            state.disk_signature = _disk_signature(path)
            return
        if memory_digest != state.baseline_digest:
            state.disk_signature = _disk_signature(path)
            state.repair_disk = True
            _report(f'文件覆盖内存失败："{label}"的内存已修改，将以内存为准')
            return

        # Best-effort CAS: direct mutable objects cannot make this check and the
        # in-place replacement transactional, so recheck immediately before it.
        if _digest(namespace[name]) != memory_digest:
            _report(f'文件覆盖内存失败："{label}"在同步期间发生并发修改')
            return
        _replace(name_space, name, file_value)
        state.baseline_digest = file_digest
        state.disk_signature = _disk_signature(path)
        state.repair_disk = False
        load_errors.pop(key, None)


def _sync_memory_key(key: tuple[str, str], now: float) -> None:
    name_space, name = key
    with _lock:
        state = _states.get(key)
        if state is None:
            return
        state.next_memory_check = _phase(key, MEMORY_SCAN_WINDOW, now + 0.001)
        try:
            value = storage[name_space][name]
            text, digest = _serialize(value)
        except KeyError:
            _report(f'内存项"{_key_label(*key)}"被直接删除；请使用 storage.delete()')
            return
        except Exception as error:
            state.next_memory_check = now + RETRY_DELAY
            _report(f'检查"{_key_label(*key)}"内存变化失败，将在下轮重试：{error}', logging.ERROR)
            return
        should_write = digest != state.baseline_digest or state.repair_disk
    if not should_write:
        return
    try:
        _write_snapshot(name_space, name, value, text, digest)
    except Exception as error:
        with _lock:
            state = _states.get(key)
            if state is not None:
                state.next_memory_check = time.monotonic() + RETRY_DELAY
        _report(f'同步"{_key_label(*key)}"到文件失败，将在下轮重试：{error}', logging.ERROR)


def _discover_memory_states() -> None:
    with _lock:
        for name_space, namespace in list(storage.items()):
            try:
                names = list(namespace)
            except RuntimeError:
                continue
            for name in names:
                if isinstance(name, str):
                    _ensure_state(name_space, name)


def _discover_disk_files() -> None:
    try:
        for root, _, files in os.walk(root_path):
            for filename in files:
                if not filename.endswith(".json"):
                    continue
                path = os.path.join(root, filename)
                key = _path_to_key(path)
                if key is None:
                    continue
                with _lock:
                    known = key in _states
                if not known:
                    _file_events.put(path)
    except Exception as error:
        _report(f'扫描 storage 文件失败：{error}', logging.ERROR)


def _poll_known_files(now: float) -> None:
    with _lock:
        due = [
            (key, state)
            for key, state in _states.items()
            if state.next_file_check <= now
        ][:MAX_FILE_STATS_PER_TICK]
        for key, state in due:
            state.next_file_check = _phase(key, FILE_SCAN_WINDOW, now + 0.001)
    for key, state in due:
        path = _path(*key)
        if _disk_signature(path) != state.disk_signature:
            # setdefault is important: the fallback sees the mismatch on every
            # pass until it is processed and must not postpone it forever.
            _pending_file_events.setdefault(
                os.path.abspath(path), now + FILE_SETTLE_DELAY
            )


def _watchdog_handler():
    try:
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        return None

    class Handler(FileSystemEventHandler):
        @staticmethod
        def _queue(event):
            if event.is_directory:
                return
            _file_events.put(event.src_path)

        def on_created(self, event):
            self._queue(event)

        def on_modified(self, event):
            self._queue(event)

        def on_deleted(self, event):
            self._queue(event)

        def on_moved(self, event):
            self._queue(event)
            destination = getattr(event, "dest_path", None)
            if destination:
                _file_events.put(destination)

    return Handler()


def _start_file_observer() -> None:
    global _observer
    handler = _watchdog_handler()
    if handler is None:
        _report(
            "未安装 watchdog，文件热同步降级为约 2 秒一次的滚动 stat 轮询",
            logging.INFO,
        )
        return
    try:
        from watchdog.observers import Observer

        observer = Observer()
        observer.schedule(handler, root_path, recursive=True)
        observer.start()
        _observer = observer
    except Exception as error:
        _report(f'启动 watchdog 失败，改用滚动 stat 轮询：{error}', logging.ERROR)


def _worker_loop() -> None:
    next_discovery = 0.0
    while not _stop_event.is_set():
        now = time.monotonic()
        if now >= next_discovery:
            _discover_memory_states()
            _discover_disk_files()
            next_discovery = now + DISCOVERY_INTERVAL

        while True:
            try:
                path = _file_events.get_nowait()
            except Empty:
                break
            # Editors commonly truncate before writing.  Resetting this short
            # stability window coalesces those events so a transient zero-byte
            # state is not mistaken for the explicit empty-file pull signal.
            _pending_file_events[os.path.abspath(path)] = now + FILE_SETTLE_DELAY

        _poll_known_files(now)
        due_files = [
            path for path, due_at in _pending_file_events.items() if due_at <= now
        ]
        for path in due_files[:MAX_FILE_EVENTS_PER_TICK]:
            _pending_file_events.pop(path, None)
            try:
                _process_file_path(path)
            except Exception as error:
                _report(f'处理文件变化"{path}"失败：{error}', logging.ERROR)

        with _lock:
            due_memory = [
                key for key, state in _states.items() if state.next_memory_check <= now
            ]
        # A stable phase spreads normal work; this hard cap also bounds a bad
        # collision or a backlog after the process was paused.
        for key in due_memory[:MAX_MEMORY_SYNCS_PER_TICK]:
            _sync_memory_key(key, now)
        _stop_event.wait(WORKER_TICK)


def _start_worker() -> None:
    global _worker
    os.makedirs(root_path, exist_ok=True)
    _start_file_observer()
    _worker = Thread(target=_worker_loop, name="storage-sync", daemon=True)
    _worker.start()


def shutdown(*, flush: bool = True) -> None:
    """停止热同步并按需强制落盘；可由显式退出路径和 atexit 重复调用。"""
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


if not _is_reload:
    load()
    _start_worker()
    atexit.register(shutdown)
