"""通过历史第三方接口查询百度百科摘要。

该工具依赖 ``api.wer.plus`` 的响应格式，不是百度官方 API。网络依赖只在
实际调用时导入和访问；接口不可用、限流或改变格式仍会让调用失败。
"""


def baidu_encyclopedia(object: str) -> str:
    """查询一个对象的百科摘要。

    @param
    object: 要查询的对象
    """
    from urllib.parse import quote

    import requests

    url = quote(
        f"https://api.wer.plus/api/dub?t={object}",
        safe=";/?:@&=+$,",
        encoding="utf-8",
    )
    response = requests.get(url, timeout=15).json()
    if response["code"] != 200:
        return "查询失败"
    return str(response["data"]["text"])


__all__ = ["baidu_encyclopedia"]
