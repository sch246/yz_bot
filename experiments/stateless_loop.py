"""Synthetic fresh-context loop and read-only action-commit recovery.

No production lifecycle, archive path import or model transport. Optional note
tools use the existing NoteWriter; this module owns no note versions or budget.
The snapshot is private control data; only resolved visible records enter Chat.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from a0_ledger import canonical, digest, durable
from comparison_snapshot import resolve_ref
from mods import llm


def build_screen(snapshot, wake, *, count=2):
    """Recent records in this window; occurrence order is not arrival order."""
    if type(count) is not int or count < 1 or wake['cursor'] != snapshot['opened_cursor']:
        raise ValueError('invalid screen count/opening')
    visible = set(snapshot['visible_event_keys'])
    events = sorted((event for event in snapshot['frozen_sources']['events']
                     if event['event_key'] in visible and event['source_alias'] == wake['window']),
                    key=lambda event: (event['record']['time'], event['event_key']))
    selected = events[-count:]
    if not selected:
        raise ValueError('empty wake window')
    records = []
    for event in selected:
        ref = {'snapshot_id': snapshot['snapshot_id'], 'source_alias': event['source_alias'],
               'event_key': event['event_key']}
        resolved = resolve_ref(snapshot, ref)
        records.append({'ref': ref, 'origin': resolved['visible_origin'], 'day': event['day'], 'time': event['record']['time'],
                        'author_id': event['record'].get('user_id'), 'raw': resolved['raw'].decode(),
                        'sha256': event['sha256']})
    last = selected[-1]
    screen = {'schema': 1, 'snapshot_id': snapshot['snapshot_id'], 'cursor': snapshot['opened_cursor'],
              'window': wake['window'], 'records': records,
              'boundary': {'time': last['record']['time'], 'event_key': last['event_key']},
              'truncated_count': max(0, len(events) - count),
              'new_old': 'approximate screen boundary; late/truncated/noncontinuous records may be mislabelled'}
    return {**screen, 'sha256': digest(screen)}


def material_values(snapshot, materials):
    """Validate frozen values; source/version legality belongs to their producer."""
    values = []
    fields = {'kind', 'source_id', 'version_id', 'snapshot_id', 'sha256', 'raw'}
    # WHY: absent or tombstoned notes still need an exposed parent baseline,
    # without presenting file-state metadata as remembered content or inference.
    for material in materials:
        if (set(material) != fields or material['kind'] not in {'note', 'note_baseline', 'selector_inference'}
                or any(not isinstance(material[key], str) or not material[key].strip()
                       for key in ('source_id', 'version_id', 'snapshot_id', 'sha256'))
                or material['snapshot_id'] != snapshot['snapshot_id']
                or type(material['raw']) is not bytes
                or hashlib.sha256(material['raw']).hexdigest() != material['sha256']):
            raise ValueError('invalid frozen material bytes/hash/snapshot/source/version')
        values.append({key: material[key] for key in fields - {'raw'}} | {
            'text': material['raw'].decode('utf-8', errors='strict'),
            'interpretation': {'note': 'versioned-note',
                               'note_baseline': 'harness-file-state-baseline-not-note-content',
                               'selector_inference': 'inference-not-evidence'}[material['kind']]})
    return values


def selected_context(snapshot, references, actions, committed_state, materials=()):
    """Explicit exact references plus committed action facts, never old messages."""
    material = []
    for reference in references:
        result = resolve_ref(snapshot, reference)
        material.append({'ref': reference, 'sha256': result['sha256'], 'raw': result['raw'].decode()})
    facts = []
    for identity in actions:
        if identity not in committed_state['outputs']:
            raise ValueError('explicit action is not a committed output')
        output = committed_state['outputs'][identity]
        execution = [{'action': action, 'status': status, 'result': deepcopy(committed_state['results'].get(action))}
                     for action, status in committed_state['actions'].items() if action.rsplit('#', 1)[0] == identity]
        facts.append({'commit': identity, 'wake_id': output['wake_id'], 'request_id': output['request_id'],
                      'generated_assistant': deepcopy(output['assistant']), 'execution': execution,
                      'terminal': deepcopy(committed_state['terminals'].get(output['wake_id']))})
    return {'references': material, 'committed_actions': facts, 'materials': material_values(snapshot, materials)}


def recover_commits(root):
    """Read and validate only. Never constructs a client or invokes any tool.

    A renamed record whose directory fsync failed remains persistence-unknown;
    the marker and .new files close recovery instead of claiming crash proof.
    """
    root = Path(root)
    folder = root / 'commits'
    if (root / 'commit-persistence-unknown.json').exists() or list(folder.glob('*.new')):
        raise ValueError('commit persistence unknown/residual tail')
    files = sorted(folder.iterdir()) if folder.exists() else []
    envelopes = []
    for number, path in enumerate(files, 1):
        if path.is_symlink() or path.name != f'{number:06d}.json':
            raise ValueError('commit gap/foreign/residual file')
        envelopes.append(json.loads(path.read_bytes()))
    return _recover_rows(envelopes)


def _recover_rows(envelopes):
    previous, generation = None, 0
    current_wake, current_snapshot, cursor = None, None, 0
    boundaries, acknowledged, outputs, dispatched, results, records = {}, [], {}, set(), {}, []
    terminals = {}
    for number, envelope in enumerate(envelopes, 1):
        row = envelope['record']
        if (set(envelope) != {'record', 'sha256'} or envelope['sha256'] != digest(row)
                or row['sequence'] != number or row['previous'] != previous or row['schema'] != 1):
            raise ValueError('corrupt commit/checksum/parent')
        kind, value = row['kind'], row['value']
        if kind == 'output':
            if 'reasoning_content' in value['assistant']:
                raise ValueError('cross-wake thinking is not an action fact')
            if value['wake_id'] in terminals:
                raise ValueError('output after terminal wake')
            if value['id'] in outputs:
                raise ValueError('duplicate output')
            if value['screen'] is not None:
                screen = value['screen']
                if (value['parent_generation'] != generation or value['generation'] != generation + 1
                        or screen['sha256'] != digest({k: v for k, v in screen.items() if k != 'sha256'})
                        or value['snapshot_id'] != screen['snapshot_id']
                        or screen['cursor'] < cursor
                        or not screen['records']
                        or screen['boundary']['event_key'] != screen['records'][-1]['ref']['event_key']
                        or screen['boundary']['time'] != screen['records'][-1]['time']
                        or any(record['ref']['snapshot_id'] != screen['snapshot_id']
                               or record['ref']['source_alias'] != screen['window']
                               or hashlib.sha256(record['raw'].encode()).hexdigest() != record['sha256']
                               for record in screen['records'])
                        or len(value['acknowledged']) != len(set(value['acknowledged']))
                        or any(old['wake_id'] == value['wake_id'] for old in outputs.values())
                        or value['covered_wakes'] != [item['id'] for item in value['wake_facts']]
                        or any(item['window'] != screen['window'] or item['cursor'] > screen['cursor']
                               or item['event_key'] not in {record['ref']['event_key'] for record in screen['records']}
                               for item in value['wake_facts'])
                        or value['acknowledged'] != value['covered_wakes']):
                    raise ValueError('stale generation/screen/acknowledgement')
                generation += 1
                current_wake, current_snapshot, cursor = value['wake_id'], value['snapshot_id'], screen['cursor']
                boundaries[screen['window']] = screen['boundary']
                if set(acknowledged) & set(value['acknowledged']):
                    raise ValueError('duplicate wake acknowledgement')
                acknowledged.extend(value['acknowledged'])
            elif (value['generation'] != generation or value['parent_generation'] != generation - 1
                  or value['wake_id'] != current_wake or value['snapshot_id'] != current_snapshot
                  or value['acknowledged'] or value['covered_wakes'] or value['wake_facts']):
                raise ValueError('subrequest generation drift')
            if not value['id'].startswith(value['wake_id'] + '/output-'):
                raise ValueError('output/wake identity drift')
            outputs[value['id']] = value
        elif kind in {'dispatch', 'result'}:
            action = value['action']
            output, index = action.rsplit('#', 1)
            if output not in outputs or not 1 <= int(index) <= len(outputs[output]['assistant'].get('tool_calls', [])):
                raise ValueError('unknown action')
            if outputs[output]['wake_id'] in terminals:
                raise ValueError('action after terminal wake')
            if kind == 'dispatch':
                if action in dispatched:
                    raise ValueError('duplicate dispatch')
                dispatched.add(action)
            else:
                if action not in dispatched or action in results:
                    raise ValueError('result without unique dispatch')
                results[action] = value
        elif kind == 'wake-end':
            wake = value['wake_id']
            owned = [item for item in outputs.values() if item['wake_id'] == wake]
            if (wake in terminals or value['generation'] != generation or value['cutoff_head'] != previous
                    or value['status'] not in {'complete', 'cancelled', 'failed'}
                    or not {item['request_id'] for item in owned} <= set(value['request_ids'])
                    or len(value['request_ids']) != len(set(value['request_ids']))):
                raise ValueError('terminal wake/cutoff drift')
            if value['status'] == 'complete' and (not owned or owned[-1]['assistant'].get('tool_calls')):
                raise ValueError('complete wake lacks final output')
            terminals[wake] = value
        else:
            raise ValueError('unknown commit kind')
        records.append(row)
        previous = envelope['sha256']
    actions = {}
    for output, value in outputs.items():
        for index, _ in enumerate(value['assistant'].get('tool_calls', []), 1):
            action = f'{output}#{index}'
            actions[action] = (results[action].get('status', 'completed') if action in results else
                               'unknown' if action in dispatched else 'not-dispatched')
    return {'schema': 1, 'generation': generation, 'head': previous, 'records': records,
            'boundaries': boundaries, 'acknowledged': acknowledged, 'actions': actions,
            'results': results, 'outputs': outputs, 'terminals': terminals, 'automatic_requests': 0, 'automatic_tools': 0}


def append_commit(root, kind, value, *, fault=None):
    """One durable record; output includes screen and wake acknowledgement."""
    state = recover_commits(root)
    row = {'schema': 1, 'sequence': len(state['records']) + 1, 'previous': state['head'],
           'kind': kind, 'value': value}
    folder = root / 'commits'
    folder.mkdir(exist_ok=True)
    path = folder / f"{row['sequence']:06d}.json"
    # One validator covers both write admission and read-only recovery.
    _recover_rows([{'record': item, 'sha256': digest(item)} for item in state['records']]
                  + [{'record': row, 'sha256': digest(row)}])
    try:
        if fault == 'before':
            raise OSError('synthetic commit before write')
        if fault in {'flush', 'fsync', 'after-rename'}:
            import a0_ledger
            from unittest.mock import patch
            if fault == 'flush':
                # A partial temporary record is deliberately retained.
                path.with_name(path.name + '.new').write_bytes(b'{"partial":')
                raise OSError('synthetic flush failure')
            original = a0_ledger.os.fsync
            calls = [0]
            def fail_fsync(fd):
                calls[0] += 1
                if calls[0] == (1 if fault == 'fsync' else 2):
                    raise OSError('synthetic commit fsync failure')
                return original(fd)
            with patch.object(a0_ledger.os, 'fsync', fail_fsync):
                durable(path, {'record': row, 'sha256': digest(row)})
        else:
            durable(path, {'record': row, 'sha256': digest(row)})
    except BaseException:
        if fault != 'before':
            try:
                durable(root / 'commit-persistence-unknown.json', {'status': 'persistence-unknown'})
            except OSError:
                pass
        raise
    return row


def close_session(client, reason):
    """Close the one shared Ledger, preserving damaged/unknown originals."""
    if client.ledger.poisoned or client.ledger.state['closed']:
        return
    try:
        # WHY: stop() writes. Never overwrite corrupt disk evidence with the
        # in-memory projection while trying to report a different failure.
        client.projection()
        client.ledger.stop(reason)
    except BaseException:
        client.ledger.poisoned = True


def note_source_handles(cutoff):
    """Public source coordinates map only to this output's verified exposure.

    The model selects coordinates it actually received, not post-output hashes.
    Generated self is an expression source, never proof of external success.
    """
    values = {}
    for identity, item in cutoff['exposures'].items():
        kind = item['kind']
        handle = None
        if kind == 'record':
            handle = 'record:' + item['ref']['source_alias'] + ':' + item['ref']['event_key']
        elif kind == 'material':
            handle = 'material:' + item['source_id'] + ':' + item['version_id']
        elif kind == 'generated':
            handle = 'generated:' + ('self' if item['output_id'] == cutoff['output_id'] else item['output_id'])
        elif kind == 'execution' and item['visibility'] == 'actual-request':
            handle = 'action:' + item['action']
        if handle:
            values.setdefault(handle, []).append(identity)
    return values


def drive_wake(snapshot, wake, wakes, root, client, *, references=(), actions=(), materials=(), scenario='normal',
               notes=None, note_keys=(), note_aliases=None, process='main', after_output=None, cancelled=lambda: False):
    """A fresh main/background Chat, optionally interleaved before terminal.

    after_output is an explicit caller seam for a second independent Chat;
    generation never holds a note lock. All outputs use the existing journal's
    shared sequence, even when a nested Chat finishes before this Chat's tools.
    """
    if process not in {'main', 'background'} or (note_keys and notes is None):
        raise ValueError('explicit process and note writer required')
    try:
        if scenario == 'construct-failure':
            raise ValueError('synthetic screen construction failure')
        root = Path(root)
        screen = build_screen(snapshot, wake)
        state = recover_commits(root)
        if wake['id'] in state['terminals']:
            raise ValueError('wake already terminal')
        existing = [v for v in state['outputs'].values() if v['wake_id'] == wake['id']]
        if existing and (existing[-1]['snapshot_id'] != snapshot['snapshot_id']
                         or existing[-1]['generation'] != state['generation']):
            raise ValueError('wake is no longer the active snapshot/generation')
        generation = state['generation'] if existing else state['generation'] + 1
        client.guard_tools()
        exposed_notes = {(note_aliases or {}).get(item['source_id'], item['source_id'])
                         for item in materials if item['kind'] == 'note'}
        baselines = tuple(notes.freeze_baseline(key, snapshot, allowed_keys=note_keys)
                          for key in note_keys if key not in exposed_notes) if notes else ()
        material = selected_context(snapshot, references, actions, state, tuple(materials) + baselines)
    except BaseException:
        close_session(client, 'context-failure')
        raise
    covered = [item['id'] for item in wakes if item['cursor'] <= wake['cursor']
               and item['window'] == wake['window']
               and item['event_key'] in {record['ref']['event_key'] for record in screen['records']}
               and item['id'] not in state['acknowledged']]
    instruction = ('Fictional archive calibration. Read only snapshot-qualified references. '
                   'selector_inference material is inference, not evidence.')
    if notes:
        instruction += (' Note revision sources use visible coordinates: record:<source_alias>:<event_key>, '
                        'material:<source_id>:<version_id>, action:<output_id>#<position>, generated:self '
                        'or generated:<output_id>. Baselines are harness file state, not statement sources. '
                        'generated:self supports expression or intent, never external success. '
                        'Use read_note to receive exact current bytes; a revision status is not a read. '
                        'A result from another tool in the same batch is not yet in your input.')
    messages = [{'role': 'system', 'content': instruction},
                {'role': 'user', 'content': canonical({'wake': {k: wake[k] for k in ('id', 'reason', 'window')},
                    'screen': screen, 'selected': material}).decode()}]
    stopped, output_ids, tool_count = [False], [], [0]
    chat = None
    previous_current = client.current
    attribution = {'role': 'chat' if process == 'main' else 'librarian',
                   'wake': wake['id'], 'snapshot_id': snapshot['snapshot_id']}

    def should_stop():
        if stopped[0] or cancelled():
            stopped[0] = True
            close_session(client, 'cancelled')
        return stopped[0]

    def guard():
        if should_stop():
            raise RuntimeError('cancelled before tool/request')
        client.guard_tools()
        recover_commits(root)
        if notes and (notes.poisoned or (notes.root / 'persistence-unknown.json').exists()
                      or any(notes.root.rglob('*.new'))):
            raise RuntimeError('note persistence unknown; session closed')

    def persist_output(assistant, calls):
        guard()
        current = recover_commits(root)
        first = not any(v['wake_id'] == wake['id'] for v in current['outputs'].values())
        output = f"{wake['id']}/output-{1 + sum(v['wake_id'] == wake['id'] for v in current['outputs'].values())}"
        value = {'id': output, 'wake_id': wake['id'], 'snapshot_id': snapshot['snapshot_id'],
                 'generation': generation, 'parent_generation': generation - 1,
                 'request_id': client.ledger.state['requests'][-1]['id'], 'context_sha256': digest(client.exchanges[-1]['body']['messages']),
                 'screen': screen if first else None, 'covered_wakes': covered if first else [],
                 'acknowledged': covered if first else [],
                 'wake_facts': [dict(item) for item in wakes if item['id'] in covered] if first else [],
                 'assistant': {k: deepcopy(v) for k, v in assistant.items() if k != 'reasoning_content'}}
        fault = {'output-failure': 'before', 'output-flush': 'flush', 'output-fsync': 'fsync',
                 'output-after-rename': 'after-rename'}.get(scenario) if first else None
        append_commit(root, 'output', value, fault=fault)
        client.trace.append({'event': 'output-commit', 'output': output, 'request_id': value['request_id'], 'process': process})
        output_ids.append(output)
        if scenario in {'stop-before-tool', 'cancel-before-tool'}:
            stopped[0] = True
            close_session(client, 'cancelled')
        elif after_output:
            after_output(output, process)
        return output

    def execute(function):
        guard()
        action = chat.active_action
        append_commit(root, 'dispatch', {'action': action})
        client.trace.append({'event': 'dispatch', 'action': action, 'process': process})
        try:
            content, status = function(action)
        except ValueError as error:
            append_commit(root, 'result', {'action': action, 'status': 'failed', 'error': type(error).__name__})
            client.trace.append({'event': 'result-commit', 'action': action, 'status': 'failed'})
            raise
        tool_count[0] += 1
        client.trace.append({'event': 'tool', 'action': action, 'process': process})
        append_commit(root, 'result', {'action': action, 'content': content, 'status': status},
                      fault='before' if scenario == 'result-failure' else None)
        client.trace.append({'event': 'result-commit', 'action': action, 'status': status})
        if scenario == 'cancel-after-tool':
            stopped[0] = True
            close_session(client, 'cancelled')
        return content

    def read_reference(snapshot_id: str, source_alias: str, event_key: str) -> str:
        """Read one complete original record from this wake's frozen snapshot."""
        def read(_):
            result = resolve_ref(snapshot, {'snapshot_id': snapshot_id, 'source_alias': source_alias, 'event_key': event_key})
            return canonical({'ref': {'snapshot_id': snapshot_id, 'source_alias': source_alias, 'event_key': event_key},
                              'origin': result['visible_origin'], 'sha256': result['sha256'],
                              'raw': result['raw'].decode()}).decode(), 'completed'
        return execute(read)

    def read_note(key: str) -> str:
        """Read one authorized committed note, or its explicit absent/tombstone baseline."""
        from stateless_notes import read_committed
        def read(_):
            if key not in note_keys:
                raise ValueError('note scope not authorized')
            value = read_committed(notes.root, key, snapshot, allowed_keys=note_keys)
            if value is None:
                value = notes.freeze_baseline(key, snapshot, allowed_keys=note_keys)
            wire = {k: v for k, v in value.items() if k != 'raw'} | {'text': value['raw'].decode()}
            return canonical({'note_material': wire}).decode(), 'completed'
        return execute(read)

    def revise_note(key: str, body: str, reason: str, expectation: str, sources: list[str], claim: str = 'draft') -> str:
        """Propose exact Markdown from this output; source coordinates must be in your actual input.

        claim: draft/inference/correction/observation/quote/attempt/success/failed/unknown; success needs an input action result.
        """
        def revise(action):
            registered = notes.register_trigger(root, wake['id'], snapshot, allowed_keys=note_keys)
            task = notes.admit_trigger(registered['id'], root, snapshot, through_output=action.rsplit('#', 1)[0],
                                       note_aliases=note_aliases)
            mapping = note_source_handles(task['cutoff'])
            try:
                if type(sources) is not list or any(type(source) is not str or source not in mapping for source in sources):
                    raise ValueError('source coordinate was not exposed to generator')
                identities = sorted({identity for source in sources for identity in mapping[source]})
                candidate = notes.prepare_candidate(task['id'], key, body.encode(), sources=identities,
                    reason=reason, expectation=expectation,
                    statements=[{'kind': claim, 'text': reason, 'sources': identities}],
                    producer={'process': process, 'basis': 'native revise_note tool arguments', 'output_id': task['cutoff']['output_id']})
            except ValueError as error:
                return canonical({'note_revision': {'status': 'rejected', 'reason': str(error)}}).decode(), 'failed'
            outcome = notes.commit_candidate(candidate)
            client.trace.append({'event': 'note-revision', 'action': action, 'candidate_id': candidate,
                                 'task_id': task['id'], 'process': process, 'outcome': outcome})
            # A persistence failure is never delivered as ordinary successful
            # text. The shared Ledger closes before another Chat/tool can run.
            if outcome['status'] == 'failed' or notes.poisoned:
                raise RuntimeError('note persistence unknown')
            return canonical({'note_revision': {'candidate_id': candidate, **outcome}}).decode(), (
                'completed' if outcome['status'] in {'committed', 'no-op'} else 'failed')
        return execute(revise)

    functions = [read_reference] + ([read_note, revise_note] if notes else [])
    chat = llm.Chat(model=client.config['default_model'], messages=messages,
                    functions=functions, chat_client=client, do_process_image=False)
    chat.fail_fast = chat.preserve_native = True
    chat.on_output = persist_output
    chat.should_stop = should_stop
    def next_request():
        try:
            guard()
        except Exception as error:
            raise llm.RequiredContextError(str(error)) from error
        client.current = dict(attribution)
        return []
    chat.add_context_provider(next_request)
    client.chats.append(chat)
    client.current = dict(attribution)
    try:
        chat.chat(stream=False)
        guard() if not stopped[0] else None
    except BaseException:
        close_session(client, 'session-failure')
        raise
    finally:
        client.current = previous_current
    return {'wake': wake['id'], 'process': process, 'screen': screen, 'outputs': output_ids,
            'tools': tool_count[0], 'cancelled': stopped[0]}


def finish_wake(root, wake_id, status, request_ids, *, reason=None, client=None, fault=None):
    """Commit the round cutoff separately from first-output wake confirmation."""
    try:
        state = recover_commits(root)
        value = {'wake_id': wake_id, 'generation': state['generation'], 'status': status,
                 'request_ids': list(request_ids), 'cutoff_head': state['head'], 'reason': reason}
        append_commit(root, 'wake-end', value, fault=fault)
    except BaseException:
        if client is not None:
            close_session(client, 'terminal-persistence-failure')
        raise
    return value
