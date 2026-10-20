"""Closed synthetic note-prefix/commit calibration through T10's actual SDK.

Candidate Markdown is supplied by fixtures. Main/background labels describe the
producing process; both use the same parent, exposure and persistence checks.
No production lifecycle, real HTTP, automatic retry or model quality claim.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
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
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from a0_ledger import LIMITS, canonical, durable
from comparison_snapshot import _safe_root, open_snapshot
from stateless_calibration import ClosedClient, synthetic_fixture, sentinels
from stateless_selector_calibration import SelectorClient
from stateless_selector import build_selector_input, select_materials, resolve_selection, record_consumption
from stateless_loop import drive_wake, finish_wake, recover_commits
from stateless_notes import NoteWriter, snapshot_notes, read_committed, recover_notes, _decode, _read_json, _pack


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _reject(call, expected=None):
    try:
        call()
    except (ValueError, RuntimeError, OSError) as error:
        if expected:
            assert expected in str(error), str(error)
        return str(error)
    raise AssertionError('invalid operation accepted')


def _wire(material):
    return {k: v for k, v in material.items() if k != 'raw'} | {'text': material['raw'].decode()}


class NotesClient(SelectorClient):
    """One inherited SDK/guard; process attribution changes no write policy."""
    process_role = 'chat'

    def _before_http(self, request):
        if self.current.get('role') != 'selector':
            self.current['role'] = self.process_role
        return super()._before_http(request)


def doctor(root):
    started = time.perf_counter()
    assertions, faults = [], {}
    def passed(name, **details):
        assertions.append({'assertion': name, **details})
    frozen, schedule, wakes = synthetic_fixture()
    first, second = [open_snapshot(frozen, schedule, cursor) for cursor in (1, 2)]
    keys = ('global/main', 'person/101', 'group/alpha')
    # Larger *synthetic* run envelope, still one serial ClosedClient/Ledger guard.
    with patch.dict(LIMITS, wakes=40, attempts=100, allowances=409600), \
         sentinels(root) as counters, redirect_stdout(io.StringIO()) as console:
        notes = NoteWriter.create(root / 'notes')
        loop = root / 'loop'
        loop.mkdir()
        manifest = {'synthetic': True, 'schema': 'notes-calibration-v2', 'source': frozen['frozen_id']}
        durable(loop / 'manifest.json', manifest)
        client = NotesClient(loop, manifest, 'normal')
        client.ceiling.update(attempts=100, tokens=4000000, micro_cny=10000000)
        number = [0]
        def drive(snapshot, wake, materials=(), *, hook=None, terminal=True, references=(), actions=()):
            import stateless_loop
            original = stateless_loop.append_commit
            def commit(folder, kind, value, **kwargs):
                row = original(folder, kind, value, **kwargs)
                if hook:
                    hook(kind, value)
                return row
            client.ledger.wake()
            with patch.object(stateless_loop, 'append_commit', commit):
                outcome = drive_wake(snapshot, wake, [wake], loop, client,
                                     materials=materials, references=references, actions=actions)
            requests = [k for k, v in client.saved.items() if v['attribution']['task_id'] == wake['id']]
            if terminal:
                finish_wake(loop, wake['id'], 'complete', requests)
            return outcome
        def baselines(snapshot, writer=notes):
            return tuple(writer.freeze_baseline(k, snapshot, allowed_keys=keys) for k in keys)
        def admit(wake, snapshot, *, output=None, aliases=None, writer=notes):
            registered = writer.register_trigger(loop, wake['id'], snapshot, allowed_keys=keys)
            return writer.admit_trigger(registered['id'], loop, snapshot, through_output=output, note_aliases=aliases)
        def candidate(task, body, *, process='main', key='global/main', writer=notes, sources=None, **kwargs):
            return writer.prepare_candidate(task['id'], key, body,
                sources=list(task['cutoff']['exposures']) if sources is None else sources,
                reason='explicit synthetic revision', expectation='exact bytes after explicit read',
                producer={'process': process, 'basis': 'fixture bytes based on frozen input',
                          'output_id': task['cutoff']['output_id']}, **kwargs)
        def new_task(*, writer=notes, materials=None, aliases=None, role='chat', actions=()):
            number[0] += 1
            wake = {**wakes[1], 'id': 'notes-round-' + str(number[0])}
            # Explicit alpha reference supplies stable person/group provenance.
            reference = first['frozen_sources']['events'][0]
            ref = {'snapshot_id': second['snapshot_id'], 'source_alias': reference['source_alias'],
                   'event_key': reference['event_key']}
            client.process_role = role
            try:
                drive(second, wake, baselines(second, writer) if materials is None else materials,
                      references=(ref,), actions=actions)
            finally:
                client.process_role = 'chat'
            return admit(wake, second, aliases=aliases, writer=writer)
        state = {}
        try:
            initial = baselines(first)
            def live(kind, value):
                if kind != 'output':
                    if kind == 'result':
                        old = admit(wakes[0], first, output=state['early']['cutoff']['output_id'])
                        assert old['id'] == state['early']['id'] and old['cutoff'] == state['early']['cutoff']
                    return
                task = admit(wakes[0], first, output=value['id'])
                assert not recover_commits(loop)['terminals']
                if 'early' not in state:
                    state['early'] = task
                    registered = notes.register_trigger(loop, wakes[0]['id'], first, allowed_keys=keys)
                    assert notes.admit_trigger(registered['id'], loop, first)['status'] == 'pending'
                    a = candidate(task, b'# A\r\nOLD_NOTE_A  \n')
                    c = candidate(task, b'UNCOMMITTED_LATE_C\n', process='background')
                    state.update(a=a, c=c)
                    reverse = root / 'reverse-writers'
                    shutil.copytree(notes.root, reverse)
                    reversed_writer = NoteWriter(reverse)
                    assert reversed_writer.commit_candidate(c)['status'] == 'committed'
                    assert reversed_writer.commit_candidate(a)['status'] == 'stale'
                    state['a_result'] = notes.commit_candidate(a)
                    assert state['a_result']['status'] == 'committed'
                    assert notes.commit_candidate(c)['status'] == 'stale'
                    state['frozen_a'] = snapshot_notes(notes.root, first, allowed_keys=keys)
                    # Explicit resolver read on the next native request. This is
                    # calibration wiring, not T13's real revision/read tools.
                    supplied = [False]
                    def reread():
                        if supplied[0]:
                            return []
                        supplied[0] = True
                        return [{'role': 'user', 'content': canonical({'note_material':
                            _wire(notes.freeze_baseline('global/main', first, allowed_keys=keys))}).decode()}]
                    client.chats[-1].add_context_provider(reread)
                    generated = [k for k, v in task['cutoff']['exposures'].items() if v['kind'] == 'generated']
                    _reject(lambda: candidate(task, b'false success', sources=generated,
                        statements=[{'kind': 'success', 'text': 'tool succeeded', 'sources': generated}]), 'completed execution')
                    state['intent'] = candidate(task, b'intent only', sources=generated,
                        statements=[{'kind': 'draft', 'text': 'intended action', 'sources': generated}])
                else:
                    state['late'] = task
                    assert task['cutoff']['generation'] == state['early']['cutoff']['generation']
                    assert task['cutoff']['sequence'] > state['early']['cutoff']['sequence']
                    state['b'] = candidate(task, b'# Corrected\r\nCOMMITTED_B  \n', process='background')
                    shutil.copytree(notes.root, root / 'fault-baseline')
                    state['b_result'] = notes.commit_candidate(state['b'])
                    assert state['b_result']['status'] == 'committed'
                    state['frozen_b'] = snapshot_notes(notes.root, first, allowed_keys=keys)
            drive(first, wakes[0], initial, hook=live, terminal=False)
            early, late = state['early'], state['late']
            assert notes.commit_candidate(state['c'])['status'] in {'stale', 'rejected'}
            assert notes.commit_candidate(state['b'])['status'] == 'no-op'
            _reject(lambda: candidate(early, b'old view new parent', parent={
                'version_id': state['b_result']['version_id'], 'sha256': state['b_result']['sha256']}), 'differs')
            _reject(lambda: candidate(early, b'old view', process='background'), 'older wake cutoff')
            assert b'OLD_NOTE_A' in state['frozen_a'][0]['raw'] and b'COMMITTED_B' in state['frozen_b'][0]['raw']
            finish_wake(loop, wakes[0]['id'], 'complete', list(client.saved))
            assert admit(wakes[0], first, output=early['cutoff']['output_id'])['cutoff'] == early['cutoff']
            assert not any(v.get('status') == 'completed' for v in early['cutoff']['exposures'].values())
            assert any(v.get('visibility') == 'actual-request' for v in late['cutoff']['exposures'].values())
            passed('nonterminal-main-background-shared-CAS-both-orders-and-same-wake-prefix-parent-refusal')
            passed('generated-intent-not-success-host-results-not-backfilled-old-cutoff-immutable')
            # Real selector + opaque note handle -> chat SDK wire. Parent binding
            # resolves the frozen alias, never treats the catalog title as text.
            material = read_committed(notes.root, 'global/main', second, allowed_keys=keys)
            alias = 'opaque-note-handle'
            exposed = {**material, 'source_id': alias}
            wake = {**wakes[1], 'id': 'mixed-selector-chat'}
            entry = {'entry_id': 'note-v1', 'kind': 'note', 'title': 'fictional committed note',
                'scope': 'global', 'branch_id': 'main', 'version_id': material['version_id'],
                'source_id': alias, 'committed_cursor': 1, 'expires_cursor': 3, 'sha256': material['sha256'],
                'ref': {'source_id': alias, 'version_id': material['version_id'], 'snapshot_id': second['snapshot_id']}}
            resolver_calls = []
            def resolver(_):
                resolver_calls.append(alias)
                return {k: v for k, v in exposed.items() if k != 'kind'}
            context = build_selector_input(second, wake, 'fictional hint', [entry], scopes=('global',))
            receipt = {}
            client.ledger.wake()
            selection = select_materials(context, second, client, resolver, receipt, loop / 'selector.json')
            bundle = resolve_selection(context, selection, second, resolver)
            before = len(client.exchanges)
            # drive increments the shared synthetic wake counter; no extra role budget.
            drive(second, wake, tuple(bundle['materials']))
            actual = client.saved[client.exchanges[before]['request_id']]
            assert record_consumption(receipt, bundle, actual)[0]['status'] == 'injected'
            mixed = admit(wake, second, aliases={alias: 'global/main'})
            assert set(mixed['cutoff']['request_roles'].values()) == {'selector', 'chat'}
            assert mixed['cutoff']['seen_notes']['global/main']['version_id'] == material['version_id']
            _reject(lambda: admit(wake, second, aliases={alias: 'person/101'}), 'illegal exposed note')
            _reject(lambda: admit(wake, second, aliases={alias: 'person/unauthorized'}), 'unauthorized')
            assert b'COMMITTED_B' in canonical(actual) and b'UNCOMMITTED_' not in canonical(actual)
            durable(root / 'mixed-consumption.json', {'receipt': receipt, 'cutoff': mixed['cutoff'],
                                                       'resolver_calls': resolver_calls})
            held = {**wakes[1], 'id': 'held-request-A'}
            drive(second, held, tuple({**v, 'snapshot_id': second['snapshot_id']} for v in state['frozen_a']))
            held_body = client.exchanges[-2]['body']
            assert json.loads(held_body['messages'][1]['content'])['selected']['materials'][0]['text'].encode() == state['frozen_a'][0]['raw']
            passed('mixed-chat-selector-real-SDK-opaque-alias-mapping-and-next-wake-exact-B-held-A-no-candidate-leak')
            # Emulate the future note-read result using T10's existing tool and
            # action journal, without introducing another chat execution loop.
            import stateless_loop
            original_canonical = stateless_loop.canonical
            read_value = read_committed(notes.root, 'global/main', second, allowed_keys=keys)
            def note_result(value):
                if isinstance(value, dict) and set(value) == {'ref', 'origin', 'sha256', 'raw'}:
                    return original_canonical({'note_material': _wire(read_value)})
                return original_canonical(value)
            with patch.object(stateless_loop, 'canonical', note_result):
                read_task = new_task()
            read_output = next(v['value']['id'] for v in recover_commits(loop)['records']
                               if v['kind'] == 'output' and v['value']['wake_id'] == read_task['wake_id'])
            # No top-level note or baseline: the background request sees the
            # parent only inside an exact committed action's note-read result.
            task = new_task(role='librarian', materials=(), actions=(read_output,))
            assert task['cutoff']['seen_notes']['global/main']['version_id'] == read_value['version_id']
            durable(root / 'background-action-note-read.json', {'task': task,
                'source_output': read_output, 'source_material': _wire(read_value)})
            passed('committed-actions-note-material-parent-actually-exposed-to-background-request')
            # Every later proposal has a newly exposed exact baseline.
            assert task['cutoff']['generator_role'] == 'librarian'
            assert any(v.get('visibility') == 'actual-request' and v.get('status') == 'completed'
                       for v in task['cutoff']['exposures'].values())
            passed('independent-background-request-role-and-confirmed-action-exposure-share-same-producer-policy')
            pending = candidate(task, b'UNCOMMITTED_CANDIDATE_SECRET\n')
            deleted = notes.commit_candidate(candidate(task, b'', operation='delete', process='background'))
            assert deleted['status'] == 'committed' and snapshot_notes(notes.root, second, allowed_keys=keys) == ()
            assert notes.commit_candidate(pending)['status'] == 'stale'
            tomb_raw = (notes.root / 'current/global/main.md').read_bytes()
            tomb, _ = _decode(tomb_raw)
            task = new_task()
            restored = notes.commit_candidate(candidate(task, b'# Corrected\r\nCOMMITTED_B  \n',
                operation='restore', restore_version=state['b_result']['version_id']))
            assert restored['status'] == 'committed'
            task = new_task()
            rolled = notes.commit_candidate(candidate(task, b'# A\r\nOLD_NOTE_A  \n',
                operation='rollback', restore_version=state['a_result']['version_id']))
            assert rolled['status'] == 'committed'
            chain, version = [], rolled['version_id']
            while version:
                meta, _ = _decode((notes.root / 'versions' / (version + '.md')).read_bytes())
                chain.append(version)
                version = meta['parent_version']
            assert len(chain) == 5 and tomb['version_id'] in chain and restored['version_id'] in chain
            passed('tombstone-not-injected-explicit-restore-rollback-add-versions-retain-history', chain=chain)
            task = new_task()
            person = candidate(task, b'PERSON_101\n', key='person/101')
            assert notes.commit_candidate(person)['status'] == 'committed'
            assert read_committed(notes.root, 'person/101', second, allowed_keys=['person/101'])
            _reject(lambda: read_committed(notes.root, 'person/101', second, allowed_keys=['group/beta']), 'scope')
            _reject(lambda: candidate(task, b'wrong', key='../outside'))
            _reject(lambda: candidate(task, b'wrong', sources=['unexposed-future']), 'future')
            _reject(lambda: candidate(task, b'wrong', branch='foreign'), 'foreign')
            _reject(lambda: read_committed(notes.root, 'global/main', first, allowed_keys=keys), 'future')
            passed('scope-stable-person-path-branch-unexposed-future-refusals')
            latest = candidate(task, b'queued before human edit\n')
            path = notes.root / 'current/global/main.md'
            path.write_bytes(path.read_bytes().partition(b'\n')[0] + b'\n# HUMAN_EDIT\r\n exact  \n')
            assert notes.commit_candidate(latest)['status'] == 'rejected'
            _reject(lambda: snapshot_notes(notes.root, second, allowed_keys=keys), 'paused')
            pause = _read_json(notes.root / 'pauses/global/main.json')
            _reject(lambda: notes.rebaseline('global/main', expected_observation=pause['observed_sha256'],
                parent_version=state['a_result']['version_id'], trigger_id=task['id'], sources=list(task['cutoff']['exposures']),
                reason='bad pruning', expectation='reject'), 'parent')
            assert notes.rebaseline('global/main', expected_observation=pause['observed_sha256'],
                parent_version=rolled['version_id'], trigger_id=task['id'], sources=list(task['cutoff']['exposures']),
                reason='explicit human correction', expectation='keep human bytes')['status'] == 'committed'
            assert b'HUMAN_EDIT' in read_committed(notes.root, 'global/main', second, allowed_keys=keys)['raw']
            assert notes.commit_candidate(latest)['status'] == 'stale'
            for status in ('failed', 'not-admitted', 'no-op'):
                notes.finish_trigger(task['id'], status, 'synthetic ' + status)
            passed('manual-edit-pauses-explicit-baseline-preserves-history-and-trigger-outcomes-without-auto-request')
        finally:
            client.close()
        # Durable note failure matrix uses the same ready B proposal and A base.
        for fault in ('previous', 'audit-before', 'audit-partial', 'audit-flush', 'audit-fsync', 'audit-replace',
                      'audit-after-replace', 'audit-dir-fsync', 'current-before', 'current-partial', 'current-flush',
                      'current-fsync', 'current-replace', 'current-after-replace', 'current-dir-fsync',
                      'receipt-before', 'receipt-partial', 'receipt-flush', 'receipt-fsync', 'receipt-replace',
                      'receipt-after-replace', 'receipt-dir-fsync'):
            folder = root / ('fault-' + fault)
            shutil.copytree(root / 'fault-baseline', folder)
            writer = NoteWriter(folder)
            result = writer.commit_candidate(state['b'], fault=fault)
            assert result['status'] == 'failed' and writer.poisoned
            before = {str(p.relative_to(folder)): _hash(p.read_bytes()) for p in folder.rglob('*') if p.is_file()}
            def forbidden(*_, **__):
                raise AssertionError('read-only recovery wrote/constructed client')
            with patch.object(Path, 'write_bytes', forbidden), patch.object(Path, 'write_text', forbidden), \
                 patch.object(ClosedClient, '__init__', forbidden):
                recovered = recover_notes(folder)
            after = {str(p.relative_to(folder)): _hash(p.read_bytes()) for p in folder.rglob('*') if p.is_file()}
            assert before == after and not recovered['rejected'] and recovered['persistence_unknown']
            assert recovered['notes']['global/main']['version_id'] in {state['a_result']['version_id'], state['b_result']['version_id']}
            _reject(lambda: writer.commit_candidate(state['b']), 'closed')
            _reject(lambda: NoteWriter(folder).commit_candidate(state['b']), 'closed')
            faults[fault] = {'result': result, 'recovery': recovered, 'all_bytes_unchanged': True}
        passed('22-note-IO-breakpoints-old-or-new-readonly-recovery-reopened-writer-closed')
        original_candidate = _read_json(root / 'fault-baseline/candidates' / (state['b'] + '.json'))
        for name, changes in (
                ('restore', {'operation': 'restore', 'restore_version': 'nonexistent'}),
                ('delete-body', {'operation': 'delete'}), ('operation', {'operation': 'unrecognized'}),
                ('generation', {'generation': 999}), ('prefix-sequence', {'cutoff_sequence': 999}),
                ('frozen-source', {'source_frozen_id': 'foreign'}), ('scope', {'key': 'person/202'}),
                ('future-source', {'sources': ['unexposed-future']})):
            folder = root / ('tamper-' + name)
            shutil.copytree(root / 'fault-baseline', folder)
            forged = {**deepcopy(original_candidate), **changes}
            identity = _hash(canonical(forged))
            (folder / 'candidates' / (identity + '.json')).write_bytes(_pack(forged))
            assert NoteWriter(folder).commit_candidate(identity)['status'] == 'rejected', name
        passed('recomputed-candidate-hashes-cannot-bypass-source-scope-prefix-operation-policy')
        for mode in ('missing', 'rewind', 'symlink', 'cooperative', 'race'):
            folder = root / ('external-' + mode)
            shutil.copytree(root / 'fault-baseline', folder)
            writer = NoteWriter(folder)
            path = folder / 'current/global/main.md'
            if mode == 'missing':
                path.unlink()
                assert writer.commit_candidate(state['b'])['status'] == 'rejected'
            elif mode == 'rewind':
                assert writer.commit_candidate(state['b'])['status'] == 'committed'
                path.write_bytes(state['frozen_a'][0]['raw'])
                assert writer.commit_candidate(state['b'])['status'] == 'rejected'
            elif mode == 'symlink':
                path.unlink()
                path.symlink_to(notes.root / 'current/global/main.md')
                _reject(lambda: snapshot_notes(folder, first, allowed_keys=keys), 'symlink')
            elif mode == 'cooperative':
                writer.pause_note('global/main', reason='explicit pre-edit pause')
                assert writer.commit_candidate(state['b'])['status'] == 'rejected'
            else:
                import stateless_notes
                replace = stateless_notes.os.replace
                def race(source, target):
                    if str(target).endswith('current/global/main.md'):
                        Path(target).write_bytes(b'NONCOOPERATING_EDITOR_WRITE')
                    return replace(source, target)
                with patch.object(stateless_notes.os, 'replace', race):
                    assert writer.commit_candidate(state['b'])['status'] == 'committed'
                assert b'NONCOOPERATING_EDITOR_WRITE' not in path.read_bytes()
            passed('external-' + mode + ('-outside-cooperative-guarantee' if mode == 'race' else '-refused'))
        # Source-domain failures cannot be hidden by an older valid cutoff.
        for marker in ('persistence-unknown.json', 'ledger.json.new', 'commit-persistence-unknown.json'):
            path = loop / marker
            path.write_bytes(b'fictional fault')
            try:
                result = NoteWriter(root / 'fault-baseline').commit_candidate(state['b'])
                assert result['status'] == 'rejected', (marker, result)
            finally:
                path.unlink()
        ledger_path = loop / 'ledger.json'
        ledger_raw = ledger_path.read_bytes()
        ledger_path.unlink()
        try:
            assert NoteWriter(root / 'fault-baseline').commit_candidate(state['b'])['status'] == 'rejected'
        finally:
            ledger_path.write_bytes(ledger_raw)
        passed('source-Ledger-and-commit-persistence-unknown-or-missing-close-writing')
        # Cancel/failure facts remain exact in the T10 journal; no retry request.
        for scenario, expected in (('cancel-before-tool', {'not-dispatched'}),
                                   ('result-failure', {'unknown', 'not-dispatched'}),
                                   ('future-tool', {'failed', 'not-dispatched'})):
            folder = root / (scenario + '-loop')
            folder.mkdir()
            other = ClosedClient(folder, manifest, scenario)
            writer = NoteWriter.create(root / (scenario + '-notes'))
            if scenario == 'future-tool':
                future = next(e for e in frozen['events'] if e['event_key'] not in first['visible_event_keys'])
                other.future_reference = {'source_alias': future['source_alias'], 'event_key': future['event_key']}
            try:
                other.ledger.wake()
                # T13 now closes the shared session immediately on failure,
                # rather than waiting for its caller to append a terminal.
                # Capture the legal pre-failure candidate at output persistence.
                ready = {}
                original_append = stateless_loop.append_commit
                def capture(folder_arg, kind, value, **kwargs):
                    row = original_append(folder_arg, kind, value, **kwargs)
                    if kind == 'output':
                        registered = writer.register_trigger(folder, wakes[0]['id'], first, allowed_keys=keys)
                        task = writer.admit_trigger(registered['id'], folder, first, through_output=value['id'])
                        ready['candidate'] = candidate(task, b'before failure', writer=writer)
                    return row
                try:
                    with patch.object(stateless_loop, 'append_commit', capture):
                        outcome = drive_wake(first, wakes[0], [wakes[0]], folder, other, scenario=scenario,
                            materials=(writer.freeze_baseline('global/main', first, allowed_keys=keys),))
                    status = 'cancelled' if outcome['cancelled'] else 'complete'
                except (ValueError, RuntimeError, OSError):
                    status = 'failed'
                journal = recover_commits(folder)
                assert set(journal['actions'].values()) == expected
                registered = writer.register_trigger(folder, wakes[0]['id'], first, allowed_keys=keys)
                task = writer.admit_trigger(registered['id'], folder, first, through_output=next(iter(journal['outputs'])))
                ready = ready['candidate']
                finish_wake(folder, wakes[0]['id'], status, list(other.saved))
                assert writer.commit_candidate(ready)['status'] == 'rejected'
                _reject(lambda: candidate(task, b'after failure', writer=writer), 'writes closed')
                assert other.attempts == 1
                passed('cancel-failed-unknown-facts-preserved-and-old-candidates-closed-' + scenario, statuses=sorted(expected))
            finally:
                other.close()
        assert all(v == 0 for v in counters.values())
    durable(root / 'parser-console.json', {'synthetic_console': console.getvalue()})
    result = {'schema': 'stateless-notes-doctor-v2', 'status': 'ok', 'assertions': assertions, 'faults': faults,
        'sentinels': counters, 'real_HTTP': 0, 'QQ': 0, 'elapsed_seconds': time.perf_counter() - started,
        'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'source_hashes': {name: _hash((Path(__file__).parent / name).read_bytes()) for name in (
            'stateless_notes.py', 'stateless_notes_calibration.py', 'stateless_loop.py', 'stateless_calibration.py',
            'stateless_selector.py', 'stateless_selector_calibration.py')},
        'limitations': ['fixture candidate bytes; no real generation, semantic learning or subjectivity proof',
            'T12 explicit reread uses existing Chat context provider; real note tools and orchestration belong to T13',
            'ordinary-group synthetic archive, no private-ingestion proof',
            'cooperative lock; noncooperative editor TOCTOU reproduced',
            'fault injection, not physical power-loss/all-filesystem proof; no atomic multi-file snapshot']}
    durable(root / 'doctor.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('doctor', 'recover'))
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'recover':
            result = recover_notes(args.output)
        else:
            root = _safe_root(args.output)
            root.mkdir(parents=True, mode=0o700)
            result = doctor(root)
        print(json.dumps({'status': result.get('status'), 'assertions': len(result.get('assertions', [])),
                         'result': result if args.command == 'recover' else None}))
        return 0
    except (ValueError, RuntimeError, OSError, AssertionError, KeyError) as error:
        print(f'notes calibration: {type(error).__name__}: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
