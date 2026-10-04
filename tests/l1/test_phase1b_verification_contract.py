"""Deferred selection is explicit and cannot rewrite an earlier intent."""
import json
import pytest

from codex_review import evidence


def should_require_configured_loop_verification_without_new_round_edits():
    import pytest
    from codex_review import apply
    # given a configured loop command and an unchanged reviewed candidate
    target = dict(loop_id='loop', verify_command='python -m pytest')
    # when normalizing verification, then a skipped invocation cannot masquerade as not applicable
    with pytest.raises(apply.ContractViolation, match='submitted result'):
        apply.verification_result('not-applicable', target, [])
    assert apply.verification_result('passed', target, []) == 'passed'
    with pytest.raises(apply.ContractViolation, match='submitted result'):
        apply.verification_result('passed', dict(target, loop_id=None), [])
    assert apply.verification_result(None, dict(target, loop_id=None), []) == 'not-applicable'


def should_freeze_a_deferred_command_once_without_rewriting_capture(review, plan_file_factory, state_root):
    # given a loop whose command selection was explicitly deferred
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--defer-verify', '--reason', 'Choose after inspecting the candidate')
    assert prepared.exit_code == 0, prepared
    loop_id, round_id = prepared.envelope['job_id'], prepared.envelope['payload']['round']
    loop, job = state_root / loop_id, state_root / round_id
    before = (job / 'target.json').read_bytes()
    assert json.loads(before)['verification_intent'] == 'deferred'
    (job / 'staging').mkdir(exist_ok=True)
    (job / 'staging' / 'verify.sh').write_text('python -m pytest\n')
    command = evidence.seal(str(job), round_id, 'staging/verify.sh')['evidence_id']
    (loop / 'staging').mkdir(exist_ok=True)
    def select():
        current = review('status', loop_id).envelope['payload']
        decision = dict(kind='select-verification', rationale='Authorized project suite',
                        round=round_id, command_evidence_id=command,
                        checkpoint_sha256=current['checkpoint_sha256'])
        (loop / 'staging' / 'decision.json').write_text(json.dumps(decision))
        return review('continue', loop_id, '--expected-generation', str(current['generation']),
                      '--decision-file', 'staging/decision.json')
    # when selecting a sealed command
    result = select()
    # then selection is append-only, resets credit and cannot be replaced
    assert result.exit_code == 0, result
    assert result.envelope['payload']['verification_contract']['command'] == 'python -m pytest'
    assert result.envelope['payload']['verification_contract']['intent'] == 'deferred'
    assert result.envelope['payload']['clean_streak']['streak'] == 0
    assert (job / 'target.json').read_bytes() == before
    assert select().envelope['error']['code'] == 'verification-already-selected'


def should_reject_deferred_intent_outside_a_loop(review, plan_file_factory):
    # given an explicit deferred intent with no loop to resolve it
    # when preparing, then do not publish an ambiguous one-pass job
    result = review('prepare', 'plan', str(plan_file_factory()), '--no-repo',
                    '--defer-verify', '--reason', 'later')
    assert result.exit_code == 2
    assert result.envelope['error']['code'] == 'option-not-applicable'


def should_not_record_unresolved_deferred_intent_as_declined(review, plan_file_factory, state_root):
    from codex_review import loops
    # given an applied-stage fixture whose verification selection remains unresolved
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--defer-verify', '--reason', 'choose later')
    loop_id, round_id = prepared.envelope['job_id'], prepared.envelope['payload']['round']
    directory = str(state_root / loop_id)
    current = loops.read(directory)
    loops.append(directory, 0, dict(current, phase='applied'), dict(kind='fixture-stage'))
    # when attempting closure, then resolution precedes reading any verification submission
    result = review('record-verification', loop_id, '--round', round_id, '--expected-generation', '1',
                    '--verification-file', 'staging/verification.json')
    assert result.exit_code == 18, result
    assert result.envelope['error']['details']['reason'] == 'verification-selection-required'
    assert not (state_root / round_id / 'verification.json').exists()


@pytest.mark.parametrize('outcome,finish_first', [('failed', True), ('denied', True),
    ('not-run-explicitly', True), ('failed', False), ('denied', False)])
