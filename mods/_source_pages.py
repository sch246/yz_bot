"""Small durable pages of archive references for a named source.

The caller owns the source journal and its page count.  Pages are numbered in
fetch order (newest page first); members within a page are already ordered
oldest to newest.  A reader reverses page order without collecting the source.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID


MAX_PAGE_BYTES = 1_048_576
MAX_PAGE_MEMBERS = 1_000
_MEMBER_KEYS = frozenset({
    "origin", "message_id", "message_seq", "time", "user_id", "group_id", "target_id", "mentioned",
})


class IncompletePageError(RuntimeError):
    """An expected page has only an uncommitted temporary file."""


def _identity(source_uuid: str | UUID, page_number: int) -> tuple[str, str]:
    source = UUID(str(source_uuid)).hex
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 0:
        raise ValueError("page_number must be a nonnegative integer")
    return source, f"{source}-{page_number:08d}.json"


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


def _member(value: dict) -> dict:
    if not isinstance(value, dict) or not {"origin", "message_id"} <= value.keys():
        raise ValueError("each page member needs origin and message_id")
    if value.keys() - _MEMBER_KEYS:
        raise ValueError("page members may contain only archive reference metadata")
    origin = value["origin"]
    message_id = value["message_id"]
    if not isinstance(origin, str) or not origin:
        raise ValueError("origin must be a nonempty archive location")
    if isinstance(message_id, bool) or not isinstance(message_id, (int, str)) or message_id == "":
        raise ValueError("message_id must be a QQ message identifier")
    if "mentioned" in value and not isinstance(value["mentioned"], bool):
        raise ValueError("mentioned must be a boolean")
    for key in value.keys() - {"origin", "message_id", "mentioned"}:
        if isinstance(value[key], bool) or not isinstance(value[key], int):
            raise ValueError(f"{key} must be an integer")
    return dict(value)


def _page_bytes(source: str, page_number: int, members: Iterable[dict], cursor: str) -> bytes:
    if not isinstance(cursor, str) or not cursor.isascii() or not cursor.isdecimal():
        raise ValueError("page cursor must be a decimal message_seq")
    page_members = []
    for member in members:
        if len(page_members) == MAX_PAGE_MEMBERS:
            raise ValueError("page has too many members")
        page_members.append(_member(member))
    data = json.dumps(
        {"source": source, "page": page_number, "cursor": cursor, "members": page_members},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    if len(data) > MAX_PAGE_BYTES:
        raise ValueError("page is too large")
    return data


def write_page(
    root: str | os.PathLike, source_uuid: str | UUID, page_number: int,
    members: Iterable[dict], *, cursor: str,
) -> Path:
    """Commit one page once, or accept an identical retry after a crash.

    A differing retry is an error; committed pages are never overwritten.  No
    message body is accepted, and no source-wide member list is retained.
    """
    source, filename = _identity(source_uuid, page_number)
    data = _page_bytes(source, page_number, members, cursor)
    directory = Path(root)
    _ensure_directory(directory)
    destination = directory / filename
    descriptor, temporary = tempfile.mkstemp(prefix=f".{filename}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            with destination.open("rb") as stream:
                previous = stream.read(MAX_PAGE_BYTES + 1)
            if previous != data:
                raise ValueError(f"page {page_number} was already committed with different contents")
        else:
            _sync_directory(directory)
    finally:
        os.unlink(temporary)
        _sync_directory(directory)
    return destination


def _pending_path(root: str | os.PathLike, source_uuid: str | UUID,
                  page_number: int) -> Path:
    _source, filename = _identity(source_uuid, page_number)
    return Path(root) / (filename + ".pending")


def write_pending(root: str | os.PathLike, source_uuid: str | UUID,
                  page_number: int, rows: list[dict]) -> Path:
    """Durably stage one raw remote page before writing its archive records."""
    if not isinstance(rows, list) or not rows or len(rows) > MAX_PAGE_MEMBERS or any(
            not isinstance(row, dict) for row in rows):
        raise ValueError("pending page must contain a bounded nonempty list of remote rows")
    directory = Path(root)
    _ensure_directory(directory)
    destination = _pending_path(root, source_uuid, page_number)
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix="." + destination.name + ".",
                                              suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.read_bytes() != payload:
                raise ValueError("pending page differs from its durable retry")
        else:
            _sync_directory(directory)
    finally:
        os.unlink(temporary)
        _sync_directory(directory)
    return destination


def read_pending(root: str | os.PathLike, source_uuid: str | UUID,
                 page_number: int) -> list[dict] | None:
    """Return only the one unfinished page, never a source-wide body list."""
    path = _pending_path(root, source_uuid, page_number)
    try:
        with path.open(encoding="utf-8") as stream:
            rows = json.load(stream)
    except FileNotFoundError:
        return None
    if not isinstance(rows, list) or not rows or len(rows) > MAX_PAGE_MEMBERS or any(
            not isinstance(row, dict) for row in rows):
        raise ValueError("pending page is corrupt")
    return rows


def delete_pending(root: str | os.PathLike, source_uuid: str | UUID,
                   page_number: int) -> None:
    """Discard a staged body only after its reference page is journaled."""
    path = _pending_path(root, source_uuid, page_number)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _sync_directory(path.parent)


def read_page_info(root: str | os.PathLike, source_uuid: str | UUID,
                   page_number: int) -> tuple[str, list[dict]]:
    """Read one bounded committed page and its durable remote cursor."""
    source, filename = _identity(source_uuid, page_number)
    directory = Path(root)
    try:
        with (directory / filename).open("rb") as stream:
            data = stream.read(MAX_PAGE_BYTES + 1)
    except FileNotFoundError:
        if any(directory.glob(f".{filename}.*.tmp")):
            raise IncompletePageError(f"page {page_number} has an uncommitted temporary file") from None
        raise
    if len(data) > MAX_PAGE_BYTES:
        raise ValueError(f"page {page_number} is too large")
    page = json.loads(data)
    if (not isinstance(page, dict) or set(page) != {"source", "page", "cursor", "members"}
            or page["source"] != source or page["page"] != page_number):
        raise ValueError(f"page {page_number} has invalid identity")
    cursor = page["cursor"]
    if not isinstance(cursor, str) or not cursor.isascii() or not cursor.isdecimal():
        raise ValueError(f"page {page_number} has invalid cursor")
    members = page.get("members")
    if not isinstance(members, list) or len(members) > MAX_PAGE_MEMBERS:
        raise ValueError(f"page {page_number} has invalid members")
    return cursor, [_member(member) for member in members]


def read_page(root: str | os.PathLike, source_uuid: str | UUID, page_number: int) -> list[dict]:
    """Read one bounded page of archive references."""
    return read_page_info(root, source_uuid, page_number)[1]


def iter_oldest(
    root: str | os.PathLike, source_uuid: str | UUID, page_count: int,
) -> Iterator[dict]:
    """Yield oldest to newest, keeping at most one page of members in memory."""
    if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 0:
        raise ValueError("page_count must be a nonnegative integer")
    for page_number in range(page_count - 1, -1, -1):
        yield from read_page(root, source_uuid, page_number)
