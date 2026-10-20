"""Explicit official client and a read-only default verifier.

Preparation (no credential and no HTTP): place manifest.json, plan.json,
target-metadata.json, models-response.json, research-allocation.json and
authorization.json in a fresh external root, then run this file --root ROOT.
The reviewed authorization binds their exact bytes and this source snapshot.
It is an operator handoff, not a signature or an automatic account check.

Execution is separate: Python callers pass temporary_api_key explicitly to
OfficialClient. CLI callers must select run --credential-stdin and supply one
temporary credential line on stdin; never put a credential in arguments/files.
run performs the one frozen text entry without tools. T17 owns the subject loop.
verify/recover only inspect: no GET, key discovery, retry or replay exists here.
"""
from __future__ import annotations

import argparse
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stateless_capacity_client as capacity
from a0_ledger import canonical, digest
import httpx
import openai

FILES = ('plan.json', 'target-metadata.json', 'models-response.json', 'research-allocation.json', 'authorization.json')
COMMON_TOKENS = 100000000
PUBLIC_REVIEW = '2026-10-20T15:15:35+00:00'
_CALIBRATION = object()
_calibration_context = ContextVar('stateless-official-closed-peer', default=None)


def contract():
    value = capacity.contract()
    value.update(schema='stateless-official-nonstream-v1', mode='live',
                 execution='explicit-temporary-credential; execute-once; operator-bound-records',
                 failure_billing='unknown: HTTP failure, disconnect, cancellation and missing usage retain full reserve',
                 pilot_binding=None)
    value['capacity_evidence'] = {
        'source': 'https://api-docs.deepseek.com/api/list-models/', 'checked_at': PUBLIC_REVIEW,
        'applicability': 'field semantics and Example 1048576/393216; actual target requires bound operator metadata',
        'status': 'public-price-table-1M; exact-integers-example-only'}
    value['price_evidence'] = {
        'source': 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/', 'checked_at': PUBLIC_REVIEW,
        'applicability': 'historical conservative peak 2/0.04/8 CNY per million; operator must accept current applicability'}
    value['identity_policy'] = 'application-stop-on-model-or-fingerprint-drift; not a provider invariance guarantee'
    return value


def _read(root, name):
    path = root / name
    capacity._require(path.is_file() and not path.is_symlink(), 'missing-or-unsafe-run-record')
    try:
        raw = path.read_bytes()
        return json.loads(raw), hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, TypeError):
        raise capacity.CapacityStop('unreadable-run-record') from None


def _time(value):
    try:
        result = datetime.fromisoformat(value)
        capacity._require(result.utcoffset() is not None, 'invalid-record-time')
        return result
    except (TypeError, ValueError):
        raise capacity.CapacityStop('invalid-record-time') from None


def _contains_credential(value, credential):
    if isinstance(value, str):
        return credential in value
    if type(value) in (int, float):
        return credential in str(value)
    if isinstance(value, dict):
        return any(_contains_credential(k, credential) or _contains_credential(v, credential) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_credential(v, credential) for v in value)
    return False


def manifest(root, run_id, budgets, role_stages, records):
    """Build a declaration from explicit record hashes; does not grant execution."""
    return {'schema': 'stateless-official-manifest-v1', 'root': str(Path(root)), 'run_id': run_id,
            'contract': contract(), 'budgets': deepcopy(budgets), 'role_stages': dict(role_stages),
            'openai': openai.__version__, 'httpx': httpx.__version__, 'records': dict(records)}


def binding(value):
    return digest({key: item for key, item in value.items() if key != 'records'})


