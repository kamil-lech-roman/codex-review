"""The driver retains an accepted issue until a bound verified cold re-examination."""
import hashlib
import json
import uuid
import pytest

from qualification_fixture import qualify_author
from test_phase1b_loop_interface import qualify_cold
from test_record_probes import RESPONSE


@pytest.mark.parametrize('reviewed_input', ['artifact', 'companion', 'mismatched-companion', 'unbound'])
@pytest.mark.parametrize('origin,fix,verified,valid', [
    ('r2', 'r1', 'r1', False),
    ('r1', 'r2', 'r1', False),
    ('r1', 'r3', 'r3', False),
    ('r2', 'r2', 'r2', True),
    ('r2', 'r2', 'r3', True),
])
def should_require_the_fix_to_follow_the_finding_and_precede_reexamination(
        monkeypatch, origin, fix, verified, valid, reviewed_input):
    from codex_review import loop_driver, response
    # given valid evidence on one path, with IO/qualification stubbed to isolate round ordering
    reference = dict(round=origin, local_id='F-1')
    finding = dict(reference=reference, status='open', disposition='accepted', fix_paths=['model.py'])
    current = dict(phase='reviewed', current_round='r3', rounds=['r1', 'r2', 'r3'], generation=4,
                   attempts=[], findings=[finding], assessments={'r3': dict(disposition='no-observed-breach')}, pause=None)
    target = dict(repo_root='/repo', artifacts_detail=[dict(path='model.py' if reviewed_input == 'artifact' else 'other.py')])
    if reviewed_input in ('companion', 'mismatched-companion'):
        target['companions'] = [dict(path='/repo/model.py', image=dict(kind='regular', content_identity='verified'))]
    reviewed = dict(target)
    if reviewed_input == 'mismatched-companion':
        reviewed['companions'] = [dict(path='/repo/model.py', image=dict(kind='regular', content_identity='different'))]
    result = dict(verification_result='passed', changed_paths=['/repo/model.py'], post_fix_sha='candidate',
                  verification_contract={}, verification_evidence={})
    monkeypatch.setattr(loop_driver.loops, '_json', lambda path: (reviewed if '/r3/' in path else target)
                        if path.endswith('target.json') else result)
    monkeypatch.setattr(loop_driver.images, 'sha256_file', lambda path: 'digest')
    monkeypatch.setattr(loop_driver.execution, 'reliances', lambda manifest: [dict(key={})])
    monkeypatch.setattr(loop_driver.qualification, 'lookup', lambda *args: {})
    monkeypatch.setattr(loop_driver.qualification, 'continuous', lambda *args: True)
    monkeypatch.setattr(loop_driver.context, 'applicable', lambda target: True)
    monkeypatch.setattr(loop_driver.loops, 'snapshot_retained', lambda *args: True)
    monkeypatch.setattr(loop_driver.apply_module, 'identity_sha', lambda target: 'candidate')
    monkeypatch.setattr(loop_driver.verification, 'contract', lambda target: {})
    monkeypatch.setattr(loop_driver.verification, 'revalidate', lambda *args: None)
    monkeypatch.setattr(loop_driver, 'check_launch_qualification', lambda d, c, t: (c, dict(valid=True)))
    monkeypatch.setattr(loop_driver, 'check_exclusion_qualifications', lambda d, c, t: (c, []))
    monkeypatch.setattr(loop_driver, 'check_reviewer_qualifications', lambda d, c, t: (c, []))
    published = []
    monkeypatch.setattr(loop_driver.loops, 'append', lambda d, g, c, decision: published.append(c))
    decision = dict(disposition='fixed', reference=reference, reviewer_round='r3', verification_round=verified,
                    fix_round=fix, report_sha256='digest', verification_sha256='digest',
                    fix_apply_sha256='digest', rationale='Claimed same-path correction')
    # when closing, then stale or reversed evidence cannot substitute for a later fix
    # Verification in r3 uses that round's image, so it agrees even when r2 held different bytes.
    closure_valid = valid and reviewed_input != 'unbound' and (reviewed_input != 'mismatched-companion' or verified == 'r3')
    if closure_valid:
        loop_driver.resolve_finding('/state/loop', current, decision)
        assert published[0]['findings'][0]['status'] == 'fixed'
    else:
        with pytest.raises(response.ContractViolation) as error:
            loop_driver.resolve_finding('/state/loop', current, decision)
        assert error.value.code == ('invalid-fix-order' if not valid else 'fix-not-reexamined')
        assert published == []


