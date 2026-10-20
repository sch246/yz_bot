"""Finite synthetic workspace; default prepare/verify/recover never send or take keys.

run is separate and explicit, using the fixed OfficialClient and operator-bound
records. This module owns world effects/clock only; the existing driver, notes
and original Ledger own generation/action facts, memory and spend respectively.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from a0_ledger import canonical, digest, durable
from comparison_snapshot import _safe_root, freeze_sources, open_snapshot
import stateless_official as official
import stateless_capacity_client as capacity
from stateless_loop import drive_wake, finish_wake, recover_commits, close_session
from stateless_notes import NoteWriter, snapshot_notes, recover_notes

BOOTSTRAP = '''This is a finite synthetic workspace, not production or an assigned task.
No interest, plan, preferred arrangement or success score is supplied. You may
observe, move an object, write a card, leave a file note, continue, keep a choice,
set it aside or finish silently. No tool or memory revision is mandatory.
Objects circle/triangle and cards one/two are visible through observe_workspace.
Places are table, left and right. Moving/writing changes this fictional world
only. Cards are external objects; global/main is optional versioned file memory.
At clock advancement one tray (left/right) may tilt; objects there slide to the
table. The fixed mechanism/seed are design priors, not a reward or your judgment.
Use actual observations/results to distinguish effects from intentions.
There are at most three wakes and twelve shared HTTP requests, each at most4096
output tokens. Shared application caps are 2,000,000 cumulative tokens and5 CNY
at conservative frozen prices, not a provider bill; unknown usage stops the run.
Every native follow-up costs a request; all roles share the budget.
After bootstrap the harness offers at most one labelled clock opportunity. A
further revisit happens only if you actually call request_revisit. Delay zero
explicitly declines every remaining opportunity. Finishing or silence alone
requests no revisit. These are simulated ticks, not a production scheduler.
Only committed notes/actions are offered to a fresh later Chat. Their exposure
is evidence of input, not proof that you used them; later choices remain open.
'''
TOOLS = ('observe_workspace', 'move_object', 'write_card', 'request_revisit')
INPUT_FILES = ('bootstrap.txt', 'world-rules.json')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _require(ok, label):
    if not ok:
        raise ValueError(label)


def _root(value):
    root = Path(value)
    _require(root.is_absolute() and not any(p.is_symlink() for p in (root, *root.parents)), 'unsafe subject root')
    _safe_root(root / '.uncreated-subject-boundary')
    return root


def _read(root, name):
    path = root / name
    _require(path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1, 'missing/unsafe subject record')
    return path.read_bytes()


def rules(seed):
    return {'schema': 'subject-workspace-rules-v1', 'seed': seed,
            'places': ['table', 'left', 'right'], 'objects': {'circle': 'table', 'triangle': 'left'},
            'cards': {'one': 'A short line beside an empty square.', 'two': 'Two circles and a blank margin.'},
            'transition': 'on each advanced integer tick tilt left/right by (seed+tick)%2; slide its objects to table',
            'feedback': 'state and actual parameters only; no note, self-assessment, goal, score or ideal answer',
            'card_limit_utf8': 512, 'revisit_max_delay': 3, 'clock_tie_order': 'harness then requested action id'}


def source_hashes():
    value = capacity.source_hashes()
    repo = Path(__file__).resolve().parents[1]
    for name in ('subject_probe.py', 'subject_probe_calibration.py'):
        path = repo / 'experiments' / name
        _require(path.is_file(), 'subject sources incomplete')
        value['experiments/' + name] = _hash(path.read_bytes())
    return value


def _snapshot(bootstrap):
    raw = ('【】Workspace(101) 12:00:00 | 1\n    ' + bootstrap.replace('\n', '\n    ').rstrip() + '\n').encode()
    frozen = freeze_sources([{'source_alias': 'workspace', 'target': 1001, 'days': {'2026-01-01': raw}}],
        bot={'id': 900, 'name': 'FixtureBot', 'names': {'FixtureBot': 900}}, synthetic=True)
    event = frozen['events'][0]
    schedule = [{'opened_cursor': 1, 'virtual_time': event['record']['time'], 'event_keys': [event['event_key']]}]
    return open_snapshot(frozen, schedule, 1)


def _entry(subject, raw_subject, bootstrap):
    snapshot = _snapshot(bootstrap)
    return {'schema': 'stateless-official-entry-v1', 'objective': 'Finite synthetic workspace; choices and silence remain open.',
            'seed': subject['seed'], 'material_sha256': _hash(raw_subject),
            'stopping': 'one-entry; caller-loop-bounded-by-frozen-Ledger; unknown-stop-no-replay',
            'entry': {'current': {'role': 'chat', 'wake': 'subject-1', 'snapshot_id': snapshot['snapshot_id']},
                      'messages': [{'role': 'system', 'content': 'Frozen bootstrap declaration; subject native driver owns actual requests.'},
                                   {'role': 'user', 'content': bootstrap}]}}


def prepare(root, *, run_id='subject-first-segment', seed=7, max_wakes=3):
    """Create only fictional frozen inputs/templates; no operator grant or Ledger."""
    root = _root(root)
    _require(type(seed) is int and 0 <= seed <= 2147483647 and type(max_wakes) is int and 1 <= max_wakes <= 3,
             'invalid finite subject bounds')
    _require(isinstance(run_id, str) and run_id and len(run_id) <= 80 and all(c.isalnum() or c in '-_' for c in run_id), 'invalid run id')
    root.mkdir(mode=0o700, parents=True)
    (root / 'bootstrap.txt').write_bytes(BOOTSTRAP.encode())
    durable(root / 'world-rules.json', rules(seed))
    ceiling = {'tokens': 2000000, 'micro_cny': 5000000, 'attempts': 12}
    subject = {'schema': 'subject-probe-input-v1', 'root': str(root), 'run_id': run_id, 'seed': seed,
               'max_wakes': max_wakes, 'tools': list(TOOLS), 'role_stages': {'chat': 'question'},
               'budgets': {'research': ceiling, 'stages': {stage: dict(ceiling) if stage == 'question' else
                    {'tokens': 1, 'micro_cny': 1, 'attempts': 1} for stage in capacity.STAGES}},
               'files': {name: _hash((root / name).read_bytes()) for name in INPUT_FILES}, 'code': source_hashes(),
               'selection': 'all authorized current global/main plus committed output/action facts; no selector or background request',
               'clock': 'bootstrap; at most one harness opportunity at tick1; model-requested due ticks; max_wakes is a ceiling'}
    durable(root / 'subject.json', subject)
    durable(root / 'plan.json', _entry(subject, (root / 'subject.json').read_bytes(), BOOTSTRAP))
    template = official.manifest(root, run_id, subject['budgets'], subject['role_stages'], {})
    durable(root / 'official-template.json', template)
    return {'status': 'prepared-not-ready', 'send_authorized': False, 'network': 0,
            'operator_records_required': list(official.FILES[1:]), 'subject_sha256': _hash((root / 'subject.json').read_bytes()),
            'official_binding': official.binding(template)}


def verify_inputs(root):
    """Exact local frozen bytes/code; deliberately separate from operator approval."""
    root = _root(root)
    raw = _read(root, 'subject.json')
    subject = json.loads(raw)
    _require(set(subject) == {'schema', 'root', 'run_id', 'seed', 'max_wakes', 'tools', 'role_stages', 'budgets',
                              'files', 'code', 'selection', 'clock'} and subject['schema'] == 'subject-probe-input-v1'
             and subject['root'] == str(root) and subject['code'] == source_hashes()
             and subject['tools'] == list(TOOLS) and subject['role_stages'] == {'chat': 'question'}
             and type(subject['max_wakes']) is int and 1 <= subject['max_wakes'] <= 3
             and type(subject['seed']) is int and 0 <= subject['seed'] <= 2147483647
             and set(subject['files']) == set(INPUT_FILES), 'subject input declaration drift')
    capacity._validate_budgets(subject)
    ceiling = subject['budgets']['research']
    _require(ceiling == {'tokens': 2000000, 'micro_cny': 5000000, 'attempts': 12}
             and subject['budgets']['stages']['question'] == ceiling, 'subject application ceiling drift')
    for name, expected in subject['files'].items():
        _require(_hash(_read(root, name)) == expected, 'subject material drift')
    bootstrap = _read(root, 'bootstrap.txt').decode('utf-8')
    _require(bootstrap == BOOTSTRAP and json.loads(_read(root, 'world-rules.json')) == rules(subject['seed']),
             'subject rule/bootstrap drift')
    _require(json.loads(_read(root, 'plan.json')) == _entry(subject, raw, bootstrap), 'subject plan/material binding drift')
    template = official.manifest(root, subject['run_id'], subject['budgets'], subject['role_stages'], {})
    _require(json.loads(_read(root, 'official-template.json')) == template, 'subject official template drift')
    if (root / 'manifest.json').exists():
        actual = json.loads(_read(root, 'manifest.json'))
        _require({**actual, 'records': {}} == template, 'subject official manifest drift')
    return subject


def verify(root, *, _calibration=None):
    subject = verify_inputs(root)
    try:
        checked = official.verify(root, _calibration=_calibration)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError):
        return {'status': 'not-ready', 'send_authorized': False, 'network': 0,
                'reason': 'complete applicable operator metadata/allocation/authorization and seal are required'}
    _require(checked['records']['plan.json']['material_sha256'] == _hash(_read(Path(root), 'subject.json')),
             'subject material binding drift')
    return {'status': 'verified-local-records-only', 'send_authorized': False, 'network': 0,
            'max_wakes': subject['max_wakes'], 'metadata_trust': checked['metadata_trust']}


def seal(root):
    """Bind explicitly supplied operator records, without making or granting them."""
    root = _root(root)
    verify_inputs(root)
    _require(not any((root / p).exists() for p in ('execution-claim.json', 'ledger.json', 'manifest.json')), 'run already sealed/claimed')
    template = json.loads(_read(root, 'official-template.json'))
    value = {**template, 'records': {name: _hash(_read(root, name)) for name in official.FILES}}
    durable(root / 'manifest.json', value)
    return verify(root)


class Workspace:
    """One current world file: effects/clock authority, never a second action log."""
    def __init__(self, root, *, create=False):
        self.root = _root(root)
        self.subject = verify_inputs(self.root)
        self.subject_sha = _hash(_read(self.root, 'subject.json'))
        self.rules = json.loads(_read(self.root, 'world-rules.json'))
        self.poisoned = False
        if create:
            _require(not (self.root / 'world.json').exists(), 'world already exists; no replay')
            self._save({'schema': 'subject-world-v1', 'subject_sha256': self.subject_sha, 'revision': 0, 'tick': 0,
                        'objects': deepcopy(self.rules['objects']), 'cards': deepcopy(self.rules['cards']),
                        'effects': {}, 'revisits': {}, 'deliveries': [], 'clocks': [], 'harness_used': False, 'rest': False})
        self._load()

    def _save(self, state):
        try:
            durable(self.root / 'world.json', {'value': state, 'sha256': digest(state)})
        except BaseException:
            self.poisoned = True
            try:
                durable(self.root / 'world-persistence-unknown.json', {'status': 'unknown', 'resume': False})
            except BaseException:
                pass
            raise RuntimeError('world persistence unknown') from None

    def _load(self):
        verify_inputs(self.root)
        _require(not self.poisoned and not any((self.root / n).exists() for n in
                 ('world-persistence-unknown.json', 'world.json.new')), 'world persistence unknown')
        value = json.loads(_read(self.root, 'world.json'))
        state = value['value']
        _require(set(value) == {'value', 'sha256'} and value['sha256'] == digest(state)
                 and state['schema'] == 'subject-world-v1' and state['subject_sha256'] == self.subject_sha,
                 'world evidence drift')
        return state

    def guard(self):
        state = self._load()
        journal = recover_commits(self.root)
        for action, receipt in state['effects'].items():
            _require(action in journal['results'] and journal['results'][action].get('content') == receipt['content']
                     and journal['actions'][action] == receipt['status'], 'world effect/result unknown or drift')
            identity, position = action.rsplit('#', 1)
            call = journal['outputs'][identity]['assistant']['tool_calls'][int(position) - 1]
            _require(call['function']['name'] == receipt['operation']
                     and digest(json.loads(call['function']['arguments'])) == receipt['arguments_sha256'],
                     'world effect parameters drift')
        for action, result in journal['results'].items():
            identity, position = action.rsplit('#', 1)
            call = journal['outputs'][identity]['assistant']['tool_calls'][int(position) - 1]
            if call['function']['name'] in TOOLS:
                _require(action in state['effects'], 'confirmed world result lacks effect authority')
        for action, item in state['revisits'].items():
            receipt = state['effects'].get(action, {})
            observed = json.loads(receipt.get('content', '{}')).get('world_result', {})
            _require(receipt.get('operation') == 'request_revisit' and receipt['status'] == 'completed'
                     and observed.get('revisit_action') == action and observed.get('due_tick') == item['due_tick']
                     and observed.get('reason') == item['reason'] and item['status'] in {'pending', 'delivered', 'declined'},
                     'revisit lacks actual completed request')
        for item in state['deliveries']:
            if item['source'] == 'model-revisit':
                requested = state['revisits'].get(item['request_action'], {})
                _require(requested.get('status') == 'delivered' and requested['due_tick'] <= item['tick'],
                         'revisit delivered without actual due request')
        _require(not any(status == 'unknown' for status in journal['actions'].values()), 'dispatched effect unknown; no replay')
        return state

    def observe(self, state):
        return {'tick': state['tick'], 'objects': deepcopy(state['objects']), 'cards': deepcopy(state['cards']),
                'last_clock': deepcopy(state['clocks'][-1]) if state['clocks'] else None,
                'permission': 'synthetic world only; no score or assigned task'}

    def act(self, action, operation, arguments):
        state = self._load()
        journal = recover_commits(self.root)
        _require(journal['actions'].get(action) == 'unknown' and action not in state['effects'], 'effect requires unique actual dispatch')
        identity, position = action.rsplit('#', 1)
        call = journal['outputs'][identity]['assistant']['tool_calls'][int(position) - 1]
        _require(call['function']['name'] == operation and json.loads(call['function']['arguments']) == arguments,
                 'effect differs from dispatched parameters')
        result, status = None, 'completed'
        if operation == 'observe_workspace':
            _require(arguments == {}, 'invalid observation parameters')
            result = self.observe(state)
        elif operation == 'move_object':
            key, target = arguments['object_id'], arguments['destination']
            if key not in state['objects'] or target not in self.rules['places']:
                result, status = {'refused': 'unknown object/place'}, 'failed'
            else:
                previous = state['objects'][key]
                state['objects'][key] = target
                result = {'object_id': key, 'from': previous, 'to': target, 'tick': state['tick']}
        elif operation == 'write_card':
            key, text = arguments['card_id'], arguments['text']
            if key not in state['cards'] or not isinstance(text, str) or len(text.encode()) > self.rules['card_limit_utf8']:
                result, status = {'refused': 'unknown card or text exceeds512 UTF8 bytes'}, 'failed'
            else:
                state['cards'][key] = text
                result = {'card_id': key, 'text': text, 'tick': state['tick']}
        elif operation == 'request_revisit':
            delay, reason = arguments['delay_ticks'], arguments['reason']
            if type(delay) is not int or not 0 <= delay <= self.rules['revisit_max_delay'] or not isinstance(reason, str) or not 0 < len(reason.encode()) <= 512:
                result, status = {'refused': 'delay must be0..3 and reason1..512 UTF8 bytes'}, 'failed'
            elif delay == 0:
                state['rest'] = True
                for item in state['revisits'].values():
                    if item['status'] == 'pending': item['status'] = 'declined'
                result = {'future_opportunities': 'declined', 'action': action}
            elif state['rest'] or len(state['deliveries']) >= self.subject['max_wakes']:
                result, status = {'refused': 'future opportunities declined or finite wake ceiling'}, 'failed'
            else:
                state['revisits'][action] = {'due_tick': state['tick'] + delay, 'reason': reason, 'status': 'pending'}
                result = {'revisit_action': action, 'due_tick': state['tick'] + delay, 'reason': reason,
                          'delivery': 'bounded opportunity, not guaranteed beyond wake/budget ceiling'}
        else:
            raise ValueError('unsupported world operation')
        content = canonical({'world_result': result}).decode()
        state['effects'][action] = {'operation': operation, 'arguments_sha256': digest(arguments), 'content': content, 'status': status}
        state['revision'] += 1
        self._save(state)
        return content, status

    def deliver(self):
        state = self.guard()
        journal = recover_commits(self.root)
        _require(all(journal['terminals'].get(v['wake_id'], {}).get('status') == 'complete' for v in state['deliveries']),
                 'previous wake not complete; no further opportunity')
        if len(state['deliveries']) >= self.subject['max_wakes'] or state['rest']:
            return None
        if not state['deliveries']:
            source, tick, request = 'bootstrap', 0, None
        else:
            options = []
            if not state['harness_used']: options.append((1, '', 'harness-clock', None))
            for action, item in state['revisits'].items():
                if item['status'] == 'pending': options.append((item['due_tick'], action, 'model-revisit', action))
            if not options: return None
            due, _, source, request = min(options)
            tick = max(state['tick'], due)
            if source == 'harness-clock': state['harness_used'] = True
            else: state['revisits'][request]['status'] = 'delivered'
        for next_tick in range(state['tick'] + 1, tick + 1):
            tilted = ('left', 'right')[(self.rules['seed'] + next_tick) % 2]
            moved = []
            for key, place in state['objects'].items():
                if place == tilted:
                    moved.append({'object_id': key, 'from': place, 'to': 'table'})
                    state['objects'][key] = 'table'
            state['clocks'].append({'tick': next_tick, 'tilted': tilted, 'moved': moved})
        state['tick'] = tick
        delivery = {'wake_id': 'subject-' + str(len(state['deliveries']) + 1), 'source': source,
                    'tick': tick, 'request_action': request}
        state['deliveries'].append(delivery)
        state['revision'] += 1
        self._save(state)
        return deepcopy(delivery)


def recover(root):
    """Read only, including incomplete/expired runs. No client, replay or repair."""
    root = _root(root)
    result = {'schema': 'subject-probe-recovery-v1', 'resume': False, 'network': 0, 'send_authorized': False,
              'official': official.recover(root)}
    for name, function in (('inputs', lambda: verify_inputs(root)), ('journal', lambda: recover_commits(root)),
                           ('notes', lambda: recover_notes(root / 'notes')), ('world', lambda: Workspace(root).guard())):
        try:
            value = function()
            result[name] = {'status': 'readable', 'value': value}
        except (ValueError, RuntimeError, OSError, KeyError, TypeError):
            result[name] = {'status': 'unavailable-or-unknown'}
    return result


def run_probe(root, *, temporary_api_key, _calibration=None, cancelled=lambda: False):
    """One explicit bounded execution, with only the original driver/client."""
    root = _root(root)
    checked = verify(root, _calibration=_calibration)
    _require(checked['status'] == 'verified-local-records-only', 'subject run not ready')
    subject = verify_inputs(root)
    client = (official._calibration_client(root, json.loads(_read(root, 'manifest.json'))) if _calibration is official._CALIBRATION
              else official.OfficialClient(root, json.loads(_read(root, 'manifest.json')), temporary_api_key=temporary_api_key))
    outcomes, failure = [], None
    try:
        notes = NoteWriter.create(root / 'notes')
        world = Workspace(root, create=True)
        snapshot = _snapshot(_read(root, 'bootstrap.txt').decode())
        delivered = []
        while True:
            if cancelled():
                close_session(client, 'cancelled')
                raise RuntimeError('cancelled')
            verify_inputs(root)
            client.guard_tools()
            current_world = world.guard()
            if (current_world['rest'] or len(current_world['deliveries']) >= subject['max_wakes'] or
                    current_world['deliveries'] and current_world['harness_used'] and
                    not any(v['status'] == 'pending' for v in current_world['revisits'].values())):
                break
            # WHY: a due opportunity must not advance the world after the shared
            # next-request reserve already fails. The HTTP hook repeats admission.
            decision = client.admission({'role_id': 'chat', 'task_id': 'subject-' + str(len(delivered) + 1),
                'snapshot_id': snapshot['snapshot_id'], 'run_id': subject['run_id'], 'branch_id': 'main',
                'parent_checkpoint_id': None, 'stage': 'question'})
            _require(decision['allowed'], 'subject next-wake shared budget refused')
            event = world.deliver()
            if event is None: break
            wake = {'id': event['wake_id'], 'reason': event['source'], 'window': 'workspace', 'cursor': 1,
                    'event_key': snapshot['visible_event_keys'][0]}
            delivered.append(wake)
            client.ledger.wake()
            state = recover_commits(root)
            material = snapshot_notes(notes.root, snapshot, allowed_keys=('global/main',))
            outcome = drive_wake(snapshot, wake, delivered, root, client, notes=notes, note_keys=('global/main',),
                materials=material, actions=tuple(state['outputs']), simulation=world, cancelled=cancelled)
            outcomes.append(outcome)
            ids = [key for key, item in client.saved.items() if item['value']['attribution']['task_id'] == wake['id']]
            finish_wake(root, wake['id'], 'cancelled' if outcome['cancelled'] else 'complete', ids, client=client)
            if outcome['cancelled']: raise RuntimeError('cancelled')
        close_session(client, 'finite-subject-segment-ended')
    except BaseException as error:
        import traceback
        failure = {'type': type(error).__name__, 'reason': 'subject segment stopped; inspect structural evidence',
                   'frames': [{'file': Path(v.filename).name, 'function': v.name, 'line': v.lineno}
                              for v in traceback.extract_tb(error.__traceback__)]}
        close_session(client, 'subject-session-failure')
    finally:
        client.close()
    report = {'schema': 'subject-probe-result-v1', 'status': 'stopped' if failure else 'complete', 'failure': failure,
              'outcomes': outcomes, 'trace': client.trace, 'ledger': deepcopy(client.ledger.state),
              'transport': [client.transport_evidence(row['id']) for row in client.ledger.state['requests']],
              'recovery': recover(root), 'execution_mode': 'mock' if _calibration is official._CALIBRATION else 'explicit-official',
              'interpretation': {'formation': 'inspect actual outputs/note receipts', 'selection': 'explicit committed-only caller selection',
                  'injection': 'exact saved request bodies; no candidate scan', 'use': 'not inferred from existence/exposure; human trajectory judgment',
                  'external_result': 'world effect linked to actual dispatch/result; no score'},
              'limits': ['bounded designed world; no autonomous choice/semantic quality proof',
                         'cooperative serial execution; unknown stops, no physical crash or concurrency proof']}
    try:
        durable(root / 'result.json', report)
    except BaseException:
        close_session(client, 'subject-result-persistence-unknown')
        raise RuntimeError('subject result persistence unknown') from None
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('prepare', 'verify', 'seal', 'recover', 'run'), default='verify')
    parser.add_argument('--root', required=True)
    parser.add_argument('--run-id', default='subject-first-segment')
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--max-wakes', type=int, default=3)
    parser.add_argument('--credential-stdin', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command != 'run':
            _require(not args.credential_stdin, 'credential only in explicit run')
            result = prepare(args.root, run_id=args.run_id, seed=args.seed, max_wakes=args.max_wakes) if args.command == 'prepare' else (
                seal(args.root) if args.command == 'seal' else recover(args.root) if args.command == 'recover' else verify(args.root))
        else:
            _require(args.credential_stdin, 'explicit credential stdin required')
            _require(verify(args.root)['status'] == 'verified-local-records-only', 'subject run not ready')
            temporary = sys.stdin.readline().rstrip('\r\n')
            result = run_probe(args.root, temporary_api_key=temporary)
            del temporary
        print(json.dumps(result if args.command != 'run' else {'status': result['status'], 'root': args.root}))
        return 1 if result.get('status') in ('not-ready', 'stopped') else 0
    except BaseException:
        print('subject-probe-refused-or-stopped', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
