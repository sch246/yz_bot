"""把一条命令当成某个人说的，投进任意窗口执行，并把它的输出带回给你。

- `cmds__run_command(".jrrp")` —— 以当前说话者的身份、在这个窗口里执行 `.jrrp`，
  返回值就是这条命令本来会发出来的文字。
- `cmds__run_command(".jrrp", sender="980001119")` —— 换个身份执行：写 QQ 号，
  也接受 `[CQ:at,qq=980001119]`。
- `cmds__run_command(".jrrp", target="g916083933")` —— 在别的窗口执行：`g<群号>`／`u<QQ号>`，
  留空表示当前窗口。
- `cmds__run_command(".jrrp", capture=False)` —— 不捕获：命令原样投进事件循环，它的输出
  自己作为一条消息发出去，这里只回一句"已投递"。
- `cmds__list_commands("jr")` —— 列出可用的命令名，确认一个命令存不存在、叫什么。
"""

from __future__ import annotations

import inspect
import logging
import re
import time

_log = logging.getLogger(__name__)

_match_at = re.compile(r"^\[CQ:at,qq=([0-9]+)\]$")
_match_window = re.compile(r"^([guGU]?)([0-9]+)$")
_SIGILS = (".", "#!", "!")
_ASYNC_WAIT = 5.0
_op_names: dict[str, bool] = {}


def list_commands(keyword: str = "") -> str:
    """列出当前可用的命令名，用来确认一个命令存不存在、叫什么。

    @param
    keyword: 只看名字里含这个词的命令；留空列出全部
    """
    from mods import command

    names = [name for name in command.available_commands() if keyword in name]
    if not names:
        return f"没有名字含 {keyword!r} 的命令"
    return "、".join(names)


def run_command(text: str, target: str = "", sender: str = "", capture: bool = True) -> str:
    """执行一条命令，把它的输出当文本交回来。

    @param
    text: 命令原文，例如 .jrrp、.jrxm、.answer、!ls
    target: 在哪个窗口执行：g<群号>、u<QQ号>，留空表示当前窗口
    sender: 以谁的身份执行：留空=当前说话的人，也可以写 QQ 号或 [CQ:at,qq=...]
    capture: True=输出收下来交给你，聊天里不出现；False=原样投递，输出自己冒出来
    """
    from mods import context, history, op

    command_text = text.strip()
    complaint = _complaint(command_text)
    if complaint:
        return complaint
    current = context.current() or {}
    window = _resolve_window(target, current)
    if isinstance(window, str):
        return window
    group_id, user_id = window
    initiator = history.author(current)
    executor, complaint = _resolve_sender(sender, initiator)
    if complaint:
        return complaint
    if (
        executor != initiator
        and not op.is_op(initiator)
        and op.is_op(executor)
        and _needs_op(command_text)
    ):
        return "权限不足：发起者不在 op 名单里，不能借 op 的身份执行需要 op 的命令"
    event = _event(command_text, group_id, user_id, executor)
    if not capture:
        from mods import connect

        _receipt(command_text, executor, group_id, user_id)
        connect._events.put(event)
        return f"已投递，由主循环执行：{command_text}"
    return _capture(event, command_text, executor, initiator)


def _capture(event: dict, text: str, executor, initiator) -> str:
    """跑一遍真实路由，只把它要发出去的文本收下来，不落到聊天里。"""
    from mods import bot, command, context, message

    collected: list[str] = []
    origin = context.current()
    saved = (message.send, message.sendmsg)
    message.send = _interceptor(saved[0], event, collected)
    message.sendmsg = _interceptor(saved[1], event, collected)
    try:
        context.set_current(event)
        matched = command.match(text[1:]) if text.startswith(".") else None
        if matched is None:
            _await_quiet(collected, bot._route(event))
        else:
            bot._handle_result(command.run(*matched))
    finally:
        message.send, message.sendmsg = saved
        context.set_current(origin)
    body = "\n".join(part for part in collected if part.strip())
    if not body:
        return "（这条命令没有输出）"
    if executor != initiator:
        return f"[以 {_name_of(executor)} 的身份]\n{body}"
    return body


def _interceptor(original, event: dict, collected: list):
    """拦下这次执行自己发的消息；别的线程、别的窗口照常发出去。"""
    from mods import context

    def wrapper(value, user_id=None, group_id=None, **params):
        if context.current() is event:
            collected.append(_plain(value))
            return None
        return original(value, user_id=user_id, group_id=group_id, **params)

    return wrapper


