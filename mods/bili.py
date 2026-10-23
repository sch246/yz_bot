"""B 站：柚子自己的账号。评论区是窗口 ``bili:<目标>``，有人回复她、@ 她时写进那个窗口并叫醒她。

WHY: 维护者 2026-10-23：接入 B 站，参考 dsh-bili-scroll（逐条读字幕原文判断、评论区互动）。
用她自己的新号，扫码登录、二维码只发到管理员私聊（她自己也能发起），接口用 bilibili-api-python。按事件流提案
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
        raise RuntimeError("B 站还没登录：用 cmds__run_command(\".bili login\", target=\"u<管理员QQ号>\") 把二维码发给管理员扫")
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
    if "[CQ:" in text:
        raise ValueError("B 站评论只能发文字，图片和别的 CQ 码发不出去（开头的 [CQ:reply,id=…] 除外）")
    root = int(_state().get("roots", {}).get(str(parent), parent)) if parent else None
    try:
        result = call(comment.send_comment(text=text, oid=oid, type_=comment.CommentResourceType(type_),
                                           root=root, parent=parent, credential=credential()))
    except TimeoutError as error:
        raise windows.Unconfirmed(f"{error}，这条评论可能已经发出去了。") from None
    rpid = str((result or {}).get("rpid_str") or (result or {}).get("rpid") or "")
    if not rpid:
        raise windows.Unconfirmed("B 站收下了，却没有回评论号，这条评论可能已经发出去了。")
    # WHY: 拿到评论号就是已经发出去了，后面的记账失败只记日志、照常返回评论号。抛出去会被
    # send 说成"没有发出去，还在输入框里"，她再 send 一次就公开重复发了一条（2026-10-23 审查）。
    try:
        remember_root(rpid, root)
        storage.save()
        # WHY: 她自己的评论只写进记录、不登记到达。QQ 里她的话是回声事件带回来的；B 站没有
        # 回声，不写就只有对方的回复、没有她说过什么。不登记到达，因为自己的话不是"未读"。
        with context.window_lock(window):
            chatlog.append_record("bili", window[1], _me(), (f"[CQ:reply,id={parent}]" if parent else "") + text,
                                  time.time(), rpid)
    except Exception:
        _stream.exception("B 站评论 %s 已发出，本地记录失败", rpid)
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
    # WHY: 通知里只有文字（source_content），表情是 `[名字]` 文字；只发了图或大表情的那条可能是空的，
    # 空正文 push 会拒收，那一条就永远投不进去、卡住后面的通知。
    content = str(body.get("source_content") or "").strip() or "（这条没有文字，可能只有图片；要看用 bili__thread 翻原评论）"
    text = (f"[CQ:reply,id={parent}]" if parent else "") + content
    title = str(body.get("title") or "")
    reason = ("B 站有人回复你" if feed == "reply" else "B 站有人 @ 你") + (f"：《{title}》" if title else "")
    chat.push(("bili", target_of(type_, oid)), f"{user.get('nickname') or '?'}({user.get('mid') or '?'})",
              text, wake=reason, record_id=rpid)


def poll() -> None:
    """Fetch the newest replies and @s and push the ones after the last position into their windows.

    WHY: 第一次（还没有位置）只记下最新位置，不把账号以前的通知整批倒进来叫醒她。

    WHY: 每次只看第一页（约 20 条）。两分钟里来了一页以上时不往前翻、不把它们都写进本地，
    而是在她自己的 me 窗口留一句、叫醒她，让她用 ``bili__inbox`` 去 B 站消息中心翻
    （维护者 2026-10-23：漏通知不好，但不想都记在本地）。
    """
    from bilibili_api import session

    found = _load_credential()
    if found is None:
        return
    if _with_device(found):
        _save_credential(found)
    state = _state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("refreshed") != today and found.ac_time_value:
        if call(found.check_refresh()):
            call(found.refresh())
            _save_credential(found)
            _stream.info("B 站凭证刷新了")
        state["refreshed"] = today
    for feed, fetch, stamp in (("reply", session.get_replies, "reply_time"), ("at", session.get_at, "at_time")):
        data = call(fetch(found)) or {}
        items = data.get("items") or []
        keys = [(int(item.get(stamp) or 0), int(item.get("id") or 0)) for item in items]
        last = tuple(state[feed]) if state.get(feed) else None
        if last is None:
            if keys:
                state[feed] = list(max(keys))
            continue
        if keys and min(keys) > last and not (data.get("cursor") or {}).get("is_end", True):
            from mods import chat

            name = "回复" if feed == "reply" else "@"
            chat.push(chat.windows.ME, "B 站", f"B 站上次查过之后来的{name}超过一页，只有最新的 {len(items)} 条进了评论区窗口；"
                      f"更早的用 bili__inbox(\"{feed}\") 往前翻。", wake=f"B 站{name}太多")
        # WHY: 位置一条一条往前推，投递成功才推。先推到整页末尾再投，中间一条失败，它和后面
        # 没投的下次都被当成已经处理过（2026-10-23 审查）。只有写盘这类环境错误停下来等下次重试；
        # 这一条自己的数据不对（ValueError、TypeError、KeyError）重试也一样，记下来跳过，不然它
        # 卡住这一路后面的通知，整次轮询退出又连带 @ 那一路也收不到（维护者 2026-10-23 同意）。
        for key, item in sorted(zip(keys, items), key=lambda pair: pair[0]):
            if key > last:
                try:
                    _deliver(feed, item)
                except (ValueError, TypeError, KeyError):
                    _stream.exception("B 站通知 %s 投不进窗口，跳过", item.get("id"))
                state[feed] = list(key)
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

def _scanner(event: dict) -> int | None:
    """Whose private chat the login QR code may go to; None when nobody's.

    WHY: 谁扫了这个码，谁的号就成了她的号。码落到别人手里、别人一扫，她就用别人的号说话，
    所以只发到管理员的私聊：管理员自己发命令就发给管理员本人；Bot 自己是作者时（她经
    ``cmds__run_command`` 跑，维护者 2026-10-23 允许她自己发起登录），只在对端是管理员的
    私聊窗口里才发，发给那个对端。
    """
    from mods import history, identity

    author = history.author(event)
    if author is None:
        return None
    if int(author) != identity.bot_id():
        return int(author) if op.is_op(int(author)) else None
    peer = event.get("target_id") if event.get("group_id") is None else None
    return int(peer) if peer is not None and int(peer) != identity.bot_id() and op.is_op(int(peer)) else None


def _tell(text: str) -> None:
    from mods import chat

    chat.push(chat.windows.ME, "B 站登录", text, wake="B 站登录")


def _with_device(found) -> bool:
    """Give *found* a device identity (buvid3/buvid4) of its own when it lacks one; True when it changed.

    WHY: 维护者 2026-10-23：设备标识这类增强稳定性的要加。凭证里没有 buvid 时，bilibili-api
    每个进程临时生成、激活一对，重启一次就像换了一台设备，登录却还是同一个，在风控眼里不像
    真人。所以第一次拿到就存进凭证，之后一直用这一对。拿不到（网络、接口变了）只记日志：
    没有它照样能用，只是回到库的老办法。
    """
    if found.buvid3 and found.buvid4:
        return False
    from bilibili_api.utils.network import get_buvid

    try:
        buvid3, buvid4 = call(get_buvid())
    except Exception:
        _stream.exception("B 站设备标识（buvid）拿不到，先不带")
        return False
    found.buvid3, found.buvid4 = found.buvid3 or buvid3, found.buvid4 or buvid4
    return True


def adopt(found) -> str:
    """Save *found* as her login and check it by reading her own profile; returns what happened."""
    from bilibili_api import user

    _with_device(found)
    _save_credential(found)
    state = _state()
    for key in ("reply", "at", "error", "uid", "name"):
        state.pop(key, None)
    try:
        info = call(user.get_self_info(found))
        if not info.get("mid"):
            raise ValueError("资料里没有 uid，票据大概不管用")
        state.update(uid=info.get("mid"), name=info.get("name"))
    except Exception as error:
        return f"B 站登录票据存下了，但用它查自己的资料失败（{type(error).__name__}）：{error}"
    finally:
        storage.save()
    return f"B 站登录好了：{_me()}"


def use_cookie(cookie: str) -> str:
    """Take her login from a ``k=v; k=v`` cookie (her own browser's), save it and check it.

    WHY: 维护者 2026-10-23：柚子有自己的浏览器，可以在里面登 B 站、把 Cookie 取出来交给这里；
    这个号是她的，Cookie 经过她无所谓。扫码接口哪天再变，这条路不受影响，还顺带 buvid3/buvid4。
    """
    from bilibili_api import Credential

    pairs = dict(part.strip().partition("=")[::2] for part in str(cookie).split(";") if "=" in part)
    if not pairs.get("SESSDATA"):
        return "Cookie 里没有 SESSDATA，存不了：先在浏览器里登好 B 站，再用 browser__get_cookies(\"https://www.bilibili.com\") 取"
    return adopt(Credential(sessdata=pairs["SESSDATA"], bili_jct=pairs.get("bili_jct"), buvid3=pairs.get("buvid3"),
                            buvid4=pairs.get("buvid4"), dedeuserid=pairs.get("DedeUserID"),
                            ac_time_value=pairs.get("ac_time_value")))


_PASSPORT = "https://passport.bilibili.com/x/passport-login/web/qrcode/"
_BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/140.0 Safari/537.36", "Referer": "https://www.bilibili.com/"}


def _login(user_id: int) -> None:
    """Send a login QR code to *user_id* and wait up to three minutes for the scan, telling her how it went.

    Runs on its own thread: the code is an image, and ``cmds__run_command`` captures what a command
    sends from the calling thread, which would hand her the whole base64 picture as text.

    WHY: 维护者 2026-10-23：私聊里只弹二维码，别的话柚子自己说。以前码前码后各有一句模块
    写死的说明，加上她自己的一句，读起来像人机。扫没扫、登没登好、过没过期写进她的 me
    窗口叫醒她，由她决定跟对方说什么。

    WHY: 生成和轮询自己做，不用 bilibili-api 的 ``QrCodeLogin``。它只在轮询返回的 ``url``
    的查询串里找 SESSDATA，找不到也报成功；2026-10-23 设备上两次扫码都是这样，存下来的
    凭证只有刷新令牌（她自己挖出来的）。所以这里查询串和响应的 Set-Cookie 都看，拿不到
    SESSDATA 就如实说没登上。它的 TV 渠道也不能换：遇到"已扫码未确认"会直接抛错。
    """
    import io
    from urllib.parse import parse_qsl, urlsplit

    from bilibili_api import Credential
    import httpx
    import qrcode

    device = Credential()
    _with_device(device)
    try:
        # 扫码也从这台"设备"登：带着之后要存进凭证的那对 buvid，和浏览器里登录一样。
        with httpx.Client(headers=_BROWSER, timeout=15,
                          cookies={key: value for key, value in (("buvid3", device.buvid3), ("buvid4", device.buvid4))
                                   if value}) as client:
            data = client.get(_PASSPORT + "generate").json()["data"]
            picture = io.BytesIO()
            qrcode.make(data["url"]).save(picture, format="PNG")
            message.send(f"[CQ:image,file=base64://{base64.b64encode(picture.getvalue()).decode()}]", user_id=user_id)
            deadline = time.time() + 180
            while time.time() < deadline:
                time.sleep(3)
                response = client.get(_PASSPORT + "poll", params={"qrcode_key": data["qrcode_key"]})
                body = response.json()["data"]
                # 86101 还没扫，86090 扫了还没确认，86038 过期，0 登上了。
                if body["code"] == 86038:
                    break
                if body["code"] != 0:
                    continue
                cookies = dict(parse_qsl(urlsplit(body.get("url") or "").query))
                cookies.update({name: value for name, value in response.cookies.items() if value})
                if not cookies.get("SESSDATA"):
                    _stream.warning("B 站扫码确认了却没拿到 SESSDATA，拿到的字段：%s", sorted(cookies))
                    _tell("B 站扫码确认了，但 B 站没给登录票据（SESSDATA），这次没登上，凭证没存。"
                          f"拿到的字段只有：{'、'.join(sorted(cookies)) or '（没有）'}。")
                    return
                _tell(adopt(Credential(sessdata=cookies["SESSDATA"], bili_jct=cookies.get("bili_jct"),
                                       buvid3=device.buvid3, buvid4=device.buvid4,
                                       dedeuserid=cookies.get("DedeUserID"), ac_time_value=body.get("refresh_token"))))
                return
    except Exception as error:
        _tell(f"B 站登录出错了（{type(error).__name__}）：{error}，这次登录作废，要登录就再跑一次 .bili login")
        return
    _tell(f"发给 {user_id} 的 B 站登录二维码三分钟没人扫，过期了；要登录就再跑一次 .bili login")


@command
def run(body: str):
    """B 站账号。

    格式：.bili 看登录状态；.bili login 扫码登录。二维码只发到管理员私聊：管理员自己发，或者
    柚子在管理员的私聊窗口里跑这条命令。
    """
    event = context.current() or {}
    if body.strip() == "login":
        scanner = _scanner(event)
        if scanner is None:
            return "登录二维码只能发到管理员私聊：管理员自己发 .bili login，或者在管理员的私聊窗口里跑"
        threading.Thread(target=_login, args=(scanner,), name="bili-login", daemon=True).start()
        # WHY: 成功时不回任何字，谁发起都一样。以前她自己跑时回一句给她看的说明，她用
        # cmds 的 capture=false 跑，那句就原样上了屏（2026-10-23 设备上）。该知道的写在
        # 工具说明里，结果另写进她的 me 窗口。
        return None
    if body.strip():
        return run.__doc__
    state = _state()
    if _load_credential() is None:
        return "B 站没登录。管理员发 .bili login 扫码，或者在管理员的私聊窗口里跑 .bili login"
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
