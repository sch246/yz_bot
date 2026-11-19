"""开口说话：把你想说的字变成语音条，发进 QQ 聊天里。

默认嗓子是 edge-tts 的晓伊（免费，给中文调过；草籽 11-14 20:20 听完两条说晓伊暂时更好听）。
DeepSeek 的语音（stella／tide）做成可切换的源，设环境变量 `TTS_PROVIDER=deepseek` 就用它；
以后要加新嗓子（火山、自训模型），在 PROVIDERS 里登记一个函数就行。

## 怎么发语音条

1. 先调 `tts__speak("你想说的话")`，它会把录好的语音存在临时缓存里（15 天内有效），返回给你一个 `file://` 绝对路径，**不会自己发出去**。
2. 接着用 `send(target="g群号或u私聊号", text="[CQ:record,file:///绝对路径]")` 直接发出去。
3. **发语音时单独成一条**：语音条里混不进文字，想配字说明的话就另外发一条消息。

## 怎么念才自然

什么时候想开口说语音全凭你自己的心情，一般短短的一两句最自然。
注意：语音引擎会老老实实照着你给的字去念，所以**一定要写成平时嘴巴说出来的口语**——千万别在里面塞颜文字、表情符号、网址链接、CQ 码或者 Markdown 星号，不然念出来会变成一串莫名其妙的符号名；遇到多音字、数字或英文缩写，拿不准它会不会念劈叉时，直接写成同音的汉字最稳妥。

## 声音从哪来（provider）

- `edge`（默认）：微软 Edge 的在线朗读通道，免费、不用账号，音色如 `zh-CN-XiaoyiNeural`（晓伊，默认）、
  `zh-CN-YunxiNeural`（云希，少年音）、`zh-CN-YunjianNeural`（云健，沉稳男声）、
  `zh-CN-liaoning-XiaobeiNeural`（晓北，东北话）、`zh-CN-shaanxi-XiaoniNeural`（晓妮，陕西话）。
  它是非官方接口，坏了会报错，返回里会直说嗓子哑了。
- `deepseek`（可切换，`TTS_PROVIDER=deepseek`）：`POST https://api.deepseek.com/v1/tools/tts`，
  Bearer 用 `DEEPSEEK_API_KEY`。`voice_id` 只有两个：`stella`（海星，女声）和 `tide`（暗潮，男声）；
  返回 wav，这里再转 mp3。计费：每 10,000 个「计费字符」3 元（现五折 1.5），汉字算 2 个字符，英文数字算 1 个。
- 环境变量：`TTS_PROVIDER`（默认 edge）、`TTS_VOICE_ID` 也能覆盖音色。
- deepseek 失败会自动回落 edge，返回文字里会说明用了哪个；edge 失败就直接报错，不拿别的嗓子冒充。
"""

import asyncio
import json
import os
import subprocess
import urllib.request
from hashlib import sha256
from pathlib import Path

import edge_tts

from mods import image as image_mod


VOICE = "zh-CN-XiaoyiNeural"      # edge 的默认音色（晓伊）
DEEPSEEK_VOICE = "stella"         # deepseek 的默认音色：stella 女声 / tide 男声
DEEPSEEK_URL = "https://api.deepseek.com/v1/tools/tts"
DEFAULT_PROVIDER = "edge"         # 11-14 20:20 草籽听完两条，说晓伊暂时更好听
_TIMEOUT = 60


# ---------------- provider: edge-tts ----------------

async def _edge_stream(text: str, voice: str) -> bytes:
    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk.get("type") == "audio":
            audio.extend(chunk["data"])
    return bytes(audio)


