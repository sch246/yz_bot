#!/usr/bin/env python3
"""Hard-cut the image cache from URL keys to content-hash identities.

Run while the Bot is stopped. The default mode only reports the migration.
Use ``--apply`` after reviewing the report. No network requests are made, and
the report never prints source URLs or description text.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.parse import urlparse

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / '_code'))

from s3.image_identity import (  # noqa: E402
    file_uri_to_path,
    hash_file,
    image_uri_alias_key,
    intrinsic_image_description_identity,
    valid_description_identity,
    valid_image_digest,
)
from s3.image_description_cache import AUTO_IMAGE_DESCRIPTION_VERSION  # noqa: E402


HASHED_IMAGE = re.compile(r'^(?P<stem>[0-9a-f]{32}|[0-9a-f]{64})(?P<ext>\.[A-Za-z0-9]+)?$')
FORMAT_EXTENSION = {
    'JPEG': '.jpg',
    'PNG': '.png',
    'GIF': '.gif',
    'WEBP': '.webp',
    'BMP': '.bmp',
    'TIFF': '.tif',
}


def read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open('r', encoding='utf-8') as file:
        value = json.load(file)
    if not isinstance(value, type(default)):
        raise ValueError(f'{path} 根类型应为 {type(default).__name__}')
    return value


def normalized_description(entry) -> dict | None:
    if isinstance(entry, str):
        description = entry
        cached_at = date.today().isoformat()
    elif isinstance(entry, dict) and isinstance(entry.get('description'), str):
        description = entry['description']
        cached_at = entry.get('cached_at', date.today().isoformat())
        try:
            date.fromisoformat(cached_at)
        except (TypeError, ValueError):
            cached_at = date.today().isoformat()
    else:
        return None
    return {
        'description': description,
        'cached_at': cached_at,
        'prompt_version': AUTO_IMAGE_DESCRIPTION_VERSION,
    }


def inspect_image(path: Path) -> tuple[str, str]:
    with Image.open(path) as image:
        image_format = image.format
        image.verify()
    extension = FORMAT_EXTENSION.get(image_format)
    if extension is None:
        raise ValueError(f'{path} 使用了不支持的图片格式 {image_format!r}')
    return hash_file(str(path)), extension


def scan_images(tmp_dir: Path):
    old_stem_to_digest = {}
    moves = []
    errors = []
    if not tmp_dir.exists():
        return old_stem_to_digest, moves, errors

    for path in sorted(tmp_dir.iterdir()):
        if not path.is_file():
            continue
        match = HASHED_IMAGE.fullmatch(path.name)
        if match is None:
            continue
        try:
            digest, extension = inspect_image(path)
        except Exception as error:
            errors.append(f'{path}: {error}')
            continue
        old_stem = match.group('stem')
        if len(old_stem) == 64 and old_stem != digest:
            errors.append(f'{path}: 64 位文件名与内容 SHA-256 不一致')
            continue
        old_stem_to_digest[old_stem] = digest
        target = tmp_dir / f'{digest}{extension}'
        if path != target:
            moves.append((path, target, digest))
    return old_stem_to_digest, moves, errors


def description_identity(
    source: str,
    old_stem_to_digest: dict[str, str],
) -> tuple[str | None, str | None]:
    """Return (description identity, resolvable SHA-256 alias target)."""
    if valid_description_identity(source):
        return source, None
    parsed = urlparse(source)
    if parsed.scheme.lower() == 'file':
        try:
            digest = hash_file(file_uri_to_path(source))
            return digest, digest
        except (OSError, ValueError):
            return None, None
    if parsed.scheme.lower() in {'http', 'https'}:
        legacy_stem = hashlib.md5(source.encode('utf-8')).hexdigest()
        digest = old_stem_to_digest.get(legacy_stem)
        if digest is not None:
            return digest, digest
        return intrinsic_image_description_identity(source), None
    return None, None


def choose_description(current: dict | None, candidate: dict) -> tuple[dict, bool]:
    if current is None or current == candidate:
        return candidate, False
    current_date = current.get('cached_at', '')
    candidate_date = candidate.get('cached_at', '')
    if candidate_date > current_date:
        return candidate, True
    if current_date > candidate_date:
        return current, True
    # JSON object order is stable. For an equal date, the later source wins.
    return candidate, True


def unresolved_category(source: str, *, invalid_entry: bool = False) -> str:
    if invalid_entry:
        return '描述条目格式无效'
    parsed = urlparse(source) if isinstance(source, str) else None
    host = (parsed.hostname or '').lower() if parsed else ''
    if host:
        return f'缺少原图且 URL 无可恢复摘要（{host}）'
    scheme = parsed.scheme.lower() if parsed else ''
    return f'无法识别的来源类型（{scheme or "非 URI"}）'


def build_migration(descriptions, aliases, old_stem_to_digest, *, drop_unresolved, force_conflicts):
    migrated_descriptions = {}
    migrated_aliases = dict(aliases)
    unresolved = Counter()
    conflicts = Counter()
    collapsed_description_variants = 0

    for source, raw_entry in descriptions.items():
        entry = normalized_description(raw_entry)
        if entry is None:
            unresolved[unresolved_category(source, invalid_entry=True)] += 1
            continue
        identity, alias_digest = description_identity(source, old_stem_to_digest)
        if identity is None:
            unresolved[unresolved_category(source)] += 1
            continue

        selected, collapsed = choose_description(migrated_descriptions.get(identity), entry)
        if collapsed:
            collapsed_description_variants += 1
        migrated_descriptions[identity] = selected

        # Only a known SHA-256 blob can back a URI alias. Intrinsic SHA-1/MD5
        # identities can recover descriptions, but cannot resolve image bytes.
        if alias_digest is not None and not valid_image_digest(source):
            try:
                alias_key = image_uri_alias_key(source)
            except ValueError:
                conflicts['无法建立 URI alias'] += 1
                continue
            previous = migrated_aliases.get(alias_key)
            previous_digest = previous.get('digest') if isinstance(previous, dict) else None
            if previous_digest not in (None, alias_digest):
                conflicts['同一 URI alias 指向不同 SHA-256'] += 1
                continue
            alias_date = entry['cached_at']
            if previous_digest == alias_digest:
                for field in ('resolved_at', 'last_used_at'):
                    previous_date = previous.get(field)
                    try:
                        date.fromisoformat(previous_date)
                    except (TypeError, ValueError):
                        continue
                    alias_date = max(alias_date, previous_date)
            migrated_aliases[alias_key] = {
                'digest': alias_digest,
                'resolved_at': alias_date,
                'last_used_at': alias_date,
            }

    if unresolved and not drop_unresolved:
        raise ValueError('存在无法迁移的描述；使用 --drop-unresolved 明确丢弃')
    if conflicts and not force_conflicts:
        raise ValueError('存在 URI alias 冲突；使用 --force-conflicts 保留先遇到的映射')
    return (
        migrated_descriptions,
        migrated_aliases,
        unresolved,
        conflicts,
        collapsed_description_variants,
    )


def atomic_write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(value, file, ensure_ascii=False, indent=4)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass
        raise


def backup_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def apply_migration(data_dir, description_path, alias_path, moves, descriptions, aliases):
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = data_dir / 'migrations' / f'image-cache-{stamp}'
    backup_dir.mkdir(parents=True, exist_ok=False)

    for source in (description_path, alias_path):
        if source.exists():
            backup_file(source, backup_dir / source.relative_to(data_dir))
    for source, _, _ in moves:
        if source.exists():
            backup_file(source, backup_dir / source.relative_to(data_dir))

    manifest = {
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'moves': [
            {'from': str(source.relative_to(data_dir)), 'to': str(target.relative_to(data_dir))}
            for source, target, _ in moves
        ],
    }
    atomic_write_json(backup_dir / 'manifest.json', manifest)

    for source, target, digest in moves:
        if not source.exists():
            continue
        if target.exists():
            if hash_file(str(target)) != digest:
                raise ValueError(f'目标文件内容冲突: {target}')
            source.unlink()
        else:
            os.replace(source, target)

    atomic_write_json(description_path, descriptions)
    atomic_write_json(alias_path, aliases)
    return backup_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    parser.add_argument('--apply', action='store_true', help='实际写入；省略时只报告')
    parser.add_argument('--drop-unresolved', action='store_true', help='明确丢弃没有对应原图的旧描述')
    parser.add_argument('--force-conflicts', action='store_true', help='保留发生冲突时先遇到的 URI alias')
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    tmp_dir = data_dir / 'tmp_files'
    storage_dir = data_dir / 'storage' / 'llm_system'
    description_path = storage_dir / 'description_cache.json'
    alias_path = storage_dir / 'image_uri_aliases.json'

    descriptions = read_json(description_path, {})
    aliases = read_json(alias_path, {})
    old_stem_to_digest, moves, image_errors = scan_images(tmp_dir)
    if image_errors:
        print('\n'.join(f'ERROR {error}' for error in image_errors), file=sys.stderr)
        return 2

    try:
        (
            migrated_descriptions,
            migrated_aliases,
            unresolved,
            conflicts,
            collapsed_description_variants,
        ) = build_migration(
            descriptions,
            aliases,
            old_stem_to_digest,
            drop_unresolved=args.drop_unresolved,
            force_conflicts=args.force_conflicts,
        )
    except ValueError as error:
        # Rebuild permissively so the report still explains what blocks apply.
        (
            migrated_descriptions,
            migrated_aliases,
            unresolved,
            conflicts,
            collapsed_description_variants,
        ) = build_migration(
            descriptions,
            aliases,
            old_stem_to_digest,
            drop_unresolved=True,
            force_conflicts=True,
        )
        print(f'BLOCKED: {error}', file=sys.stderr)

    print(f'缓存文件改名/去重: {len(moves)}')
    print(f'摘要描述: {len(migrated_descriptions)}')
    print(f'URI aliases: {len(migrated_aliases)}')
    print(f'无法迁移描述: {sum(unresolved.values())}')
    print(f'合并的重复描述版本: {collapsed_description_variants}')
    print(f'URI alias 冲突: {sum(conflicts.values())}')
    for category, count in sorted(unresolved.items()):
        print(f'UNRESOLVED {category}: {count}')
    for category, count in sorted(conflicts.items()):
        print(f'CONFLICT {category}: {count}')

    blocked = (unresolved and not args.drop_unresolved) or (conflicts and not args.force_conflicts)
    if not args.apply:
        print('DRY RUN：未修改任何文件')
        return 2 if blocked else 0
    if blocked:
        print('未执行迁移；请显式选择 unresolved/conflict 策略', file=sys.stderr)
        return 2

    backup_dir = apply_migration(
        data_dir,
        description_path,
        alias_path,
        moves,
        migrated_descriptions,
        migrated_aliases,
    )
    print(f'迁移完成；备份位于 {backup_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
