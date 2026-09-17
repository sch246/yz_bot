"""把宿主机上的文件、或临时写好的文本直接发进当前聊天。

QQ 的文件通道是 `[CQ:file,file=file://绝对路径]`：本体必须在 Bot 宿主机上，不能拿
聊天里的 http(s) 链接顶替——那种先下载成本地文件再发。要发图片时 `inline=True`。

- `send_text` 只写 `data/files/`，文件名里的目录部分会被剥掉，因此不需要权限，适合
  把一段笔记、一份小统计直接丢给对方。
- `send_file` 能读宿主机上的任意路径，所以只对管理员开放。

失败时返回以"发送失败："开头的原因，照原样转告用户即可，同样的参数重试一般还是失败。
文件超过 100 MB 不会尝试上传（QQ 侧也会拒绝）。`user_id`/`group_id` 都留空就发给当前
对话，群和私聊不用自己判断。
"""

from __future__ import annotations

import os
from pathlib import Path

from mods import context, cq, message, op

# send_text 的落盘目录：用户可控的只有文件名，路径固定在这里，所以不必判权限
OUTBOX = Path("data/files")
# QQ 侧对单文件大小也有上限，超了连上传都不会开始，这里提前拦一道省一次往返
MAX_BYTES = 100 * 1024 * 1024
# 等 send() 的回执最多等这么久；超时只是慢，不代表失败，所以单独给一句提示
SEND_TIMEOUT = 25
# 只有这些后缀才允许 inline，避免把 .exe 之类当图片塞进图片通道
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _destination(user_id, group_id):
    """决定这条消息发给谁，返回可以直接展开给 message.send 的参数字典。

    显式给的 group_id 优先于 user_id；两个都没给就回落到当前对话，群聊和私聊由
    message.target 自己分辨，调用方不用关心。
    """
    if group_id is not None:
        return {"group_id": group_id}
    if user_id is not None:
        return {"user_id": user_id}
    event = context.current()
    if not event:
        raise RuntimeError("当前没有聊天上下文，无法判断发到哪里")
    return message.target(event)


def _deliver(path, inline, target):
    """把已经存在的本地文件按 CQ:file / CQ:image 送到 target，返回一句中文结果。

    两个公开函数共用这一段：写入、权限判断各管各的，真正发出去的动作都在这里，
    所以大小上限、超时提示、结果文案只需要维护一份。
    """
    # 转成绝对路径：QQ 侧只认 file:// 的绝对路径，相对路径会直接失败
    absolute = path.resolve()
    size = absolute.stat().st_size
    if size > MAX_BYTES:
        return f"发送失败：{absolute.name} 有 {size} B，超过 {MAX_BYTES} B 上限，未尝试上传"
    # 图片通道要求后缀在白名单里，否则一律老老实实当文件发
    kind = "image" if inline and absolute.suffix.lower() in IMAGE_SUFFIXES else "file"
    code = cq.dump({"type": kind, "data": {"file": f"file://{absolute}"}})
    future = message.send(code, **target)
    try:
        future.result(timeout=SEND_TIMEOUT)
    except TimeoutError:
        # 超时和失败不等价：文件可能还在后台上传，所以不写"发送失败"
        return f"已提交 {absolute.name}（{size} B），{SEND_TIMEOUT} 秒内没有收到回执，可能还在后台上传"
    except Exception as error:
        return f"发送失败：{error}"
    # 回执里带上发去了哪里，多目标调用时方便对照
    where = f"群 {target['group_id']}" if "group_id" in target else f"私聊 {target['user_id']}"
    return f"已发送 {absolute.name}（{size} B，按{kind}发送）→ {where}"


def send_file(path: str, inline: bool = False, user_id: int | None = None, group_id: int | None = None) -> str:
    """把宿主机上的一个文件发进聊天，返回发送结果；需要管理员权限。

    @param
    path: 宿主机上的文件路径，相对路径按 Bot 工作目录解析
    inline: 传 True 且后缀是常见图片时走图片通道，在聊天里直接展开；默认按文件发送
    user_id: 目标私聊 QQ 号，留空则发给当前对话
    group_id: 目标群号，留空则发给当前对话；与 user_id 同时给出时以群为准
    """
    # 能读宿主机任意路径，所以先卡权限；require_op 自己会提醒非管理员，这里只需短路
    if not op.require_op(context.current()):
        return "发送失败：发送宿主机文件需要管理员权限"
    # expanduser 支持 ~/... 这种写法；不是文件（含目录、拼错的重名）就不必往下走
    absolute = Path(path).expanduser()
    if not absolute.is_file():
        return f"发送失败：{absolute} 不是宿主机上的文件"
    try:
        target = _destination(user_id, group_id)
        return _deliver(absolute, inline, target)
    except (OSError, RuntimeError, ValueError) as error:
        return f"发送失败：{error}"


def send_text(name: str, content: str, user_id: int | None = None, group_id: int | None = None) -> str:
    """把一段文本写成 UTF-8 文件再发出去，返回发送结果；不需要管理员权限。

    @param
    name: 对方看到的文件名，目录部分会被剥掉，没有后缀时补 .txt
    content: 写进文件的文本，空字符串会被拒绝
    user_id: 目标私聊 QQ 号，留空则发给当前对话
    group_id: 目标群号，留空则发给当前对话；与 user_id 同时给出时以群为准
    """
    if not content:
        return "发送失败：content 不能为空"
    # 先把反斜杠统一成斜杠再取 basename，Windows 风格的 ../ 和 ..\\ 都进不来
    # 剥完是空串（比如传了 ".."）就退化成默认名字，绝不让用户决定写到哪里
    filename = os.path.basename(name.strip().replace("\\", "/").strip()) or "柚子的小纸条.txt"
    if not os.path.splitext(filename)[1]:
        filename += ".txt"
    try:
        target = _destination(user_id, group_id)
        OUTBOX.mkdir(parents=True, exist_ok=True)
        path = OUTBOX / filename
        path.write_text(content, encoding="utf-8")
        return _deliver(path, False, target)
    except (OSError, RuntimeError, ValueError) as error:
        return f"发送失败：{error}"


__all__ = ["send_file", "send_text"]
