import os

from main import storage


settings = storage.get('bbxdw', 'settings')


def source_log_dir() -> str:
    group_id = settings.get('source_group_id')
    if isinstance(group_id, bool) or not isinstance(group_id, int) or group_id <= 0:
        raise ValueError(
            "未配置百变语料来源群：请设置 "
            "storage.get('bbxdw', 'settings')['source_group_id']"
        )
    path = os.path.join('chatlog', 'group', str(group_id))
    if not os.path.isdir(path):
        raise ValueError('配置的百变语料来源群没有可用聊天日志')
    return path


def phrase_allowed(phrase: str, max_length: int) -> bool:
    excluded = settings.get('excluded_fragments', [])
    if (
        not isinstance(excluded, list)
        or not all(isinstance(item, str) and item for item in excluded)
    ):
        raise ValueError(
            "百变语料排除词配置无效："
            "storage.get('bbxdw', 'settings')['excluded_fragments'] 必须是字符串列表"
        )
    return len(phrase) <= max_length and not any(item in phrase for item in excluded)
