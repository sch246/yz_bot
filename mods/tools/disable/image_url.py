"""把网络图片 URL 下载并转换为本地图片 CQ 码。

下载、内容识别和临时文件生命周期继续复用 ``mods.cq.url2cq``，本模块
仅保留旧工具的一参数调用面。
"""


def url2cq(url: str) -> str:
    """把图片 URL 转换为 CQ 图片码。

    @param
    url: 图片 URL
    """
    from mods import cq

    return cq.url2cq(url)


__all__ = ["url2cq"]