def _await_quiet(collected: list, route) -> None:
    """shell 的输出是子进程跑完才回来的，等它一会儿。"""
    if route not in ("shell", "shell-dry-run"):
        return
    deadline = time.monotonic() + _ASYNC_WAIT
    while not collected and time.monotonic() < deadline:
        time.sleep(0.05)


def _plain(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "".join(_plain(part) for part in value)
    return str(value)


def _complaint(text: str) -> str | None:
    """Reject what must not be injected, before anything is written."""
    if not text:
        return "命令为空。text 要写成一条真正的命令，例如 .jrrp"
    if not text.startswith(_SIGILS):
        return "只投递命令：text 要以 . 、 ! 或 #! 开头，普通文本会掉进聊天路由"
    if text.startswith(".") and _lookup(text) is None:
        return f"没有这个命令：{text.split(maxsplit=1)[0]}"
    return None


def _lookup(text: str):
    from mods import command

    if not text.startswith("."):
        return None
    return command.match(text[1:])


def _needs_op(text: str) -> bool:
    """这条命令要不要 op：shell 一律算；点命令看它所在模块自己有没有查权限。"""
    if text.startswith(("!", "#!")):
        return True
    matched = _lookup(text)
    if matched is None:
        return True
    name = matched[0]
    if name not in _op_names:
        _op_names[name] = _module_checks_op(name)
    return _op_names[name]


def _module_checks_op(name: str) -> bool:
    """保守判据：读不到、拿不准，都当作需要 op。"""
    from mods import command

    function = command.get(name)
    try:
        path = inspect.getsourcefile(function)
        with open(path, encoding="utf-8", errors="replace") as handle:
            source = handle.read()
    except Exception:
        return True
    return "require_op" in source or "is_op(" in source


def _resolve_sender(choice: str, initiator):
    value = (choice or "").strip()
    if not value:
        if initiator is None:
            return None, "拿不到发起者，请显式给出 sender，或在聊天里调用"
        return int(initiator), None
    at = _match_at.fullmatch(value)
    if at:
        return int(at.group(1)), None
    if value.lstrip("+-").isdigit():
        return int(value), None
    return None, f"sender 无法识别：{choice!r}。留空＝当前说话的人，或者写一个 QQ 号"


def _resolve_window(target: str, current: dict):
    """Turn ``target`` into ``(group_id, user_id)``; a string means a complaint."""
    choice = target.strip()
    if not choice:
        group_id = current.get("group_id")
        if group_id is not None:
            return int(group_id), None
        target_id = current.get("target_id")
        if target_id is None:
            return "当前不在任何窗口里，请在聊天中调用或给出 target"
        return None, int(target_id)
    matched = _match_window.fullmatch(choice)
    if matched is None:
        return f"target 无法识别：{choice!r}。用 g<群号>、u<QQ号>，或留空表示当前窗口"
    kind, digits = matched.groups()
    if kind.lower() == "g":
        return int(digits), None
    return None, int(digits)


def _name_of(user_id) -> str:
    from mods import identity

    try:
        if int(user_id) == identity.bot_id():
            return identity.bot_name()
    except Exception:
        pass
    return identity.user_names.get(int(user_id)) or f"QQ{user_id}"


def _receipt(command: str, executor, group_id, user_id) -> None:
    """在窗口里留一行谁投递了什么；发不出去不该让投递本身失败。"""
    from mods import cq, message

    destination = {"group_id": group_id} if group_id is not None else {"user_id": user_id}
    try:
        message.send(f"以 {_name_of(executor)} 的身份投递：{cq.escape(command)}", **destination)
    except Exception:
        _log.exception("投递回执发送失败：%s", command)


def _event(text: str, group_id, user_id, author) -> dict:
    """顶层 ``user_id`` 是作者（两种窗口一致）；私聊的窗口另写在 ``target_id`` 上。"""
    from mods import identity

    event = {
        "time": int(time.time()),
        "self_id": identity.bot_id(),
        "post_type": "message",
        "message_id": -time.time_ns(),
        "message": text,
        "raw_message": text,
        "sender": {"user_id": author, "nickname": _name_of(author)},
        "_injected": True,
    }
    if group_id is not None:
        event.update(
            {
                "message_type": "group",
                "sub_type": "normal",
                "group_id": group_id,
                "user_id": author,
            }
        )
    else:
        event.update({"message_type": "private", "sub_type": "friend", "user_id": author, "target_id": user_id})
    return event


__all__ = ["list_commands", "run_command"]