@pytest.mark.parametrize('cache_failure,companion', [(None, False), ('before', False), ('after', False), (None, True),
                                                         (None, 'ignored-companion'), (None, 'ignored-dependency'),
                                                         (None, 'ignored-directory-delete'), (None, 'inconclusive-sibling'), (None, 'unattributed-sibling'), (None, 'unattributed-related-sibling'), (None, 'symlinked-ancestor'), (None, 'amended-revised-scope')])
def should_close_an_accepted_fix_only_after_verified_cold_reexamination(
        review, plan_file_factory, state_root, codex_reply, tmp_path, make_repo, cache_failure, companion):
    _verified_fix_workflow(review, plan_file_factory, state_root, codex_reply, tmp_path, make_repo, cache_failure, companion)


@pytest.mark.parametrize('phase,slot,damage', [(phase, slot, damage)
    for phase in ('closure', 'count', 'retained-credit', 'fixed-credit')
    for slot in ('command', 'output', 'rc') for damage in ('missing', 'corrupted')] +
    [('fixed-credit', 'snapshot', damage) for damage in ('missing', 'corrupted')] +
    [('closure', 'target', 'corrupted')])
def should_revalidate_verification_evidence_when_closing_and_retaining_credit(
        review, plan_file_factory, state_root, codex_reply, tmp_path, make_repo, phase, slot, damage):
    _verified_fix_workflow(review, plan_file_factory, state_root, codex_reply, tmp_path, make_repo,
                           None, False, evidence_fault=(phase, slot, damage))


@pytest.mark.parametrize('restoration', ['restored-only', 'restored-partial', 'removed-new'])
def should_close_a_verified_fix_that_removes_the_original_difference(
        review, plan_file_factory, state_root, codex_reply, tmp_path, make_repo, restoration):
    _verified_fix_workflow(review, plan_file_factory, state_root, codex_reply, tmp_path, make_repo,
                           None, restoration)


@pytest.mark.parametrize('drift_stage', ['after-capture', 'after-verification'])
def should_refuse_ignored_retained_drift_before_review_or_clean_credit(
        review, plan_file_factory, state_root, codex_reply, tmp_path, make_repo, drift_stage):
    _verified_fix_workflow(review, plan_file_factory, state_root, codex_reply, tmp_path, make_repo,
                           None, 'removed-new', retained_drift=drift_stage)


@pytest.mark.parametrize('lost_record', ['fix-apply', 'review-report', 'review-log'])
@pytest.mark.parametrize('damage', ['missing', 'corrupted'])
def should_reopen_fixed_closures_when_their_bound_records_are_lost(
        review, plan_file_factory, state_root, codex_reply, tmp_path, make_repo, lost_record, damage):
    _verified_fix_workflow(review, plan_file_factory, state_root, codex_reply, tmp_path, make_repo,
                           None, 'ignored-dependency', closure_fault=(lost_record, damage))


