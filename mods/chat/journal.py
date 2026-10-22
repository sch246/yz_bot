"""A readable day log of how her memory ran: one record per wake, per librarian pass, per policy edit.

WHY: 2026-10-22 the maintainer asked that she debug herself the way a person
debugs her -- which needs the same view a person has.  The oplog holds what she
did; this file holds what she was shown, what she went looking for, what the
librarian let go of and how notes changed, so "why did I forget that" can be
answered by reading one file.  Shaped like chatlog: an unindented head, an
indented body; ``⚠`` marks a line the nightly reflection should look at.
"""

from __future__ import annotations

import difflib
from pathlib import Path
import re
import threading
import time

ROOT = Path("data") / "journal"
_lock = threading.Lock()
_HEAD = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] ")
# 别人指出她忘了或重复问的说法；只是给反思看的线索，不当判决。
_FORGOT = re.compile(r"忘了|忘记|刚(才)?说过|说过了|又问|不记得|上次说")
_DIFF_LINES = 40


def _path(day: str) -> Path:
    return ROOT / f"{day}.log"


def append(head: str, body: list[str], stamp: float | None = None) -> None:
    moment = time.localtime(time.time() if stamp is None else stamp)
    lines = [f"[{time.strftime('%H:%M:%S', moment)}] {head}"]
    for line in body:
        lines.extend("    " + part for part in str(line).split("\n"))
    with _lock:
        ROOT.mkdir(parents=True, exist_ok=True)
        with _path(time.strftime("%Y-%m-%d", moment)).open("a", encoding="utf-8") as file:
            file.write("\n".join(lines) + "\n")


def records(day: str) -> list[str]:
    path = _path(day)
    if not path.is_file():
        return []
    found: list[list[str]] = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        if _HEAD.match(line):
            found.append([line])
        elif found and line:
            found[-1].append(line)
    return ["\n".join(record) for record in found]


def days() -> list[str]:
    return sorted(path.stem for path in ROOT.glob("*.log")) if ROOT.is_dir() else []


def since(stamp: float | None, limit: int) -> str:
    """Records after *stamp* (all of the last two days when None), newest kept whole.

    Older records beyond *limit* characters shrink to their head and ``⚠`` lines,
    so a busy day still fits and no warning is dropped.
    """
    start = time.time() - 2 * 86400 if stamp is None else stamp
    first = time.strftime("%Y-%m-%d", time.localtime(start))
    picked: list[tuple[str, str]] = []
    for day in days():
        if day < first:
            continue
        for record in records(day):
            moment = time.mktime(time.strptime(day + " " + record[1:9], "%Y-%m-%d %H:%M:%S"))
            if moment > start:
                picked.append((day, record))
    shown: list[str] = []
    used = 0
    for day, record in reversed(picked):
        text = f"{day} {record}"
        if used + len(text) > limit:
            lines = text.split("\n")
            text = "\n".join([lines[0], *(line for line in lines[1:] if "⚠" in line)])
        used += len(text)
        shown.append(text)
    return "\n".join(reversed(shown))


def diff(old: str, new: str) -> list[str]:
    lines = [line for line in difflib.unified_diff(old.strip().split("\n") if old.strip() else [],
                                                   new.strip().split("\n") if new.strip() else [],
                                                   lineterm="", n=0)
             if not line.startswith(("---", "+++", "@@"))]
    if len(lines) > _DIFF_LINES:
        lines = lines[:_DIFF_LINES] + [f"…（共 {len(lines)} 行改动）"]
    return lines or ["（内容没变）"]


def forgot_lines(screens: list[str]) -> list[str]:
    """Chat lines on screen where someone may be saying she forgot."""
    found = []
    for text in screens:
        for line in text.split("\n"):
            if line.startswith("    ") and _FORGOT.search(line):
                found.append(line.strip()[:80])
    return found[:5]
