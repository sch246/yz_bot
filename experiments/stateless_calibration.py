"""Closed synthetic two-wake calibration: real SDK/parser, no real network."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import resource
import socket
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from a0_ledger import LIMITS, PRICES, Ledger, canonical, digest, durable
from a0_sdk_calibration import no_sdk_environment, normalize_usage
from comparison_budget import STAGES, project_costs, admit_request
from comparison_snapshot import freeze_sources, open_snapshot, resolve_ref, _safe_root
from stateless_loop import build_screen, drive_wake, recover_commits, finish_wake, material_values, selected_context
from mods import llm
import httpx
import openai

MODEL = 'deepseek/deepseek-flash'
WIRE_MODEL = 'deepseek-flash'
URL = 'https://stateless-loop.invalid'
SCENARIOS = ('normal', 'materials', 'future-tool', 'construct-failure', 'connection', 'length', 'missing', 'conflict', 'overreport',
             'cancel-request', 'reserve-fsync', 'settle-fsync', 'request-evidence-failure',
             'output-failure', 'output-flush', 'output-fsync', 'output-after-rename',
             'stop-before-tool', 'cancel-before-tool', 'cancel-after-tool', 'result-failure', 'budget-second-wake')


def synthetic_fixture():
    """Only invented group records; private input is outside T07's capability."""
    def record(name, author, clock, number, text):
        return f'【】{name}({author}) {clock} | {number}\n    {text}\n'.encode()
    rows = [
        ('alpha', record('Same', 101, '12:00:00', 7, 'TRUNCATED_EARLY')),
        ('alpha', record('Same', 101, '12:00:01', 8, 'OLD_FACT_A')),
        ('alpha', record('Same', 101, '12:00:02', 9, 'CORRECTION_B [CQ:at,qq=900]')),
        ('beta', record('Same', 202, '12:00:02', 9, 'OTHER_WINDOW_SAME_SECOND')),
        ('beta', record('Same', 202, '12:00:02', 9, 'DUPLICATE_ID_SECOND_ORIGIN')),
        ('beta', record('Late', 203, '11:59:59', 10, 'LATE_OLD_TIME')),
        ('beta', record('Same', 202, '12:00:04', 11, 'SECOND_WAKE')),
        ('alpha', record('FutureNameOnly', 204, '12:00:05', 12, 'FUTURE_BODY_ONLY')),
        ('future-directory-only', record('FutureDirectoryName', 205, '12:00:06', 13, 'FUTURE_DIRECTORY_BODY'))]
    sources = [{'source_alias': alias, 'target': index,
                'days': {'2026-01-01': b''.join(raw for source, raw in rows if source == alias)}}
               for index, alias in enumerate(('alpha', 'beta', 'future-directory-only'), 1001)]
    frozen = freeze_sources(sources, bot={'id': 900, 'name': 'FixtureBot', 'names': {'FixtureBot': 900}}, synthetic=True)
    by_body = {event['record']['message']: event['event_key'] for event in frozen['events']}
    def key(marker):
        return next(value for text, value in by_body.items() if marker in text)
    first = [event['event_key'] for event in frozen['events'] if event['source_alias'] != 'future-directory-only'
             and event['event_key'] not in {key('LATE_OLD_TIME'), key('SECOND_WAKE'), key('FUTURE_BODY_ONLY')}]
    base = max(event['record']['time'] for event in frozen['events'])
    schedule = [{'opened_cursor': 1, 'virtual_time': base, 'event_keys': first},
                {'opened_cursor': 2, 'virtual_time': base + 1, 'event_keys': [key('LATE_OLD_TIME'), key('SECOND_WAKE')]},
                {'opened_cursor': 3, 'virtual_time': base + 2,
                 'event_keys': [key('FUTURE_BODY_ONLY'), key('FUTURE_DIRECTORY_BODY')]}]
    wakes = [{'id': 'wake-alpha', 'reason': 'synthetic-mention', 'window': 'alpha', 'cursor': 1, 'event_key': key('CORRECTION_B')},
             {'id': 'wake-beta', 'reason': 'synthetic-mention', 'window': 'beta', 'cursor': 2, 'event_key': key('SECOND_WAKE')},
             {'id': 'wake-after-freeze', 'reason': 'synthetic-mention', 'window': 'alpha', 'cursor': 3, 'event_key': key('FUTURE_BODY_ONLY')}]
    return frozen, schedule, wakes


