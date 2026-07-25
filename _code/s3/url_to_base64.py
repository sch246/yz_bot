import base64
import hashlib
import io
import mimetypes
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import date, timedelta
from urllib.parse import urlparse

from PIL import Image, UnidentifiedImageError
from s3.image_identity import (
    file_uri_to_path,
    hash_file,
    image_uri_alias_key,
    valid_image_digest,
)


TEMP_PATH = 'data/tmp_files'
IMAGE_CACHE_MAX_IDLE_DAYS = 15
IMAGE_ALIAS_MAX_AGE_DAYS = 15
IMAGE_CACHE_PRUNE_INTERVAL_SECONDS = 24 * 60 * 60
MAX_LOCAL_IMAGE_BYTES = 20 * 1024 * 1024

_HASHED_CACHE_FILE = re.compile(
    r'^(?:[0-9a-f]{32}|[0-9a-f]{64})(?:\.[A-Za-z0-9]+)?$'
)
_cache_prune_lock = threading.Lock()
_last_cache_prune = {}
_alias_lock = threading.RLock()
_image_uri_aliases = {}
_resolve_inflight = {}
_content_write_lock = threading.Lock()

PIL_FORMAT_TO_MIME = {
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
    'GIF': 'image/gif',
    'WEBP': 'image/webp',
    'BMP': 'image/bmp',
    'TIFF': 'image/tiff',
}

MIME_TO_EXTENSION = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/bmp': '.bmp',
    'image/tiff': '.tif',
}

os.makedirs(TEMP_PATH, exist_ok=True)


def configure_image_uri_aliases(aliases: dict) -> None:
    """Install the storage-owned URI-to-content-digest mapping."""
    if not isinstance(aliases, dict):
        raise TypeError('图片 URI alias 必须是 dict')
    global _image_uri_aliases
    with _alias_lock:
        _image_uri_aliases = aliases


def _parse_cache_date(value) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def get_cached_image_digest(uri: str, today: date = None) -> str | None:
    """Resolve a fresh URI alias without requiring the cached bytes to exist."""
    today = today or date.today()
    key = image_uri_alias_key(uri)
    with _alias_lock:
        entry = _image_uri_aliases.get(key)
        if not isinstance(entry, dict) or not valid_image_digest(entry.get('digest')):
            return None
        last_used_at = _parse_cache_date(entry.get('last_used_at'))
        if last_used_at is None:
            last_used_at = _parse_cache_date(entry.get('resolved_at'))
        if last_used_at is None or last_used_at < today - timedelta(days=IMAGE_ALIAS_MAX_AGE_DAYS):
            _image_uri_aliases.pop(key, None)
            return None
        entry['last_used_at'] = today.isoformat()
        return entry['digest']


def cache_image_uri_digest(uri: str, digest: str, today: date = None) -> None:
    if not valid_image_digest(digest):
        raise ValueError('图片内容摘要必须是 SHA-256 十六进制字符串')
    today = today or date.today()
    with _alias_lock:
        _image_uri_aliases[image_uri_alias_key(uri)] = {
            'digest': digest,
            'resolved_at': today.isoformat(),
            'last_used_at': today.isoformat(),
        }


def prune_image_uri_aliases(today: date = None) -> int:
    today = today or date.today()
    cutoff = today - timedelta(days=IMAGE_ALIAS_MAX_AGE_DAYS)
    removed = 0
    with _alias_lock:
        for key, entry in list(_image_uri_aliases.items()):
            last_used = _parse_cache_date(entry.get('last_used_at')) if isinstance(entry, dict) else None
            if last_used is None and isinstance(entry, dict):
                last_used = _parse_cache_date(entry.get('resolved_at'))
            if last_used is None or last_used < cutoff:
                del _image_uri_aliases[key]
                removed += 1
    return removed


def touch_image_cache(path: str, now: float = None) -> bool:
    """刷新临时图片的最后使用时间。"""
    timestamp = time.time() if now is None else now
    try:
        os.utime(path, (timestamp, timestamp))
    except OSError:
        return False
    return True