def verify(root, value=None, *, _calibration=None):
    """Read only; trust the operator's captured metadata/allocation, never query it."""
    root = Path(root)
    capacity._require(root.is_absolute() and not any(p.is_symlink() for p in (root, *root.parents)), 'unsafe-root')
    capacity._safe_root(root / '.uncreated-boundary-check')
    disk, _ = _read(root, 'manifest.json')
    value = disk if value is None else value
    capacity._require(value == disk and set(value) == {'schema', 'root', 'run_id', 'contract', 'budgets',
                      'role_stages', 'openai', 'httpx', 'records'}
                      and value['schema'] == 'stateless-official-manifest-v1'
                      and value['root'] == str(root) and isinstance(value['run_id'], str) and value['run_id']
                      and value['contract'] == contract() and value['openai'] == openai.__version__
                      and value['httpx'] == httpx.__version__ and set(value['records']) == set(FILES), 'official-manifest-drift')
    capacity._validate_budgets(value)
    records = {}
    for name in FILES:
        records[name], sha = _read(root, name)
        capacity._require(sha == value['records'][name], 'run-record-drift')
    origin = 'synthetic-calibration' if _calibration is _CALIBRATION else 'operator-observed-official'
    metadata, models = records['target-metadata.json'], records['models-response.json']
    capacity._require(set(metadata) == {'schema', 'origin', 'method', 'url', 'observed_at', 'response_sha256'}
                      and metadata['schema'] == 'stateless-target-metadata-v1' and metadata['origin'] == origin
                      and metadata['method'] == 'GET' and metadata['url'] == capacity.ENDPOINT + '/models'
                      and metadata['response_sha256'] == value['records']['models-response.json'], 'target-metadata-required')
    capacity._require(isinstance(models, dict) and models.get('object') == 'list'
                      and isinstance(models.get('data'), list), 'target-metadata-schema')
    candidates = [item for item in models['data'] if isinstance(item, dict) and item.get('id') == capacity.WIRE_MODEL]
    capacity._require(len(candidates) == 1 and type(candidates[0].get('context_window')) is int
                      and candidates[0]['context_window'] == capacity.LIMITS['context']
                      and type(candidates[0].get('max_output_tokens')) is int
                      and candidates[0]['max_output_tokens'] == 393216, 'target-capacity-drift')
    plan = records['plan.json']
    capacity._require(set(plan) == {'schema', 'objective', 'seed', 'material_sha256', 'stopping', 'entry'}
                      and plan['schema'] == 'stateless-official-entry-v1' and type(plan['seed']) is int
                      and isinstance(plan['objective'], str) and plan['objective']
                      and isinstance(plan['material_sha256'], str) and len(plan['material_sha256']) == 64
                      and plan['stopping'] == 'one-entry; caller-loop-bounded-by-frozen-Ledger; unknown-stop-no-replay', 'run-plan-schema')
    entry = plan['entry']
    capacity._require(set(entry) == {'current', 'messages'} and set(entry['current']) == {'role', 'wake', 'snapshot_id'}
                      and entry['current']['role'] in value['role_stages']
                      and all(isinstance(item, str) and item for item in entry['current'].values())
                      and isinstance(entry['messages'], list) and bool(entry['messages'])
                      and all(isinstance(item, dict) and set(item) == {'role', 'content'}
                              and item['role'] in ('system', 'user') and isinstance(item['content'], str)
                              for item in entry['messages']), 'run-entry-schema')
    allocation = records['research-allocation.json']
    capacity._require(set(allocation) == {'schema', 'origin', 'revision', 'ceiling', 'spent', 'reservations'}
                      and allocation['schema'] == 'stateless-research-allocation-v1' and allocation['origin'] == origin
                      and isinstance(allocation['revision'], str) and allocation['revision']
                      and set(allocation['ceiling']) == set(capacity.METRICS)
                      and set(allocation['spent']) == set(capacity.METRICS)
                      and allocation['ceiling']['tokens'] == COMMON_TOKENS
                      and all(type(v) is int and v >= 0 for v in [*allocation['ceiling'].values(), *allocation['spent'].values()])
                      and isinstance(allocation['reservations'], list), 'common-allocation-required')
    matched, ids = [], set()
    totals = dict(allocation['spent'])
    for receipt in allocation['reservations']:
        capacity._require(set(receipt) == {'id', 'run_id', 'root', 'binding', 'budgets'}
                          and isinstance(receipt['id'], str) and receipt['id'] and receipt['id'] not in ids,
                          'allocation-receipt-schema')
        ids.add(receipt['id'])
        capacity._validate_budgets({'budgets': receipt['budgets'], 'role_stages': value['role_stages']})
        for metric in capacity.METRICS:
            totals[metric] += receipt['budgets']['research'][metric]
            # Stage ceilings overlap the shared run ceiling. They are not
            # separately spendable balances or additive capacity reservations.
            capacity._require(all(stage[metric] <= receipt['budgets']['research'][metric]
                                  for stage in receipt['budgets']['stages'].values()), 'stage-allocation-exceeds-run')
        if receipt['run_id'] == value['run_id'] or receipt['root'] == str(root):
            matched.append(receipt)
    capacity._require(all(totals[k] <= allocation['ceiling'][k] for k in capacity.METRICS)
                      and len(matched) == 1 and matched[0]['run_id'] == value['run_id']
                      and matched[0]['root'] == str(root) and matched[0]['binding'] == binding(value)
                      and matched[0]['budgets'] == value['budgets'], 'run-stage-preallocation-required')
    auth = records['authorization.json']
    capacity._require(set(auth) == {'schema', 'origin', 'mode', 'binding', 'records', 'reservation_id',
                                   'approved_by', 'valid_from', 'expires_at', 'accept_unknown_billing', 'accept_current_contract'}
                      and auth['schema'] == 'stateless-official-authorization-v1' and auth['origin'] == origin
                      and auth['mode'] == 'execute-once' and auth['binding'] == binding(value)
                      and auth['records'] == {k: v for k, v in value['records'].items() if k != 'authorization.json'}
                      and auth['reservation_id'] == matched[0]['id']
                      and isinstance(auth['approved_by'], str) and auth['approved_by']
                      and auth['accept_unknown_billing'] is True and auth['accept_current_contract'] is True,
                      'explicit-run-authorization-required')
    start, expires, seen = _time(auth['valid_from']), _time(auth['expires_at']), _time(metadata['observed_at'])
    now = datetime.now(timezone.utc)
    capacity._require(start <= seen <= now < expires and 0 < (expires - start).total_seconds() <= 86400,
                      'expired-or-inapplicable-evidence')
    return {'manifest': deepcopy(value), 'records': records, 'status': 'verified-local-records-only',
            'send_authorized': False, 'network': 0, 'metadata_trust': 'operator record; no API/authenticity verification'}


