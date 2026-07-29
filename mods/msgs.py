"""Predicates over raw OneBot event dictionaries."""

from __future__ import annotations

from mods import cq


def haskeys(value: dict, keys) -> bool:
    return all(key in value for key in keys)


evt = ["time", "self_id", "post_type"]
evt_msg = evt + ["sub_type", "message_id", "user_id", "message", "raw_message", "font", "sender"]
evt_req = evt + ["request_type"]
evt_notice = evt + ["notice_type"]
evt_meta = evt + ["meta_event_type"]


def is_evt(msg: dict) -> bool:
    return isinstance(msg, dict) and haskeys(msg, evt)


def is_heartbeat(msg: dict) -> bool:
    return isinstance(msg, dict) and msg.get("meta_event_type") == "heartbeat"


def is_connected(msg: dict) -> bool:
    return isinstance(msg, dict) and msg.get("meta_event_type") == "lifecycle" and msg.get("sub_type") == "connect"


def is_api(msg: dict) -> bool:
    return isinstance(msg, dict) and "retcode" in msg


def not_api(msg: dict) -> bool:
    return not is_api(msg)


def is_msg(msg: dict) -> bool:
    return isinstance(msg, dict) and "message" in msg


def is_group_msg(msg: dict) -> bool:
    return is_msg(msg) and msg.get("group_id") is not None


def is_friend(msg: dict) -> bool:
    return is_msg(msg) and msg.get("sub_type") == "friend"


def is_anonymous(msg: dict) -> bool:
    return is_msg(msg) and msg.get("sub_type") == "anonymous"


def is_cq(msg: dict) -> bool:
    if not is_msg(msg):
        return False
    value = msg["message"]
    return cq.find_all(value) == [value]


def is_img(msg: dict) -> bool:
    return is_cq(msg) and cq.load(msg["message"])["type"] == "image"


def is_reply(msg: dict) -> bool:
    return is_msg(msg) and (msg.get("reply") is not None or "reply_cq" in msg)


def is_req(msg: dict) -> bool:
    return isinstance(msg, dict) and "request_type" in msg


def is_friend_req(msg: dict) -> bool:
    return is_req(msg) and msg.get("request_type") == "friend"


def is_group_req(msg: dict) -> bool:
    return is_req(msg) and msg.get("request_type") == "group"


def is_notice(msg: dict) -> bool:
    return isinstance(msg, dict) and "notice_type" in msg


def is_file(msg: dict) -> bool:
    return is_cq(msg) and cq.load(msg["message"])["type"] == "file"


def is_group_file(msg: dict) -> bool:
    return is_notice(msg) and msg.get("notice_type") == "group_upload"


def is_private_file(msg: dict) -> bool:
    return is_notice(msg) and msg.get("notice_type") == "offline_file"


def is_change_admin(msg: dict) -> bool:
    return is_notice(msg) and msg.get("notice_type") == "group_admin"


def is_leave(msg: dict) -> bool:
    return is_notice(msg) and msg.get("notice_type") == "group_decrease"


def is_join(msg: dict) -> bool:
    return is_notice(msg) and msg.get("notice_type") == "group_increase"


def is_ban(msg: dict) -> bool:
    return is_notice(msg) and msg.get("notice_type") == "group_ban"


def is_group_recall(msg: dict) -> bool:
    return is_notice(msg) and msg.get("notice_type") == "group_recall"


def is_friend_recall(msg: dict) -> bool:
    return is_notice(msg) and msg.get("notice_type") == "friend_recall"


def is_recall(msg: dict) -> bool:
    return is_group_recall(msg) or is_friend_recall(msg)


def is_newfriend(msg: dict) -> bool:
    return is_notice(msg) and msg.get("notice_type") == "friend_add"


def is_card_new(msg: dict) -> bool:
    return isinstance(msg, dict) and "card_new" in msg


def is_notify(msg: dict) -> bool:
    return is_notice(msg) and msg.get("notice_type") == "notify"


def is_poke(qq=None):
    if isinstance(qq, dict):
        return is_notice(qq) and qq.get("sub_type") == "poke"

    def predicate(msg: dict) -> bool:
        return is_notice(msg) and msg.get("sub_type") == "poke" and msg.get("target_id") == qq

    return predicate


def is_lucky_king(msg: dict) -> bool:
    return is_notify(msg) and msg.get("sub_type") == "lucky_king"


def is_honor(msg: dict) -> bool:
    return isinstance(msg, dict) and "honor_type" in msg


def get_honor(honor_type: str) -> str:
    return {"talkative": "龙王", "performer": "群聊之火", "emotion": "快乐源泉"}[honor_type]


def is_client_status(msg: dict) -> bool:
    return isinstance(msg, dict) and "client" in msg


def is_essence(msg: dict) -> bool:
    return is_notice(msg) and msg.get("notice_type") == "essence"