def prune_image_cache(
    target_dir: str = TEMP_PATH,
    now: float = None,
    max_idle_days: int = IMAGE_CACHE_MAX_IDLE_DAYS,
) -> int:
    """删除超过指定天数未使用的哈希命名临时图片。"""
    timestamp = time.time() if now is None else now
    cutoff = timestamp - max_idle_days * 24 * 60 * 60
    removed = 0

    try:
        entries = list(os.scandir(target_dir))
    except OSError:
        return 0

    for entry in entries:
        if not entry.is_file() or not _HASHED_CACHE_FILE.fullmatch(entry.name):
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                os.remove(entry.path)
                removed += 1
        except OSError:
            continue

    return removed


def maybe_prune_image_cache(target_dir: str = TEMP_PATH) -> int:
    """有图片活动时至多每天清理一次临时图片缓存。"""
    cache_dir = os.path.abspath(target_dir)
    monotonic_now = time.monotonic()
    with _cache_prune_lock:
        last_prune = _last_cache_prune.get(cache_dir)
        if (
            last_prune is not None
            and monotonic_now - last_prune < IMAGE_CACHE_PRUNE_INTERVAL_SECONDS
        ):
            return 0
        removed = prune_image_cache(target_dir)
        removed_aliases = prune_image_uri_aliases()
        _last_cache_prune[cache_dir] = monotonic_now

    if removed:
        print(
            f"🧹 已清理 {removed} 个超过 "
            f"{IMAGE_CACHE_MAX_IDLE_DAYS} 天未使用的临时图片"
        )
    if removed_aliases:
        print(f"🧹 已清理 {removed_aliases} 条过期图片 URI alias")
    return removed


def _validate_image_file(
    path: str,
    max_bytes: int = MAX_LOCAL_IMAGE_BYTES,
) -> tuple[str, str]:
    """验证本地图片，返回绝对路径和 MIME 类型。"""
    resolved_path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(resolved_path):
        raise ValueError('图片文件不存在或不是普通文件')

    size = os.path.getsize(resolved_path)
    if size > max_bytes:
        raise ValueError(f'图片文件超过 {max_bytes // (1024 * 1024)} MiB 限制')

    try:
        with Image.open(resolved_path) as image:
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ValueError('文件不是可识别的图片') from error

    mime_type = PIL_FORMAT_TO_MIME.get(image_format)
    if not mime_type:
        mime_type = mimetypes.guess_type(resolved_path)[0]
    if not mime_type or not mime_type.startswith('image/'):
        raise ValueError(f'不支持的图片格式: {image_format or "unknown"}')

    return resolved_path, mime_type


def _find_content_cache_file(digest: str, target_dir: str = TEMP_PATH) -> str | None:
    if not valid_image_digest(digest):
        return None
    try:
        candidates = sorted(
            entry.path
            for entry in os.scandir(target_dir)
            if entry.is_file() and os.path.splitext(entry.name)[0] == digest
        )
    except OSError:
        return None
    return candidates[0] if candidates else None


def _remove_cache_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _validate_image_bytes(
    image_bytes: bytes,
    max_bytes: int = MAX_LOCAL_IMAGE_BYTES,
) -> tuple[str, str]:
    if not image_bytes:
        raise ValueError('下载内容为空')
    if len(image_bytes) > max_bytes:
        raise ValueError(f'图片文件超过 {max_bytes // (1024 * 1024)} MiB 限制')
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ValueError('下载内容不是可识别的图片') from error
    mime_type = PIL_FORMAT_TO_MIME.get(image_format)
    if not mime_type:
        raise ValueError(f'不支持的图片格式: {image_format or "unknown"}')
    return mime_type, hashlib.sha256(image_bytes).hexdigest()


def _store_content_image(
    image_bytes: bytes,
    mime_type: str,
    digest: str,
    target_dir: str,
    max_bytes: int,
) -> tuple[str, str, str]:
    os.makedirs(target_dir, exist_ok=True)
    extension = MIME_TO_EXTENSION.get(
        mime_type,
        mimetypes.guess_extension(mime_type) or '.bin',
    )
    cache_path = os.path.join(target_dir, digest + extension)
    with _content_write_lock:
        existing = _find_content_cache_file(digest, target_dir)
        if existing is not None:
            try:
                resolved_path, existing_mime = _validate_image_file(existing, max_bytes)
                touch_image_cache(resolved_path)
                return resolved_path, existing_mime, digest
            except ValueError:
                _remove_cache_file(existing)

        fd, temporary = tempfile.mkstemp(
            dir=target_dir,
            prefix=f'.{digest}.',
            suffix='.tmp',
        )
        try:
            with os.fdopen(fd, 'wb') as image_file:
                image_file.write(image_bytes)
                image_file.flush()
                os.fsync(image_file.fileno())
            os.replace(temporary, cache_path)
        except BaseException:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass
            raise
    resolved_path, resolved_mime = _validate_image_file(cache_path, max_bytes)
    return resolved_path, resolved_mime, digest


