"""Recovery distinguishes a child identity from an outcome."""
import os
import socket
import subprocess
import sys

import pytest

from codex_review import transport


@pytest.mark.parametrize('expired', [False, True])
def should_clear_only_elapsed_availability_pauses_without_launching(
        review, plan_file_factory, state_root, monkeypatch, expired):
    from datetime import datetime, timezone
    from codex_review import loop_driver, loops
    # given a charged no-judgment attempt paused at the provider's named deadline
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'inspection')
    directory = str(state_root / prepared.envelope['job_id'])
    current = loops.read(directory)
    deadline = datetime(2030, 1, 1, tzinfo=timezone.utc)
    paused = dict(current, pause=dict(kind='availability', retry_after=deadline.isoformat()),
                  attempts=[dict(id='a' * 16, round=current['current_round'], outcome='no-judgment', charged=True)])
    loops.append(directory, 0, paused, dict(kind='offline-availability-fixture'))
    monkeypatch.setattr(loop_driver.time, 'time', lambda: deadline.timestamp() + (1 if expired else -1))
    # when continuing, then only elapsed time releases the pause and nothing is launched/refunded
    result = loop_driver.continue_loop(directory, {}, loops.read(directory))
    assert result['exit'] == (0 if expired else 18)
    after = loops.read(directory)
    assert (after['pause'] is None) == expired
    assert after['attempts'] == paused['attempts']
    assert after['counting'] == paused['counting']
    assert after['generation'] == (2 if expired else 1)


def should_identify_a_real_child_then_report_its_absence():
    # given a real long-lived child and its kernel start identity
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        token = transport.process_start(child.pid)
        assert token is not None
        launch = dict(host=socket.gethostname(), pid=child.pid, process_start=token)
        # when inspecting during and after its lifetime
        assert transport.process_state(launch) == 'live'
    finally:
        child.terminate()
        child.wait()
    # then absence means quiescent, not a model outcome
    assert transport.process_state(launch) == 'absent'


def should_not_confuse_a_reused_pid_with_the_original_child(monkeypatch):
    # given the PID exists but belongs to a different kernel start identity
    monkeypatch.setattr(transport, '_process_identity', lambda pid: ('new-start', 'Z'))
    launch = dict(host=socket.gethostname(), pid=os.getpid(), process_start='old-start')
    # when recovery checks the child, then it identifies reuse without claiming completion
    assert transport.process_state(launch) == 'reused'


def should_keep_an_unpublished_launch_identity_uncertain():
    # given a reservation without a durable process identity
    # when recovery inspects it, then missing PID evidence is not proof no launch happened
    assert transport.process_state(None) == 'unknown'


@pytest.mark.parametrize('kind', ['cold', 'rebuttal'])
def should_require_cited_quiescence_before_retiring_a_call_without_launch_identity(
        review, plan_file_factory, state_root, kind):
    import json
    from codex_review import evidence, loops
    # given a reservation interrupted before any launch identity was retained
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'quiescence control')
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    current = loops.read(str(directory))
    attempt = dict(id='a' * 16, round=job.name, kind=kind, outcome='pending', charged=True, return_phase='reviewed')
    current['counting']['streak'] = 1
    loops.append(str(directory), 0, dict(current, phase='calling', attempts=[attempt]), dict(kind='offline-pending-fixture'))
    current = review('status', str(directory)).envelope['payload']
    decision = dict(kind='retire-attempt', attempt=attempt['id'], asserted_by='user',
                    authorization='User authorized retiring the outcome-unknown call',
                    rationale='Explicit quiescence assessment is required; missing PID alone is insufficient',
                    checkpoint_sha256=current['checkpoint_sha256'])
    (directory / 'staging').mkdir()
    path = directory / 'staging' / 'decision.json'
    def submit():
        path.write_text(json.dumps(decision))
        return review('continue', str(directory), '--expected-generation', str(current['generation']),
                      '--decision-file', 'staging/decision.json')
    assert submit().envelope['error']['code'] == 'call-not-quiescent'
    (job / 'staging').mkdir(exist_ok=True)
    (job / 'staging' / 'quiescence.txt').write_text('Isolated fixture: no child was launched; host process inspection is required in real use')
    citation = evidence.seal(str(job), job.name, 'staging/quiescence.txt')['evidence_id']
    decision.update(quiescence_host=socket.gethostname(), quiescence_evidence_ids=[citation])
    launch_dir = job / 'reviewer-attempts' / attempt['id']
    launch_dir.mkdir(parents=True)
    launch_path = launch_dir / 'launch.json'
    launch_path.write_text(json.dumps(dict(host=socket.gethostname(), pid=os.getpid(), process_start=transport.process_start(os.getpid()))))
    assert submit().envelope['error']['code'] == 'call-not-quiescent'
    launch_path.unlink()
    # when the user cites same-host quiescence evidence, then retirement never pretends it proved a non-launch
    result = submit()
    assert result.exit_code == 0, result
    assert result.envelope['payload']['budget']['launched'] == 1
    assert result.envelope['payload']['call_outcomes']['retired_unknown'] == [attempt['id']]
    assert result.envelope['payload']['stage'] == ('recapture' if kind == 'cold' else 'reviewed')
    assert result.envelope['payload']['clean_streak']['streak'] == (0 if kind == 'cold' else 1)


