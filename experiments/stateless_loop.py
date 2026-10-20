"""Synthetic fresh-context loop and read-only action-commit recovery.

No production lifecycle, archive path import, model transport, or note writer.
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


def drive_wake(snapshot, wake, wakes, root, client, *, references=(), actions=(), materials=(), scenario='normal'):
    """One new Chat; same-wake native pairs live only inside this invocation."""
    if scenario == 'construct-failure':
        raise ValueError('synthetic screen construction failure')
    screen = build_screen(snapshot, wake)
    state = recover_commits(root)
    material = selected_context(snapshot, references, actions, state, materials)
    generation = state['generation'] + 1
    covered = [item['id'] for item in wakes if item['cursor'] <= wake['cursor']
               and item['window'] == wake['window']
               and item['event_key'] in {record['ref']['event_key'] for record in screen['records']}
               and item['id'] not in state['acknowledged']]
    messages = [{'role': 'system', 'content': 'Fictional archive calibration. Read only snapshot-qualified references. selector_inference material is inference, not evidence.'},
                {'role': 'user', 'content': canonical({'wake': {k: wake[k] for k in ('id', 'reason', 'window')},
                    'screen': screen, 'selected': material}).decode()}]
    stopped = [False]
    first = [True]
    output_ids = []
    tool_count = [0]
    chat = None

    def persist_output(assistant, calls):
        client.guard_tools()
        output = f"{wake['id']}/output-{len(output_ids) + 1}"
        value = {'id': output, 'wake_id': wake['id'], 'snapshot_id': snapshot['snapshot_id'],
                 'generation': generation, 'parent_generation': generation - 1,
                 'request_id': client.ledger.state['requests'][-1]['id'], 'context_sha256': digest(client.exchanges[-1]['body']['messages']),
                 'screen': screen if first[0] else None, 'covered_wakes': covered if first[0] else [],
                 'acknowledged': covered if first[0] else [],
                 'wake_facts': [dict(item) for item in wakes if item['id'] in covered] if first[0] else [],
                 'assistant': {k: deepcopy(v) for k, v in assistant.items() if k != 'reasoning_content'}}
        fault = {'output-failure': 'before', 'output-flush': 'flush', 'output-fsync': 'fsync',
                 'output-after-rename': 'after-rename'}.get(scenario) if first[0] else None
        append_commit(root, 'output', value, fault=fault)
        client.trace.append({'event': 'output-commit', 'output': output})
        output_ids.append(output)
        first[0] = False
        if scenario in {'stop-before-tool', 'cancel-before-tool'}:
            stopped[0] = True
        return output

    def read_reference(snapshot_id: str, source_alias: str, event_key: str) -> str:
        """Read one complete original record from this wake's frozen snapshot."""
        if stopped[0]:
            raise RuntimeError('cancelled before tool')
        client.guard_tools()
        action = chat.active_action
        append_commit(root, 'dispatch', {'action': action})
        client.trace.append({'event': 'dispatch', 'action': action})
        try:
            result = resolve_ref(snapshot, {'snapshot_id': snapshot_id, 'source_alias': source_alias, 'event_key': event_key})
        except ValueError:
            append_commit(root, 'result', {'action': action, 'status': 'failed', 'error': 'reference-refused'})
            client.trace.append({'event': 'result-commit', 'action': action, 'status': 'failed'})
            raise
        content = canonical({'ref': {'snapshot_id': snapshot_id, 'source_alias': source_alias, 'event_key': event_key},
                             'origin': result['visible_origin'], 'sha256': result['sha256'],
                             'raw': result['raw'].decode()}).decode()
        tool_count[0] += 1
        client.trace.append({'event': 'tool', 'action': action, 'raw_sha256': hashlib.sha256(result['raw']).hexdigest()})
        append_commit(root, 'result', {'action': action, 'content': content},
                      fault='before' if scenario == 'result-failure' else None)
        client.trace.append({'event': 'result-commit', 'action': action})
        if scenario == 'cancel-after-tool':
            stopped[0] = True
        return content

    chat = llm.Chat(model=client.config['default_model'], messages=messages,
                    functions=[read_reference], chat_client=client, do_process_image=False)
    chat.fail_fast = True
    chat.preserve_native = True
    chat.on_output = persist_output
    chat.should_stop = lambda: stopped[0]
    # Keep references alive for object-isolation evidence; never reuse messages.
    client.chats.append(chat)
    client.current = {'wake': wake['id'], 'snapshot_id': snapshot['snapshot_id']}
    chat.chat(stream=False)
    return {'wake': wake['id'], 'screen': screen, 'outputs': output_ids,
            'tools': tool_count[0], 'cancelled': stopped[0]}


def finish_wake(root, wake_id, status, request_ids, *, reason=None):
    """Commit the round cutoff separately from first-output wake confirmation."""
    state = recover_commits(root)
    value = {'wake_id': wake_id, 'generation': state['generation'], 'status': status,
             'request_ids': list(request_ids), 'cutoff_head': state['head'], 'reason': reason}
    append_commit(root, 'wake-end', value)
    return value
