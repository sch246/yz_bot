"""Daily luck score."""

import time

from mods import identity, msgs
from mods.command import command
from mods.randoms import getran, rd


大成功 = ["(๑´∀`๑)", "Ｏ(≧▽≦)Ｏ", "＼（￣︶￣）／", "(≧∇≦)/", "٩(◕‿◕｡)۶", "ヽ(✿ﾟ▽ﾟ)ノ", "(✪ω✪)"]
大失败 = ["=͟͟͞͞(꒪ᗜ꒪ ‧̣̥̇)", "( ๑ŏ ﹏ ŏ๑ )", "(ó﹏ò｡) ", "(╯°□°）╯︵ ┻━┻", "(⊙_⊙;)", "(╥_╥)", "(；一ω一||)"]
TIP = """在查看结果前，请先同意以下附加使用条款：
1. 我知晓并了解柚子的今日人品功能完全没有出Bug。
2. 柚子及它的主人不对使用本功能所间接造成的一切财产损失(如砸电脑等)等负责。"""


LOAD_AFTER = ("identity",)


def on_load(_ctx) -> None:
    from mods import is_available

    if not is_available("identity"):
        raise RuntimeError("jrrp 依赖的 identity 不可用")


@command
def run(body: str):
    """查看自己的今日人品。

    格式：.jrrp [zero]
    每日结果固定；极低结果会要求再次发送 .jrrp 确认查看，zero 用于把当天首次结果设为 0。
    """
    set_zero = body.strip() == "zero"
    if body.strip() and not set_zero:
        return run.__doc__
    date = time.strftime("%y-%m-%d")
    data = identity.getstorage()
    show = True
    if data.get("jrrp_date") != date:
        score = 0 if set_zero else rd(1, 101) - 1
        data["jrrp_"] = score
        if time.strftime("%m-%d") != "04-01":
            data["jrrp_date"] = date
        else:
            # WHY: 愚人节把上限提到一百万，并且故意不写 jrrp_date——当天可以反复刷。
            # 两件事都是玩笑的一部分：不锁定才能让那个夸张的数反复出现。
            # 这个分支跳过日期写入是有意的，不是漏了。
            score = 0 if set_zero else rd(1, 1_000_001) - 1
            data["jrrp_"] = score
        if score > 95:
            data["jrrp"] = f"{score}\n{getran(大成功)}"
        elif score < 5:
            data["jrrp"] = f"{score}\n{getran(大失败)}"
            reply = yield TIP
            show = msgs.is_msg(reply) and msgs.body(reply).strip() == ".jrrp"
        else:
            data["jrrp"] = str(score)
    if show:
        return data["jrrp"]
