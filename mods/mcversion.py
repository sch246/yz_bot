"""Minecraft version query and persisted update subscriptions."""

import logging

import requests

from mods import context, is_available, log, message, scheduler, storage, thread
from mods.command import command


LOAD_AFTER = ("scheduler", "storage")
MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest.json"

_logger = logging.getLogger(__name__)
_stream = log.stream("mc")
ver = {}
subscribes = []


def _latest():
    response = requests.get(MANIFEST_URL, timeout=20)
    response.raise_for_status()
    latest = response.json()["latest"]
    if not isinstance(latest, dict):
        raise ValueError("Minecraft version manifest latest 形状错误")
    return latest


def check_latest_version():
    _stream.info("检查 Minecraft 版本…")
    try:
        latest = _latest()
        if ver.get("snapshot") != latest["snapshot"]:
            ver.update(release=latest["release"], snapshot=latest["snapshot"])
            for destination in tuple(subscribes):
                message.send(
                    f"发现新版本:{ver['snapshot']}({ver['release']})",
                    user_id=(
                        None
                        if destination.get("group_id") is not None
                        else destination.get("user_id")
                    ),
                    group_id=destination.get("group_id"),
                )
    except Exception:
        _logger.exception("检查 Minecraft 版本失败")
    _stream.info(f"当前版本 {ver.get('snapshot', '')}({ver.get('release', '')})")


def on_load(_ctx):
    global ver, subscribes
    missing = [name for name in ("scheduler", "storage") if not is_available(name)]
    if missing:
        raise RuntimeError("mcversion 依赖模块不可用: " + ", ".join(missing))
    loaded_ver = storage.get("mcversion", "ver", dict)
    loaded_subscribes = storage.get("mcversion", "subscribes", list)
    if not isinstance(loaded_ver, dict):
        raise TypeError("mcversion.ver 必须是对象")
    if not isinstance(loaded_subscribes, list) or not all(
        isinstance(item, dict)
        and (item.get("group_id") is not None or item.get("user_id") is not None)
        for item in loaded_subscribes
    ):
        raise TypeError("mcversion.subscribes 必须是对象列表")
    loaded_ver.setdefault("snapshot", "")
    ver, subscribes = loaded_ver, loaded_subscribes
    scheduler.get_scheduler().add_job(
        check_latest_version,
        "interval",
        seconds=60,
        id="mcversion-check",
        replace_existing=True,
        max_instances=1,
    )


@command
@thread.to_thread
def run(body: str):
    """查询 Minecraft 最新版本并管理当前窗口订阅。

    格式：.mcversion [check|enable|disable]
    无参数查询 release/snapshot；check 立即检查更新，enable/disable 管理当前群或私聊提醒。后台也会定期检查。
    """
    operation = body.strip().split(maxsplit=1)[0] if body.strip() else ""
    if not operation:
        latest = _latest()
        return f"当前最新版本是{latest['release']}\n快照为{latest['snapshot']}"
    event = context.current()
    destination = {
        "group_id": event.get("group_id"),
        "user_id": event.get("user_id"),
    }
    if operation == "check":
        check_latest_version()
        return None
    if operation == "enable":
        if destination not in subscribes:
            subscribes.append(destination)
        return "已订阅更新提醒"
    if operation == "disable":
        if destination in subscribes:
            subscribes.remove(destination)
            return "已取消订阅"
        return "当前窗口未订阅"
    return run.__doc__
