"""Shared configuration and corpus filtering for the 百变 commands."""

import os

from mods import storage


LOAD_AFTER = ("storage",)
settings: dict | None = None


def _settings() -> dict:
    if settings is None:
        raise RuntimeError("百变语料配置尚未加载")
    return settings


def source_log_dir() -> str:
    group_id = _settings().get("source_group_id")
    if isinstance(group_id, bool) or not isinstance(group_id, int) or group_id <= 0:
        raise ValueError(
            "未配置百变语料来源群：请设置 "
            "storage.get('bbxdw', 'settings')['source_group_id']"
        )
    path = os.path.join("chatlog", "group", str(group_id))
    if not os.path.isdir(path):
        raise ValueError("配置的百变语料来源群没有可用聊天日志")
    return path


def phrase_allowed(phrase: str, max_length: int) -> bool:
    excluded = _settings().get("excluded_fragments", [])
    if (
        not isinstance(excluded, list)
        or not all(isinstance(item, str) and item for item in excluded)
    ):
        raise ValueError(
            "百变语料排除词配置无效："
            "storage.get('bbxdw', 'settings')['excluded_fragments'] 必须是字符串列表"
        )
    return len(phrase) <= max_length and not any(item in phrase for item in excluded)


def on_load(_ctx) -> None:
    global settings
    from mods import is_available

    if not is_available("storage"):
        raise RuntimeError("bbx 依赖的 storage 不可用")
    settings = storage.get("bbxdw", "settings")
