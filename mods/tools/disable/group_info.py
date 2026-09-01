"""读取当前群的成员数量和成员资料列表。

两个函数都沿用当前消息决定群聊目标，并通过 ``mods.identity`` 请求
OneBot 的实时群成员列表；私聊中调用会由现有身份模块明确报错。
"""


def group_size() -> str:
    """获取当前群的群员数量。"""
    from mods import identity

    return str(len(identity.memberlist()))


def group_members() -> str:
    """获取当前群的群员列表。"""
    from mods import identity

    return "\n".join(
        f'{identity.getname(member["user_id"])}({member["user_id"]}) '
        f'名片:"{member["title"]}" sex:{member["sex"]}'
        for member in identity.memberlist()
    )


__all__ = ["group_size", "group_members"]
