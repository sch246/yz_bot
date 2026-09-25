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
from typing import Any, Iterable, Iterator
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


def _iter_sidecar(path: Path, root: str | os.PathLike | None, *, offset: int = 0,
                  line: int = 0) -> Iterator[tuple[int, int, dict[str, Any]]]:
    """Isolate an uncommitted final fragment; reject a bad complete line."""
    if not path.exists():
        return
    with path.open("r+b") as file:
        file.seek(offset)
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


def _index(directory: Path) -> sqlite3.Connection:
    """Open a disposable index; the JSONL and DD.log files remain authoritative."""
    database = sqlite3.connect(directory / ".backfill-index.sqlite3")
    database.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, "
                     "inode INTEGER, offset INTEGER, lines INTEGER)")
    columns = {row[1] for row in database.execute("PRAGMA table_info(ids)")}
    if not {"stamp", "seq"} <= columns:
        if columns:
            database.execute("DROP TABLE ids")
        database.execute("DELETE FROM files")
    database.execute("CREATE TABLE IF NOT EXISTS ids (path TEXT, message_id TEXT, stamp INTEGER, "
                     "seq INTEGER, line INTEGER, PRIMARY KEY (path, message_id, stamp, seq))")
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

    if not path.name.endswith(_SUFFIX):
        raise ValueError("回填身份索引只接受 sidecar 档案")
    incremental = (not force and previous is not None and previous[2] == stat.st_ino
                   and stat.st_size > previous[0])
    if not incremental:
        database.execute("DELETE FROM ids WHERE path=?", (key,))
        database.execute("DELETE FROM lines WHERE path=?", (key,))
    lines = previous[4] if incremental else 0
    offset = previous[3] if incremental else 0
    for lines, offset, message in _iter_sidecar(path, root, offset=offset, line=lines):
        database.execute("INSERT OR IGNORE INTO ids VALUES (?, ?, ?, ?, ?)",
                         (key, _message_id(message["message_id"]), _time(message["time"]),
                          _sequence(message["message_seq"]), lines))
        database.execute("INSERT INTO lines VALUES (?, ?, ?)", (key, lines, offset))
    offset = path.stat().st_size
    stat = path.stat()
    database.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?)",
                     (key, stat.st_size, stat.st_mtime_ns, stat.st_ino, offset, lines))
    return lines


def _indexed_keys(database: sqlite3.Connection, path: Path, keys: set[tuple[str, int, int]],
                  root: str | os.PathLike | None) -> dict[tuple[str, int, int], str]:
    _refresh(database, path, root)
    key = str(path.relative_to(_base(root)))
    found: dict[tuple[str, int, int], str] = {}
    by_stamp: dict[tuple[str, int], list[tuple[str, int, int]]] = {}
    for identity in keys:
        by_stamp.setdefault(identity[:2], []).append(identity)
    requested = tuple({message_id for message_id, _, _ in keys})
    for start in range(0, len(requested), 500):
        batch = requested[start:start + 500]
        placeholders = ",".join("?" for _ in batch)
        for message_id, stamp, sequence, line in database.execute(
                f"SELECT message_id, stamp, seq, line FROM ids "
                f"WHERE path=? AND message_id IN ({placeholders}) ORDER BY line",
                (key, *batch)):
            candidates = [identity for identity in by_stamp.get((message_id, stamp), ())
                          if identity[2] == sequence]
            for identity in candidates:
                found.setdefault(identity, _origin(path, line, root))
    return found


def _lookup_locked(database: sqlite3.Connection, kind: str, target: int | str,
                   keys: set[tuple[str, int, int]],
                   root: str | os.PathLike | None) -> dict[tuple[str, int, int], str]:
    from mods import chatlog

    directory = _window(kind, target, root)
    seen: dict[tuple[str, int, int], str] = {}
    since = min(stamp for _, stamp, _ in keys)
    until = max(stamp for _, stamp, _ in keys)
    for path in sorted(directory.rglob("*" + _SUFFIX)):
        ordinary = path.with_name(path.name.removesuffix(_SUFFIX) + ".log")
        day = chatlog.day_of(ordinary)
        if day is None:
            continue
        start, end = chatlog._day_bounds(day)
        if start <= until and end > since:
            for identity, origin in _indexed_keys(database, path, keys - seen.keys(), root).items():
                seen.setdefault(identity, origin)
        if len(seen) == len(keys):
            return seen
    return seen


