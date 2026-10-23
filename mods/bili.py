"""B 站：柚子自己的账号。评论区是窗口 ``bili:<目标>``，有人回复她、@ 她时写进那个窗口并叫醒她。

WHY: 维护者 2026-10-23：接入 B 站，参考 dsh-bili-scroll（逐条读字幕原文判断、评论区互动）。
用她自己的新号，扫码登录、二维码私聊发给维护者，接口用 bilibili-api-python。按事件流提案
§3.4，一个评论区就是一个窗口：别人的回复经 ``chat.push`` 进来，她用 type_word/send 发评论；
刷首页、搜索、看详情、读字幕和评论是工具（``mods/tools/bili.py``），不进窗口。

窗口目标：视频写 BV 号；别的评论区（动态、图文、专栏……）写 ``t<类型>-<oid>``，类型就是
B 站评论接口的 type（17 动态、11 图文、12 专栏）。回复某条评论和 QQ 一样，在正文开头写
``[CQ:reply,id=<评论号>]``；记录头里的号就是评论号（rpid）。

凭证在 ``data/bili/credential.json``（权限 600，不进 git）；已读位置、她的昵称和"哪条评论
属于哪一楼"在 storage ``bili/state``。
"""

from __future__ import annotations

import asyncio
import base64
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import threading
import time

from mods import FEATURE, context, log, message, op, storage
from mods.command import command


PHASE = FEATURE
LOAD_AFTER = ("chat", "scheduler", "storage", "op")
CREDENTIAL = "data/bili/credential.json"
JOB = "bili-poll"
POLL_MINUTES = 2
# 记住"这条评论在哪一楼"的条数上限；回复楼中楼要带上楼主的号，记不住时退回把它当楼主。
_ROOTS_MAX = 5000
_REPLY = re.compile(r"^\s*\[CQ:reply,id=(\d+)\]\s*")
_stream = log.stream("bili")
_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()
_polling = threading.Lock()


def call(coroutine, timeout: float = 60.0):
    """Run one bilibili_api coroutine on this module's own event loop and wait for the result.

    WHY: 库是异步的，调用方（工具、轮询、发送）都是普通线程。库按事件循环缓存 HTTP 客户端
    （``bilibili_api.utils.network.session_pool``），每个线程各开一个循环就各留一个连不回收的
    客户端；所以全部请求排进同一个常驻循环。超时抛 ``TimeoutError``。
    """
    global _loop
    with _loop_lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, name="bili-loop", daemon=True).start()
    future = asyncio.run_coroutine_threadsafe(coroutine, _loop)
    try:
        return future.result(timeout)
    except FutureTimeout:
        future.cancel()
        raise TimeoutError(f"B 站 {timeout:.0f} 秒没有回应") from None


def _state() -> dict:
    return storage.get("bili", "state")


