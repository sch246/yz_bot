"""Bot, user, and group identity caches and storage conveniences."""

from __future__ import annotations

import os
import random
from typing import Any

from mods import INFRA
from mods import config, connect, storage


PHASE = INFRA
LOAD_AFTER = ("connect", "storage")

qq: int | None = None
name: str | None = None
nicknames: list[str] = []
names: tuple[str, ...] = ()

user_names: dict[int, str] = {}
group_user_infos: dict[int, dict[int, tuple[str, str]]] = {}
group_member_list: dict[int, list[int]] = {}


def _current() -> dict[str, Any]:
    from mods import context

    current = getattr(context, "current", None)
    if callable(current):
        return current()
    thismsg = getattr(context, "thismsg", None)
    if callable(thismsg):
        return thismsg()
    raise RuntimeError("context 未提供当前消息接口")


def bot_id() -> int:
    if qq is None:
        raise RuntimeError("Bot 身份尚未加载")
    return qq


def bot_name() -> str:
    if nicknames:
        return nicknames[0]
    if name:
        return name
    return "bot"


def update(msg: dict[str, Any]) -> None:
    sender = msg.get("sender")
    if not isinstance(sender, dict):
        return
    user_id = sender.get("user_id", msg.get("user_id"))
    if user_id is None:
        return
    user_id = int(user_id)
    nickname = sender.get("nickname")
    if nickname:
        update_user_name(user_id, str(nickname))
    group_id = msg.get("group_id")
    if group_id is not None and "title" in sender and "card" in sender:
        update_group_user_info(
            int(group_id),
            user_id,
            str(sender.get("title", "")),
            str(sender.get("card", "")),
        )


def update_user_name(user_id: int, value: str) -> None:
    user_names[int(user_id)] = value


def update_group_user_info(
    group_id: int,
    user_id: int,
    title: str,
    card: str,
) -> None:
    group_user_infos.setdefault(int(group_id), {})[int(user_id)] = (title, card)


def get_user_name(user_id: int) -> str:
    user_id = int(user_id)
    cached = user_names.get(user_id)
    if cached is not None:
        return cached
    reply = connect.call_api("get_stranger_info", user_id=user_id)
    if reply.get("retcode") == 0 and isinstance(reply.get("data"), dict):
        value = str(reply["data"].get("nickname", "[unknown]"))
        update_user_name(user_id, value)
        return value
    return "[unknown]"


def refresh_group_member_list(group_id: int) -> list[int]:
    group_id = int(group_id)
    reply = connect.call_api("get_group_member_list", group_id=group_id)
    if reply.get("retcode") != 0:
        raise RuntimeError(f"群成员列表获取失败：{reply.get('wording', reply)}")
    members = [int(member["user_id"]) for member in reply.get("data", [])]
    group_member_list[group_id] = members
    return members


def in_group(user_id: int, group_id: int, refresh: bool = True) -> bool:
    group_id = int(group_id)
    if refresh or group_id not in group_member_list:
        refresh_group_member_list(group_id)
    return int(user_id) in group_member_list[group_id]


def get_group_user_info(group_id: int, user_id: int) -> tuple[str, str]:
    group_id, user_id = int(group_id), int(user_id)
    cached = group_user_infos.get(group_id, {}).get(user_id)
    if cached is None:
        reply = connect.call_api(
            "get_group_member_info",
            group_id=group_id,
            user_id=user_id,
        )
        if reply.get("retcode") != 0 or not isinstance(reply.get("data"), dict):
            return "", "[unknown]"
        data = reply["data"]
        cached = str(data.get("title", "")), str(data.get("card", ""))
        update_group_user_info(group_id, user_id, *cached)
        if data.get("nickname"):
            update_user_name(user_id, str(data["nickname"]))
    title, card = cached
    return title, card or get_user_name(user_id)


def get_group_name(group_id: int) -> str:
    reply = connect.call_api("get_group_info", group_id=int(group_id))
    if reply.get("retcode") == 0 and isinstance(reply.get("data"), dict):
        return str(reply["data"].get("group_name", "[unknown]"))
    return "[unknown]"


def ensure_user_id(user_id: int | None = None) -> int:
    return int(_current()["user_id"] if user_id is None else user_id)


def ensure_group_id(group_id: int | None = None) -> int:
    if group_id is None:
        group_id = _current().get("group_id")
    if group_id is None:
        raise ValueError("需要在群内发送或者输入群号以调用此函数")
    return int(group_id)


def getstorage(user_id: int | None = None) -> dict[str, Any]:
    return storage.get("users", str(ensure_user_id(user_id)))


def getgroupstorage(group_id: int | None = None) -> dict[str, Any]:
    return storage.get("groups", str(ensure_group_id(group_id)))