def lookup_origin(kind: str, target: int | str, message_id: int | str, stamp: int,
                  sequence: int | None = None, *, root: str | os.PathLike | None = None) -> str | None:
    """Match a live event to one archived original, never by QQ id alone."""
    directory = _window(kind, target, root)
    path = sidecar_path(kind, target, stamp, root=root)
    if not path.is_file():
        return None
    from mods import chatlog

    with chatlog._append_lock, _lock, closing(_index(directory)) as database, database:
        _refresh(database, path, root)
        rows = database.execute("SELECT seq, line FROM ids WHERE path=? AND message_id=? AND stamp=?",
                                (str(path.relative_to(_base(root))), _message_id(message_id), _time(stamp))).fetchall()
        if sequence is not None:
            rows = [row for row in rows if row[0] == _sequence(sequence)]
        return _origin(path, rows[0][1], root) if len(rows) == 1 else None


def append_messages(
    kind: str,
    target: int | str,
    messages: Iterable[dict[str, Any]],
    *,
    root: str | os.PathLike | None = None,
) -> list[str]:
    """Durably append missing originals; return one existing/new origin per input.

    Only sidecar rows with the complete ``message_id + time + message_seq``
    identity are reused. Ordinary DD.log lacks ``message_seq`` and therefore
    never suppresses a possibly distinct remote original.
    """
    incoming = list(messages)
    keys = [_validate(message) for message in incoming]
    _window(kind, target, root)
    for message in incoming:
        _validate_window(message, kind, target)
    if not incoming:
        return []
    from mods import chatlog

    directory = _window(kind, target, root)
    identities = set(keys)
    with chatlog._window_lock(directory), chatlog._append_lock, _lock:
        _ensure_directory(directory)
        with closing(_index(directory)) as database, database:
            result: list[str] = []
            seen: dict[tuple[str, int, int], str] = {}
            loaded: dict[Path, tuple[dict[tuple[str, int, int], str], int]] = {}
            pending: dict[Path, list[tuple[tuple[str, int, int], int, bytes]]] = {}
            window_seen = _lookup_locked(database, kind, target, identities, root)
            for message, identity in zip(incoming, keys):
                message_id, stamp, sequence = identity
                path = sidecar_path(kind, target, stamp, root=root)
                if path not in loaded:
                    day_seen = _indexed_keys(database, path, identities, root)
                    loaded[path] = day_seen, _refresh(database, path, root)
                day_seen, line_count = loaded[path]
                origin = day_seen.get(identity) or seen.get(identity) or window_seen.get(identity)
                if origin is None:
                    encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                    line_count += 1
                    loaded[path] = day_seen, line_count
                    origin = _origin(path, line_count, root)
                    day_seen[identity] = origin
                    pending.setdefault(path, []).append((identity, line_count, encoded))
                seen[identity] = origin
                result.append(origin)
            for path, entries in pending.items():
                _ensure_directory(path.parent)
                offsets = []
                with path.open("ab") as file:
                    for identity, line_count, encoded in entries:
                        offsets.append((identity, line_count, file.tell()))
                        file.write(encoded)
                    file.flush()
                key = str(path.relative_to(_base(root)))
                database.executemany("INSERT OR IGNORE INTO ids VALUES (?, ?, ?, ?, ?)",
                                     ((key, *identity, line_count) for identity, line_count, _ in offsets))
                database.executemany("INSERT INTO lines VALUES (?, ?, ?)",
                                     ((key, line_count, offset) for _, line_count, offset in offsets))
                stat = path.stat()
                database.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?)",
                                 (key, stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_size, loaded[path][1]))
            # WHY: A prior attempt may have written complete bytes and then
            # failed fsync. On retry every identity already matches, so `pending`
            # is empty; sync every involved sidecar before the derived index may
            # commit and report durable success. Directory fsync is repeated too
            # because a failed first attempt cannot prove the filename durable.
            for path in loaded:
                if not path.is_file():
                    continue
                with path.open("rb") as file:
                    os.fsync(file.fileno())
                _sync_directory(path.parent)
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