def _download_image_to_cache(
    uri: str,
    target_dir: str = TEMP_PATH,
    max_bytes: int = MAX_LOCAL_IMAGE_BYTES,
) -> tuple[str, str, str]:
    """下载并验证网络图片，写入内容寻址缓存。"""
    print(f"⬇️ 图片缓存未命中，尝试下载: {image_uri_alias_key(uri)}")
    try:
        result = subprocess.run([
            'curl',
            '-k',
            '-L',
            '-s',
            '-H',
            'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 '
            'Safari/537.36 Edg/134.0.0.0',
            '--max-time',
            '15',
            '--max-filesize',
            str(max_bytes),
            uri,
        ], capture_output=True)
    except FileNotFoundError as error:
        raise ValueError('找不到 curl，无法下载网络图片') from error

    if result.returncode != 0 or not result.stdout:
        raise ValueError(f'网络图片下载失败（curl 返回码 {result.returncode}）')
    mime_type, digest = _validate_image_bytes(result.stdout, max_bytes)
    resolved = _store_content_image(
        result.stdout,
        mime_type,
        digest,
        target_dir,
        max_bytes,
    )
    cache_image_uri_digest(uri, digest)
    return resolved


def _resolve_cached_network_image(
    uri: str,
    target_dir: str,
    max_bytes: int,
) -> tuple[str, str, str] | None:
    digest = get_cached_image_digest(uri)
    if digest is None:
        return None
    cached_path = _find_content_cache_file(digest, target_dir)
    if cached_path is None:
        return None
    try:
        resolved_path, mime_type = _validate_image_file(cached_path, max_bytes)
    except ValueError:
        _remove_cache_file(cached_path)
        return None
    touch_image_cache(resolved_path)
    return resolved_path, mime_type, digest


def resolve_image_with_digest(
    uri: str,
    target_dir: str = TEMP_PATH,
    max_bytes: int = MAX_LOCAL_IMAGE_BYTES,
) -> tuple[str, str, str]:
    """把图片 URI 解析为本地文件、MIME 与 SHA-256 内容身份。"""
    if not isinstance(uri, str) or not uri.strip():
        raise ValueError('图片 URI 不能为空')
    uri = uri.strip()

    scheme = urlparse(uri).scheme.lower()
    maybe_prune_image_cache(target_dir)
    if scheme == 'file':
        path, mime_type = _validate_image_file(file_uri_to_path(uri), max_bytes)
        digest = hash_file(path)
        cache_image_uri_digest(uri, digest)
        return path, mime_type, digest
    if scheme not in {'http', 'https'}:
        raise ValueError('图片 URI 只支持 http://、https:// 或 file://')

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
                is_owner = True
            else:
                is_owner = False
        if is_owner:
            break
        event.wait()
        cached = _resolve_cached_network_image(uri, target_dir, max_bytes)
        if cached is not None:
            return cached

    try:
        cached = _resolve_cached_network_image(uri, target_dir, max_bytes)
        if cached is not None:
            return cached
        return _download_image_to_cache(uri, target_dir, max_bytes)
    finally:
        with _alias_lock:
            _resolve_inflight.pop(source_key, None)
            event.set()


def resolve_image_uri(
    uri: str,
    target_dir: str = TEMP_PATH,
    max_bytes: int = MAX_LOCAL_IMAGE_BYTES,
) -> tuple[str, str]:
    """Compatibility boundary returning the validated file and MIME type."""
    path, mime_type, _ = resolve_image_with_digest(uri, target_dir, max_bytes)
    return path, mime_type


def image_uri_to_data_uri(
    uri: str,
    target_dir: str = TEMP_PATH,
    max_bytes: int = MAX_LOCAL_IMAGE_BYTES,
) -> str:
    """通过统一 URI 解析入口返回视觉模型使用的 Data URI。"""
    image_path, mime_type, _ = resolve_image_with_digest(uri, target_dir, max_bytes)
    with open(image_path, 'rb') as image_file:
        encoded = base64.b64encode(image_file.read()).decode('ascii')
    return f'data:{mime_type};base64,{encoded}'