@pytest.mark.parametrize('lost_receipt, missing_logs', [(False, False), (True, False), (True, True)])
def should_recover_a_published_judgment_without_relaunching(review, plan_file_factory, state_root, codex_reply, tmp_path, lost_receipt, missing_logs):
    import json
    from codex_review import loops
    from test_phase1b_loop_interface import qualify_cold
    # given a completed call whose final loop transition was lost
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'inspection')
    loop_id, round_id = prepared.envelope['job_id'], prepared.envelope['payload']['round']
    directory = state_root / loop_id
    qualify_cold(state_root, state_root / round_id)
    codex_reply(dict(verdict='approve', findings=[], declared_scope=['A0001'], verified_claims=[]))
    assert review('run', loop_id, '--round', round_id, '--expected-generation', '0',
                  fake={'CODEX_FAKE_RESPONSE': codex_reply.path}).exit_code == 0
    # Simulate death after report publication, before complete-call append.
    (directory / 'transitions' / '00000002.json').unlink()
    pending = review('status', loop_id).envelope['payload']
    assert pending['budget']['pending'] == 1
    attempt_dir = state_root / round_id / 'reviewer-attempts' / pending['latest_attempt']['id']
    if lost_receipt:
        (attempt_dir / 'completion.json').unlink()
        (state_root / round_id / 'report.json').unlink()
        (state_root / round_id / 'response.json').unlink()
    if missing_logs:
        (attempt_dir / 'stdout.jsonl').unlink()
        (attempt_dir / 'stderr.txt').unlink()
    decision = dict(kind='resolve-call', attempt=pending['latest_attempt']['id'],
                    checkpoint_sha256=pending['checkpoint_sha256'], rationale='Reconcile retained receipt and report')
    (directory / 'staging').mkdir(exist_ok=True)
    (directory / 'staging' / 'resolution.json').write_text(json.dumps(decision))
    marker = tmp_path / 'unexpected-launch'
    if lost_receipt:
        retirement = dict(decision, kind='retire-attempt', asserted_by='user', authorization='Requested retirement')
        (directory / 'staging' / 'retirement.json').write_text(json.dumps(retirement))
        refused = review('continue', loop_id, '--expected-generation', '1', '--decision-file', 'staging/retirement.json')
        assert refused.envelope['error']['code'] == 'outcome-recoverable'
    # when resolving from durable output
    resolved = review('continue', loop_id, '--expected-generation', '1', '--decision-file',
                      'staging/resolution.json', fake={'CODEX_FAKE_ARGV': marker})
    # then the same launch is charged once and routed to assessment, never called again
    assert resolved.exit_code == 0, resolved
    current = loops.read(str(directory))
    assert current['phase'] == 'assessment'
    assert len(current['attempts']) == 1
    assert current['attempts'][0]['outcome'] == 'judgment'
    assert not marker.exists()
    if lost_receipt:
        timing = json.loads((state_root / round_id / 'report.json').read_text())['run_metadata']['timing']
        assert timing['complete'] is False
        assert timing['cpu_user_seconds'] is None
        assert timing['wall_seconds'] is None
        assert not (attempt_dir / 'completion.json').exists()
    if missing_logs:
        current_status = review('status', loop_id).envelope['payload']
        assert current_status['latest_attempt']['thread_id'] is None
        assessment = dict(kind='read-assessment', round=round_id, disposition='no-observed-breach',
                          report_sha256=current_status['report_sha256'], log_sha256=None,
                          previous_assessment_sha256=None, event_lines=[], rationale='Logs were lost',
                          checkpoint_sha256=current_status['checkpoint_sha256'])
        (directory / 'staging/assessment.json').write_text(json.dumps(assessment))
        refused = review('continue', loop_id, '--expected-generation', str(current_status['generation']),
                         '--decision-file', 'staging/assessment.json')
        assert refused.envelope['error']['code'] == 'incomplete-read-assessment'
        assessment['disposition'] = 'read-unverified'
        (directory / 'staging/assessment.json').write_text(json.dumps(assessment))
        accepted = review('continue', loop_id, '--expected-generation', str(current_status['generation']),
                          '--decision-file', 'staging/assessment.json')
        assert accepted.exit_code == 0, accepted
        assert round_id in loops.read(str(directory))['disqualified_rounds']


