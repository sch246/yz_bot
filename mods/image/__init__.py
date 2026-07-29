"""Content-addressed image resolution, descriptions, and vision input."""

from __future__ import annotations

import base64
from datetime import date, timedelta
import hashlib
import io
import mimetypes
import os
import re
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlparse, urlsplit, urlunsplit

from PIL import Image, UnidentifiedImageError

from .identity import (
    canonical_http_uri,
    file_uri_to_path,
    hash_file,
    image_uri_alias_key,
    intrinsic_image_description_identity,
    valid_description_identity,
    valid_image_digest,
)
from .vision import AUTO_IMAGE_SPLIT_PROMPT, split_long_image_data_uri


LOAD_AFTER = ("storage",)

TEMP_PATH = "data/tmp_files"
IMAGE_CACHE_MAX_IDLE_DAYS = 15
IMAGE_ALIAS_MAX_AGE_DAYS = 15
IMAGE_CACHE_PRUNE_INTERVAL_SECONDS = 24 * 60 * 60
DESCRIPTION_CACHE_MAX_AGE_DAYS = 15
AUTO_IMAGE_DESCRIPTION_VERSION = "auto-v1"
MAX_LOCAL_IMAGE_BYTES = 20 * 1024 * 1024

PIL_FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}
MIME_TO_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tif",
}
_HASHED_CACHE_FILE = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f]{64})(?:\.[A-Za-z0-9]+)?$")

_cache_prune_lock = threading.Lock()
_last_cache_prune: dict[str, float] = {}
_description_prune_lock = threading.Lock()
_last_description_prune: dict[int, tuple[dict, date]] = {}
_alias_lock = threading.RLock()
_image_uri_aliases: dict = {}
_resolve_inflight: dict[str, threading.Event] = {}
_content_write_lock = threading.Lock()


def _display_uri(uri: str, limit: int = 180) -> str:
    """Describe an image source without printing query credentials or Base64."""
    if uri.startswith("data:"):
        media_type = uri.partition(";")[0] or "data:image"
        return f"{media_type};base64,… ({len(uri)} 字符)"
    parsed = urlsplit(uri)
    if parsed.scheme in {"http", "https"}:
        value = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    elif parsed.scheme == "file":
        value = f"file://…/{os.path.basename(parsed.path)}"
    else:
        value = uri
    return value if len(value) <= limit else value[:limit] + "…"


def _cache_summary(path: str, mime: str, digest: str) -> str:
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return f"{mime}，{size / 1024:.1f} KiB，sha256:{digest[:12]}"


def configure_image_uri_aliases(aliases: dict) -> None:
    if not isinstance(aliases, dict):
        raise TypeError("图片 URI alias 必须是 dict")
    global _image_uri_aliases
    with _alias_lock:
        _image_uri_aliases = aliases


def _parse_cache_date(value) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def get_cached_image_digest(uri: str, today: date | None = None) -> str | None:
    today = today or date.today()
    key = image_uri_alias_key(uri)
    with _alias_lock:
        entry = _image_uri_aliases.get(key)
        if not isinstance(entry, dict) or not valid_image_digest(entry.get("digest")):
            return None
        last_used = _parse_cache_date(entry.get("last_used_at")) or _parse_cache_date(entry.get("resolved_at"))
        if last_used is None or last_used < today - timedelta(days=IMAGE_ALIAS_MAX_AGE_DAYS):
            _image_uri_aliases.pop(key, None)
            return None
        entry["last_used_at"] = today.isoformat()
        return entry["digest"]


def cache_image_uri_digest(uri: str, digest: str, today: date | None = None) -> None:
    if not valid_image_digest(digest):
        raise ValueError("图片内容摘要必须是 SHA-256")
    today = today or date.today()
    with _alias_lock:
        _image_uri_aliases[image_uri_alias_key(uri)] = {
            "digest": digest,
            "resolved_at": today.isoformat(),
            "last_used_at": today.isoformat(),
        }


def prune_image_uri_aliases(today: date | None = None) -> int:
    today = today or date.today()
    cutoff = today - timedelta(days=IMAGE_ALIAS_MAX_AGE_DAYS)
    removed = 0
    with _alias_lock:
        for key, entry in list(_image_uri_aliases.items()):
            last_used = _parse_cache_date(entry.get("last_used_at")) if isinstance(entry, dict) else None
            if last_used is None and isinstance(entry, dict):
                last_used = _parse_cache_date(entry.get("resolved_at"))
            if last_used is None or last_used < cutoff:
                del _image_uri_aliases[key]
                removed += 1
    return removed


def touch_image_cache(path: str, now: float | None = None) -> bool:
    timestamp = time.time() if now is None else now
    try:
        os.utime(path, (timestamp, timestamp))
        return True
    except OSError:
        return False


