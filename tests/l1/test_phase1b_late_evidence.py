"""Late reviewer bytes remain attributable after an outcome was explicitly retired."""
import json
import socket
import pytest

from codex_review import images, loops
from test_record_probes import RESPONSE


def independent_annotation(review, directory, fresh, alias, plan, accept=False, command=None):
    from qualification_fixture import qualify_command
    qualify_command(fresh, command or 'echo counterexample\n')
    annotation = dict(finding_id=alias, disposition='rejected', read='unsupported',
                      probe_execution='executed', rationale='Independent counterexample for the retained original claim')
    if accept:
        annotation.update(disposition='accepted', read='confirmed-introduced', fix_paths=[plan.name],
                          rationale='The original claimed behavior is reproducible on identical inputs',
                          false_result='The probe would observe the required behavior', path_exercised='The original plan requirement')
    for kind, slot, data in [('command', 'staging/probe-' + alias + '.sh', command or 'echo counterexample\n'),
                             ('output', 'probes/' + alias + '.out', 'counterexample\n'),
                             ('rc', 'probes/' + alias + '.rc', '0\n')]:
        (fresh / slot).parent.mkdir(exist_ok=True)
        if command and kind != 'command':
            data = (executed.stdout + executed.stderr).decode() if kind == 'output' else str(executed.returncode) + '\n'
        (fresh / slot).write_text(data)
        status = review('status', str(directory)).envelope['payload']
        sealed = review('seal', str(directory), '--round', fresh.name, '--expected-generation', str(status['generation']), slot)
        assert sealed.exit_code == 0, sealed
        annotation[kind + '_evidence_id'] = sealed.envelope['payload']['evidence_id']
        if command and kind == 'command':
            import subprocess
            execution = sealed.envelope['payload']['execution']
            assert execution['ready']
            executed = subprocess.run(execution['argv'], cwd=execution['cwd'], capture_output=True)
            assert executed.returncode == 0
    return annotation