def should_preserve_the_provider_deadline_when_recovering_a_no_judgment_call(
        review, plan_file_factory, state_root, tmp_path):
    import json
    from test_phase1b_loop_interface import qualify_cold
    # given a quota failure interrupted before its loop completion transition
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'inspection')
    loop_id, round_id = prepared.envelope['job_id'], prepared.envelope['payload']['round']
    directory = state_root / loop_id
    qualify_cold(state_root, state_root / round_id)
    log = tmp_path / 'quota.jsonl'
    log.write_text('{"type":"error","message":"try again at Jan 1st, 2030 10:08 AM"}\n')
    result = review('run', loop_id, '--round', round_id, '--expected-generation', '0',
                    fake={'CODEX_FAKE_STDOUT': log, 'CODEX_FAKE_RC': 1})
    assert result.exit_code == 7, result
    deadline = result.envelope['payload']['pause']['retry_after']
    (directory / 'transitions' / '00000002.json').unlink()
    pending = review('status', loop_id).envelope['payload']
    (directory / 'staging').mkdir(exist_ok=True)
    (directory / 'staging' / 'resolution.json').write_text(json.dumps(dict(
        kind='resolve-call', attempt=pending['latest_attempt']['id'],
        checkpoint_sha256=pending['checkpoint_sha256'], rationale='Recover recorded quota outcome')))
    # when the original outcome is recovered, then its deadline still blocks another launch
    resolved = review('continue', loop_id, '--expected-generation', '1', '--decision-file', 'staging/resolution.json')
    assert resolved.exit_code == 0, resolved
    assert resolved.envelope['payload']['pause'] == dict(kind='availability', retry_after=deadline)
    assert resolved.envelope['payload']['budget']['no_judgment'] == 1
    assert resolved.envelope['payload']['next_verb'] is None


def should_resolve_relative_provider_deadlines_against_call_completion_not_recovery_day(
        review, plan_file_factory, state_root, tmp_path):
    import json
    from datetime import datetime
    from codex_review import reviewer
    # given retained output from a call completed on a different day
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--no-verify', '--reason', 'inspection')
    job = state_root / prepared.envelope['job_id']
    log = tmp_path / 'relative-quota.jsonl'
    log.write_text('{"type":"error","message":"try again at 3:35 PM"}\n')
    assert review('run', str(job), fake={'CODEX_FAKE_STDOUT': log}).exit_code == 7
    attempt = next((job / 'reviewer-attempts').iterdir())
    receipt_path = attempt / 'completion.json'
    receipt = json.loads(receipt_path.read_text())
    receipt['completed_at'] = datetime(2030, 1, 1, 14, 0).timestamp()
    receipt_path.write_text(json.dumps(receipt))
    # when recollecting later, then the original date anchors the deadline
    recovered = reviewer.collected(str(attempt))
    assert recovered['retry_after'] == datetime(2030, 1, 1, 15, 35).astimezone().isoformat()