def code_hashes():
    repo = Path(__file__).resolve().parents[1]
    files = sorted((repo / 'mods').rglob('*.py')) + [repo / 'experiments' / name for name in (
        'stateless_loop.py', 'stateless_calibration.py', 'comparison_snapshot.py', 'comparison_budget.py',
        'a0_ledger.py', 'a0_sdk_calibration.py', 'a0_calibration.py', 'memory_replay.py')]
    return {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


class ClosedClient(llm.LLMClient):
    """No constructor argument can supply an endpoint, credential or transport."""
    def __init__(self, root, manifest, scenario):
        self.root, self.scenario = root, scenario
        self.trace, self.chats, self.exchanges = [], [], []
        self.attempts, self.settlements = 0, 0
        self.active, self.current = None, None
        self.lock = threading.Lock()
        self.config = {'default_model': MODEL, 'providers': {'deepseek': {'models': {WIRE_MODEL: {'function_calling': True}}}}}
        self.strict_tools = True
        self.request_policy = lambda *_: {'max_tokens': 4096}
        self.environment_reads = []
        self.ledger = Ledger(root, 'synthetic-stateless-loop', digest(manifest))
        self.evidence = {}
        self.ceiling = {'tokens': 1000000, 'micro_cny': 5000000, 'attempts': 12}
        self.identity = {'pilot': self.ledger.state['pilot'], 'manifest': self.ledger.state['manifest'],
                         'prices_sha256': digest(PRICES), 'limits_sha256': digest(LIMITS)}
        with no_sdk_environment(self.environment_reads):
            self.http = httpx.Client(transport=httpx.MockTransport(self._mock), trust_env=False,
                                     follow_redirects=False, event_hooks={'request': [self._before_http]})
            self.sdk = openai.OpenAI(api_key='sk-fictional-stateless-loop', admin_api_key='fictional-admin',
                                    organization='fictional-org', project='fictional-project', webhook_secret='fictional-webhook',
                                    base_url=URL, http_client=self.http, max_retries=0, timeout=1)
        self.clients = {'deepseek': SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self._create)))}

    def projection(self):
        disk = json.loads((self.root / 'ledger.json').read_bytes())
        if disk != {'state': self.ledger.state, 'sha256': digest(self.ledger.state)}:
            raise ValueError('fresh Ledger disk/memory drift')
        self.ledger.validate()
        rows = []
        for row in disk['state']['requests']:
            saved = json.loads((self.root / self.evidence[row['id']]).read_bytes())
            if saved != self.saved[row['id']]:
                raise ValueError('request attribution evidence drift')
            rows.append({'ledger_id': 'shared', 'row': row, 'envelope': saved['envelope'], 'attribution': saved['attribution']})
        limits = {**self.ceiling}
        if self.scenario == 'budget-second-wake':
            limits['attempts'] = 2
        return project_costs(rows, {'ledger_slices': {'shared': {**disk, 'poisoned': self.ledger.poisoned}},
            'branches': {'main': {'run_id': 'two-wake', 'parent_checkpoint_id': None,
                                  'request_ids': [row['id'] for row in disk['state']['requests']]}}, 'checkpoints': {}},
            {'synthetic': True, 'ledgers': {'shared': self.identity},
             'attributions': {row['envelope']['request_id']: digest(row['attribution']) for row in rows},
             'limits': {'research': limits, 'stages': {stage: dict(self.ceiling) for stage in STAGES}}})

    def _before_http(self, request):
        if self.active is None or self.active['row'] is not None:
            raise RuntimeError('request outside single serial generation')
        body, raw = json.loads(request.content), request.content
        if request.method != 'POST' or str(request.url) != URL + '/chat/completions' or body['stream'] is not False:
            raise ValueError('closed wire contract drift')
        request_id = f"{self.ledger.state['pilot']}/{len(self.ledger.state['requests']) + 1:04d}"
        attr = {'role_id': self.current.get('role', 'chat'), 'task_id': self.current['wake'], 'run_id': 'two-wake', 'branch_id': 'main',
                'parent_checkpoint_id': None, 'stage': 'formation', 'snapshot_id': self.current['snapshot_id']}
        envelope = {'request_id': request_id, 'method': request.method, 'url': str(request.url),
                    'body': body, 'serialized_body': raw.decode(), 'body_sha256': hashlib.sha256(raw).hexdigest()}
        projection = self.projection()
        decision = admit_request(projection, {'ledger_id': 'shared', 'request': envelope, 'allowance': 4096,
            'attribution': attr, 'ledger_cut': projection['ledger_cut'],
            'current_ledger_slices': projection['source']['lineage']['ledger_slices'], 'mode': 'serial'})
        self.trace.append({'event': 'admit', 'request_id': request_id, 'decision': decision})
        if not decision['allowed'] or decision['send_authorized'] is not False:
            raise RuntimeError('common budget refused: ' + ', '.join(decision['reasons']))
        number = len(self.ledger.state['requests']) + 1
        filename = f'request-{number:04d}.json'
        evidence = {'envelope': envelope, 'attribution': attr}
        if self.scenario == 'request-evidence-failure':
            raise OSError('synthetic request evidence failure')
        durable(self.root / filename, evidence)
        self.evidence[request_id] = filename
        self.saved[request_id] = deepcopy(evidence)
        self.trace.append({'event': 'request-evidence', 'request_id': request_id})
        if self.scenario == 'reserve-fsync':
            with patch('a0_ledger.os.fsync', side_effect=OSError('synthetic reserve fsync failure')):
                row = self.ledger.reserve(envelope, 4096)
        else:
            row = self.ledger.reserve(envelope, 4096)
        self.active['row'] = row
        self.exchanges.append(envelope)
        self.trace.append({'event': 'reserve', 'request_id': request_id})

    def _create(self, **params):
        if self.active is None:
            raise RuntimeError('SDK outside declared generation')
        with no_sdk_environment(self.environment_reads):
            response = self.sdk.chat.completions.create(**params)
        raw = response.usage.model_dump(exclude_none=True) if response.usage else None
        self.active['raw_usage'] = raw
        if self.scenario == 'cancel-request':
            raise RuntimeError('synthetic cancellation after usage before parser release')
        return response

    def _mock(self, request):
        row = self.active['row']
        disk = json.loads((self.root / 'ledger.json').read_bytes())['state']['requests'][-1]
        saved = json.loads((self.root / self.evidence[row['id']]).read_bytes())
        if (self.active['attempted'] or row != disk or disk['status'] != 'reserved'
                or request.content.decode() != saved['envelope']['serialized_body']):
            raise RuntimeError('mock without unique durable reservation/evidence')
        self.active['attempted'] = True
        self.attempts += 1
        self.trace.append({'event': 'mock', 'request_id': row['id']})
        if self.scenario == 'connection':
            raise httpx.ConnectError('synthetic connection failure', request=request)
        messages = json.loads(request.content)['messages']
        initial = not any(item['role'] == 'tool' for item in messages)
        if initial:
            context = json.loads(messages[1]['content'])
            reference = deepcopy(context['screen']['records'][0]['ref'])
            if self.scenario == 'future-tool':
                reference.update(self.future_reference)
            calls = [{'id': f"{self.current['wake']}-call-{i}", 'type': 'function',
                      'function': {'name': 'read_reference', 'arguments': canonical(reference).decode()}} for i in (1, 2)]
            message = {'role': 'assistant', 'content': '', 'reasoning_content': f"EPHEMERAL_{self.current['wake']}", 'tool_calls': calls}
        else:
            message = {'role': 'assistant', 'content': f"COMPLETED_{self.current['wake']}",
                       'reasoning_content': f"FINAL_EPHEMERAL_{self.current['wake']}"}
        usage = {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120, 'prompt_cache_hit_tokens': 40}
        if self.scenario == 'missing':
            usage = None
        elif self.scenario == 'conflict':
            usage['prompt_tokens_details'] = {'cached_tokens': 41}
        elif self.scenario == 'overreport':
            usage.update(completion_tokens=4097, total_tokens=4197)
        return httpx.Response(200, json={'id': 'synthetic-completion', 'object': 'chat.completion', 'created': 1767225600,
            'model': WIRE_MODEL, 'choices': [{'index': 0, 'finish_reason': 'length' if self.scenario == 'length' else
                'tool_calls' if initial else 'stop', 'message': message}], 'usage': usage})

    def generate_response(self, *args, **kwargs):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('concurrent generation refused')
        self.saved = getattr(self, 'saved', {})
        self.active = {'row': None, 'raw_usage': None, 'attempted': False}
        buffered, error, completed = [], None, None
        inner = None
        try:
            self.ledger.guard()
            inner = super().generate_response(*args, **kwargs)
            while True:
                try:
                    buffered.append(next(inner))
                except StopIteration as stop:
                    completed = stop.value
                    break
        except BaseException as failure:
            error = failure
        finally:
            if inner is not None:
                inner.close()
            try:
                row, raw = self.active['row'], self.active['raw_usage']
                if row is not None:
                    usage = None
                    outcome = type(error).__name__ if error else 'stop'
                    if raw is not None:
                        try:
                            usage = normalize_usage(raw)
                        except (ValueError, KeyError, TypeError):
                            outcome = 'invalid-usage'
                    durable(self.root / f"observation-{len(self.ledger.state['requests']):04d}.json",
                            {'request_id': row['id'], 'raw_usage': raw, 'usage': usage, 'outcome': outcome})
                    self.settlements += 1
                    if self.scenario == 'settle-fsync':
                        with patch('a0_ledger.os.fsync', side_effect=OSError('synthetic settle fsync failure')):
                            self.ledger.settle(row, usage, outcome)
                    else:
                        self.ledger.settle(row, usage, outcome)
                    self.trace.append({'event': 'settle', 'request_id': row['id']})
            finally:
                self.active = None
                self.lock.release()
        if error is not None:
            raise error
        for value in buffered:
            yield value
        return completed

    def guard_tools(self):
        self.ledger.guard()
        projection = self.projection()
        if projection['blockers']:
            raise RuntimeError('common failure domain closed')

    def close(self):
        self.sdk.close()


