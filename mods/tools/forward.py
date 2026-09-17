"""读取和发送 QQ 合并转发消息。

## 读取

聊天里出现 `[CQ:forward,id=…]` 时，正文里只有这串卡片文字，节点内容要调 `read_forward`
去取；`message_id` 给那串 CQ 或者只给 id 都行。自己发出去的转发在上下文里是一张
`[CQ:json,…]` 卡片，原样交给它也认。取回的内容落在 `data/forward/`，
图片一并本地化，所以同一个 id 再问一次是读盘，不会重新联网。

id 会过期：旧转发回"消息已过期或者为内层消息"，也有回包 retcode 为 0 却一条节点都没有
的情况。这两种照实转告用户就行，重试不会变好。

`.cave add` 一条转发时会自动走同一套取回和存档，不必先手动读一遍。

## 发送

`send_forward` 的 `nodes` 是纯文本，一行一个节点：

    柚子: 第一条
    康康: 第二条

昵称和正文用第一个 `:` 或 `：` 分开，行里没有冒号就署 Bot 自己的名字；正文要换行就写
`\n`（两个字符）。正文照常可以写 CQ 码，例如 `[CQ:image,file=…]`。

默认发到当前对话，`user_id` / `group_id` 可以指定别的窗口，同时给出时以群为准。返回值
是回执，消息那时已经发出去了——不要为了"确认"再发一遍。
"""

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