@pytest.mark.parametrize('outcome,count', [('judgment', 16), ('no-judgment', 4)])
def should_surface_budget_causes_and_route_to_explicit_extension(
        review, plan_file_factory, state_root, outcome, count):
    from codex_review import loops
    from test_phase1b_loop_interface import qualify_cold
    # given exhausted calls with unresolved defects and disputes
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'budget control')
    directory = str(state_root / prepared.envelope['job_id'])
    job = state_root / prepared.envelope['payload']['round']
    qualify_cold(state_root, job)
    current = loops.read(directory)
    attempts = [dict(id=str(i), round=job.name, kind='cold', outcome=outcome, failure_kind='unavailable') for i in range(count)]
    ledger = [dict(reference=dict(round=job.name, local_id='F-1'), status='open', disposition='accepted'),
              dict(reference=dict(round=job.name, local_id='F-2'), status='open', disposition='defer')]
    loops.append(directory, 0, dict(current, attempts=attempts, findings=ledger), dict(kind='offline-budget-fixture'))
    # when launch is refused, then structured causes distinguish work remaining from availability
    result = review('run', directory, '--round', job.name, '--expected-generation', '1')
    assert result.exit_code == 18, result
    details = result.envelope['error']['details']
    assert details['reason'] == 'budget-exhausted'
    assert details['causes']['accepted_defects'] == [ledger[0]['reference']]
    assert details['causes']['disputes'] == [ledger[1]['reference']]
    assert details['causes']['unavailability'] == ([str(i) for i in range(count)] if outcome == 'no-judgment' else [])
    status = review('status', directory).envelope['payload']
    assert status['next_verb'] is None
    assert status['pause']['kind'] == 'budget-exhausted'
    assert status['resolver_kinds'] == ['budget-extension']
    assert status['generation'] == 1


@pytest.mark.parametrize('kind', ['cold', 'rebuttal'])
@pytest.mark.parametrize('contradiction', [None, 'bad-marker', 'launched-child'])
def should_recover_durable_nonlaunch_without_a_completion_receipt(
        review, plan_file_factory, state_root, monkeypatch, contradiction, kind):
    import json
    from codex_review import loops, state
    # given a pending call whose Popen failed and whose receipt publication was interrupted
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'launch interruption control')
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    attempt = dict(id='d' * 16, round=job.name, kind=kind, outcome='pending', charged=True, return_phase='reviewed')
    current = loops.read(str(directory))
    current['counting']['streak'] = 1
    loops.append(str(directory), 0, dict(current, phase='calling', attempts=[attempt]), dict(kind='offline-reservation-fixture'))
    attempt_dir = job / 'reviewer-attempts' / attempt['id']
    attempt_dir.mkdir(parents=True)
    write = state.atomic_json
    def fail_launch(*args, **kwargs):
        raise OSError('controlled executable launch failure')
    def interrupt_after_marker(path, value):
        write(path, value)
        if str(path).endswith('/not-launched.json'):
            raise RuntimeError('interrupted after durable nonlaunch')
    with monkeypatch.context() as patch:
        patch.setattr(transport.subprocess, 'Popen', fail_launch)
        patch.setattr(state, 'atomic_json', interrupt_after_marker)
        with pytest.raises(RuntimeError, match='durable nonlaunch'):
            transport.launch(['/isolated/fixture/codex'], str(attempt_dir), str(job))
    assert not (attempt_dir / 'completion.json').exists()
    if contradiction == 'bad-marker':
        (attempt_dir / 'not-launched.json').write_text('{}')
    if contradiction == 'launched-child':
        (attempt_dir / 'launch.json').write_text(json.dumps(dict(host=socket.gethostname(), pid=os.getpid(),
                                                               process_start=transport.process_start(os.getpid()))))
    # when resolving from the durable transport evidence, then only a proven nonlaunch cancels the reservation
    current = review('status', str(directory)).envelope['payload']
    decision = dict(kind='resolve-call', attempt=attempt['id'], checkpoint_sha256=current['checkpoint_sha256'],
                    rationale='The retained transport marker proves Popen failed before any child existed')
    (directory / 'staging').mkdir()
    (directory / 'staging/decision.json').write_text(json.dumps(decision))
    resolved = review('continue', str(directory), '--expected-generation', str(current['generation']),
                      '--decision-file', 'staging/decision.json')
    if contradiction:
        assert resolved.exit_code == 8, resolved
        assert review('status', str(directory)).envelope['payload']['budget']['pending'] == 1
    else:
        assert resolved.exit_code == 0, resolved.envelope
        assert resolved.envelope['payload']['budget']['launched'] == 0
        assert resolved.envelope['payload']['clean_streak']['streak'] == 1
        assert resolved.envelope['payload']['stage'] == ('prepared' if kind == 'cold' else 'reviewed')
        assert resolved.envelope['payload']['latest_attempt']['outcome'] == 'not-launched'