def run_synthetic_session(frozen_sources, schedule, wakes, budget_fixture, output, *, scenario='normal', materials_by_wake=None):
    """In-memory synthetic inputs only. Fresh external output, no resume/send API."""
    if scenario not in SCENARIOS or budget_fixture != 'synthetic-default':
        raise ValueError('only built-in synthetic scenario/default Ledger policy')
    if len(wakes) < 2 or len({wake['id'] for wake in wakes}) != len(wakes):
        raise ValueError('two distinct explicit wakes required')
    if materials_by_wake is None:
        materials_by_wake = {}
        if scenario == 'materials':
            for wake in wakes[:2]:
                snapshot = open_snapshot(frozen_sources, schedule, wake['cursor'])
                materials_by_wake[wake['id']] = []
                for kind in ('note', 'selector_inference'):
                    raw = f"{kind.upper()}_{wake['id']}_EXACT_BYTES\r\nfictional version\n".encode()
                    materials_by_wake[wake['id']].append({'kind': kind, 'source_id': 'fixture-' + kind,
                        'version_id': 'fixture-version-1', 'snapshot_id': snapshot['snapshot_id'],
                        'sha256': hashlib.sha256(raw).hexdigest(), 'raw': raw})
    if not isinstance(materials_by_wake, dict) or set(materials_by_wake) - {wake['id'] for wake in wakes[:2]}:
        raise ValueError('materials must name an explicit session wake')
    frozen_materials = {key: tuple(dict(item) for item in values) for key, values in materials_by_wake.items()}
    material_manifest = {wake['id']: material_values(open_snapshot(frozen_sources, schedule, wake['cursor']),
                          frozen_materials.get(wake['id'], ())) for wake in wakes[:2]}
    root = _safe_root(output)
    root.mkdir(parents=True, mode=0o700)
    started = time.perf_counter()
    manifest = {'schema': 'stateless-loop-v1', 'synthetic': True, 'scenario': scenario,
                'source_sha256': frozen_sources['frozen_id'], 'schedule_sha256': digest(schedule),
                'wakes_sha256': digest(wakes), 'materials_sha256': digest(material_manifest), 'code': code_hashes(), 'openai': openai.__version__, 'httpx': httpx.__version__,
                'input_bound': 'canonical full envelope bytes + 1024; synthetic only',
                'transport': 'closed MockTransport', 'reasoning_across_wakes': False,
                'roles': {'chat': 'synthetic SDK fixture', 'selector': 'explicit references only', 'librarian': 'not implemented'}}
    durable(root / 'manifest.json', manifest)
    client = ClosedClient(root, manifest, scenario)
    if scenario == 'future-tool':
        visible = set(open_snapshot(frozen_sources, schedule, wakes[0]['cursor'])['visible_event_keys'])
        event = next(event for event in frozen_sources['events'] if event['event_key'] not in visible)
        client.future_reference = {'source_alias': event['source_alias'], 'event_key': event['event_key']}
    outcomes, failure, terminal_failure = [], None, None
    try:
        for wake in wakes[:2]:
            client.ledger.wake()
            snapshot = open_snapshot(frozen_sources, schedule, wake['cursor'])
            # A committed output is deliberately selected as plain sourced fact;
            # no previous messages, native results or reasoning are reused.
            prior = recover_commits(root)
            actions = list(prior['outputs'])
            references = []
            if prior['generation']:
                old_screen = next(value['screen'] for value in reversed(list(prior['outputs'].values())) if value['screen'])
                references = [{**old_screen['records'][-1]['ref'], 'snapshot_id': snapshot['snapshot_id']}]
            result = drive_wake(snapshot, wake, wakes, root, client, references=references, actions=actions, materials=frozen_materials.get(wake['id'], ()), scenario=scenario)
            outcomes.append(result)
            request_ids = [event['request_id'] for event in client.trace if event['event'] == 'mock'
                           and client.saved[event['request_id']]['attribution']['task_id'] == wake['id']]
            finish_wake(root, wake['id'], 'cancelled' if result['cancelled'] else 'complete', request_ids)
            if result['cancelled']:
                client.ledger.stop('cancelled')
                break
    except (Exception,) as error:
        failure = {'type': type(error).__name__, 'reason': str(error)}
        if not client.ledger.poisoned and not client.ledger.state['closed']:
            client.ledger.stop('session-failure')
        try:
            request_ids = [event['request_id'] for event in client.trace if event['event'] == 'mock'
                           and client.saved[event['request_id']]['attribution']['task_id'] == wake['id']]
            finish_wake(root, wake['id'], 'failed', request_ids, reason=failure['type'])
        except (OSError, ValueError, KeyError) as terminal_error:
            terminal_failure = type(terminal_error).__name__ + ': ' + str(terminal_error)
    finally:
        client.close()
    shared_guard_blocked = None
    if failure or any(item['cancelled'] for item in outcomes):
        before = (client.attempts, client.settlements, len(client.ledger.state['requests']), len(client.trace))
        client.current = {'role': 'librarian', 'wake': 'blocked-role-probe', 'snapshot_id': 'synthetic-blocked'}
        try:
            list(client.generate_response([{'role': 'user', 'content': 'synthetic blocked role'}], model=MODEL, stream=False))
        except RuntimeError as guard_error:
            shared_guard_blocked = (before == (client.attempts, client.settlements, len(client.ledger.state['requests']), len(client.trace))
                                    and str(guard_error).startswith('pilot closed:'))
        else:
            shared_guard_blocked = False
        if not shared_guard_blocked:
            raise AssertionError('role switch bypassed common stop')
    try:
        recovered = recover_commits(root)
        recovery_failure = None
    except (ValueError, OSError, KeyError) as error:
        recovered, recovery_failure = None, str(error)
    try:
        projection = client.projection()
    except (ValueError, KeyError) as error:
        projection = {'error': str(error)}
    report = {'schema': 'stateless-calibration-v1', 'status': 'stopped' if failure or any(item['cancelled'] for item in outcomes) else 'complete',
              'scenario': scenario, 'failure': failure, 'wakes': outcomes, 'attempts': client.attempts,
              'settlements': client.settlements, 'ledger': deepcopy(client.ledger.state), 'costs': projection,
              'shared_failure_guard_blocked': shared_guard_blocked, 'terminal_failure': terminal_failure,
              'trace': client.trace, 'recovery': recovered, 'recovery_failure': recovery_failure,
              'pending_wakes': [wake['id'] for wake in wakes if not recovered or wake['id'] not in recovered['acknowledged']],
              'chat_objects_distinct': len(client.chats) < 2 or client.chats[0] is not client.chats[1],
              'messages_objects_distinct': len(client.chats) < 2 or client.chats[0].messages is not client.chats[1].messages,
              'elapsed_seconds': time.perf_counter() - started, 'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'real_HTTP': 0, 'QQ': 0,
              'limitations': ['MockTransport/nonstream only; no provider acceptance or semantic memory evidence',
                              'two explicit wakes; no real scheduler/concurrent writer', 'no selector/librarian model or note writes',
                              'group-only T07 fixture; private identity behavior not exercised',
                              'read-only recovery; no request, tool replay, supplier resume/fork',
                              'fault injection is not physical power-loss proof']}
    durable(root / 'result.json', report)
    return report