def should_offer_authorized_recapture_after_verification_without_findings_fails(
        review, plan_file_factory, state_root, codex_reply, tmp_path, outcome, finish_first):
    import subprocess
    from codex_review import apply, images
    from qualification_fixture import qualify_author
    from test_phase1b_loop_interface import qualify_cold
    # given a valid clean review with a configured command and no findings to resolve
    flags = ['--no-verify', '--reason', 'Explicit declination'] if outcome == 'not-run-explicitly' else ['--verify', 'exit 1']
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop', *flags,
                      *(['--effort', 'max'] if not finish_first else []))
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    if not finish_first:
        from codex_review import counting, loops
        # Prior-credit fixture isolates the decision bypass; no live qualification is claimed.
        credit = counting.initial()
        for _ in range(2):
            credit = counting.round_finished(credit, effort='xhigh', eligible=True, accepted=0, blockers=False)
        current = loops.read(str(directory))
        loops.append(str(directory), current['generation'], dict(current, counting=credit), dict(kind='offline-prior-credit-fixture'))
    qualify_cold(state_root, job)
    qualify_author(job, commands=['exit 1\n'])
    def status():
        return review('status', str(directory)).envelope['payload']
    def invoke(verb, *args, **kwargs):
        current = status()
        member = [] if verb in ('continue', 'recapture') else ['--round', current['round']]
        return review(verb, str(directory), *member, '--expected-generation', str(current['generation']), *args, **kwargs)
    def decide(payload):
        (directory / 'staging').mkdir(exist_ok=True)
        (directory / 'staging/decision.json').write_text(json.dumps(dict(payload, checkpoint_sha256=status()['checkpoint_sha256'])))
        return invoke('continue', '--decision-file', 'staging/decision.json')
    def record(name, payload):
        (job / ('staging/' + name + '.json')).write_text(json.dumps(payload))
        return invoke('record-' + name, '--' + name + '-file', 'staging/' + name + '.json')
    log = tmp_path / 'clean.jsonl'
    log.write_text('{"type":"turn.completed"}\n')
    codex_reply(dict(verdict='approve', findings=[], declared_scope=['A0001'], verified_claims=[]))
    assert invoke('run', fake={'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log}).exit_code == 0
    assert decide(dict(kind='read-assessment', round=job.name, disposition='no-observed-breach',
                       report_sha256=status()['report_sha256'], log_sha256=images.sha256_file(str(log)),
                       previous_assessment_sha256=None, event_lines=[], rationale='Complete offline no-read fixture')).exit_code == 0
    assert record('probes', dict(report_sha256=status()['report_sha256'], annotations=[])).exit_code == 0
    assert record('apply', dict(report_sha256=status()['report_sha256'], probes_sha256=status()['probes_sha256'], results=[])).exit_code == 0
    (job / 'staging/verify.sh').write_text('exit 1\n')
    sealed = invoke('seal', 'staging/verify.sh').envelope['payload']
    check = dict(result=outcome, command_evidence_id=sealed['evidence_id'])
    if outcome == 'failed':
        execution = sealed['execution']
        assert execution['ready']
        completed = subprocess.run(execution['argv'], cwd=execution['cwd'], capture_output=True)
        assert completed.returncode == 1
        (job / 'verify.out').write_bytes(completed.stdout + completed.stderr)
        (job / 'verify.rc').write_text('1\n')
        check.update(output_evidence_id=invoke('seal', 'verify.out').envelope['payload']['evidence_id'],
                     rc_evidence_id=invoke('seal', 'verify.rc').envelope['payload']['evidence_id'])
    elif outcome == 'denied':
        # Synthetic user-declination fixture, not a claim about a live host permission prompt.
        check.update(denial_kind='interactive', asserted_by='user')
    else:
        check = dict(result=outcome)
    bad = record('verification', dict(apply_sha256=images.sha256_file(str(job / 'apply.json')),
                                      verification=dict(check, execution=True)))
    assert not (job / 'verification.json').exists(), bad
    assert bad.exit_code == 8, bad
    assert bad.envelope['error']['code'] == 'caller-execution-metadata'
    assert status()['stage'] == 'applied'
    assert record('verification', dict(apply_sha256=images.sha256_file(str(job / 'apply.json')), verification=check)).exit_code == (
        12 if outcome == 'failed' else 13 if outcome == 'denied' else 0)
    if outcome == 'not-run-explicitly':
        assert invoke('continue').exit_code == 0
        assert status()['clean_streak']['streak'] == 1
        return
    before = {p.name: p.read_bytes() for p in job.glob('*.json')}
    # when finishing, then the advertised resolver works without inventing a finding or reopening old records
    if finish_first:
        assert invoke('continue').exit_code == 0
    current = status()
    assert not current['pending_findings']
    if finish_first:
        assert current['pause'] == dict(kind='verification-incomplete', result=outcome)
        assert current['resolver_kinds'] == ['drift-resolution']
        assert current['clean_streak']['streak'] == 0
    else:
        assert current['stage'] == 'round-complete'
        assert current['clean_streak']['streak'] == 2
    (job / 'staging/retry.txt').write_text('Authorized a fresh round after this recorded verification failure')
    citation = evidence.seal(str(job), job.name, 'staging/retry.txt')['evidence_id']
    target = json.loads((job / 'target.json').read_text())
    assert decide(dict(kind='drift-resolution', equivalence='matching', round=job.name,
                       target_sha256=images.sha256_file(str(job / 'target.json')), candidate_sha256=apply.identity_sha(target),
                       old_head=None, new_head=None, authority='Author authorized a fresh verification attempt',
                       evidence_ids=[citation], rationale='Retain the failed round and recapture unchanged inputs')).exit_code == 0
    captured = invoke('recapture')
    assert captured.exit_code == 0, captured
    assert captured.envelope['payload']['round'] != job.name
    assert captured.envelope['payload']['clean_streak']['streak'] == 0
    assert {p.name: p.read_bytes() for p in job.glob('*.json')} == before
    if not finish_first:
        reset = captured.envelope['payload']['clean_streak']
        assert reset['phase'] == 'max'
        next_clean = counting.round_finished(reset, effort='max', eligible=True, accepted=0, blockers=False)
        assert next_clean['streak'] == 1
        assert not next_clean['converged']
