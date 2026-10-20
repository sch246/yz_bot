"""Synthetic single-writer Markdown revisions. No model generation or scheduling.

The current .md is the only current-version authority. Its first line is readable
metadata; all following bytes are the Markdown body. Immutable versions are
verification/history evidence, never a fallback current pointer. Each file commits
independently. Callers must explicitly authorize scopes for each read and trigger.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time

PREFIX = b'<!-- stateless-note '
SUFFIX = b' -->\n'
KINDS = {'observation', 'quote', 'inference', 'correction', 'draft', 'attempt', 'success', 'unknown'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', value):
        raise ValueError('unsafe identity')
    return value


def _key(value):
    if not isinstance(value, str) or value.count('/') != 1:
        raise ValueError('scope/identity required')
    scope, identity = value.split('/')
    if scope not in {'person', 'group', 'global'} or (scope == 'global' and identity != 'main'):
        raise ValueError('invalid scope')
    _id(identity)
    return value


def _root(value):
    path = Path(value).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('symlink root refused')
    return path


def _path(root, relative):
    path = root / relative
    if not path.is_relative_to(root) or any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('symlink/escape refused')
    if path.exists() and path.is_file() and path.stat().st_nlink != 1:
        raise ValueError('shared hardlink refused')
    return path


def _read_json(path):
    envelope = json.loads(path.read_bytes())
    if set(envelope) != {'value', 'sha256'} or _hash(_json(envelope['value'])) != envelope['sha256']:
        raise ValueError('evidence checksum drift')
    return envelope['value']


def _pack(value):
    return _json({'value': value, 'sha256': _hash(_json(value))})


def _decode(raw):
    line, separator, body = raw.partition(b'\n')
    if not separator or not line.startswith(PREFIX) or not line.endswith(SUFFIX[:-1]):
        raise ValueError('missing note metadata')
    metadata = json.loads(line[len(PREFIX):-len(SUFFIX[:-1])])
    identity = metadata.pop('version_id')
    if (_hash(_json(metadata) + b'\n' + body) != identity or metadata['body_sha256'] != _hash(body)
            or metadata['schema'] != 'stateless-note-v1'):
        raise ValueError('external edit/version checksum drift')
    body.decode('utf-8', errors='strict')
    return {**metadata, 'version_id': identity}, body


def _encode(metadata, body):
    body.decode('utf-8', errors='strict')
    metadata = {**metadata, 'schema': 'stateless-note-v1', 'body_sha256': _hash(body)}
    metadata['version_id'] = _hash(_json(metadata) + b'\n' + body)
    return PREFIX + _json(metadata) + SUFFIX + body


def _manifest(root):
    value = _read_json(_path(root, 'manifest.json'))
    if value['schema'] != 'stateless-notes-v1' or value['synthetic'] is not True:
        raise ValueError('only synthetic note roots supported')
    _id(value['branch'])
    return value


def _receipts(root, key):
    """Immutable applied-commit audit, used only to detect external rewinds.

    Receipts cannot supply current bytes or select a fallback. A missing/broken
    current file always fails, even when receipts and history remain intact.
    """
    rows = []
    for number, path in enumerate(sorted(_path(root, 'receipts').glob('*.json')), 1):
        if path.name != f'{number:06d}.json':
            raise ValueError('receipt audit gap')
        row = _read_json(_path(root, 'receipts/' + path.name))
        if row['key'] == key:
            raw = _path(root, 'versions/' + _id(row['version_id']) + '.md').read_bytes()
            metadata, _ = _decode(raw)
            if row['sha256'] != _hash(raw) or metadata['key'] != key:
                raise ValueError('receipt version/hash drift')
            rows.append(row)
    return rows


def _current(root, key, *, check_receipts=True):
    """Validate the whole ancestor chain; never select an orphan archive."""
    path = _path(root, 'current/' + _key(key) + '.md')
    if not path.exists():
        # Evidence proves prior activity, never which archived version is current.
        # Missing Markdown must not silently create a second root revision.
        for archive in _path(root, 'versions').glob('*.md'):
            metadata, _ = _decode(_path(root, 'versions/' + archive.name).read_bytes())
            if metadata['key'] == key:
                raise ValueError('current Markdown missing despite revision evidence')
        return None
    raw = path.read_bytes()
    metadata, body = _decode(raw)
    current = {'metadata': metadata, 'body': body, 'raw': raw, 'sha256': _hash(raw)}
    seen = set()
    while True:
        version = metadata['version_id']
        _id(version)
        if version in seen or metadata['key'] != key or metadata['branch'] != _manifest(root)['branch']:
            raise ValueError('version cycle/key/branch drift')
        seen.add(version)
        if _path(root, 'versions/' + version + '.md').read_bytes() != raw:
            raise ValueError('immutable version evidence drift')
        parent = metadata['parent_version']
        if parent is None:
            if metadata['parent_sha256'] is not None:
                raise ValueError('root parent hash drift')
            break
        raw = _path(root, 'versions/' + _id(parent) + '.md').read_bytes()
        if _hash(raw) != metadata['parent_sha256']:
            raise ValueError('parent bytes drift')
        metadata, _ = _decode(raw)
    if check_receipts:
        receipts = _receipts(root, key)
        if (not receipts or receipts[-1]['version_id'] != current['metadata']['version_id']
                or any(row['version_id'] not in seen for row in receipts)):
            raise ValueError('current differs from applied commit audit; observed external edit/rewind')
    return current


def read_committed(root, key, snapshot, *, allowed_keys):
    """Return one exact frozen T10 material, or None for absent/deleted notes.

    The caller's explicit allowed_keys is an access decision, not inferred from a
    person's presence in a group. Private authorization is not supplied by T07.
    """
    root = _root(root)
    _manifest(root)
    if _path(root, 'persistence-unknown.json').exists() or any(root.rglob('*.new')):
        raise ValueError('note persistence unknown; use read-only diagnosis')
    key = _key(key)
    if key not in {_key(item) for item in allowed_keys}:
        raise ValueError('scope not authorized')
    if _path(root, 'pauses/' + key + '.json').exists():
        raise ValueError('note paused for observed external edit')
    current = _current(root, key)
    if current is None:
        return None
    metadata = current['metadata']
    if (metadata['source_frozen_id'] != snapshot['source_sha256']
            or metadata['cursor'] > snapshot['opened_cursor']):
        raise ValueError('foreign/future note source cutoff')
    if metadata['operation'] == 'delete':
        return None
    return {'kind': 'note', 'source_id': key, 'version_id': metadata['version_id'],
            'snapshot_id': snapshot['snapshot_id'], 'sha256': current['sha256'], 'raw': current['raw']}


def snapshot_notes(root, snapshot, *, allowed_keys):
    """Explicit current-only selection; no candidate/history directory scan."""
    keys = tuple(sorted({_key(key) for key in allowed_keys}))
    return tuple(value for key in keys
                 if (value := read_committed(root, key, snapshot, allowed_keys=keys)) is not None)


def recover_notes(root):
    """Read-only diagnosis; valid old/new versions may coexist with unknown IO.

    Unknown durability blocks writers, but never causes replay, auto retry or a
    guess that an orphan immutable version was committed.
    """
    root = _root(root)
    _manifest(root)
    notes, rejected = {}, {}
    keys = {str(path.relative_to(root / 'current'))[:-3] for path in _path(root, 'current').rglob('*.md')}
    for archive in _path(root, 'versions').glob('*.md'):
        try:
            metadata, _ = _decode(_path(root, 'versions/' + archive.name).read_bytes())
            keys.add(_key(metadata['key']))
        except (OSError, ValueError, KeyError, TypeError) as error:
            rejected['archive:' + archive.name] = str(error)
    for key in sorted(keys):
        try:
            value = _current(root, key, check_receipts=False)
            receipt_error = None
            try:
                confirmed = _current(root, key)['metadata']['version_id'] == value['metadata']['version_id']
            except (OSError, ValueError, KeyError, TypeError) as error:
                confirmed, receipt_error = False, str(error)
            notes[key] = {'version_id': value['metadata']['version_id'], 'sha256': value['sha256'],
                          'operation': value['metadata']['operation'], 'applied_audit_confirmed': confirmed,
                          'audit_error': receipt_error}
        except (OSError, ValueError, KeyError, TypeError) as error:
            rejected[key] = str(error)
    residual = [str(path.relative_to(root)) for path in root.rglob('*.new')]
    paused = [str(path.relative_to(root / 'pauses'))[:-5] for path in _path(root, 'pauses').rglob('*.json')]
    return {'notes': notes, 'rejected': rejected, 'paused': paused, 'residual': residual,
            'persistence_unknown': _path(root, 'persistence-unknown.json').exists(),
            'automatic_requests': 0, 'automatic_tools': 0}


class NoteWriter:
    """One short cooperative flock per mutation; generation happens elsewhere."""
    def __init__(self, root):
        self.root = _root(root)
        self.manifest = _manifest(self.root)
        self.poisoned = False

    @classmethod
    def create(cls, root, *, branch='main'):
        from comparison_snapshot import _safe_root
        root = _safe_root(root)
        root.mkdir(mode=0o700, parents=True)
        for name in ('current', 'versions', 'receipts', 'candidates', 'triggers', 'pauses', 'observations'):
            (root / name).mkdir(mode=0o700)
        writer = object.__new__(cls)
        writer.root, writer.poisoned = root, False
        writer._save('manifest.json', {'schema': 'stateless-notes-v1', 'synthetic': True, 'branch': _id(branch)})
        return cls(root)

    @contextmanager
    def _locked(self):
        if self.poisoned:
            raise RuntimeError('note writer closed after persistence failure')
        with _path(self.root, 'writer.lock').open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                if (_path(self.root, 'persistence-unknown.json').exists()
                        or any(self.root.rglob('*.new')) or _manifest(self.root) != self.manifest):
                    raise RuntimeError('note persistence unknown/manifest drift; writes closed')
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _fail(self, error):
        self.poisoned = True
        # Best effort diagnostic. Complete old/new current files remain readable;
        # absence of this marker under a broken filesystem is no durability proof.
        try:
            _path(self.root, 'persistence-unknown.json').write_bytes(_pack({'error': str(error)}))
        except OSError:
            pass

    def _atomic(self, relative, raw, *, stage, fault=None):
        try:
            path = _path(self.root, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = _path(self.root, relative + '.new')
            if relative.startswith('versions/') and path.exists():
                if path.read_bytes() != raw:
                    raise ValueError('immutable version overwrite refused')
                return
            if fault == stage + '-before':
                raise OSError('synthetic ' + fault)
            with temporary.open('xb') as stream:
                if fault == stage + '-partial':
                    stream.write(raw[:max(1, len(raw) // 2)])
                    stream.flush()
                    raise OSError('synthetic ' + fault)
                stream.write(raw)
                if fault == stage + '-flush':
                    raise OSError('synthetic ' + fault)
                stream.flush()
                if fault == stage + '-fsync':
                    raise OSError('synthetic ' + fault)
                os.fsync(stream.fileno())
            if fault == stage + '-replace':
                raise OSError('synthetic ' + fault)
            os.replace(temporary, path)
            if fault == stage + '-after-replace':
                raise OSError('synthetic ' + fault)
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                if fault == stage + '-dir-fsync':
                    raise OSError('synthetic ' + fault)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except BaseException as error:
            self._fail(error)
            raise

    def _save(self, relative, value, *, stage='audit', fault=None):
        self._atomic(relative, _pack(value), stage=stage, fault=fault)

    def register_trigger(self, loop_root, wake_id, snapshot, *, allowed_keys):
        """Idempotent wake registration, independent of terminal/processing status."""
        from stateless_loop import recover_commits
        state = recover_commits(loop_root)
        outputs = [value for value in state['outputs'].values() if value['wake_id'] == wake_id]
        if not outputs and wake_id not in state['terminals']:
            raise ValueError('wake not committed')
        if any(value['snapshot_id'] != snapshot['snapshot_id'] for value in outputs):
            raise ValueError('wake snapshot mismatch')
        identity = _hash(wake_id.encode())
        value = {'id': identity, 'wake_id': wake_id, 'snapshot_id': snapshot['snapshot_id'],
                 'source_frozen_id': snapshot['source_sha256'], 'cursor': snapshot['opened_cursor'],
                 'branch': self.manifest['branch'], 'allowed_keys': sorted({_key(key) for key in allowed_keys}),
                 'registered_at': time.time(), 'status': 'pending', 'reason': 'registered; terminal not yet admitted',
                 'cutoff': None, 'attempts': []}
        with self._locked():
            path = _path(self.root, 'triggers/' + identity + '.json')
            if path.exists():
                old = _read_json(path)
                if any(old[key] != value[key] for key in ('wake_id', 'snapshot_id', 'branch', 'allowed_keys')):
                    raise ValueError('trigger identity drift')
                return deepcopy(old)
            self._save('triggers/' + identity + '.json', value)
        return deepcopy(value)

    def admit_trigger(self, trigger_id, loop_root, snapshot, *, note_aliases=None):
        """Bind T10 terminal and exact chat exposure, with frozen note aliases.

        Selector requests remain in the terminal request/hash/role cutoff, but
        their catalog/screen is not supported as a candidate body source here.
        A catalog title is not evidence that its note's full text was exposed.
        """
        from a0_ledger import digest
        from comparison_snapshot import resolve_ref
        from stateless_loop import recover_commits
        state = recover_commits(loop_root)
        with self._locked():
            relative = 'triggers/' + _id(trigger_id) + '.json'
            task = _read_json(_path(self.root, relative))
            if task['snapshot_id'] != snapshot['snapshot_id']:
                raise ValueError('trigger snapshot mismatch')
            if note_aliases is not None and type(note_aliases) is not dict:
                raise ValueError('ordinary frozen note alias mapping required')
            aliases = dict(note_aliases or {})
            for alias, key in aliases.items():
                if (not isinstance(alias, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', alias)
                        or _key(key) not in task['allowed_keys']):
                    raise ValueError('invalid note alias or unauthorized target scope')
            terminal = state['terminals'].get(task['wake_id'])
            if terminal is None:
                return {'status': 'pending', 'reason': 'no terminal; no processing permission'}
            exposures = []
            outputs = {key: value for key, value in state['outputs'].items() if value['wake_id'] == task['wake_id']}
            evidence, roles = {}, {}
            for path in sorted(Path(loop_root).glob('request-*.json')):
                if path.is_symlink():
                    raise ValueError('symlink request evidence')
                saved = json.loads(path.read_bytes())
                envelope = saved['envelope']
                if envelope['request_id'] in terminal['request_ids']:
                    if (saved['attribution']['task_id'] != task['wake_id']
                            or saved['attribution']['snapshot_id'] != task['snapshot_id']
                            or _hash(envelope['serialized_body'].encode()) != envelope['body_sha256']
                            or json.loads(envelope['serialized_body']) != envelope['body']):
                        raise ValueError('request cutoff/evidence drift')
                    role = saved['attribution']['role_id']
                    if role not in {'chat', 'selector'}:
                        raise ValueError('unsupported role in chat terminal cutoff')
                    if envelope['request_id'] in evidence:
                        raise ValueError('duplicate terminal request evidence')
                    evidence[envelope['request_id']] = envelope
                    roles[envelope['request_id']] = role
            if set(evidence) != set(terminal['request_ids']):
                raise ValueError('missing actual request evidence')
            for output in outputs.values():
                if (output['request_id'] not in evidence or roles[output['request_id']] != 'chat'
                        or output['context_sha256'] != digest(evidence[output['request_id']]['body']['messages'])):
                    raise ValueError('output does not match actual wire evidence')
            for request_id, envelope in evidence.items():
                if roles[request_id] == 'selector':
                    continue
                context = json.loads(envelope['body']['messages'][1]['content'])
                if context['screen']['snapshot_id'] != task['snapshot_id']:
                    raise ValueError('wire snapshot mismatch')
                records = context['screen']['records'] + context['selected']['references']
                for item in records:
                    resolved = resolve_ref(snapshot, item['ref'])
                    if resolved['raw'] != item['raw'].encode() or resolved['sha256'] != item['sha256']:
                        raise ValueError('wire original bytes drift')
                    exposures.append({'kind': 'record', 'request_id': request_id, 'ref': item['ref'],
                                      'sha256': resolved['sha256'], 'author_id': str(resolved['record'].get('user_id')),
                                      'window': resolved['source_alias']})
                for item in context['selected']['materials']:
                    if (item['snapshot_id'] != task['snapshot_id']
                            or _hash(item['text'].encode()) != item['sha256']):
                        raise ValueError('wire material bytes drift')
                    if item['kind'] == 'note':
                        note_key = _key(aliases.get(item['source_id'], item['source_id']))
                        archived = _path(self.root, 'versions/' + _id(item['version_id']) + '.md').read_bytes()
                        note_metadata, _ = _decode(archived)
                        if (note_key not in task['allowed_keys'] or note_metadata['key'] != note_key
                                or note_metadata['version_id'] != item['version_id']
                                or note_metadata['branch'] != task['branch']
                                or note_metadata['source_frozen_id'] != task['source_frozen_id']
                                or note_metadata['cursor'] > task['cursor']
                                or note_metadata['operation'] == 'delete' or archived != item['text'].encode()
                                or item['version_id'] not in {row['version_id'] for row in _receipts(self.root, note_key)}):
                            raise ValueError('illegal exposed note scope/version/cutoff')
                    exposure = {'kind': 'material', 'material_kind': item['kind'], 'request_id': request_id,
                                **{key: item[key] for key in ('source_id', 'version_id', 'sha256')}}
                    if item['kind'] == 'note':
                        exposure['note_key'] = note_key
                    exposures.append(exposure)
            for output_id, output in outputs.items():
                exposures.append({'kind': 'generated', 'output_id': output_id, 'assistant': output['assistant']})
                for action, status in state['actions'].items():
                    if action.rsplit('#', 1)[0] == output_id:
                        result = state['results'].get(action)
                        exposures.append({'kind': 'execution', 'action': action, 'status': status, 'result': result})
                        if result and result.get('content'):
                            content = json.loads(result['content'])
                            if 'ref' in content:
                                resolved = resolve_ref(snapshot, content['ref'])
                                if content['raw'].encode() != resolved['raw']:
                                    raise ValueError('result original bytes drift')
                                exposures.append({'kind': 'record', 'request_id': output['request_id'], 'ref': content['ref'],
                                                  'sha256': resolved['sha256'], 'author_id': str(resolved['record'].get('user_id')),
                                                  'window': resolved['source_alias']})
            exposures = {digest(item): item for item in exposures}
            cutoff = {'terminal': terminal, 'terminal_sha256': digest(terminal), 'exposures': exposures,
                      'request_hashes': {key: value['body_sha256'] for key, value in evidence.items()},
                      'request_roles': roles, 'note_aliases': aliases, 'note_aliases_sha256': digest(aliases),
                      'selector_body_sources': 'unsupported; request role/hash only'}
            if task['cutoff'] is not None and task['cutoff'] != cutoff:
                raise ValueError('terminal cutoff changed')
            task['cutoff'] = cutoff
            task['reason'] = 'terminal and actual exposure cutoff bound; explicit processing only'
            self._save(relative, task)
            return deepcopy(task)

    def finish_trigger(self, trigger_id, status, reason):
        if status not in {'no-op', 'failed', 'not-admitted', 'success'} or not reason:
            raise ValueError('explicit outcome/reason required')
        with self._locked():
            relative = 'triggers/' + _id(trigger_id) + '.json'
            task = _read_json(_path(self.root, relative))
            if status in {'success', 'no-op'} and (not task['cutoff']
                    or task['cutoff']['terminal']['status'] != 'complete'):
                raise ValueError('processing outcome needs complete terminal cutoff')
            task['status'], task['reason'] = status, reason
            task['attempts'].append({'status': status, 'reason': reason, 'at': time.time()})
            self._save(relative, task)
            return deepcopy(task)

    def _validate(self, candidate, task, current):
        """One policy for prepared, persisted and manually adopted proposals."""
        fields = {'schema', 'trigger_id', 'key', 'branch', 'operation', 'body', 'body_sha256',
                  'parent_version', 'parent_sha256', 'source_frozen_id', 'snapshot_id', 'cursor',
                  'generation', 'cutoff_sha256', 'sources', 'reason', 'expectation', 'statements', 'restore_version'}
        if set(candidate) != fields or candidate['schema'] != 'stateless-note-candidate-v1':
            raise ValueError('invalid candidate fields')
        if not task['cutoff'] or task['cutoff']['terminal']['status'] != 'complete':
            raise ValueError('incomplete/cancelled/failed wake; no processing permission')
        key, operation = _key(candidate['key']), candidate['operation']
        if (candidate['branch'] != self.manifest['branch'] or task['branch'] != candidate['branch']
                or key not in task['allowed_keys']):
            raise ValueError('foreign branch/wrong scope')
        for field in ('source_frozen_id', 'snapshot_id', 'cursor'):
            if candidate[field] != task[field]:
                raise ValueError('candidate source/cutoff drift')
        if (candidate['generation'] != task['cutoff']['terminal']['generation']
                or candidate['cutoff_sha256'] != _hash(_json(task['cutoff']))
                or candidate['trigger_id'] != task['id']):
            raise ValueError('candidate terminal cutoff drift')
        if (not isinstance(candidate['body'], str) or operation not in {'write', 'delete', 'restore', 'rollback'}
                or _hash(candidate['body'].encode()) != candidate['body_sha256']):
            raise ValueError('invalid operation/Markdown bytes')
        exposures, sources = task['cutoff']['exposures'], candidate['sources']
        if (not isinstance(sources, list) or not sources or set(sources) - set(exposures)
                or not candidate['reason'] or not candidate['expectation']):
            raise ValueError('unexposed/future source or missing reason/expectation')
        scope, identity = key.split('/')
        if scope in {'person', 'group'}:
            field = 'author_id' if scope == 'person' else 'window'
            if not any(exposures[source].get(field) == identity for source in sources):
                raise ValueError('source does not establish target stable identity/scope')
        for statement in candidate['statements']:
            if (set(statement) != {'kind', 'text', 'sources'} or statement['kind'] not in KINDS
                    or not statement['sources'] or set(statement['sources']) - set(sources)):
                raise ValueError('invalid typed statement provenance')
            if statement['kind'] == 'success' and not any(
                    exposures[source].get('kind') == 'execution' and exposures[source].get('status') == 'completed'
                    for source in statement['sources']):
                raise ValueError('successful action needs completed execution evidence')
        if current and (task['source_frozen_id'] != current['metadata']['source_frozen_id']
                        or task['cursor'] < current['metadata']['cursor']
                        or candidate['generation'] < current['metadata']['generation']):
            raise ValueError('older wake cutoff/foreign source cannot revise newer note baseline')
        if operation == 'delete' and (candidate['body'] or current is None):
            raise ValueError('delete requires existing note and empty body')
        if operation not in {'restore', 'rollback'}:
            if candidate['restore_version'] is not None:
                raise ValueError('unexpected restore version')
            return
        version = _id(candidate['restore_version'])
        old_raw = _path(self.root, 'versions/' + version + '.md').read_bytes()
        old, body = _decode(old_raw)
        if (old['key'] != key or old['branch'] != task['branch'] or body != candidate['body'].encode()
                or old['cursor'] > task['cursor'] or old['source_frozen_id'] != task['source_frozen_id']):
            raise ValueError('restore version/key/bytes mismatch')
        ancestor, ancestors = current, set()
        while ancestor is not None:
            ancestors.add(ancestor['metadata']['version_id'])
            previous = ancestor['metadata']['parent_version']
            if previous is None:
                break
            previous_raw = _path(self.root, 'versions/' + _id(previous) + '.md').read_bytes()
            previous_metadata, previous_body = _decode(previous_raw)
            ancestor = {'metadata': previous_metadata, 'body': previous_body}
        if version not in ancestors:
            raise ValueError('restore requires committed ancestor, not orphan audit bytes')
        if current is None or (operation == 'restore' and current['metadata']['operation'] != 'delete'):
            raise ValueError('restore needs current tombstone')

    def prepare_candidate(self, trigger_id, key, body, *, operation='write', sources, reason, expectation,
                          statements=(), restore_version=None, branch=None):
        """Persist supplied synthetic/model bytes, not a librarian implementation."""
        if type(body) is not bytes or operation not in {'write', 'delete', 'restore', 'rollback'}:
            raise ValueError('single Markdown bytes operation required')
        body.decode('utf-8', errors='strict')
        key = _key(key)
        with self._locked():
            task = _read_json(_path(self.root, 'triggers/' + _id(trigger_id) + '.json'))
            if not task['cutoff']:
                raise ValueError('incomplete wake; no processing permission')
            try:
                current = _current(self.root, key)
            except (OSError, ValueError, KeyError, TypeError) as error:
                self._observe(key, str(error))
                raise ValueError('observed external edit; paused') from error
            if _path(self.root, 'pauses/' + key + '.json').exists():
                raise ValueError('note paused')
            candidate = {'schema': 'stateless-note-candidate-v1', 'trigger_id': trigger_id, 'key': key,
                         'branch': branch or self.manifest['branch'], 'operation': operation, 'body': body.decode(), 'body_sha256': _hash(body),
                         'parent_version': current['metadata']['version_id'] if current else None,
                         'parent_sha256': current['sha256'] if current else None,
                         'source_frozen_id': task['source_frozen_id'], 'snapshot_id': task['snapshot_id'], 'cursor': task['cursor'],
                         'generation': task['cutoff']['terminal']['generation'] if task['cutoff'] else None,
                         'cutoff_sha256': _hash(_json(task['cutoff'])), 'sources': sorted(set(sources)),
                         'reason': reason, 'expectation': expectation, 'statements': list(statements),
                         'restore_version': restore_version}
            self._validate(candidate, task, current)
            identity = _hash(_json(candidate))
            relative = 'candidates/' + identity + '.json'
            path = _path(self.root, relative)
            if not path.exists():
                self._save(relative, candidate, stage='candidate')
            elif _read_json(path) != candidate:
                raise ValueError('candidate identity collision')
            return identity

    def _receipt(self, key, version_id, raw, *, fault=None):
        number = len(list(_path(self.root, 'receipts').glob('*.json'))) + 1
        self._save(f'receipts/{number:06d}.json', {'key': key, 'version_id': version_id, 'sha256': _hash(raw)},
                   stage='receipt', fault=fault)

    def _observe(self, key, reason):
        path = _path(self.root, 'current/' + key + '.md')
        raw = path.read_bytes() if path.exists() else b''
        observation = _hash(raw)
        self._atomic('observations/' + observation + '.md', raw, stage='observation')
        receipts = _receipts(self.root, key)
        claimed = receipts[-1]['version_id'] if receipts else None
        pause = {'key': key, 'observed_sha256': observation, 'expected_version': claimed, 'reason': reason}
        self._save('pauses/' + key + '.json', pause)
        return deepcopy(pause)

    def pause_note(self, key, *, reason):
        """Pause before a human edit; call again afterwards to bind observed bytes."""
        if not reason:
            raise ValueError('explicit pause reason required')
        with self._locked():
            return self._observe(_key(key), reason)

    def commit_candidate(self, candidate_id, *, fault=None):
        """Only write gateway. Stale same-file candidates are never merged."""
        with self._locked():
            candidate = _read_json(_path(self.root, 'candidates/' + _id(candidate_id) + '.json'))
            if _hash(_json(candidate)) != candidate_id or candidate['branch'] != self.manifest['branch']:
                raise ValueError('candidate id/branch drift')
            key = _key(candidate['key'])
            task = _read_json(_path(self.root, 'triggers/' + _id(candidate['trigger_id']) + '.json'))
            if _path(self.root, 'pauses/' + key + '.json').exists():
                return {'status': 'rejected', 'reason': 'note paused'}
            try:
                current = _current(self.root, key)
            except (OSError, ValueError, KeyError, TypeError) as error:
                self._observe(key, str(error))
                return {'status': 'rejected', 'reason': 'observed external edit; paused'}
            # Idempotency follows only the committed current ancestor chain.
            ancestor = current
            while ancestor is not None:
                if ancestor['metadata']['candidate_id'] == candidate_id:
                    return {'status': 'no-op', 'reason': 'candidate already committed',
                            'version_id': ancestor['metadata']['version_id']}
                parent = ancestor['metadata']['parent_version']
                if parent is None:
                    break
                raw = _path(self.root, 'versions/' + _id(parent) + '.md').read_bytes()
                metadata, body = _decode(raw)
                ancestor = {'metadata': metadata, 'body': body, 'raw': raw}
            try:
                self._validate(candidate, task, current)
            except (ValueError, OSError, KeyError, TypeError) as error:
                return {'status': 'rejected', 'reason': str(error)}
            parent = current['metadata']['version_id'] if current else None
            parent_hash = current['sha256'] if current else None
            if (parent, parent_hash) != (candidate['parent_version'], candidate['parent_sha256']):
                return {'status': 'stale', 'reason': 'parent version/hash changed; candidate retained'}
            body = candidate['body'].encode()
            if _hash(body) != candidate['body_sha256']:
                return {'status': 'rejected', 'reason': 'candidate bytes drift'}
            if current and current['body'] == body and current['metadata']['operation'] == candidate['operation']:
                return {'status': 'no-op', 'reason': 'identical body and operation'}
            metadata = {key: deepcopy(value) for key, value in candidate.items() if key not in {'body', 'schema'}}
            metadata['candidate_id'] = candidate_id
            raw = _encode(metadata, body)
            version, _ = _decode(raw)
            try:
                if current:
                    # Already immutable, but verify previous evidence before
                    # writing audit/new bytes. A failed backup check closes us.
                    if fault == 'previous':
                        raise OSError('synthetic previous-version evidence failure')
                    assert _path(self.root, 'versions/' + parent + '.md').read_bytes() == current['raw']
                self._atomic('versions/' + version['version_id'] + '.md', raw, stage='audit', fault=fault)
                self._atomic('current/' + key + '.md', raw, stage='current', fault=fault)
                self._receipt(key, version['version_id'], raw, fault=fault)
            except BaseException as error:
                self._fail(error)
                return {'status': 'failed', 'reason': str(error), 'persistence': 'unknown; inspect read-only recovery'}
            return {'status': 'committed', 'version_id': version['version_id'], 'sha256': _hash(raw)}

    def rebaseline(self, key, *, expected_observation, parent_version, trigger_id, sources, reason, expectation):
        """Explicitly adopt observed human body as a NEW revision, keeping evidence.

        Caller pauses editing or shares writer.lock throughout this operation.
        This is a synthetic maintenance entry, not production editor integration.
        """
        key = _key(key)
        with self._locked():
            pause = _read_json(_path(self.root, 'pauses/' + key + '.json'))
            path = _path(self.root, 'current/' + key + '.md')
            observed = path.read_bytes()
            if pause['observed_sha256'] != expected_observation or _hash(observed) != expected_observation:
                raise ValueError('manual edit changed after observation')
            parent_raw = _path(self.root, 'versions/' + _id(parent_version) + '.md').read_bytes()
            parent, _ = _decode(parent_raw)
            if (parent_version != pause['expected_version'] or parent['key'] != key
                    or parent['branch'] != self.manifest['branch']):
                raise ValueError('manual baseline parent scope/branch mismatch')
            task = _read_json(_path(self.root, 'triggers/' + _id(trigger_id) + '.json'))
            # Preserve everything after the recognizable metadata line; invalid
            # header text is kept in the immutable observation, never guessed.
            if not observed.startswith(PREFIX) or b'\n' not in observed:
                raise ValueError('repair metadata header explicitly before rebasing')
            body = observed.partition(b'\n')[2]
            proposal = {'schema': 'stateless-note-candidate-v1', 'trigger_id': trigger_id, 'key': key,
                        'branch': self.manifest['branch'], 'operation': 'write', 'body': body.decode('utf-8'),
                        'body_sha256': _hash(body), 'parent_version': parent_version, 'parent_sha256': _hash(parent_raw),
                        'source_frozen_id': task['source_frozen_id'], 'snapshot_id': task['snapshot_id'],
                        'cursor': task['cursor'], 'generation': task['cutoff']['terminal']['generation'] if task['cutoff'] else None,
                        'cutoff_sha256': _hash(_json(task['cutoff'])), 'sources': list(sources), 'reason': reason,
                        'expectation': expectation, 'statements': [], 'restore_version': None}
            self._validate(proposal, task, {'metadata': parent, 'raw': parent_raw})
            metadata = {key: value for key, value in parent.items()
                        if key not in {'version_id', 'schema', 'body_sha256'}}
            metadata.update(operation='manual-baseline', parent_version=parent_version, parent_sha256=_hash(parent_raw),
                            candidate_id='manual-' + expected_observation, trigger_id=trigger_id,
                            sources=sorted(sources), reason=reason, expectation=expectation,
                            snapshot_id=task['snapshot_id'], cursor=task['cursor'], source_frozen_id=task['source_frozen_id'],
                            generation=task['cutoff']['terminal']['generation'],
                            cutoff_sha256=_hash(_json(task['cutoff'])), observation_sha256=expected_observation,
                            statements=[], restore_version=None)
            raw = _encode(metadata, body)
            version, _ = _decode(raw)
            self._atomic('versions/' + version['version_id'] + '.md', raw, stage='audit')
            self._atomic('current/' + key + '.md', raw, stage='current')
            self._receipt(key, version['version_id'], raw)
            pause.update(resolved_version=version['version_id'])
            self._save('observations/resolved-' + expected_observation + '.json', pause)
            try:
                _path(self.root, 'pauses/' + key + '.json').unlink()
                descriptor = os.open(_path(self.root, 'pauses/' + key).parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except BaseException as error:
                self._fail(error)
                raise
            return {'status': 'committed', 'version_id': version['version_id'], 'sha256': _hash(raw)}
