"""Append-only archive for original NapCat history messages missing from chatlog."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from contextlib import closing
from typing import Any, Iterable, Iterator, MutableMapping
from uuid import uuid4


_lock = threading.Lock()
_SUFFIX = ".backfill.jsonl"


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_directory(directory: Path) -> None:
    if directory.is_dir():
        return
    _ensure_directory(directory.parent)
    try:
        directory.mkdir()
    except FileExistsError:
        if not directory.is_dir():
            raise
    else:
        _sync_directory(directory.parent)


def _base(root: str | os.PathLike | None) -> Path:
    return Path("chatlog" if root is None else root)


def _window(kind: str, target: int | str, root: str | os.PathLike | None) -> Path:
    if kind not in ("group", "private") or not str(target).isdigit():
        raise ValueError("回填只支持群聊或私聊窗口")
    return _base(root) / kind / str(target)


def _message_id(value: Any) -> str:
    if isinstance(value, bool) or not str(value).lstrip("-").isdigit():
        raise ValueError("回填消息缺少 QQ message_id")
    return str(int(value))


def _sequence(value: Any) -> int:
    if isinstance(value, bool) or not str(value).lstrip("-").isdigit():
        raise ValueError("回填消息缺少 NapCat message_seq")
    return int(value)


def _time(value: Any) -> int:
    if isinstance(value, bool) or not str(value).isdigit():
        raise ValueError("回填消息缺少秒级时间")
    return int(value)


def _validate(message: Any) -> tuple[str, int, int]:
    if not isinstance(message, dict) or "message" not in message:
        raise ValueError("回填消息缺少原始正文")
    sender = message.get("sender")
    if not isinstance(sender, dict) or "user_id" not in sender:
        raise ValueError("回填消息缺少原始作者")
    _message_id(sender["user_id"])
    return _message_id(message.get("message_id")), _time(message.get("time")), _sequence(message.get("message_seq"))


def _validate_window(message: dict[str, Any], kind: str, target: int | str) -> None:
    if message.get("message_type", kind) != kind:
        raise ValueError("回填消息类型与档案路径不一致")
    field = "group_id" if kind == "group" else "target_id"
    value = message.get(field)
    if value is not None and _message_id(value) != _message_id(target):
        raise ValueError("回填消息窗口与档案路径不一致")


def sidecar_path(kind: str, target: int | str, timestamp: int, *, root: str | os.PathLike | None = None) -> Path:
    """Return the separate day file; never redirect a historical write to DD.log."""
    local = time.localtime(timestamp)
    return _window(kind, target, root) / time.strftime("%Y-%m", local) / f"{local.tm_mday:02d}{_SUFFIX}"


def _origin(path: Path, line: int, root: str | os.PathLike | None) -> str:
    return f"{path.relative_to(_base(root))}:{line}"


def _iter_sidecar(path: Path, root: str | os.PathLike | None) -> Iterator[tuple[int, int, dict[str, Any]]]:
    """Isolate an uncommitted final fragment; reject a bad complete line."""
    if not path.exists():
        return
    with path.open("r+b") as file:
        line = 0
        while True:
            offset = file.tell()
            chunk = file.readline()
            if not chunk:
                break
            if not chunk.endswith(b"\n"):
                complete = file.tell() - len(chunk)
                backup = path.with_name(path.name + ".incomplete-" + uuid4().hex)
                with backup.open("xb") as isolated:
                    isolated.write(chunk)
                    isolated.flush()
                    os.fsync(isolated.fileno())
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                file.seek(complete)
                if file.read() != chunk:
                    raise RuntimeError("回填档案在残尾修复期间被外部修改")
                file.truncate(complete)
                file.flush()
                os.fsync(file.fileno())
                break
            line += 1
            try:
                message = json.loads(chunk)
                _validate(message)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                raise ValueError(f"回填档案损坏：{_origin(path, line, root)}") from error
            yield line, offset, message


def _read_sidecar(path: Path, root: str | os.PathLike | None) -> list[dict[str, Any]]:
    return [message for _, _, message in _iter_sidecar(path, root)]


def _ordinary_ids(path: Path, root: str | os.PathLike | None) -> dict[str, str]:
    if not path.is_file():
        return {}
    from mods import chatlog

    found: dict[str, str] = {}
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            if line.startswith("    "):
                continue
            head = chatlog._split_head(line.rstrip("\r\n"))
            if head is None or not head["message_id"]:
                continue
            try:
                message_id = _message_id(head["message_id"])
            except ValueError:
                continue
            found.setdefault(message_id, _origin(path, line_number, root))
    return found


def _index(directory: Path) -> sqlite3.Connection:
    """Open a disposable index; the JSONL and DD.log files remain authoritative."""
    database = sqlite3.connect(directory / ".backfill-index.sqlite3")
    database.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, "
                     "inode INTEGER, offset INTEGER, lines INTEGER)")
    database.execute("CREATE TABLE IF NOT EXISTS ids (path TEXT, message_id TEXT, line INTEGER, "
                     "PRIMARY KEY (path, message_id))")
    database.execute("CREATE TABLE IF NOT EXISTS lines (path TEXT, line INTEGER, offset INTEGER, "
                     "PRIMARY KEY (path, line))")
    return database


def _refresh(database: sqlite3.Connection, path: Path, root: str | os.PathLike | None,
             *, force: bool = False) -> int:
    """Bring a disposable identity index up to the source file's current state."""
    key = str(path.relative_to(_base(root)))
    previous = database.execute("SELECT size, mtime, inode, offset, lines FROM files WHERE path=?", (key,)).fetchone()
    if not path.is_file():
        database.execute("DELETE FROM ids WHERE path=?", (key,))
        database.execute("DELETE FROM lines WHERE path=?", (key,))
        database.execute("DELETE FROM files WHERE path=?", (key,))
        return 0
    stat = path.stat()
    if not force and previous and previous[:3] == (stat.st_size, stat.st_mtime_ns, stat.st_ino):
        return previous[4]

    sidecar = path.name.endswith(_SUFFIX)
    incremental = (not force and not sidecar and previous is not None and previous[2] == stat.st_ino
                   and stat.st_size > previous[0])
    if not incremental:
        database.execute("DELETE FROM ids WHERE path=?", (key,))
        database.execute("DELETE FROM lines WHERE path=?", (key,))
    lines = previous[4] if incremental else 0
    offset = previous[3] if incremental else 0
    if sidecar:
        for lines, offset, message in _iter_sidecar(path, root):
            database.execute("INSERT OR IGNORE INTO ids VALUES (?, ?, ?)",
                             (key, _message_id(message["message_id"]), lines))
            database.execute("INSERT INTO lines VALUES (?, ?, ?)", (key, lines, offset))
        offset = path.stat().st_size
    else:
        from mods import chatlog

        with path.open("rb") as file:
            file.seek(offset)
            while True:
                start = file.tell()
                chunk = file.readline()
                if not chunk or not chunk.endswith(b"\n"):
                    offset = start
                    break
                lines += 1
                offset = file.tell()
                line = chunk.decode("utf-8")
                if line.startswith("    "):
                    continue
                head = chatlog._split_head(line.rstrip("\r\n"))
                if head is None or not head["message_id"]:
                    continue
                try:
                    message_id = _message_id(head["message_id"])
                except ValueError:
                    continue
                database.execute("INSERT OR IGNORE INTO ids VALUES (?, ?, ?)", (key, message_id, lines))
    stat = path.stat()
    database.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?)",
                     (key, stat.st_size, stat.st_mtime_ns, stat.st_ino, offset, lines))
    return lines