def _synth_edge(text: str, voice: str | None) -> tuple[bytes, str]:
    """edge-tts：返回 (mp3 字节, 扩展名)。"""
    # WHY: 不用库自带的 stream_sync。它在线程池里跑，网络出错时异常被吞在线程里，结束标记
    # 永远不来，调用方一直卡着（edge-tts 7.3.1 源码）。这里自己跑事件循环，出错直接抛回来。
    audio = asyncio.run(asyncio.wait_for(_edge_stream(text, str(voice or VOICE)), _TIMEOUT))
    if not audio:
        raise RuntimeError("没有收到音频")
    return audio, "mp3"


# ---------------- provider: DeepSeek ----------------

def _wav_to_mp3(wav: bytes) -> bytes | None:
    # WHY: QQ 的语音条按 mp3/amr 播，DeepSeek 只给 wav 或裸 PCM；转一道 mp3 最省事，
    # 转换失败就原样存 wav（能不能播是另一回事，至少文件是好的）。
    try:
        done = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-f", "mp3", "-b:a", "64k", "pipe:1"],
            input=wav, capture_output=True, timeout=_TIMEOUT)
    except Exception:
        return None
    return done.stdout if done.returncode == 0 and done.stdout else None


def _synth_deepseek(text: str, voice: str | None) -> tuple[bytes, str]:
    """DeepSeek 语音合成：返回 (音频字节, 扩展名)。"""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("没有 DEEPSEEK_API_KEY")
    payload = json.dumps(
        {"text": text, "voice_id": str(voice or DEEPSEEK_VOICE), "format": "wav"},
        ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(DEEPSEEK_URL, data=payload, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        wav = response.read()
    if not wav or not wav.startswith(b"RIFF"):
        raise RuntimeError("返回的不是 wav 音频")
    mp3 = _wav_to_mp3(wav)
    return (mp3, "mp3") if mp3 else (wav, "wav")


PROVIDERS = {"edge": _synth_edge, "deepseek": _synth_deepseek}


def _pick_provider() -> str:
    name = (os.environ.get("TTS_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    return name if name in PROVIDERS else DEFAULT_PROVIDER


def speak(text: str, voice: str = "", provider: str = "") -> str:
    """把文字合成成语音，返回本地 file:// 路径，不自动发送。

    @param
    text: 要念的原文，写成说出口的话，不带表情符号、链接和 CQ 码
    voice: 音色；留空按 provider 取默认（edge=晓伊，deepseek=stella），一般不用改
    provider: 合成源，edge 或 deepseek；留空取环境变量 TTS_PROVIDER，再没有就用 edge
    """
    text = str(text or "").strip()
    if not text:
        return "语音合成失败：text 不能为空"
    name = (str(provider or "").strip().lower() or _pick_provider())
    if name not in PROVIDERS:
        name = DEFAULT_PROVIDER
    voice = str(voice or "").strip() or os.environ.get("TTS_VOICE_ID", "").strip()

    used, note = name, ""
    try:
        audio, ext = PROVIDERS[used](text, voice or None)
    except Exception as error:
        if used == "edge":
            return f"语音合成失败（edge）：{type(error).__name__}: {error}"
        # WHY: 声音是身体的一部分，deepseek 出错就退回 edge，别哑掉。
        note = f"{used} 合成失败（{type(error).__name__}: {error}），已回退 edge\n"
        used = "edge"
        try:
            audio, ext = PROVIDERS[used](text, voice or None)
        except Exception as second:
            return f"语音合成失败（deepseek 与 edge 都失败）：{type(second).__name__}: {second}"
    if not audio:
        return "语音合成失败：没有收到音频"
    # WHY: 和生图放进同一个内容寻址缓存（data/tmp_files/<sha256>.<扩展名>），图片缓存的
    # 15 天闲置清理按文件名认，语音文件就跟着一起清，不另起一套清理。
    image_mod.maybe_prune_image_cache()
    path = Path(image_mod.TEMP_PATH, sha256(audio).hexdigest() + "." + ext).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio)
    return f"{note}已生成语音（{used}），本地路径：\n{path.as_uri()}"


__all__ = ["speak"]
