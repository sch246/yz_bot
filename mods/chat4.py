"""Explicit compatibility alias for the small GPT-4-class chat entry."""

from mods import chat
from mods.command import command


MODEL = "openai/gpt-4o-mini"
LOAD_AFTER = ("chat",)


def ask():
    return chat.chat(model=MODEL)


@command
def run(body: str, model: str = MODEL):
    """使用固定兼容模型发送一次单句请求。

    格式：.chat4 <内容>
    其它提示词、图片和工具行为与 .chat 相同。
    """
    return chat.run(body, model)


def on_load(ctx) -> None:
    from mods import is_available

    if not is_available("chat"):
        raise RuntimeError("chat4 requires the available chat mod")
