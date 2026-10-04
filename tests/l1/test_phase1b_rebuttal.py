"""Resumed adjudication stays bound to one original thread and cannot grant clean credit."""
import json

import pytest

from codex_review import evidence, images, loop_driver
from qualification_fixture import qualify_fixture
from test_phase1b_loop_interface import qualify_cold
from test_record_probes import RESPONSE

THREAD = 'a2c80d10-8dbb-4426-9089-f1d0b3a88e23'


@pytest.fixture
def disputed_round(review, plan_file_factory, state_root, codex_reply, tmp_path, request):
    effort = ['--effort', 'max'] if getattr(request, 'param', None) == 'max' else []
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'offline adjudication fixture', *effort)
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    qualify_cold(state_root, job)
    original = RESPONSE
    if getattr(request, 'param', None) == 'pre-existing':
        original = dict(RESPONSE, verdict='approve', findings=[dict(RESPONSE['findings'][0], classification='pre-existing')])
    codex_reply(original)
    log = tmp_path / 'cold.jsonl'
    log.write_text(json.dumps(dict(type='thread.started', thread_id=THREAD)) + '\n{"type":"turn.completed"}\n')
    assert review('run', str(directory), '--round', job.name, '--expected-generation', '0',
                  fake={'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log}).exit_code == 0
    current = review('status', str(directory)).envelope['payload']
    (directory / 'staging').mkdir()
    decision = dict(kind='read-assessment', round=job.name, disposition='no-observed-breach',
                    rationale='Complete offline fixture log contains no reads', event_lines=[],
                    previous_assessment_sha256=None, checkpoint_sha256=current['checkpoint_sha256'],
                    report_sha256=images.sha256_file(str(job / 'report.json')), log_sha256=images.sha256_file(str(log)))
    (directory / 'staging' / 'decision.json').write_text(json.dumps(decision))
    assert review('continue', str(directory), '--expected-generation', str(current['generation']),
                  '--decision-file', 'staging/decision.json').exit_code == 0
    (job / 'staging' / 'argument.txt').write_text('Counterexample and governing requirement, supplied as data')
    sealed = evidence.seal(str(job), job.name, 'staging/argument.txt')['evidence_id']
    target = json.loads((job / 'target.json').read_text())
    qualify_fixture(state_root, job, loop_driver.launch_key(target, 'resume'))
    return directory, job, sealed, log


def rebut(review, fixture, fake):
    directory, job, sealed, _ = fixture
    current = review('status', str(directory)).envelope['payload']
    payload = dict(checkpoint_sha256=current['checkpoint_sha256'],
                   report_sha256=images.sha256_file(str(job / 'report.json')),
                   probes_sha256=current['probes_sha256'],
                   claims=[dict(reference=dict(round=job.name, local_id='F-1'),
                                rationale='The sealed counterexample contradicts the claim', evidence_ids=[sealed])])
    (job / 'staging' / 'rebuttal.json').write_text(json.dumps(payload))
    return review('rebut', str(directory), '--round', job.name, '--expected-generation', str(current['generation']),
                  '--rebuttal-file', 'staging/rebuttal.json', fake=fake)


def assess_rebuttal(review, directory, job, disposition='no-observed-breach'):
    current = review('status', str(directory)).envelope['payload']
    attempt = current['latest_attempt']
    requirement = next(r for r in current['read_assessment_requirements'] if r['attempt'] == attempt['id'])
    decision = dict(kind='read-assessment', round=job.name, attempt=attempt['id'], disposition=disposition,
                    rationale='Complete offline resumed-log fixture; its originating review history is intentional',
                    event_lines=[], previous_assessment_sha256=requirement['previous_assessment_sha256'],
                    checkpoint_sha256=current['checkpoint_sha256'],
                    adjudication_sha256=current['adjudication']['sha256'],
                    log_sha256=images.sha256_file(attempt['attempt_dir'] + '/stdout.jsonl'))
    (directory / 'staging/decision.json').write_text(json.dumps(decision))
    return review('continue', str(directory), '--expected-generation', str(current['generation']),
                  '--decision-file', 'staging/decision.json')


def should_resume_the_exact_thread_once_and_keep_the_original_judgment(
        review, disputed_round, codex_reply, codex_argv):
    # given a disputed original finding and qualified resume fixture
    directory, job, _, log = disputed_round
    original = (job / 'report.json').read_bytes()
    codex_reply(dict(decisions=[dict(id='F-1', classification='introduced', outcome='withdrawn',
                                    rationale='The counterexample refutes this claim')], new_findings=[]))
    # when one adjudication resumes the original thread
    result = rebut(review, disputed_round, {'CODEX_FAKE_RESPONSE': codex_reply.path,
                                          'CODEX_FAKE_STDOUT': log, 'CODEX_FAKE_ARGV': codex_argv})
    assert result.exit_code == 0, result
    args = json.loads(codex_argv.read_text())
    assert args[:2] == ['exec', 'resume']
    assert THREAD in args and '--last' not in args
    assert '-C' not in args and '-s' not in args
    assert 'sandbox_mode="read-only"' in args
    assert '--ignore-user-config' in args and '--ignore-rules' in args
    # then original evidence survives, adjudication costs a call but earns no credit, and retry refuses
    assert (job / 'report.json').read_bytes() == original
    assert result.envelope['payload']['budget']['judgments'] == 2
    assert result.envelope['payload']['clean_streak']['streak'] == 0
    assert result.envelope['payload']['next_verb'] == 'continue'
    assert assess_rebuttal(review, directory, job).exit_code == 0
    second = rebut(review, disputed_round, {})
    assert second.exit_code == 18, second
    assert second.envelope['error']['details']['reason'] == 'rebuttal-already-launched'


@pytest.mark.parametrize('reply,expected', [('', 7), ('{"invalid":true}', 8), (b'\xff', 8)])
def should_charge_an_unsuccessful_resume_without_withdrawing_or_retrying(
        review, disputed_round, codex_reply, fake_codex, reply, expected):
    # given a resumption that fails to produce a valid adjudication
    directory, job, _, log = disputed_round
    if isinstance(reply, bytes):
        fake_codex.write_text(fake_codex.read_text().replace(
            "open(out, 'w').write(body)", "open(out, 'wb').write(" + repr(reply) + ")"))
    else:
        codex_reply(reply)
    # when the attempt completes, then it is charged, pauses the dispute and cannot retry
    result = rebut(review, disputed_round, {'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log})
    assert result.exit_code == expected, result
    assert result.envelope['payload']['budget']['launched'] == 2
    assert result.envelope['payload']['budget']['no_judgment'] == 1
    assert result.envelope['payload']['pause']['kind'] == 'stalled-dispute'
    if isinstance(reply, bytes):
        assert result.envelope['error']['code'] == 'malformed-encoding'
        assert any(p.read_bytes() == reply for p in (job / 'reviewer-attempts').glob('*/last-message.txt'))
    assert rebut(review, disputed_round, {}).envelope['error']['details']['reason'] == 'rebuttal-already-launched'


def should_refuse_a_missing_original_thread_without_spending_a_rebuttal(review, disputed_round, tmp_path):
    from codex_review import loops
    # given a judgment whose launch never emitted a session ID
    directory, job, _, _ = disputed_round
    current = loops.read(str(directory))
    current['attempts'][0]['thread_id'] = None
    loops.append(str(directory), current['generation'], current, dict(kind='offline-missing-id-fixture'))
    marker = tmp_path / 'unexpected-resume'
    # when rebutting, then refusal happens before transport or accounting
    refused = rebut(review, disputed_round, {'CODEX_FAKE_ARGV': marker})
    assert refused.exit_code == 18, refused
    assert refused.envelope['error']['details']['reason'] == 'session-not-resumable'
    assert not marker.exists()
    assert review('status', str(directory)).envelope['payload']['budget']['launched'] == 1


@pytest.mark.parametrize('recovery', [None, 'receipt', 'published-adjudication'])
def should_complete_a_rebuttal_that_collides_with_retained_author_work(
        review, disputed_round, codex_reply, recovery):
    from codex_review import loops
    # given an effective work report containing an independently retained finding
    directory, job, _, log = disputed_round
    alias = 'retained-' + 'a' * 16 + '-F-9'
    original = (job / 'report.json').read_bytes()
    retained = dict(RESPONSE['findings'][0], id=alias)
    report = job / 'retained-findings' / 'report.json'
    report.parent.mkdir()
    report.write_text(json.dumps(dict(RESPONSE, findings=RESPONSE['findings'] + [retained])))
    current = loops.read(str(directory))
    loops.append(str(directory), current['generation'], dict(current, round_records={job.name: dict(
        report='retained-findings/report.json', probes='probes.json')}), dict(kind='offline-retained-work-fixture'))
    assert record_rejections(review, directory, job, ['F-1', alias]).exit_code == 0
    before = report.read_bytes()
    codex_reply(dict(decisions=[dict(id='F-1', classification='introduced', outcome='maintained',
                                    rationale='Original claim remains')], new_findings=[retained]))
    # when a resumed answer reuses that ID, then it is a completed malformed answer, not a stuck call
    result = rebut(review, disputed_round, {'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log})
    assert result.exit_code == 8, result
    assert result.envelope['error']['code'] == 'reused-finding-id'
    status = review('status', str(directory)).envelope['payload']
    adjudication_path = job / 'reviewer-attempts' / status['latest_attempt']['id'] / 'adjudication.json'
    assert not adjudication_path.exists()
    if recovery:
        current = loops.read(str(directory))
        (directory / 'transitions' / ('%08d.json' % current['generation'])).unlink()
        if recovery == 'published-adjudication':
            adjudication_path.write_text(codex_reply.path.read_text())
        status = review('status', str(directory)).envelope['payload']
        assert status['latest_attempt']['outcome'] == 'pending'
        decision = dict(kind='resolve-call', attempt=status['latest_attempt']['id'],
                        checkpoint_sha256=status['checkpoint_sha256'], rationale='Recover the completed malformed reply')
        (directory / 'staging/decision.json').write_text(json.dumps(decision))
        recovered = review('continue', str(directory), '--expected-generation', str(status['generation']),
                           '--decision-file', 'staging/decision.json')
        assert recovered.exit_code == 0, recovered
        status = review('status', str(directory)).envelope['payload']
    assert status['latest_attempt']['outcome'] == 'no-judgment'
    assert status['budget']['no_judgment'] == 1
    assert status['pause']['kind'] == 'stalled-dispute'
    assert (job / 'report.json').read_bytes() == original
    assert report.read_bytes() == before
    assert adjudication_path.exists() == (recovery == 'published-adjudication')
    assert not adjudication_path.with_name('effective-report.json').exists()
    assert rebut(review, disputed_round, {}).envelope['error']['details']['reason'] == 'rebuttal-already-launched'
    assert review('abandon', str(directory), '--expected-generation', str(status['generation']),
                  '--reason', 'Stop this completed malformed dispute').exit_code == 0


@pytest.mark.parametrize('recovered', [False, True])
def should_keep_resume_quota_deadlines_independent_of_dispute_state(
        review, disputed_round, tmp_path, monkeypatch, recovered):
    from codex_review import codex, loops
    directory, job, citation, _ = disputed_round
    # given a resumed quota failure with a provider deadline
    log = tmp_path / 'quota.jsonl'
    log.write_text('{"type":"error","message":"try again at Jan 1st, 2030 10:08 AM"}\n')
    failed = rebut(review, disputed_round, {'CODEX_FAKE_STDOUT': log, 'CODEX_FAKE_RC': 1})
    assert failed.exit_code == 7, failed
    if recovered:
        current = loops.read(str(directory))
        (directory / 'transitions' / ('%08d.json' % current['generation'])).unlink()
        status = review('status', str(directory)).envelope['payload']
        decision = dict(kind='resolve-call', attempt=status['latest_attempt']['id'],
                        checkpoint_sha256=status['checkpoint_sha256'], rationale='Recover quota outcome')
        (directory / 'staging' / 'decision.json').write_text(json.dumps(decision))
        assert review('continue', str(directory), '--expected-generation', str(status['generation']),
                      '--decision-file', 'staging/decision.json').exit_code == 0
    status = review('status', str(directory)).envelope['payload']
    assert status['latest_attempt'].get('retry_after')
    # when explicit user direction resolves the dispute, its separate quota constraint must survive
    decision = dict(kind='dispute-resolution', round=job.name, attempt=status['latest_attempt']['id'],
                    checkpoint_sha256=status['checkpoint_sha256'], rationale='Continue independent investigation',
                    asserted_by='user', authorization='User requested continued investigation', evidence_ids=[citation])
    (directory / 'staging' / 'decision.json').write_text(json.dumps(decision))
    assert review('continue', str(directory), '--expected-generation', str(status['generation']),
                  '--decision-file', 'staging/decision.json').exit_code == 0
    current = loops.read(str(directory))
    # exercise launch admission independently of lifecycle, even if a recapture has cleared its pause
    monkeypatch.setattr(codex, 'resolve', lambda: None)
    result = loop_driver.run(str(directory), dict(round=job.name), dict(current, phase='prepared', pause=None))
    assert result['exit'] == 18
    assert result['error']['details']['reason'] == 'availability'


def record_rejections(review, directory, job, ids, disposition='rejected', scope_question=False, read=None, reopening=None):
    from qualification_fixture import qualify_author
    current = review('status', str(directory)).envelope['payload']
    if current['stage'] == 'assessment' and current['latest_attempt']['kind'] == 'rebuttal':
        assert assess_rebuttal(review, directory, job).exit_code == 0
    qualify_author(job, commands=['echo checked\n'])
    annotations = []
    for local_id in ids:
        citations = {}
        for field, slot, text in [('command', 'staging/probe-' + local_id + '.sh', 'echo checked\n'),
                                   ('output', 'probes/' + local_id + '.out', 'counterexample\n'),
                                   ('rc', 'probes/' + local_id + '.rc', '0\n')]:
            (job / slot).parent.mkdir(exist_ok=True)
            (job / slot).write_text(text)
            current = review('status', str(directory)).envelope['payload']
            sealed = review('seal', str(directory), '--round', job.name, '--expected-generation',
                            str(current['generation']), slot)
            assert sealed.exit_code == 0, sealed
            citations[field + '_evidence_id'] = sealed.envelope['payload']['evidence_id']
        annotation = dict(finding_id=local_id, probe_execution='executed',
                          read='unsupported' if disposition == 'rejected' else 'inconclusive',
                          rationale='Sealed evidence supports this assessment', **citations)
        if scope_question:
            annotation.update(read='confirmed-pre-existing', scope_question=True)
        elif disposition is not None:
            annotation['disposition'] = disposition
        if read is not None:
            annotation['read'] = read
        if reopening is not None:
            annotation['reopening'] = reopening
        annotations.append(annotation)
    current = review('status', str(directory)).envelope['payload']
    payload = dict(report_sha256=current.get('report_sha256') or images.sha256_file(str(job / 'report.json')), annotations=annotations)
    if current.get('adjudication'):
        payload['adjudication_sha256'] = current['adjudication']['sha256']
    if current.get('previous_probes_sha256'):
        payload['previous_probes_sha256'] = current['previous_probes_sha256']
    (job / 'staging' / 'probes.json').write_text(json.dumps(payload))
    return review('record-probes', str(directory), '--round', job.name, '--expected-generation',
                  str(current['generation']), '--probes-file', 'staging/probes.json')


@pytest.mark.parametrize('assessment', ['no-observed-breach', 'history-consumed'])
def should_probe_new_findings_after_rebuttal_without_rewriting_prior_probes(
        review, disputed_round, codex_reply, assessment):
    # given original probes already recorded before adjudication
    directory, job, _, log = disputed_round
    original = record_rejections(review, directory, job, ['F-1'])
    assert original.exit_code == 0, original
    before = (job / 'probes.json').read_bytes()
    new_finding = dict(RESPONSE['findings'][0], id='F-2', claim='A different concrete claim')
    codex_reply(dict(decisions=[dict(id='F-1', classification='introduced', outcome='withdrawn', rationale='refuted')],
                    new_findings=[new_finding]))
    # when adjudication adds a new ID, then all claims need an append-only author assessment
    result = rebut(review, disputed_round, {'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log})
    assert result.exit_code == 0, result
    assert result.envelope['payload']['unprobed_findings'] == ['F-1', 'F-2']
    assert assess_rebuttal(review, directory, job, assessment).exit_code == 0
    revised = record_rejections(review, directory, job, ['F-1', 'F-2'])
    assert revised.exit_code == 0, revised
    assert (job / 'probes.json').read_bytes() == before
    assert revised.envelope['payload']['probes_sha256'] != original.envelope['payload']['probes_sha256']
    assert revised.envelope['payload']['next_verb'] == 'record-apply'
    current = revised.envelope['payload']
    (job / 'staging' / 'apply.json').write_text(json.dumps(dict(report_sha256=current['report_sha256'],
        probes_sha256=current['probes_sha256'], results=[])))
    applied = review('record-apply', str(directory), '--round', job.name, '--expected-generation',
                     str(current['generation']), '--apply-file', 'staging/apply.json')
    assert applied.exit_code == 0, applied
    (job / 'staging' / 'verification.json').write_text(json.dumps(dict(apply_sha256=applied.envelope['payload']['apply_sha256'],
        verification=dict(result='not-run-explicitly'))))
    verified = review('record-verification', str(directory), '--round', job.name, '--expected-generation',
                      str(applied.envelope['payload']['generation']), '--verification-file', 'staging/verification.json')
    assert verified.exit_code == 0, verified
    finished = review('continue', str(directory), '--expected-generation', str(verified.envelope['payload']['generation']))
    assert finished.exit_code == 0, finished
    assert finished.envelope['payload']['clean_streak']['streak'] == 1  # only the cold call, not the rebuttal
    from codex_review import loops
    current = loops.read(str(directory))
    assert {b['attempt'] for b in current['counting']['read_bindings']} == {a['id'] for a in current['attempts']}
    # and later loss of the relied-on resumed log invalidates that credit too
    with open(current['attempts'][-1]['attempt_dir'] + '/stdout.jsonl', 'a') as handle:
        handle.write('{"type":"turn.completed"}\n')
    target = json.loads((job / 'target.json').read_text())
    updated, _ = loop_driver.check_launch_qualification(str(directory), current, target)
    assert updated['counting']['streak'] == 0


def should_refuse_resume_without_matching_qualification_before_launch(review, disputed_round, state_root, tmp_path):
    from codex_review import qualification
    # given qualified cold launch but no qualification for the resume configuration
    directory, job, _, _ = disputed_round
    index_path = state_root / 'qualifications' / 'index.json'
    index = json.loads(index_path.read_text())
    target = json.loads((job / 'target.json').read_text())
    del index[qualification.digest(loop_driver.launch_key(target, 'resume'))]
    index_path.write_text(json.dumps(index))
    marker = tmp_path / 'unqualified-resume'
    # when rebutting, then flags alone never authorize a launch
    result = rebut(review, disputed_round, {'CODEX_FAKE_ARGV': marker})
    assert result.exit_code == 18, result
    assert result.envelope['error']['details']['reason'] == 'qualification-required'
    assert not marker.exists()
    assert review('status', str(directory)).envelope['payload']['budget']['launched'] == 1


def should_hand_back_a_maintained_dispute_without_new_actionable_evidence(review, disputed_round, codex_reply):
    # given the single adjudication maintains a claim
    directory, job, _, log = disputed_round
    codex_reply(dict(decisions=[dict(id='F-1', classification='introduced', outcome='maintained',
                                    rationale='The cited counterexample does not exercise the failure')], new_findings=[]))
    assert rebut(review, disputed_round, {'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log}).exit_code == 0
    # when the author repeats rejection without a cited basis for reopening, then it cannot turn into clean credit
    rejected = record_rejections(review, directory, job, ['F-1'])
    assert rejected.exit_code == 18, rejected
    assert rejected.envelope['error']['details']['reason'] == 'stalled-dispute'
    assert not (job / 'probes.json').exists()
    assert review('status', str(directory)).envelope['payload']['next_verb'] is None


def should_retain_a_maintained_disagreement_across_authorized_recapture(review, disputed_round, codex_reply):
    from pathlib import Path
    from codex_review import apply
    # given a conclusive author rejection that the original reviewer subsequently maintains
    directory, job, citation, log = disputed_round
    assert record_rejections(review, directory, job, ['F-1']).exit_code == 0
    original = (job / 'probes.json').read_bytes()
    codex_reply(dict(decisions=[dict(id='F-1', classification='introduced', outcome='maintained',
                                    rationale='The counterexample misses the failing path')], new_findings=[]))
    assert rebut(review, disputed_round, {'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log}).exit_code == 0
    assert assess_rebuttal(review, directory, job).exit_code == 0
    target = json.loads((job / 'target.json').read_text())
    Path(target['plan_identity']['plan_path']).write_text('# Independently changed candidate\n')
    current = review('status', str(directory)).envelope['payload']
    decision = dict(kind='drift-resolution', round=job.name, checkpoint_sha256=current['checkpoint_sha256'],
                    target_sha256=images.sha256_file(str(job / 'target.json')), candidate_sha256=apply.identity_sha(target),
                    old_head=None, new_head=None, authority='User-authorized candidate update',
                    rationale='Keep the unresolved original disagreement while recapturing', evidence_ids=[citation], equivalence='reset')
    (directory / 'staging/decision.json').write_text(json.dumps(decision))
    # when leaving before revised probes, then the disagreement remains a durable convergence blocker
    moved = review('continue', str(directory), '--expected-generation', str(current['generation']),
                   '--decision-file', 'staging/decision.json')
    assert moved.exit_code == 0, moved
    recaptured = review('recapture', str(directory), '--expected-generation', str(moved.envelope['payload']['generation']))
    assert recaptured.exit_code == 0, recaptured
    pending = recaptured.envelope['payload']['pending_findings']
    assert len(pending) == 1
    assert pending[0]['reference'] == dict(round=job.name, local_id='F-1')
    assert pending[0]['status'] == 'open'
    assert (job / 'probes.json').read_bytes() == original


@pytest.mark.parametrize('new_evidence', [False, True])
def should_resolve_a_failed_rebuttal_only_with_new_evidence_or_user_direction(
        review, disputed_round, codex_reply, new_evidence):
    # given the permitted resume has failed, leaving the original finding actionable
    directory, job, original_evidence, log = disputed_round
    codex_reply('')
    result = rebut(review, disputed_round, {'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log})
    assert result.exit_code == 7
    current = review('status', str(directory)).envelope['payload']
    citation = original_evidence
    if new_evidence:
        (job / 'staging' / 'new-evidence.txt').write_text('New independently checked counterexample on the disputed path')
        citation = evidence.seal(str(job), job.name, 'staging/new-evidence.txt')['evidence_id']
    decision = dict(kind='dispute-resolution', checkpoint_sha256=current['checkpoint_sha256'],
                    round=job.name, attempt=current['latest_attempt']['id'], asserted_by='author',
                    evidence_ids=[citation], rationale='Continue independent investigation with the cited evidence')
    path = directory / 'staging' / 'decision.json'
    path.write_text(json.dumps(decision))
    # when resolving the pause, then repeating the same argument cannot reopen it
    resumed = review('continue', str(directory), '--expected-generation', str(current['generation']),
                     '--decision-file', 'staging/decision.json')
    if not new_evidence:
        assert resumed.exit_code == 8, resumed
        assert resumed.envelope['error']['code'] == 'new-evidence-required'
        decision.update(asserted_by='user', authorization='User directed a fresh independent probe of this disputed claim')
        path.write_text(json.dumps(decision))
        resumed = review('continue', str(directory), '--expected-generation', str(current['generation']),
                         '--decision-file', 'staging/decision.json')
    # then original findings still require probes, with no credit, withdrawal or second resume
    assert resumed.exit_code == 0, resumed
    assert resumed.envelope['payload']['next_verb'] == 'record-probes'
    assert resumed.envelope['payload']['budget']['launched'] == 2
    assert resumed.envelope['payload']['clean_streak']['streak'] == 0
    assert record_rejections(review, directory, job, ['F-1']).exit_code == 0
    assert rebut(review, disputed_round, {}).envelope['error']['details']['reason'] == 'rebuttal-already-launched'


@pytest.mark.parametrize('lost_receipt', [False, True, 'lost-answer'])
def should_recover_rebuttal_publication_without_repeating_the_call(review, disputed_round, codex_reply, tmp_path, lost_receipt):
    from codex_review import loops
    # given a completed adjudication interrupted before its loop transition
    directory, job, _, log = disputed_round
    codex_reply(dict(decisions=[dict(id='F-1', classification='introduced', outcome='withdrawn', rationale='refuted')], new_findings=[dict(RESPONSE['findings'][0], id='F-2')] if lost_receipt == 'lost-answer' else []))
    result = rebut(review, disputed_round, {'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log})
    assert result.exit_code == 0, result
    generation = result.envelope['payload']['generation']
    (directory / 'transitions' / ('%08d.json' % generation)).unlink()
    pending = review('status', str(directory)).envelope['payload']
    if lost_receipt:
        (job / 'reviewer-attempts' / pending['latest_attempt']['id'] / 'completion.json').unlink()
    if lost_receipt == 'lost-answer':
        attempt_dir = job / 'reviewer-attempts' / pending['latest_attempt']['id']
        for name in ('last-message.txt', 'stdout.jsonl', 'stderr.txt'):
            (attempt_dir / name).unlink()
    decision = dict(kind='resolve-call', attempt=pending['latest_attempt']['id'],
                    checkpoint_sha256=pending['checkpoint_sha256'], rationale='Reconcile retained adjudication bytes')
    (directory / 'staging' / 'decision.json').write_text(json.dumps(decision))
    marker = tmp_path / 'unexpected-second-resume'
    # when resolving the call, then the same reply is adopted once without a fresh launch or lost charge
    resolved = review('continue', str(directory), '--expected-generation', str(pending['generation']),
                      '--decision-file', 'staging/decision.json', fake={'CODEX_FAKE_ARGV': marker})
    assert resolved.exit_code == 0, resolved
    assert resolved.envelope['payload']['budget']['judgments'] == 2
    assert resolved.envelope['payload']['adjudication']['result']['decisions'][0]['outcome'] == 'withdrawn'
    assert resolved.envelope['payload']['stage'] == 'assessment'
    assert resolved.envelope['payload']['next_verb'] == 'continue'
    assert not marker.exists()
    assert len(loops.read(str(directory))['attempts']) == 2
    if not lost_receipt:
        assert resolved.envelope['payload']['latest_attempt']['timing']['complete'] is True
    if lost_receipt == 'lost-answer':
        assert resolved.envelope['payload']['adjudication']['result']['new_findings'][0]['id'] == 'F-2'
        assert resolved.envelope['payload']['latest_attempt']['timing']['complete'] is False


def should_retire_an_irrecoverable_rebuttal_without_voiding_its_original_findings(review, disputed_round):
    import socket
    import subprocess
    import sys
    from codex_review import loops
    # given a reserved rebuttal whose child is demonstrably gone but has no completion receipt
    directory, job, sealed, _ = disputed_round
    original = (job / 'report.json').read_bytes()
    child = subprocess.Popen([sys.executable, '-c', 'pass'])
    child.wait()
    current = loops.read(str(directory))
    attempt = dict(id='a' * 16, round=job.name, kind='rebuttal', outcome='pending', charged=True,
                   origin_attempt=current['attempts'][0]['id'], thread_id=THREAD, return_phase='reviewed',
                   claims=[dict(id='F-1', evidence=[dict(sha256=images.sha256_file(str(job / 'evidence' / sealed / 'content')))])])
    attempt_dir = job / 'reviewer-attempts' / attempt['id']
    attempt_dir.mkdir()
    (attempt_dir / 'launch.json').write_text(json.dumps(dict(host=socket.gethostname(), pid=child.pid, process_start='retired-child')))
    loops.append(str(directory), current['generation'], dict(current, phase='calling', attempts=current['attempts'] + [attempt]),
                 dict(kind='offline-interrupted-rebuttal-fixture'))
    current = review('status', str(directory)).envelope['payload']
    (directory / 'staging' / 'decision.json').write_text(json.dumps(dict(kind='retire-attempt', attempt=attempt['id'],
        asserted_by='user', authorization='User authorized retiring this irrecoverable attempt', rationale='Child is quiescent',
        checkpoint_sha256=current['checkpoint_sha256'])))
    # when explicitly retiring it, then preserve the original round and require dispute resolution
    result = review('continue', str(directory), '--expected-generation', str(current['generation']),
                    '--decision-file', 'staging/decision.json')
    assert result.exit_code == 0, result
    assert result.envelope['payload']['pause']['kind'] == 'stalled-dispute'
    assert result.envelope['payload']['latest_attempt']['outcome'] == 'retired-unknown'
    assert result.envelope['payload']['latest_attempt']['thread_id'] == THREAD
    assert result.envelope['payload']['budget']['launched'] == 2
    assert not (job / 'abandoned.json').exists()
    assert (job / 'report.json').read_bytes() == original


@pytest.mark.parametrize('disputed_round', ['max'], indirect=True)
def should_withhold_confirmation_when_its_resume_qualification_fails(review, disputed_round, codex_reply):
    from codex_review import counting, loops, qualification
    # given a real driver rebuttal followed by independent refutation and round closure records
    directory, job, _, log = disputed_round
    codex_reply(dict(decisions=[dict(id='F-1', classification='introduced', outcome='withdrawn', rationale='refuted')],
                    new_findings=[]))
    assert rebut(review, disputed_round, {'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log}).exit_code == 0
    assert record_rejections(review, directory, job, ['F-1']).exit_code == 0
    def status():
        return review('status', str(directory)).envelope['payload']
    for name in ('apply', 'verification'):
        current = status()
        payload = (dict(report_sha256=current['report_sha256'], probes_sha256=current['probes_sha256'], results=[])
                   if name == 'apply' else dict(apply_sha256=images.sha256_file(str(job / 'apply.json')),
                                               verification=dict(result='not-run-explicitly')))
        (job / ('staging/' + name + '.json')).write_text(json.dumps(payload))
        assert review('record-' + name, str(directory), '--round', job.name, '--expected-generation',
                      str(current['generation']), '--' + name + '-file', 'staging/' + name + '.json').exit_code == 0
    # The earlier-credit fixture isolates the final confirmation gate; this round ran at max.
    current = loops.read(str(directory))
    credited = counting.initial()
    for _ in range(2):
        credited = counting.round_finished(credited, effort='xhigh', eligible=True, accepted=0, blockers=False)
    loops.append(str(directory), current['generation'], dict(current, counting=credited), dict(kind='offline-prior-credit-fixture'))
    proof = next(a['qualification'] for a in current['attempts'] if a['kind'] == 'rebuttal')
    record = json.loads((directory.parent / 'qualifications' / (proof['digest'] + '.json')).read_text())
    evidence_job = directory.parent / record.pop('evidence_job')
    record.update(supersedes=proof['digest'])
    record['controls'][0]['outcome'] = 'fail'
    qualification.record(str(directory.parent), str(evidence_job), record)
    # when the resume capability fails before counting, then the loop cannot claim convergence
    result = review('continue', str(directory), '--expected-generation', str(status()['generation']))
    assert result.exit_code == 18, result.envelope
    assert not status()['clean_streak']['converged']
    assert status()['clean_streak']['streak'] == 0
    # passing replacement controls release completion without retroactively restoring this round's credit
    qualify_fixture(directory.parent, job, proof['key'])
    result = review('continue', str(directory), '--expected-generation', str(status()['generation']))
    assert result.exit_code == 0, result.envelope
    assert status()['stage'] == 'recapture'
    assert status()['clean_streak']['streak'] == 0


@pytest.mark.parametrize('disputed_round', ['pre-existing'], indirect=True)
@pytest.mark.parametrize('reopening_kind', ['absent', 'repeated', 'new'])
def should_require_new_evidence_to_refute_a_maintained_pre_existing_scope_question(
        review, disputed_round, codex_reply, reopening_kind):
    # given an acknowledged scope question maintained by the resumed reviewer
    directory, job, original_evidence, log = disputed_round
    assert record_rejections(review, directory, job, ['F-1'], disposition=None, scope_question=True).exit_code == 0
    original = (job / 'probes.json').read_bytes()
    codex_reply(dict(decisions=[dict(id='F-1', classification='pre-existing', outcome='maintained',
                                    rationale='The original evidence does not refute the scope concern')], new_findings=[]))
    assert rebut(review, disputed_round, {'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log}).exit_code == 0
    reopening = None
    if reopening_kind != 'absent':
        citation = original_evidence
        if reopening_kind == 'new':
            (job / 'staging/new-control.txt').write_text('Independent counterexample on the disputed path')
            citation = evidence.seal(str(job), job.name, 'staging/new-control.txt')['evidence_id']
        reopening = dict(rationale='This cited control justifies reopening', evidence_ids=[citation])
    # when refuting the maintained claim, then only new cited evidence can remove its blocker
    result = record_rejections(review, directory, job, ['F-1'], disposition=None, read='unsupported', reopening=reopening)
    assert (job / 'probes.json').read_bytes() == original
    current = review('status', str(directory)).envelope['payload']
    if reopening_kind == 'new':
        assert result.exit_code == 0, result
        assert not current['pending_findings']
    else:
        assert result.exit_code == 18, result
        assert result.envelope['error']['details']['reason'] == 'stalled-dispute'
        assert current['pending_findings']


def should_refuse_caller_execution_on_a_denied_probe_without_corrupting_the_loop(review, disputed_round):
    # given a sealed command declined before execution and no output evidence
    directory, job, _, _ = disputed_round
    (job / 'staging/probe-F-1.sh').write_text('echo declined\n')
    command = evidence.seal(str(job), job.name, 'staging/probe-F-1.sh')['evidence_id']
    annotation = dict(finding_id='F-1', disposition='inconclusive', read='inconclusive', probe_execution='denied',
                      command_evidence_id=command, denial_kind='interactive', asserted_by='user', rationale='User declined this fixture command')
    def submit(value):
        current = review('status', str(directory)).envelope['payload']
        (job / 'staging/probes.json').write_text(json.dumps(dict(report_sha256=current['report_sha256'], annotations=[value])))
        return review('record-probes', str(directory), '--round', job.name, '--expected-generation', str(current['generation']),
                      '--probes-file', 'staging/probes.json')
    # when untrusted execution metadata is supplied, then refusal precedes publication and correction remains possible
    result = submit(dict(annotation, execution=True))
    assert not (job / 'probes.json').exists(), result
    assert result.envelope['error']['code'] == 'caller-execution-metadata'
    assert review('status', str(directory)).envelope['payload']['stage'] == 'reviewed'
    assert submit(annotation).exit_code == 14
    assert 'execution' not in json.loads((job / 'probes.json').read_text())['annotations'][0]
@pytest.mark.parametrize('basis,allowed', [('same-bytes', False), ('new-bytes', True),
    ('user', True), ('missing-authorization', False)])
def should_require_a_new_basis_to_clear_a_retained_maintained_dispute(tmp_path, basis, allowed):
    from codex_review import adjudication, response
    # given a retained original adjudication and evidence now sealed in a later round
    origin = tmp_path / ('a' * 16)
    later = tmp_path / ('b' * 16)
    origin.mkdir()
    (later / 'staging').mkdir(parents=True)
    result = dict(decisions=[dict(id='F-1', outcome='maintained')])
    record = origin / 'adjudication.json'
    record.write_text(json.dumps(result))
    (later / 'staging/basis.txt').write_text('new counterexample' if basis == 'new-bytes' else 'old counterexample')
    citation = evidence.seal(str(later), later.name, 'staging/basis.txt')['evidence_id']
    current = dict(rebuttals={origin.name: dict(attempt='attempt', path=str(record), result=result,
                    sha256=images.sha256_file(str(record)))}, attempts=[dict(id='attempt', claims=[dict(
                    id='F-1', evidence=[dict(sha256=images.sha256_bytes(b'old counterexample'))])])])
    reopening = dict(rationale='Recheck the maintained failure scenario', evidence_ids=[citation])
    if basis in ('user', 'missing-authorization'):
        reopening['asserted_by'] = 'user'
        if basis == 'user':
            reopening['authorization'] = 'User explicitly authorized reopening this dispute'
    # when clearing the retained disagreement, then resealing the old argument does not reopen it
    if allowed:
        adjudication.require_retained_reopening(current, dict(round=origin.name, local_id='F-1'), str(later), reopening)
    else:
        with pytest.raises(response.ContractViolation):
            adjudication.require_retained_reopening(current, dict(round=origin.name, local_id='F-1'), str(later), reopening)


@pytest.mark.parametrize('when', ['before-credit', 'retained-credit'])
@pytest.mark.parametrize('slot', ['command', 'output', 'rc'])
def should_withhold_credit_after_losing_refutation_evidence(review, disputed_round, when, slot):
    from codex_review import loops
    # given a completed, independently refuted round with immutable author records
    directory, job, _, _ = disputed_round
    assert record_rejections(review, directory, job, ['F-1']).exit_code == 0
    def status():
        return review('status', str(directory)).envelope['payload']
    def invoke(verb, *args):
        current = status()
        member = [] if verb == 'continue' else ['--round', job.name]
        return review(verb, str(directory), *member, '--expected-generation', str(current['generation']), *args)
    for name in ('apply', 'verification'):
        current = status()
        payload = (dict(report_sha256=current['report_sha256'], probes_sha256=current['probes_sha256'], results=[])
                   if name == 'apply' else dict(apply_sha256=images.sha256_file(str(job / 'apply.json')),
                                               verification=dict(result='not-run-explicitly')))
        (job / ('staging/' + name + '.json')).write_text(json.dumps(payload))
        assert invoke('record-' + name, '--' + name + '-file', 'staging/' + name + '.json').exit_code == 0
    if when == 'retained-credit':
        assert invoke('continue').exit_code == 0
        assert status()['clean_streak']['streak'] == 1
    annotation = json.loads((job / 'probes.json').read_text())['annotations'][0]
    old_records = {p.name: p.read_bytes() for p in job.glob('*.json')}
    # when a relied-on seal is lost before counting or before the next launch
    (job / 'evidence' / annotation[slot + '_evidence_id'] / 'content').unlink()
    if when == 'before-credit':
        invoke('continue')
    else:
        current = loops.read(str(directory))
        loop_driver.check_launch_qualification(str(directory), current, json.loads((job / 'target.json').read_text()))
    # then credit is withheld and the unsupported refutation becomes pending again
    assert status()['clean_streak']['streak'] == 0
    assert not status()['clean_streak']['converged']
    assert status()['pending_findings'][0]['reference'] == dict(round=job.name, local_id='F-1')
    assert {p.name: p.read_bytes() for p in job.glob('*.json')} == old_records
