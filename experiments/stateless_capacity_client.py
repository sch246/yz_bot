"""Shared non-streaming capacity accounting; this constructor stays closed.

The official route/model and historical numeric snapshot are declarations only.
CapacityClient uses an internal MockTransport. stateless_official reuses these
guards and settlement with a separate explicit official execution contract.
Ledger is the only durable balance; projections never maintain another total.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import threading
from types import SimpleNamespace

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from a0_capacity import CapacityPolicy, LIMITS, PRICES, MODEL, WIRE_MODEL, ENDPOINT, declaration
from a0_ledger import Ledger, canonical, digest, durable
from a0_sdk_calibration import normalize_usage, no_sdk_environment
from comparison_snapshot import _safe_root
from mods import llm
import httpx
import openai

STAGES = ('formation', 'probe', 'question', 'verification')
ROLE_STAGES = {'chat': 'question', 'selector': 'probe', 'librarian': 'formation', 'background': 'formation'}
METRICS = ('tokens', 'micro_cny', 'attempts')
SCENARIOS = ('normal', 'missing', 'invalid', 'alias', 'conflict-alias', 'total', 'unknown-usage',
             'null-counter', 'null-cache', 'null-details', 'false-details', 'list-details', 'null-nested-cache', 'joint-overreport', 'output-overreport', 'connection', '429', '500',
             'cancel-before', 'cancel-after', 'length', 'model-drift', 'fingerprint-drift')


class CapacityStop(RuntimeError):
    """Only fixed labels cross the SDK/parser boundary."""


def source_hashes():
    repo = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(['git', 'ls-files', '-z', '--', 'mods', 'experiments'], cwd=repo,
                             capture_output=True, check=True).stdout.split(b'\0')
    names = {name.decode('utf-8') for name in tracked if name.endswith(b'.py')}
    # New delivery sources must be bound before the commit exists as well.
    names.update({'experiments/stateless_capacity_client.py', 'experiments/stateless_capacity_calibration.py',
                  'experiments/stateless_official.py'})
    files = [repo / name for name in sorted(names)]
    return {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def contract():
    value = declaration('synthetic')
    value.update(schema='stateless-nonstream-capacity-v1', code=source_hashes())
    value['request'] = {k: v for k, v in value['request'].items() if k != 'stream_options'}
    value['request']['stream'] = False
    value['execution'] = 'closed-sdk-MockTransport-only; live constructor not implemented'
    return value


def manifest(run_id, budgets, *, role_stages=None):
    """Caller supplies explicit fixture ceilings; none is a formal allocation."""
    return {'schema': 'stateless-capacity-manifest-v1', 'synthetic': True, 'run_id': run_id,
            'contract': contract(), 'budgets': deepcopy(budgets), 'role_stages': dict(ROLE_STAGES if role_stages is None else role_stages),
            'openai': openai.__version__, 'httpx': httpx.__version__}


def _require(ok, label):
    if not ok:
        raise CapacityStop(label)


def _wire_usage(raw):
    _require(isinstance(raw, dict), 'invalid-usage')
    # The shared normalizer treats null aliases/falsey details as absent.
    # This contract rejects their explicit wire presence before SDK coercion.
    for key, number in raw.items():
        if key in ('prompt_tokens_details', 'completion_tokens_details'):
            _require(type(number) is dict and all(type(v) is int and v >= 0
                                                 for v in number.values()), 'invalid-usage')
        else:
            _require(type(number) is int and number >= 0, 'invalid-usage')
    return normalize_usage(raw)


def _validate_manifest(value):
    _require(set(value) == {'schema', 'synthetic', 'run_id', 'contract', 'budgets', 'role_stages', 'openai', 'httpx'}
             and value['schema'] == 'stateless-capacity-manifest-v1' and value['synthetic'] is True
             and isinstance(value['run_id'], str) and bool(value['run_id'])
             and value['contract'] == contract()
             and value['openai'] == openai.__version__ and value['httpx'] == httpx.__version__, 'manifest-drift')
    _validate_budgets(value)


def _validate_budgets(value):
    roles = value['role_stages']
    _require(isinstance(roles, dict) and bool(roles)
             and all(isinstance(k, str) and bool(k) and v in STAGES for k, v in roles.items()), 'role-stage-schema')
    budgets = value['budgets']
    _require(set(budgets) == {'research', 'stages'} and set(budgets['stages']) == set(STAGES), 'budget-schema')
    for ceiling in [budgets['research'], *budgets['stages'].values()]:
        _require(set(ceiling) == set(METRICS) and all(type(v) is int and v > 0 for v in ceiling.values()),
                 'budget-schema')


class CapacityClient(llm.LLMClient):
    """drive_wake/selector protocol, one Ledger for all roles and stages.

    ``current`` is supplied by the loop, then frozen for a generation. Stages
    come from the immutable manifest, never an untrusted current-stage override.
    Python monkeypatching is a calibration capability, not a security boundary.
    """
    def __init__(self, root, frozen_manifest, *, scenario='normal'):
        _validate_manifest(frozen_manifest)
        _require(scenario in SCENARIOS, 'unknown-scenario')
        self._initialize(root, frozen_manifest, scenario=scenario)
        self._open_sdk('sk-fictional-capacity-only', httpx.MockTransport(self._mock))

    def _expected_contract(self):
        return contract()

    def _initialize(self, root, frozen_manifest, *, scenario):
        """Shared setup after the owning closed/official schema was validated."""
        self.root = Path(root)
        _require(self.root.is_absolute() and not any(p.is_symlink() for p in (self.root, *self.root.parents)),
                 'unsafe-root')
        # Validate all registered worktree boundaries without creating a file.
        _safe_root(self.root / '.uncreated-boundary-check')
        self.manifest = deepcopy(frozen_manifest)
        self.manifest_hash = digest(self.manifest)
        _require(json.loads((self.root / 'manifest.json').read_bytes()) == self.manifest, 'manifest-drift')
        self.contract = self.manifest['contract']
        self.scenario = scenario
        self.ledger = Ledger(self.root, self.manifest['run_id'], self.manifest_hash,
                             limits=LIMITS, prices=PRICES, policy=CapacityPolicy(self.contract))
        self.trace, self.chats, self.exchanges, self.saved = [], [], [], {}
        self.current, self.active, self.response_identity = None, None, None
        self.lock = threading.Lock()
        self.attempts = self.settlements = 0
        self.environment_reads = []
        self.config = {'default_model': MODEL, 'providers': {'deepseek': {'models': {
            WIRE_MODEL: {'function_calling': True, 'vision': False, 'tool_images': False}}}}}
        self._config_hash = digest(self.config)
        self.strict_tools = True
        self.request_policy = lambda *_: {'max_tokens': LIMITS['output']}
        self.clients = {'deepseek': SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self._create)))}
        self._clients = self.clients['deepseek']
    def _open_sdk(self, temporary_key, transport):
        with no_sdk_environment(self.environment_reads):
            self.http = httpx.Client(transport=transport, trust_env=False,
                follow_redirects=False, event_hooks={'request': [self._before_http], 'response': [self._observe_http]})
            self.sdk = openai.OpenAI(api_key=temporary_key, admin_api_key='unused',
                organization='', project='', webhook_secret='unused', base_url=ENDPOINT,
                max_retries=0, timeout=self.contract['timeout_seconds'], http_client=self.http)
        self._sdk = self.sdk

    def _poison(self, label):
        self.ledger.poisoned = True
        try:
            durable(self.root / 'persistence-unknown.json', {'status': 'unknown', 'reason': label, 'resume': False})
        except BaseException:
            pass

    def _write(self, name, value):
        try:
            durable(self.root / name, value)
        except BaseException:
            self._poison('evidence-persistence-unknown')
            raise CapacityStop('evidence-persistence-unknown') from None

    def _integrity(self):
        _require(digest(self.manifest) == self.manifest_hash and self.contract is self.manifest['contract'], 'manifest-drift')
        _require(json.loads((self.root / 'manifest.json').read_bytes()) == self.manifest, 'manifest-drift')
        _require(self.contract == self._expected_contract(), 'code-contract-drift')
        _require(self.ledger.policy.contract == self.contract and self.ledger.limits == LIMITS
                 and self.ledger.prices == PRICES and self.ledger.state['manifest'] == self.manifest_hash
                 and self.ledger.state['pilot'] == self.manifest['run_id'], 'ledger-policy-drift')
        self._ledger_snapshot()
        _require(digest(self.config) == self._config_hash and self.clients.get('deepseek') is self._clients
                 and self._clients.chat.completions.create == self._create and self.sdk is self._sdk
                 and self.sdk.max_retries == 0 and self.sdk.timeout == self.contract['timeout_seconds']
                 and not self.http.follow_redirects
                 and self.strict_tools is True, 'sdk-config-drift')

    def _ledger_snapshot(self):
        # WHY: a read/parse failure is an unknown authority, not permission to
        # repair it by writing the in-memory balance while stopping a caller.
        try:
            disk = json.loads((self.root / 'ledger.json').read_bytes())
            _require(disk == {'state': self.ledger.state, 'sha256': digest(self.ledger.state)},
                     'ledger-integrity-unknown')
            self.ledger.validate()
        except (OSError, ValueError, TypeError, KeyError, IndexError, CapacityStop):
            self._poison('ledger-integrity-unknown')
            raise CapacityStop('ledger-integrity-unknown') from None
        return disk

    def _stop(self, reason):
        if not self.ledger.poisoned and not self.ledger.state['closed']:
            self._ledger_snapshot()
            self.ledger.stop(reason)

    def projection(self):
        """Read S and I from unchanged original Ledger rows and exact evidence."""
        self._integrity()
        zero = lambda: dict.fromkeys(METRICS, 0)
        scopes = {key: {'S': zero(), 'I': zero()} for key in ('research', *('stages/' + s for s in STAGES))}
        for row in self.ledger.state['requests']:
            saved = self.saved.get(row['id'])
            _require(saved is not None and json.loads((self.root / saved['file']).read_bytes()) == saved['value'],
                     'request-evidence-drift')
            record = saved['value']
            _require(digest(record['envelope']) == row['request_sha256']
                     and record['envelope']['request_id'] == row['id']
                     and record['attribution']['run_id'] == self.manifest['run_id']
                     and record['attribution']['stage'] == self.manifest['role_stages'][record['attribution']['role_id']], 'attribution-drift')
            known = row['status'] == 'settled' and row['usage'] is not None
            amount = {'tokens': row['usage'][0] + row['usage'][2] if known else row['input_bound'] + row['allowance'],
                      'micro_cny': row['charged'] if row['status'] == 'settled' else row['reserved'], 'attempts': 1}
            for key in ('research', 'stages/' + record['attribution']['stage']):
                target = scopes[key]['S' if row['status'] == 'settled' else 'I']
                for metric in METRICS:
                    target[metric] += amount[metric]
        return {'ledger_sha256': digest(self.ledger.state), 'poisoned': self.ledger.poisoned,
                'closed': self.ledger.state['closed'], 'scopes': scopes}

    def admission(self, attribution):
        _require(set(attribution) == {'role_id', 'task_id', 'snapshot_id', 'run_id', 'branch_id',
                                      'parent_checkpoint_id', 'stage'}
                 and attribution['role_id'] in self.manifest['role_stages']
                 and attribution['stage'] == self.manifest['role_stages'][attribution['role_id']]
                 and attribution['run_id'] == self.manifest['run_id'], 'attribution-drift')
        projection = self.projection()
        reserve = {'tokens': LIMITS['context'] + LIMITS['output'],
                   'micro_cny': self.ledger.cost(LIMITS['context'], 0, LIMITS['output'], reserve=True), 'attempts': 1}
        formulas, reasons = {}, []
        if projection['poisoned'] or projection['closed']:
            reasons.append('shared-closed')
        if any(row['status'] == 'reserved' for row in self.ledger.state['requests']):
            reasons.append('shared-inflight')
        for scope, values in projection['scopes'].items():
            ceiling = (self.manifest['budgets']['research'] if scope == 'research' else
                       self.manifest['budgets']['stages'][scope.split('/')[1]])
            formulas[scope] = {}
            for metric in METRICS:
                S, I = values['S'][metric], values['I'][metric]
                R = reserve[metric] if scope in ('research', 'stages/' + attribution['stage']) else 0
                ok = S + I + R <= ceiling[metric]
                formulas[scope][metric] = {'S': S, 'I': I, 'R': R, 'total': S + I + R,
                                           'limit': ceiling[metric], 'ok': ok}
                if not ok:
                    reasons.append(scope + ':' + metric)
        return {'allowed': not reasons, 'send_authorized': False, 'formulas': formulas,
                'reasons': reasons, 'ledger_sha256': projection['ledger_sha256']}

    def _attribution(self):
        _require(isinstance(self.current, dict) and set(self.current) in (
            {'wake', 'snapshot_id'}, {'role', 'wake', 'snapshot_id'}), 'attribution-schema')
        role = self.current.get('role', 'chat')
        _require(role in self.manifest['role_stages'] and all(isinstance(self.current[k], str) and self.current[k]
                                            for k in ('wake', 'snapshot_id')), 'attribution-schema')
        return {'role_id': role, 'task_id': self.current['wake'], 'snapshot_id': self.current['snapshot_id'],
                'run_id': self.manifest['run_id'], 'branch_id': 'main', 'parent_checkpoint_id': None,
                'stage': self.manifest['role_stages'][role]}

    def _before_http(self, request):
        try:
            self.ledger.guard()
            self._integrity()
            active = self.active
            _require(active is not None and not active['hook'], 'duplicate-http-hook')
            active['hook'] = True
            _require(self._attribution() == active['attribution'], 'attribution-drift')
            body = json.loads(request.content)
            _require(request.method == 'POST' and str(request.url) == ENDPOINT + '/chat/completions'
                     and body == active['expected_body'] and body['model'] == WIRE_MODEL, 'wire-drift')
            _require(set(body) <= {'model', 'messages', 'tools', *self.contract['request']}
                     and all(body.get(k) == v for k, v in self.contract['request'].items())
                     and 'stream_options' not in body, 'request-contract-drift')
            for tool in body.get('tools', []):
                _require(set(tool) == {'type', 'function'} and tool['type'] == 'function'
                         and 'strict' not in tool['function'], 'tool-schema-drift')
            request_id = f"{self.ledger.state['pilot']}/{len(self.ledger.state['requests']) + 1:04d}"
            _require(request.headers.get('X-Capacity-Request-ID') == request_id, 'request-id-drift')
            envelope = {'request_id': request_id, 'method': request.method, 'url': str(request.url),
                        'body': body, 'serialized_body': request.content.decode(),
                        'body_sha256': hashlib.sha256(request.content).hexdigest()}
            decision = self.admission(active['attribution'])
            self.trace.append({'event': 'admit', 'request_id': request_id, 'decision': decision})
            _require(decision['allowed'], 'shared-budget-refused')
            number = len(self.ledger.state['requests']) + 1
            record = {'envelope': envelope, 'attribution': deepcopy(active['attribution'])}
            filename = f'request-{number:04d}.json'
            self._write(filename, record)
            self.saved[request_id] = {'file': filename, 'value': deepcopy(record)}
            self.trace.append({'event': 'request-evidence', 'request_id': request_id})
            active['row'] = self.ledger.reserve(envelope, LIMITS['output'])
            self.exchanges.append(envelope)
            self.trace.append({'event': 'reserve', 'request_id': request_id})
        except BaseException as error:
            label = str(error) if isinstance(error, CapacityStop) else 'reservation-failed'
            if self.active is not None:
                self.active['error'] = label
            if not self.ledger.poisoned and not self.ledger.state['closed']:
                try:
                    self._stop(label)
                except BaseException:
                    pass
            raise

    def _observe_http(self, response):
        active = self.active
        try:
            _require(active is not None and active['row'] is not None and not active['observed'], 'response-hook-drift')
            active['observed'] = True
            raw = response.read()
            if response.status_code != 200:
                raise CapacityStop('http-failure')
            value = json.loads(raw)
            active['raw_usage'] = value.get('usage')
            try:
                active['usage'] = _wire_usage(active['raw_usage'])
            except (ValueError, KeyError, TypeError, CapacityStop):
                raise CapacityStop('missing-usage' if active['raw_usage'] is None else 'invalid-usage') from None
            identity = {'model': value.get('model'), 'system_fingerprint': value.get('system_fingerprint')}
            active['identity'] = identity
            _require(identity['model'] == self.contract['response_model']
                     and (identity['system_fingerprint'] is None or isinstance(identity['system_fingerprint'], str)),
                     'response-identity-drift')
            if self.response_identity is None:
                self.response_identity = deepcopy(identity)
            _require(identity == self.response_identity, 'response-identity-drift')
            _require(self._attribution() == active['attribution'], 'attribution-drift')
            self._write(f"response-{len(self.ledger.state['requests']):04d}.json", {
                'request_id': active['row']['id'], 'identity': identity, 'raw_usage': active['raw_usage'],
                'response_sha256': hashlib.sha256(raw).hexdigest()})
            self.trace.append({'event': 'response-observation', 'request_id': active['row']['id']})
        except BaseException as error:
            active['error'] = str(error) if isinstance(error, CapacityStop) else 'response-observation-failed'
            raise

    def _create(self, **params):
        _require(self.active is not None and not self.active['hook'], 'sdk-outside-generation')
        _require(params.get('stream') is False and params.get('model') == WIRE_MODEL, 'request-contract-drift')
        params = deepcopy(params)
        params.update(thinking={'type': 'enabled'}, reasoning_effort='high', tool_choice=params.get('tool_choice', 'auto'))
        self.active['expected_body'] = deepcopy(params)
        thinking = params.pop('thinking')
        params['extra_body'] = {'thinking': thinking}
        params['extra_headers'] = {'X-Capacity-Request-ID': f"{self.ledger.state['pilot']}/{len(self.ledger.state['requests']) + 1:04d}"}
        value, error = None, None
        try:
            with no_sdk_environment(self.environment_reads):
                value = self.sdk.chat.completions.create(**params)
            _require(self.active['observed'] and self.active['row'] is not None, 'unobserved-response')
            if self.scenario == 'cancel-after':
                raise CapacityStop('cancelled')
        except BaseException as failure:
            error = self.active['error'] or (str(failure) if isinstance(failure, CapacityStop) else 'transport-failure')
        # WHY: the production parser prints as well as yields. Settle before
        # returning the SDK object so even its console output follows durability.
        self._finish_request(error or 'stop')
        if error:
            raise CapacityStop(error) from None
        return value

    def _finish_request(self, outcome):
        row = self.active['row']
        if row is None or row['status'] != 'reserved' or self.ledger.poisoned:
            return
        self._ledger_snapshot()
        if self.ledger.state['closed'] and outcome == 'stop':
            outcome = 'shared-closed'
        self._write(f"observation-{len(self.ledger.state['requests']):04d}.json", {
            'request_id': row['id'], 'raw_usage': self.active['raw_usage'], 'usage': self.active['usage'],
            'identity': self.active['identity'], 'outcome': outcome})
        self.settlements += 1
        try:
            self.ledger.settle(row, self.active['usage'], outcome)
        except BaseException:
            raise CapacityStop('settlement-unknown' if self.ledger.poisoned else
                               self.ledger.state['closed'] or 'settlement-failed') from None
        self.trace.append({'event': 'settle', 'request_id': row['id']})

    def _mock(self, request):
        active = self.active
        _require(active is not None and active['row'] is not None and not active['attempted'], 'mock-without-reserve')
        row = active['row']
        _require(json.loads((self.root / 'ledger.json').read_bytes())['state']['requests'][-1] == row
                 and row['status'] == 'reserved'
                 and request.content.decode() == self.exchanges[-1]['serialized_body'], 'mock-without-reserve')
        active['attempted'] = True
        self.attempts += 1
        # WHY: selector currently recognizes this closed-only event as exposure.
        # A future live adapter must introduce a real sent event in the selector.
        self.trace.append({'event': 'mock', 'request_id': row['id']})
        if self.scenario == 'connection':
            raise httpx.ConnectError('fictional failure', request=request)
        if self.scenario in ('429', '500'):
            return httpx.Response(int(self.scenario), json={'error': {'message': 'fictional'}})
        return httpx.Response(200, json=self._fixture_response(json.loads(request.content)))

    def _fixture_response(self, body):
        """Invented content only, also exercising the actual read-tool loop."""
        role = self.active['attribution']['role_id']
        initial = not any(v['role'] == 'tool' for v in body['messages'])
        context = json.loads(body['messages'][1]['content']) if role in ('chat', 'selector') else None
        calls = []
        if role == 'selector':
            ids = [v['entry_id'] for v in context['input']['catalog']]
            if initial:
                calls = [{'id': 'fixture-read', 'type': 'function', 'function': {'name': 'read_material',
                         'arguments': canonical({'entry_id': ids[0]}).decode()}}] if ids else []
            content = canonical({'situation': 'Synthetic inference.', 'references': ids,
                                 'input_hash': context['input_hash']}).decode()
        elif role == 'chat' and context is not None:
            if initial and body.get('tools'):
                calls = [{'id': 'fixture-reference', 'type': 'function', 'function': {'name': 'read_reference',
                         'arguments': canonical(context['screen']['records'][0]['ref']).decode()}}]
            content = 'Synthetic completed chat.'
        else:
            content = 'Synthetic background result.'
        message = {'role': 'assistant', 'content': '' if calls else content, 'reasoning_content': 'EPHEMERAL'}
        if calls:
            message['tool_calls'] = calls
        usage = {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120, 'prompt_cache_hit_tokens': 40}
        mode = self.scenario
        if mode == 'missing': usage = None
        elif mode == 'invalid': usage['completion_tokens'] = -1
        elif mode == 'alias':
            usage.pop('prompt_cache_hit_tokens')
            usage['prompt_tokens_details'] = {'cached_tokens': 40}
        elif mode == 'conflict-alias': usage['prompt_tokens_details'] = {'cached_tokens': 41}
        elif mode == 'total': usage['total_tokens'] = 121
        elif mode == 'unknown-usage': usage['fee'] = 0
        elif mode == 'null-counter': usage['prompt_tokens'] = None
        elif mode == 'null-cache': usage['prompt_cache_hit_tokens'] = None
        elif mode == 'null-details': usage['prompt_tokens_details'] = None
        elif mode == 'false-details': usage['completion_tokens_details'] = False
        elif mode == 'list-details': usage['prompt_tokens_details'] = []
        elif mode == 'null-nested-cache': usage['prompt_tokens_details'] = {'cached_tokens': None}
        elif mode == 'joint-overreport':
            usage = {'prompt_tokens': LIMITS['context'], 'completion_tokens': 20,
                     'total_tokens': LIMITS['context'] + 20}
        elif mode == 'output-overreport':
            usage.update(completion_tokens=4097, total_tokens=4197)
        return {'id': 'fictional-capacity', 'object': 'chat.completion', 'created': 1767225600,
                'model': 'drift' if mode == 'model-drift' else WIRE_MODEL,
                'system_fingerprint': 'drift' if mode == 'fingerprint-drift' and self.attempts > 1 else 'fixture-v1',
                'choices': [{'index': 0, 'finish_reason': 'length' if mode == 'length' else
                             'tool_calls' if calls else 'stop', 'message': message}], 'usage': usage}

    def generate_response(self, *args, **kwargs):
        if not self.lock.acquire(blocking=False):
            # The owning generation notices the shared stop before release.
            if not self.ledger.poisoned:
                try:
                    self._stop('concurrent-generation')
                except BaseException:
                    pass
            raise CapacityStop('concurrent-generation')
        inner, error, buffered, completed = None, None, [], None
        self.active = {'row': None, 'usage': None, 'raw_usage': None, 'identity': None, 'error': None,
                       'hook': False, 'observed': False, 'attempted': False, 'attribution': None}
        try:
            try:
                self.ledger.guard()
                self._integrity()
                self.active['attribution'] = self._attribution()
                if self.scenario == 'cancel-before':
                    raise CapacityStop('cancelled')
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
            outcome = (str(error) if isinstance(error, CapacityStop) else 'parser-failure') if error else 'stop'
            self._finish_request(outcome)
            if error is not None or self.ledger.poisoned or self.ledger.state['closed']:
                if not self.ledger.poisoned and not self.ledger.state['closed']:
                    self._stop(outcome)
                raise CapacityStop('persistence-unknown' if self.ledger.poisoned else
                                   self.ledger.state['closed'] or outcome) from None
            self._integrity()
            for value in buffered:
                self.ledger.guard()
                _require(self._attribution() == self.active['attribution'], 'attribution-drift')
                yield value
            self.ledger.guard()
            _require(self._attribution() == self.active['attribution'], 'attribution-drift')
            return completed
        except GeneratorExit:
            if not self.ledger.poisoned and not self.ledger.state['closed']:
                self._stop('cancelled')
            raise
        except BaseException as failure:
            if not self.ledger.poisoned and not self.ledger.state['closed']:
                try:
                    self._stop('local-failure')
                except BaseException:
                    pass
            raise CapacityStop(str(failure) if isinstance(failure, CapacityStop) else 'local-failure') from None
        finally:
            self.active = None
            self.lock.release()

    def guard_tools(self):
        try:
            self.ledger.guard()
            self.projection()
        except BaseException:
            if not self.ledger.poisoned and not self.ledger.state['closed']:
                self._stop('tool-guard-failure')
            raise CapacityStop('shared-closed') from None

    def close(self):
        self.sdk.close()
