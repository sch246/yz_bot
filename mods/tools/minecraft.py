"""查询 MC 百科（mcmod.cn）上的 Minecraft 模组资料。

用法是两步：先 `search_mc_mod` 用关键字搜，再用搜到的模组的 mcmod ID 调 `check_mod` 读详情。ID 就是模组页面 `https://www.mcmod.cn/class/<ID>.html` 里的那个数字；用户直接给了链接时可以跳过搜索。

两个函数返回的都是网页正文的纯文本，中英文名、简介、支持版本、加载器等信息混在一行行文字里，没有结构化字段，也不含链接地址。所以：搜索结果里如果没有出现可用的数字 ID，就把搜到的模组名反馈给用户，或请用户给出模组页面链接，不要猜一个 ID 去调 `check_mod`——猜错只会拿到别的模组或空结果。

搜索结果会截断到 1000 字符并以 `...` 结尾，说明还有更多条目，可以让关键字更具体再搜。空结果通常意味着关键字不对（试试英文原名或中文译名），而不是模组不存在。

站点访问失败或改版时会抛出异常并把错误回传，重试一次仍失败就直接告诉用户查不到。

这个模块只覆盖模组百科，查不了服务器在线状态、玩家信息、正版账号或游戏内数据。
"""

import requests


def search_mc_mod(name: str) -> str:
    """按关键字搜索 Minecraft 模组，返回搜索结果的纯文本（超过 1000 字符会截断）。

    @param
    name: 模组名关键字，中文译名或英文原名都行，越具体结果越准
    """
    from bs4 import BeautifulSoup

    response = requests.get(f"https://search.mcmod.cn/s?key={name}", timeout=15)
    result = "\n".join(item.get_text().strip() for item in BeautifulSoup(response.text, "html.parser").find_all(class_="search-result-list"))
    return result[:1000] + ("..." if len(result) > 1000 else "")


def check_mod(id: int) -> str:
    """按 mcmod ID 读取模组详情页正文，返回简介、支持版本等纯文本。

    @param
    id: mcmod 的 class ID，即 https://www.mcmod.cn/class/<ID>.html 中的数字，不要凭印象猜
    """
    from bs4 import BeautifulSoup

    response = requests.get(f"https://www.mcmod.cn/class/{id}.html", timeout=15)
    return "\n".join(item.get_text().strip() for item in BeautifulSoup(response.text, "html.parser").find_all(class_="text-area"))


__all__ = ["search_mc_mod", "check_mod"]
