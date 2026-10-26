"""翻阅 MC 百科（mcmod.cn），查 Minecraft 模组的资料与介绍。

## 怎么查模组（两步走）

1. 先用 `search_mc_mod("关键词")` 搜出模组列表，从返回的正文里找到那个模组对应的数字 ID（就是百科网址 `https://www.mcmod.cn/class/<ID>.html` 里的那串数字）；如果朋友直接把链接发给你了，就能省掉这一步。
2. 拿到数字 ID 后，调 `check_mod(id)` 去读那个模组的详细页面（中英文名、简介、支持哪些 MC 版本和加载器都在里面）。

**注意别瞎猜 ID**：因为返回的是网页纯文本，万一搜索结果里没直接透出数字 ID，就把搜到的模组名字报给对方确认、或者请他发个百科链接过来，**千万别自己瞎蒙一个数字去调 `check_mod`**，蒙错了只会查出一篇八竿子打不着的模组。
如果搜出来是空的，多半是译名或叫法对不上，换英文原名或者简称再试一次。这个小百科只管查模组资料，查不了服务器开没开或者正版玩家档案哦。"""

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
