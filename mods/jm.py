"""Lazy jmcomic search and download command."""

import os
import threading

from jmcomic import JmOption, create_option_by_file

from mods import context, cq, message, pages, text, thread
from mods.command import command


_client = None
_client_lock = threading.Lock()


def get_client():
    global _client
    with _client_lock:
        if _client is None:
            _client = JmOption.default().new_jm_client()
        return _client


@thread.to_thread
def search(query):
    page = get_client().search_site(search_query=query, page=1)
    print(
        f"结果总数: {page.total}, 分页大小: {page.page_size}，页数: {page.page_count}",
        flush=True,
    )
    return pages.display(
        [f"[{album_id}]: {title}" for album_id, title in page]
    )


@thread.to_thread
def download(book_id, message_id):
    path = os.path.abspath(f"data/jm/{book_id}.pdf")
    if not os.path.isfile(path):
        option = create_option_by_file("data/option.yml")
        option.download_album(book_id)
    if not os.path.isfile(path):
        message.sendmsg(f'下载失败，文件"{path}"不存在')
        return None
    message.sendmsg(cq.dump({"type": "file", "data": {"file": f"file://{path}"}}))
    message.sendmsg(
        cq.dump({"type": "reply", "data": {"id": message_id}})
        + "你的本子已下载并转换为PDF，已发送给你！"
    )
    return None


def _report_download_failure(future) -> None:
    try:
        future.result()
    except Exception as error:
        message.sendmsg(f"下载失败：{type(error).__name__}: {error}")


@command
def run(body: str):
    """搜索漫画或按数字 ID 获取 PDF。

    格式：.jm search <关键词> | .jm <数字 ID>
    搜索结果支持翻页；ID 模式先回复“解析中”，下载或命中缓存后发送 PDF。
    """
    argument, remaining = text.read_params(cq.unescape(body))
    if argument == "search" and remaining.strip():
        return search(remaining.strip())
    if argument.isdigit():
        future = download(int(argument), context.current()["message_id"])
        future.add_done_callback(_report_download_failure)
        return "解析中"
    return run.__doc__