def _indexed_ids(database: sqlite3.Connection, path: Path, ids: set[str],
                 root: str | os.PathLike | None) -> dict[str, str]:
    _refresh(database, path, root)
    key = str(path.relative_to(_base(root)))
    found: dict[str, str] = {}
    requested = tuple(ids)
    for start in range(0, len(requested), 500):
        batch = requested[start:start + 500]
        placeholders = ",".join("?" for _ in batch)
        for message_id, line in database.execute(
                f"SELECT message_id, line FROM ids WHERE path=? AND message_id IN ({placeholders})",
                (key, *batch)):
            found[message_id] = _origin(path, line, root)
    return found


def _lookup_locked(database: sqlite3.Connection, kind: str, target: int | str,
                   ids: set[str], since: int, until: int,
                   root: str | os.PathLike | None) -> dict[str, str]:
    from mods import chatlog

    directory = _window(kind, target, root)
    seen: dict[str, str] = {}
    for pattern in ("*.log", "*" + _SUFFIX):
        for path in sorted(directory.rglob(pattern)):
            ordinary = path if pattern == "*.log" else path.with_name(path.name.removesuffix(_SUFFIX) + ".log")
            day = chatlog.day_of(ordinary)
            if day is None:
                continue
            start, end = chatlog._day_bounds(day)
            if start <= until and end > since:
                for message_id, origin in _indexed_ids(database, path, ids - seen.keys(), root).items():
                    seen.setdefault(message_id, origin)
            if len(seen) == len(ids):
                return seen
    return seen


