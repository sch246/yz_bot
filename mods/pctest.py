"""A small long-lived easter-egg command."""

from mods import context, identity
from mods.command import command


LOAD_AFTER = ("identity",)


def on_load(_ctx) -> None:
    from mods import is_available

    if not is_available("identity"):
        raise RuntimeError("pctest 依赖的 identity 不可用")


@command
def run(_body: str) -> str:
    """运行一个简单的头像回显测试。

    格式：.pctest
    返回固定测试文本和调用者头像；命令不读取参数。
    """
    return f"测试完毕，测试者为：{identity.headshot(context.current()['user_id'])}"
