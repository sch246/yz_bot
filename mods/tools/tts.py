"""把文字念成语音，发到 QQ 里就是一条语音消息。默认是你的声音：晓伊（活泼的女声）。

`tts__speak("要说的话")` 把语音存进 Bot 的临时缓存，返回 `file://` 绝对路径，不自动发送。要发就
`type_word(target="g群号或u私聊对端号", text="[CQ:record,file=file:///绝对路径]")`，下一步再 `send`。
语音单独成一条，同一条里别再带文字，要配字就另发一条。缓存闲置 15 天后可能清理，生成了就及时发。

什么时候说语音由你自己定，适合短的一两句。念的就是你给的原文：写成说出口的话，别带表情符号、
颜文字、链接、CQ 码和 Markdown，念出来会很怪；数字和英文缩写的念法可能和你想的不一样，拿不准就写成汉字。

`voice` 一般不用改。要换别的声音（比如讲故事里的角色）可以用 `zh-CN-XiaoxiaoNeural`（晓晓，温柔女声）、
`zh-CN-YunxiNeural`（云希，少年男声）、`zh-CN-YunjianNeural`（云健，成年男声）、
`zh-CN-liaoning-XiaobeiNeural`（晓北，东北话）、`zh-CN-shaanxi-XiaoniNeural`（晓妮，陕西话）。

失败时返回"语音合成失败："开头的原因。用的是微软 Edge 浏览器的在线朗读，不是正式接口，微软一改就会一直
失败；这时如实说现在发不了语音，改发文字，不要反复重试。
"""

import asyncio
from hashlib import sha256
from pathlib import Path

import edge_tts

from mods import image as image_mod


# WHY: 维护者 2026-10-23 选的：免费、不用账号和密钥的 edge-tts，音色晓伊。它是非官方接口，
# 坏了以后要换正式的（当时的备选是火山引擎豆包语音合成），只改这个函数，返回形状不变。
VOICE = "zh-CN-XiaoyiNeural"
_TIMEOUT = 60


async def _synthesize(text: str, voice: str) -> bytes:
    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk.get("type") == "audio":
            audio.extend(chunk["data"])
    return bytes(audio)


def speak(text: str, voice: str = VOICE) -> str:
    """把文字合成成语音，返回本地 file:// 路径，不自动发送。

    @param
    text: 要念的原文，写成说出口的话，不带表情符号、链接和 CQ 码
    voice: 音色，默认晓伊；一般不用改
    """
    text = str(text or "").strip()
    if not text:
        return "语音合成失败：text 不能为空"
    try:
        # WHY: 不用库自带的 stream_sync。它在线程池里跑，网络出错时异常被吞在线程里，结束标记
        # 永远不来，调用方一直卡着（edge-tts 7.3.1 源码）。这里自己跑事件循环，出错直接抛回来。
        audio = asyncio.run(asyncio.wait_for(_synthesize(text, str(voice or VOICE)), _TIMEOUT))
    except Exception as error:
        return f"语音合成失败：{type(error).__name__}: {error}"
    if not audio:
        return "语音合成失败：没有收到音频"
    # WHY: 和生图放进同一个内容寻址缓存（data/tmp_files/<sha256>.<扩展名>），图片缓存的
    # 15 天闲置清理按文件名认，语音文件就跟着一起清，不另起一套清理。
    image_mod.maybe_prune_image_cache()
    path = Path(image_mod.TEMP_PATH, sha256(audio).hexdigest() + ".mp3").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio)
    return f"已生成语音，本地路径：\n{path.as_uri()}"


__all__ = ["speak"]
