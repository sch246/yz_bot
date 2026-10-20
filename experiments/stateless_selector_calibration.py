"""Closed synthetic selector -> T10 drive_wake -> actual SDK wire calibration."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import resource
import sys
import time
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from a0_ledger import canonical, digest, durable
from comparison_snapshot import open_snapshot, resolve_ref, _safe_root
from stateless_loop import drive_wake, recover_commits, finish_wake, selected_context
from stateless_calibration import ClosedClient, synthetic_fixture, sentinels, MODEL, WIRE_MODEL, code_hashes
from stateless_selector import (LIMITS, build_selector_input, select_materials, resolve_selection,
                                record_consumption, save_receipt, close_wake)
import httpx

SCENARIOS = ('normal', 'zero', 'budget', 'cancel-before', 'cancel-after-read', 'cancel-request',
             'missing', 'conflict', 'overreport', 'connection', 'length', 'reserve-fsync', 'settle-fsync',
             'request-evidence-failure', 'receipt-failure', 'read-receipt-failure', 'resolve-receipt-failure',
             'bad-input-hash', 'unknown-reference', 'future-read', 'expired-reference', 'malformed-final',
             'situation-limit', 'reference-limit', 'read-limit', 'request-limit', 'bytes-limit', 'bad-note-hash', 'resolve-bad-hash', 'chat-result-failure', 'chat-receipt-failure')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture_catalog(snapshot, state):
    """Only invented values. Note storage is a fixed exact-version read closure."""
    values = {}
    raw = b'EXACT_NOTE_V1\r\n  keep trailing spaces  \n'
    entry = {'entry_id': 'note-v1', 'kind': 'note', 'title': 'fictional reusable observation',
             'scope': 'global', 'branch_id': 'main', 'version_id': 'v1', 'source_id': 'fixture-note',
             'committed_cursor': 1, 'expires_cursor': 3, 'sha256': sha(raw),
             'ref': {'source_id': 'fixture-note', 'version_id': 'v1', 'snapshot_id': snapshot['snapshot_id']}}
    candidates = [entry]
    values[('fixture-note', 'v1')] = {'source_id': 'fixture-note', 'version_id': 'v1',
        'snapshot_id': snapshot['snapshot_id'], 'raw': raw, 'sha256': sha(raw)}
    for alias, marker in [('alpha', 'CORRECTION_B'), ('beta', 'OTHER_WINDOW_SAME_SECOND')]:
        event = next(v for v in snapshot['frozen_sources']['events'] if v['source_alias'] == alias and marker in v['record']['message'])
        ref = {'snapshot_id': snapshot['snapshot_id'], 'source_alias': alias, 'event_key': event['event_key']}
        exact = resolve_ref(snapshot, ref)
        candidates.append({'entry_id': 'record-' + alias, 'kind': 'chatlog', 'title': 'fictional same message ID 9 in ' + alias,
             'scope': 'group:' + alias, 'branch_id': 'main', 'version_id': 'frozen-record', 'source_id': alias,
             'committed_cursor': 1, 'expires_cursor': 3, 'sha256': exact['sha256'], 'ref': ref})
    for identity in list(state['outputs'])[:1]:
        value = selected_context(snapshot, (), [identity], state)['committed_actions'][0]
        candidates.append({'entry_id': 'prior-action', 'kind': 'action', 'title': 'committed prior generation and actual results',
             'scope': 'global', 'branch_id': 'main', 'version_id': 'committed-cutoff', 'source_id': 'fixture-actions',
             'committed_cursor': 1, 'expires_cursor': 3, 'sha256': sha(canonical(value)), 'ref': {'output_id': identity}})
    # The private titles and versions must never enter any wire, including catalog.
    for mode, changes in [('future', {'committed_cursor': 3}), ('uncommitted', {'committed_cursor': 0}),
                          ('branch', {'branch_id': 'foreign'}), ('scope', {'scope': 'private:other'}),
                          ('expired', {'expires_cursor': 0})]:
        candidates.append({**deepcopy(entry), 'entry_id': 'hidden-' + mode,
                           'title': 'HIDDEN_TITLE_' + mode, 'version_id': 'HIDDEN_VERSION_' + mode, **changes})
    calls = []
    def resolver(entry):
        calls.append([entry['source_id'], entry['version_id']])
        return deepcopy(values[(entry['source_id'], entry['version_id'])])
    return candidates, resolver, values, calls


class SelectorClient(ClosedClient):
    """Only role-specific closed responses differ; inherited guard/SDK unchanged."""
    def __init__(self, root, manifest, scenario):
        inherited = scenario if scenario in {'cancel-request', 'missing', 'conflict', 'overreport', 'connection',
                    'length', 'reserve-fsync', 'settle-fsync', 'request-evidence-failure'} else 'normal'
        super().__init__(root, manifest, inherited)
        self.selector_scenario = scenario
        self.selector_reads = 0
        if scenario == 'budget':
            self.ceiling['attempts'] = 2

    def _mock(self, request):
        if self.current.get('role') != 'selector':
            return super()._mock(request)
        row = self.active['row']
        disk = json.loads((self.root / 'ledger.json').read_bytes())['state']['requests'][-1]
        saved = json.loads((self.root / self.evidence[row['id']]).read_bytes())
        if (self.active['attempted'] or row != disk or disk['status'] != 'reserved'
                or request.content.decode() != saved['envelope']['serialized_body']):
            raise RuntimeError('selector mock without durable unique reservation/evidence')
        self.active['attempted'] = True
        self.attempts += 1
        self.trace.append({'event': 'mock', 'request_id': row['id']})
        scenario = self.selector_scenario
        if scenario == 'connection':
            raise httpx.ConnectError('synthetic selector connection failure', request=request)
        body = json.loads(request.content)
        context = json.loads(body['messages'][1]['content'])
        ids = [v['entry_id'] for v in context['input']['catalog']]
        initial = not any(m['role'] == 'tool' for m in body['messages'])
        if initial and scenario != 'zero':
            read_ids = ids[:3]
            if scenario == 'future-read':
                read_ids = ['hidden-future', 'note-v1']
            calls = [{'id': self.current['wake'] + '-selector-' + str(i), 'type': 'function',
                      'function': {'name': 'read_material', 'arguments': canonical({'entry_id': entry_id}).decode()}}
                     for i, entry_id in enumerate(read_ids)]
            message = {'role': 'assistant', 'content': '', 'tool_calls': calls,
                       'reasoning_content': 'SELECTOR_EPHEMERAL_' + self.current['wake']}
            finish = 'tool_calls'
        else:
            choice = {'situation': 'Fixture inference only; exact evidence follows separately.',
                      'references': [] if scenario == 'zero' else ids + ['note-v1'], 'input_hash': context['input_hash']}
            if scenario == 'bad-input-hash': choice['input_hash'] = '0' * 64
            if scenario in {'unknown-reference', 'expired-reference'}:
                choice['references'] = ['missing' if scenario == 'unknown-reference' else 'hidden-expired']
            if scenario == 'situation-limit': choice['situation'] = 'x' * (LIMITS['situation_bytes'] + 1)
            if scenario == 'reference-limit': choice['references'] = ['note-v1'] * (LIMITS['references'] + 1)
            content = '{' if scenario == 'malformed-final' else canonical(choice).decode()
            message = {'role': 'assistant', 'content': content, 'reasoning_content': 'FINAL_SELECTOR_EPHEMERAL'}
            finish = 'stop'
        usage = {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120, 'prompt_cache_hit_tokens': 40}
        if scenario == 'missing': usage = None
        elif scenario == 'conflict': usage['prompt_tokens_details'] = {'cached_tokens': 41}
        elif scenario == 'overreport': usage.update(completion_tokens=4097, total_tokens=4197)
        return httpx.Response(200, json={'id': 'synthetic-selector', 'object': 'chat.completion', 'created': 1767225600,
            'model': WIRE_MODEL, 'choices': [{'index': 0, 'finish_reason': 'length' if scenario == 'length' else finish,
                                           'message': message}], 'usage': usage})


def run(output, scenario='normal'):
    if scenario not in SCENARIOS:
        raise ValueError('unknown closed synthetic scenario')
    root = _safe_root(output)
    root.mkdir(parents=True, mode=0o700)
    started = time.perf_counter()
    frozen, schedule, wakes = synthetic_fixture()
    hashes = code_hashes()
    for name in ('stateless_selector.py', 'stateless_selector_calibration.py'):
        hashes['experiments/' + name] = sha((Path(__file__).parent / name).read_bytes())
    manifest = {'schema': 'stateless-selector-calibration-v1', 'synthetic': True, 'scenario': scenario,
                'code': hashes, 'frozen_id': frozen['frozen_id'], 'schedule_sha256': digest(schedule),
                'wakes_sha256': digest(wakes), 'limits': LIMITS, 'transport': 'closed MockTransport',
                'limitation': 'synthetic selection only; no semantic quality/provider acceptance'}
    durable(root / 'manifest.json', manifest)
    client = SelectorClient(root, manifest, scenario)
    receipts, failure, guard_closed = [], None, None
    selection, bundle, receipt = None, None, None
    try:
        for wake in wakes[:2]:
            client.ledger.wake()
            snapshot = open_snapshot(frozen, schedule, wake['cursor'])
            state = recover_commits(root)
            candidates, resolver, values, calls = fixture_catalog(snapshot, state)
            limits = dict(LIMITS)
            if scenario == 'read-limit': limits['reads'] = 1
            if scenario == 'request-limit': limits['requests'] = 1
            if scenario == 'bytes-limit': limits['material_bytes'] = 1
            if scenario == 'bad-note-hash':
                values[('fixture-note', 'v1')]['raw'] = b'WRONG_BYTES'
            context = build_selector_input(snapshot, wake, 'FROZEN_HINT', candidates,
                        scopes=('global', 'group:alpha', 'group:beta'), limits=limits, committed_state=state)
            receipt, selection, bundle = {}, None, None
            receipts.append(receipt)
            receipt_path = root / ('selector-' + wake['id'] + '.json')
            original = durable
            def fault_receipt(path, value):
                events = value.get('events', [])
                stage = events[-1]['stage'] if events else None
                if ((scenario == 'receipt-failure' and stage == 'catalog')
                        or (scenario == 'read-receipt-failure' and stage == 'read')
                        or (scenario == 'resolve-receipt-failure' and stage == 'resolved-bundle')):
                    raise OSError('synthetic selector receipt failure at ' + stage)
                return original(path, value)
            import stateless_selector
            with patch.object(stateless_selector, 'durable', fault_receipt):
                selection = select_materials(context, snapshot, client, resolver, receipt, receipt_path,
                    committed_state=state, cancelled=lambda: scenario == 'cancel-before' or
                        scenario == 'cancel-after-read' and len(calls) > 0)
                if scenario == 'resolve-bad-hash':
                    values[('fixture-note', 'v1')]['raw'] = b'CHANGED_AFTER_SELECTION'
                bundle = resolve_selection(context, selection, snapshot, resolver, committed_state=state)
                receipt['resolved'] = deepcopy(bundle['values'])
                receipt['events'].append({'stage': 'resolved-bundle', 'entry_ids': selection['references']})
                save_receipt(client, receipt_path, receipt)
            resolver_count = len(calls)
            # Same logical note source now has a newer value. T10 receives old bytes.
            values[('fixture-note', 'v1')]['raw'] = b'NEWEST_POISON_MUST_NOT_REPLACE_FROZEN_BYTES'
            before_chat = len(client.exchanges)
            result = drive_wake(snapshot, wake, wakes, root, client, references=bundle['references'],
                                actions=bundle['actions'], materials=bundle['materials'],
                                scenario='result-failure' if scenario == 'chat-result-failure' else 'normal')
            assert len(calls) == resolver_count
            chat_request = client.saved[client.exchanges[before_chat]['request_id']]
            consumed = record_consumption(receipt, bundle, chat_request)
            if any(v['status'] != 'injected' for v in consumed):
                raise AssertionError('T10 failed exact material consumption')
            receipt['note_resolver_calls_after_freeze'] = len(calls) - resolver_count
            if scenario == 'chat-receipt-failure':
                with patch.object(stateless_selector, 'durable', side_effect=OSError('synthetic post-chat receipt failure')):
                    save_receipt(client, receipt_path, receipt)
            else:
                save_receipt(client, receipt_path, receipt)
            request_ids = [v['envelope']['request_id'] for v in client.saved.values()
                           if v['attribution']['task_id'] == wake['id']]
            finish_wake(root, wake['id'], 'complete', request_ids)
    except Exception as error:
        failure = {'type': type(error).__name__, 'reason': str(error)}
        close_wake(client, 'selector-session-failure')
        if receipt is not None:
            if bundle is not None:
                sent = {v['request_id'] for v in client.trace if v['event'] == 'mock'}
                actual = [client.saved[v['request_id']] for v in client.exchanges
                          if v['request_id'] in sent and client.saved[v['request_id']]['attribution']['role_id'] == 'chat'
                          and client.saved[v['request_id']]['attribution']['task_id'] == wake['id']]
                record_consumption(receipt, bundle, actual[0] if actual else None)
            receipt['failure'] = failure
            # Failure evidence uses a separate sink; the deliberately failing
            # receipt was never permission to continue the wake.
            durable(root / 'failed-selector.json', receipt)
        before = (client.attempts, len(client.ledger.state['requests']), len(client.trace))
        client.current = {'role': 'chat', 'wake': 'blocked', 'snapshot_id': 'blocked'}
        try:
            list(client.generate_response([{'role': 'user', 'content': 'blocked role probe'}], model=MODEL, stream=False))
        except RuntimeError:
            guard_closed = before == (client.attempts, len(client.ledger.state['requests']), len(client.trace))
        else:
            guard_closed = False
        assert guard_closed
    finally:
        client.close()
    try:
        costs = client.projection()
    except ValueError as error:
        costs = {'error': str(error)}
    result = {'schema': 'stateless-selector-result-v1', 'status': 'stopped' if failure else 'complete',
              'failure': failure, 'scenario': scenario, 'attempts': client.attempts, 'settlements': client.settlements,
              'shared_guard_closed': guard_closed, 'receipts': receipts, 'trace': client.trace,
              'ledger': client.ledger.state, 'costs': costs,
              'request_roles': [client.saved[v['request_id']]['attribution']['role_id'] for v in client.exchanges],
              'selector_chat_objects_distinct': len({id(v) for v in client.chats}) == len(client.chats),
              'messages_objects_distinct': len({id(v.messages) for v in client.chats}) == len(client.chats),
              'elapsed_seconds': time.perf_counter() - started,
              'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'real_HTTP': 0, 'QQ': 0}
    durable(root / 'result.json', result)
    return result


def doctor(root):
    cases, assertions = {}, []
    def passed(name): assertions.append(name)
    with sentinels(root) as counters, redirect_stdout(io.StringIO()) as console:
        for scenario in SCENARIOS:
            report = run(root / scenario, scenario)
            cases[scenario] = {'status': report['status'], 'attempts': report['attempts'], 'failure': report['failure']}
            if scenario in {'normal', 'zero'}:
                assert report['status'] == 'complete'
                assert report['selector_chat_objects_distinct'] and report['messages_objects_distinct']
                assert report['attempts'] == (8 if scenario == 'normal' else 6)
                for receipt in report['receipts']:
                    assert len(receipt['events']) > 3 and receipt['selector_request_ids']
                    assert receipt['note_resolver_calls_after_freeze'] == 0
                    assert all(v['status'] == 'injected' for v in receipt['consumption'])
                    if scenario == 'zero': assert receipt['selection']['references'] == []
                    else:
                        assert receipt['selection']['duplicates'] == [{'entry_id': 'note-v1', 'index': len(receipt['selection']['references'])}]
                        assert {v['entry_id'] for v in receipt['resolved']} >= {'record-alpha', 'record-beta', 'note-v1'}
                saved = [json.loads(p.read_bytes()) for p in sorted((root / scenario).glob('request-*.json'))]
                for request in saved:
                    text = request['envelope']['serialized_body']
                    assert 'HIDDEN_TITLE_' not in text and 'HIDDEN_VERSION_' not in text
                    assert 'FUTURE_BODY_ONLY' not in text and 'NEWEST_POISON' not in text
                    if request['attribution']['role_id'] == 'chat':
                        initial = json.loads(request['envelope']['body']['messages'][1]['content'])
                        assert initial['screen']['records']
                        assert 'SELECTOR_EPHEMERAL' not in text
                selector_initial = [v for v in saved if v['attribution']['role_id'] == 'selector'
                                    and len(v['envelope']['body']['messages']) == 2]
                assert len(selector_initial) == 2
                assert all(v['role'] in {'system', 'user'} for v in selector_initial[1]['envelope']['body']['messages'])
                passed(scenario + ': fresh selector/chat exact T10 wire and no re-read')
            else:
                assert report['status'] == 'stopped' and report['failure'] and report['shared_guard_closed']
                if scenario in {'chat-result-failure', 'chat-receipt-failure'}:
                    assert 'chat' in report['request_roles']
                    assert all(v['status'] == 'injected' for v in report['receipts'][0]['consumption'])
                else:
                    assert 'chat' not in report['request_roles']
                if scenario == 'budget':
                    assert report['attempts'] == 2
                    assert all(v['status'] == 'selected-not-injected' for v in report['receipts'][0]['consumption'])
                if scenario in {'cancel-before', 'receipt-failure', 'request-evidence-failure'}:
                    assert report['attempts'] == 0
                if scenario in {'cancel-after-read', 'read-limit', 'request-limit', 'bytes-limit', 'future-read', 'bad-note-hash', 'read-receipt-failure'}:
                    assert report['attempts'] == 1
                passed(scenario + ': accurate consumption and shared role guard closed')
        frozen, schedule, wakes = synthetic_fixture()
        snapshot = open_snapshot(frozen, schedule, 1)
        candidates, resolver, _, _ = fixture_catalog(snapshot, {'outputs': {}, 'terminals': {}})
        kwargs = {'scopes': ('global', 'group:alpha', 'group:beta')}
        for name, mutate in [
            ('ambiguous', lambda rows: rows.append(deepcopy(rows[0]))),
            ('chat-bad-hash', lambda rows: rows[1].update(sha256='0' * 64)),
            ('unopened-chat', lambda rows: rows[1]['ref'].update(event_key=next(e['event_key'] for e in frozen['events'] if e['event_key'] not in snapshot['visible_event_keys']))),
            ('ambiguous-ID', lambda rows: rows[2].update(ref={'snapshot_id': snapshot['snapshot_id'], 'source_alias': 'beta', 'message_id': 9})),
            ('stale-note', lambda rows: rows[0]['ref'].update(snapshot_id='stale')),
            ('wrong-chat-scope', lambda rows: rows[2].update(scope='group:alpha'))]:
            rows = deepcopy(candidates)
            mutate(rows)
            try: build_selector_input(snapshot, wakes[0], 'hint', rows, **kwargs)
            except ValueError: passed(name + ': catalog refused')
            else: raise AssertionError(name + ' accepted')
        for limit in ('catalog_entries', 'catalog_bytes'):
            low = {**LIMITS, limit: 1}
            try: build_selector_input(snapshot, wakes[0], 'hint', candidates, limits=low, **kwargs)
            except ValueError: passed(limit + ': no truncated catalog')
            else: raise AssertionError('catalog limit accepted')
        context = build_selector_input(snapshot, wakes[0], 'hint', candidates, **kwargs)
        assert {v['reason'] for v in context['excluded']} == {'future', 'uncommitted', 'foreign-branch', 'wrong-scope', 'expired'}
        selection = {'situation': 'inference', 'references': ['note-v1'], 'input_hash': context['input_hash']}
        try: resolve_selection(context, selection, open_snapshot(frozen, schedule, 2), resolver)
        except ValueError: passed('expired snapshot resolution refused')
        else: raise AssertionError('stale snapshot accepted')
        assert all(v == 0 for v in counters.values())
        passed('socket QQ external-write sentinels zero')
    durable(root / 'parser-console.json', {'console': console.getvalue()})
    result = {'schema': 'stateless-selector-doctor-v1', 'status': 'ok', 'assertions': assertions,
              'assertion_count': len(assertions), 'cases': cases, 'sentinels': counters,
              'limitations': ['all identities and selections synthetic; no semantic quality evidence',
                              'frozen note resolver only; no T12 writer/T13 joint adaptation',
                              'nonstream MockTransport; no paid HTTP/QQ/production state']}
    durable(root / 'doctor.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('doctor', 'run'))
    parser.add_argument('--output', required=True)
    parser.add_argument('--scenario', choices=SCENARIOS, default='normal')
    args = parser.parse_args(argv)
    try:
        root = _safe_root(args.output)
        if args.command == 'doctor':
            root.mkdir(parents=True, mode=0o700)
            result = doctor(root)
        else:
            with sentinels(root), redirect_stdout(io.StringIO()):
                result = run(root, args.scenario)
        print(json.dumps({'status': result['status'], 'assertion_count': result.get('assertion_count'),
                          'failure': result.get('failure'), 'output': str(root)}))
        return int(result['status'] == 'stopped')
    except (OSError, ValueError, RuntimeError, AssertionError, KeyError) as error:
        print(f'selector calibration: {type(error).__name__}: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
