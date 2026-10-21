"""Offline OfficialClient/SDK/native workspace calibration. All choices are fixtures."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import resource
import sys
import time
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import subject_probe as probe
import stateless_capacity_client as capacity
import stateless_official as official
import stateless_loop as loop
from stateless_capacity_calibration import isolation, official_fixture
from a0_ledger import canonical, digest, durable

CASES = ('active', 'quiet', 'rest', 'same-batch', 'cancel', 'budget', 'usage',
         'effect-before', 'effect-after', 'result-unknown', 'revisit-unknown', 'note-unknown', 'input-drift', 'wake-budget')


def fictional_operator(root, case):
    """Invent records, never manufacture a public ready/run authorization."""
    prepared = probe.prepare(root, run_id='fixture-subject-' + case)
    assert prepared['status'] == 'prepared-not-ready' and probe.verify(root)['status'] == 'not-ready'
    template = json.loads((root / 'official-template.json').read_bytes())
    builder = root / 'fixture-record-builder'
    builder.mkdir()
    def fill(manifest, data):
        manifest.update(deepcopy(template))
        data['plan.json'] = json.loads((root / 'plan.json').read_bytes())
        receipt = data['research-allocation.json']['reservations'][0]
        receipt.update(root=str(root), run_id=template['run_id'], binding=official.binding(template), budgets=deepcopy(template['budgets']))
        data['authorization.json']['binding'] = official.binding(template)
    folder, manifest = official_fixture(builder, 'operator', budgets=template['budgets'], role_stages=template['role_stages'], mutate=fill)
    for name in official.FILES:
        if name != 'plan.json': (root / name).write_bytes((folder / name).read_bytes())
    durable(root / 'manifest.json', manifest)
    assert probe.verify(root, _calibration=official._CALIBRATION)['status'] == 'verified-local-records-only'
    assert probe.verify(root)['status'] == 'not-ready'
    return manifest


def tool(name, arguments, request, position):
    return {'id': f'fixture-{request}-{position}', 'type': 'function',
            'function': {'name': name, 'arguments': canonical(arguments).decode()}}


def fixture_response(client, body, *, case):
    """These tool bodies/reasons are predeclared fake-model choices, not autonomy."""
    messages = body['messages']
    context = json.loads(messages[1]['content'])
    wake = context['wake']['id']
    round_number = 1 + sum(v['role'] == 'assistant' for v in messages)
    number = client.attempts
    def call(name, args, pos): return tool(name, args, number, pos)
    calls, content = [], 'FIXTURE_FINISHED_OR_SILENT'
    if case == 'quiet':
        content = ''
    elif case == 'rest':
        if round_number == 1: calls = [call('request_revisit', {'delay_ticks': 0, 'reason': 'fixture decline future opportunities'}, 1)]
    elif wake == 'subject-1':
        if round_number == 1:
            calls = [call('observe_workspace', {}, 1), call('move_object', {'object_id': 'circle', 'destination': 'left'}, 2),
                     call('write_card', {'card_id': 'one', 'text': 'FIXTURE_EXTERNAL_CARD'}, 3)]
            if case == 'same-batch':
                calls.append(call('revise_note', {'key': 'global/main', 'body': 'UNCOMMITTED_SAME_BATCH\n',
                    'reason': 'fixture premature success', 'expectation': 'refuse unseen result',
                    'sources': ['tool:fixture-' + str(number) + '-2'], 'claim': 'success'}, 4))
        elif round_number == 2:
            previous = next(v for v in messages if v.get('tool_calls'))['tool_calls'][1]['id']
            calls = [call('revise_note', {'key': 'global/main', 'body': '# Fixture\nI placed the circle on the left.\n',
                'reason': 'fixture reading of actual move result', 'expectation': 'later exact input can revisit it',
                'sources': ['tool:' + previous], 'claim': 'success'}, 1),
                     call('read_note', {'key': 'global/main'}, 2),
                     call('request_revisit', {'delay_ticks': 1 if case == 'active' else 2,
                                             'reason': 'fixture optional revisit'}, 3)]
    else:
        if round_number == 1:
            calls = [call('observe_workspace', {}, 1)]
        elif round_number == 2 and wake == 'subject-2':
            previous = next(v for v in messages if v.get('tool_calls'))['tool_calls'][0]['id']
            calls = [call('revise_note', {'key': 'global/main', 'body': '# Fixture\nThe clock slid the circle back to the table.\n',
                'reason': 'fixture observation of independent clock effect', 'expectation': 'optional later request sees exact changed note',
                'sources': ['tool:' + previous], 'claim': 'observation'}, 1), call('read_note', {'key': 'global/main'}, 2)]
    message = {'role': 'assistant', 'content': '' if calls else content, 'reasoning_content': 'FIXTURE_EPHEMERAL_ONLY'}
    if calls: message['tool_calls'] = calls
    usage = {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120, 'prompt_cache_hit_tokens': 40}
    if case == 'usage' and wake == 'subject-2': usage = None
    return {'id': 'fixture-subject-response', 'object': 'chat.completion', 'created': 1767225600,
            'model': capacity.WIRE_MODEL, 'system_fingerprint': 'fixture-subject-v1',
            'choices': [{'index': 0, 'finish_reason': 'tool_calls' if calls else 'stop', 'message': message}], 'usage': usage}


def run_case(root, case):
    fictional_operator(root, case)
    cancel = [False]
    original_act, original_save = probe.Workspace.act, probe.Workspace._save
    original_append = loop.append_commit
    original_commit = probe.NoteWriter.commit_candidate
    fired = [False]
    def act(world, action, operation, arguments):
        result = original_act(world, action, operation, arguments)
        if case == 'cancel': cancel[0] = True
        return result
    def save(world, state):
        if case in ('effect-before', 'effect-after') and state['effects'] and not fired[0]:
            fired[0] = True
            if case == 'effect-after': original_save(world, state)
            world.poisoned = True
            durable(world.root / 'world-persistence-unknown.json', {'status': 'fixture IO unknown', 'resume': False})
            raise OSError('fixture external effect persistence failure')
        return original_save(world, state)
    def append(root_arg, kind, value, **kwargs):
        if kind == 'result' and not fired[0] and (case == 'result-unknown' or
                case == 'revisit-unknown' and 'revisit_action' in value.get('content', '')):
            fired[0] = True
            return original_append(root_arg, kind, value, fault='after-rename')
        return original_append(root_arg, kind, value, **kwargs)
    def commit(writer, candidate, **kwargs):
        return original_commit(writer, candidate, fault='current-after-replace') if case == 'note-unknown' else original_commit(writer, candidate, **kwargs)
    def response(client, body):
        value = fixture_response(client, body, case=case)
        if case == 'budget' and client.attempts == 1:
            # Legal near-capacity settlement; next full R cannot fit the 2M
            # shared token ceiling. The first actual tool result stays known.
            value['usage'] = {'prompt_tokens': 1000000, 'completion_tokens': 20, 'total_tokens': 1000020}
        if case == 'wake-budget' and client.attempts == 3:
            value['usage'] = {'prompt_tokens': 1000000, 'completion_tokens': 20, 'total_tokens': 1000020}
        if case == 'input-drift' and client.attempts == 1:
            (root / 'bootstrap.txt').write_bytes(b'fixture changed frozen input')
        return value
    with patch.object(capacity.CapacityClient, '_fixture_response', response), patch.object(probe.Workspace, 'act', act), \
         patch.object(probe.Workspace, '_save', save), patch.object(loop, 'append_commit', append), \
         patch.object(probe.NoteWriter, 'commit_candidate', commit):
        report = probe.run_probe(root, temporary_api_key='sk-fictional-official-only',
            _calibration=official._CALIBRATION, cancelled=lambda: cancel[0])
    return report


def doctor(root, cases=CASES):
    started, results = time.perf_counter(), []
    with isolation(root) as counters, redirect_stdout(io.StringIO()) as console:
        for case in cases:
            folder = root / case
            report = run_case(folder, case)
            ledger = report['ledger']
            assert all(not item['sent'] and not item['transport_attempted'] for item in report['transport'])
            wire = [json.loads(p.read_bytes()) for p in sorted(folder.glob('request-*.json'))]
            assert len(wire) == len(ledger['requests'])
            assert all(v['attribution']['role_id'] == 'chat' and v['attribution']['stage'] == 'question' for v in wire)
            assert all(v['status'] == 'settled' for v in ledger['requests'])
            before = {str(p.relative_to(folder)): probe._hash(p.read_bytes()) for p in folder.rglob('*') if p.is_file()}
            with patch.object(official.OfficialClient, '__init__', side_effect=AssertionError('recovery constructed a client')), \
                 patch.object(probe.Workspace, 'act', side_effect=AssertionError('recovery replayed action')):
                recovery = probe.recover(folder)
            assert before == {str(p.relative_to(folder)): probe._hash(p.read_bytes()) for p in folder.rglob('*') if p.is_file()}
            assert recovery['resume'] is False and ledger['closed']
            if case in ('active', 'quiet', 'rest', 'same-batch'):
                assert report['status'] == 'complete', report['failure']
                state = json.loads((folder / 'world.json').read_bytes())['value']
                journal = loop.recover_commits(folder)
                assert len(state['deliveries']) == (1 if case == 'rest' else 2 if case == 'quiet' else 3)
                assert state['deliveries'][0]['source'] == 'bootstrap'
                first = next(iter(journal['outputs'].values()))
                assert first['acknowledged'] == ['subject-1']
                if case not in ('quiet', 'rest'):
                    assert [v['source'] for v in state['deliveries']] == ['bootstrap', 'harness-clock', 'model-revisit']
                    requested = state['deliveries'][2]['request_action']
                    assert journal['results'][requested]['status'] == 'completed' and requested in state['revisits']
                    if case == 'active':
                        assert [v['tick'] for v in state['deliveries']] == [0, 1, 1]
                    observations = []
                    for action, receipt in state['effects'].items():
                        if receipt['operation'] != 'observe_workspace': continue
                        observed = json.loads(receipt['content'])['world_result']
                        wake_id = action.split('/')[0]
                        delivery = next(v for v in state['deliveries'] if v['wake_id'] == wake_id)
                        assert observed['delivery'] == delivery
                        expected = [] if wake_id == 'subject-1' else [
                            {'action': requested, **state['revisits'][requested],
                             'status': 'pending' if wake_id == 'subject-2' else 'delivered'}]
                        assert observed['revisits'] == expected
                        assert set(observed) == {'tick', 'objects', 'cards', 'last_clock', 'delivery', 'revisits', 'permission'}
                        identity, position = action.rsplit('#', 1)
                        call_id = journal['outputs'][identity]['assistant']['tool_calls'][int(position) - 1]['id']
                        assert any(message.get('role') == 'tool' and message.get('tool_call_id') == call_id
                                   and message['content'] == receipt['content']
                                   for request in wire if request['attribution']['task_id'] == wake_id
                                   for message in request['envelope']['body']['messages'])
                        observations.append(observed)
                    assert len(observations) == 3
                    assert state['objects']['circle'] == 'table'
                    assert state['cards']['one'] == 'FIXTURE_EXTERNAL_CARD'
                    assert state['clocks'][0]['moved'] == [{'object_id': 'circle', 'from': 'left', 'to': 'table'},
                                                         {'object_id': 'triangle', 'from': 'left', 'to': 'table'}]
                    current = (folder / 'notes/current/global/main.md').read_bytes()
                    third = [v for v in wire if v['attribution']['task_id'] == 'subject-3']
                    initial = third[0]['envelope']['body']['messages']
                    assert len(initial) == 2
                    materials = json.loads(initial[1]['content'])['selected']['materials']
                    assert any(v['text'].encode() == current and v['sha256'] == probe._hash(current) for v in materials)
                    # Rejected tool arguments remain legitimate same-wake native history.
                    # Historical generated actions may also be explicitly selected later;
                    # the rejected candidate must never become selected note material.
                    assert b'UNCOMMITTED_' not in canonical(materials)
                    assert all(b'UNCOMMITTED_' not in p.read_bytes() for p in (folder / 'notes/versions').glob('*.md'))
                    assert b'slid the circle' in canonical(third)
                    # World feedback is absent until the clock actually advances.
                    first_wire = [v for v in wire if v['attribution']['task_id'] == 'subject-1']
                    assert b'"tilted"' not in canonical(first_wire)
                    if case == 'same-batch': assert any(v['status'] == 'failed' for v in journal['results'].values())
                elif case == 'quiet':
                    assert len(wire) == 2 and not state['effects'] and not state['revisits']
                    assert not list((folder / 'notes/current').rglob('*.md'))
                else:
                    assert state['rest'] and len(state['effects']) == 1
                results.append({'case': case, 'status': 'pass', 'wakes': len(state['deliveries']), 'requests': len(wire)})
            else:
                assert report['status'] == 'stopped', case
                assert ledger['closed'], case
                if case in ('effect-before', 'effect-after', 'result-unknown', 'revisit-unknown'):
                    assert recovery['world']['status'] == 'unavailable-or-unknown', case
                if case == 'note-unknown':
                    assert recovery['notes']['value']['persistence_unknown'] is True, case
                    assert all(not v['applied_audit_confirmed'] for v in recovery['notes']['value']['notes'].values())
                if case == 'revisit-unknown':
                    state = json.loads((folder / 'world.json').read_bytes())['value']
                    assert len(state['deliveries']) == 1 and state['revisits']
                if case == 'wake-budget':
                    state = json.loads((folder / 'world.json').read_bytes())['value']
                    assert len(state['deliveries']) == 1 and state['tick'] == 0
                # Reconstructing the public run must fail before another request:
                # Official execute-once claim remains even if root is otherwise valid.
                previous = len(wire)
                try: probe.run_probe(folder, temporary_api_key='sk-fictional-official-only', _calibration=official._CALIBRATION)
                except (RuntimeError, ValueError, OSError): pass
                else: raise AssertionError('failed root replayed')
                assert len(list(folder.glob('request-*.json'))) == previous
                results.append({'case': case, 'status': 'pass', 'requests': len(wire), 'no_replay': True})
        assert all(v == 0 for values in counters for v in values.values())
    durable(root / 'parser-console.json', {'fictional_parser_console': console.getvalue()})
    result = {'schema': 'subject-probe-doctor-v1', 'status': 'ok', 'assertions': results, 'sentinels': counters,
              'source_hashes': probe.source_hashes(), 'elapsed_seconds': time.perf_counter() - started,
              'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'limitation': 'scripted fixture choices prove composition/refusal, not autonomy or semantic quality'}
    durable(root / 'doctor.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('doctor', 'run'), default='doctor')
    parser.add_argument('--output', required=True)
    parser.add_argument('--case', choices=CASES)
    args = parser.parse_args(argv)
    try:
        root = probe._root(args.output)
        if args.command == 'doctor':
            root.mkdir(parents=True, mode=0o700)
            result = doctor(root, CASES if args.case is None else (args.case,))
        else:
            # Single fixture run remains isolated/closed, never a live selector.
            with isolation(root.parent), redirect_stdout(io.StringIO()): result = run_case(root, args.case or 'active')
        print(json.dumps({'status': result['status'], 'assertions': len(result.get('assertions', []))}))
        return 0 if result['status'] in ('ok', 'complete') else 1
    except (RuntimeError, ValueError, OSError, KeyError, AssertionError) as error:
        print('subject calibration: ' + type(error).__name__ + ': ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
