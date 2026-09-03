"""Regex search over the current QQ window's append-only chat log."""

import re

from mods import chatlog, cq, is_available, pages, thread
from mods.command import command


LOAD_AFTER = ("chatlog",)
PAGE_SIZE = 5


def on_load(_ctx) -> None:
    if not is_available("chatlog"):
        raise RuntimeError("search 需要已成功加载的 chatlog")


@command
@thread.to_thread
def run(body: str):
    """用正则表达式搜索当前聊天窗口的历史日志。

    格式：.search <正则表达式>
    命中的整条记录一并显示，每页 5 条，可继续发送翻页指令。
    """
    pattern = cq.unescape(body).lstrip()
    if not pattern:
        return run.__doc__
    try:
        records = chatlog.search_current(pattern)
    except re.error as error:
        return f"搜索表达式无效: {error}"
    return pages.display([cq.escape(record) for record in records], page_size=PAGE_SIZE)
