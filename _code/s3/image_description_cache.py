"""Content-identity keyed cache for automatic image descriptions."""

from datetime import date, timedelta
import threading
from typing import Optional

from s3.image_identity import valid_description_identity


DESCRIPTION_CACHE_MAX_AGE_DAYS = 15
AUTO_IMAGE_DESCRIPTION_VERSION = 'auto-v1'
_prune_lock = threading.Lock()
_last_prune_by_cache = {}


def _entry(description: str, cached_at: date) -> dict:
    return {
        'description': description,
        'cached_at': cached_at.isoformat(),
        'prompt_version': AUTO_IMAGE_DESCRIPTION_VERSION,
    }


def get_cached_description(cache: dict, image_digest: str, today: date = None) -> Optional[str]:
    """Read and refresh a current automatic description for a content digest."""
    if not valid_description_identity(image_digest):
        return None
    today = today or date.today()
    entry = cache.get(image_digest)
    if isinstance(entry, dict) and isinstance(entry.get('description'), str):
        if entry.get('prompt_version') != AUTO_IMAGE_DESCRIPTION_VERSION:
            del cache[image_digest]
            return None
        try:
            cached_at = date.fromisoformat(entry['cached_at'])
        except (KeyError, TypeError, ValueError):
            cached_at = today
        if cached_at < today - timedelta(days=DESCRIPTION_CACHE_MAX_AGE_DAYS):
            del cache[image_digest]
            return None
        entry['cached_at'] = today.isoformat()
        return entry['description']
    return None


def prune_description_cache(
    cache: dict,
    today: date = None,
    max_age_days: int = DESCRIPTION_CACHE_MAX_AGE_DAYS,
) -> int:
    today = today or date.today()
    cutoff = today - timedelta(days=max_age_days)
    removed = 0
    for image_digest, entry in list(cache.items()):
        if (
            not valid_description_identity(image_digest)
            or not isinstance(entry, dict)
            or not isinstance(entry.get('description'), str)
            or entry.get('prompt_version') != AUTO_IMAGE_DESCRIPTION_VERSION
        ):
            del cache[image_digest]
            removed += 1
            continue
        try:
            cached_at = date.fromisoformat(entry['cached_at'])
        except (KeyError, TypeError, ValueError):
            entry['cached_at'] = today.isoformat()
            continue
        if cached_at < cutoff:
            del cache[image_digest]
            removed += 1
    return removed


def maybe_prune_description_cache(cache: dict, today: date = None) -> int:
    """Prune a cache at most once per day when image processing is active."""
    today = today or date.today()
    cache_id = id(cache)
    with _prune_lock:
        previous = _last_prune_by_cache.get(cache_id)
        if previous is not None and previous[0] is cache and previous[1] == today:
            return 0
        removed = prune_description_cache(cache, today=today)
        _last_prune_by_cache[cache_id] = (cache, today)
        return removed


def cache_description(
    cache: dict,
    image_digest: str,
    description: str,
    today: date = None,
) -> int:
    if not valid_description_identity(image_digest):
        raise ValueError('图片描述键必须是受支持的内容摘要')
    today = today or date.today()
    cache[image_digest] = _entry(description, today)
    return prune_description_cache(cache, today=today)