@contextmanager
def sentinels(root):
    """Block sockets, QQ and writes outside this disposable synthetic root."""
    from mods import connect, message
    counters = {'socket': 0, 'QQ': 0, 'external_write': 0}
    active = [True]
    def audit(event, args):
        if not active[0]:
            return
        paths = []
        if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
                paths.append(args[0])
        elif event in {'os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod', 'os.truncate'}:
            paths.append(args[0])
        elif event in {'os.rename', 'os.link', 'os.symlink'}:
            paths.extend(args[:2])
        for value in paths:
            path = Path(os.fsdecode(value)).absolute()
            if path != Path(os.devnull) and not path.resolve().is_relative_to(root):
                counters['external_write'] += 1
                raise RuntimeError('blocked synthetic write outside output root')
    sys.addaudithook(audit)
    def block_socket(*_, **__):
        counters['socket'] += 1
        raise RuntimeError('blocked real socket')
    def block_qq(*_, **__):
        counters['QQ'] += 1
        raise RuntimeError('blocked QQ')
    try:
        with ExitStack() as stack:
            for name in ('connect', 'connect_ex'):
                stack.enter_context(patch.object(socket.socket, name, block_socket))
            for name in ('create_connection', 'getaddrinfo'):
                stack.enter_context(patch.object(socket, name, block_socket))
            for name in ('send', 'sendmsg', '_send_now'):
                stack.enter_context(patch.object(message, name, block_qq))
            stack.enter_context(patch.object(connect, 'call_api', block_qq))
            yield counters
    finally:
        active[0] = False


