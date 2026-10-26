"""Window kinds: where she can read, be woken, and talk.

WHY: 维护者 2026-10-23：输入不止 QQ 以后，群聊和私聊只是两种交互；B 站评论区、手机、
MC 服务器聊天同样是她要看、要回的地方，用同一个模型，不另造"源"。一个窗口还是
``(种类, 目标)``，记录都在 ``chatlog/<种类>/<目标>/``、格式相同，所以画面、read_log、search、
未读和唤醒不分种类；种类之间只差"怎么写标签"和"怎么发出去"，就是这张表。
见 docs/working/proposals/event-stream-core.md。

新来源登记一个种类（``kind``），有事发生时 ``chat.push`` 一条记录。QQ 的两种也在表里，
记录仍由 NapCat 事件经 ``chatlog.write`` 写入，不走 ``push``。
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Callable

from mods import identity

QQ = ("group", "private")
ME = ("me", "")
_NAME = re.compile(r"[a-z][a-z0-9_]*")
_SAY_TIMEOUT = 30.0


_TITLES = Path("data") / "storage" / "window_titles.json"


def _titles() -> dict:
    try:
        loaded = json.loads(_TITLES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def set_title(window: tuple, name: str) -> str:
    """给一个窗口起个名字（空字符串是删掉），存盘，重启还在。

    WHY: 2026-10-26 维护者：“给窗口号换名字的工具，这个比现有的还通用”——
    群、私聊、B 站评论区、手机都该能起名，不只 QQ 昵称。
    """
    key = f"{window[0]}:{window[1]}"
    loaded = _titles()
    if name.strip():
        loaded[key] = name.strip()
    else:
        loaded.pop(key, None)
    _TITLES.parent.mkdir(parents=True, exist_ok=True)
    _TITLES.write_text(json.dumps(loaded, ensure_ascii=False, indent=1), encoding="utf-8")
    return loaded.get(key, "")


def known_name(window: tuple) -> str:
    """她起的名 > 群名 > 昵称；都没有就空字符串。"""
    custom = _titles().get(f"{window[0]}:{window[1]}")
    if custom:
        return str(custom)
    kind, target = window
    try:
        if kind == "group":
            return identity.get_group_name(int(target))
        if kind == "private":
            return identity.get_user_name(int(target))
    except (TypeError, ValueError):
        return ""
    return ""


class Unconfirmed(Exception):
    """The sender cannot tell whether the text went out; she should look before resending."""


def _send_qq(window: tuple, text: str) -> str:
    """Send through NapCat and wait for the message id.

    WHY: 三类结果，不是两类。异常＝没发出去；拿到 id＝发出去了；而 `retcode == 0` 却没有
    message_id 是**第三类**——`_send_now` 此时既不抛也没有号码，消息很可能已经发出去。
    把它当失败会让模型重发一条已经在窗口里的话，当成功又会让这次发言在 oplog 里没有
    可反查的号码。所以单列成"未确认"，照常触发下一轮，由模型自己去看聊天记录。
    删除条件：send_msg 在 retcode 为 0 时被证明总是带 message_id。

    WHY: **超时归"未确认"，不归"失败"。** 等待超时说明 future 还没有结果，而不是结果是
    失败——消息可能正排在发送队列里，也可能已经发出去了。把它写成"没有发出去"是一句会
    骗到模型的断言，它会照着重发。队列上限 20、每条节流最多 0.6 秒，最坏约 12 秒，所以
    30 秒是留了一倍余量的"大概率不是排队问题"。

    WHY: 正文**不转义**。模型是 CQ 原生的一方：收到的消息原样带 CQ 码，写出去的也照原样
    发，往返因此闭合。在这里 `cq.escape` 会让它写的 at 和图片变成字面文本。
    """
    from mods import message

    destination = {"group_id" if window[0] == "group" else "user_id": window[1]}
    try:
        message_id = message.sendmsg(text, **destination).result(timeout=_SAY_TIMEOUT)
    except TimeoutError:
        raise Unconfirmed(f"等了 {_SAY_TIMEOUT:.0f} 秒还没有结果，这句话可能正在发、也可能已经发出去。") from None
    if message_id is None:
        raise Unconfirmed("对端收下了请求，却没有回一个 message_id，所以这句话可能已经发出去了。")
    return str(message_id)


# 种类 → 发送函数 send(window, text) -> 记录号；None 表示只能看。发送函数要等到知道结果才返回：
# 返回＝发出去了；抛 Unconfirmed＝可能发出去了；抛别的＝没发出去，字留在输入框里。
_kinds: dict[str, Callable[[tuple, str], str] | None] = {"group": _send_qq, "private": _send_qq, "me": None}


def kind(name: str, send: Callable[[tuple, str], str] | None = None) -> None:
    """Register a window kind; *send* makes its windows answerable, None leaves them read-only.

    Registering the same name again replaces its sender, so a reloaded module can
    register on every load.  QQ's two kinds and ``me`` are fixed.
    """
    if not _NAME.fullmatch(name) or name in (*QQ, "me", "bot"):
        raise ValueError(f"种类名要是小写字母开头的 [a-z0-9_]，且不能是 group、private、me、bot：{name}")
    _kinds[name] = send


def known(window) -> bool:
    return isinstance(window, (tuple, list)) and len(window) == 2 and window[0] in _kinds


def label(window: tuple) -> str:
    """``g123`` / ``u456`` for QQ, ``me`` for her own window, ``种类:目标`` for the rest."""
    if window[0] == "group":
        return f"g{window[1]}"
    if window[0] == "private":
        return f"u{window[1]}"
    if tuple(window) == ME:
        return "me"
    return f"{window[0]}:{window[1]}"


def title(window: tuple) -> str:
    """清单里怎么称呼一个窗口：标签加名字（她起的名、群名或昵称），认不出就只写标签。"""
    text = label(window)
    name = known_name(window)
    return f"{text}（{name}）" if name else text


def parse_target(target: str) -> tuple[str, int]:
    """Accept only an explicit QQ group or private-peer destination."""
    matched = re.fullmatch(r"([gu])([1-9][0-9]*)", str(target).strip())
    if matched is None:
        raise ValueError("目标必须是 g<群号> 或 u<私聊对端号>")
    return ("group" if matched[1] == "g" else "private"), int(matched[2])


def parse(text: str) -> tuple:
    """The window a label names; ValueError for anything unregistered or malformed."""
    value = str(text).strip()
    if matched := re.fullmatch(r"([gu])([1-9][0-9]*)", value):
        return ("group" if matched[1] == "g" else "private"), int(matched[2])
    if value == "me":
        return ME
    name, _, target = value.partition(":")
    if target and name in _kinds and name not in (*QQ, "me"):
        return name, target
    others = "、".join(f"{name}:目标" for name in _kinds if name not in (*QQ, "me"))
    raise ValueError("窗口写 g<群号>、u<私聊对端号>、me" + (f" 或 {others}" if others else ""))


def parse_all(labels) -> list[tuple]:
    """The windows among *labels* that still parse; a kind whose module is not loaded drops out."""
    found = []
    for text in labels:
        try:
            found.append(parse(text))
        except ValueError:
            pass
    return found


def sender(window: tuple) -> Callable[[tuple, str], str] | None:
    return _kinds.get(window[0])


def logged() -> list[tuple]:
    """Every window of a registered kind that has a chatlog directory."""
    from mods import chatlog

    found = list(chatlog.known_windows())
    root = Path(chatlog.rootfile)
    if (root / "me").is_dir():
        found.append(ME)
    for name in _kinds:
        if name in (*QQ, "me") or not (root / name).is_dir():
            continue
        found.extend((name, path.name) for path in (root / name).iterdir() if path.is_dir())
    return found
