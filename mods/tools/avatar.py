"""看看别人的 QQ 头像或群头像长什么样：拼出高清头像地址，想亲眼看时再交给 `attach_image`。

小提示：这里拿到的是纯粹的头像原图（`qlogo`），QQ 客户端外面套着的那层花里胡哨的"头像挂件/相框"是独立图层，不在头像原图里。尺寸一般选 `640`（高清大图）或 `100`（缩略小图）最稳。"""

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
