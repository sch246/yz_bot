"""Regex search over the current QQ window's append-only chat log."""

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
    结果每页显示 5 行，可继续发送翻页指令。
    """
    pattern = cq.unescape(body).lstrip()
    if not pattern:
        return run.__doc__
    content = cq.escape(chatlog.search_current(pattern))
    return pages.display(content, page_size=PAGE_SIZE)
