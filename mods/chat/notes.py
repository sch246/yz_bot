"""Plain Markdown notes she keeps.

``self`` is who she is (it replaced the old persona prompt), ``global`` how
she does things (procedural memory), ``recent`` what is
going on right now (short-term memory), ``todo`` what is still open, ``awaker``
when she wakes herself, one note per person and per group what she knows and
what happened with them, and ``diary:<day|month|year>`` the diary each handoff
rewrites for its day.

WHY: 2026-10-22 维护者："能用普通文件的不搞复杂系统"。这些都是她随时能读写的普通
Markdown 文件，维护者也能用任何编辑器改。

WHY: files, not storage JSON, so the maintainer can read and fix them with any
editor while the Bot runs.  Every overwrite or deletion first copies the old
text under ``.history/``: nobody checks an edit before it lands, so a wrong one
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
_lock = threading.RLock()
_SCOPE = re.compile(r"^(self|global|recent|todo|awaker|person:\d+|group:\d+|diary:\d{4}(-\d{2}(-\d{2})?)?)$")


def _path(scope: str) -> Path:
    if not _SCOPE.fullmatch(scope):
        raise ValueError("scope 只能是 self、global、recent、todo、awaker、person:<QQ号>、group:<群号> 或 diary:<日期>")
    if ":" not in scope:
        return ROOT / f"{scope}.md"
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
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = ROOT / ".history" / relative.parent / f"{relative.stem}.{stamp}{path.suffix}"
    number = 1
    while target.exists():
        number += 1
        target = target.with_name(f"{relative.stem}.{stamp}-{number}{path.suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(path.read_bytes())


def _replace(path: Path, text: str) -> str:
    """Back up, then write or delete one file; returns the text it replaced."""
    with _lock:
        previous = path.read_text(encoding="utf-8") if path.is_file() else ""
        if previous.strip() == text.strip():
            return previous
        _backup(path)
        if not text.strip():
            if path.is_file():
                path.unlink()
            return previous
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text.strip() + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return previous


def write(scope: str, text: str) -> str:
    return _replace(_path(scope), text)


def versions(scope: str) -> list[str]:
    """Backed-up versions of one note, oldest first, as their time stamps."""
    path = _path(scope)
    relative = path.relative_to(ROOT)
    directory = ROOT / ".history" / relative.parent
    if not directory.is_dir():
        return []
    prefix = relative.stem + "."
    return sorted(item.name[len(prefix):-len(path.suffix)] for item in directory.glob(f"{relative.stem}.*{path.suffix}")
                  if item.name.startswith(prefix))


def read_version(scope: str, version: str) -> str:
    path = _path(scope)
    if not re.fullmatch(r"\d{8}-\d{6}(-\d+)?", version):
        raise ValueError("version 写 versions 列出的时间戳，如 20261022-153000")
    relative = path.relative_to(ROOT)
    target = ROOT / ".history" / relative.parent / f"{relative.stem}.{version}{path.suffix}"
    return target.read_text(encoding="utf-8") if target.is_file() else ""


# WHY: 策略文件是她在反思里能改的那一层（2026-10-22）：handoff 是她给交接写的附加说明，
# tidy 是什么时候交接的 Python 条件，changes 是每次改动的原因、预期和事后评估，
# proposals 是要维护者动手的改动（代码、醒来时带什么）。
POLICIES = ("handoff", "tidy", "changes", "proposals")


def _policy_path(name: str) -> Path:
    if name not in POLICIES:
        raise ValueError("策略文件只有 " + "、".join(POLICIES))
    path = ROOT / "policy" / (f"{name}.py" if name == "tidy" else f"{name}.md")
    old = ROOT / "policy" / "librarian.md"
    if name == "handoff" and not path.exists() and old.is_file():
        # 以前给图书管理员的附加说明，现在给交接；第一次读到时改名。
        os.replace(old, path)
    return path


def read_policy(name: str) -> str:
    path = _policy_path(name)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def write_policy(name: str, text: str) -> str:
    return _replace(_policy_path(name), text)


def append_policy(name: str, line: str) -> None:
    """Add one line at the end, without a backup.

    WHY: 维护者 2026-10-23 定：changes 是只往后加的流水账，旧内容都还在文件里；以前每追加
    一行都把整个旧文件复制进 .history，备份总量随条数平方增长（一万条约 15 GB）。整份改写
    仍走 write_policy，改前照样备份。
    """
    with _lock:
        path = _policy_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = path.read_bytes() if path.is_file() else b""
        with path.open("a", encoding="utf-8") as stream:
            stream.write(("\n" if previous and not previous.endswith(b"\n") else "") + line.strip() + "\n")


def listing(limit: int = 60) -> list[dict]:
    """Existing notes, most recently changed first, with their first line."""
    if not ROOT.is_dir():
        return []
    found = []
    for path in ROOT.rglob("*.md"):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in (".history", "diary", "policy"):
            continue
        scope = path.stem if len(relative.parts) == 1 else f"{relative.parts[0]}:{path.stem}"
        # self 总在系统提示最前面，不用列进"可以召回的东西"。
        if not _SCOPE.fullmatch(scope) or scope == "self":
            continue
        text = path.read_text(encoding="utf-8").strip()
        found.append({"scope": scope, "changed": path.stat().st_mtime,
                      "first": text.split("\n", 1)[0][:80], "chars": len(text)})
    found.sort(key=lambda item: item["changed"], reverse=True)
    return found[:limit]


def diaries(prefix: str = "") -> list[str]:
    """Diary keys (``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``), oldest first."""
    directory = ROOT / "diary"
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.md") if path.stem.startswith(prefix))