def doctor(root):
    frozen, schedule, wakes = synthetic_fixture()
    assertions, cases = [], {}
    def passed(name, **details):
        assertions.append({'assertion': name, 'status': 'pass', **details})
    with sentinels(root) as counters, redirect_stdout(io.StringIO()) as console:
        for scenario in SCENARIOS:
            report = run_synthetic_session(frozen, schedule, wakes, 'synthetic-default', root / scenario, scenario=scenario)
            cases[scenario] = report
            recovered = report['recovery']
            if scenario not in {'normal', 'materials'}:
                assert report['shared_failure_guard_blocked'] is True
            if scenario in {'normal', 'materials'}:
                assert report['status'] == 'complete' and report['attempts'] == report['settlements'] == 4
                assert report['chat_objects_distinct'] and report['messages_objects_distinct']
                assert recovered['generation'] == 2 and recovered['acknowledged'] == ['wake-alpha', 'wake-beta']
                assert {key: value['status'] for key, value in recovered['terminals'].items()} == {'wake-alpha': 'complete', 'wake-beta': 'complete'}
                assert report['pending_wakes'] == ['wake-after-freeze']
                wire = [json.loads((root / scenario / f'request-{i:04d}.json').read_bytes())['envelope']['body'] for i in range(1, 5)]
                assert len(wire[0]['messages']) == len(wire[2]['messages']) == 2
                assert all(item['role'] in {'system', 'user'} for item in wire[2]['messages'])
                assert 'EPHEMERAL_wake-alpha' not in canonical(wire[2]).decode()
                assert 'COMPLETED_wake-alpha' in canonical(wire[2]).decode()  # Explicit committed action.
                assert 'CORRECTION_B' in canonical(wire[0]).decode()
                for body in wire:
                    text = canonical(body).decode()
                    for marker in ('FUTURE_BODY_ONLY', 'FutureNameOnly', 'future-directory-only', 'FUTURE_DIRECTORY_BODY', 'FutureDirectoryName'):
                        assert marker not in text
                for body, wake in ((wire[1], wakes[0]), (wire[3], wakes[1])):
                    assistant = next(item for item in body['messages'] if item.get('tool_calls'))
                    assert assistant['reasoning_content'] == f"EPHEMERAL_{wake['id']}"
                    calls = assistant['tool_calls']
                    for call in calls:
                        result = next(item for item in body['messages'] if item.get('tool_call_id') == call['id'])
                        content = json.loads(result['content'])
                        snapshot = open_snapshot(frozen, schedule, wake['cursor'])
                        exact = resolve_ref(snapshot, json.loads(call['function']['arguments']))
                        assert content['raw'].encode() == exact['raw'] and content['sha256'] == exact['sha256']
                assert len(recovered['results']) == 4 and set(recovered['actions'].values()) == {'completed'}
                assert all('reasoning_content' not in value['assistant'] for value in recovered['outputs'].values())
                if scenario == 'materials':
                    for body, wake in ((wire[0], wakes[0]), (wire[2], wakes[1])):
                        selected = json.loads(body['messages'][1]['content'])['selected']['materials']
                        assert len(selected) == 2
                        for value in selected:
                            raw = f"{value['kind'].upper()}_{wake['id']}_EXACT_BYTES\r\nfictional version\n".encode()
                            assert value['text'].encode() == raw and value['sha256'] == hashlib.sha256(raw).hexdigest()
                            assert value['snapshot_id'] == json.loads(body['messages'][1]['content'])['screen']['snapshot_id']
                            assert value['version_id'] == 'fixture-version-1' and value['source_id'] == 'fixture-' + value['kind']
                            assert value['interpretation'] == ('inference-not-evidence' if value['kind'] == 'selector_inference' else 'versioned-note')
                    passed('actual-wire-exact-frozen-note-and-labelled-selector-inference')
                passed('two-new-Chats-wire-isolation-and-explicit-committed-history')
                passed('actual-SDK-production-parser-native-pairs-and-exact-T07-tool-results')
                assert report['costs']['scopes']['research']['actual']['tokens'] == 480
                assert report['costs']['scopes']['research']['actual']['attempts'] == 4
                passed('cumulative-two-wake-original-Ledger-and-common-costs', tokens=480,
                       micro_cny=report['costs']['scopes']['research']['actual']['micro_cny'])
            elif scenario == 'future-tool':
                assert report['failure'] and report['attempts'] == 1
                assert list(recovered['actions'].values()) == ['failed', 'not-dispatched']
                assert not any(item['event'] == 'tool' for item in report['trace'])
                passed('actual-SDK-tool-refuses-unopened-original-and-stops-following-tool')
            elif scenario in {'output-flush', 'output-fsync', 'output-after-rename'}:
                assert report['failure'] and report['recovery_failure'] and report['attempts'] == 1
                assert not any(item['event'] == 'tool' for item in report['trace'])
                passed(scenario + '-preserves-failed-original-and-refuses-unknown-durability')
            elif scenario in {'construct-failure', 'connection', 'length', 'missing', 'conflict', 'overreport',
                              'cancel-request', 'reserve-fsync', 'settle-fsync', 'request-evidence-failure', 'output-failure'}:
                assert report['failure'] and recovered['boundaries'] == {} and recovered['acknowledged'] == []
                assert not recovered['actions'] and not any(item['event'] == 'tool' for item in report['trace'])
                assert report['ledger']['closed'] or scenario in {'reserve-fsync', 'settle-fsync'}
                assert report['attempts'] == (0 if scenario in {'construct-failure', 'reserve-fsync', 'request-evidence-failure'} else 1)
                if scenario in {'missing', 'conflict', 'connection'}:
                    row = report['ledger']['requests'][0]
                    assert row['usage'] is None and row['charged'] == row['reserved']
                passed(scenario + '-no-output-boundary-ack-or-tool-release')
            elif scenario in {'stop-before-tool', 'cancel-before-tool', 'cancel-after-tool', 'result-failure'}:
                assert recovered['acknowledged'] == ['wake-alpha'] and recovered['generation'] == 1
                assert report['pending_wakes'] == ['wake-beta', 'wake-after-freeze']
                statuses = list(recovered['actions'].values())
                if scenario in {'stop-before-tool', 'cancel-before-tool'}:
                    assert statuses == ['not-dispatched', 'not-dispatched']
                elif scenario == 'cancel-after-tool':
                    assert statuses == ['completed', 'not-dispatched']
                else:
                    assert statuses == ['unknown', 'not-dispatched'] and report['failure']
                assert report['attempts'] == 1
                passed(scenario + '-committed-frozen-boundary-and-unreplayed-actions', states=statuses)
            elif scenario == 'budget-second-wake':
                assert report['failure'] and report['attempts'] == report['settlements'] == 2
                assert recovered['generation'] == 1 and recovered['acknowledged'] == ['wake-alpha']
                decision = [item for item in report['trace'] if item['event'] == 'admit'][-1]['decision']
                assert not decision['allowed'] and decision['send_authorized'] is False
                assert decision['formulas']['research']['attempts'] == {'S': 2, 'I': 0, 'R': 1, 'total': 3, 'limit': 2, 'ok': False}
                passed('second-wake-common-limit-before-mock-without-reset', formula=decision['formulas']['research']['attempts'])
            # Every actual mock must have evidence and reserve first; settlement
            # must precede any action generated by that request.
            events = report['trace']
            for index, event in enumerate(events):
                if event['event'] == 'mock':
                    names = [item['event'] for item in events[:index] if item.get('request_id') == event['request_id']]
                    assert names == ['admit', 'request-evidence', 'reserve']
                if event['event'] == 'output-commit':
                    assert events[index - 1]['event'] == 'settle'
                if event['event'] == 'tool':
                    assert events[index - 1]['event'] == 'dispatch'
                    assert any(item['event'] == 'output-commit' for item in events[:index])
            if report['failure']:
                # Try a different role against the stopped/unknown shared cut.
                # No new client, request, reserve or transport is constructed.
                source = report['costs'].get('source')
                if source is not None:
                    projection = project_costs(**source)
                    state = projection['source']['lineage']['ledger_slices']['shared']['state']
                    attr = {'role_id': 'librarian', 'task_id': 'cannot-escape', 'run_id': 'two-wake', 'branch_id': 'main',
                            'parent_checkpoint_id': None, 'stage': 'verification'}
                    decision = admit_request(projection, {'ledger_id': 'shared', 'request': {'request_id':
                        f"{state['pilot']}/{len(state['requests']) + 1:04d}"}, 'allowance': 4096, 'attribution': attr,
                        'ledger_cut': projection['ledger_cut'], 'current_ledger_slices': source['lineage']['ledger_slices'], 'mode': 'serial'})
                    assert not decision['allowed']
        snapshot = open_snapshot(frozen, schedule, 1)
        screen = build_screen(snapshot, wakes[0])
        assert screen['truncated_count'] == 1 and 'TRUNCATED_EARLY' not in canonical(screen).decode()
        for event in frozen['events']:
            if event['event_key'] not in snapshot['visible_event_keys']:
                try:
                    resolve_ref(snapshot, {'snapshot_id': snapshot['snapshot_id'], 'source_alias': event['source_alias'], 'event_key': event['event_key']})
                except ValueError:
                    pass
                else:
                    raise AssertionError('future reference accepted')
        try:
            resolve_ref(snapshot, {'snapshot_id': snapshot['snapshot_id'], 'source_alias': 'beta', 'message_id': 9})
        except ValueError:
            pass
        else:
            raise AssertionError('ambiguous duplicate ID accepted')
        second = open_snapshot(frozen, schedule, 2)
        late = next(event for event in frozen['events'] if event['record'].get('message') == 'LATE_OLD_TIME')
        assert late['event_key'] in second['visible_event_keys'] and late['record']['time'] < screen['boundary']['time']
        authors = {event['record'].get('user_id') for event in frozen['events'] if event['record'].get('sender', {}).get('nickname') == 'Same'}
        assert authors == {101, 202}
        passed('same-second-two-windows-late-truncation-duplicate-ID-same-name-and-future-read-refusals')
        # Read-only means even the ability to request, invoke, or write is denied.
        before = {str(p.relative_to(root / 'normal')): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (root / 'normal').rglob('*') if p.is_file()}
        def forbidden(*_, **__):
            raise AssertionError('recovery invoked request/tool/write')
        with patch.object(ClosedClient, '__init__', forbidden), patch.object(ClosedClient, '_create', forbidden), patch.object(llm.Tool, '__init__', forbidden), \
             patch.object(Path, 'write_bytes', forbidden), patch.object(Path, 'write_text', forbidden):
            assert recover_commits(root / 'normal') == cases['normal']['recovery']
        after = {str(p.relative_to(root / 'normal')): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (root / 'normal').rglob('*') if p.is_file()}
        assert before == after
        passed('read-only-recovery-request-tool-write-sentinels-and-all-bytes-unchanged')
        # Corrupt and stale candidates live in independent disposable copies.
        import shutil
        for mode in ('checksum', 'stale-generation', 'subrequest-identity', 'residual-tail'):
            target = root / ('recovery-' + mode)
            shutil.copytree(root / 'normal' / 'commits', target / 'commits')
            path = target / 'commits' / ('000006.json' if mode == 'subrequest-identity' else '000001.json')
            envelope = json.loads(path.read_bytes())
            if mode == 'checksum':
                envelope['record']['value']['generation'] = 999
            elif mode == 'stale-generation':
                envelope['record']['value']['parent_generation'] = 99
                envelope['sha256'] = digest(envelope['record'])
            elif mode == 'subrequest-identity':
                envelope['record']['value']['snapshot_id'] = 'foreign-snapshot'
                envelope['sha256'] = digest(envelope['record'])
            else:
                (target / 'commits' / '000999.json.new').write_bytes(b'{')
            if mode != 'residual-tail':
                path.write_bytes(canonical(envelope))
            try:
                recover_commits(target)
            except ValueError:
                passed('recovery-rejects-' + mode)
            else:
                raise AssertionError('corrupt/stale recovery accepted')
        for name, expected in [('cancel-before-tool', ['not-dispatched', 'not-dispatched']),
                               ('cancel-after-tool', ['completed', 'not-dispatched']),
                               ('result-failure', ['unknown', 'not-dispatched']),
                               ('future-tool', ['failed', 'not-dispatched'])]:
            selected = selected_context(snapshot, (), ['wake-alpha/output-1'], cases[name]['recovery'])
            fact = selected['committed_actions'][0]
            assert fact['generated_assistant']['tool_calls']
            assert [item['status'] for item in fact['execution']] == expected
            assert fact['terminal']['status'] in {'cancelled', 'failed'}
        passed('explicit-committed-actions-distinguish-generated-completed-failed-unknown-and-not-dispatched')
        # The normal second wake actually consumes completed action facts.
        request = json.loads((root / 'normal' / 'request-0003.json').read_bytes())['envelope']['body']
        facts = json.loads(request['messages'][1]['content'])['selected']['committed_actions']
        fact = next(item for item in facts if item['commit'] == 'wake-alpha/output-1')
        assert [item['status'] for item in fact['execution']] == ['completed', 'completed']
        assert all(item['result']['content'] for item in fact['execution']) and fact['terminal']['status'] == 'complete'
        passed('actual-wire-explicit-generated-plan-and-committed-results-remain-distinct')
        for name, source, kind in [('post-terminal-dispatch', 'cancel-before-tool', 'dispatch'),
                                   ('post-terminal-result', 'result-failure', 'result')]:
            target = root / name
            shutil.copytree(root / source / 'commits', target / 'commits')
            state = recover_commits(target)
            row = {'schema': 1, 'sequence': len(state['records']) + 1, 'previous': state['head'],
                   'kind': kind, 'value': {'action': 'wake-alpha/output-1#1', 'content': 'late synthetic result'}}
            (target / 'commits' / f"{row['sequence']:06d}.json").write_bytes(canonical({'record': row, 'sha256': digest(row)}))
            try:
                recover_commits(target)
            except ValueError as error:
                assert str(error) == 'action after terminal wake'
                passed('recovery-rejects-' + name)
            else:
                raise AssertionError('action after terminal accepted')
        assert all(value == 0 for value in counters.values())
        passed('socket-QQ-external-write-sentinels-zero', **counters)
    durable(root / 'parser-console.json', {'synthetic_console': console.getvalue()})
    result = {'schema': 'stateless-doctor-v1', 'status': 'ok', 'assertions': assertions, 'assertion_count': len(assertions),
              'cases': {key: {'status': value['status'], 'attempts': value['attempts'], 'settlements': value['settlements'],
                              'failure': value['failure'], 'recovery_failure': value['recovery_failure']} for key, value in cases.items()},
              'real_HTTP': 0, 'QQ': 0, 'sentinels': counters,
              'limitations': cases['normal']['limitations']}
    durable(root / 'doctor.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('doctor', 'run', 'recover'))
    parser.add_argument('--output', required=True, help='new external output root; existing root only for read-only recover')
    parser.add_argument('--scenario', choices=SCENARIOS, default='normal')
    args = parser.parse_args(argv)
    try:
        if args.command == 'recover':
            # CLI recovery is deliberately synthetic-only: bind the manifest
            # label before reading action facts; no SDK/client construction.
            root = Path(args.output).absolute()
            manifest = json.loads((root / 'manifest.json').read_bytes())
            if manifest.get('schema') != 'stateless-loop-v1' or manifest.get('synthetic') is not True:
                raise ValueError('only this synthetic action format can recover')
            result = recover_commits(root)
        elif args.command == 'doctor':
            root = _safe_root(args.output)
            root.mkdir(parents=True, mode=0o700)
            result = doctor(root)
        else:
            frozen, schedule, wakes = synthetic_fixture()
            # Sentinel scope includes output creation and all SDK operations.
            root = _safe_root(args.output)
            with sentinels(root), redirect_stdout(io.StringIO()):
                result = run_synthetic_session(frozen, schedule, wakes, 'synthetic-default', root, scenario=args.scenario)
        summary = ({key: result[key] for key in ('schema', 'generation', 'head', 'boundaries', 'acknowledged',
                    'actions', 'terminals', 'automatic_requests', 'automatic_tools')} if args.command == 'recover' else
                   {'status': result.get('status'), 'assertion_count': result.get('assertion_count'), 'failure': result.get('failure')})
        print(json.dumps({**summary, 'output': str(root)}, ensure_ascii=False))
        return 1 if result.get('failure') or result.get('status') == 'stopped' else 0
    except (OSError, ValueError, RuntimeError, AssertionError, KeyError) as error:
        print(f'stateless calibration: {type(error).__name__}: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