def prune_image_cache(target_dir: str = TEMP_PATH, now: float | None = None, max_idle_days: int = IMAGE_CACHE_MAX_IDLE_DAYS) -> int:
    cutoff = (time.time() if now is None else now) - max_idle_days * 86400
    try:
        entries = list(os.scandir(target_dir))
    except OSError:
        return 0
    removed = 0
    for entry in entries:
        if not entry.is_file() or not _HASHED_CACHE_FILE.fullmatch(entry.name):
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                os.remove(entry.path)
                removed += 1
        except OSError:
            pass
    return removed


def maybe_prune_image_cache(target_dir: str = TEMP_PATH) -> int:
    directory = os.path.abspath(target_dir)
    now = time.monotonic()
    with _cache_prune_lock:
        previous = _last_cache_prune.get(directory)
        if previous is not None and now - previous < IMAGE_CACHE_PRUNE_INTERVAL_SECONDS:
            return 0
        removed = prune_image_cache(target_dir)
        aliases_removed = prune_image_uri_aliases()
        _last_cache_prune[directory] = now
        if removed or aliases_removed:
            print(
                f"🧹 已清理 {removed} 个图片缓存文件、"
                f"{aliases_removed} 条 URI alias",
                flush=True,
            )
        return removed


def _validate_image_file(path: str, max_bytes: int = MAX_LOCAL_IMAGE_BYTES) -> tuple[str, str]:
    resolved = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(resolved):
        raise ValueError("图片文件不存在或不是普通文件")
    if os.path.getsize(resolved) > max_bytes:
        raise ValueError(f"图片文件超过 {max_bytes // 1024 // 1024} MiB 限制")
    try:
        with Image.open(resolved) as image:
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ValueError("文件不是可识别的图片") from error
    mime = PIL_FORMAT_TO_MIME.get(image_format) or mimetypes.guess_type(resolved)[0]
    if not mime or not mime.startswith("image/"):
        raise ValueError(f"不支持的图片格式: {image_format or 'unknown'}")
    return resolved, mime


def _validate_image_bytes(content: bytes, max_bytes: int) -> tuple[str, str]:
    if not content:
        raise ValueError("下载内容为空")
    if len(content) > max_bytes:
        raise ValueError(f"图片文件超过 {max_bytes // 1024 // 1024} MiB 限制")
    try:
        with Image.open(io.BytesIO(content)) as image:
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ValueError("下载内容不是可识别的图片") from error
    mime = PIL_FORMAT_TO_MIME.get(image_format)
    if not mime:
        raise ValueError(f"不支持的图片格式: {image_format or 'unknown'}")
    return mime, hashlib.sha256(content).hexdigest()


def _find_content_cache_file(digest: str, target_dir: str) -> str | None:
    if not valid_image_digest(digest):
        return None
    try:
        matches = sorted(
            entry.path for entry in os.scandir(target_dir)
            if entry.is_file() and os.path.splitext(entry.name)[0] == digest
        )
    except OSError:
        return None
    return matches[0] if matches else None


