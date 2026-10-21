"""Offline OfficialClient/SDK/native workspace calibration. All choices are fixtures."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import resource
import shutil
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

TRANSFER_CASES = ('prior-active', 'prior-quiet', 'prior-cancel', 'prior-budget', 'prior-usage', 'prior-drift', 'prior-reject')
CASES = ('active', 'quiet', 'rest', 'same-batch', 'cancel', 'budget', 'usage',
         'effect-before', 'effect-after', 'result-unknown', 'revisit-unknown', 'note-unknown', 'input-drift', 'wake-budget') + TRANSFER_CASES


def fictional_operator(root, case, **prepare_options):
    """Invent records, never manufacture a public ready/run authorization."""
    prepared = probe.prepare(root, run_id='fixture-subject-' + case, **prepare_options)
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
    # Exercise public seal too. Fixture records stay synthetic and the public
    # verify therefore stays not-ready; only the internal SDK path accepts them.
    assert probe.seal(root)['status'] == 'not-ready'
    assert json.loads((root / 'manifest.json').read_bytes()) == manifest
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
    if case.startswith('prior-'):
        if case == 'prior-quiet':
            content = ''
        elif round_number == 1:
            calls = [call('observe_workspace', {}, 1)]
            if wake == 'subject-1':
                calls += [call('move_object', {'object_id': 'circle', 'destination': 'left'}, 2),
                          call('request_revisit', {'delay_ticks': 1, 'reason': 'fixture optional new-world revisit'}, 3)]
    elif case == 'quiet':
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


def tree_hashes(root):
    return {str(p.relative_to(root)): probe._hash(p.read_bytes()) for p in root.rglob('*') if p.is_file()}


def copy_prior_fixture(prior_root, origin):
    """Relocate synthetic origin evidence coherently before negative mutation."""
    shutil.copytree(prior_root, origin)
    subject = json.loads((origin / 'subject.json').read_bytes())
    subject['root'] = str(origin)
    durable(origin / 'subject.json', subject)
    durable(origin / 'plan.json', probe._entry(subject, (origin / 'subject.json').read_bytes(), probe.BOOTSTRAP))
    manifest = json.loads((origin / 'manifest.json').read_bytes())
    manifest['root'] = str(origin)
    manifest['records']['plan.json'] = probe._hash((origin / 'plan.json').read_bytes())
    durable(origin / 'manifest.json', manifest)
    ledger = json.loads((origin / 'ledger.json').read_bytes())['state']
    ledger['manifest'] = digest(manifest)
    durable(origin / 'ledger.json', {'state': ledger, 'sha256': digest(ledger)})
    assert probe.official.recover(origin)['ledger_status'] == 'closed'
    # Prove the copy reaches the note checks; an earlier identity refusal must
    # not be counted as a scope/source/receipt negative below.
    probe._prior_note(origin / 'notes', 'global/main', origin.parent / 'uncreated-preflight')
    return origin


def transfer_refusals(root, prior_root):
    """Mutate only private copies: supplied hashes cannot replace commit evidence."""
    checks, reasons = [], {}
    original = tree_hashes(prior_root)
    def refused(label, operation, destination, *, expected=None):
        before = tree_hashes(destination) if destination.exists() else None
        with patch.object(official.OfficialClient, '__init__', side_effect=AssertionError('refusal constructed client')):
            try: operation()
            except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
                reasons[label] = str(error)
                if expected is not None: assert str(error) == expected, (label, str(error))
            else: raise AssertionError('accepted invalid prior input: ' + label)
        assert (tree_hashes(destination) if destination.exists() else None) == before, label
        checks.append(label)
    def prepare(destination, notes, key='global/main'):
        return probe.prepare(destination, prior_notes_root=notes, prior_note_key=key, seed=8)
    notes = prior_root / 'notes'
    for label, options in (('root-without-key', {'prior_notes_root': notes}),
                           ('key-without-root', {'prior_note_key': 'global/main'})):
        target = root / label
        refused(label, lambda: probe.prepare(target, **options), target)
    target = root / 'scope'
    refused('scope', lambda: prepare(target, notes, 'person/fixture'), target)
    for label, target in (('overlap-inside', prior_root / 'uncreated-child'),
                          ('overlap-parent', prior_root.parent), ('overlap-dotdot', prior_root / 'elsewhere/..')):
        refused(label, lambda: prepare(target, notes), target)
    # Copies retain frozen origin paths in their subject record; rewrite that
    # path only so each negative has its own internally consistent fixture root.
    for case in ('absent', 'receipt', 'bytes', 'foreign-source', 'future-cursor', 'foreign-snapshot', 'branch', 'deleted', 'non-synthetic',
                 'paused', 'persistence-unknown', 'active-origin'):
        origin = copy_prior_fixture(prior_root, root / ('origin-' + case))
        current = origin / 'notes/current/global/main.md'
        if case == 'absent': current.unlink()
        elif case == 'receipt': sorted((origin / 'notes/receipts').glob('*.json'))[-1].unlink()
        elif case == 'bytes': current.write_bytes(current.read_bytes() + b'EXTERNAL_EDIT')
        elif case == 'paused':
            (origin / 'notes/pauses/global').mkdir()
            durable(origin / 'notes/pauses/global/main.json', {'fixture': True})
        elif case == 'persistence-unknown': durable(origin / 'notes/persistence-unknown.json', {'fixture': True})
        elif case == 'non-synthetic':
            import stateless_notes as note_module
            manifest_path = origin / 'notes/manifest.json'
            manifest = note_module._read_json(manifest_path)
            manifest['synthetic'] = False
            manifest_path.write_bytes(note_module._pack(manifest))
        elif case == 'active-origin':
            envelope = json.loads((origin / 'ledger.json').read_bytes())
            envelope['state']['closed'] = ''
            durable(origin / 'ledger.json', {'state': envelope['state'], 'sha256': digest(envelope['state'])})
        else:
            # Make locally coherent forged current/archive/receipt bytes to
            # reach the original source/cursor/snapshot checks, not just SHA.
            import stateless_notes as note_module
            metadata, body = note_module._decode(current.read_bytes())
            metadata.pop('version_id')
            if case == 'foreign-source': metadata['source_frozen_id'] = 'foreign-fixture'
            elif case == 'future-cursor': metadata['cursor'] = 2
            elif case == 'foreign-snapshot': metadata['snapshot_id'] = 'foreign-fixture'
            elif case == 'deleted': metadata['operation'] = 'delete'
            else: metadata['branch'] = 'other-fixture'
            raw = note_module._encode(metadata, body)
            new_metadata, _ = note_module._decode(raw)
            current.write_bytes(raw)
            (origin / 'notes/versions' / (new_metadata['version_id'] + '.md')).write_bytes(raw)
            receipt_path = sorted((origin / 'notes/receipts').glob('*.json'))[-1]
            row = note_module._read_json(receipt_path)
            row.update(version_id=new_metadata['version_id'], sha256=probe._hash(raw))
            receipt_path.write_bytes(note_module._pack(row))
        target = root / ('rejected-' + case)
        refused(case, lambda: prepare(target, origin / 'notes'), target)
    historical = copy_prior_fixture(prior_root, root / 'historical-code-origin')
    manifest = json.loads((historical / 'manifest.json').read_bytes())
    for name in ('subject_probe.py', 'subject_probe_calibration.py'):
        manifest['contract']['code'].pop('experiments/' + name, None)
    durable(historical / 'manifest.json', manifest)
    ledger = json.loads((historical / 'ledger.json').read_bytes())['state']
    ledger['manifest'] = digest(manifest)
    durable(historical / 'ledger.json', {'state': ledger, 'sha256': digest(ledger)})
    assert prepare(root / 'historical-code-subset', historical / 'notes')['status'] == 'prepared-not-ready'
    # The donor is a real closed SDK-Mock run, not a fabricated checksum.
    donor = root / 'other-closed-run'
    assert run_case(donor, 'quiet')['status'] == 'complete'
    foreign_ledger = (donor / 'ledger.json').read_bytes()
    for when in ('before-prepare', 'after-prepare'):
        origin = copy_prior_fixture(prior_root, root / ('ledger-origin-' + when))
        target = root / ('foreign-ledger-' + when)
        if when == 'after-prepare': prepare(target, origin / 'notes')
        (origin / 'ledger.json').write_bytes(foreign_ledger)
        assert official.recover(origin)['ledger_status'] == 'closed'
        operation = (lambda: prepare(target, origin / 'notes')) if when == 'before-prepare' else (
            lambda: probe.run_probe(target, temporary_api_key='sk-fictional-official-only', _calibration=official._CALIBRATION))
        refused('foreign-ledger-' + when, operation, target, expected='prior Ledger origin identity drift')
    origin = copy_prior_fixture(prior_root, root / 'manifest-origin')
    manifest_path = origin / 'manifest.json'
    saved_manifest = manifest_path.read_bytes()
    manifest = json.loads(saved_manifest)
    manifest['run_id'] += '-foreign'
    durable(manifest_path, manifest)
    target = root / 'manifest-before-prepare'
    refused('manifest-before-prepare', lambda: prepare(target, origin / 'notes'), target,
            expected='prior historical manifest/input binding drift')
    manifest_path.write_bytes(saved_manifest)
    target = root / 'manifest-after-prepare'
    prepare(target, origin / 'notes')
    assert 'manifest.json' in json.loads((target / 'subject.json').read_bytes())['prior_note']['origin']['files']
    # Canonical manifest identity remains valid; only its pinned bytes change.
    manifest_path.write_bytes(saved_manifest + b' ')
    refused('manifest-after-prepare', lambda: probe.run_probe(target, temporary_api_key='sk-fictional-official-only',
        _calibration=official._CALIBRATION), target, expected='prior note bytes/version/origin drift')
    manifest_path.write_bytes(saved_manifest)
    plan_path = origin / 'plan.json'
    plan_path.write_bytes(plan_path.read_bytes() + b' ')
    target = root / 'plan-record-drift'
    refused('plan-record-drift', lambda: prepare(target, origin / 'notes'), target, expected='prior record hash drift')
    frozen = root / 'prepared'
    prepare(frozen, notes)
    original_subject = (frozen / 'subject.json').read_bytes()
    for field in ('version_id', 'sha256', 'origin'):
        subject = json.loads(original_subject)
        if field == 'origin': subject['prior_note'][field]['run_id'] += '-drift'
        else: subject['prior_note'][field] = '0' * 64
        durable(frozen / 'subject.json', subject)
        refused('frozen-' + field, lambda: probe.run_probe(frozen, temporary_api_key='sk-fictional-official-only',
            _calibration=official._CALIBRATION), frozen)
    (frozen / 'subject.json').write_bytes(original_subject)
    raw = (frozen / probe.PRIOR_FILE).read_bytes()
    (frozen / probe.PRIOR_FILE).write_bytes(raw + b'COPY_DRIFT')
    refused('frozen-bytes', lambda: probe.verify_inputs(frozen), frozen)
    (frozen / probe.PRIOR_FILE).write_bytes(raw)
    # Drift the source after prepare, preserving the selected current bytes.
    changed = prior_root / 'subject.json'
    saved = changed.read_bytes()
    try:
        changed.write_bytes(saved + b' ')
        refused('origin-after-freeze', lambda: probe.run_probe(frozen, temporary_api_key='sk-fictional-official-only',
            _calibration=official._CALIBRATION), frozen)
    finally:
        changed.write_bytes(saved)
    expired = root / 'expired-new-run'
    fictional_operator(expired, 'expired', seed=8, prior_notes_root=notes, prior_note_key='global/main')
    auth = json.loads((expired / 'authorization.json').read_bytes())
    auth['expires_at'] = auth['valid_from']
    durable(expired / 'authorization.json', auth)
    manifest = json.loads((expired / 'manifest.json').read_bytes())
    manifest['records']['authorization.json'] = probe._hash((expired / 'authorization.json').read_bytes())
    durable(expired / 'manifest.json', manifest)
    refused('expired-new-authorization', lambda: probe.run_probe(expired, temporary_api_key='sk-fictional-official-only',
        _calibration=official._CALIBRATION), expired)
    assert tree_hashes(prior_root) == original
    return {'case': 'prior-reject', 'status': 'pass', 'refusals': checks, 'reasons': reasons, 'requests': 0, 'setup_mock_requests': 2,
            'historical_code_subset_accepted': True, 'prior_bytes_unchanged': True}


def run_transfer_case(root, case, prior_root):
    before = tree_hashes(prior_root)
    if case == 'prior-reject':
        return transfer_refusals(root, prior_root)
    fictional_operator(root, case, seed=8, prior_notes_root=prior_root / 'notes', prior_note_key='global/main')
    original_act = probe.Workspace.act
    cancel = [False]
    def act(world, action, operation, arguments):
        result = original_act(world, action, operation, arguments)
        if case == 'prior-cancel': cancel[0] = True
        return result
    def response(client, body):
        value = fixture_response(client, body, case=case)
        if case == 'prior-budget' and client.attempts == 1:
            value['usage'] = {'prompt_tokens': 1000000, 'completion_tokens': 20, 'total_tokens': 1000020}
        if case == 'prior-usage': value['usage'] = None
        if case == 'prior-drift' and client.attempts == 1:
            current = prior_root / 'notes/current/global/main.md'
            current.write_bytes(current.read_bytes() + b'FIXTURE_CONCURRENT_EDIT')
        return value
    current = prior_root / 'notes/current/global/main.md'
    saved = current.read_bytes()
    try:
        with patch.object(capacity.CapacityClient, '_fixture_response', response), patch.object(probe.Workspace, 'act', act), \
             patch.object(probe.NoteWriter, 'create', side_effect=AssertionError('prior mode created writer')):
            report = probe.run_probe(root, temporary_api_key='sk-fictional-official-only',
                _calibration=official._CALIBRATION, cancelled=lambda: cancel[0])
    finally:
        if current.read_bytes() != saved: current.write_bytes(saved)
    assert tree_hashes(prior_root) == before
    assert not (root / 'notes').exists()
    assert report['ledger']['closed'] and all(not v['sent'] and not v['transport_attempted'] for v in report['transport'])
    wire = [json.loads(p.read_bytes()) for p in sorted(root.glob('request-*.json'))]
    assert len(wire) == len(report['ledger']['requests'])
    for request in wire:
        body = request['envelope']['body']
        names = {tool['function']['name'] for tool in body['tools']}
        assert set(probe.TOOLS) <= names and not {'read_note', 'revise_note'} & names
        assert 'read-only history' in body['messages'][0]['content']
        assert 'not a current observation' in body['messages'][0]['content']
        selected = json.loads(body['messages'][1]['content'])['selected']
        assert len(selected['materials']) == 1
        material = selected['materials'][0]
        assert material['text'].encode() == saved and material['sha256'] == probe._hash(saved)
        assert material['version_id'] == probe._decode(saved)[0]['version_id']
        assert material['snapshot_id'] == probe._decode(saved)[0]['snapshot_id']
        assert all(item['request_id'].startswith('fixture-subject-' + case + '/') for item in selected['committed_actions'])
    assert not json.loads(wire[0]['envelope']['body']['messages'][1]['content'])['selected']['committed_actions']
    state = json.loads((root / 'world.json').read_bytes())['value']
    journal = loop.recover_commits(root)
    success = case in ('prior-active', 'prior-quiet')
    assert report['status'] == ('complete' if success else 'stopped'), report['failure']
    if case == 'prior-active':
        assert [item['source'] for item in state['deliveries']] == ['bootstrap', 'harness-clock', 'model-revisit']
        assert state['objects']['circle'] == 'left' and state['clocks'][0]['tilted'] == 'right'
        assert all(item['status'] == 'completed' for item in journal['results'].values())
        assert any(json.loads(message['content']).get('world_result', {}).get('objects', {}).get('circle') == 'left'
                   for request in wire[1:] for message in request['envelope']['body']['messages'] if message['role'] == 'tool')
    elif case == 'prior-quiet':
        assert len(wire) == 2 and not state['effects'] and not state['revisits']
    else:
        assert len(wire) == 1 and len(state['deliveries']) == 1
        assert len(state['effects']) == (1 if case == 'prior-cancel' else 3 if case == 'prior-budget' else 0)
        if case == 'prior-usage': assert report['ledger']['requests'][0]['usage'] is None
        previous = tree_hashes(root)
        try: probe.run_probe(root, temporary_api_key='sk-fictional-official-only', _calibration=official._CALIBRATION)
        except (RuntimeError, ValueError, OSError): pass
        else: raise AssertionError('prior failed run replayed')
        assert tree_hashes(root) == previous
    hashes = tree_hashes(root)
    with patch.object(official.OfficialClient, '__init__', side_effect=AssertionError('recover client')), \
         patch.object(probe.Workspace, 'act', side_effect=AssertionError('recover effect')):
        recovered = probe.recover(root)
    assert tree_hashes(root) == hashes and recovered['resume'] is False
    assert recovered['notes']['status'] == 'disabled-read-only-prior-note'
    durable(root / 'prior-integrity.json', {'before': before, 'after': tree_hashes(prior_root), 'unchanged': True})
    return {'case': case, 'status': 'pass', 'requests': len(wire), 'prior_bytes_unchanged': True,
            'wakes': len(state['deliveries']), 'replay': False, 'local_notes': False}


def doctor(root, cases=CASES):
    started, results = time.perf_counter(), []
    with isolation(root) as counters, redirect_stdout(io.StringIO()) as console:
        prior_root = root / 'prior-run'
        if any(case in TRANSFER_CASES for case in cases):
            assert run_case(prior_root, 'active')['status'] == 'complete'
        for case in cases:
            folder = root / case
            if case in TRANSFER_CASES:
                if case == 'prior-reject': folder.mkdir(mode=0o700)
                results.append(run_transfer_case(folder, case, prior_root))
                continue
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
            assert args.case not in TRANSFER_CASES, 'prior cases use doctor with a constructed stopped origin'
            with isolation(root.parent), redirect_stdout(io.StringIO()): result = run_case(root, args.case or 'active')
        print(json.dumps({'status': result['status'], 'assertions': len(result.get('assertions', []))}))
        return 0 if result['status'] in ('ok', 'complete') else 1
    except (RuntimeError, ValueError, OSError, KeyError, AssertionError) as error:
        print('subject calibration: ' + type(error).__name__ + ': ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
