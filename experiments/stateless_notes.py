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
KINDS = {'observation', 'quote', 'inference', 'correction', 'draft', 'attempt', 'success', 'unknown', 'failed'}


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

    def freeze_baseline(self, key, snapshot, *, allowed_keys):
        """Freeze a harness-authored parent read, including absence/tombstones.

        Pass the returned material into the generating request. This receipt is
        evidence of a read, not another current-version authority. An old receipt
        remains old even if a different writer commits before the tool runs.
        """
        key = _key(key)
        if key not in {_key(k) for k in allowed_keys}:
            raise ValueError('scope not authorized')
        with self._locked():
            if _path(self.root, 'pauses/' + key + '.json').exists():
                raise ValueError('note paused')
            current = _current(self.root, key)
            if current and (current['metadata']['source_frozen_id'] != snapshot['source_sha256']
                            or current['metadata']['cursor'] > snapshot['opened_cursor']):
                raise ValueError('foreign/future baseline')
            value = {'schema': 'stateless-note-baseline-v1', 'key': key, 'branch': self.manifest['branch'],
                     'version_id': current['metadata']['version_id'] if current else None,
                     'sha256': current['sha256'] if current else None,
                     'source_frozen_id': snapshot['source_sha256'], 'cursor': snapshot['opened_cursor']}
            raw = _json(value)
            identity = _hash(raw)
            relative = 'observations/baseline-' + identity + '.json'
            if not _path(self.root, relative).exists():
                self._save(relative, value, stage='baseline')
            elif _read_json(_path(self.root, relative)) != value:
                raise ValueError('immutable baseline drift')
            return {'kind': 'note_baseline', 'source_id': 'baseline-' + _hash(key.encode())[:24],
                    'version_id': identity, 'snapshot_id': snapshot['snapshot_id'], 'sha256': identity, 'raw': raw}

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
                 'registered_at': time.time(), 'status': 'pending', 'reason': 'registered; explicit output or terminal cutoff not yet admitted',
                 'cutoff': None, 'attempts': [], 'loop_root': str(_root(loop_root))}
        with self._locked():
            path = _path(self.root, 'triggers/' + identity + '.json')
            if path.exists():
                old = _read_json(path)
                if any(old[key] != value[key] for key in ('wake_id', 'snapshot_id', 'branch', 'allowed_keys', 'loop_root', 'source_frozen_id', 'cursor')):
                    raise ValueError('trigger identity drift')
                return deepcopy(old)
            self._save('triggers/' + identity + '.json', value)
        return deepcopy(value)

    def admit_trigger(self, trigger_id, loop_root, snapshot, *, note_aliases=None, through_output=None):
        """Freeze an immutable T10 prefix and the generator's actual wire view.

        through_output permits a live wake. Without it, the existing terminal
        mode freezes the last committed output. Admission never upgrades an old
        view when more results or a terminal are subsequently appended. Each
        distinct cutoff has its own task id; the registered trigger stays intact.
        Selector requests are audited but their catalog is not a body source.
        """
        from a0_ledger import digest
        from comparison_snapshot import resolve_ref
        from stateless_loop import recover_commits, _recover_rows
        state = recover_commits(loop_root)
        with self._locked():
            original = _read_json(_path(self.root, 'triggers/' + _id(trigger_id) + '.json'))
            if (original['snapshot_id'] != snapshot['snapshot_id']
                    or original['loop_root'] != str(_root(loop_root))):
                raise ValueError('trigger snapshot/loop mismatch')
            if note_aliases is not None and type(note_aliases) is not dict:
                raise ValueError('ordinary frozen note alias mapping required')
            aliases = dict(note_aliases or {})
            for alias, key in aliases.items():
                if (not isinstance(alias, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', alias)
                        or _key(key) not in original['allowed_keys']):
                    raise ValueError('invalid note alias or unauthorized target scope')
            terminal = state['terminals'].get(original['wake_id'])
            owned = {k: v for k, v in state['outputs'].items() if v['wake_id'] == original['wake_id']}
            if through_output is None:
                if terminal is None:
                    return {'status': 'pending', 'reason': 'choose a committed output or terminal cutoff'}
                if not owned:
                    return {'status': 'not-admitted', 'reason': 'no committed output'}
                through_output = next(reversed(owned))
                mode = 'terminal'
            else:
                mode = 'output'
            if through_output not in owned:
                raise ValueError('output does not belong to trigger wake')
            output = owned[through_output]
            sequence = next(row['sequence'] for row in state['records']
                            if row['kind'] == 'output' and row['value']['id'] == through_output)
            if mode == 'terminal':
                sequence = next(row['sequence'] for row in state['records']
                                if row['kind'] == 'wake-end' and row['value']['wake_id'] == original['wake_id'])
            rows = state['records'][:sequence]
            prefix = _recover_rows([{'record': row, 'sha256': digest(row)} for row in rows])
            evidence, roles, files = {}, {}, {}
            for path in sorted(Path(loop_root).glob('request-*.json')):
                if path.is_symlink():
                    raise ValueError('symlink request evidence')
                saved = json.loads(path.read_bytes())
                envelope, attr = saved['envelope'], saved['attribution']
                if attr['task_id'] != original['wake_id']:
                    continue
                if ((mode == 'terminal' and envelope['request_id'] not in terminal['request_ids'])
                        or (mode == 'output' and envelope['request_id'] != output['request_id'])):
                    continue
                if (attr['snapshot_id'] != original['snapshot_id'] or attr['branch_id'] != original['branch']
                        or _hash(envelope['serialized_body'].encode()) != envelope['body_sha256']
                        or json.loads(envelope['serialized_body']) != envelope['body']):
                    raise ValueError('request cutoff/evidence drift')
                if not isinstance(attr['role_id'], str) or not attr['role_id'] or envelope['request_id'] in evidence:
                    raise ValueError('unsupported/duplicate request role/evidence')
                evidence[envelope['request_id']], roles[envelope['request_id']] = envelope, attr['role_id']
                files[path.name] = digest(saved)
            required = set(terminal['request_ids']) if mode == 'terminal' else {output['request_id']}
            if not required <= set(evidence):
                raise ValueError('missing actual request evidence')
            # Only the generating request is an exposure authority. Other same-
            # batch results can already exist on the host without being seen.
            request = evidence[output['request_id']]
            if output['context_sha256'] != digest(request['body']['messages']):
                raise ValueError('output does not match actual wire evidence')
            messages = request['body']['messages']
            context = json.loads(messages[1]['content'])
            if context['screen']['snapshot_id'] != original['snapshot_id']:
                raise ValueError('wire snapshot mismatch')
            exposures, seen_notes = [], {}
            def record(item, *, source_snapshot_id=None):
                reference = item['ref']
                if source_snapshot_id is not None:
                    if reference['snapshot_id'] != source_snapshot_id:
                        raise ValueError('action result/source snapshot drift')
                    # A prior committed result retains its original snapshot id.
                    # Re-resolve its exact key in the current visible source set;
                    # its raw bytes must still match, never substitute latest text.
                    reference = {**reference, 'snapshot_id': snapshot['snapshot_id']}
                resolved = resolve_ref(snapshot, reference)
                if resolved['raw'] != item['raw'].encode() or resolved['sha256'] != item['sha256']:
                    raise ValueError('wire original bytes drift')
                exposures.append({'kind': 'record', 'request_id': output['request_id'], 'ref': item['ref'],
                                  'sha256': resolved['sha256'], 'author_id': str(resolved['record'].get('user_id')),
                                  'window': resolved['source_alias']})
            def material(item, *, reread=False, source_snapshot_id=None):
                if item['kind'] not in {'note', 'selector_inference', 'note_baseline'}:
                    raise ValueError('unsupported exposed material kind')
                if (item['snapshot_id'] != (source_snapshot_id or original['snapshot_id'])
                        or _hash(item['text'].encode()) != item['sha256']):
                    raise ValueError('wire material bytes drift')
                value = {'kind': 'material', 'material_kind': item['kind'], 'request_id': output['request_id'],
                         **{k: item[k] for k in ('source_id', 'version_id', 'sha256')}}
                if item['kind'] == 'note_baseline':
                    identity = _hash(item['text'].encode())
                    baseline = _read_json(_path(self.root, 'observations/baseline-' + identity + '.json'))
                    if (_json(baseline) != item['text'].encode() or item['version_id'] != identity
                            or item['source_id'] != 'baseline-' + _hash(baseline['key'].encode())[:24]
                            or baseline['schema'] != 'stateless-note-baseline-v1'
                            or baseline['branch'] != original['branch']
                            or baseline['source_frozen_id'] != original['source_frozen_id']
                            or baseline['cursor'] > original['cursor'] or baseline['key'] not in original['allowed_keys']):
                        raise ValueError('unverified/foreign baseline read')
                    parent = {k: baseline[k] for k in ('version_id', 'sha256')}
                    if not reread and baseline['key'] in seen_notes and seen_notes[baseline['key']] != parent:
                        raise ValueError('ambiguous exposed parent versions')
                    seen_notes[baseline['key']] = parent
                    return  # Mechanical read receipt is not a factual statement source.
                if item['kind'] == 'note':
                    key = _key(aliases.get(item['source_id'], item['source_id']))
                    raw = _path(self.root, 'versions/' + _id(item['version_id']) + '.md').read_bytes()
                    meta, _ = _decode(raw)
                    if (key not in original['allowed_keys'] or meta['key'] != key
                            or meta['branch'] != original['branch']
                            or meta['source_frozen_id'] != original['source_frozen_id']
                            or meta['cursor'] > original['cursor'] or meta['operation'] == 'delete'
                            or raw != item['text'].encode()
                            or item['version_id'] not in {r['version_id'] for r in _receipts(self.root, key)}):
                        raise ValueError('illegal exposed note scope/version/cutoff')
                    value['note_key'] = key
                    parent = {'version_id': meta['version_id'], 'sha256': _hash(raw)}
                    if not reread and key in seen_notes and seen_notes[key] != parent:
                        raise ValueError('ambiguous exposed parent versions')
                    seen_notes[key] = parent
                exposures.append(value)
            for item in context['screen']['records'] + context['selected']['references']:
                record(item)
            for item in context['selected']['materials']:
                material(item)
            for fact in context['selected']['committed_actions']:
                identity = fact['commit']
                generated = prefix['outputs'].get(identity)
                if (generated is None or fact['wake_id'] != generated['wake_id']
                        or fact['request_id'] != generated['request_id']
                        or fact['generated_assistant'] != generated['assistant']):
                    raise ValueError('exposed action output differs from T10 commit')
                terminal_fact = fact['terminal']
                if terminal_fact is not None and terminal_fact != prefix['terminals'].get(generated['wake_id']):
                    raise ValueError('unconfirmed exposed action terminal')
                exposures.append({'kind': 'generated', 'output_id': identity, 'assistant': generated['assistant']})
                for execution in fact['execution']:
                    action = execution['action']
                    if action.rsplit('#', 1)[0] != identity or action not in prefix['actions']:
                        raise ValueError('foreign exposed action')
                    result, status = execution['result'], execution['status']
                    if result is not None:
                        if result != prefix['results'].get(action) or status != result.get('status', 'completed'):
                            raise ValueError('exposed execution lacks confirmed result')
                    elif status not in {'unknown', 'not-dispatched'}:
                        raise ValueError('exposed execution falsely claims completion')
                    elif status == 'unknown' and not any(row['kind'] == 'dispatch' and row['value']['action'] == action for row in rows):
                        raise ValueError('exposed unknown lacks dispatch')
                    exposures.append({'kind': 'execution', **deepcopy(execution),
                                      'visibility': 'actual-request', 'request_id': output['request_id']})
                    if result and result.get('content'):
                        try:
                            content = json.loads(result['content'])
                        except (ValueError, TypeError):
                            continue
                        if isinstance(content, dict) and 'ref' in content:
                            record(content, source_snapshot_id=generated['snapshot_id'])
                        if isinstance(content, dict) and set(content) == {'note_material'}:
                            material(content['note_material'], source_snapshot_id=generated['snapshot_id'])
            # Verify native result pairs against the existing T10 action journal.
            calls = {}
            generator_chat = output.get('chat_id')
            prior_outputs = iter((identity, v) for identity, v in prefix['outputs'].items()
                                 if v['wake_id'] == original['wake_id'] and identity != through_output
                                 and v.get('chat_id') == generator_chat)
            for message in messages[2:]:
                if message['role'] == 'assistant':
                    expected = {k: v for k, v in message.items() if k != 'reasoning_content'}
                    if generator_chat is None:
                        # WHY: old single-Chat evidence remains readable, but
                        # equal outputs cannot establish which Chat produced a
                        # native pair. Never guess from role or matching bytes.
                        remaining = list(prior_outputs)
                        if sum(v['assistant'] == expected for _, v in remaining) > 1:
                            raise ValueError('ambiguous legacy native Chat identity')
                        prior_outputs = iter(remaining)
                    identity, generated = next(((identity, v) for identity, v in prior_outputs
                                                if v['assistant'] == expected), (None, None))
                    if generated is None:
                        raise ValueError('wire assistant differs from committed output order')
                    exposures.append({'kind': 'generated', 'output_id': identity, 'assistant': generated['assistant']})
                    for index, call in enumerate(message.get('tool_calls', []), 1):
                        if call['id'] in calls:
                            raise ValueError('ambiguous native tool id')
                        calls[call['id']] = identity + '#' + str(index)
                elif message['role'] == 'tool':
                    action = calls.get(message['tool_call_id'])
                    result = prefix['results'].get(action)
                    if result is None or result.get('content') != message['content']:
                        raise ValueError('wire tool has no confirmed result in prefix')
                    exposures.append({'kind': 'execution', 'action': action,
                                      'status': prefix['actions'][action], 'result': result,
                                      'request_id': output['request_id'], 'visibility': 'actual-request'})
                    try:
                        content = json.loads(message['content'])
                    except (ValueError, TypeError):
                        continue
                    if isinstance(content, dict) and 'ref' in content:
                        record(content)
                    if isinstance(content, dict) and set(content) == {'note_material'}:
                        material(content['note_material'], reread=True)
                elif message['role'] == 'user':
                    value = json.loads(message['content'])
                    if set(value) != {'note_material'}:
                        raise ValueError('unsupported same-wake harness message')
                    material(value['note_material'], reread=True)
                else:
                    raise ValueError('unsupported same-wake wire message')
            exposures.append({'kind': 'generated', 'output_id': through_output, 'assistant': output['assistant']})
            for action, status in prefix['actions'].items():
                if action.rsplit('#', 1)[0] == through_output:
                    exposures.append({'kind': 'execution', 'action': action, 'status': status,
                                      'result': prefix['results'].get(action), 'visibility': 'host-prefix-not-generator-exposure'})
            cutoff = {'mode': mode, 'terminal': terminal if mode == 'terminal' else None,
                      'generation': output['generation'], 'sequence': sequence, 'head': prefix['head'],
                      'lineage': digest(state['records'][0]), 'output_id': through_output,
                      'generator_role': roles[output['request_id']], 'generator_chat_id': generator_chat,
                      'exposures': {digest(item): item for item in exposures}, 'seen_notes': seen_notes,
                      'request_hashes': {k: v['body_sha256'] for k, v in evidence.items()},
                      'request_roles': roles, 'request_files': files, 'note_aliases': aliases,
                      'note_aliases_sha256': digest(aliases),
                      'selector_body_sources': 'unsupported; request role/hash only'}
            base = original.get('registered_trigger_id', original['id'])
            identity = digest({'trigger': base, 'cutoff': cutoff})
            task = {**original, 'id': identity, 'registered_trigger_id': base, 'cutoff': cutoff,
                    'reason': 'immutable output prefix and actual generator exposure bound',
                    'status': 'pending', 'attempts': []}
            relative = 'triggers/' + identity + '.json'
            if _path(self.root, relative).exists():
                old = _read_json(_path(self.root, relative))
                if old['cutoff'] != cutoff:
                    raise ValueError('immutable cutoff drift')
                return deepcopy(old)
            self._save(relative, task)
            return deepcopy(task)

    def _verify_cutoff(self, task):
        from a0_ledger import digest
        from stateless_loop import recover_commits
        cutoff = task['cutoff']
        if not cutoff:
            raise ValueError('incomplete wake; choose a committed output cutoff')
        state = recover_commits(task['loop_root'])
        terminal = state['terminals'].get(task['wake_id'])
        if terminal and terminal['status'] != 'complete':
            raise ValueError('cancelled/failed wake; writes closed')
        if (cutoff['mode'] == 'terminal' and (not terminal or terminal != cutoff['terminal'])
                or len(state['records']) < cutoff['sequence']
                or digest(state['records'][cutoff['sequence'] - 1]) != cutoff['head']
                or digest(state['records'][0]) != cutoff['lineage']):
            raise ValueError('source prefix drift')
        from a0_ledger import Ledger
        loop_root = _root(task['loop_root'])
        saved = json.loads(_path(loop_root, 'ledger.json').read_bytes())
        # Reuse Ledger's read-only reopen validation, including unknown IO,
        # reservations and settlement failures; never reset or write a budget.
        manifest = json.loads(_path(loop_root, 'manifest.json').read_bytes())
        schema = manifest.get('schema', '')
        if schema in {'stateless-capacity-manifest-v1', 'stateless-official-manifest-v1'}:
            import stateless_capacity_client as capacity
            if schema == 'stateless-capacity-manifest-v1':
                capacity._validate_manifest(manifest)
            else:
                import stateless_official as official
                if (set(manifest) != {'schema', 'root', 'run_id', 'contract', 'budgets',
                                     'role_stages', 'openai', 'httpx', 'records'}
                        or manifest['root'] != str(loop_root)
                        or not isinstance(manifest['run_id'], str) or not manifest['run_id']
                        or manifest['contract'] != official.contract()
                        or manifest['openai'] != official.openai.__version__
                        or manifest['httpx'] != official.httpx.__version__):
                    raise ValueError('unsupported official source contract')
                capacity._validate_budgets(manifest)
            # WHY: historical source validation uses the actual writer policy,
            # not an execution authorization or a guess from a price field.
            # Origin/time-sensitive official.verify would reject valid closed
            # calibration evidence and would conflate read proof with a grant.
            contract = manifest['contract']
            ledger = Ledger(loop_root, manifest['run_id'], digest(manifest),
                            limits=contract['limits'], prices=contract['prices'], reopen=True,
                            policy=capacity.CapacityPolicy(contract))
        else:
            if schema not in {'', 'stateless-loop-v1', 'stateless-selector-calibration-v1',
                              'stateless-joint-v1', 'notes-calibration-v2'}:
                raise ValueError('unsupported source manifest schema')
            ledger = Ledger(loop_root, saved['state']['pilot'], saved['state']['manifest'],
                            limits=saved['state']['limits'], prices=saved['state']['prices'], reopen=True)
        requests = {row['id']: row for row in ledger.state['requests']}
        for filename, expected in cutoff['request_files'].items():
            path = _path(loop_root, filename)
            request = json.loads(path.read_bytes())
            envelope = request['envelope']
            row = requests.get(envelope['request_id'])
            if (digest(request) != expected or row is None or row['status'] != 'settled'
                    or row['request_sha256'] != digest(envelope)):
                raise ValueError('request cutoff/evidence/Ledger drift')
        return cutoff

    def finish_trigger(self, trigger_id, status, reason):
        if status not in {'no-op', 'failed', 'not-admitted', 'success'} or not reason:
            raise ValueError('explicit outcome/reason required')
        with self._locked():
            relative = 'triggers/' + _id(trigger_id) + '.json'
            task = _read_json(_path(self.root, relative))
            if status in {'success', 'no-op'}:
                self._verify_cutoff(task)
            task['status'], task['reason'] = status, reason
            task['attempts'].append({'status': status, 'reason': reason, 'at': time.time()})
            self._save(relative, task)
            return deepcopy(task)

    def _validate(self, candidate, task, current):
        """One policy for prepared, persisted and manually adopted proposals."""
        fields = {'schema', 'trigger_id', 'key', 'branch', 'operation', 'body', 'body_sha256',
                  'parent_version', 'parent_sha256', 'source_frozen_id', 'snapshot_id', 'cursor',
                  'generation', 'cutoff_sha256', 'sources', 'reason', 'expectation', 'statements', 'restore_version',
                  'cutoff_sequence', 'cutoff_head', 'producer'}
        if set(candidate) != fields or candidate['schema'] != 'stateless-note-candidate-v1':
            raise ValueError('invalid candidate fields')
        cutoff = self._verify_cutoff(task)
        key, operation = _key(candidate['key']), candidate['operation']
        if (candidate['branch'] != self.manifest['branch'] or task['branch'] != candidate['branch']
                or key not in task['allowed_keys']):
            raise ValueError('foreign branch/wrong scope')
        for field in ('source_frozen_id', 'snapshot_id', 'cursor'):
            if candidate[field] != task[field]:
                raise ValueError('candidate source/cutoff drift')
        if (candidate['generation'] != cutoff['generation']
                or candidate['cutoff_sequence'] != cutoff['sequence'] or candidate['cutoff_head'] != cutoff['head']
                or candidate['cutoff_sha256'] != _hash(_json(task['cutoff']))
                or candidate['trigger_id'] != task['id']):
            raise ValueError('candidate immutable cutoff drift')
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
                    and exposures[source].get('visibility') == 'actual-request'
                    for source in statement['sources']):
                raise ValueError('successful action needs completed execution evidence')
        if current and (task['source_frozen_id'] != current['metadata']['source_frozen_id']
                        or task['cursor'] < current['metadata']['cursor']
                        or candidate['generation'] < current['metadata']['generation']
                        or candidate['cutoff_sequence'] < current['metadata']['cutoff_sequence']
                        or task['cutoff']['lineage'] != _read_json(_path(self.root, 'triggers/' +
                            _id(current['metadata']['trigger_id']) + '.json'))['cutoff']['lineage']):
            raise ValueError('older wake cutoff/foreign source cannot revise newer note baseline')
        producer = candidate['producer']
        if (not isinstance(producer, dict) or set(producer) != {'process', 'basis', 'output_id'}
                or producer['process'] not in {'main', 'background'} or not producer['basis']):
            raise ValueError('explicit candidate production process/basis required')
        if producer['output_id'] != cutoff['output_id']:
            raise ValueError('generator output differs from frozen exposure')
        if cutoff['seen_notes'].get(key) != {
                'version_id': candidate['parent_version'], 'sha256': candidate['parent_sha256']}:
            raise ValueError('generator did not see target parent version/baseline')
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
                          statements=(), restore_version=None, branch=None, producer=None, parent=None):
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
            exposed_parent = task['cutoff']['seen_notes'].get(key)
            if exposed_parent is None:
                raise ValueError('generator did not see target parent baseline')
            if parent is not None and parent != exposed_parent:
                raise ValueError('explicit parent differs from actual generator exposure')
            candidate = {'schema': 'stateless-note-candidate-v1', 'trigger_id': trigger_id, 'key': key,
                         'branch': branch or self.manifest['branch'], 'operation': operation, 'body': body.decode(), 'body_sha256': _hash(body),
                         'parent_version': exposed_parent['version_id'],
                         'parent_sha256': exposed_parent['sha256'],
                         'source_frozen_id': task['source_frozen_id'], 'snapshot_id': task['snapshot_id'], 'cursor': task['cursor'],
                         'generation': task['cutoff']['generation'],
                         'cutoff_sequence': task['cutoff']['sequence'], 'cutoff_head': task['cutoff']['head'],
                         'producer': deepcopy(producer),
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
                        'cursor': task['cursor'], 'generation': task['cutoff']['generation'],
                        'cutoff_sequence': task['cutoff']['sequence'], 'cutoff_head': task['cutoff']['head'],
                        'producer': {'process': 'background', 'basis': 'explicit human rebaseline',
                                     'output_id': task['cutoff']['output_id']},
                        'cutoff_sha256': _hash(_json(task['cutoff'])), 'sources': list(sources), 'reason': reason,
                        'expectation': expectation, 'statements': [], 'restore_version': None}
            self._validate(proposal, task, {'metadata': parent, 'raw': parent_raw})
            metadata = {key: value for key, value in parent.items()
                        if key not in {'version_id', 'schema', 'body_sha256'}}
            metadata.update(operation='manual-baseline', parent_version=parent_version, parent_sha256=_hash(parent_raw),
                            candidate_id='manual-' + expected_observation, trigger_id=trigger_id,
                            sources=sorted(sources), reason=reason, expectation=expectation,
                            snapshot_id=task['snapshot_id'], cursor=task['cursor'], source_frozen_id=task['source_frozen_id'],
                            generation=task['cutoff']['generation'],
                            cutoff_sequence=task['cutoff']['sequence'], cutoff_head=task['cutoff']['head'],
                            producer=proposal['producer'],
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
