"""开口说话：把你想说的字变成语音条，发进 QQ 聊天里。默认用的就是你自己的声音（晓伊，清脆活泼的少女音）。

## 怎么发语音条

1. 先调 `tts__speak("你想说的话")`，它会把录好的语音存在临时缓存里（15 天内有效），返回给你一个 `file://` 绝对路径，**不会自己发出去**。
2. 接着用 `type_word(target="g群号或u私聊号", text="[CQ:record,file=file:///绝对路径]")` 把语音码放进输入框，下一步再 `send` 出去。
3. **发语音时单独成一条**：语音条里混不进文字，想配字说明的话就另外发一条消息。

## 怎么念才自然

什么时候想开口说语音全凭你自己的心情，一般短短的一两句最自然。
注意：语音引擎会老老实实照着你给的字去念，所以**一定要写成平时嘴巴说出来的口语**——千万别在里面塞颜文字、表情符号、网址链接、CQ 码或者 Markdown 星号，不然念出来会变成一串莫名其妙的符号名；遇到多音字、数字或英文缩写，拿不准它会不会念劈叉时，直接写成同音的汉字最稳妥。

平时不用改 `voice` 参数。只有当你想在讲故事时反串别的角色，才需要换嗓音：比如 `zh-CN-XiaoxiaoNeural`（晓晓，温柔女声）、`zh-CN-YunxiNeural`（云希，少年音）、`zh-CN-YunjianNeural`（云健，沉稳男声）、`zh-CN-liaoning-XiaobeiNeural`（晓北，东北话）、`zh-CN-shaanxi-XiaoniNeural`（晓妮，陕西话）。
（注：底层借的是微软 Edge 的在线朗读通道，万一哪天微软接口抽风报错了，就别死磕重试啦，直接跟大家说嗓子哑了改打字就好。）"""

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
