"""Synthetic Markdown commit/recovery calibration through T10's actual SDK wire.

Supplied candidates below are fixtures, not librarian learning results. No model
candidate generator, endpoint override, automatic retry or production entry.
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
from a0_ledger import canonical, durable
from comparison_snapshot import _safe_root, open_snapshot
from stateless_calibration import ClosedClient, synthetic_fixture, sentinels
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


def doctor(root):
    started = time.perf_counter()
    assertions, faults = [], {}
    def passed(name, **details):
        assertions.append({'assertion': name, **details})
    frozen, schedule, wakes = synthetic_fixture()
    first, second = [open_snapshot(frozen, schedule, cursor) for cursor in (1, 2)]
    keys = ('global/main', 'person/101', 'group/alpha')
    with sentinels(root) as counters, redirect_stdout(io.StringIO()) as console:
        notes = NoteWriter.create(root / 'notes')
        loop = root / 'loop'
        loop.mkdir()
        manifest = {'synthetic': True, 'schema': 'notes-calibration-v1', 'source': frozen['frozen_id']}
        durable(loop / 'manifest.json', manifest)
        client = ClosedClient(loop, manifest, 'normal')
        def drive(snapshot, wake, materials=(), *, scenario='normal', terminal=True):
            client.ledger.wake()
            outcome = drive_wake(snapshot, wake, [wake], loop, client, materials=materials, scenario=scenario)
            request_ids = [key for key, value in client.saved.items() if value['attribution']['task_id'] == wake['id']]
            if terminal:
                finish_wake(loop, wake['id'], 'cancelled' if outcome['cancelled'] else 'complete', request_ids)
            return outcome
        try:
            drive(first, wakes[0], terminal=False)
            trigger = notes.register_trigger(loop, wakes[0]['id'], first, allowed_keys=keys)
            assert notes.admit_trigger(trigger['id'], loop, first)['status'] == 'pending'
            _reject(lambda: notes.prepare_candidate(trigger['id'], 'global/main', b'premature', sources=['x'],
                                                    reason='fixture', expectation='must reject'), 'incomplete')
            count = len(client.exchanges)
            finish_wake(loop, wakes[0]['id'], 'complete', list(client.saved))
            trigger = notes.admit_trigger(trigger['id'], loop, first)
            assert len(client.exchanges) == count
            assert notes.register_trigger(loop, wakes[0]['id'], first, allowed_keys=keys)['id'] == trigger['id']
            first_trigger = deepcopy(trigger)
            exposures = trigger['cutoff']['exposures']
            record_sources = [key for key, value in exposures.items() if value['kind'] == 'record']
            action_sources = [key for key, value in exposures.items() if value['kind'] == 'execution']
            generated_sources = [key for key, value in exposures.items() if value['kind'] == 'generated']
            assert record_sources and action_sources and generated_sources
            passed('registration-before-terminal-does-not-authorize-processing; actual-cutoff-without-extra-request')
            def candidate(body, *, key='global/main', operation='write', restore_version=None, writer=notes,
                          task=trigger, sources=record_sources, **kwargs):
                return writer.prepare_candidate(task['id'], key, body, operation=operation, sources=sources,
                     reason='explicit synthetic correction/recovery', expectation='exact bytes next wake',
                     restore_version=restore_version, **kwargs)
            a = candidate(b'# A\r\nOLD_NOTE_A  \n')
            assert candidate(b'# A\r\nOLD_NOTE_A  \n') == a
            a_result = notes.commit_candidate(a)
            assert a_result['status'] == 'committed'
            assert notes.commit_candidate(a)['status'] == 'no-op'
            with notes._locked():
                _reject(lambda: NoteWriter(notes.root).commit_candidate(a))
            assert not notes.poisoned
            passed('cooperative-competing-writer-refused-without-mutation')
            frozen_a = snapshot_notes(notes.root, second, allowed_keys=keys)
            assert len(frozen_a) == 1
            old_candidate = candidate(b'UNCOMMITTED_LATE_C\n')
            b = candidate(b'# Corrected\r\nCOMMITTED_B  \n')
            # Fault copies start with A and a ready B candidate.
            shutil.copytree(notes.root, root / 'fault-baseline')
            b_result = notes.commit_candidate(b)
            assert b_result['status'] == 'committed'
            notes.finish_trigger(trigger['id'], 'success', 'synthetic fixture candidates committed; no model generation')
            assert notes.commit_candidate(old_candidate)['status'] == 'stale'
            frozen_b = snapshot_notes(notes.root, second, allowed_keys=keys)
            assert b'COMMITTED_B' in frozen_b[0]['raw'] and b'OLD_NOTE_A' in frozen_a[0]['raw']
            assert notes.commit_candidate(b)['status'] == 'no-op'
            pending = candidate(b'UNCOMMITTED_CANDIDATE_SECRET\n')
            drive(second, wakes[1], frozen_b)
            second_task = notes.register_trigger(loop, wakes[1]['id'], second, allowed_keys=keys)
            second_task = notes.admit_trigger(second_task['id'], loop, second)
            notes.finish_trigger(second_task['id'], 'no-op', 'fixture: no note change')
            # The request already assembled from A keeps A even after B commits.
            held_wake = {**wakes[1], 'id': 'held-request-alpha'}
            held_a = tuple({**value, 'snapshot_id': second['snapshot_id']} for value in frozen_a)
            drive(second, held_wake, held_a)
            wire_b = json.loads((loop / 'request-0003.json').read_bytes())['envelope']['body']
            wire_a = json.loads((loop / 'request-0005.json').read_bytes())['envelope']['body']
            material_b = json.loads(wire_b['messages'][1]['content'])['selected']['materials']
            material_a = json.loads(wire_a['messages'][1]['content'])['selected']['materials']
            assert material_b[0]['text'].encode() == frozen_b[0]['raw']
            assert material_a[0]['text'].encode() == frozen_a[0]['raw']
            assert material_b[0]['sha256'] == _hash(frozen_b[0]['raw'])
            assert 'UNCOMMITTED_' not in canonical(wire_b).decode()
            passed('actual-T10-drive-wake-SDK-wire-exact-committed-B-and-held-A-excludes-candidates', requests=len(client.exchanges))
            # Newer terminal first: delayed old task must not borrow the new parent.
            second_sources = list(second_task['cutoff']['exposures'])
            newer = candidate(b'NEWER_CUTOFF\n', task=second_task, sources=second_sources)
            assert notes.commit_candidate(newer)['status'] == 'committed'
            _reject(lambda: candidate(b'LATE_OLD_WAKE\n'), 'older wake cutoff')
            passed('late-old-wake-cannot-rebase-on-newer-source-cutoff')
            # Keep subsequent lifecycle checks on the latest task.
            trigger, record_sources = second_task, second_sources
            delete = candidate(b'', operation='delete', task=trigger, sources=record_sources)
            assert notes.commit_candidate(delete)['status'] == 'committed'
            assert snapshot_notes(notes.root, second, allowed_keys=keys) == ()
            deletion_loop = root / 'independent-deletion-loop'
            deletion_loop.mkdir()
            deletion_manifest = {**manifest, 'scenario': 'independent tombstone consumption diagnostic'}
            durable(deletion_loop / 'manifest.json', deletion_manifest)
            deletion_client = ClosedClient(deletion_loop, deletion_manifest, 'normal')
            try:
                deletion_client.ledger.wake()
                deletion_wake = {**wakes[1], 'id': 'deleted-note-read'}
                drive_wake(second, deletion_wake, [deletion_wake], deletion_loop, deletion_client,
                           materials=snapshot_notes(notes.root, second, allowed_keys=keys))
                finish_wake(deletion_loop, deletion_wake['id'], 'complete', list(deletion_client.saved))
                deleted_wire = json.loads((deletion_loop / 'request-0001.json').read_bytes())['envelope']['body']
                assert json.loads(deleted_wire['messages'][1]['content'])['selected']['materials'] == []
            finally:
                deletion_client.close()
            tombstone = (notes.root / 'current/global/main.md').read_bytes()
            tomb, _ = _decode(tombstone)
            assert tomb['operation'] == 'delete' and tomb['parent_version']
            restored = candidate(b'# Corrected\r\nCOMMITTED_B  \n', operation='restore',
                                 restore_version=b_result['version_id'], task=trigger, sources=record_sources)
            restore_result = notes.commit_candidate(restored)
            assert restore_result['status'] == 'committed' and restore_result['version_id'] != b_result['version_id']
            rollback = candidate(b'# A\r\nOLD_NOTE_A  \n', operation='rollback', restore_version=a_result['version_id'],
                                 task=trigger, sources=record_sources)
            rollback_result = notes.commit_candidate(rollback)
            assert rollback_result['status'] == 'committed'
            chain, version = [], rollback_result['version_id']
            while version:
                row, _ = _decode((notes.root / 'versions' / (version + '.md')).read_bytes())
                chain.append(version)
                version = row['parent_version']
            assert len(chain) == 6 and b_result['version_id'] in chain and tomb['version_id'] in chain
            passed('delete-not-injected-explicit-restore-and-rollback-add-revisions-retain-history', chain=chain)
            # Person identity is the stable account key, independent of a display
            # name/window. T07 itself cannot supply private archives.
            person = candidate(b'PERSON_101\n', key='person/101', task=first_trigger,
                               sources=[key for key, value in first_trigger['cutoff']['exposures'].items()
                                        if value.get('author_id') == '101'])
            assert notes.commit_candidate(person)['status'] == 'committed'
            person_material = read_committed(notes.root, 'person/101', second, allowed_keys=['person/101'])
            assert person_material['source_id'] == 'person/101'
            _reject(lambda: read_committed(notes.root, 'person/101', second, allowed_keys=['group/beta']), 'scope')
            _reject(lambda: candidate(b'wrong', key='person/202', task=second_task, sources=second_sources), 'scope')
            _reject(lambda: candidate(b'wrong', key='../outside', task=second_task, sources=second_sources))
            _reject(lambda: candidate(b'wrong', task=second_task, sources=['unopened-future']))
            _reject(lambda: candidate(b'wrong', task=second_task, sources=second_sources, branch='foreign'))
            _reject(lambda: read_committed(notes.root, 'global/main', first, allowed_keys=keys), 'future')
            second_generated = [key for key, value in second_task['cutoff']['exposures'].items() if value['kind'] == 'generated']
            _reject(lambda: candidate(b'false-success', task=second_task, sources=second_generated,
                                     statements=[{'kind': 'success', 'text': 'sent', 'sources': second_generated}]), 'completed execution')
            passed('stable-person-key-explicit-scope-path-branch-future-and-generated-success-refusals')
            # Actual outside edit, pause and a new baseline; old parent cannot
            # prune intervening history during recovery.
            latest_candidate = candidate(b'queued before human edit\n', task=trigger, sources=record_sources)
            path = notes.root / 'current/global/main.md'
            original = path.read_bytes()
            path.write_bytes(original.partition(b'\n')[0] + b'\n# HUMAN_EDIT\r\n  exact  \n')
            assert notes.commit_candidate(latest_candidate)['status'] == 'rejected'
            _reject(lambda: snapshot_notes(notes.root, second, allowed_keys=['global/main']), 'paused')
            pause = _read_json(notes.root / 'pauses/global/main.json')
            _reject(lambda: notes.rebaseline('global/main', expected_observation=pause['observed_sha256'],
                    parent_version=a_result['version_id'], trigger_id=trigger['id'], sources=record_sources,
                    reason='bad history pruning', expectation='reject'), 'parent')
            rebased = notes.rebaseline('global/main', expected_observation=pause['observed_sha256'],
                    parent_version=rollback_result['version_id'], trigger_id=trigger['id'], sources=record_sources,
                    reason='adopt explicit human correction', expectation='preserve human bytes')
            assert rebased['status'] == 'committed'
            assert b'HUMAN_EDIT' in read_committed(notes.root, 'global/main', second, allowed_keys=keys)['raw']
            assert notes.commit_candidate(latest_candidate)['status'] == 'stale'
            passed('observed-manual-edit-pauses-explicit-baseline-new-version-rejects-history-pruning')
            # Removing current manually is not a fresh empty note.
            missing_root = root / 'missing-current'
            shutil.copytree(root / 'fault-baseline', missing_root)
            missing_writer = NoteWriter(missing_root)
            (missing_root / 'current/global/main.md').unlink()
            first_task = _read_json(missing_root / 'triggers' / (list((missing_root / 'triggers').glob('*.json'))[0].stem + '.json'))
            _reject(lambda: candidate(b'new root', writer=missing_writer, task=first_task,
                                      sources=list(first_task['cutoff']['exposures'])), 'external edit')
            assert list((missing_root / 'pauses').rglob('*.json'))
            passed('external-current-deletion-cannot-create-second-root')
            # Symlink in ordinary current namespace is rejected, never followed.
            link_root = root / 'symlink'
            shutil.copytree(root / 'fault-baseline', link_root)
            target = link_root / 'current/global/main.md'
            target.unlink()
            target.symlink_to(root / 'notes/current/global/main.md')
            _reject(lambda: snapshot_notes(link_root, first, allowed_keys=['global/main']), 'symlink')
            passed('symlink-current-refused')
            # Pending, cancelled, failure and budget refusal are tracked without
            # constructing a librarian client or automatic retry.
            for status in ('failed', 'not-admitted', 'no-op'):
                notes.finish_trigger(trigger['id'], status, 'synthetic ' + status)
            task_after = _read_json(notes.root / 'triggers' / (trigger['id'] + '.json'))
            assert [value['status'] for value in task_after['attempts']][-3:] == ['failed', 'not-admitted', 'no-op']
            assert len(client.exchanges) == 6
            passed('no-change-failure-budget-refusal-recorded-without-auto-requests')
        finally:
            client.close()
        # Same T10 action journal distinguishes generated/attempted/unknown/
        # failed facts. These fixtures are reference tools, not QQ sends.
        for scenario, expected_statuses in (
                ('cancel-before-tool', {'not-dispatched'}),
                ('result-failure', {'unknown', 'not-dispatched'}),
                ('future-tool', {'failed', 'not-dispatched'})):
            cancellation = root / (scenario + '-loop')
            cancellation.mkdir()
            cancel_client = ClosedClient(cancellation, {**manifest, 'scenario': scenario}, scenario)
            if scenario == 'future-tool':
                future = next(event for event in frozen['events'] if event['event_key'] not in first['visible_event_keys'])
                cancel_client.future_reference = {'source_alias': future['source_alias'], 'event_key': future['event_key']}
            try:
                cancel_client.ledger.wake()
                try:
                    outcome = drive_wake(first, wakes[0], [wakes[0]], cancellation, cancel_client, scenario=scenario)
                    terminal_status = 'cancelled' if outcome['cancelled'] else 'complete'
                except (RuntimeError, OSError, ValueError):
                    terminal_status = 'failed'
                finish_wake(cancellation, wakes[0]['id'], terminal_status, list(cancel_client.saved))
                cancel_client.ledger.stop('explicit terminal diagnostic; no automatic retry')
                cancelled_writer = NoteWriter.create(root / (scenario + '-notes'))
                task = cancelled_writer.register_trigger(cancellation, wakes[0]['id'], first, allowed_keys=['global/main'])
                task = cancelled_writer.admit_trigger(task['id'], cancellation, first)
                execution = [value for value in task['cutoff']['exposures'].values() if value['kind'] == 'execution']
                assert {value['status'] for value in execution} == expected_statuses
                _reject(lambda: cancelled_writer.prepare_candidate(task['id'], 'global/main', b'no retry',
                          sources=list(task['cutoff']['exposures']), reason='partial terminal', expectation='reject'))
                cancelled_writer.finish_trigger(task['id'], 'not-admitted', 'first slice processes only complete terminal; explicit diagnostic retained')
                assert cancel_client.attempts == 1
                passed('partial-terminal-facts-no-auto-retry-' + scenario, statuses=sorted(expected_statuses))
            finally:
                cancel_client.close()
        for fault in ('previous', 'audit-before', 'audit-partial', 'audit-flush', 'audit-fsync', 'audit-replace',
                      'audit-after-replace', 'audit-dir-fsync', 'current-before', 'current-partial', 'current-flush',
                      'current-fsync', 'current-replace', 'current-after-replace', 'current-dir-fsync',
                      'receipt-before', 'receipt-partial', 'receipt-flush', 'receipt-fsync', 'receipt-replace',
                      'receipt-after-replace', 'receipt-dir-fsync'):
            folder = root / ('fault-' + fault)
            shutil.copytree(root / 'fault-baseline', folder)
            writer = NoteWriter(folder)
            result = writer.commit_candidate(b, fault=fault)
            assert result['status'] == 'failed' and writer.poisoned
            before = {str(p.relative_to(folder)): _hash(p.read_bytes()) for p in folder.rglob('*') if p.is_file()}
            def forbidden(*_, **__):
                raise AssertionError('read-only recovery wrote/constructed a client')
            with patch.object(Path, 'write_bytes', forbidden), patch.object(Path, 'write_text', forbidden), \
                 patch.object(ClosedClient, '__init__', forbidden):
                recovered = recover_notes(folder)
            after = {str(p.relative_to(folder)): _hash(p.read_bytes()) for p in folder.rglob('*') if p.is_file()}
            assert before == after and not recovered['rejected'] and recovered['persistence_unknown']
            current = recovered['notes']['global/main']['version_id']
            assert current in {a_result['version_id'], b_result['version_id']}
            _reject(lambda: writer.commit_candidate(b), 'closed')
            _reject(lambda: NoteWriter(folder).commit_candidate(b), 'closed')
            faults[fault] = {'result': result, 'recovery': recovered, 'all_bytes_unchanged': True}
        passed('22-injected-previous-audit-current-receipt-breakpoints-read-only-old-or-new-and-reopened-writer-closed')
        tamper_root = root / 'tampered-candidates'
        shutil.copytree(root / 'fault-baseline', tamper_root)
        original_candidate = _read_json(tamper_root / 'candidates' / (b + '.json'))
        for name, changes in (
                ('restore', {'operation': 'restore', 'restore_version': 'nonexistent'}),
                ('delete-body', {'operation': 'delete'}),
                ('operation', {'operation': 'unrecognized'}),
                ('generation', {'generation': 999}),
                ('frozen-source', {'source_frozen_id': 'foreign'}),
                ('scope', {'key': 'person/202'}),
                ('future-source', {'sources': ['unexposed-future']})):
            forged = {**deepcopy(original_candidate), **changes}
            forged_id = _hash(canonical(forged))
            (tamper_root / 'candidates' / (forged_id + '.json')).write_bytes(_pack(forged))
            result = NoteWriter(tamper_root).commit_candidate(forged_id)
            assert result['status'] == 'rejected', (name, result)
        passed('recomputed-candidate-hashes-cannot-bypass-shared-commit-policy')
        rewind_root = root / 'external-full-version-rewind'
        shutil.copytree(root / 'fault-baseline', rewind_root)
        rewind_writer = NoteWriter(rewind_root)
        assert rewind_writer.commit_candidate(b)['status'] == 'committed'
        (rewind_root / 'current/global/main.md').write_bytes(frozen_a[0]['raw'])
        assert rewind_writer.commit_candidate(b)['status'] == 'rejected'
        rewind_pause = _read_json(rewind_root / 'pauses/global/main.json')
        assert rewind_pause['expected_version'] == b_result['version_id']
        _reject(lambda: rewind_writer.rebaseline('global/main', expected_observation=rewind_pause['observed_sha256'],
                  parent_version=a_result['version_id'], trigger_id=first_trigger['id'],
                  sources=list(first_trigger['cutoff']['exposures']), reason='bad rewind', expectation='reject'), 'parent')
        passed('whole-file-external-rewind-detected-by-immutable-applied-audit-without-current-fallback')
        paused_root = root / 'cooperative-manual-edit'
        shutil.copytree(root / 'fault-baseline', paused_root)
        paused_writer = NoteWriter(paused_root)
        paused_writer.pause_note('global/main', reason='human begins edit while automatic writer paused')
        assert paused_writer.commit_candidate(b)['status'] == 'rejected'
        paused_path = paused_root / 'current/global/main.md'
        paused_path.write_bytes(paused_path.read_bytes().partition(b'\n')[0] + b'\nCOOPERATIVE_HUMAN_BYTES\r\n')
        observed = paused_writer.pause_note('global/main', reason='human saved; explicitly observe new bytes')
        assert paused_writer.rebaseline('global/main', expected_observation=observed['observed_sha256'],
                   parent_version=a_result['version_id'], trigger_id=first_trigger['id'],
                   sources=list(first_trigger['cutoff']['exposures']), reason='explicit human baseline',
                   expectation='keep human body')['status'] == 'committed'
        assert paused_writer.commit_candidate(b)['status'] == 'stale'
        passed('explicit-pre-edit-pause-and-post-edit-observation-new-baseline')
        # Deliberate non-cooperating editor at the final check/replace gap.
        race_root = root / 'toctou-counterexample'
        shutil.copytree(root / 'fault-baseline', race_root)
        race_writer = NoteWriter(race_root)
        import stateless_notes
        original_replace = stateless_notes.os.replace
        def racing_replace(source, target):
            if str(target).endswith('current/global/main.md'):
                Path(target).write_bytes(b'NONCOOPERATING_EDITOR_WRITE')
            return original_replace(source, target)
        with patch.object(stateless_notes.os, 'replace', racing_replace):
            race_result = race_writer.commit_candidate(b)
        assert race_result['status'] == 'committed'
        assert b'NONCOOPERATING_EDITOR_WRITE' not in (race_root / 'current/global/main.md').read_bytes()
        passed('TOCTOU-counterexample-confirms-noncooperative-editor-outside-guarantee')
        assert all(value == 0 for value in counters.values())
    durable(root / 'parser-console.json', {'synthetic_console': console.getvalue()})
    result = {'schema': 'stateless-notes-doctor-v1', 'status': 'ok', 'assertions': assertions, 'faults': faults,
              'sentinels': counters, 'real_HTTP': 0, 'QQ': 0, 'elapsed_seconds': time.perf_counter() - started,
              'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'source_hashes': {name: _hash((Path(__file__).parent / name).read_bytes())
                                for name in ('stateless_notes.py', 'stateless_notes_calibration.py', 'stateless_loop.py', 'stateless_calibration.py')},
              'limitations': ['fixture candidates; no real librarian generation or semantic learning evidence',
                              'T07 ordinary-group archive only; stable personal key and explicit scope, no private-source ingestion proof',
                              'per-file cooperative lock; noncooperative editor TOCTOU explicitly reproduced',
                              'fault injection, not physical power-loss/all-filesystem durability proof',
                              'no atomic multi-file snapshot; no automatic retry, scheduler or production editor integration']}
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
