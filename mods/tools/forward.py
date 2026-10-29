"""拆看别人发来的 QQ 合并转发聊天记录，或者你自己打包发一条合并转发。

## 怎么拆开看（read_forward）

聊天里看到 `[CQ:forward,id=…]`（或者你自己以前发出去的 `[CQ:json,…]` 转发卡片）时，画面上只有一个外壳，里面的长篇聊天记录得调 `read_forward(message_id)` 才能拆开看到。
拆出来的内容和里面的图片都会顺手存进宿主机的 `data/forward/` 和 `data/images/` 里，下次再翻同一个 id 直接读本地就行，不用重新联网。（顺带一提：如果要用 `.cave add` 把一条合并转发收进回声洞，直接丢给它就行，它自己会去拆，不用你先手动读一遍。）

- **看转发里的图片**：节点里的图片会自动本地化并显示为 `[图片: file:///root/bot/data/images/...]`（或 url）。想看哪张图，直接拿这个路径调 `attach_image(path)` 挂进对话或者用 `vision.ask(path, "问图内容")` 识图即可。
- **看嵌套合并转发**：如果转发里嵌套了另一层转发，外层会自动递归缩进展开；你也可以把内层卡片上显示的 id 单独传给 `read_forward(inner_id)`，单独翻看那一段。

如果遇到特别久远的旧转发，QQ 服务器可能会回"消息已过期或者为内层消息"或者给个空包，这时候再怎么重试也是拿不到的，如实告诉对方太久远打不开就好。

## 怎么打包发出去（send_forward）

想把好几句话打包成一条合并转发卡片发出去（比如整理长篇笔记或段子），用 `send_forward`。`nodes` 参数写纯文本，**一行代表一条消息**，用冒号把"发言人名字"和"内容"隔开：

    柚子: 第一条说的话
    康康: 第二条说的话

如果某一行没写冒号，就会默认挂上你自己（柚子）的名字；同一条消息里想换行就写 `\\n`，里面照常能塞 `[CQ:image,file=…]` 这样的图片码。
注意：`send_forward` 一调用，卡片当场就发出去了，拿到回执就代表已经成功送达，**千万别为了"确认一下"又调一遍**，不然群里会连着刷出两张一模一样的卡片。"""

from __future__ import annotations


def read_forward(message_id: str, limit: int = 30) -> str:
    """读取一条合并转发的节点内容，返回渲染好的多行文本。

    @param
    message_id: 转发 id，或者正文里那串 [CQ:forward,id=…]/[CQ:json,…] 卡片
    limit: 最多展开多少条节点，默认 30；转发很长时调大它可以看到全部
    """
    from mods import forward

    value = str(message_id or "").strip()
    if not value:
        return "没有给出转发 id"
    value = value.strip("\"'")
    if value.lower().startswith("id="):
        value = value[3:].strip("\"' ")
    key = forward.code_id(value) or value
    found = forward.record(key)
    if found is None:
        return f"取不到这条合并转发（{key}）：id 可能已经过期，也可能回包里没有节点"
    return forward.render(found, limit=limit)


def send_forward(nodes: str, user_id: int | None = None, group_id: int | None = None) -> str:
    """把几行文本作为一条合并转发发出去，返回发送结果。

    @param
    nodes: 节点正文，一行一条，写成 `昵称: 正文`；没有冒号就署 Bot 自己的名字，换行写 \\n
    user_id: 目标私聊 QQ 号，留空则发给当前对话
    group_id: 目标群号，留空则发给当前对话；与 user_id 同时给出时以群为准
    """
    from mods import forward

    built = forward.build(nodes)
    if not built:
        return "发送失败：nodes 里没有有效内容，每行写成 `昵称: 正文`"
    return forward.deliver(built, user_id=user_id, group_id=group_id)


__all__ = ["read_forward", "send_forward"]