@pytest.mark.parametrize('damage', ['lost-answer', 'lost-logs', 'wrong-attempt', 'malformed-report', 'non-object', 'bad-metadata'])
def should_recover_a_surviving_published_finding_before_permitting_retirement(
        review, plan_file_factory, state_root, codex_reply, tmp_path, damage):
    import json
    from codex_review import loops
    from test_phase1b_loop_interface import qualify_cold
    from test_record_probes import RESPONSE
    # given publication survived but the completion transition and transient output did not
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'offline recovery fixture')
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    qualify_cold(state_root, job)
    codex_reply(RESPONSE)
    assert review('run', str(directory), '--round', job.name, '--expected-generation', '0',
                  fake={'CODEX_FAKE_RESPONSE': codex_reply.path}).exit_code == 0
    (directory / 'transitions/00000002.json').unlink()
    current = review('status', str(directory)).envelope['payload']
    attempt = current['latest_attempt']['id']
    path = job / 'reviewer-attempts' / attempt
    (path / 'completion.json').unlink()
    (path / 'last-message.txt').unlink()
    if damage == 'lost-logs':
        (path / 'stdout.jsonl').unlink()
        (path / 'stderr.txt').unlink()
    if damage in ('wrong-attempt', 'malformed-report', 'non-object', 'bad-metadata'):
        report = json.loads((job / 'report.json').read_text())
        if damage == 'wrong-attempt':
            report['run_metadata']['attempt_dir'] += '-other'
        elif damage == 'non-object':
            report = []
        elif damage == 'bad-metadata':
            report['run_metadata'] = []
        else:
            report['findings'][0]['id'] = None
        (job / 'report.json').write_text(json.dumps(report))
    original = (job / 'report.json').read_bytes()
    (directory / 'staging').mkdir(exist_ok=True)
    def decide(kind):
        payload = dict(kind=kind, attempt=attempt, checkpoint_sha256=current['checkpoint_sha256'],
                       rationale='Recover the published judgment', asserted_by='user', authorization='Offline fixture')
        (directory / 'staging/decision.json').write_text(json.dumps(payload))
        return review('continue', str(directory), '--expected-generation', str(current['generation']),
                      '--decision-file', 'staging/decision.json')
    # when recovery or retirement is requested, then valid publication is adopted and suspect publication is not discarded
    retired = decide('retire-attempt')
    assert retired.exit_code == 8, retired
    recovered = decide('resolve-call')
    if damage in ('wrong-attempt', 'malformed-report', 'non-object', 'bad-metadata'):
        assert recovered.exit_code == 8
        assert loops.read(str(directory))['phase'] == 'calling'
    else:
        assert retired.envelope['error']['code'] == 'outcome-recoverable'
        assert recovered.exit_code == 0, recovered
        state = loops.read(str(directory))
        assert state['phase'] == 'assessment'
        assert state['attempts'][0]['outcome'] == 'judgment'
        assert json.loads((job / 'report.json').read_text())['findings'] == RESPONSE['findings']
    assert (job / 'report.json').read_bytes() == original