def recover(root):
    """Inspect even expired/interrupted runs; never construct a client or replay."""
    root = Path(root)
    capacity._require(root.is_absolute() and not any(p.is_symlink() for p in (root, *root.parents)), 'unsafe-root')
    capacity._safe_root(root / '.uncreated-boundary-check')
    status, count = 'unstarted', 0
    if (root / 'ledger.json').exists():
        try:
            envelope, _ = _read(root, 'ledger.json')
            ledger = capacity.Ledger.__new__(capacity.Ledger)
            ledger.state, ledger.limits = envelope['state'], dict(capacity.LIMITS)
            ledger.prices, ledger.policy = dict(capacity.PRICES), capacity.CapacityPolicy(contract())
            capacity._require(envelope['sha256'] == digest(ledger.state)
                              and ledger.state['limits'] == ledger.limits and ledger.state['prices'] == ledger.prices,
                              'ledger-integrity-unknown')
            ledger.validate()
            count = len(ledger.state['requests'])
            status = 'closed' if ledger.state['closed'] else 'inflight-unknown' if any(
                v['status'] == 'reserved' for v in ledger.state['requests']) else 'settled-no-replay'
        except (OSError, ValueError, TypeError, KeyError, IndexError, capacity.CapacityStop):
            status = 'ledger-unknown'
    elif (root / 'execution-claim.json').exists():
        status = 'claimed-ledger-missing-unknown'
    if (root / 'persistence-unknown.json').exists() or (root / 'ledger.json.new').exists():
        status = 'persistence-unknown'
    return {'schema': 'stateless-official-recovery-v1', 'ledger_status': status, 'requests': count,
            'resume': False, 'send_authorized': False, 'network': 0}


class _Transport(httpx.BaseTransport):
    def __init__(self, owner, calibration):
        self.owner = owner
        self.calibration = calibration
        # HTTPTransport construction itself can read certificate environment.
        with capacity.no_sdk_environment(owner.environment_reads):
            self.inner = httpx.MockTransport(owner._mock) if calibration else httpx.HTTPTransport(retries=0, trust_env=False)
        self._inner = self.inner

    def handle_request(self, request):
        owner = self.owner
        owner._final_transport_guard(request)
        if self.calibration:
            return self.inner.handle_request(request)
        row = owner.active['row']
        owner.active['attempted'] = True
        owner.attempts += 1
        owner._transport_event('transport-attempt', row['id'], sent=False)
        def trace(name, info):
            if name in ('http11.send_request_body.complete', 'http2.send_request_body.complete'):
                owner._transport_event('sent', row['id'], sent=True)
        request.extensions['trace'] = trace
        # No network was proved before this call. A connection failure remains
        # attempted/unknown, never sent or free. Header/body details stay private.
        response = self.inner.handle_request(request)
        owner._transport_event('response-headers', row['id'], sent=any(
            item['event'] == 'sent' and item.get('request_id') == row['id'] for item in owner.trace))
        return response

    def close(self):
        self.inner.close()


