import base64
import hashlib
import io
import mimetypes
import os
import re
import subprocess
import threading
import time
from urllib.parse import unquote, urlparse

from PIL import Image, UnidentifiedImageError


TEMP_PATH = 'data/tmp_files'
IMAGE_CACHE_MAX_IDLE_DAYS = 15
IMAGE_CACHE_PRUNE_INTERVAL_SECONDS = 24 * 60 * 60
MAX_LOCAL_IMAGE_BYTES = 20 * 1024 * 1024

_HASHED_CACHE_FILE = re.compile(
    r'^(?:[0-9a-f]{32}|[0-9a-f]{64})(?:\.[A-Za-z0-9]+)?$'
)
_cache_prune_lock = threading.Lock()
_last_cache_prune = {}

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
        _last_cache_prune[cache_dir] = monotonic_now

    if removed:
        print(
            f"🧹 已清理 {removed} 个超过 "
            f"{IMAGE_CACHE_MAX_IDLE_DAYS} 天未使用的临时图片"
        )
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


def _file_uri_to_path(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme.lower() != 'file':
        raise ValueError('不是 file:// 图片 URI')
    if parsed.netloc not in ('', 'localhost'):
        raise ValueError('file:// URI 不支持远程主机')
    path = unquote(parsed.path)
    if not os.path.isabs(path):
        raise ValueError('file:// URI 必须使用绝对路径')
    return path


def _find_url_cache_file(url: str, target_dir: str = TEMP_PATH) -> str | None:
    url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
    try:
        candidates = sorted(
            entry.path
            for entry in os.scandir(target_dir)
            if entry.is_file() and os.path.splitext(entry.name)[0] == url_hash
        )
    except OSError:
        return None
    return candidates[0] if candidates else None


def _remove_cache_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _download_image_to_cache(
    uri: str,
    target_dir: str = TEMP_PATH,
    max_bytes: int = MAX_LOCAL_IMAGE_BYTES,
) -> tuple[str, str]:
    """下载并验证网络图片，写入 URL 哈希缓存。"""
    print(f"⬇️ 图片缓存未命中，尝试下载: {uri}")
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
    if len(result.stdout) > max_bytes:
        raise ValueError(f'图片文件超过 {max_bytes // (1024 * 1024)} MiB 限制')

    try:
        with Image.open(io.BytesIO(result.stdout)) as image:
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ValueError('下载内容不是可识别的图片') from error

    mime_type = PIL_FORMAT_TO_MIME.get(image_format)
    if not mime_type:
        raise ValueError(f'不支持的图片格式: {image_format or "unknown"}')

    os.makedirs(target_dir, exist_ok=True)
    extension = MIME_TO_EXTENSION.get(
        mime_type,
        mimetypes.guess_extension(mime_type) or '.bin',
    )
    cache_path = os.path.join(
        target_dir,
        hashlib.md5(uri.encode('utf-8')).hexdigest() + extension,
    )

    try:
        with open(cache_path, 'wb') as image_file:
            image_file.write(result.stdout)
        return _validate_image_file(cache_path, max_bytes)
    except Exception:
        _remove_cache_file(cache_path)
        raise


def resolve_image_uri(
    uri: str,
    target_dir: str = TEMP_PATH,
    max_bytes: int = MAX_LOCAL_IMAGE_BYTES,
) -> tuple[str, str]:
    """把 http(s):// 或 file:// 图片 URI 解析为已验证的本地文件。"""
    if not isinstance(uri, str) or not uri.strip():
        raise ValueError('图片 URI 不能为空')
    uri = uri.strip()

    scheme = urlparse(uri).scheme.lower()
    if scheme == 'file':
        return _validate_image_file(_file_uri_to_path(uri), max_bytes)
    if scheme not in {'http', 'https'}:
        raise ValueError('图片 URI 只支持 http://、https:// 或 file://')

    maybe_prune_image_cache(target_dir)
    cached_path = _find_url_cache_file(uri, target_dir)
    if cached_path is not None:
        try:
            resolved = _validate_image_file(cached_path, max_bytes)
            touch_image_cache(cached_path)
            return resolved
        except ValueError:
            _remove_cache_file(cached_path)

    return _download_image_to_cache(uri, target_dir, max_bytes)


def image_uri_to_data_uri(
    uri: str,
    target_dir: str = TEMP_PATH,
    max_bytes: int = MAX_LOCAL_IMAGE_BYTES,
) -> str:
    """通过统一 URI 解析入口返回视觉模型使用的 Data URI。"""
    image_path, mime_type = resolve_image_uri(uri, target_dir, max_bytes)
    with open(image_path, 'rb') as image_file:
        encoded = base64.b64encode(image_file.read()).decode('ascii')
    return f'data:{mime_type};base64,{encoded}'