@pytest.mark.parametrize('kernel_state,expected', [('S', 'live'), ('T', 'live'), ('Z', 'exited'), ('X', 'exited')])
def should_distinguish_linux_exited_processes_from_live_identities(monkeypatch, kernel_state, expected):
    import io
    from codex_review import quiescence, response
    # given a matching kernel start token with an explicitly sampled Linux process state
    def read_proc(path, *args, **kwargs):
        if path == '/proc/4242/stat':
            return io.StringIO('4242 (reviewer (child)) ' + ' '.join([kernel_state] + ['0'] * 18 + ['123456']))
        assert path == '/proc/sys/kernel/random/boot_id'
        return io.StringIO('boot-fixture\n')
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr('builtins.open', read_proc)
    monkeypatch.setattr(os, 'kill', lambda pid, signal: None)
    launch = dict(host=socket.gethostname(), pid=4242, process_start='boot-fixture:123456')
    # when recovery checks quiescence, then stopped live children remain blocked but exited ones can be reconciled
    assert transport.process_start(4242) == launch['process_start']
    assert transport.process_state(launch) == expected
    if expected == 'live':
        with pytest.raises(response.ContractViolation, match='quiescence'):
            quiescence.require(launch, {}, '/unused', completion=True)
    else:
        assert quiescence.require(launch, {}, '/unused')['process_state'] == 'exited'


@pytest.mark.parametrize('answer', [False, True])
def should_recover_an_unreaped_linux_child_without_inventing_an_outcome(
        review, plan_file_factory, state_root, monkeypatch, answer):
    import io
    import json
    from codex_review import loop_driver, loops, response
    # given a published reservation whose child exited without a completion receipt
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'recovery control')
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    current = loops.read(str(directory))
    attempt = dict(id='a' * 16, round=job.name, kind='cold', outcome='pending', charged=True)
    pending = dict(current, phase='calling', attempts=[attempt])
    path = job / 'reviewer-attempts' / attempt['id']
    path.mkdir(parents=True)
    (path / 'launch.json').write_text(json.dumps(dict(host=socket.gethostname(), pid=4242,
                                                    process_start='boot-fixture:123456')))
    if answer:
        (path / 'last-message.txt').write_text(json.dumps(dict(verdict='approve', findings=[], declared_scope=['A0001'], verified_claims=[])))
        (path / 'invocation.json').write_text(json.dumps(dict(codex_path='offline-fixture', model='fixture-model', effort='xhigh', cwd=str(job))))
    real_open = open
    def read_proc(name, *args, **kwargs):
        if name == '/proc/4242/stat':
            return io.StringIO('4242 (reviewer) ' + ' '.join(['Z'] + ['0'] * 18 + ['123456']))
        if name == '/proc/sys/kernel/random/boot_id':
            return io.StringIO('boot-fixture\n')
        return real_open(name, *args, **kwargs)
    monkeypatch.setenv('CODEX_REVIEW_STATE', str(state_root))
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr('builtins.open', read_proc)
    monkeypatch.setattr(os, 'kill', lambda pid, signal: None)
    decision = dict(kind='resolve-call', attempt=attempt['id'], rationale='Inspect retained outcome')
    # when recovering, then only an actual validated answer becomes a judgment
    if answer:
        recovered = loop_driver._recover_call(str(directory), pending, decision)
        assert recovered['phase'] == 'assessment'
        assert recovered['attempts'][-1]['outcome'] == 'judgment'
        assert (job / 'report.json').exists()
    else:
        with pytest.raises(response.ContractViolation) as error:
            loop_driver._recover_call(str(directory), pending, decision)
        assert error.value.code == 'call-outcome-unknown'
        recovered = loop_driver._recover_call(str(directory), pending, dict(decision,
            kind='retire-attempt', asserted_by='user', authorization='Explicit fixture retirement authorization'))
        assert recovered['phase'] == 'recapture'
        assert recovered['attempts'][-1]['outcome'] == 'retired-unknown'
        assert recovered['attempts'][-1]['quiescence']['process_state'] == 'exited'
        assert not (job / 'report.json').exists()
    assert recovered['attempts'][-1]['charged']
    assert not recovered['counting']['converged']


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Requires Linux procfs and an unreaped child')
def should_identify_a_real_linux_zombie_as_quiescent():
    from codex_review import quiescence
    # given our own child, exited but deliberately not yet reaped
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    try:
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
        launch = dict(host=socket.gethostname(), pid=pid, process_start=transport.process_start(pid))
        # when inspecting recovery, then termination is established without asserting an answer
        assert transport.process_state(launch) == 'exited'
        assert quiescence.require(launch, {}, '/unused')['process_state'] == 'exited'
    finally:
        os.waitpid(pid, 0)
