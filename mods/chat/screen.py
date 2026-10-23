"""Render chat windows for the model in the chatlog's own record format.

WHY: the model reads exactly what ``chatlog/`` stores, so "read more" is reading
the same files with the same eyes; there is no second message format to keep in
step with the first.  The window and day live in the path, so a screen adds one
``# g群号 YYYY-MM-DD`` line where the path would have been.

Only ordinary ``.log`` day files are read.  Offline backfill sidecars
(``.backfill.jsonl``) are not rendered yet; a recovered gap shows up only once it
is written back as ordinary lines.
"""

from __future__ import annotations

from pathlib import Path
import time

from mods import chatlog

from .windows import label


def _day_files(window: tuple) -> list[tuple[tuple[int, int, int], Path]]:
    directory = chatlog.window_path(window[0], window[1])
    found = []
    for day in chatlog._archive_days(directory):
        path, _sidecar = chatlog._day_paths(window[0], window[1], day, None)
        if path.is_file():
            found.append((day, path))
    return found


def _record_time(day: tuple[int, int, int], head: str) -> int | None:
    match = chatlog._TIME_SUFFIX.search(head.rpartition(" | ")[0] or head)
    return chatlog._epoch(day, match) if match else None


def records(window: tuple, count: int, before: int | None = None, since: float | None = None) -> list[dict]:
    """The newest *count* records of one window, oldest first.

    *before* is an exclusive Unix time; it pages backwards through the files.
    *since* stops at day files that end before it.
    """
    if count <= 0:
        return []
    picked: list[dict] = []
    for day, path in _day_files(window):
        if since is not None and chatlog._day_bounds(day)[1] < since:
            break
        lines = path.read_text(encoding="utf-8").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        day_records = []
        for start, end in chatlog._file_records(lines):
            # WHY: `#` 开头的消息是控制与调试输出（子命令结果、traceback、`#hint` 状态），
            # 按跨模块约定不回流进模型，见 docs/llm.md「上下文如何组装」。
            if end - start > 1 and lines[start + 1].startswith("    #"):
                continue
            text = "\n".join(lines[start:end])
            stamp = _record_time(day, lines[start])
            if before is not None and stamp is not None and stamp >= before:
                continue
            day_records.append({"day": day, "time": stamp, "text": text})
        picked[:0] = day_records[-(count - len(picked)):]
        if len(picked) >= count:
            break
    return picked


def render(window: tuple, items: list[dict], seen: int | None = None) -> str:
    """Join records under day headers; mark where unseen records begin."""
    lines: list[str] = []
    current = None
    divided = seen is None
    for item in items:
        if item["day"] != current:
            current = item["day"]
            lines.append(f"# {label(window)} {current[0]:04d}-{current[1]:02d}-{current[2]:02d}")
        if not divided and item["time"] is not None and item["time"] > seen:
            lines.append("—— 以下是上次之后的新消息 ——")
            divided = True
        lines.append(item["text"])
    return "\n".join(lines)


def latest_time(items: list[dict]) -> int | None:
    stamps = [item["time"] for item in items if item["time"] is not None]
    return max(stamps) if stamps else None


def clock(stamp: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() if stamp is None else stamp))
