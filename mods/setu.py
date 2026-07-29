"""On-demand image lookup through the configured public API."""

import re

import requests

from mods import cq, message, thread
from mods.command import command


API_URL = "https://api.lolicon.app/setu/v2"
LOAD_AFTER = ("image",)


def on_load(_ctx) -> None:
    from mods import is_available

    if not is_available("image"):
        raise RuntimeError("setu 依赖的 image 不可用")


def link_image(**params):
    """Return one CQ image for link actions without sending it immediately."""
    query = [("size", "original"), ("size", "regular"), *params.items()]
    response = requests.get(API_URL, params=query, timeout=20)
    response.raise_for_status()
    pictures = response.json().get("data") or []
    if not pictures:
        return None
    original = pictures[0]["urls"]["original"]
    original = re.sub(
        r"https://.+/(\d+)_p(\d+)\.(jpg|png|gif)",
        r"https://pixiv.re/\1.\3",
        original,
    )
    return f"[CQ:image,url={original}]"


@command
@thread.to_thread
def run(body: str):
    """从图片 API 获取随机图片并发送原图链接与预览图。

    格式：.setu [key=value ...]
    参数会透传给图片 API；网络调用失败会作为命令异常报告。
    """
    params = [("size", "original"), ("size", "regular")]
    for item in body.strip().split():
        key, separator, value = item.partition("=")
        if separator and key:
            params.append((key, value))
    response = requests.get(API_URL, params=params, timeout=20)
    response.raise_for_status()
    pictures = response.json().get("data")
    if not pictures:
        return "获取失败"
    urls = pictures[0]["urls"]
    original = re.sub(
        r"https://.+/(\d+)_p(\d+)\.(jpg|png|gif)",
        r"https://pixiv.re/\1.\3",
        urls["original"],
    )
    message.sendmsg(original)
    message.sendmsg(cq.url2cq(urls["regular"].replace("i.pximg.net", "i.pixiv.re")))
    return None