def lookup_origins(
    kind: str, target: int | str, message_ids: Iterable[int | str], since: int, until: int,
    *, root: str | os.PathLike | None = None,
) -> dict[str, str]:
    """Return stable origins only for requested QQ ids, without a window-sized map."""
    if until < since:
        raise ValueError("回填窗口终点早于起点")
    directory = _window(kind, target, root)
    ids = {_message_id(message_id) for message_id in message_ids}
    if not ids or not directory.is_dir():
        return {}
    from mods import chatlog

    with chatlog._append_lock, _lock, closing(_index(directory)) as database, database:
        return _lookup_locked(database, kind, target, ids, since, until, root)


def index_window(
    kind: str,
    target: int | str,
    since: int,
    until: int,
    *,
    root: str | os.PathLike | None = None,
) -> dict[str, str]:
    """Index QQ ids once for a time window; reuse the returned map across pages."""
    if until < since:
        raise ValueError("回填窗口终点早于起点")
    directory = _window(kind, target, root)
    seen: dict[str, str] = {}
    if not directory.is_dir():
        return seen
    from mods import chatlog

    with chatlog._append_lock, _lock:
        for path in sorted(directory.rglob("*.log")):
            day = chatlog.day_of(path)
            if day is None:
                continue
            start, end = chatlog._day_bounds(day)
            if start <= until and end > since:
                for message_id, origin in _ordinary_ids(path, root).items():
                    seen.setdefault(message_id, origin)
        for path in sorted(directory.rglob("*" + _SUFFIX)):
            day = chatlog.day_of(path.with_name(path.name.removesuffix(_SUFFIX) + ".log"))
            if day is None:
                continue
            start, end = chatlog._day_bounds(day)
            if start <= until and end > since:
                for line, message in enumerate(_read_sidecar(path, root), 1):
                    seen.setdefault(_message_id(message["message_id"]), _origin(path, line, root))
    return seen


def append_messages(
    kind: str,
    target: int | str,
    messages: Iterable[dict[str, Any]],
    *,
    seen: MutableMapping[str, str] | None = None,
    root: str | os.PathLike | None = None,
) -> list[str]:
    """Durably append missing originals; return one existing/new origin per input.

    An ordinary DD.log is checked again under its writer lock immediately before
    each day batch, so a live event after ``index_window`` still wins overlap.
    """
    incoming = list(messages)
    keys = [_validate(message) for message in incoming]
    _window(kind, target, root)
    for message in incoming:
        _validate_window(message, kind, target)
    if not incoming:
        return []
    if seen is None:
        seen = {}
    from mods import chatlog

    result: list[str] = []
    loaded: dict[Path, tuple[dict[str, str], int]] = {}
    directory = _window(kind, target, root)
    _ensure_directory(directory)
    ids = {message_id for message_id, _, _ in keys}
    stamps = [stamp for _, stamp, _ in keys]
    with chatlog._append_lock, _lock, closing(_index(directory)) as database, database:
        window_seen = _lookup_locked(database, kind, target, ids, min(stamps), max(stamps), root)
        for message, (message_id, stamp, _sequence_number) in zip(incoming, keys):
            path = sidecar_path(kind, target, stamp, root=root)
            if path not in loaded:
                ordinary = path.with_name(path.name.removesuffix(_SUFFIX) + ".log")
                day_seen = _indexed_ids(database, ordinary, ids, root)
                for existing_id, origin in _indexed_ids(database, path, ids - day_seen.keys(), root).items():
                    day_seen.setdefault(existing_id, origin)
                loaded[path] = day_seen, _refresh(database, path, root)
            day_seen, line_count = loaded[path]
            origin = day_seen.get(message_id) or seen.get(message_id) or window_seen.get(message_id)
            if origin is None:
                _ensure_directory(path.parent)
                new_file = not path.exists()
                encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                with path.open("ab") as file:
                    offset = file.tell()
                    file.write(encoded)
                    file.flush()
                    os.fsync(file.fileno())
                if new_file:
                    _sync_directory(path.parent)
                line_count += 1
                loaded[path] = day_seen, line_count
                origin = _origin(path, line_count, root)
                day_seen[message_id] = origin
                key = str(path.relative_to(_base(root)))
                database.execute("INSERT OR IGNORE INTO ids VALUES (?, ?, ?)", (key, message_id, line_count))
                database.execute("INSERT INTO lines VALUES (?, ?, ?)", (key, line_count, offset))
                stat = path.stat()
                database.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?)",
                                 (key, stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_size, line_count))
            seen[message_id] = origin
            result.append(origin)
    return result