def _verified_fix_workflow(review, plan_file_factory, state_root, codex_reply, tmp_path, make_repo,
                           cache_failure, companion, evidence_fault=None, retained_drift=None, closure_fault=None):
    # given a loop with configured verification and an accepted introduced defect
    import subprocess
    from codex_review import apply, context, execution, images, loop_driver
    from qualification_fixture import qualify_fixture, qualify_command
    related = companion == 'unattributed-related-sibling'
    unattributed = companion == 'unattributed-sibling' or related
    sibling = companion == 'inconclusive-sibling' or unattributed
    aliased = companion == 'symlinked-ancestor'
    amended = companion == 'amended-revised-scope'
    restoration = companion in ('restored-only', 'restored-partial', 'removed-new')
    ignored = isinstance(companion, str) and not sibling and not aliased and not amended and not restoration
    verify_in_recheck = companion in ('ignored-dependency', 'ignored-directory-delete')
    flags = []
    if restoration:
        repo = make_repo({'b.py': 'VALUE = 0\n'} if companion == 'removed-new' else
                         {'a.py': 'VALUE = 0\n', 'b.py': 'VALUE = 0\n'})
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
        plan = repo / 'a.py'
        plan.write_text('VALUE = 1\n')
        paths = ['a.py']
        if companion == 'restored-partial':
            (repo / 'b.py').write_text('VALUE = 2\n')
            paths.append('b.py')
        assertion = tmp_path / 'expected-paths.json'
        assertion.write_text(json.dumps(paths))
        prepared = review('prepare', 'code', '--base', head, '--head', head, '--loop', '--verify', 'echo verified',
                          '--expect-paths', str(assertion), cwd=repo)
        fix_path = 'a.py'
    elif aliased:
        repo = make_repo({'src/module.py': 'VALUE = 0\n'})
        (repo / 'alias').symlink_to('src', target_is_directory=True)
        subprocess.run(['git', 'add', 'alias'], cwd=repo, check=True)
        subprocess.run(['git', 'commit', '-m', 'fixture alias'], cwd=repo, check=True, capture_output=True)
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
        plan = repo / 'alias/module.py'
        plan.write_text('VALUE = 1\n')
        prepared = review('prepare', 'code', '--base', head, '--head', head, '--loop', '--verify', 'echo verified', cwd=repo)
        fix_path = 'alias/module.py'
    elif sibling or amended:
        repo = make_repo({'a.py': 'VALUE = 0\n', 'b.py': 'VALUE = 0\n'})
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
        plan = repo / 'a.py'
        plan.write_text('VALUE = 1\n')
        (repo / 'b.py').write_text('VALUE = 2\n')
        prepared = review('prepare', 'code', '--base', head, '--head', head, '--loop', '--verify', 'echo verified', cwd=repo)
        fix_path = 'a.py'
    elif ignored:
        repo = make_repo({'app.py': 'VALUE = 0\n', '.gitignore': 'runtime/\n'})
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
        (repo / 'app.py').write_text('VALUE = 2\n')
        (repo / 'runtime').mkdir()
        plan = repo / 'runtime/value.py'
        plan.write_text('defective text\n')
        declarations = tmp_path / 'inputs.json'
        declarations.write_text(json.dumps([dict(id='runtime', path=str(plan.parent if companion == 'ignored-directory-delete' else plan))]))
        flags = ['--dependencies-file' if verify_in_recheck else '--companions-file', str(declarations)]
        prepared = review('prepare', 'code', '--base', head, '--head', head, '--loop', '--verify', 'echo verified', *flags, cwd=repo)
        fix_path = 'runtime/value.py'
    else:
        plan = plan_file_factory('defective text\n')
        if companion:
            declarations = tmp_path / 'companions.json'
            declarations.write_text(json.dumps([dict(id='governing', path=str(plan))]))
            flags = ['--companions-file', str(declarations)]
        prepared = review('prepare', 'plan', str(plan), '--no-repo', '--loop', '--verify', 'echo verified', *flags)
        fix_path = plan.name
    assert prepared.exit_code == 0, prepared
    loop_id = prepared.envelope['job_id']
    directory = state_root / loop_id
    def status():
        return review('status', loop_id).envelope['payload']
    def invoke(verb, *args):
        current = status()
        round_args = [] if verb in ('continue', 'recapture') else ['--round', current['round']]
        return review(verb, loop_id, *round_args, '--expected-generation', str(current['generation']), *args)
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    def decide(decision):
        decision = dict(decision, checkpoint_sha256=status()['checkpoint_sha256'])
        (directory / 'staging').mkdir(exist_ok=True)
        (directory / 'staging' / 'decision.json').write_text(json.dumps(decision))
        return invoke('continue', '--decision-file', 'staging/decision.json')
    def capture_report(report):
        current = status()
        job = state_root / current['round']
        log = tmp_path / (current['round'] + '.jsonl')
        log.write_text(json.dumps(dict(type='thread.started', thread_id=str(uuid.uuid4()))) + '\n{"type":"turn.completed"}\n')
        qualify_cold(state_root, job)
        if sibling or amended or companion == 'restored-partial':
            report = dict(report, declared_scope=['A0001', 'A0002'])
        codex_reply({k: v for k, v in report.items() if not (ignored or sibling or aliased or amended or restoration) or k != 'verified_claims'})
        ran = review('run', loop_id, '--round', current['round'], '--expected-generation', str(current['generation']),
                     fake={'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log})
        assert ran.exit_code == 0, ran
        assessed = decide(dict(kind='read-assessment', round=job.name, disposition='no-observed-breach',
                               rationale='Offline complete no-read fixture', event_lines=[],
                               previous_assessment_sha256=None, report_sha256=sha(job / 'report.json'),
                               log_sha256=sha(log)))
        assert assessed.exit_code == 0, assessed
        return job
    def seal(job, slot, text):
        path = job / slot
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        return invoke('seal', slot).envelope['payload']['evidence_id']
    def record(job, name, payload):
        (job / 'staging' / (name + '.json')).write_text(json.dumps(payload))
        result = invoke('record-' + name, '--' + name + '-file', 'staging/' + name + '.json')
        assert result.exit_code == 0, result
    qualify_cold(state_root, state_root / status()['round'])
    first_report = RESPONSE
    if sibling:
        first_report = dict(RESPONSE, findings=RESPONSE['findings'] + [dict(RESPONSE['findings'][0], id='F-2',
            artifact=None if unattributed else 'A0002', classification='pre-existing' if unattributed else 'introduced')])
    origin = capture_report(first_report)
    qualify_author(origin, commands=['echo checked\n'])
    annotation = dict(finding_id='F-1', disposition='accepted', read='confirmed-introduced',
                      probe_execution='executed', fix_paths=[fix_path], rationale='The fixture demonstrates the claimed defect',
                      false_result='The old text would satisfy the stated rule',
                      path_exercised='Sealed output identifies the inspected rule',
                      command_evidence_id=seal(origin, 'staging/probe-F-1.sh', 'echo checked\n'),
                      output_evidence_id=seal(origin, 'probes/F-1.out', 'checked\n'),
                      rc_evidence_id=seal(origin, 'probes/F-1.rc', '0\n'))
    annotations = [annotation]
    if sibling:
        annotations.append(dict(finding_id='F-2', disposition='inconclusive', read='inconclusive',
            probe_execution='executed', rationale='The first observation did not decide the independent claim',
            command_evidence_id=seal(origin, 'staging/probe-F-2.sh', 'echo checked\n'),
            output_evidence_id=seal(origin, 'probes/F-2.out', 'checked\n'),
            rc_evidence_id=seal(origin, 'probes/F-2.rc', '0\n')))
    if unattributed:
        annotations[-1].pop('disposition')
    record(origin, 'probes', dict(report_sha256=sha(origin / 'report.json'), annotations=annotations))
    apply_binding = {}
    if amended:
        initial_probes = (origin / 'probes.json').read_bytes()
        amendment = dict(checkpoint_sha256=status()['checkpoint_sha256'], report_sha256=sha(origin / 'report.json'),
                         probes_sha256=sha(origin / 'probes.json'), additions=[dict(finding_id='F-1', paths=['b.py'],
                         rationale='The accepted fix also needs its helper', authority='Authorized implementation scope')])
        (origin / 'staging/amendment.json').write_text(json.dumps(amendment))
        added = invoke('amend-paths', '--amendment-file', 'staging/amendment.json')
        assert added.exit_code == 0, added
        apply_binding['amendments_sha256'] = added.envelope['payload']['amendments_sha256']
        revised = decide(dict(kind='finding-resolution', disposition='reassess',
            reference=dict(round=origin.name, local_id='F-1'), probes_sha256=sha(origin / 'probes.json'),
            evidence_ids=[annotation['output_evidence_id']], rationale='Retain the accepted probe with amended scope'))
        assert revised.exit_code == 0, revised
        record(origin, 'probes', dict(report_sha256=sha(origin / 'report.json'), annotations=annotations,
                                      previous_probes_sha256=sha(origin / 'probes.json')))
        assert (origin / 'probes.json').read_bytes() == initial_probes
        plan = repo / 'b.py'
    if companion in ('ignored-directory-delete', 'removed-new'):
        plan.unlink()
    elif restoration:
        plan.write_text('VALUE = 0\n')
    else:
        plan.write_text('corrected text\n')
    if restoration:
        subprocess.run(['git', 'update-index', '--refresh'], cwd=repo, capture_output=True)
    record(origin, 'apply', dict(report_sha256=sha(origin / 'report.json'), probes_sha256=status()['probes_sha256'], **apply_binding,
                                results=[dict(finding_id='F-1', result='applied',
                                              sibling_sweep=dict(rationale='Only the plan sentence applies', searched_scope=[fix_path], related_sites=[]))]))
    if verify_in_recheck:
        # A changed declared runtime needs qualified authorized recapture before new execution.
        target = json.loads((origin / 'target.json').read_text())
        post = context.after_apply(str(origin), target)
        qualify_fixture(state_root, origin, execution.key(post))
        qualify_command(origin, 'echo verified\n', post)
        citation = seal(origin, 'staging/transition.txt', 'Authorize the corrected declared runtime and fresh verification')
        transitioned = decide(dict(kind='drift-resolution', round=origin.name,
            target_sha256=sha(origin / 'target.json'), candidate_sha256=apply.identity_sha(target),
            old_head=head, new_head=head, launch_key=loop_driver.launch_key(post), evidence_ids=[citation],
            authority='Author authorized the corrected runtime', rationale='Requalify and verify in the new round'))
        assert transitioned.exit_code == 0, transitioned
    else:
        record(origin, 'verification', dict(apply_sha256=sha(origin / 'apply.json'), verification=dict(result='passed',
            command_evidence_id=seal(origin, 'staging/verify.sh', 'echo verified\n'),
            output_evidence_id=seal(origin, 'verify.out', 'verified\n'), rc_evidence_id=seal(origin, 'verify.rc', '0\n'))))
        assert invoke('continue').exit_code == 0
    assert status()['pending_findings'][0]['reference'] == dict(round=origin.name, local_id='F-1')
    assert status()['clean_streak']['streak'] == 0
    if sibling:
        assert status()['pause']['kind'] == 'unresolved-findings'
        result = decide(dict(kind='finding-resolution', disposition='reassess',
            reference=dict(round=origin.name, local_id='F-2'), probes_sha256=sha(origin / 'probes.json'),
            evidence_ids=[annotation['output_evidence_id']], rationale='Reprobe the independent claim after the accepted fix'))
        assert result.exit_code == 0, result
    original_records = {p.name: p.read_bytes() for p in origin.glob('*.json')}
    # when a fresh complete-scope review examines the verified candidate
    if retained_drift:
        (repo / '.git/info/exclude').write_text('a.py\n')
    assert invoke('recapture').exit_code == 0
    if retained_drift == 'after-capture':
        job = state_root / status()['round']
        qualify_cold(state_root, job)
        plan.write_text('VALUE = 999\n')
        refused = invoke('run')
        assert refused.exit_code == 4, refused
        assert status()['clean_streak']['streak'] == 0
        assert not (job / 'report.json').exists()
        return
    later_findings = [dict(RESPONSE['findings'][0], id='F-2', classification='pre-existing', artifact=None)] if related else []
    recheck = capture_report(dict(verdict='approve', findings=later_findings, declared_scope=['A0001'], verified_claims=[]))
    if restoration:
        target = json.loads((recheck / 'target.json').read_text())
        retained = next(a for a in target['artifacts_detail'] if a['path'] == 'a.py')
        assert retained['projection'] == 'retained'
        assert (recheck / 'artifacts' / retained['id'] / 'after').read_bytes() == (b'' if companion == 'removed-new' else b'VALUE = 0\n')
    if sibling:
        from test_phase1b_late_evidence import independent_annotation
        # The unchanged b.py is assessed separately; the cold reviewer did not repeat its claim.
        if related:
            from test_phase1b_rebuttal import record_rejections
            assert record_rejections(review, directory, recheck, ['F-2'], None, read='unsupported').exit_code == 0
            counterexample = json.loads((recheck / 'probes.json').read_text())['annotations'][0]
            assessment = dict(related_reference=dict(round=recheck.name, local_id='F-2'), probes_sha256=sha(recheck / 'probes.json'))
        else:
            counterexample = independent_annotation(review, directory, recheck, 'retained-' + origin.name + '-F-2', plan,
                                                    command='test "$(cat b.py)" = "VALUE = 2" && cat b.py\n')
            if unattributed:
                counterexample.pop('disposition')
            assessment = dict(assessment=counterexample)
        applicability = dict(changed_paths=['a.py'], rationale='a.py and b.py are independent constants with no imports',
                             evidence_ids=[counterexample['output_evidence_id']])
        resolution = dict(kind='finding-resolution', disposition='refuted', reference=dict(round=origin.name, local_id='F-2'),
            origin_report_sha256=sha(origin / 'report.json'), origin_probes_sha256=sha(origin / 'probes.json'),
            **assessment, applicability=applicability, evidence_ids=[counterexample['output_evidence_id']],
            rationale='The qualified current counterexample applies to the unchanged original b.py')
        if unattributed:
            before = status()
            refused = decide(resolution)
            assert refused.exit_code == 8, refused
            assert refused.envelope['error']['code'] == 'refutation-not-equivalent'
            assert status()['generation'] == before['generation']
            assert status()['pending_findings'] == before['pending_findings']
            assert {p.name: p.read_bytes() for p in origin.glob('*.json')} == original_records
            return
        for invalid, code in [(dict(resolution, applicability=None), 'finding-applicability-required'),
                              (dict(resolution, applicability=dict(applicability, evidence_ids=[])), 'missing-resolution-evidence')]:
            refused = decide(invalid)
            assert refused.exit_code == 8, refused
            assert refused.envelope['error']['code'] == code
            assert len(status()['pending_findings']) == 2
        resolved = decide(resolution)
        assert resolved.exit_code == 0, resolved
        assert next(f for f in status()['findings'] if f['reference']['local_id'] == 'F-2')['status'] == 'refuted'
        assert {p.name: p.read_bytes() for p in origin.glob('*.json')} == original_records
    assert len(status()['pending_findings']) == 1
    decision = dict(kind='finding-resolution', disposition='fixed', reference=dict(round=origin.name, local_id='F-1'),
                    reviewer_round=recheck.name, verification_round=origin.name,
                    report_sha256=sha(recheck / 'report.json'), verification_sha256='0' * 64,
                    rationale='The complete-scope cold recheck confirms the relevant verified sentence change')
    # then a wrong evidence binding refuses, while explicit valid closure retains the evidence chain
    if not verify_in_recheck:
        assert decide(decision).envelope['error']['code'] == 'stale-closure-evidence'
        decision['verification_sha256'] = sha(origin / 'verification.json')
    def damage_verification(job):
        block = json.loads((job / 'verification.json').read_text())['verification_evidence']
        phase, slot, damage = evidence_fault
        if slot == 'target':
            path = job / 'target.json'
            target = json.loads(path.read_text())
            target['retained_files'] = {}
            path.write_text(json.dumps(target))
            original_records['target.json'] = path.read_bytes()
            return
        path = job / 'artifacts/A0001/after' if slot == 'snapshot' else job / 'evidence' / block[slot + '_evidence_id'] / 'content'
        if damage == 'missing':
            path.unlink()
        else:
            path.write_text('corrupted after recording\n')
    if evidence_fault and evidence_fault[0] == 'closure':
        before = status()
        damage_verification(origin)
        refused = decide(decision)
        assert refused.exit_code == 8, refused
        assert status()['pending_findings'] == before['pending_findings']
        assert status()['clean_streak'] == before['clean_streak']
        assert {p.name: p.read_bytes() for p in origin.glob('*.json')} == original_records
        return
    def fail_then_requalify_cache():
        from codex_review import execution, qualification
        bound = execution.key(json.loads((origin / 'target.json').read_text()))
        previous = qualification.lookup(str(state_root), bound)
        control = json.loads((state_root / 'qualifications' / (previous['digest'] + '.json')).read_text())
        evidence_job = state_root / control.pop('evidence_job')
        control.update(supersedes=previous['digest'])
        control['controls'][0]['outcome'] = 'fail'
        qualification.record(str(state_root), str(evidence_job), control)
        qualify_author(recheck)
    if not verify_in_recheck:
        if cache_failure == 'before':
            fail_then_requalify_cache()
        closed = decide(decision)
        if cache_failure == 'before':
            assert closed.exit_code == 8, closed
            assert closed.envelope['error']['code'] == 'fix-evidence-unqualified'
            assert len(status()['pending_findings']) == 1
        else:
            assert closed.exit_code == 0, closed
            assert status()['pending_findings'] == []
    # when completing that unchanged candidate's round, its configured command still runs
    qualify_author(recheck)
    record(recheck, 'probes', dict(report_sha256=sha(recheck / 'report.json'), annotations=[]))
    record(recheck, 'apply', dict(report_sha256=sha(recheck / 'report.json'),
                                probes_sha256=sha(recheck / 'probes.json'), results=[]))
    record(recheck, 'verification', dict(apply_sha256=sha(recheck / 'apply.json'), verification=dict(result='passed',
        command_evidence_id=seal(recheck, 'staging/verify.sh', 'echo verified\n'),
        output_evidence_id=seal(recheck, 'verify.out', 'verified again\n'), rc_evidence_id=seal(recheck, 'verify.rc', '0\n'))))
    assert json.loads((recheck / 'verification.json').read_text())['verification_result'] == 'passed'
    if cache_failure == 'before' or verify_in_recheck:
        decision.update(verification_round=recheck.name, verification_sha256=sha(recheck / 'verification.json'),
                        fix_round=origin.name, fix_apply_sha256=sha(origin / 'apply.json'))
        closed = decide(decision)
        assert closed.exit_code == 0, closed
        assert status()['pending_findings'] == []
    if closure_fault:
        from codex_review import counting, loops
        # given a valid fixed closure, independently of any streak bindings
        assert invoke('continue').exit_code == 0
        assert invoke('recapture').exit_code == 0
        capture_report(dict(verdict='approve', findings=[], declared_scope=['A0001'], verified_claims=[]))
        current = loops.read(str(directory))
        current = loops.append(str(directory), current['generation'], dict(current, counting=counting.reset(current['counting'])),
                               dict(kind='offline-independent-closure-retention-control'))
        lost_record, damage = closure_fault
        path = (origin / 'apply.json' if lost_record == 'fix-apply' else recheck / 'report.json' if lost_record == 'review-report' else
                next((recheck / 'reviewer-attempts').iterdir()) / 'stdout.jsonl')
        if damage == 'missing':
            path.unlink()
        else:
            path.write_text('corrupted bound record\n')
        # when admitting later work, then the old fixed finding reopens even without streak evidence
        updated, _ = loop_driver.check_launch_qualification(str(directory), current,
            json.loads((state_root / current['current_round'] / 'target.json').read_text()))
        finding = next(f for f in updated['findings'] if f['reference'] == dict(round=origin.name, local_id='F-1'))
        assert finding['status'] == 'open'
        assert updated['counting']['streak'] == 0
        return
    if retained_drift == 'after-verification':
        plan.write_text('VALUE = 999\n')
        refused = invoke('continue')
        assert refused.exit_code == 18, refused
        assert refused.envelope['error']['details']['reason'] == 'candidate-drift'
        assert status()['clean_streak']['streak'] == 0
        assert not status()['clean_streak']['converged']
        return
    if cache_failure == 'after':
        fail_then_requalify_cache()
    if evidence_fault and evidence_fault[0] in ('count', 'fixed-credit'):
        before = status()
        damage_verification(recheck if evidence_fault[0] == 'count' else origin)
        finished = invoke('continue')
        if evidence_fault[0] == 'count':
            assert finished.exit_code == 8, finished
            assert status()['clean_streak'] == before['clean_streak']
        else:
            assert finished.exit_code == 0, finished
            assert status()['pending_findings'][0]['reference'] == dict(round=origin.name, local_id='F-1')
            assert status()['clean_streak']['streak'] == 0
        return
    assert invoke('continue').exit_code == 0
    assert status()['clean_streak']['streak'] == (0 if cache_failure == 'after' else 1)
    if cache_failure == 'after':
        assert status()['pending_findings'][0]['reference'] == dict(round=origin.name, local_id='F-1')
    if evidence_fault and evidence_fault[0] == 'retained-credit':
        damage_verification(recheck)
        assert invoke('recapture').exit_code == 0
        capture_report(dict(verdict='approve', findings=[], declared_scope=['A0001'], verified_claims=[]))
        assert status()['clean_streak']['streak'] == 0
        assert not status()['clean_streak']['converged']
