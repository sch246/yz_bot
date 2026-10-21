"""One process, one center, two synthetic windows; no production route or retry.

The caller owns the original bounded client and explicitly runs a returned epoch.
Only commits are authoritative. Runtime handles carry callbacks, never pending.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import uuid

from a0_ledger import canonical
from comparison_snapshot import open_snapshot, _verify_frozen
from stateless_loop import (append_commit, build_screen, close_session, commit_boundary,
                            drive_wake, finish_wake, recover_commits)


def _pending(host, acknowledged):
    return [fact for identity, fact in host['activations'].items()
            if identity not in acknowledged and identity not in host['disposed']]


def project_record(host, kind, value, acknowledged, terminals):
    """Journal admission and recovery use this same finite host projection."""
    if kind == 'host-init':
        if (host is not None or set(value) != {'source', 'targets', 'events'}
                or len(value['targets']) != 2 or len(set(value['targets'].values())) != 2
                or any(not target.startswith('g') or not target[1:].isdecimal() for target in value['targets'].values())
                or any(alias not in value['targets'] for alias in value['events'].values())):
            raise ValueError('invalid synthetic host binding')
        return {**deepcopy(value), 'arrivals': [], 'activations': {}, 'epochs': {},
                'owner': None, 'disposed': [], 'frozen': {}, 'associated': {}}
    if host is None:
        raise ValueError('host record before binding')
    owner = host['owner']
    epoch = value.get('epoch')
    if kind == 'host-arrival':
        seen = {key for row in host['arrivals'] for key in row['event_keys']}
        if (set(value) != {'opened_cursor', 'virtual_time', 'event_keys'}
                or value['opened_cursor'] != len(host['arrivals']) + 1 or not value['event_keys']
                or len(value['event_keys']) != len(set(value['event_keys']))
                or set(value['event_keys']) & seen or set(value['event_keys']) - set(host['events'])
                or type(value['virtual_time']) is not int
                or host['arrivals'] and value['virtual_time'] < host['arrivals'][-1]['virtual_time']):
            raise ValueError('invalid arrival opening')
        host['arrivals'].append(value)
    elif kind == 'host-activate':
        seen = {key for row in host['arrivals'] for key in row['event_keys']}
        if (set(value) != {'id', 'reason', 'window', 'cursor', 'event_key'}
                or value['id'] in host['activations'] or value['event_key'] not in seen
                or value['window'] != host['events'][value['event_key']]
                or value['cursor'] != len(host['arrivals'])):
            raise ValueError('activation before arrival or duplicate identity')
        host['activations'][value['id']] = value
    elif kind == 'host-claim':
        pending = _pending(host, acknowledged)
        if (set(value) != {'epoch', 'origin'} or owner is not None or not pending
                or epoch in host['epochs'] or not isinstance(epoch, str) or len(epoch) != 32
                or value['origin'] != host['targets'][pending[0]['window']]):
            raise ValueError('stale/duplicate owner claim')
        host['epochs'][epoch] = {**value, 'running': False, 'cancelled': False,
                                 'released': False, 'ending': False, 'ending_result': None}
        host['owner'] = epoch
        host['associated'][epoch] = [value['origin']]
    elif kind == 'host-run':
        if owner != epoch or host['epochs'][epoch]['running']:
            raise ValueError('stale or repeated owner run')
        host['epochs'][epoch]['running'] = True
    elif kind == 'host-freeze':
        if (owner != epoch or not host['epochs'][epoch]['running'] or host['epochs'][epoch]['cancelled']
                or value['wake_id'] in host['frozen'] or not value['activations']
                or len(value['activations']) != len(set(value['activations']))
                or set(value['activations']) - {fact['id'] for fact in _pending(host, acknowledged)}
                or any(host['activations'][key]['window'] != value['window'] for key in value['activations'])
                or value['cursor'] != len(host['arrivals'])
                or set(value['event_keys']) - {key for row in host['arrivals'] for key in row['event_keys']}):
            raise ValueError('invalid frozen activation work')
        if any(fact['epoch'] == epoch and wake not in terminals for wake, fact in host['frozen'].items()):
            raise ValueError('owner already has unterminated work')
        host['frozen'][value['wake_id']] = value
        target = host['targets'][value['window']]
        if target not in host['associated'][epoch]:
            host['associated'][epoch].append(target)
    elif kind == 'host-associate':
        if owner != epoch or value['target'] not in host['targets'].values():
            raise ValueError('stale/foreign association')
        if value['target'] not in host['associated'][epoch]:
            host['associated'][epoch].append(value['target'])
    elif kind == 'host-cancel':
        if owner != epoch or host['epochs'][epoch]['cancelled']:
            raise ValueError('stale/duplicate cancel hit')
        host['epochs'][epoch]['cancelled'] = True
    elif kind == 'host-dispose':
        frozen = host['frozen'].get(value['wake_id'])
        if (owner != epoch or not frozen or frozen['epoch'] != epoch
                or value['reason'] not in {'cancelled', 'failed'}
                or value['activations'] != [key for key in frozen['activations'] if key not in acknowledged]
                or set(value['activations']) & set(host['disposed'])):
            raise ValueError('disposition outside frozen unacknowledged work')
        host['disposed'].extend(value['activations'])
    elif kind == 'host-release':
        work = [fact for fact in host['frozen'].values() if fact['epoch'] == epoch]
        if (owner != epoch or value['reason'] not in {'complete', 'cancelled', 'failed'}
                or any(fact['wake_id'] not in terminals for fact in work)
                or any(key not in acknowledged and key not in host['disposed'] for fact in work for key in fact['activations'])
                or value['reason'] == 'complete' and (_pending(host, acknowledged) or host['epochs'][epoch]['cancelled'])):
            raise ValueError('stale release or missed pending activation')
        host['epochs'][epoch]['released'] = True
        host['owner'] = None
    elif kind == 'host-ending':
        if epoch not in host['epochs'] or not host['epochs'][epoch]['released'] or host['epochs'][epoch]['ending']:
            raise ValueError('duplicate/premature ending notification')
        host['epochs'][epoch]['ending'] = True
    elif kind == 'host-ending-result':
        if epoch not in host['epochs'] or not host['epochs'][epoch]['ending'] or host['epochs'][epoch]['ending_result'] is not None:
            raise ValueError('ending result without unique attempt')
        host['epochs'][epoch]['ending_result'] = value
    else:
        raise ValueError('unknown host record')
    return host


def validate_output(host, output):
    frozen = host['frozen'].get(output['wake_id'])
    if (not frozen or output.get('host_epoch') != frozen['epoch'] or host['owner'] != frozen['epoch']
            or host['epochs'][frozen['epoch']]['cancelled'] or output['snapshot_id'] != frozen['snapshot_id']):
        raise ValueError('output outside live frozen owner')
    if output['screen'] is not None:
        if (output['acknowledged'] != frozen['activations']
                or [row['ref']['event_key'] for row in output['screen']['records']] != frozen['event_keys']
                or output['wake_facts'] != [host['activations'][key] for key in frozen['activations']]):
            raise ValueError('host output acknowledges outside frozen screen')


def validate_terminal(host, terminal):
    frozen = host['frozen'].get(terminal['wake_id'])
    if (not frozen or host['owner'] != frozen['epoch']
            or terminal['status'] == 'complete' and host['epochs'][frozen['epoch']]['cancelled']):
        raise ValueError('terminal outside owner or complete after cancellation')


class SyntheticHost:
    """Explicit submit/claim/run, sharing the caller's existing client and budget.

    endpoint(target, text, *, ending) returns status confirmed/failed/unknown and
    message_id for confirmed. Timeout is unknown. It must be a synthetic sink.
    No thread, model client, Ledger, archive importer or auto-resume is created.
    """
    def __init__(self, root, frozen, client, endpoint, *, after_output=None, after_result=None,
                 before_finish=None, after_release=None):
        _verify_frozen(frozen)
        aliases = frozen['binding']['targets']
        if len(aliases) != 2 or frozen['synthetic'] is not True:
            raise ValueError('exactly two frozen synthetic windows required')
        self.root, self.frozen, self.client, self.endpoint = Path(root), frozen, client, endpoint
        if self.root.resolve() != Path(client.root).resolve():
            raise ValueError('host must share original client root')
        self.targets = {alias: 'g' + str(number) for alias, number in aliases.items()}
        self.screen_count = len(frozen['events'])
        self.after_output_hook, self.after_result_hook = after_output, after_result
        self.before_finish, self.after_release = before_finish, after_release
        self.lock = commit_boundary(self.root)
        self._ending_attempted = set()
        binding = {'source': frozen['frozen_id'], 'targets': self.targets,
                   'events': {event['event_key']: event['source_alias'] for event in frozen['events']}}
        with self.lock:
            state = recover_commits(self.root)
            if state['records']:
                # WHY: read-only projection is not automatic restoration of an
                # abandoned owner or a new allowance for a closed client.
                raise ValueError('fresh host journal required; recovery is read-only')
            self._append('host-init', binding)

    def _append(self, kind, value):
        try:
            return append_commit(self.root, kind, value)
        except BaseException:
            close_session(self.client, 'host-commit-failure')
            raise

    def state(self):
        return recover_commits(self.root)

    def pending(self):
        state = self.state()
        return deepcopy(_pending(state['host'], state['acknowledged']))

    def submit(self, event_keys=(), *, activate=(), reason='synthetic-activation'):
        """Commit arrivals then activations; return a new epoch or None.

        activate contains event keys (including previously arrived ordinary
        records). Every activation gets a fresh identity, even for the same key.
        Ordinary arrivals never claim or start generation.
        """
        with self.lock:
            state = self.state()
            host = state['host']
            if event_keys:
                events = {event['event_key']: event for event in self.frozen['events']}
                clock = max([row['virtual_time'] for row in host['arrivals']] +
                            [events[key]['record']['time'] for key in event_keys])
                self._append('host-arrival', {'opened_cursor': len(host['arrivals']) + 1,
                              'virtual_time': clock, 'event_keys': list(event_keys)})
            for key in activate:
                current = self.state()['host']
                self._append('host-activate', {'id': 'activation-' + uuid.uuid4().hex,
                    'reason': reason, 'window': current['events'][key],
                    'cursor': len(current['arrivals']), 'event_key': key})
            return self._claim() if activate else None

    def _claim(self):
        state = self.state()
        host = state['host']
        pending = _pending(host, state['acknowledged'])
        if host['owner'] is not None or not pending or self.client.ledger.poisoned or self.client.ledger.state['closed']:
            return None
        epoch = uuid.uuid4().hex
        self._append('host-claim', {'epoch': epoch, 'origin': self.targets[pending[0]['window']]})
        return epoch

    def cancel(self, epoch, *, target=None):
        """Cancel exactly this live epoch, optionally via an associated window."""
        with self.lock:
            host = self.state()['host']
            if (host['owner'] != epoch or host['epochs'][epoch]['cancelled']
                    or target is not None and target not in host['associated'][epoch]):
                return False
            self._append('host-cancel', {'epoch': epoch})
            # The generator observes this flag at its original stop checkpoints;
            # closing a Ledger while HTTP is settling would corrupt that contract.
            return True

    def release(self, epoch, reason):
        """Identity-checked finalizer: a stale epoch never releases a later one."""
        with self.lock:
            if self.state()['host']['owner'] != epoch:
                return False
            self._append('host-release', {'epoch': epoch, 'reason': reason})
            return True

    def _freeze(self, epoch):
        state = self.state()
        host = state['host']
        pending = _pending(host, state['acknowledged'])
        if not pending:
            return None
        window = pending[0]['window']
        schedule = deepcopy(host['arrivals'])
        opened = {key for row in schedule for key in row['event_keys']}
        remaining = [event for event in self.frozen['events'] if event['event_key'] not in opened]
        if remaining:
            schedule.append({'opened_cursor': len(schedule) + 1,
                'virtual_time': max(schedule[-1]['virtual_time'], max(event['record']['time'] for event in remaining)),
                'event_keys': [event['event_key'] for event in remaining]})
        snapshot = open_snapshot(self.frozen, schedule, len(host['arrivals']))
        wake = {'id': 'host-wake-' + uuid.uuid4().hex, 'reason': pending[0]['reason'],
                'window': window, 'cursor': len(host['arrivals']), 'event_key': pending[0]['event_key']}
        screen = build_screen(snapshot, wake, count=self.screen_count)
        keys = [row['ref']['event_key'] for row in screen['records']]
        covered = [fact for fact in pending if fact['window'] == window and fact['event_key'] in keys]
        self._append('host-freeze', {'epoch': epoch, 'wake_id': wake['id'], 'window': window,
            'cursor': wake['cursor'], 'snapshot_id': snapshot['snapshot_id'], 'event_keys': keys,
            'activations': [fact['id'] for fact in covered]})
        return snapshot, wake, deepcopy(covered)

    def run(self, epoch):
        """Run one claimed epoch once; normal pending continues on this client."""
        with self.lock:
            host = self.state()['host']
            if (host['owner'] != epoch or host['epochs'][epoch]['running']
                    or self.client.ledger.poisoned or self.client.ledger.state['closed']):
                raise ValueError('stale/repeated run or closed original session')
            # Pre-run cancellation leaves unfrozen pending for explicit recovery.
            start_error = None
            try:
                self._append('host-run', {'epoch': epoch})
            except BaseException as failure:
                start_error = failure
            origin = host['epochs'][epoch]['origin']
        binding = _Owner(self, epoch)
        request_ids, outcomes = [], []
        error, active_wake, frozen_ids = None, None, []
        status = 'complete'
        try:
            if start_error is not None:
                raise start_error
            while True:
                with self.lock:
                    if binding.cancelled():
                        status = 'cancelled'
                        close_session(self.client, status)
                        self.release(epoch, status)
                        break
                    work = self._freeze(epoch)
                    if work is None:
                        self.release(epoch, 'complete')
                        break
                    snapshot, wake, facts = work
                    active_wake, frozen_ids = wake['id'], [fact['id'] for fact in facts]
                before = len(self.client.ledger.state['requests'])
                self.client.ledger.wake()
                try:
                    outcome = drive_wake(snapshot, wake, facts, self.root, self.client, host=binding,
                        cancelled=binding.cancelled, after_output=self.after_output_hook)
                finally:
                    request_ids.extend(row['id'] for row in self.client.ledger.state['requests'][before:])
                with self.lock:
                    status = 'cancelled' if outcome['cancelled'] or binding.cancelled() else 'complete'
                    finish_wake(self.root, active_wake, status, request_ids, client=self.client,
                                final_action=outcome['final_action'] if status == 'complete' else None)
                    if status == 'cancelled':
                        state = self.state()
                        self._append('host-dispose', {'epoch': epoch, 'wake_id': active_wake,
                            'reason': 'cancelled', 'activations': [key for key in frozen_ids if key not in state['acknowledged']]})
                outcomes.append(outcome)
                active_wake, frozen_ids = None, []
                if status == 'cancelled':
                    close_session(self.client, status)
                    self.release(epoch, status)
                    break
                if self.before_finish:
                    self.before_finish(epoch)
                # WHY: arrival commit, pending check and release share the same
                # lock. A later submit either extends this owner or claims anew.
                with self.lock:
                    if binding.cancelled():
                        status = 'cancelled'
                        close_session(self.client, status)
                        self.release(epoch, status)
                        break
                    if not self.pending():
                        self.release(epoch, 'complete')
                        break
        except BaseException as failure:
            error, status = failure, 'failed'
            try:
                status = 'cancelled' if binding.cancelled() else 'failed'
            except BaseException:
                pass
            close_session(self.client, status)
            try:
                with self.lock:
                    if active_wake is not None:
                        state = self.state()
                        if active_wake not in state['terminals']:
                            finish_wake(self.root, active_wake, status, request_ids, client=self.client,
                                        reason=type(failure).__name__)
                        self._append('host-dispose', {'epoch': epoch, 'wake_id': active_wake,
                            'reason': status,
                            'activations': [key for key in frozen_ids if key not in state['acknowledged']]})
                    self.release(epoch, status)
            except BaseException:
                # Persistence unknown remains closed; never repair its journal.
                pass
        finally:
            self.release_safely(epoch, status)
            cleanup_error = None
            try:
                if self.after_release:
                    self.after_release(epoch)
            except BaseException as failure:
                cleanup_error = type(failure).__name__
            try:
                self._ending(binding, origin, request_ids, status, cleanup_error=cleanup_error)
            except BaseException:
                pass
        if error is not None:
            raise error
        return {'epoch': epoch, 'origin': origin, 'status': status,
                'request_ids': request_ids, 'wakes': outcomes}

    def release_safely(self, epoch, status):
        try:
            self.release(epoch, status)
        except BaseException:
            pass

    def _ending(self, binding, origin, request_ids, status, *, cleanup_error=None):
        epoch = binding.epoch
        # WHY: a broken commit chain cannot save an attempt. Only this in-process
        # exit notification may still use the fake sink, once; it grants neither
        # tool replay nor permission to reopen the client or journal.
        with self.lock:
            if epoch in self._ending_attempted:
                return
            self._ending_attempted.add(epoch)
        rows = [deepcopy(row) for row in self.client.ledger.state['requests'] if row['id'] in request_ids]
        confirmed, journal_available = [], False
        try:
            state = self.state()
            journal_available = True
            for action, result in state['results'].items():
                output = state['outputs'][action.rsplit('#', 1)[0]]
                if output.get('host_epoch') != epoch or result.get('status') != 'completed':
                    continue
                try:
                    say = json.loads(result.get('content', '{}')).get('say')
                    if say and say['status'] == 'confirmed' and say.get('message_id'):
                        confirmed.append({'action': action, 'target': say['target'], 'message_id': say['message_id']})
                except (ValueError, TypeError):
                    pass
        except BaseException:
            pass
        exposures = 0
        for exchange in self.client.exchanges:
            if exchange.get('request_id') not in request_ids:
                continue
            for message in exchange['body']['messages']:
                try:
                    screen = json.loads(message.get('content', '')).get('screen', {})
                    exposures += len(screen.get('records', []))
                except (ValueError, TypeError, AttributeError):
                    pass
        summary = {'epoch': epoch, 'status': status, 'request_ids': request_ids,
                   'request_usage': [row.get('usage') for row in rows],
                   'request_screen_record_exposures': exposures,
                   'confirmed_endpoint_effects': deepcopy(binding.confirmed_effects),
                   'confirmed_endpoint_count': len(binding.confirmed_effects),
                   'confirmed_say_results': confirmed if journal_available else None,
                   'confirmed_say_count': len(confirmed) if journal_available else None,
                   'journal_available': journal_available, 'cleanup_error': cleanup_error}
        attempt_saved = False
        if journal_available:
            try:
                self._append_ending('host-ending', {'epoch': epoch, 'summary': summary, 'cleanup_error': cleanup_error})
                attempt_saved = True
            except BaseException:
                pass
        try:
            receipt = self._send(origin, canonical(summary).decode(), ending=True)
        except BaseException as failure:
            receipt = {'status': 'unknown', 'error': type(failure).__name__}
        if attempt_saved:
            try:
                self._append_ending('host-ending-result', {'epoch': epoch, 'receipt': receipt})
            except BaseException:
                pass

    def _append_ending(self, kind, value):
        try:
            append_commit(self.root, kind, value)
        except BaseException:
            # A local cleanup rejection cannot close a later owner. Actual
            # unknown journal persistence still closes the shared failure domain.
            if ((self.root / 'commit-persistence-unknown.json').exists()
                    or list((self.root / 'commits').glob('*.new'))):
                close_session(self.client, 'ending-persistence-unknown')
            raise

    def _send(self, target, text, *, ending):
        try:
            receipt = self.endpoint(target, text, ending=ending)
        except TimeoutError:
            receipt = {'status': 'unknown'}
        except Exception as error:
            return {'status': 'unknown', 'error': type(error).__name__}
        if not isinstance(receipt, dict) or receipt.get('status') not in {'confirmed', 'failed', 'unknown'}:
            return {'status': 'unknown'}
        value = {'status': receipt['status'], 'endpoint_receipt': deepcopy(receipt)}
        if type(receipt.get('message_id')) in (str, int) and receipt['message_id']:
            value['message_id'] = receipt['message_id']
        elif value['status'] == 'confirmed':
            value['status'] = 'unknown'
        return value


class _Owner:
    """Bound callbacks cannot follow a mutable current-owner pointer."""
    def __init__(self, host, epoch):
        self.host, self.epoch = host, epoch
        self.targets, self.screen_count = tuple(host.targets.values()), host.screen_count
        self.confirmed_effects = []

    def cancelled(self):
        host = self.host.state()['host']
        return host['owner'] != self.epoch or host['epochs'][self.epoch]['cancelled']

    def associate(self, window):
        target = self.host.targets.get(window, window)
        with self.host.lock:
            append_commit(self.host.root, 'host-associate', {'epoch': self.epoch, 'target': target})

    def say(self, action, text, final_call, target):
        if type(text) is not str or type(final_call) is not bool or target not in self.targets:
            raise ValueError('say requires text, boolean final_call and explicit synthetic target')
        self.associate(target)
        receipt = self.host._send(target, text, ending=False)
        if receipt['status'] == 'confirmed':
            self.confirmed_effects.append({'action': action, 'target': target, 'message_id': receipt['message_id']})
        value = {'target': target, 'text': text, 'final_call': final_call, **receipt}
        return canonical({'say': value}).decode(), 'completed' if receipt['status'] == 'confirmed' else receipt['status']

    def after_result(self, action):
        if self.host.after_result_hook:
            self.host.after_result_hook(self.epoch, action)
