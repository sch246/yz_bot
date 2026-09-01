"""提供 Minecraft Mod 的搜索与详情查询能力。"""

import requests


def search_mc_mod(name: str) -> str:
    """搜索 Minecraft Mod。

    @param
    name: Mod 名称关键字
    """
    from bs4 import BeautifulSoup

    response = requests.get(f"https://search.mcmod.cn/s?key={name}", timeout=15)
    result = "\n".join(item.get_text().strip() for item in BeautifulSoup(response.text, "html.parser").find_all(class_="search-result-list"))
    return result[:1000] + ("..." if len(result) > 1000 else "")


def check_mod(id: int) -> str:
    """按 mcmod ID 查询 Mod。

    @param
    id: mcmod class ID
    """
    from bs4 import BeautifulSoup

    response = requests.get(f"https://www.mcmod.cn/class/{id}.html", timeout=15)
    return "\n".join(item.get_text().strip() for item in BeautifulSoup(response.text, "html.parser").find_all(class_="text-area"))


__all__ = ["search_mc_mod", "check_mod"]
