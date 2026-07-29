'''复读命令'''

from mods.command import command

@command
def run(body: str):
    """复读命令后的文本。

    格式：.echo <内容>
    返回内容会去掉首尾空白。
    """
    return body.strip()
