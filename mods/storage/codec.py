"""Pure JSON projection encoding and deterministic sync scheduling."""

import hashlib
import json


def phase(key: tuple[str, str], window: float, now: float) -> float:
    if window <= 0:
        return now
    raw = hashlib.sha256("\0".join(key).encode()).digest()[:8]
    offset = int.from_bytes(raw, "big") / 2**64 * window
    start = now - now % window
    due = start + offset
    return due if due > now else due + window


def serialize(value) -> tuple[str, str]:
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
    return text, hashlib.sha256(normalized.encode()).hexdigest()


def digest(value) -> str:
    return serialize(value)[1]


def read_file(path: str, delete_marker: str = "DELETE") -> tuple[str, object | None]:
    with open(path, encoding="utf-8") as file:
        text = file.read()
    stripped = text.strip()
    if not stripped:
        return "empty", None
    if stripped == delete_marker:
        return "delete", None
    return "json", json.loads(text)