def getname(user_id: int | None = None, group_id: int | None = None) -> str:
    msg = _current()
    user_id = int(msg["user_id"] if user_id is None else user_id)
    custom = storage.get("users", str(user_id)).get("name")
    if custom:
        return str(custom)
    if group_id is None:
        group_id = msg.get("group_id")
    if group_id is not None:
        return get_group_user_info(int(group_id), user_id)[1]
    return get_user_name(user_id)


def setname(value: str, user_id: int | None = None) -> str:
    storage.get("users", str(ensure_user_id(user_id)))["name"] = value
    return value


def getgroupname(group_id: int | None = None) -> str:
    group_id = ensure_group_id(group_id)
    custom = storage.get("groups", str(group_id)).get("name")
    return str(custom) if custom else get_group_name(group_id)


def setgroupname(value: str, group_id: int | None = None) -> str:
    storage.get("groups", str(ensure_group_id(group_id)))["name"] = value
    return value


def memberlist(group_id: int | None = None) -> list[dict[str, Any]]:
    reply = connect.call_api(
        "get_group_member_list",
        group_id=ensure_group_id(group_id),
    )
    if reply.get("retcode") != 0:
        raise RuntimeError(f"群成员列表获取失败：{reply.get('wording', reply)}")
    return list(reply.get("data", []))


def headshot_url(user_id: int | None = None) -> str:
    return f"https://q2.qlogo.cn/headimg_dl?dst_uin={ensure_user_id(user_id)}&spec=100"


def headshot(user_id: int | None = None) -> str:
    return f"[CQ:image,file={headshot_url(user_id)}]"


def _private_message(event: Any) -> bool:
    return (
        isinstance(event, dict)
        and event.get("post_type") in ("message", "message_sent")
        and "group_id" not in event
        and event.get("user_id") is not None
    )


def _receive_private(*, user_id: int | None = None, text: str | None = None) -> dict[str, Any]:
    receive = getattr(connect, "recv_msg", None)
    if not callable(receive):
        raise RuntimeError("connect 未提供首次配置所需的 recv_msg()")
    while True:
        event = receive()
        if not _private_message(event):
            continue
        if user_id is not None and int(event["user_id"]) != user_id:
            continue
        if text is not None and str(event.get("message", "")).strip() != text:
            continue
        return event


def _first_configuration(login_name: str) -> dict[str, Any]:
    # WHY: 4 位、无超时、无重试限制，都是有意的。这个验证码做的是**身份确认**而不是
    # 安全：它只在裸机首次启动、config.json 还不存在的那十几秒里有效，只出现在控制台，
    # 而正盯着控制台的主人一旦看到不是自己发的，立刻关掉就行。
    # 因此越简单越好。不要"顺手加固"成 6 位、限流或过期——那只增加复杂度，不增加任何
    # 这个场景下真实存在的保护。
    # 也不直接取第一个发消息的人：Bot 仍可能被加群/加好友而随机收到无关消息。
    code = str(random.randint(1000, 9999))
    print("未检测到 config.json，开始首次配置", flush=True)
    print("请私聊 Bot 发送下面的验证码；发送账号将成为 master：", flush=True)
    print(code, flush=True)
    owner_event = _receive_private(text=code)
    owner = int(owner_event["user_id"])
    print("验证码正确，等待设置 Bot 昵称", flush=True)
    connect.call_api(
        "send_msg",
        user_id=owner,
        message="验证码正确。\n请直接发送 Bot 昵称。",
    )
    nickname_event = _receive_private(user_id=owner)
    nickname = str(nickname_event.get("message", "")).strip() or login_name or "bot"
    return {"ops": [owner], "nicknames": [nickname]}


def on_load(_ctx: dict[str, Any] | None = None) -> None:
    global qq, name, nicknames, names
    reply = connect.call_api("get_login_info")
    if reply.get("retcode") != 0 or not isinstance(reply.get("data"), dict):
        raise RuntimeError(f"获取 Bot 登录信息失败：{reply.get('wording', reply)}")
    data = reply["data"]
    qq = int(data["user_id"])
    name = str(data.get("nickname", "bot"))

    first_configuration = not os.path.exists(config.config_file)
    if first_configuration:
        document = _first_configuration(name)
    else:
        document = config.init_or_load_config({"ops": [], "nicknames": [name]})
    config.dict_save_config(document)
    if first_configuration:
        owner = int(document["ops"][0])
        configured_nickname = str(document["nicknames"][0])
        connect.call_api(
            "send_msg",
            user_id=owner,
            message=(
                "初始化完成。\n"
                "你已成为 master。\n"
                f"Bot 昵称已设置为【{configured_nickname}】。"
            ),
        )
        print("首次配置完成，config.json 已保存", flush=True)
    loaded_nicknames = document.get("nicknames", [name])
    if not isinstance(loaded_nicknames, list):
        raise ValueError("config.nicknames 必须是列表")
    nicknames = [str(value) for value in loaded_nicknames if str(value)] or [name]
    names = tuple(dict.fromkeys([name, *nicknames]))