def _load_credential():
    from bilibili_api import Credential

    try:
        data = json.loads(Path(CREDENTIAL).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        _stream.exception("B 站凭证读不出来，按没登录处理；重新 .bili login 就能恢复")
        return None
    if not isinstance(data, dict) or not data.get("sessdata"):
        return None
    return Credential(**{key: data.get(key) for key in
                         ("sessdata", "bili_jct", "buvid3", "buvid4", "dedeuserid", "ac_time_value")})


def _save_credential(credential) -> None:
    path = Path(CREDENTIAL)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump({key: getattr(credential, key, None) for key in
                   ("sessdata", "bili_jct", "buvid3", "buvid4", "dedeuserid", "ac_time_value")}, file)
    os.replace(temporary, path)


def credential():
    """The saved login; RuntimeError when there is none."""
    found = _load_credential()
    if found is None:
        raise RuntimeError("B 站还没登录：要维护者私聊 Bot 发 .bili login 扫码")
    return found


def _me() -> str:
    state = _state()
    return f"{state.get('name') or '柚子'}({state.get('uid') or '?'})"


# --- 窗口目标 ----------------------------------------------------------------

def resource(target: str) -> tuple[int, int]:
    """``BV…`` or ``t<类型>-<oid>`` → (oid, comment type); ValueError for anything else."""
    from bilibili_api import bvid2aid

    target = str(target).strip()
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", target):
        return bvid2aid(target), 1
    if matched := re.fullmatch(r"t([1-9][0-9]*)-([1-9][0-9]*)", target):
        return int(matched[2]), int(matched[1])
    raise ValueError(f"B 站评论区写 BV 号，或 t<类型>-<oid>（动态是 t17-动态号）：{target}")


def target_of(type_: int, oid: int) -> str:
    from bilibili_api import aid2bvid

    return aid2bvid(int(oid)) if int(type_) == 1 else f"t{int(type_)}-{int(oid)}"


def remember_root(rpid, root) -> None:
    """Note which thread a comment belongs to, so replying to it can name the thread's first comment."""
    if not rpid or not root or str(rpid) == str(root):
        return
    roots = _state().setdefault("roots", {})
    roots[str(rpid)] = str(root)
    while len(roots) > _ROOTS_MAX:
        roots.pop(next(iter(roots)))


# --- 发送：她的评论 ----------------------------------------------------------

def _send(window: tuple, text: str) -> str:
    """Post *text* in the comment section *window* names; returns the new comment's rpid."""
    from bilibili_api import comment

    from mods import chatlog
    from mods.chat import windows

    oid, type_ = resource(window[1])
    parent = None
    if matched := _REPLY.match(text):
        parent = int(matched[1])
        text = text[matched.end():]
    if not text.strip():
        raise ValueError("评论正文是空的")
    root = int(_state().get("roots", {}).get(str(parent), parent)) if parent else None
    try:
        result = call(comment.send_comment(text=text, oid=oid, type_=comment.CommentResourceType(type_),
                                           root=root, parent=parent, credential=credential()))
    except TimeoutError as error:
        raise windows.Unconfirmed(f"{error}，这条评论可能已经发出去了。") from None
    rpid = str((result or {}).get("rpid_str") or (result or {}).get("rpid") or "")
    if not rpid:
        raise windows.Unconfirmed("B 站收下了，却没有回评论号，这条评论可能已经发出去了。")
    remember_root(rpid, root)
    storage.save()
    # WHY: 她自己的评论只写进记录、不登记到达。QQ 里她的话是回声事件带回来的；B 站没有
    # 回声，不写就只有对方的回复、没有她说过什么。不登记到达，因为自己的话不是"未读"。
    with context.window_lock(window):
        chatlog.append_record("bili", window[1], _me(), (f"[CQ:reply,id={parent}]" if parent else "") + text,
                              time.time(), rpid)
    return rpid


# --- 轮询：回复我的、@ 我的 ----------------------------------------------------

def _deliver(feed: str, item: dict) -> None:
    from mods import chat

    user = item.get("user") or {}
    body = item.get("item") or {}
    type_, oid = int(body.get("business_id") or 0), int(body.get("subject_id") or 0)
    if not type_ or not oid:
        _stream.info("B 站通知没有评论区，跳过：%s", body.get("uri"))
        return
    rpid = str(body.get("source_id") or "")
    remember_root(rpid, body.get("root_id"))
    parent = body.get("target_id")
    text = (f"[CQ:reply,id={parent}]" if parent else "") + str(body.get("source_content") or "")
    title = str(body.get("title") or "")
    reason = ("B 站有人回复你" if feed == "reply" else "B 站有人 @ 你") + (f"：《{title}》" if title else "")
    chat.push(("bili", target_of(type_, oid)), f"{user.get('nickname') or '?'}({user.get('mid') or '?'})",
              text, wake=reason, record_id=rpid)


def poll() -> None:
    """Fetch the newest replies and @s and push the ones after the last position into their windows.

    WHY: 第一次（还没有位置）只记下最新位置，不把账号以前的通知整批倒进来叫醒她。每次只看
    第一页（约 20 条），两分钟一轮够用；两分钟里来了一页以上，更早的那些就漏了。
    """
    from bilibili_api import session

    found = _load_credential()
    if found is None:
        return
    state = _state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("refreshed") != today and found.ac_time_value:
        if call(found.check_refresh()):
            call(found.refresh())
            _save_credential(found)
            _stream.info("B 站凭证刷新了")
        state["refreshed"] = today
    for feed, fetch, stamp in (("reply", session.get_replies, "reply_time"), ("at", session.get_at, "at_time")):
        items = (call(fetch(found)) or {}).get("items") or []
        keys = [(int(item.get(stamp) or 0), int(item.get("id") or 0)) for item in items]
        last = tuple(state[feed]) if state.get(feed) else None
        if keys:
            state[feed] = list(max(keys + ([last] if last else [])))
        if last is None:
            continue
        for key, item in sorted(zip(keys, items), key=lambda pair: pair[0]):
            if key > last:
                _deliver(feed, item)
    state.pop("error", None)
    storage.save()


def _tick() -> None:
    if not _polling.acquire(blocking=False):
        return

    def work() -> None:
        try:
            poll()
        except Exception as error:
            state = _state()
            if state.get("error") is None:
                _stream.exception("B 站轮询失败")
            state["error"] = f"{datetime.now():%m-%d %H:%M} {type(error).__name__}: {error}"
        finally:
            _polling.release()

    threading.Thread(target=work, name="bili-poll", daemon=True).start()


# --- 登录 ----------------------------------------------------------------------

def _login(user_id: int) -> str:
    from bilibili_api import user
    from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents

    login = QrCodeLogin()
    call(login.generate_qrcode())
    picture = base64.b64encode(login.get_qrcode_picture().content).decode()
    message.send(f"[CQ:image,file=base64://{picture}]", user_id=user_id)
    message.send("用柚子那个号的 B 站 App 扫这个码，确认时选长期登录。三分钟内有效。", user_id=user_id)

    def wait() -> None:
        deadline = time.time() + 180
        while time.time() < deadline:
            time.sleep(3)
            try:
                event = call(login.check_state())
            except Exception as error:
                message.send(f"查登录状态失败（{type(error).__name__}）：{error}", user_id=user_id)
                return
            if event == QrCodeLoginEvents.TIMEOUT:
                break
            if event == QrCodeLoginEvents.DONE:
                found = login.get_credential()
                _save_credential(found)
                state = _state()
                try:
                    info = call(user.get_self_info(found))
                    state.update(uid=info.get("mid"), name=info.get("name"))
                except Exception:
                    _stream.exception("登录成功，但读不到自己的昵称")
                for key in ("reply", "at", "error"):
                    state.pop(key, None)
                storage.save()
                message.send(f"B 站登录好了：{_me()}", user_id=user_id)
                return
        message.send("二维码过期了，要登录就再发一次 .bili login", user_id=user_id)

    threading.Thread(target=wait, name="bili-login", daemon=True).start()
    return "二维码私聊发给你了"


@command
def run(body: str):
    """B 站账号。

    格式：.bili 看登录状态；.bili login 扫码登录（只限管理员，二维码私聊发给你）。
    """
    event = context.current() or {}
    if body.strip() == "login":
        if not op.is_op(event):
            return "只有管理员能登录 B 站账号"
        return _login(int(event["user_id"]))
    if body.strip():
        return run.__doc__
    state = _state()
    if _load_credential() is None:
        return "B 站没登录。管理员发 .bili login 扫码"
    return (f"B 站：{_me()}" + (f"\n上次轮询出错：{state['error']}" if state.get("error") else "\n轮询正常"))


def on_load(_ctx) -> None:
    from mods import chat, scheduler

    chat.kind("bili", send=_send)
    try:
        instance = scheduler.get_scheduler()
    except RuntimeError:
        return
    instance.add_job(_tick, "interval", minutes=POLL_MINUTES, id=JOB, replace_existing=True,
                     coalesce=True, max_instances=1, next_run_time=datetime.now() + timedelta(seconds=30))
