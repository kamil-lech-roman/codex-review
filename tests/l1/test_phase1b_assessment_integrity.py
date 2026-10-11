"""Read assessments remain bound to the retained judgment and exact log bytes."""
import json
from pathlib import Path

import pytest

from codex_review import images, loop_driver
from test_phase1b_rebuttal import disputed_round


def should_append_rebuttal_reassessment_against_its_own_predecessor(review, disputed_round, codex_reply):
    from codex_review import loops
    from test_phase1b_rebuttal import rebut, assess_rebuttal
    # given a resumed judgment whose reads need investigation
    directory, job, _, log = disputed_round
    cold = loops.read(str(directory))['assessments'][job.name]
    codex_reply(dict(decisions=[dict(id='F-1', classification='introduced', outcome='withdrawn',
                                    rationale='Counterexample holds')], new_findings=[]))
    assert rebut(review, disputed_round, {'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log}).exit_code == 0
    paused = assess_rebuttal(review, directory, job, 'read-unresolved')
    assert paused.exit_code == 0, paused.envelope
    assert paused.envelope['payload']['next_verb'] is None
    prior = {p.name: p.read_bytes() for p in (directory / 'transitions').glob('*.json')}
    # when a replacement omits the resumed assessment's predecessor, then its fresh generation is insufficient
    decision_path = directory / 'staging/decision.json'
    decision = json.loads(decision_path.read_text())
    status = review('status', str(directory)).envelope['payload']
    decision.update(disposition='no-observed-breach', checkpoint_sha256=status['checkpoint_sha256'])
    decision_path.write_text(json.dumps(decision))
    refused = review('continue', str(directory), '--expected-generation', str(status['generation']),
                     '--decision-file', 'staging/decision.json')
    assert refused.exit_code == 8, refused.envelope
    assert refused.envelope['error']['code'] == 'stale-assessment'
    # then a cited append releases finding work without replacing either earlier assessment
    released = assess_rebuttal(review, directory, job)
    assert released.exit_code == 0, released.envelope
    assert released.envelope['payload']['next_verb'] == 'record-probes'
    assert loops.read(str(directory))['assessments'][job.name] == cold
    assert all((directory / 'transitions' / name).read_bytes() == data for name, data in prior.items())


def should_gate_an_incomplete_resumed_judgment_before_finding_work(
        review, disputed_round, codex_reply, tmp_path):
    from codex_review import loops
    from test_phase1b_rebuttal import rebut, THREAD
    # given an assessed cold judgment followed by a valid adjudication with an incomplete log
    directory, job, _, _ = disputed_round
    original = loops.read(str(directory))['assessments'][job.name]
    log = tmp_path / 'resume-partial.jsonl'
    log.write_text(json.dumps(dict(type='thread.started', thread_id=THREAD)) + '\n')
    codex_reply(dict(decisions=[dict(id='F-1', classification='introduced', outcome='withdrawn',
                                    rationale='Counterexample holds')], new_findings=[]))
    result = rebut(review, disputed_round, {'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log})
    assert result.exit_code == 0, result.envelope
    # when finding work would consume that judgment, then its own read assessment comes first
    status = result.envelope['payload']
    assert status['stage'] == 'assessment'
    assert status['next_verb'] == 'continue'
    (job / 'staging/probes.json').write_text('{}')
    blocked = review('record-probes', str(directory), '--round', job.name,
                     '--expected-generation', str(status['generation']), '--probes-file', 'staging/probes.json')
    assert blocked.exit_code == 18, blocked.envelope
    assert blocked.envelope['error']['details']['reason'] == 'read-assessment-required'
    decision = dict(kind='read-assessment', round=job.name, attempt=status['latest_attempt']['id'],
                    disposition='no-observed-breach', rationale='Inspect the resumed log independently',
                    adjudication_sha256=status['adjudication']['sha256'], log_sha256=images.sha256_file(str(log)),
                    previous_assessment_sha256=None, event_lines=[], checkpoint_sha256=status['checkpoint_sha256'])
    (directory / 'staging/decision.json').write_text(json.dumps(decision))
    refused = review('continue', str(directory), '--expected-generation', str(status['generation']),
                     '--decision-file', 'staging/decision.json')
    assert refused.exit_code == 8, refused.envelope
    assert refused.envelope['error']['code'] == 'incomplete-read-assessment'
    decision['disposition'] = 'history-consumed'
    (directory / 'staging/decision.json').write_text(json.dumps(decision))
    refused = review('continue', str(directory), '--expected-generation', str(status['generation']),
                     '--decision-file', 'staging/decision.json')
    assert refused.exit_code == 8, refused.envelope
    assert refused.envelope['error']['code'] == 'incomplete-read-assessment'
    decision['disposition'] = 'read-unverified'
    (directory / 'staging/decision.json').write_text(json.dumps(decision))
    released = review('continue', str(directory), '--expected-generation', str(status['generation']),
                      '--decision-file', 'staging/decision.json')
    assert released.exit_code == 0, released.envelope
    assert released.envelope['payload']['next_verb'] == 'record-probes'
    retained = loops.read(str(directory))
    assert retained['assessments'][job.name] == original
    assert job.name in retained['disqualified_rounds']


@pytest.mark.parametrize('change', ['missing', 'truncated', 'appended', 'report'])
@pytest.mark.parametrize('kind', ['cold', 'rebuttal'])
def should_detect_lost_assessment_evidence(tmp_path, change, kind):
    # given a completed judgment with a valid no-read assessment
    round_id = 'a' * 16
    directory = tmp_path / ('b' * 16)
    attempt_dir = tmp_path / round_id / 'reviewer-attempts' / ('c' * 16)
    attempt_dir.mkdir(parents=True)
    log = attempt_dir / 'stdout.jsonl'
    log.write_text('{"type":"turn.completed"}\n')
    report = tmp_path / round_id / 'report.json' if kind == 'cold' else attempt_dir / 'adjudication.json'
    report.write_text('{}\n')
    attempt = dict(id='c' * 16, round=round_id, kind=kind, outcome='judgment', attempt_dir=str(attempt_dir))
    assessment = dict(disposition='no-observed-breach',
                      log_sha256=images.sha256_file(str(log)), event_lines=[])
    assessment['report_sha256' if kind == 'cold' else 'adjudication_sha256'] = images.sha256_file(str(report))
    current = dict(current_round=round_id, attempts=[attempt], assessments={round_id: assessment} if kind == 'cold' else {},
                   rebuttal_assessments={attempt['id']: assessment} if kind == 'rebuttal' else {})
    assert loop_driver.assessment_issue(str(directory), current) is None
    # when its retained evidence changes, then the favorable disposition no longer opens the gate
    if change == 'missing':
        log.unlink()
    elif change == 'report':
        report.write_text('{"different": true}\n')
    elif change == 'truncated':
        log.write_text('')
    else:
        with log.open('a') as handle:
            handle.write(json.dumps(dict(type='item.completed', item=dict(type='command_execution', command='cat prior'))) + '\n')
    assert loop_driver.assessment_issue(str(directory), current)


@pytest.mark.parametrize('restored', [False, True])
def should_release_independent_finding_work_when_a_judgment_log_is_lost(review, disputed_round, restored):
    from codex_review import qualification, loops
    from test_phase1b_rebuttal import record_rejections
    # given an assessed judgment whose log can no longer be inspected
    directory, job, _, _ = disputed_round
    def status():
        return review('status', str(directory)).envelope['payload']
    current = status()
    log = Path(current['latest_attempt']['attempt_dir']) / 'stdout.jsonl'
    original_log = log.read_bytes()
    log.unlink()
    decision = dict(kind='read-assessment', round=job.name, disposition='read-unverified',
                    rationale='The retained log was lost; independently check findings without clean credit.',
                    report_sha256=current['report_sha256'], log_sha256=None,
                    previous_assessment_sha256=qualification.digest(current['latest_read_assessment']),
                    checkpoint_sha256=current['checkpoint_sha256'])
    (directory / 'staging/decision.json').write_text(json.dumps(decision))
    # when acknowledging the evidence gap, then finding work resumes but credit remains disqualified
    result = review('continue', str(directory), '--expected-generation', str(current['generation']),
                    '--decision-file', 'staging/decision.json')
    assert result.exit_code == 0, result.envelope
    assert status()['next_verb'] == 'record-probes'
    assert job.name in loops.read(str(directory))['disqualified_rounds']
    if restored:
        log.write_bytes(original_log)
        current = status()
        assert current['pause']['kind'] == 'read-unresolved'
        replacement = dict(decision, disposition='no-observed-breach', event_lines=[],
                           log_sha256=images.sha256_file(str(log)), checkpoint_sha256=current['checkpoint_sha256'],
                           previous_assessment_sha256=qualification.digest(current['latest_read_assessment']))
        (directory / 'staging/decision.json').write_text(json.dumps(replacement))
        assert review('continue', str(directory), '--expected-generation', str(current['generation']),
                      '--decision-file', 'staging/decision.json').exit_code == 0
    assert record_rejections(review, directory, job, ['F-1']).exit_code == 0
    for name in ('apply', 'verification'):
        current = status()
        payload = (dict(report_sha256=current['report_sha256'], probes_sha256=current['probes_sha256'], results=[])
                   if name == 'apply' else dict(apply_sha256=images.sha256_file(str(job / 'apply.json')),
                                               verification=dict(result='not-run-explicitly')))
        (job / ('staging/' + name + '.json')).write_text(json.dumps(payload))
        assert review('record-' + name, str(directory), '--round', job.name,
                      '--expected-generation', str(current['generation']), '--' + name + '-file', 'staging/' + name + '.json').exit_code == 0
    result = review('continue', str(directory), '--expected-generation', str(status()['generation']))
    assert result.exit_code == 0, result.envelope
    assert status()['clean_streak']['streak'] == 0
    assert not status()['clean_streak']['converged']
