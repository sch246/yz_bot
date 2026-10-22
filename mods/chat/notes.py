"""Plain Markdown notes kept by the librarian: one global, one per person, one per group.

WHY: files, not storage JSON, so the maintainer can read and fix them with any
editor while the Bot runs.  Every overwrite or deletion first copies the old
text under ``.history/``: the librarian has no external judge, so a wrong edit
must stay recoverable.  A private chat has no note of its own; it is the
person's note (decided 2026-10-19).
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import threading
import time

from mods import storage


ROOT = Path("data") / "notes"
_lock = threading.Lock()
_SCOPE = re.compile(r"^(global|person:\d+|group:\d+)$")


def _path(scope: str) -> Path:
    if not _SCOPE.fullmatch(scope):
        raise ValueError("scope 只能是 global、person:<QQ号> 或 group:<群号>")
    if scope == "global":
        return ROOT / "global.md"
    kind, number = scope.split(":")
    return ROOT / kind / f"{number}.md"


def scope_of(window: tuple) -> str:
    return f"group:{window[1]}" if window[0] == "group" else f"person:{window[1]}"


def custom_name(scope: str) -> str | None:
    """WHY: `.setname` 自定义称呼不再进入画面（画面用 chatlog 的 QQ 名），改作这个人笔记的一部分。"""
    if not scope.startswith("person:"):
        return None
    value = storage.get("users", scope.split(":")[1]).get("name")
    return str(value) if value else None


def read(scope: str) -> str:
    path = _path(scope)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def render(scope: str) -> str:
    """One note as the model sees it, or empty when there is nothing to show."""
    body = read(scope).strip()
    name = custom_name(scope)
    if name:
        body = f"维护者设定的称呼：{name}\n{body}".strip()
    return f"## 笔记 {scope}\n{body}" if body else ""


def _backup(path: Path) -> None:
    if not path.is_file():
        return
    relative = path.relative_to(ROOT)
    target = ROOT / ".history" / relative.parent / f"{relative.stem}.{time.strftime('%Y%m%d-%H%M%S')}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(path.read_bytes())


def write(scope: str, text: str) -> None:
    path = _path(scope)
    with _lock:
        _backup(path)
        if not text.strip():
            if path.is_file():
                path.unlink()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".md.tmp")
        temporary.write_text(text.strip() + "\n", encoding="utf-8")
        os.replace(temporary, path)


def listing(limit: int = 60) -> list[dict]:
    """Existing notes, most recently changed first, with their first line."""
    if not ROOT.is_dir():
        return []
    found = []
    for path in ROOT.rglob("*.md"):
        relative = path.relative_to(ROOT)
        if relative.parts[0] == ".history":
            continue
        scope = "global" if relative.parts == ("global.md",) else f"{relative.parts[0]}:{path.stem}"
        if not _SCOPE.fullmatch(scope):
            continue
        text = path.read_text(encoding="utf-8").strip()
        found.append({"scope": scope, "changed": path.stat().st_mtime,
                      "first": text.split("\n", 1)[0][:80], "chars": len(text)})
    found.sort(key=lambda item: item["changed"], reverse=True)
    return found[:limit]