class OfficialClient(capacity.CapacityClient):
    """Fixed official SDK; no caller endpoint, SDK or transport parameter."""
    def __init__(self, root, frozen_manifest, *, temporary_api_key):
        calibration = _calibration_context.get()
        self._official_initialize(root, frozen_manifest, temporary_api_key,
                                  calibration=calibration[0] if calibration else None,
                                  scenario=calibration[1] if calibration else 'normal')

    def _official_initialize(self, root, frozen_manifest, temporary_key, *, calibration, scenario):
        self._calibration = calibration
        checked = verify(root, frozen_manifest, _calibration=calibration)
        capacity._require(isinstance(temporary_key, str) and len(temporary_key) >= 8
                          and temporary_key.isascii() and not any(ch.isspace() for ch in temporary_key), 'explicit-temporary-credential-required')
        capacity._require(not _contains_credential(checked, temporary_key), 'credential-in-run-record')
        # WHY: a permanent exclusive claim refuses reset/replay even if someone
        # removes the local Ledger after a partial constructor or cancelled run.
        claim = Path(root) / 'execution-claim.json'
        try:
            with claim.open('x', encoding='utf-8') as stream:
                stream.write(canonical({'binding': binding(frozen_manifest), 'resume': False}).decode() + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            descriptor = os.open(Path(root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except BaseException:
            raise capacity.CapacityStop('execution-already-claimed-or-claim-unknown') from None
        self._initialize(root, frozen_manifest, scenario=scenario)
        if calibration is _CALIBRATION:
            self.ledger.policy.usage_source = 'synthetic-sdk-usage'
        self._transport = _Transport(self, calibration is _CALIBRATION)
        self._temporary_key = temporary_key
        self._open_sdk(temporary_key, self._transport)

    def _expected_contract(self):
        return contract()

    def _integrity(self):
        verify(self.root, self.manifest, _calibration=self._calibration)
        claim, _ = _read(self.root, 'execution-claim.json')
        capacity._require(claim == {'binding': binding(self.manifest), 'resume': False}, 'execution-claim-drift')
        super()._integrity()
        capacity._require(self.sdk._client is self.http and self.http._transport is self._transport
                          and self._transport.inner is self._transport._inner
                          and self._transport.calibration is (self._calibration is _CALIBRATION)
                          and str(self.sdk.base_url) == capacity.ENDPOINT and not self.http._mounts
                          and self.http._trust_env is False and self.sdk.api_key == self._temporary_key,
                          'official-sdk-transport-drift')
        capacity._require(self.ledger.policy.usage_source == (
            'synthetic-sdk-usage' if self._calibration is _CALIBRATION else 'provider-usage-conservative-price'), 'usage-source-drift')

    def generate_response(self, messages, *args, **kwargs):
        tools = kwargs.get('tools') or (args[0] if args else None) or []
        if _contains_credential([messages, [tool.description for tool in tools]], self._temporary_key):
            self._stop('credential-in-input')
            raise capacity.CapacityStop('credential-in-input')
        return (yield from super().generate_response(messages, *args, **kwargs))

    def _credential_guard(self, request):
        capacity._require(request.headers.get('Authorization') == 'Bearer ' + self.sdk.api_key,
                          'official-auth-drift')
        capacity._require(self.sdk.api_key.encode() not in request.content, 'credential-in-request')

    def _before_http(self, request):
        self._credential_guard(request)
        super()._before_http(request)

    def _observe_http(self, response):
        raw_response = response.read()
        try:
            decoded = json.loads(raw_response)
            reflected = _contains_credential(decoded, self.sdk.api_key)
        except (ValueError, TypeError):
            reflected, decoded = False, None
        if self.sdk.api_key.encode() in raw_response or reflected:
            # Preserve valid usage while keeping a reflected credential out of
            # parser output, identity evidence and raw-usage observations.
            try:
                raw = decoded.get('usage') if isinstance(decoded, dict) else None
                usage = capacity._wire_usage(raw)
                # WHY: a numeric credential can also be a valid usage counter.
                # Preserve the full reserve rather than persist that secret.
                capacity._require(not _contains_credential(raw, self.sdk.api_key)
                                  and self.sdk.api_key not in canonical(raw).decode()
                                  and self.sdk.api_key not in canonical(usage).decode(), 'credential-in-usage')
                self.active['raw_usage'], self.active['usage'] = raw, usage
            except (ValueError, KeyError, TypeError, capacity.CapacityStop):
                pass
            raise capacity.CapacityStop('credential-reflected-by-peer')
        super()._observe_http(response)

    def _final_transport_guard(self, request):
        self._integrity()
        self._credential_guard(request)
        active = self.active
        capacity._require(active is not None and active['hook'] and active['row'] is not None
                          and not active['attempted'] and not self.ledger.poisoned and not self.ledger.state['closed'],
                          'transport-without-unique-reserve')
        envelope = self.saved[active['row']['id']]['value']['envelope']
        capacity._require(request.method == 'POST' and str(request.url) == capacity.ENDPOINT + '/chat/completions'
                          and request.headers.get('X-Capacity-Request-ID') == active['row']['id']
                          and request.content.decode() == envelope['serialized_body']
                          and json.loads(request.content) == active['expected_body']
                          and self._attribution() == active['attribution'], 'final-transport-wire-drift')
        # Recheck all common/stage ceilings including the durable inflight row;
        # admission cannot be called again because it would add a second R.
        projection = self.projection()
        capacity._require(all(v['S'][metric] + v['I'][metric] <= (
            self.manifest['budgets']['research'] if scope == 'research' else
            self.manifest['budgets']['stages'][scope.split('/')[1]])[metric]
            for scope, v in projection['scopes'].items() for metric in capacity.METRICS), 'final-transport-budget-drift')

    def _transport_event(self, event, request_id, *, sent):
        record = {'event': event, 'request_id': request_id, 'transport': 'official-http', 'sent': sent,
                  'provider_accepted': 'unknown'}
        self._write(f'transport-{len(self.ledger.state["requests"]):04d}-{event}.json', record)
        self.trace.append(record)

    def transport_evidence(self, request_id):
        """T17 exposure seam; mock never counts as an actual send."""
        events = [item for item in self.trace if item.get('request_id') == request_id]
        return {'request_id': request_id, 'mock_exposed': any(v['event'] == 'mock' for v in events),
                'transport_attempted': any(v['event'] == 'transport-attempt' for v in events),
                'sent': any(v['event'] == 'sent' for v in events),
                'response_headers_received': any(v['event'] == 'response-headers' for v in events),
                'provider_accepted': 'unknown', 'arrival': 'unknown'}


def _calibration_client(root, frozen_manifest, *, scenario='normal'):
    """Internal fixed fake peer; not a configurable transport or execution flag."""
    capacity._require(scenario in capacity.SCENARIOS, 'unknown-scenario')
    token = _calibration_context.set((_CALIBRATION, scenario))
    try:
        return OfficialClient(root, frozen_manifest, temporary_api_key='sk-fictional-official-only')
    finally:
        _calibration_context.reset(token)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', nargs='?', choices=('verify', 'recover', 'run'), default='verify')
    parser.add_argument('--root', required=True)
    parser.add_argument('--credential-stdin', action='store_true', help='run only: explicit temporary credential, one stdin line')
    args = parser.parse_args(argv)
    try:
        if args.command == 'recover':
            capacity._require(not args.credential_stdin, 'credential-only-in-explicit-run')
            print(json.dumps(recover(args.root)))
            return 0
        checked = verify(args.root)
        if args.command != 'run':
            capacity._require(not args.credential_stdin, 'credential-only-in-explicit-run')
            print(json.dumps({k: v for k, v in checked.items() if k not in ('manifest', 'records')}))
            return 0
        capacity._require(args.credential_stdin, 'explicit-credential-stdin-required')
        temporary_key = sys.stdin.readline().rstrip('\r\n')
        client = OfficialClient(args.root, checked['manifest'], temporary_api_key=temporary_key)
        del temporary_key
        try:
            entry = checked['records']['plan.json']['entry']
            client.current = deepcopy(entry['current'])
            generator = client.generate_response(entry['messages'], stream=False)
            while True:
                try:
                    next(generator)
                except StopIteration:
                    break
        finally:
            client.close()
        return 0
    except BaseException:
        # Do not print the source exception, request headers or credential hash.
        print('official-run-refused-or-stopped', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