def _day_file(path: str | os.PathLike, root: str | os.PathLike | None) -> tuple[Path, tuple[str, ...]]:
    base = _base(root)
    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.is_relative_to(base):
        candidate = base / candidate
    try:
        relative = candidate.relative_to(base)
    except ValueError as error:
        raise ValueError("回填路径不在档案目录中") from error
    parts = relative.parts
    if (len(parts) != 4 or parts[0] not in ("group", "private") or not parts[1].isdigit()
            or re.fullmatch(r"\d{4}-\d{2}", parts[2]) is None
            or re.fullmatch(r"\d{2}\.backfill\.jsonl", parts[3]) is None
            or not candidate.resolve().is_relative_to(base.resolve())):
        raise ValueError("回填路径不是窗口日期文件")
    return candidate, parts


def _project(message: dict[str, Any], candidate: Path, line: int, parts: tuple[str, ...],
             root: str | os.PathLike | None) -> dict[str, Any]:
    _validate_window(message, parts[0], parts[1])
    record = dict(message)
    record["_source"] = "napcat_backfill"
    record["_version"] = "raw"
    record["_derived"] = []
    record["_missing"] = [] if "post_type" in record else ["post_type"]
    record["_guessed"] = []
    record["_log_origin"] = _origin(candidate, line, root)
    if "message_type" not in record:
        record["message_type"] = parts[0]
        record["_derived"].append("message_type")
    if "user_id" not in record:
        record["user_id"] = int(message["sender"]["user_id"])
        record["_derived"].append("user_id")
    if parts[0] == "group":
        if "group_id" not in record:
            record["group_id"] = int(parts[1])
            record["_derived"].append("group_id")
    elif "target_id" not in record:
        record["target_id"] = int(parts[1])
        record["_derived"].append("target_id")
    return record


def iter_day(path: str | os.PathLike, *, root: str | os.PathLike | None = None) -> Iterator[dict[str, Any]]:
    """Stream originals and stable origins from one sidecar day file."""
    candidate, parts = _day_file(path, root)
    with _lock:
        for line, _, message in _iter_sidecar(candidate, root):
            yield _project(message, candidate, line, parts, root)


def read_origin(kind: str, target: int | str, origin: str,
                *, root: str | os.PathLike | None = None) -> dict[str, Any] | None:
    """Read one sidecar origin via its derived byte offset, without loading the day."""
    source, separator, number = origin.rpartition(":")
    if not separator or not number.isdigit() or int(number) < 1:
        raise ValueError("回填来源位置无效")
    candidate, parts = _day_file(source, root)
    _window(kind, target, root)
    if parts[:2] != (kind, str(target)) or _origin(candidate, int(number), root) != origin:
        raise ValueError("回填来源窗口不一致")
    from mods import chatlog

    directory = _window(kind, target, root)
    if not directory.is_dir():
        return None
    with chatlog._append_lock, _lock, closing(_index(directory)) as database, database:
        count = _refresh(database, candidate, root)
        key = str(candidate.relative_to(_base(root)))
        row = database.execute("SELECT offset FROM lines WHERE path=? AND line=?", (key, int(number))).fetchone()
        if row is None and int(number) <= count:
            _refresh(database, candidate, root, force=True)
            row = database.execute("SELECT offset FROM lines WHERE path=? AND line=?", (key, int(number))).fetchone()
        if row is None:
            return None
        with candidate.open("rb") as file:
            file.seek(row[0])
            chunk = file.readline()
        try:
            if not chunk.endswith(b"\n"):
                raise ValueError("回填档案记录不完整")
            message = json.loads(chunk)
            _validate(message)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"回填档案损坏：{origin}") from error
        return _project(message, candidate, int(number), parts, root)


def read_day(path: str | os.PathLike, *, root: str | os.PathLike | None = None) -> list[dict[str, Any]]:
    """Materialize one sidecar day for existing range-query callers."""
    return list(iter_day(path, root=root))


def merge_records(ordinary: Iterable[dict[str, Any]], backfilled: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge oldest-first records without changing ordinary append order.

    Same-second sidecar records precede ordinary records, matching recovery
    before live intake; only sidecar peers are ordered by NapCat sequence (then
    stable origin), even across fetched pages.
    """
    additional = sorted(backfilled, key=lambda record: (
        _time(record["time"]), _sequence(record["message_seq"]), record["_log_origin"]
    ))
    result: list[dict[str, Any]] = []
    index = 0
    for record in ordinary:
        when = record.get("time")
        while index < len(additional) and when is not None and _time(additional[index]["time"]) <= when:
            result.append(additional[index])
            index += 1
        result.append(record)
    result.extend(additional[index:])
    return result
