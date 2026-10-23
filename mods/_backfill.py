"""Fill the offline gap: page back through NapCat history and write what is missing into chatlog."""

from __future__ import annotations

import logging
import threading
import traceback

from mods import _napcat_history, chatlog


logger = logging.getLogger(__name__)
PAGE_SIZE = 100
# 窗口 → (开机前最后一条本地消息号, 它的时间)；live 消息进来之前冻结，见 connect.on_load。
_anchors: dict[tuple, tuple[str, int | None]] = {}


def prepare() -> None:
    """Freeze each window's last local message before live ingress opens."""
    if _anchors:
        return
    for window, anchor in chatlog.freeze_boot_anchors().items():
        # WHY: 没有可靠锚点的窗口不补。锚点之前的记录没有消息号（v0）或读不出来，就判断不了
        # 补回的哪些已经在日档里，直接插进去会重复。以前这种窗口取一页最近历史写进旁档，
        # 旁档不写了以后，这一页没有去处。
        if anchor is None:
            continue
        anchor_time = None
        try:
            latest = chatlog.read_range(*window, limit=1)
            anchor_time = (latest[0].get("time") if latest and str(latest[0].get("message_id")) == str(anchor)
                           and type(latest[0].get("time")) is int else None)
        except Exception:
            traceback.print_exc()
        _anchors[window] = (anchor, anchor_time)


def recover(window: tuple, call_api) -> int:
    """Page back to the frozen anchor, inserting each page into chatlog; returns how many records were new.

    WHY: A reliable frozen anchor is followed until it is found, the upstream
    ends, or the paging chain actually fails; an arbitrary page cap would
    manufacture a gap in a long but otherwise continuous offline interval.
    A gap is only logged: the pages already written stay, and the next boot's
    anchor is the newest local message, so nothing resumes it.
    """
    anchor, anchor_time = _anchors[window]
    kind, target = window
    written = 0

    def save_page(rows: list[dict]) -> None:
        nonlocal written
        written += chatlog.insert_recovered(kind, target, rows)

    try:
        result = _napcat_history.crawl_history(
            kind, int(target), call_api=call_api, save_page=save_page,
            anchor_message_id=anchor, anchor_time=anchor_time, count=PAGE_SIZE)
        if not result["anchor_found"]:
            logger.warning("离线补回 %s：远端历史到尽头也没遇到本地锚点，补回了 %d 条，再往前可能有缺口",
                           window, written)
    except _napcat_history.HistoryGap as error:
        logger.warning("离线补回 %s 中断（%s），补回了 %d 条，再往前可能有缺口", window, error.reason, written)
    return written


def start(call_api) -> None:
    """Recover every frozen window, two at a time, in the background."""
    tasks = list(_anchors)
    task_lock = threading.Lock()

    def work() -> None:
        while True:
            with task_lock:
                if not tasks:
                    return
                window = tasks.pop()
            try:
                written = recover(window, call_api)
                if written:
                    logger.info("离线补回 %s：写进 chatlog %d 条", window, written)
            except Exception:
                traceback.print_exc()

    for index in range(min(2, len(tasks))):
        threading.Thread(target=work, name=f"napcat-backfill-{index}", daemon=True).start()
