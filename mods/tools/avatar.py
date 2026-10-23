"""看 QQ 头像和群头像：拼出 qlogo 图片地址，需要亲眼看时把地址交给 attach_image。

头像是头像，挂件是挂件：qlogo 返回的只是头像图本身，QQ 客户端叠上去的头像框不在里面，拿不到。
常用尺寸 100（小）和 640（大），再大的数字不一定有原图。
"""

_HEAD = "https://q2.qlogo.cn/headimg_dl?dst_uin={qq}&spec={size}"
_GROUP = "https://p.qlogo.cn/gh/{gid}/{gid}/{size}"


def avatar_url(user_id: int, size: int = 640) -> str:
    """某个 QQ 号的头像图片地址；想亲眼看就把这个地址交给 attach_image。

    @param
    user_id: QQ 号
    size: 图片边长，常用 100 或 640
    """
    return _HEAD.format(qq=int(user_id), size=int(size))


def group_avatar_url(group_id: int, size: int = 640) -> str:
    """某个群的群头像图片地址。

    @param
    group_id: 群号
    size: 图片边长，常用 100 或 640
    """
    return _GROUP.format(gid=int(group_id), size=int(size))


__all__ = ["avatar_url", "group_avatar_url"]
