"""Snapshot-bound extra-material selection; no storage authority or transport.

The caller supplies a frozen, exact-version note resolver. Only opaque catalog
IDs reach the selector. T07 owns chatlog bytes; T10 owns committed action facts.
All requests use the caller's existing ClosedClient/Ledger failure domain.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from a0_ledger import canonical, digest, durable
from comparison_snapshot import resolve_ref
from stateless_loop import build_screen, selected_context, recover_commits
from mods import llm

LIMITS = {'catalog_entries': 12, 'catalog_bytes': 16000, 'situation_bytes': 1024,
          'references': 6, 'material_bytes': 24000, 'reads': 8, 'requests': 3,
          'output_tokens': 4096}
ENTRY_FIELDS = {'entry_id', 'kind', 'title', 'scope', 'branch_id', 'version_id',
                'committed_cursor', 'expires_cursor', 'source_id', 'sha256', 'ref'}


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _name(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[a-zA-Z0-9_-]{1,96}', value))


def _payload(context):
    raw = context['input_bytes']
    if type(raw) is not bytes or _hash(raw) != context['input_hash']:
        raise ValueError('selector input hash drift')
    return json.loads(raw)


def build_selector_input(snapshot, wake, hint, candidates, *, scopes, branch_id='main', limits=None,
                         committed_state=None):
    """Expose only eligible metadata, not private candidates or resolver bytes.

    Filtering removes future/uncommitted/foreign/expired entries before titles
    reach the model. Eligible malformed or ambiguous entries stop the wake.
    Scopes are an explicit fixture allowlist, not identity-based permission.
    """
    limits = dict(LIMITS if limits is None else limits)
    if set(limits) != set(LIMITS) or any(type(v) is not int or v < 1 for v in limits.values()):
        raise ValueError('explicit positive selector limits required')
    if limits['output_tokens'] != 4096:
        raise ValueError('ClosedClient frozen output allowance is 4096')
    if not _name(branch_id) or not scopes or any(not isinstance(v, str) or not v for v in scopes):
        raise ValueError('explicit branch/scopes required')
    if not isinstance(hint, str):
        raise ValueError('frozen hint must be text')
    screen = build_screen(snapshot, wake)
    catalog, excluded, ids = [], [], set()
    state = committed_state or {'outputs': {}, 'terminals': {}}
    for ordinal, original in enumerate(candidates):
        entry = deepcopy(original)
        if set(entry) != ENTRY_FIELDS:
            raise ValueError('catalog schema mismatch')
        reason = None
        if (type(entry['committed_cursor']) is not int or type(entry['expires_cursor']) is not int):
            raise ValueError('catalog cutoff must be integer')
        if entry['committed_cursor'] < 1:
            reason = 'uncommitted'
        elif entry['committed_cursor'] > wake['cursor']:
            reason = 'future'
        elif entry['branch_id'] != branch_id:
            reason = 'foreign-branch'
        elif entry['scope'] not in scopes:
            reason = 'wrong-scope'
        elif entry['expires_cursor'] < wake['cursor']:
            reason = 'expired'
        if reason:
            # Private diagnostics contain ordinal/reason only, never hidden title.
            excluded.append({'ordinal': ordinal, 'reason': reason})
            continue
        if (not _name(entry['entry_id']) or entry['entry_id'] in ids
                or not _name(entry['source_id']) or not _name(entry['version_id'])
                or entry['kind'] not in {'chatlog', 'note', 'action'}
                or not isinstance(entry['title'], str) or not entry['title'].strip()
                or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])):
            raise ValueError('invalid/ambiguous catalog identity/hash')
        if entry['kind'] == 'chatlog':
            exact = resolve_ref(snapshot, entry['ref'])
            if (exact['sha256'] != entry['sha256'] or entry['source_id'] != exact['source_alias']
                    or entry['scope'] != 'group:' + exact['source_alias']):
                raise ValueError('catalog chatlog source/hash mismatch')
        elif entry['kind'] == 'note':
            if entry['ref'] != {'source_id': entry['source_id'], 'version_id': entry['version_id'],
                                'snapshot_id': snapshot['snapshot_id']}:
                raise ValueError('note must bind exact source/version/snapshot')
        else:
            if set(entry['ref']) != {'output_id'}:
                raise ValueError('action must name one committed output')
            identity = entry['ref']['output_id']
            output = state['outputs'].get(identity)
            terminal = state['terminals'].get(output['wake_id']) if output else None
            screens = [v['screen'] for v in state['outputs'].values()
                       if output and v['wake_id'] == output['wake_id'] and v['screen']]
            if (not terminal or terminal['status'] not in {'complete', 'cancelled', 'failed'}
                    or len(screens) != 1 or screens[0]['cursor'] > wake['cursor']):
                raise ValueError('action lacks committed terminal cutoff')
            raw = canonical(selected_context(snapshot, (), [identity], state)['committed_actions'][0])
            if _hash(raw) != entry['sha256']:
                raise ValueError('catalog action hash mismatch')
        ids.add(entry['entry_id'])
        catalog.append(entry)
    if len(catalog) > limits['catalog_entries'] or len(canonical(catalog)) > limits['catalog_bytes']:
        raise ValueError('catalog limit exceeded; no truncation')
    payload = {'schema': 'stateless-selector-input-v1', 'wake_id': wake['id'],
               'snapshot_id': snapshot['snapshot_id'], 'branch_id': branch_id, 'scopes': list(scopes),
               'screen': screen, 'hint': {'text': hint, 'sha256': _hash(hint.encode())},
               'catalog': catalog, 'limits': limits}
    raw = canonical(payload)
    return {'input_bytes': raw, 'input_hash': _hash(raw), 'excluded': excluded}


def _entry(context, entry_id):
    found = [entry for entry in _payload(context)['catalog'] if entry['entry_id'] == entry_id]
    if len(found) != 1:
        raise ValueError(f'missing/ambiguous/illegal catalog reference: {entry_id!r}')
    return found[0]


def _resolve(context, entry_id, snapshot, note_resolver, committed_state):
    entry = _entry(context, entry_id)
    if snapshot['snapshot_id'] != _payload(context)['snapshot_id']:
        raise ValueError('stale selector snapshot')
    if entry['kind'] == 'chatlog':
        result = resolve_ref(snapshot, entry['ref'])
        raw = result['raw']
    elif entry['kind'] == 'note':
        # The resolver returns an exact frozen value, never a path/latest lookup.
        result = note_resolver(deepcopy(entry))
        if (set(result) != {'source_id', 'version_id', 'snapshot_id', 'raw', 'sha256'}
                or any(result[k] != entry['ref'][k] for k in ('source_id', 'version_id', 'snapshot_id'))):
            raise ValueError('resolver source/version/snapshot mismatch')
        raw = result['raw']
        if result['sha256'] != entry['sha256']:
            raise ValueError('resolver declared hash mismatch')
    else:
        result = selected_context(snapshot, (), [entry['ref']['output_id']], committed_state)['committed_actions'][0]
        raw = canonical(result)
    if type(raw) is not bytes or _hash(raw) != entry['sha256']:
        raise ValueError('resolved original bytes/hash mismatch')
    text = raw.decode('utf-8', errors='strict')
    return {'entry_id': entry_id, 'kind': entry['kind'], 'source_id': entry['source_id'],
            'version_id': entry['version_id'], 'snapshot_id': snapshot['snapshot_id'],
            'sha256': entry['sha256'], 'ref': deepcopy(entry['ref']), 'raw': text}


def close_wake(client, reason):
    if not client.ledger.poisoned and not client.ledger.state['closed']:
        client.ledger.stop(reason)


def save_receipt(client, path, receipt):
    try:
        durable(Path(path), receipt)
    except BaseException:
        close_wake(client, 'selector-receipt-persistence-failure')
        raise


def select_materials(context, snapshot, client, note_resolver, receipt, receipt_path, *,
                     committed_state=None, cancelled=lambda: False):
    """Fresh Chat, bounded read tool, strict final JSON; failure closes all roles."""
    before = len(client.exchanges)
    final, reads, read_bytes = [], [0], [0]
    try:
        state = committed_state or recover_commits(client.root)
        payload = _payload(context)
        if payload['snapshot_id'] != snapshot['snapshot_id']:
            raise ValueError('selector context snapshot mismatch')
        limits = payload['limits']
        receipt.update({'schema': 'stateless-selector-receipt-v1', 'wake_id': payload['wake_id'],
                        'snapshot_id': payload['snapshot_id'], 'input_hash': context['input_hash'],
                        'input': payload, 'excluded': context['excluded'], 'events': [], 'selector_request_ids': []})
        client.current = {'role': 'selector', 'wake': payload['wake_id'], 'snapshot_id': payload['snapshot_id']}
        def checkpoint(event):
            receipt['events'].append(event)
            receipt['selector_request_ids'] = [value['request_id'] for value in client.exchanges[before:]]
            sent = {v['request_id'] for v in client.trace if v['event'] == 'mock'}
            exposed = [v for v in receipt['selector_request_ids'] if v in sent]
            receipt['catalog_exposure'] = {'status': 'exposed' if exposed else 'not-sent', 'request_ids': exposed}
            save_receipt(client, receipt_path, receipt)
        checkpoint({'stage': 'catalog', 'entry_ids': [entry['entry_id'] for entry in payload['catalog']]})
        def guard():
            client.guard_tools()
            if cancelled():
                raise RuntimeError('selector cancelled')
        def read_material(entry_id: str) -> str:
            """Read one complete exact-version catalog entry by opaque entry_id."""
            guard()
            if reads[0] >= limits['reads']:
                raise ValueError('selector read limit exceeded')
            reads[0] += 1
            try:
                value = _resolve(context, entry_id, snapshot, note_resolver, state)
                read_bytes[0] += len(value['raw'].encode())
                if read_bytes[0] > limits['material_bytes']:
                    raise ValueError('selector read bytes limit exceeded')
            except Exception as error:
                checkpoint({'stage': 'read', 'entry_id': entry_id, 'status': 'failed', 'error': str(error)})
                raise
            checkpoint({'stage': 'read', 'status': 'complete', 'value': value,
                        'request_id': client.exchanges[-1]['request_id']})
            return canonical(value).decode()
        def output(assistant, calls):
            guard()
            if len(client.exchanges) - before > limits['requests']:
                raise ValueError('selector request limit exceeded')
            checkpoint({'stage': 'output', 'request_id': client.exchanges[-1]['request_id'],
                        'assistant': deepcopy(assistant)})
            if not calls:
                final.append(json.loads(assistant['content']))
        def next_request():
            try:
                guard()
                if len(client.exchanges) - before >= limits['requests']:
                    raise ValueError('selector request limit exceeded')
            except Exception as error:
                raise llm.RequiredContextError(str(error)) from error
            return []
        chat = llm.Chat(model=client.config['default_model'], messages=[
            {'role': 'system', 'content': 'Choose extra material by opaque catalog ID. Read only read_material. Return JSON with exactly situation, references (entry IDs), input_hash. Situation is inference, never original evidence.'},
            {'role': 'user', 'content': canonical({'input': payload, 'input_hash': context['input_hash']}).decode()}],
            functions=[read_material], chat_client=client, do_process_image=False)
        chat.fail_fast = chat.preserve_native = True
        chat.on_output = output
        chat.add_context_provider(next_request)
        client.chats.append(chat)
        chat.chat(stream=False)
        guard()
        if len(final) != 1:
            raise ValueError('selector has no unique final selection')
        choice = final[0]
        if (not isinstance(choice, dict) or set(choice) != {'situation', 'references', 'input_hash'}
                or choice['input_hash'] != context['input_hash'] or not isinstance(choice['situation'], str)
                or len(choice['situation'].encode()) > limits['situation_bytes']
                or not isinstance(choice['references'], list)
                or len(choice['references']) > limits['references']):
            raise ValueError('invalid selection/hash/situation/reference limit')
        unique, duplicates = [], []
        for index, identity in enumerate(choice['references']):
            if not isinstance(identity, str):
                raise ValueError('selection must use opaque catalog IDs')
            _entry(context, identity)
            if identity in unique:
                duplicates.append({'entry_id': identity, 'index': index})
            else:
                unique.append(identity)
        selection = {**choice, 'references': unique, 'duplicates': duplicates}
        receipt['selection'] = selection
        checkpoint({'stage': 'select', 'references': unique, 'duplicates': duplicates})
        return selection
    except BaseException as error:
        close_wake(client, 'selector-failure')
        receipt['selector_request_ids'] = [v['request_id'] for v in client.exchanges[before:]]
        sent = {v['request_id'] for v in client.trace if v['event'] == 'mock'}
        exposed = [v for v in receipt['selector_request_ids'] if v in sent]
        receipt['catalog_exposure'] = {'status': 'exposed' if exposed else 'not-sent', 'request_ids': exposed}
        receipt['failure'] = {'type': type(error).__name__, 'reason': str(error)}
        raise


def resolve_selection(context, selection, snapshot, note_resolver, *, committed_state=None):
    """Freeze complete values once. Passing this bundle to T10 never rereads notes."""
    payload = _payload(context)
    if (selection['input_hash'] != context['input_hash']
            or snapshot['snapshot_id'] != payload['snapshot_id']
            or not isinstance(selection['situation'], str)
            or len(selection['situation'].encode()) > payload['limits']['situation_bytes']):
        raise ValueError('selection input/snapshot/situation mismatch')
    if (len(selection['references']) != len(set(selection['references']))
            or len(selection['references']) > payload['limits']['references']):
        raise ValueError('invalid resolved selection references')
    values = []
    for identity in selection['references']:
        try:
            values.append(_resolve(context, identity, snapshot, note_resolver, committed_state or {}))
        except Exception as error:
            raise ValueError(f'resolution refused for {identity!r}: {error}') from error
    if sum(len(value['raw'].encode()) for value in values) > payload['limits']['material_bytes']:
        raise ValueError('selected bytes limit exceeded; no partial injection')
    references, actions, materials = [], [], []
    for value in values:
        if value['kind'] == 'chatlog':
            references.append(deepcopy(value['ref']))
        elif value['kind'] == 'action':
            actions.append(value['ref']['output_id'])
        else:
            materials.append({key: value[key] for key in ('source_id', 'version_id', 'snapshot_id', 'sha256')}
                             | {'kind': 'note', 'raw': value['raw'].encode()})
    raw = selection['situation'].encode()
    materials.append({'kind': 'selector_inference', 'source_id': 'selector-' + payload['wake_id'],
                      'version_id': context['input_hash'], 'snapshot_id': snapshot['snapshot_id'],
                      'sha256': _hash(raw), 'raw': raw})
    return {'references': references, 'actions': actions, 'materials': materials, 'values': values,
            'input_hash': context['input_hash'], 'situation': selection['situation']}


def record_consumption(receipt, bundle, request_evidence):
    """Compare resolved bytes with actual final HTTP body, not selected flags.

    A selection can remain unconsumed when no chat request was admitted. A
    present request with missing/altered material is a distinct, explicit state.
    Entering a request proves exposure only, never understanding or use.
    """
    if (bundle['input_hash'] != receipt['input_hash'] or digest(receipt['input']) != receipt['input_hash']
            or bundle['situation'] != receipt['selection']['situation']
            or [v['entry_id'] for v in bundle['values']] != receipt['selection']['references']):
        raise ValueError('consumption selection/input binding mismatch')
    catalog = {v['entry_id']: v for v in receipt['input']['catalog']}
    for value in bundle['values']:
        entry = catalog[value['entry_id']]
        if (any(value[k] != entry[k] for k in ('kind', 'source_id', 'version_id', 'sha256', 'ref'))
                or value['snapshot_id'] != receipt['snapshot_id']
                or _hash(value['raw'].encode()) != value['sha256']):
            raise ValueError('consumption bundle/catalog source mismatch')
    receipt['resolved'] = deepcopy(bundle['values'])
    receipt['events'].append({'stage': 'resolve', 'entry_ids': [v['entry_id'] for v in bundle['values']]})
    body = None
    if request_evidence is not None:
        envelope, attr = request_evidence['envelope'], request_evidence['attribution']
        if (attr['role_id'] != 'chat' or attr['task_id'] != receipt['wake_id']
                or attr['snapshot_id'] != receipt['snapshot_id']
                or _hash(envelope['serialized_body'].encode()) != envelope['body_sha256']
                or json.loads(envelope['serialized_body']) != envelope['body']):
            raise ValueError('consumption wire/role/wake/snapshot mismatch')
        body = json.loads(envelope['body']['messages'][1]['content'])
        if (body['screen'] != receipt['input']['screen']
                or digest({k: v for k, v in body['screen'].items() if k != 'sha256'}) != body['screen']['sha256']):
            raise ValueError('consumption current screen changed')
    consumed = []
    for value in bundle['values']:
        matches = []
        if body is not None:
            selected = body['selected']
            if value['kind'] == 'chatlog':
                matches = [v for v in selected['references'] if v['ref'] == value['ref']
                           and v['sha256'] == value['sha256'] and v['raw'] == value['raw']]
            elif value['kind'] == 'note':
                matches = [v for v in selected['materials'] if v['kind'] == 'note'
                           and all(v[k] == value[k] for k in ('source_id', 'version_id', 'snapshot_id', 'sha256'))
                           and v['text'] == value['raw'] and v['interpretation'] == 'versioned-note']
            else:
                matches = [v for v in selected['committed_actions'] if canonical(v).decode() == value['raw']]
        consumed.append({'entry_id': value['entry_id'], 'sha256': value['sha256'],
                         'status': 'injected' if len(matches) == 1 else 'selected-not-injected',
                         'chat_request_id': request_evidence['envelope']['request_id'] if body is not None else None})
    if body is not None:
        if any(len(body['selected'][key]) > len(bundle[key]) for key in ('references', 'materials')) or len(body['selected']['committed_actions']) > len(bundle['actions']):
            raise ValueError('unselected extra material in actual wire')
        inference = [v for v in body['selected']['materials'] if v['kind'] == 'selector_inference']
        if (len(inference) != 1 or inference[0]['text'] != bundle['situation']
                or inference[0]['interpretation'] != 'inference-not-evidence'
                or inference[0]['sha256'] != _hash(bundle['situation'].encode())
                or inference[0]['source_id'] != 'selector-' + receipt['wake_id']
                or inference[0]['version_id'] != receipt['input_hash']
                or inference[0]['snapshot_id'] != receipt['snapshot_id']):
            raise ValueError('selector inference altered or unlabelled')
    receipt['consumption'] = consumed
    receipt['events'].append({'stage': 'wire', 'consumption': deepcopy(consumed)})
    return consumed
