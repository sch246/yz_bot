"""Synthetic SDK+MockTransport capacity doctor; never a live execution CLI."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout, contextmanager
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import resource
import subprocess
import os
import sys
import time
from datetime import datetime, timedelta, timezone
import httpx
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import a0_ledger
import stateless_capacity_client as capacity
from a0_ledger import canonical, digest, durable
from comparison_snapshot import _safe_root, open_snapshot
from stateless_calibration import synthetic_fixture, sentinels
from stateless_selector_calibration import fixture_catalog
from stateless_selector import build_selector_input, select_materials, resolve_selection, record_consumption
from stateless_loop import drive_wake, finish_wake, recover_commits


LIMITATION = ['This doctor uses closed MockTransport only; the separate official entry is not executed live.',
              'Historical capacity/prices are declarations, not current official verification or provider acceptance.',
              'No real failure-billing, model quality, autonomous subject or production evidence.',
              'Fixture budgets are not the authorized run allocation; no formal run scale chosen.',
              'Future live selector needs a real sent event; current mock exposure is closed-only.',
              'Fault injection is not a physical crash/power-loss proof.']


@contextmanager
def isolation(root):
    """Add production-read and model-attempt counters to the existing sentinels."""
    repo = Path(__file__).resolve().parents[1]
    common = subprocess.run(['git', 'rev-parse', '--git-common-dir'], cwd=repo,
                            capture_output=True, check=True, text=True).stdout.strip()
    common = Path(common)
    if not common.is_absolute():
        common = repo / common
    repositories = {repo.resolve(), common.resolve().parent}
    counts, active = {'production_read': 0, 'real_model': 0}, [True]
    def audit(event, args):
        if not active[0] or event not in ('open', 'os.listdir', 'os.scandir'):
            return
        value = args[0]
        if not isinstance(value, (str, bytes, os.PathLike)):
            return
        path = Path(os.fsdecode(value)).absolute().resolve()
        for repository in repositories:
            if path.is_relative_to(repository):
                relative = path.relative_to(repository)
                first = relative.parts[0] if relative.parts else ''
                if (first in {'data', 'chatlog', 'log', 'config.json', '.venv', 'outputs', 'woutputs'}
                        or first.startswith(('.env', 'app.log'))):
                    counts['production_read'] += 1
                    raise RuntimeError('blocked production state access')
    sys.addaudithook(audit)
    try:
        with sentinels(root) as base:
            yield counts, base
    finally:
        active[0] = False


def fixture_budgets():
    ceiling = {'tokens': 5000000, 'micro_cny': 5000000, 'attempts': 12}
    return {'research': dict(ceiling), 'stages': {stage: dict(ceiling) for stage in capacity.STAGES}}


def new_client(root, name, *, scenario='normal', budgets=None, role_stages=None):
    folder = root / name
    folder.mkdir(mode=0o700)
    frozen = capacity.manifest('fictional-' + name, fixture_budgets() if budgets is None else budgets,
                               role_stages=role_stages)
    durable(folder / 'manifest.json', frozen)
    return capacity.CapacityClient(folder, frozen, scenario=scenario)


def request(client, role='librarian', *, wake='fixture-wake', snapshot='fixture-snapshot'):
    client.current = {'role': role, 'wake': wake, 'snapshot_id': snapshot}
    return client.generate_response([{'role': 'system', 'content': 'Synthetic only.'},
                                    {'role': 'user', 'content': '{}'}], stream=False)


def consume(generator):
    values = []
    while True:
        try:
            values.append(next(generator))
        except StopIteration as done:
            return values, done.value


def shared_closed(client):
    before = client.attempts
    for role in client.manifest['role_stages']:
        try:
            consume(request(client, role))
        except capacity.CapacityStop:
            pass
        else:
            raise AssertionError('role escaped shared closure')
    try:
        client.guard_tools()
    except capacity.CapacityStop:
        pass
    else:
        raise AssertionError('tool escaped shared closure')
    assert client.attempts == before


def save_case(root, name, client, failure=None, **extra):
    report = {'case': name, 'attempts': client.attempts, 'settlements': client.settlements,
              'failure': failure, 'poisoned': client.ledger.poisoned,
              'ledger': deepcopy(client.ledger.state), 'trace': deepcopy(client.trace), **extra}
    durable(root / name / 'result.json', report)
    client.close()
    return {k: report[k] for k in ('case', 'attempts', 'settlements', 'failure', 'poisoned')}


def official_fixture(root, name, *, budgets=None, role_stages=None, mutate=None, origin='synthetic-calibration'):
    """Invent every operator record; nothing here is an actual authorization."""
    import stateless_official as official
    folder = root / name
    folder.mkdir(mode=0o700)
    if budgets is None:
        budgets = {'research': {'tokens': 20000000, 'micro_cny': 20000000, 'attempts': 12},
                   'stages': {stage: {'tokens': 5000000, 'micro_cny': 5000000, 'attempts': 3}
                              for stage in capacity.STAGES}}
    value = official.manifest(folder, 'fictional-' + name, budgets,
                               capacity.ROLE_STAGES if role_stages is None else role_stages, {})
    now = datetime.now(timezone.utc)
    data = {
        'plan.json': {'schema': 'stateless-official-entry-v1', 'objective': 'Synthetic calibration only.', 'seed': 7,
                      'material_sha256': digest({'synthetic': True}),
                      'stopping': 'one-entry; caller-loop-bounded-by-frozen-Ledger; unknown-stop-no-replay',
                      'entry': {'current': {'role': 'librarian', 'wake': 'fixture-wake', 'snapshot_id': 'fixture-snapshot'},
                                'messages': [{'role': 'system', 'content': 'Synthetic only.'}, {'role': 'user', 'content': '{}'}]}},
        'models-response.json': {'object': 'list', 'data': [{'id': capacity.WIRE_MODEL,
                                 'context_window': capacity.LIMITS['context'], 'max_output_tokens': 393216}]},
        'target-metadata.json': {'schema': 'stateless-target-metadata-v1', 'origin': origin, 'method': 'GET',
                                 'url': capacity.ENDPOINT + '/models', 'observed_at': now.isoformat(), 'response_sha256': ''},
        'research-allocation.json': {'schema': 'stateless-research-allocation-v1', 'origin': origin, 'revision': 'fictional-revision',
                                    'ceiling': {'tokens': official.COMMON_TOKENS, 'micro_cny': 100000000, 'attempts': 100},
                                    'spent': dict.fromkeys(capacity.METRICS, 0),
                                    'reservations': [{'id': 'fictional-reservation', 'run_id': value['run_id'], 'root': str(folder),
                                                      'binding': official.binding(value), 'budgets': deepcopy(budgets)}]},
        'authorization.json': {'schema': 'stateless-official-authorization-v1', 'origin': origin, 'mode': 'execute-once',
                               'binding': official.binding(value), 'records': {}, 'reservation_id': 'fictional-reservation',
                               'approved_by': 'fictional-reviewer', 'valid_from': (now - timedelta(minutes=1)).isoformat(),
                               'expires_at': (now + timedelta(hours=1)).isoformat(),
                               'accept_unknown_billing': True, 'accept_current_contract': True}}
    if mutate is not None:
        mutate(value, data)
    for name in ('plan.json', 'models-response.json', 'target-metadata.json', 'research-allocation.json', 'authorization.json'):
        if name == 'target-metadata.json':
            data[name]['response_sha256'] = value['records']['models-response.json']
        if name == 'authorization.json':
            data[name]['records'] = dict(value['records'])
        durable(folder / name, data[name])
        value['records'][name] = hashlib.sha256((folder / name).read_bytes()).hexdigest()
    durable(folder / 'manifest.json', value)
    return folder, value


def official_doctor(root):
    import stateless_official as official
    started, results = time.perf_counter(), []
    def passed(name, **evidence):
        results.append({'case': name, 'status': 'pass', **evidence})
    def closed(name, client, action, attempts):
        try:
            action()
        except capacity.CapacityStop:
            pass
        else:
            raise AssertionError(name)
        assert client.attempts == attempts
        shared_closed(client)
        passed(name, attempts=attempts, poisoned=client.ledger.poisoned)
        save_case(root, name, client)
    with isolation(root) as (private, counters), redirect_stdout(io.StringIO()) as console:
        folder, frozen = official_fixture(root, 'normal')
        checked = official.verify(folder, _calibration=official._CALIBRATION)
        assert checked['send_authorized'] is False and not (folder / 'ledger.json').exists()
        client = official._calibration_client(folder, frozen)
        assert client.attempts == 0
        for role in frozen['role_stages']:
            if role == 'selector':
                client.current = {'role': role, 'wake': 'fixture-wake', 'snapshot_id': 'fixture-snapshot'}
                consume(client.generate_response([{'role': 'system', 'content': 'Synthetic only.'},
                    {'role': 'user', 'content': canonical({'input': {'catalog': []}, 'input_hash': digest({})}).decode()}], stream=False))
            else:
                consume(request(client, role))
        assert client.attempts == client.settlements == 4
        for row in client.ledger.state['requests']:
            evidence = client.transport_evidence(row['id'])
            assert evidence['mock_exposed'] and not evidence['sent'] and not evidence['transport_attempted']
            assert evidence['arrival'] == evidence['provider_accepted'] == 'unknown'
            assert row['usage_source'] == 'synthetic-sdk-usage'
        passed('one-Ledger-all-roles-official-schema-internal-fake-final-transport',
               projection=client.projection(), transport=[client.transport_evidence(v['id']) for v in client.ledger.state['requests']])
        save_case(root, 'normal', client)
        try:
            official.OfficialClient(folder, frozen, temporary_api_key='sk-fictional-only')
        except capacity.CapacityStop:
            passed('public-constructor-refuses-synthetic-records')
        else:
            raise AssertionError('synthetic records upgraded to official')

        passed('public-official-constructor-through-internal-calibration-context')

        for scenario in ('missing', 'alias', 'conflict-alias', 'total', 'null-counter', 'joint-overreport',
                         'output-overreport', 'connection', '429', '500', 'cancel-before', 'cancel-after', 'model-drift'):
            name = 'official-' + scenario
            folder, frozen = official_fixture(root, name)
            client = official._calibration_client(folder, frozen, scenario=scenario)
            if scenario == 'alias':
                consume(request(client))
                assert client.ledger.state['requests'][0]['usage'] == [100, 40, 20]
                passed(name)
                save_case(root, name, client)
            else:
                closed(name, client, lambda: consume(request(client)), 0 if scenario == 'cancel-before' else 1)
        for record, mutation in (
            ('metadata-example', lambda v, d: d['target-metadata.json'].update(origin='documentation-example')),
            ('metadata-context', lambda v, d: d['models-response.json']['data'][0].update(context_window=1000000)),
            ('metadata-output', lambda v, d: d['models-response.json']['data'][0].update(max_output_tokens=384000)),
            ('missing-stage-allocation', lambda v, d: d['research-allocation.json']['reservations'].clear()),
            ('other-root-allocation', lambda v, d: d['research-allocation.json']['reservations'][0].update(root='/fictional/other')),
            ('common-overallocation', lambda v, d: d['research-allocation.json']['spent'].update(tokens=official.COMMON_TOKENS)),
            ('authorization', lambda v, d: d['authorization.json'].update(mode='verify')),
            ('billing-unknown', lambda v, d: d['authorization.json'].update(accept_unknown_billing=False)),
            ('expired', lambda v, d: d['authorization.json'].update(expires_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())),
        ):
            folder, frozen = official_fixture(root, record, mutate=mutation)
            try:
                official._calibration_client(folder, frozen)
            except capacity.CapacityStop:
                assert not (folder / 'ledger.json').exists() and not (folder / 'execution-claim.json').exists()
                passed(record + '-refused-before-Ledger')
            else:
                raise AssertionError(record)
        for drift in ('sdk-client', 'sdk-base', 'key', 'route-after-hook', 'body-after-hook', 'hook-removal', 'duplicate-hook',
                      'price', 'code', 'record', 'credential-input', 'credential-response', 'credential-response-escaped'):
            name = 'official-drift-' + drift
            folder, frozen = official_fixture(root, name)
            client = official._calibration_client(folder, frozen)
            with ExitStack() as stack:
                if drift == 'sdk-client': client.sdk._client = httpx.Client(transport=httpx.MockTransport(lambda _: None), trust_env=False)
                elif drift == 'sdk-base': client.sdk.base_url = 'https://fictional.invalid/'
                elif drift == 'key': client.sdk.api_key = 'sk-fictional-drift'
                elif drift in ('route-after-hook', 'body-after-hook', 'duplicate-hook'):
                    def change(req, _drift=drift):
                        client._before_http(req)
                        if _drift == 'route-after-hook': req.url = req.url.copy_with(host='fictional.invalid')
                        elif _drift == 'body-after-hook': req._content = canonical({**json.loads(req.content), 'temperature': 1})
                        else: client._before_http(req)
                    client.http.event_hooks['request'] = [change]
                elif drift == 'hook-removal': client.http.event_hooks['request'] = []
                elif drift == 'price': client.contract['prices']['input'] = '0'
                elif drift == 'code': stack.enter_context(patch.object(capacity, 'source_hashes', return_value={'fictional': 'drift'}))
                elif drift == 'record': (folder / 'authorization.json').write_bytes(b'{}')
                elif drift in ('credential-response', 'credential-response-escaped'):
                    original = client._fixture_response
                    def reflect(body):
                        value = original(body)
                        value['system_fingerprint'] = client._temporary_key
                        return value
                    client._fixture_response = reflect
                    if drift == 'credential-response-escaped':
                        def escaped(req):
                            response = client._mock(req)
                            raw = response.content.replace(b'sk-fictional', b'\\u0073k-fictional')
                            return httpx.Response(200, content=raw)
                        client._transport.inner.handler = escaped
                messages = [{'role': 'user', 'content': client._temporary_key}] if drift == 'credential-input' else None
                if messages is not None:
                    client.current = {'role': 'librarian', 'wake': 'fixture-wake', 'snapshot_id': 'fixture-snapshot'}
                closed(name, client, lambda: consume(client.generate_response(messages, stream=False)) if messages else consume(request(client)),
                       1 if drift.startswith('credential-response') else 0)
                if drift.startswith('credential-response'):
                    assert client.ledger.state['requests'][0]['usage'] == [100, 40, 20]
        for damage in ('malformed', 'truncated', 'missing'):
            for entrance in ('generation', 'tools', 'settle'):
                name = 'official-ledger-' + damage + '-' + entrance
                folder, frozen = official_fixture(root, name)
                client = official._calibration_client(folder, frozen)
                ledger = folder / 'ledger.json'
                def corrupt():
                    if damage == 'missing': ledger.unlink()
                    else: ledger.write_bytes(b'not-json\n' if damage == 'malformed' else b'{"state":')
                if entrance == 'settle':
                    observe = client._observe_http
                    def corrupt_after_response(response):
                        observe(response)
                        corrupt()
                    client.http.event_hooks['response'] = [corrupt_after_response]
                else:
                    corrupt()
                expected = None if damage == 'missing' else b'not-json\n' if damage == 'malformed' else b'{"state":'
                closed(name, client, client.guard_tools if entrance == 'tools' else lambda: consume(request(client)),
                       1 if entrance == 'settle' else 0)
                assert (ledger.read_bytes() if ledger.exists() else None) == expected and client.ledger.poisoned
        counters.update(private)
        assert all(v == 0 for v in counters.values())
    durable(root / 'parser-console.json', {'synthetic_console': console.getvalue()})
    result = {'schema': 'stateless-official-doctor-v1', 'status': 'ok', 'assertion_count': len(results),
              'assertions': results, 'sentinels': counters, 'real_HTTP': 0, 'real_model': 0,
              'elapsed_seconds': time.perf_counter()-started, 'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'limitations': ['All metadata, authorization and allocation records are invented; no API, account or real allocation verified.',
                              'Real network/trace/provider acceptance and billing remain unverified.',
                              'No T17 subject loop, selector live exposure integration or production change.',
                              'External approved allocation snapshot is trusted, not a second writable balance or a cross-process transaction.']}
    durable(root / 'doctor.json', result)
    return result


def doctor(root):
    started = time.perf_counter()
    cases, assertions = [], []
    def passed(name, **evidence):
        assertions.append({'assertion': name, 'status': 'pass', **evidence})
    def rejected(name, client, action, expected_attempts, *, poison=False):
        try:
            action()
        except capacity.CapacityStop as failure:
            label = str(failure)
        else:
            raise AssertionError('accepted ' + name)
        assert client.attempts == expected_attempts, (name, client.attempts, expected_attempts)
        assert client.ledger.poisoned if poison else client.ledger.state['closed']
        shared_closed(client)
        passed(name, failure=label, attempts=client.attempts, poisoned=client.ledger.poisoned)
        cases.append(save_case(root, name, client, label))

    with isolation(root) as (private_counters, counters), redirect_stdout(io.StringIO()) as console:
        client = new_client(root, 'normal')
        frozen, schedule, wakes = synthetic_fixture()
        wake = wakes[0]
        snapshot = open_snapshot(frozen, schedule, wake['cursor'])
        client.ledger.wake()
        state = recover_commits(client.root)
        catalog, resolver, _, _ = fixture_catalog(snapshot, state)
        context = build_selector_input(snapshot, wake, 'Synthetic hint.', catalog,
                                       scopes=('global', 'group:alpha', 'group:beta'), committed_state=state)
        receipt = {}
        selection = select_materials(context, snapshot, client, resolver, receipt,
                                     client.root / 'selector.json', committed_state=state)
        bundle = resolve_selection(context, selection, snapshot, resolver, committed_state=state)
        before = len(client.exchanges)
        result = drive_wake(snapshot, wake, wakes, client.root, client,
                            references=bundle['references'], actions=bundle['actions'], materials=bundle['materials'])
        record = client.saved[client.exchanges[before]['request_id']]['value']
        consumed = record_consumption(receipt, bundle, record)
        assert all(v['status'] == 'injected' for v in consumed)
        finish_wake(client.root, wake['id'], 'complete', [v['request_id'] for v in client.exchanges])
        consume(request(client, 'librarian', wake=wake['id'], snapshot=snapshot['snapshot_id']))
        consume(request(client, 'background', wake=wake['id'], snapshot=snapshot['snapshot_id']))
        projection = client.projection()
        assert client.attempts == client.settlements == 6
        assert projection['scopes']['research']['S'] == {'tokens': 720, 'micro_cny': 1692, 'attempts': 6}
        assert {v['value']['attribution']['role_id'] for v in client.saved.values()} == set(capacity.ROLE_STAGES)
        assert receipt['catalog_exposure']['status'] == 'exposed'
        for row in client.ledger.state['requests']:
            assert row['input_bound'] == capacity.LIMITS['context'] and row['reserved'] == 2129920
        for index, event in enumerate(client.trace):
            if event['event'] == 'mock':
                assert [v['event'] for v in client.trace[:index] if v.get('request_id') == event['request_id']] == [
                    'admit', 'request-evidence', 'reserve']
            if event['event'] == 'output-commit':
                assert client.trace[index - 1]['event'] == 'settle'
        for envelope in client.exchanges:
            body = envelope['body']
            assert body['stream'] is False and 'stream_options' not in body
            assert envelope['url'] == capacity.ENDPOINT + '/chat/completions'
            assert hashlib.sha256(envelope['serialized_body'].encode()).hexdigest() == envelope['body_sha256']
        passed('actual-SDK-parser-selector-chat-background-one-capacity-Ledger', projection=projection,
               exact_materials=consumed, outputs=result['outputs'])
        cases.append(save_case(root, 'normal', client, projection=projection, receipt=receipt))

        for scenario in capacity.SCENARIOS:
            if scenario == 'normal':
                continue
            client = new_client(root, scenario, scenario=scenario)
            if scenario == 'alias':
                consume(request(client))
                assert client.ledger.state['requests'][0]['usage'] == [100, 40, 20]
                passed('strict-cache-alias-accepted')
                cases.append(save_case(root, scenario, client))
                continue
            action = lambda: consume(request(client))
            if scenario == 'fingerprint-drift':
                consume(request(client))
            rejected(scenario, client, action, 0 if scenario == 'cancel-before' else
                     2 if scenario == 'fingerprint-drift' else 1)
            row = client.ledger.state['requests'][-1] if client.ledger.state['requests'] else None
            if scenario in ('missing', 'invalid', 'conflict-alias', 'total', 'unknown-usage', 'null-counter',
                            'null-cache', 'null-details', 'false-details', 'list-details', 'null-nested-cache',
                            'connection', '429', '500'):
                assert row['usage'] is None and row['charged'] == row['reserved']
            if scenario in ('cancel-after', 'model-drift', 'fingerprint-drift', 'length'):
                assert row['usage'] == [100, 40, 20] and row['charged'] == 282
            if scenario == 'joint-overreport':
                assert row['usage'][0] <= row['input_bound'] and row['usage'][2] <= row['allowance']
                assert client.ledger.state['closed'] == 'usage-overreport'

        # S + I + R limits, including equality and a second-role attempt.
        R = {'tokens': capacity.LIMITS['context'] + capacity.LIMITS['output'],
             'micro_cny': 2129920, 'attempts': 1}
        for scope in ('research', 'stages'):
            for metric in capacity.METRICS:
                name = scope + '-' + metric
                budgets = fixture_budgets()
                ceiling = budgets['research'] if scope == 'research' else budgets['stages']['formation']
                ceiling[metric] = R[metric]
                client = new_client(root, name, budgets=budgets)
                consume(request(client))  # Exactly the full reserve fits.
                rejected(name, client, lambda: consume(request(client)), 1)
                formula = client.trace[-1]['decision']['formulas'][
                    'research' if scope == 'research' else 'stages/formation'][metric]
                assert formula['S'] > 0 and formula['I'] == 0 and formula['R'] == R[metric] and not formula['ok']
                passed(name + '-S-plus-I-plus-R', formula=formula)
        budgets = fixture_budgets()
        budgets['research']['tokens'] = R['tokens'] + 119
        client = new_client(root, 'shared-cross-role-token', budgets=budgets)
        consume(request(client))
        rejected('shared-cross-role-token', client, lambda: consume(request(client, 'selector')), 1)
        budgets = fixture_budgets()
        budgets['research']['micro_cny'] = R['micro_cny'] - 1
        client = new_client(root, 'reserve-CNY-before-first-byte', budgets=budgets)
        rejected('reserve-CNY-before-first-byte', client, lambda: consume(request(client)), 0)

        client = new_client(root, 'inflight-projection')
        original_mock = client._mock
        inflight = []
        def inspect_inflight(http_request):
            decision = client.admission(client.active['attribution'])
            formula = decision['formulas']['research']['tokens']
            assert formula['S'] == 0 and formula['I'] == formula['R'] == R['tokens']
            assert not decision['allowed'] and 'shared-inflight' in decision['reasons']
            inflight.append(decision)
            return original_mock(http_request)
        # The transport itself is still the constructor's closed MockTransport.
        client.http._transport.handler = inspect_inflight
        consume(request(client))
        passed('same-Ledger-inflight-I-plus-next-capacity-R-blocks-admission', decision=inflight[0])
        cases.append(save_case(root, 'inflight-projection', client))

        client = new_client(root, 'manifest-role-stage', role_stages={'background-other': 'verification'})
        consume(request(client, 'background-other'))
        projected = client.projection()
        assert projected['scopes']['stages/verification']['S']['tokens'] == 120
        assert projected['scopes']['stages/formation']['S']['tokens'] == 0
        client.current['stage'] = 'formation'
        rejected('manifest-role-stage', client, lambda: consume(client.generate_response([
            {'role': 'system', 'content': 'Synthetic.'}, {'role': 'user', 'content': '{}'}], stream=False)), 1)
        passed('manifest-custom-role-stage-and-no-current-stage-override')

        # Real persistence failures at each responsibility boundary.
        for failure in ('request', 'response', 'observation', 'reserve', 'settle'):
            name = 'persistence-' + failure
            client = new_client(root, name)
            if failure in ('reserve', 'settle'):
                original = getattr(client.ledger, failure)
                def fail_ledger(*args, _original=original, **kwargs):
                    with patch.object(a0_ledger.os, 'fsync', side_effect=OSError('fictional fsync failure')):
                        return _original(*args, **kwargs)
                with patch.object(client.ledger, failure, fail_ledger):
                    rejected(name, client, lambda: consume(request(client)), 0 if failure == 'reserve' else 1, poison=True)
            else:
                original = capacity.durable
                def fail_evidence(path, value, _target=failure):
                    if path.name.startswith(_target + '-'):
                        raise OSError('fictional evidence failure')
                    return original(path, value)
                with patch.object(capacity, 'durable', fail_evidence):
                    rejected(name, client, lambda: consume(request(client)), 0 if failure == 'request' else 1, poison=True)
            assert (client.root / 'persistence-unknown.json').exists()
            try:
                a0_ledger.Ledger(client.root, client.ledger.state['pilot'], client.manifest_hash,
                    limits=capacity.LIMITS, prices=capacity.PRICES, policy=capacity.CapacityPolicy(client.contract), reopen=True)
            except ValueError:
                passed(name + '-reopen-refused')
            else:
                raise AssertionError('unknown persistence resumed')

        client = new_client(root, 'direct-sdk-outside-generation')
        def direct_sdk():
            try:
                with capacity.no_sdk_environment(client.environment_reads):
                    client.sdk.chat.completions.create(model=capacity.WIRE_MODEL,
                        messages=[{'role': 'user', 'content': 'Synthetic.'}], stream=False)
            except Exception:
                raise capacity.CapacityStop('direct-sdk-refused') from None
        rejected('direct-sdk-outside-generation', client, direct_sdk, 0)

        # Final HTTP hooks see drift after SDK serialization.
        for drift in ('route', 'method', 'model', 'body', 'role', 'wake', 'snapshot', 'duplicate-hook'):
            name = 'drift-' + drift
            client = new_client(root, name)
            original = client._before_http
            def mutate(request, _drift=drift):
                if _drift == 'route': request.url = request.url.copy_with(host='fictional.invalid')
                elif _drift == 'method': request.method = 'GET'
                elif _drift in ('model', 'body'):
                    body = json.loads(request.content)
                    body['model' if _drift == 'model' else 'max_tokens'] = 'drift' if _drift == 'model' else 1
                    request._content = canonical(body)
                elif _drift in ('role', 'wake', 'snapshot'):
                    client.current[{'role': 'role', 'wake': 'wake', 'snapshot': 'snapshot_id'}[_drift]] = 'drift'
                original(request)
                if _drift == 'duplicate-hook': original(request)
            client.http.event_hooks['request'] = [mutate]
            rejected(name, client, lambda: consume(request(client)), 0)
        for drift in ('manifest', 'code', 'sdk', 'config', 'ledger', 'attribution'):
            name = 'drift-' + drift
            client = new_client(root, name)
            with ExitStack() as stack:
                if drift == 'manifest':
                    value = deepcopy(client.manifest)
                    value['run_id'] = 'drift'
                    durable(client.root / 'manifest.json', value)
                elif drift == 'code':
                    stack.enter_context(patch.object(capacity, 'source_hashes', return_value={'drift': '0'}))
                elif drift == 'sdk':
                    client.clients['deepseek'].chat.completions.create = lambda **_: None
                elif drift == 'config': client.config['default_model'] = 'drift'
                elif drift == 'ledger':
                    durable(client.root / 'ledger.json', {'state': {}, 'sha256': 'drift'})
                else:
                    consume(request(client))
                    row = client.ledger.state['requests'][0]
                    saved = client.saved[row['id']]
                    value = deepcopy(saved['value'])
                    value['attribution']['role_id'] = 'selector'
                    durable(client.root / saved['file'], value)
                rejected(name, client, lambda: consume(request(client)), 1 if drift == 'attribution' else 0,
                         poison=drift == 'ledger')

        client = new_client(root, 'concurrent')
        first = request(client)
        next(first)  # Settled, but parser output generator still owns flight.
        rejected('concurrent', client, lambda: consume(request(client)), 1)
        try:
            next(first)
        except capacity.CapacityStop:
            passed('concurrent-closure-blocks-paused-buffer-release')
        else:
            raise AssertionError('paused generator released after shared closure')
        first.close()
        passed('concurrent-generation-never-constructs-second-SDK-request')
        client = new_client(root, 'generator-cancel')
        first = request(client)
        next(first)
        first.close()
        shared_closed(client)
        assert client.ledger.state['requests'][0]['status'] == 'settled'
        cases.append(save_case(root, 'generator-cancel', client, 'cancelled'))
        passed('generator-close-after-usage-keeps-settlement-and-closes-tools')
        client = new_client(root, 'completed-attribution-drift')
        first = request(client)
        next(first)
        next(first)
        client.current['wake'] = 'drift-after-last-yield'
        rejected('completed-attribution-drift', client, lambda: next(first), 1)
        client = new_client(root, 'parser-console-after-durable-settle')
        console_calls = []
        original_message = capacity.llm.console.message
        def inspect_console(*args, **kwargs):
            disk = json.loads((client.root / 'ledger.json').read_bytes())['state']
            assert disk['requests'][-1]['status'] == 'settled'
            assert (client.root / 'observation-0001.json').exists()
            console_calls.append(disk['requests'][-1]['id'])
            return original_message(*args, **kwargs)
        with patch.object(capacity.llm.console, 'message', inspect_console):
            consume(request(client))
        assert console_calls == [client.ledger.state['requests'][0]['id']]
        passed('production-parser-console-follows-durable-settle', request_ids=console_calls)
        cases.append(save_case(root, 'parser-console-after-durable-settle', client))
        counters.update(private_counters)
        assert all(value == 0 for value in counters.values()), counters
        passed('socket-QQ-external-write-zero', **counters)
    durable(root / 'parser-console.json', {'synthetic_console': console.getvalue()})
    result = {'schema': 'stateless-capacity-doctor-v1', 'status': 'ok', 'cases': cases,
              'assertions': assertions, 'assertion_count': len(assertions), 'sentinels': counters,
              'real_model': 0, 'real_HTTP': 0, 'QQ': 0, 'elapsed_seconds': time.perf_counter() - started,
              'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, 'limitations': LIMITATION}
    durable(root / 'doctor.json', result)
    return result


PARSER_CASES = ('length', 'stop', 'tool_calls', 'parser-error', 'missing', 'cancel-after',
                'diagnostic-write', 'response-write', 'reflected-finish', 'reflected-index',
                'reflected-type', 'reflected-memory', 'reflected-summary', 'memory-unavailable', 'closed-client')


def parser_doctor(root, selected=None):
    """Small selectable actual SDK/parser probes; no console body accumulation."""
    import stateless_official as official
    started, assertions = time.perf_counter(), []
    names = PARSER_CASES if selected is None else (selected,)
    with isolation(root) as (private, counters), open(os.devnull, 'w') as sink, redirect_stdout(sink):
        for name in names:
            if name == 'closed-client':
                client = new_client(root, name)
            else:
                folder, frozen = official_fixture(root, name)
                if name in ('reflected-index', 'reflected-memory', 'reflected-summary'):
                    key, scenario = '87654321', 'normal'
                    if name == 'reflected-summary':
                        # A safe tiny body can derive a forbidden hash without
                        # reflecting the credential anywhere in the raw JSON.
                        peer = capacity.SimpleNamespace(active={'attribution': {'role_id': 'librarian'}}, scenario='missing')
                        preview = capacity.CapacityClient._fixture_response(peer, {'messages': []})
                        preview['choices'][0]['index'] = 7
                        key, scenario = hashlib.sha256(httpx.Response(200, json=preview).content).hexdigest(), 'missing'
                    token = official._calibration_context.set((official._CALIBRATION, scenario))
                    try:
                        client = official.OfficialClient(folder, frozen, temporary_api_key=key)
                    finally:
                        official._calibration_context.reset(token)
                else:
                    client = official._calibration_client(folder, frozen,
                        scenario=name if name in ('length', 'missing', 'cancel-after') else 'normal')
            original_response, wire, dispatched = client._fixture_response, [], []
            def response(body):
                value = original_response(body)
                value['choices'][0]['index'] = 7  # Preserve wire index, never enumerate it.
                if name in ('tool_calls', 'diagnostic-write', 'response-write') and not any(v['role'] == 'tool' for v in body['messages']):
                    value['choices'][0].update(finish_reason='tool_calls', message={
                        'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'diagnostic-call', 'type': 'function',
                        'function': {'name': 'observe', 'arguments': '{"value":"fixture"}'}}]})
                if name == 'reflected-finish':
                    value['choices'][0]['finish_reason'] = client._temporary_key
                if name == 'reflected-index':
                    value['choices'][0]['index'] = int(client._temporary_key)
                # This is the exact byte serialization used by httpx.Response(json=...).
                raw = httpx.Response(200, json=value).content
                wire.append({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                             'choices': [{'index': v['index'], 'finish_reason': v['finish_reason']} for v in value['choices']]})
                return value
            def observe(value: str):
                client.guard_tools()
                disk = json.loads((client.root / 'ledger.json').read_bytes())['state']
                assert disk['requests'][-1]['status'] == 'settled'
                dispatched.append(value)
                return 'fixture-observed'
            class FixtureParserError(RuntimeError):
                pass
            error_type = type(client._temporary_key, (RuntimeError,), {}) if name == 'reflected-type' else FixtureParserError
            def fail_parser(*args, **kwargs):
                private_local = 'LOCAL-MUST-NOT-PERSIST'
                disk = json.loads((client.root / 'ledger.json').read_bytes())['state']
                assert disk['requests'][-1]['status'] == 'settled'
                assert private_local
                raise error_type('MESSAGE-MUST-NOT-PERSIST')
            original_durable = capacity.durable
            def fail_evidence(path, value):
                if path.name.startswith(('diagnostic' if name == 'diagnostic-write' else 'response') + '-'):
                    raise OSError('EVIDENCE-MESSAGE-MUST-NOT-PERSIST')
                return original_durable(path, value)
            failure = None
            with ExitStack() as stack:
                stack.enter_context(patch.object(client, '_fixture_response', response))
                if name in ('parser-error', 'reflected-type'):
                    stack.enter_context(patch.object(capacity.llm.console, 'message', fail_parser))
                if name in ('diagnostic-write', 'response-write'):
                    stack.enter_context(patch.object(capacity, 'durable', fail_evidence))
                if name in ('memory-unavailable', 'reflected-memory'):
                    stack.enter_context(patch.object(capacity, 'memory_sample', return_value={
                        'rss_kib': None, 'hwm_rss_kib': None, 'ru_maxrss_kib': 87654321 if name == 'reflected-memory' else None,
                        'proc_source': 'unavailable', 'ru_source': 'fixture' if name == 'reflected-memory' else 'unavailable'}))
                try:
                    if name in ('tool_calls', 'diagnostic-write', 'response-write'):
                        client.current = {'role': 'librarian', 'wake': 'fixture-wake', 'snapshot_id': 'fixture-snapshot'}
                        consume(client.chat([{'role': 'system', 'content': 'Synthetic only.'},
                            {'role': 'user', 'content': '{}'}], tools=[capacity.llm.Tool(observe)], stream=False))
                    else:
                        consume(request(client))
                except capacity.CapacityStop as error:
                    failure = str(error)
            normal = name in ('stop', 'tool_calls', 'memory-unavailable', 'closed-client')
            assert (failure is None) == normal, (name, failure)
            rows = client.ledger.state['requests']
            assert len(rows) == client.attempts == (2 if name == 'tool_calls' else 1)
            assert dispatched == (['fixture'] if name == 'tool_calls' else [])
            if name != 'response-write':
                assert all(row['status'] == 'settled' for row in rows)
                assert rows[0]['usage'] == (None if name in ('missing', 'reflected-summary') else [100, 40, 20])
                assert rows[0]['charged'] == (rows[0]['reserved'] if name in ('missing', 'reflected-summary') else 282)
            else:
                assert rows[0]['status'] == 'reserved' and 'usage' not in rows[0] and 'charged' not in rows[0]
            if not normal:
                shared_closed(client)
            if name.startswith('reflected-'):
                assert not list(client.root.glob('diagnostic-*.json'))
                if name in ('reflected-finish', 'reflected-index', 'reflected-summary'):
                    assert not list(client.root.glob('response-*.json'))
                if name == 'reflected-summary':
                    assert wire[0]['sha256'] == client._temporary_key
                    assert 'response_summary' not in json.loads((client.root / 'observation-0001.json').read_bytes())
            elif name not in ('response-write', 'diagnostic-write'):
                for index, expected in enumerate(wire, 1):
                    summary = json.loads((client.root / f'response-{index:04d}.json').read_bytes()) if name != 'missing' else (
                        json.loads((client.root / f'observation-{index:04d}.json').read_bytes())['response_summary'])
                    assert summary['choices'] == expected['choices']
                    assert summary['response_bytes'] == expected['bytes'] and summary['response_sha256'] == expected['sha256']
                    diagnostic = json.loads((client.root / f'diagnostic-{index:04d}.json').read_bytes())
                    assert diagnostic['request_id'] == rows[index - 1]['id']
                    stages = [v['stage'] for v in diagnostic['memory']]
                    assert 'generation-enter' in stages and 'sdk-create-exit' in stages
                    assert ('parser-failed' if failure else 'parser-exit') in stages
                    if name in ('length', 'parser-error'):
                        original = diagnostic['exception']
                        assert original['stage'] == 'response-parse'
                        assert original['type']['name'] == ('RuntimeError' if name == 'length' else 'FixtureParserError')
                        assert any(v['function'] == ('_non_stream_response' if name == 'length' else 'fail_parser') for v in original['frames'])
                        assert all(set(v) == {'file', 'function', 'line'} and '/' not in v['file'] for v in original['frames'])
                    if name == 'memory-unavailable':
                        assert all(v['rss_kib'] is v['hwm_rss_kib'] is v['ru_maxrss_kib'] is None for v in diagnostic['memory'])
            if name in ('response-write', 'diagnostic-write'):
                assert client.ledger.poisoned and (client.root / 'persistence-unknown.json').exists()
                before = (client.root / 'ledger.json').read_bytes()
                try:
                    a0_ledger.Ledger(client.root, frozen['run_id'], digest(frozen), limits=capacity.LIMITS,
                        prices=capacity.PRICES, policy=capacity.CapacityPolicy(client.contract), reopen=True)
                except ValueError:
                    pass
                else:
                    raise AssertionError('persistence unknown reopened')
                assert (client.root / 'ledger.json').read_bytes() == before
            if name != 'closed-client':
                assert all(not client.transport_evidence(row['id'])['sent'] for row in rows)
                key = client._temporary_key.encode()
                assert all(key not in path.read_bytes() for path in client.root.iterdir() if path.is_file())
            assert all(b'MESSAGE-MUST-NOT-PERSIST' not in path.read_bytes() and b'LOCAL-MUST-NOT-PERSIST' not in path.read_bytes()
                       for path in client.root.iterdir() if path.is_file())
            assertions.append(save_case(root, name, client, failure, tool_dispatch=dispatched))
        counters.update(private)
        assert all(value == 0 for value in counters.values()), counters
    result = {'status': 'ok', 'assertions': assertions, 'assertion_count': len(assertions), 'sentinels': counters,
              'elapsed_seconds': time.perf_counter() - started,
              'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'limitations': LIMITATION + ['VmHWM is current-mm high water; ru_maxrss may include a pre-exec peak; neither is per-request allocation.',
                  'New synthetic finish reasons do not establish the old real segment finish reason or memory cause.']}
    durable(root / 'parser-doctor.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('doctor', 'official-doctor', 'parser-doctor'))
    parser.add_argument('--case', choices=PARSER_CASES, help='one bounded parser-doctor probe')
    parser.add_argument('--output', required=True, help='fresh external synthetic evidence directory')
    args = parser.parse_args(argv)
    root = _safe_root(args.output)
    root.mkdir(parents=True, mode=0o700)
    if args.case and args.command != 'parser-doctor':
        parser.error('--case is only supported by parser-doctor')
    result = parser_doctor(root, args.case) if args.command == 'parser-doctor' else (
        doctor if args.command == 'doctor' else official_doctor)(root)
    print(json.dumps({k: result[k] for k in ('status', 'assertion_count', 'elapsed_seconds', 'peak_rss_kib', 'sentinels')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
