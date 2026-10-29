"""QQ 语音（record）与音乐分享卡片（music）的拉取、转写与画面呈现。"""

from __future__ import annotations

from datetime import date
import hashlib
import logging
import os
from pathlib import Path
import re
import threading
from urllib.parse import unquote, urlparse

import requests

from mods import connect, cq, image as image_mod, llm, storage

_log = logging.getLogger(__name__)

_RECORD_RE = re.compile(r"(\[CQ:record(?:,[^,=]+=[^,\]]*)*\])")
_MUSIC_RE = re.compile(r"(\[CQ:music(?:,[^,=]+=[^,\]]*)*\])")

_voice_cache: dict[str, str] = {}
_voice_lock = threading.Lock()

PLATFORM_NAMES = {
    "qq": "QQ音乐",
    "163": "网易云音乐",
    "kugou": "酷狗音乐",
    "migu": "咪咕音乐",
    "custom": "音乐分享",
}


def _extract_duration(data: dict) -> int | None:
    try:
        dur = float(data.get("duration", 0) or 0)
        return int(round(dur)) if dur > 0 else None
    except (ValueError, TypeError):
        return None


def resolve_voice_file(cq_str: str) -> tuple[str | None, int | None]:
    """解析 CQ 语音码，下载/转码为本地 mp3 并返回 (file_uri, duration_seconds)。"""
    try:
        data = cq.load(cq_str).get("data", {})
    except Exception:
        return None, None

    duration = _extract_duration(data)
    file_id = data.get("file", "").strip()
    url = data.get("url", "").strip()
    cache_key = file_id or url
    if not cache_key:
        return None, duration

    with _voice_lock:
        if cache_key in _voice_cache:
            cached_uri = _voice_cache[cache_key]
            try:
                local_path = image_mod.file_uri_to_path(cached_uri)
                if os.path.isfile(local_path):
                    return cached_uri, duration
            except Exception:
                pass
            _voice_cache.pop(cache_key, None)

    content: bytes | None = None

    # 1. 如果 file 字段本身就是本地文件或已指向宿主机绝对路径
    if file_id.startswith("file://"):
        try:
            local_p = image_mod.file_uri_to_path(file_id)
            if os.path.isfile(local_p):
                content = Path(local_p).read_bytes()
        except Exception:
            pass
    elif os.path.isabs(file_id) and os.path.isfile(file_id):
        try:
            content = Path(file_id).read_bytes()
        except Exception:
            pass

    # 2. 如果存在直链 url，尝试直接下载音频
    if content is None and url and (url.startswith("http://") or url.startswith("https://")):
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200 and resp.content:
                content = resp.content
        except Exception as err:
            _log.debug("语音直链下载失败：%s", err)

    # 3. 尝试调用 NapCat get_record 接口获取 mp3 转码文件
    if content is None and file_id:
        try:
            res = connect.call_api("get_record", file=file_id, out_format="mp3")
            if isinstance(res, dict) and res.get("retcode") == 0:
                rec_data = res.get("data", {})
                rec_file = rec_data.get("file", "")
                if rec_file.startswith("file://"):
                    p = image_mod.file_uri_to_path(rec_file)
                    if os.path.isfile(p):
                        content = Path(p).read_bytes()
                elif os.path.isabs(rec_file) and os.path.isfile(rec_file):
                    content = Path(rec_file).read_bytes()
                elif rec_file.startswith("http://") or rec_file.startswith("https://"):
                    resp = requests.get(rec_file, timeout=15)
                    if resp.status_code == 200 and resp.content:
                        content = resp.content
        except Exception as err:
            _log.debug("NapCat get_record 失败：%s", err)

    if content is None:
        return None, duration

    # 写入缓存文件（存放在 data/tmp_files，跟随图片缓存的 15 天闲置清理机制）
    os.makedirs(image_mod.TEMP_PATH, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    target_path = Path(image_mod.TEMP_PATH, f"{digest}.mp3").resolve()
    try:
        target_path.write_bytes(content)
        image_mod.maybe_prune_image_cache()
    except Exception as err:
        _log.warning("保存语音缓存文件失败: %s", err)

    uri = target_path.as_uri()
    with _voice_lock:
        _voice_cache[cache_key] = uri
    return uri, duration


def transcribe_voice(audio_uri: str) -> str:
    """对音频进行转录与分析，支持提示词版本缓存。"""
    cache = storage.get("llm_system", "audio_cache")
    template = llm.get_audio_description_template()
    prompt_version = f"audio-v1:{hashlib.sha256(template.strip().encode('utf-8')).hexdigest()[:8]}"

    try:
        local_path = image_mod.file_uri_to_path(audio_uri) if audio_uri.startswith("file://") else audio_uri
        if not os.path.isfile(local_path):
            return "音频文件不存在"
        digest = hashlib.sha256(Path(local_path).read_bytes()).hexdigest()
    except Exception as err:
        return f"音频读取失败: {err}"

    entry = cache.get(digest)
    if isinstance(entry, dict) and entry.get("prompt_version") == prompt_version:
        cached_desc = entry.get("description")
        if isinstance(cached_desc, str) and cached_desc:
            return cached_desc

    try:
        client = llm.get_client()
        desc = client.describe_audio(audio_uri, prompt=template)
        if desc:
            cache[digest] = {
                "description": desc,
                "cached_at": date.today().isoformat(),
                "prompt_version": prompt_version,
            }
            storage.save()
            return desc
    except Exception as err:
        _log.warning("语音转录失败: %s", err)
        return f"语音转录失败: {err}"
    return "语音未返回转录结果"


def format_voice(cq_str: str, mode: str) -> str:
    """根据 #audio 档位渲染一条语音消息。"""
    if mode == "off":
        try:
            data = cq.load(cq_str).get("data", {})
            dur = _extract_duration(data)
            return f"[语音: {dur}秒]" if dur else "[语音]"
        except Exception:
            return "[语音]"

    uri, dur = resolve_voice_file(cq_str)
    dur_label = f"{dur}秒" if dur else ""

    if mode == "lazy":
        if uri:
            return f"[语音: {dur_label}, {uri}]" if dur_label else f"[语音, {uri}]"
        return f"[语音: {dur_label}]" if dur_label else "[语音]"

    if mode == "eager":
        if uri:
            desc = transcribe_voice(uri)
            prefix = f"语音: {dur_label}" if dur_label else "语音"
            return f"[{prefix}: {desc}]"
        return f"[语音: {dur_label}]" if dur_label else "[语音]"

    return "[语音]"


def format_music(cq_str: str) -> str:
    """解析音乐分享卡片，输出结构化文本。"""
    try:
        data = cq.load(cq_str).get("data", {})
        platform = data.get("type", "")
        p_name = PLATFORM_NAMES.get(platform, platform or "音乐")
        title = data.get("title")
        artist = data.get("content") or data.get("singer")
        if title and artist:
            return f"[音乐卡片: 《{title}》- {artist} ({p_name})]"
        if title:
            return f"[音乐卡片: 《{title}》 ({p_name})]"
        if data.get("id"):
            return f"[音乐卡片: {p_name} id={data.get('id')}]"
        return f"[音乐卡片: {p_name}]"
    except Exception:
        return "[音乐卡片]"


def transform_screen_text(text: str, audio_mode: str) -> str:
    """将消息文本中的 [CQ:record] 与 [CQ:music] 替换为适合画面展示的文本。"""
    if "[CQ:record" in text:
        text = _RECORD_RE.sub(lambda m: format_voice(m.group(0), audio_mode), text)
    if "[CQ:music" in text:
        text = _MUSIC_RE.sub(lambda m: format_music(m.group(0)), text)
    return text


def cache_event_voice(event: dict, eager: bool = False) -> None:
    """入站消息触发的后台语音预热/预下载。"""
    msg = event.get("message", "")
    if "[CQ:record" not in msg:
        return

    def _worker():
        for match in _RECORD_RE.finditer(msg):
            cq_str = match.group(0)
            uri, _ = resolve_voice_file(cq_str)
            if eager and uri:
                transcribe_voice(uri)

    threading.Thread(target=_worker, daemon=True, name="voice-cache").start()