def _store_content_image(content: bytes, mime: str, digest: str, target_dir: str, max_bytes: int) -> tuple[str, str, str]:
    os.makedirs(target_dir, exist_ok=True)
    destination = os.path.join(target_dir, digest + MIME_TO_EXTENSION.get(mime, mimetypes.guess_extension(mime) or ".bin"))
    with _content_write_lock:
        existing = _find_content_cache_file(digest, target_dir)
        if existing is not None:
            try:
                path, existing_mime = _validate_image_file(existing, max_bytes)
                touch_image_cache(path)
                return path, existing_mime, digest
            except ValueError:
                try:
                    os.remove(existing)
                except OSError:
                    pass
        descriptor, temporary = tempfile.mkstemp(dir=target_dir, prefix=f".{digest}.", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except BaseException:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise
    path, resolved_mime = _validate_image_file(destination, max_bytes)
    return path, resolved_mime, digest


def _download_image_to_cache(uri: str, target_dir: str, max_bytes: int) -> tuple[str, str, str]:
    print(f"⬇️ 正在下载图片：{_display_uri(uri)}", flush=True)
    try:
        result = subprocess.run(
            ["curl", "-k", "-L", "-s", "-H", "User-Agent: Mozilla/5.0", "--max-time", "15", "--max-filesize", str(max_bytes), uri],
            capture_output=True,
        )
    except FileNotFoundError as error:
        raise ValueError("找不到 curl，无法下载网络图片") from error
    if result.returncode != 0 or not result.stdout:
        raise ValueError(f"网络图片下载失败（curl 返回码 {result.returncode}）")
    mime, digest = _validate_image_bytes(result.stdout, max_bytes)
    resolved = _store_content_image(result.stdout, mime, digest, target_dir, max_bytes)
    cache_image_uri_digest(uri, digest)
    print(f"✅ 图片已缓存：{_cache_summary(*resolved)}", flush=True)
    return resolved


def _resolve_cached_network_image(uri: str, target_dir: str, max_bytes: int) -> tuple[str, str, str] | None:
    digest = get_cached_image_digest(uri)
    path = _find_content_cache_file(digest, target_dir) if digest else None
    if path is None:
        return None
    try:
        path, mime = _validate_image_file(path, max_bytes)
    except ValueError:
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    touch_image_cache(path)
    print(f"✅ 图片内容缓存命中：{_cache_summary(path, mime, digest)}", flush=True)
    return path, mime, digest


def resolve_image_with_digest(uri: str, target_dir: str = TEMP_PATH, max_bytes: int = MAX_LOCAL_IMAGE_BYTES) -> tuple[str, str, str]:
    if not isinstance(uri, str) or not uri.strip():
        raise ValueError("图片 URI 不能为空")
    uri = uri.strip()
    maybe_prune_image_cache(target_dir)
    scheme = urlparse(uri).scheme.lower()
    if scheme == "file":
        path, mime = _validate_image_file(file_uri_to_path(uri), max_bytes)
        digest = hash_file(path)
        cache_image_uri_digest(uri, digest)
        print(f"✅ 本地图片已解析：{_cache_summary(path, mime, digest)}", flush=True)
        return path, mime, digest
    if scheme not in {"http", "https"}:
        raise ValueError("图片 URI 只支持 http://、https:// 或 file://")
    cached = _resolve_cached_network_image(uri, target_dir, max_bytes)
    if cached is not None:
        return cached
    source_key = image_uri_alias_key(uri)
    while True:
        with _alias_lock:
            event = _resolve_inflight.get(source_key)
            if event is None:
                event = threading.Event()
                _resolve_inflight[source_key] = event
                owner = True
            else:
                owner = False
        if owner:
            break
        print(f"⏳ 等待同一图片的下载任务：{_display_uri(uri)}", flush=True)
        event.wait()
        cached = _resolve_cached_network_image(uri, target_dir, max_bytes)
        if cached is not None:
            return cached
    try:
        return _resolve_cached_network_image(uri, target_dir, max_bytes) or _download_image_to_cache(uri, target_dir, max_bytes)
    finally:
        with _alias_lock:
            _resolve_inflight.pop(source_key, None)
            event.set()


def resolve_image_uri(uri: str, target_dir: str = TEMP_PATH, max_bytes: int = MAX_LOCAL_IMAGE_BYTES) -> tuple[str, str]:
    path, mime, _ = resolve_image_with_digest(uri, target_dir, max_bytes)
    return path, mime


def image_uri_to_data_uri(uri: str, target_dir: str = TEMP_PATH, max_bytes: int = MAX_LOCAL_IMAGE_BYTES) -> str:
    path, mime, _ = resolve_image_with_digest(uri, target_dir, max_bytes)
    with open(path, "rb") as stream:
        encoded = base64.b64encode(stream.read()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def get_cached_description(cache: dict, identity: str, today: date | None = None) -> str | None:
    if not valid_description_identity(identity):
        return None
    today = today or date.today()
    entry = cache.get(identity)
    if not isinstance(entry, dict) or not isinstance(entry.get("description"), str):
        return None
    if entry.get("prompt_version") != AUTO_IMAGE_DESCRIPTION_VERSION:
        del cache[identity]
        return None
    cached_at = _parse_cache_date(entry.get("cached_at")) or today
    if cached_at < today - timedelta(days=DESCRIPTION_CACHE_MAX_AGE_DAYS):
        del cache[identity]
        return None
    entry["cached_at"] = today.isoformat()
    return entry["description"]


def prune_description_cache(cache: dict, today: date | None = None, max_age_days: int = DESCRIPTION_CACHE_MAX_AGE_DAYS) -> int:
    today = today or date.today()
    cutoff = today - timedelta(days=max_age_days)
    removed = 0
    for identity, entry in list(cache.items()):
        valid = (
            valid_description_identity(identity)
            and isinstance(entry, dict)
            and isinstance(entry.get("description"), str)
            and entry.get("prompt_version") == AUTO_IMAGE_DESCRIPTION_VERSION
        )
        if not valid:
            del cache[identity]
            removed += 1
            continue
        cached_at = _parse_cache_date(entry.get("cached_at"))
        if cached_at is None:
            entry["cached_at"] = today.isoformat()
        elif cached_at < cutoff:
            del cache[identity]
            removed += 1
    return removed


def maybe_prune_description_cache(cache: dict, today: date | None = None) -> int:
    today = today or date.today()
    cache_id = id(cache)
    with _description_prune_lock:
        previous = _last_description_prune.get(cache_id)
        if previous is not None and previous[0] is cache and previous[1] == today:
            return 0
        removed = prune_description_cache(cache, today)
        _last_description_prune[cache_id] = (cache, today)
        return removed


def cache_description(cache: dict, identity: str, description: str, today: date | None = None) -> int:
    if not valid_description_identity(identity):
        raise ValueError("图片描述键必须是受支持的内容摘要")
    today = today or date.today()
    cache[identity] = {
        "description": description,
        "cached_at": today.isoformat(),
        "prompt_version": AUTO_IMAGE_DESCRIPTION_VERSION,
    }
    return prune_description_cache(cache, today)


def on_load(ctx) -> None:
    from mods import storage

    os.makedirs(TEMP_PATH, exist_ok=True)
    configure_image_uri_aliases(storage.get("llm_system", "image_uri_aliases"))
