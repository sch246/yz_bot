"""Fill the offline gap: page back through NapCat history and write what is missing into chatlog."""

from __future__ import annotations

import logging
import threading
import traceback

from mods import _napcat_history, chatlog


logger = logging.getLogger(__name__)
PAGE_SIZE = 100
OFFLINE_CALL = "离线时叫你"
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
    calls = 0

    def save_page(rows: list[dict]) -> None:
        nonlocal written, calls
        added = chatlog.insert_recovered(kind, target, rows)
        written += len(added)
        calls += _register_calls(window, added)

    try:
        result = _napcat_history.crawl_history(
            kind, int(target), call_api=call_api, save_page=save_page,
            anchor_message_id=anchor, anchor_time=anchor_time, count=PAGE_SIZE)
        if not result["anchor_found"]:
            # WHY: 若一条都没补回（written==0），说明该窗口在 NTQQ 本地无新漫游记录（静默窗口或新启动无缓存），
            # 并非真正发生丢失，记 debug；若补回了部分（written>0）但没到锚点，说明记录可能存在缺口，才报警。
            if written:
                logger.warning("离线补回 %s：远端历史到尽头也没遇到本地锚点，补回了 %d 条，再往前可能有缺口",
                               window, written)
            else:
                logger.debug("离线补回 %s：远端无新记录（未遇到本地锚点）", window)
    except _napcat_history.HistoryGap as error:
        # WHY: 若一条都没补回（written==0）且仅是窗口不可达或跳过，记 debug，避免冷窗口在启动时警告轰炸。
        if written:
            logger.warning("离线补回 %s 中断（%s），补回了 %d 条，再往前可能有缺口", window, error.reason, written)
        else:
            logger.debug("离线补回 %s 未拉取新消息（%s）", window, error.reason)
    if calls:
        from mods import chat

        threading.Thread(target=chat._agent._drive_agent, args=(None, window),
                         name="napcat-backfill-wake", daemon=True).start()
    return written


def _register_calls(window: tuple, events: list[dict]) -> int:
    """Register the recovered messages that would have woken her live as activated arrivals.

    WHY: 维护者 2026-10-23：补回里有人叫她就叫醒。判据和实时消息同一个
    （``chat.activation_signal``：at、名字开头、私聊对方发来的）；只有这些登记到达，
    别的补回消息不算未读，画面里照样能看到。唤醒原因写「离线时叫你」，她从时间也看得出
    是离线期间的。一个窗口补完才叫一次，其中几条叫她的合进同一次唤醒。
    """
    from mods import chat, context, oplog

    found = 0
    for event in events:
        if event.get("post_type") != "message" or not chat.activation_signal(event):
            continue
        with context.window_lock(window):
            arrival = oplog.arrive(window, event)
            oplog.activate(arrival, OFFLINE_CALL)
        found += 1
    return found


def start(call_api) -> None:
    """Recover every frozen window, two at a time, in the background."""
    tasks = list(_anchors)
    # WHY: 已经退出的群聊和非好友私聊在 NapCat NTQQ 中无法拉取历史（接口报 200 消息undefined不存在），
    # 在发起后台补回前比对好友和群列表过滤掉无用窗口，避免数十次无效网络请求与误报。若获取列表失败则保留原任务。
    active_windows = None
    try:
        friends_res = call_api("get_friend_list")
        groups_res = call_api("get_group_list")
        if (isinstance(friends_res, dict) and friends_res.get("retcode") == 0
                and isinstance(groups_res, dict) and groups_res.get("retcode") == 0):
            active_windows = set()
            for f in friends_res.get("data") or []:
                if isinstance(f, dict) and "user_id" in f:
                    active_windows.add(("private", int(f["user_id"])))
            for g in groups_res.get("data") or []:
                if isinstance(g, dict) and "group_id" in g:
                    active_windows.add(("group", int(g["group_id"])))
    except Exception:
        logger.debug("获取当前好友/群列表失败，回退到全量补回", exc_info=True)
    if active_windows is not None:
        tasks = [w for w in tasks if w in active_windows]
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