@pytest.mark.parametrize('recurs,accept', [(False, False), (True, False), (False, True), (False, 'reassess')])
def should_surface_and_reconcile_late_judgment_without_reopening_its_job(
        review, plan_file_factory, state_root, codex_reply, tmp_path, recurs, accept):
    # given a retired cold attempt in a terminal original job, with no usable answer at retirement
    plan = plan_file_factory()
    verification = ['--verify', 'true'] if accept else ['--no-verify', '--reason', 'late-output control']
    prepared = review('prepare', 'plan', str(plan), '--no-repo', '--loop', *verification)
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    attempt_dir = job / 'reviewer-attempts' / ('a' * 16)
    attempt_dir.mkdir(parents=True)
    (attempt_dir / 'launch.json').write_text(json.dumps(dict(host=socket.gethostname(), pid=2147483647, process_start='gone')))
    (attempt_dir / 'stdout.jsonl').write_text('')
    (attempt_dir / 'stderr.txt').write_text('')
    current = loops.read(str(directory))
    attempt = dict(id=attempt_dir.name, round=job.name, kind='cold', outcome='retired-unknown',
                   charged=True, retired_evidence={})
    loops.append(str(directory), 0, dict(current, phase='recapture', attempts=[attempt]), dict(kind='offline-retirement-fixture'))
    (job / 'abandoned.json').write_text(json.dumps(dict(reason='User retired the unknown outcome')))
    terminal = (job / 'abandoned.json').read_bytes()
    # when output arrives later, status exposes its pending reconciliation without refund or credit
    raw = attempt_dir / 'last-message.txt'
    raw.write_text(json.dumps(RESPONSE))
    (attempt_dir / 'stdout.jsonl').write_text('{"type":"turn.completed"}\n')
    current = review('status', str(directory)).envelope['payload']
    assert len(current['late_evidence']) == 1
    pending = current['late_evidence'][0]
    assert pending['attempt'] == attempt['id']
    assert pending['files']['last-message.txt'] == images.sha256_file(str(raw))
    assert current['next_verb'] is None
    assert current['pause']['kind'] == 'late-evidence'
    assert current['budget']['launched'] == 1
    decision = dict(kind='resolve-call', attempt=attempt['id'], checkpoint_sha256=current['checkpoint_sha256'],
                    attempt_evidence_sha256=pending['sha256'], rationale='Validate the late answer against its original capture')
    (directory / 'staging').mkdir()
    path = directory / 'staging' / 'decision.json'
    path.write_text(json.dumps(decision))
    result = review('continue', str(directory), '--expected-generation', str(current['generation']),
                    '--decision-file', 'staging/decision.json')
    assert result.exit_code == 0, result
    after = result.envelope['payload']
    assert after['budget']['launched'] == 1
    assert after['budget']['judgments'] == 1
    assert after['clean_streak']['streak'] == 0
    assert after['pending_findings'][0]['reference'] == dict(round=job.name, local_id='F-1')
    assert after['pending_findings'][0]['disposition'] == 'unassessed'
    assert not after['late_evidence']
    assert (job / 'abandoned.json').read_bytes() == terminal
    assert not (job / 'report.json').exists()
    # independently probe the equivalent current capture before resolving the old finding
    from test_phase1b_loop_interface import qualify_cold
    from test_phase1b_rebuttal import record_rejections
    import uuid
    captured = review('recapture', str(directory), '--expected-generation', str(after['generation']))
    assert captured.exit_code == 0, captured
    current = captured.envelope['payload']
    fresh = state_root / current['round']
    qualify_cold(state_root, fresh)
    log = tmp_path / 'fresh.jsonl'
    log.write_text(json.dumps(dict(type='thread.started', thread_id=str(uuid.uuid4()))) + '\n{"type":"turn.completed"}\n')
    codex_reply(RESPONSE if recurs else dict(RESPONSE, findings=[], verdict='approve'))
    ran = review('run', str(directory), '--round', fresh.name, '--expected-generation', str(current['generation']),
                 fake={'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log})
    assert ran.exit_code == 0, ran
    def decide(payload):
        current = review('status', str(directory)).envelope['payload']
        path.write_text(json.dumps(dict(payload, checkpoint_sha256=current['checkpoint_sha256'])))
        return review('continue', str(directory), '--expected-generation', str(current['generation']),
                      '--decision-file', 'staging/decision.json')
    assert decide(dict(kind='read-assessment', round=fresh.name, disposition='no-observed-breach',
                       report_sha256=ran.envelope['payload']['report_sha256'], log_sha256=images.sha256_file(str(log)),
                       previous_assessment_sha256=None, event_lines=[], rationale='Complete offline no-read fixture')).exit_code == 0
    probed = record_rejections(review, directory, fresh, ['F-1'] if recurs else [])
    assert probed.exit_code == 0, probed
    current_report = (fresh / 'report.json').read_bytes()
    if recurs:
        annotation = json.loads((fresh / 'probes.json').read_text())['annotations'][0]
        assessment = dict(related_reference=dict(round=fresh.name, local_id='F-1'),
                          probes_sha256=probed.envelope['payload']['probes_sha256'])
    else:
        alias = 'retained-' + job.name + '-F-1'
        annotation = independent_annotation(review, directory, fresh, alias, plan, accept)
        assessment = dict(assessment=annotation)
    resolution = dict(kind='finding-resolution', disposition='accepted' if accept else 'refuted',
                           reference=dict(round=job.name, local_id='F-1'), **assessment,
                           origin_report_sha256=images.sha256_file(str(raw)),
                           evidence_ids=[annotation['output_evidence_id']], rationale='The equivalent current probe refutes this late claim')
    if not recurs and not accept:
        for invalid, expected in [
            (dict(resolution, origin_report_sha256='0' * 64), 'stale-relation-evidence'),
            (dict(resolution, assessment=dict(annotation, read='inconclusive')), 'refutation-not-probed'),
            (dict(resolution, assessment=dict(annotation, command_evidence_id=annotation['output_evidence_id'])), 'evidence-wrong-slot'),
        ]:
            refused = decide(invalid)
            assert refused.exit_code == 8, refused
            assert refused.envelope['error']['code'] == expected
            assert review('status', str(directory)).envelope['payload']['pending_findings']
    resolved = decide(resolution)
    assert resolved.exit_code == 0, resolved
    if accept:
        import subprocess
        def invoke(verb, *args):
            status = review('status', str(directory)).envelope['payload']
            return review(verb, str(directory), '--round', fresh.name, '--expected-generation', str(status['generation']), *args)
        status = resolved.envelope['payload']
        assert status['next_verb'] == 'record-probes'
        recorded = invoke('record-probes', '--probes-file', status['pending_probe_submission'])
        assert recorded.exit_code == 0, recorded
        item = recorded.envelope['payload']['pending_findings'][0]
        assert item['reference'] == dict(round=job.name, local_id='F-1')
        assert item['disposition'] == 'accepted'
        assert item['late_attempt'] == attempt['id']
        assert item['late_report_sha256'] == images.sha256_file(str(raw))
        if accept == 'reassess':
            deny_then_refute_retained(review, directory, fresh, job, plan, raw, alias, codex_reply, tmp_path)
            assert (job / 'abandoned.json').read_bytes() == terminal
            assert not (job / 'report.json').exists()
            return
        # the same ownership/baseline machinery must protect this independently accepted work
        plan.write_text('# fixed requirement\n')
        status = recorded.envelope['payload']
        payload = dict(report_sha256=status['report_sha256'], probes_sha256=status['probes_sha256'],
                       results=[dict(finding_id=alias, result='applied', sibling_sweep=dict(
                           searched_scope=[plan.name], related_sites=[], rationale='One plan requirement'))])
        (fresh / 'staging' / 'apply.json').write_text(json.dumps(payload))
        applied = invoke('record-apply', '--apply-file', 'staging/apply.json')
        assert applied.exit_code == 0, applied
        assert json.loads((fresh / 'apply.json').read_text())['changed_paths'] == [str(plan)]
        (fresh / 'staging' / 'verify.sh').write_text('true\n')
        sealed = invoke('seal', 'staging/verify.sh').envelope['payload']
        launch = sealed['execution']
        assert launch['ready']
        result = subprocess.run(launch['argv'], cwd=launch['cwd'], capture_output=True)
        (fresh / 'verify.out').write_bytes(result.stdout + result.stderr)
        (fresh / 'verify.rc').write_text(str(result.returncode) + '\n')
        check = dict(result='passed', command_evidence_id=sealed['evidence_id'],
                     output_evidence_id=invoke('seal', 'verify.out').envelope['payload']['evidence_id'],
                     rc_evidence_id=invoke('seal', 'verify.rc').envelope['payload']['evidence_id'])
        (fresh / 'staging' / 'verification.json').write_text(json.dumps(dict(
            apply_sha256=images.sha256_file(str(fresh / 'apply.json')), verification=check)))
        verified = invoke('record-verification', '--verification-file', 'staging/verification.json')
        assert verified.exit_code == 0, verified
        assert json.loads((fresh / 'verification.json').read_text())['verification_result'] == 'passed'
    else:
        assert not resolved.envelope['payload']['pending_findings']
    assert (fresh / 'report.json').read_bytes() == current_report
    assert (job / 'abandoned.json').read_bytes() == terminal
    assert not (job / 'report.json').exists()
    # then a later mutation cannot silently inherit the acknowledgment of different bytes
    raw.write_text(json.dumps(dict(RESPONSE, verdict='approve')))
    assert review('status', str(directory)).envelope['payload']['late_evidence']


def should_keep_live_or_stale_late_output_unresolved(review, plan_file_factory, state_root):
    import os
    import pytest
    from codex_review import late_evidence, qualification, response, transport
    # given a retired record but a matching child still alive (a contradictory quiescence assessment)
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop', '--no-verify', '--reason', 'live control')
    directory = str(state_root / prepared.envelope['job_id'])
    job = state_root / prepared.envelope['payload']['round']
    attempt = dict(id='b' * 16, round=job.name, kind='cold', outcome='retired-unknown', retired_evidence={})
    current = dict(loops.read(directory), attempts=[attempt])
    path = job / 'reviewer-attempts' / attempt['id']
    path.mkdir(parents=True)
    (path / 'launch.json').write_text(json.dumps(dict(host=socket.gethostname(), pid=os.getpid(), process_start=transport.process_start(os.getpid()))))
    (path / 'last-message.txt').write_text(json.dumps(RESPONSE))
    # when resolving stale or still-live output, then neither can publish a usable judgment
    decision = dict(attempt=attempt['id'], attempt_evidence_sha256='0' * 64)
    with pytest.raises(response.ContractViolation, match='observed output'):
        late_evidence.reconcile(directory, current, decision)
    decision['attempt_evidence_sha256'] = qualification.digest(late_evidence.snapshot(str(path)))
    with pytest.raises(response.ContractViolation) as error:
        late_evidence.reconcile(directory, current, decision)
    assert error.value.code == 'call-not-quiescent'


@pytest.mark.parametrize('raw', [b'{partial', b'\xff'])
def should_acknowledge_invalid_late_output_without_calling_it_a_no_judgment_completion(
        review, plan_file_factory, state_root, raw):
    import pytest
    from codex_review import late_evidence, qualification, response
    # given quiescent output that is not a valid report and has no completion receipt
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop', '--no-verify', '--reason', 'invalid control')
    directory = str(state_root / prepared.envelope['job_id'])
    job = state_root / prepared.envelope['payload']['round']
    attempt = dict(id='c' * 16, round=job.name, kind='cold', outcome='retired-unknown', retired_evidence={})
    current = dict(loops.read(directory), attempts=[attempt])
    path = job / 'reviewer-attempts' / attempt['id']
    path.mkdir(parents=True)
    (path / 'launch.json').write_text(json.dumps(dict(host=socket.gethostname(), pid=2147483647, process_start='gone')))
    (path / 'last-message.txt').write_bytes(raw)
    decision = dict(attempt=attempt['id'], attempt_evidence_sha256=qualification.digest(late_evidence.snapshot(str(path))))
    with pytest.raises(response.ContractViolation, match='explicitly acknowledge'):
        late_evidence.reconcile(directory, current, decision)
    # when explicitly acknowledged as unusable, then uncertainty and charge survive
    decision['disposition'] = 'unusable'
    updated = late_evidence.reconcile(directory, current, decision)
    assert updated['attempts'][0]['outcome'] == 'retired-unknown'
    assert updated['attempts'][0]['late_result']['validation_code']
    assert not late_evidence.pending(directory, updated)
    assert updated['findings'] == []


def should_allow_cited_same_host_quiescence_for_late_output_without_a_pid(
        review, plan_file_factory, state_root):
    from codex_review import evidence, late_evidence, qualification
    # given an attempt whose launch identity was lost, followed by a complete late answer
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop', '--no-verify', '--reason', 'pidless late control')
    directory = str(state_root / prepared.envelope['job_id'])
    job = state_root / prepared.envelope['payload']['round']
    attempt = dict(id='d' * 16, round=job.name, kind='cold', outcome='retired-unknown', retired_evidence={})
    current = dict(loops.read(directory), attempts=[attempt])
    path = job / 'reviewer-attempts' / attempt['id']
    path.mkdir(parents=True)
    (path / 'last-message.txt').write_text(json.dumps(RESPONSE))
    (job / 'staging').mkdir(exist_ok=True)
    (job / 'staging' / 'quiescence.txt').write_text('Offline fixture; real use requires cited same-host process inspection')
    citation = evidence.seal(str(job), job.name, 'staging/quiescence.txt')['evidence_id']
    decision = dict(attempt=attempt['id'], attempt_evidence_sha256=qualification.digest(late_evidence.snapshot(str(path))),
                    asserted_by='user', quiescence_host=socket.gethostname(), quiescence_evidence_ids=[citation])
    # when the user supplies the same evidence required for retirement, then no invented PID is necessary
    updated = late_evidence.reconcile(directory, current, decision)
    assert updated['attempts'][0]['late_result']['report']['findings'] == RESPONSE['findings']
    assert updated['attempts'][0]['late_result']['quiescence']['asserted_by'] == 'user'


@pytest.mark.parametrize('outcome', ['withdrawn', 'maintained'])
def should_keep_cold_credit_when_reconciling_a_retired_rebuttal(
        review, plan_file_factory, state_root, outcome):
    from codex_review import late_evidence, qualification
    # given a retired targeted rebuttal and credit from earlier cold rounds
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'targeted retirement control')
    directory = str(state_root / prepared.envelope['job_id'])
    job = state_root / prepared.envelope['payload']['round']
    (job / 'report.json').write_text(json.dumps(RESPONSE))
    attempt = dict(id='c' * 16, round=job.name, kind='rebuttal', outcome='retired-unknown',
                   charged=True, retired_evidence={}, claims=[dict(id='F-1', evidence=[])])
    current = dict(loops.read(directory), attempts=[attempt])
    current['counting']['streak'] = 1
    current['findings'] = [dict(reference=dict(round=job.name, local_id='F-1'), status='refuted', disposition='rejected')]
    path = job / 'reviewer-attempts' / attempt['id']
    path.mkdir(parents=True)
    (path / 'launch.json').write_text(json.dumps(dict(host=socket.gethostname(), pid=2147483647, process_start='gone')))
    (path / 'last-message.txt').write_text(json.dumps(dict(decisions=[dict(id='F-1', classification='introduced',
        outcome=outcome, rationale='Assessment of the cited evidence')], new_findings=[])))
    decision = dict(attempt=attempt['id'], attempt_evidence_sha256=qualification.digest(late_evidence.snapshot(str(path))))
    # when its late output is reconciled, then it neither gains nor discards cold-round credit
    result = late_evidence.reconcile(directory, current, decision)
    assert result['counting'] == current['counting']
    assert job.name not in result.get('disqualified_rounds', [])
    assert result['attempts'][0]['charged']
    assert result['findings'][0]['status'] == ('open' if outcome == 'maintained' else 'refuted')
    bound = result['rebuttals'][job.name]
    assert bound['sha256'] == images.sha256_file(bound['path'])
    assert bound['result']['decisions'][0]['outcome'] == outcome
    if outcome == 'maintained':
        from codex_review import adjudication, response
        with pytest.raises(response.ContractViolation, match='requires cited new evidence'):
            adjudication.require_retained_reopening(result, dict(round=job.name, local_id='F-1'), str(job), None)


def deny_then_refute_retained(review, directory, fresh, origin, plan, raw, alias, codex_reply, tmp_path):
    from test_phase1b_loop_interface import qualify_cold
    from test_phase1b_rebuttal import record_rejections
    from codex_review import evidence
    import uuid
    def status():
        return review('status', str(directory)).envelope['payload']
    def invoke(verb, *args, **kwargs):
        current = status()
        member = [] if verb in ('continue', 'recapture') else ['--round', current['round']]
        return review(verb, str(directory), *member, '--expected-generation', str(current['generation']), *args, **kwargs)
    def decide(payload):
        (directory / 'staging/decision.json').write_text(json.dumps(dict(payload, checkpoint_sha256=status()['checkpoint_sha256'])))
        return invoke('continue', '--decision-file', 'staging/decision.json')
    # given acceptance could not be applied, preserve the unchanged candidate and terminal evidence
    payload = dict(report_sha256=status()['report_sha256'], probes_sha256=status()['probes_sha256'],
                   results=[dict(finding_id=alias, result='denied', denial_kind='interactive', asserted_by='user',
                                 sibling_sweep=dict(searched_scope=[], related_sites=[], rationale='Edit declined before any change'))])
    (fresh / 'staging/apply.json').write_text(json.dumps(payload))
    applied = invoke('record-apply', '--apply-file', 'staging/apply.json')
    assert applied.exit_code == 11, applied
    (fresh / 'staging/verify.sh').write_text('true\n')
    command = invoke('seal', 'staging/verify.sh').envelope['payload']['evidence_id']
    payload = dict(apply_sha256=images.sha256_file(str(fresh / 'apply.json')), verification=dict(
        result='denied', command_evidence_id=command, denial_kind='interactive', asserted_by='user'))
    (fresh / 'staging/verification.json').write_text(json.dumps(payload))
    assert invoke('record-verification', '--verification-file', 'staging/verification.json').exit_code == 11
    assert invoke('continue').exit_code == 0
    before = {p.name: p.read_bytes() for p in fresh.glob('*.json')}
    (fresh / 'staging/reassess.txt').write_text('Independent fresh investigation is authorized; no fix landed')
    citation = evidence.seal(str(fresh), fresh.name, 'staging/reassess.txt')['evidence_id']
    assert decide(dict(kind='finding-resolution', disposition='reassess', reference=dict(round=origin.name, local_id='F-1'),
                       probes_sha256=status()['probes_sha256'], evidence_ids=[citation], rationale='Investigate the retained claim')).exit_code == 0
    assert invoke('recapture').exit_code == 0
    newer = directory.parent / status()['round']
    qualify_cold(directory.parent, newer)
    log = tmp_path / 'third.jsonl'
    log.write_text(json.dumps(dict(type='thread.started', thread_id=str(uuid.uuid4()))) + '\n{"type":"turn.completed"}\n')
    codex_reply(dict(RESPONSE, verdict='approve', findings=[]))
    assert invoke('run', fake={'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log}).exit_code == 0
    assert decide(dict(kind='read-assessment', round=newer.name, disposition='no-observed-breach',
                       report_sha256=status()['report_sha256'], log_sha256=images.sha256_file(str(log)),
                       previous_assessment_sha256=None, event_lines=[], rationale='Complete offline no-read fixture')).exit_code == 0
    assert record_rejections(review, directory, newer, []).exit_code == 0
    # when a third-round qualified counterexample refutes the late origin, then its retained source still resolves
    annotation = independent_annotation(review, directory, newer, alias, plan)
    resolved = decide(dict(kind='finding-resolution', disposition='refuted', reference=dict(round=origin.name, local_id='F-1'),
                           origin_report_sha256=images.sha256_file(str(raw)), assessment=annotation,
                           evidence_ids=[annotation['output_evidence_id']], rationale='Equivalent inputs disprove the retained claim'))
    assert resolved.exit_code == 0, resolved
    assert not status()['pending_findings']
    assert {p.name: p.read_bytes() for p in fresh.glob('*.json')} == before
