"""Unresolved author assessments can be revisited without rewriting prior evidence."""
import json

import pytest

from codex_review import images
from test_phase1b_rebuttal import disputed_round, record_rejections


@pytest.mark.parametrize('disputed_round,disposition', [('pre-existing', None), ('introduced', 'rejected')], indirect=['disputed_round'])
def should_pause_completed_rounds_with_unsettled_probe_reads(review, disputed_round, disposition):
    # given valid executed evidence whose observation is inconclusive
    directory, job, _, _ = disputed_round
    assert record_rejections(review, directory, job, ['F-1'], disposition, read='inconclusive').exit_code == 0
    def status():
        return review('status', str(directory)).envelope['payload']
    for name in ('apply', 'verification'):
        current = status()
        payload = (dict(report_sha256=current['report_sha256'], probes_sha256=current['probes_sha256'], results=[])
                   if name == 'apply' else dict(apply_sha256=images.sha256_file(str(job / 'apply.json')),
                                               verification=dict(result='not-run-explicitly')))
        (job / 'staging' / (name + '.json')).write_text(json.dumps(payload))
        assert review('record-' + name, str(directory), '--round', job.name, '--expected-generation',
                      str(current['generation']), '--' + name + '-file', 'staging/' + name + '.json').exit_code == 0
    # when counting a completed round, then the durable unresolved finding prevents clean credit
    result = review('continue', str(directory), '--expected-generation', str(status()['generation']))
    assert result.exit_code == 0, result
    assert result.envelope['payload']['clean_streak']['streak'] == 0
    assert result.envelope['payload']['pause']['kind'] == 'unresolved-findings'
    assert result.envelope['payload']['pending_findings'][0]['reference'] == dict(round=job.name, local_id='F-1')


def should_resolve_preexisting_uncertainty_only_after_a_conclusive_reassessment():
    from codex_review import findings
    # given unresolved evidence retained without an explicit scope question
    pending = dict(finding_id='F1', read='inconclusive', probe_execution='executed')
    ledger = findings.add([], 'round', [pending])
    assert findings.blocking(ledger)
    # when a later probe establishes pre-existing behavior, then its resolved record remains visible
    ledger = findings.add(ledger, 'round', [dict(pending, read='confirmed-pre-existing')])
    assert not findings.outstanding(ledger)
    assert ledger[0]['status'] == 'reviewed'


@pytest.mark.parametrize('disputed_round', ['pre-existing'], indirect=True)
def should_preserve_an_explicit_scope_question_through_reassessment(review, disputed_round):
    # given a known pre-existing behavior whose scope is awaiting user direction
    directory, job, citation, _ = disputed_round
    assert record_rejections(review, directory, job, ['F-1'], scope_question=True).exit_code == 0
    current = review('status', str(directory)).envelope['payload']
    decision = dict(kind='finding-resolution', disposition='reassess',
                    reference=dict(round=job.name, local_id='F-1'),
                    checkpoint_sha256=current['checkpoint_sha256'], probes_sha256=current['probes_sha256'],
                    evidence_ids=[citation], rationale='Recheck the existing behavior')
    (directory / 'staging' / 'decision.json').write_text(json.dumps(decision))
    assert review('continue', str(directory), '--expected-generation', str(current['generation']),
                  '--decision-file', 'staging/decision.json').exit_code == 0
    # when a new probe still confirms the behavior but omits the scope flag
    result = record_rejections(review, directory, job, ['F-1'], None, read='confirmed-pre-existing')
    assert result.exit_code == 0, result
    # then confirmed existence cannot replace the outstanding user's scope decision
    pending = result.envelope['payload']['pending_findings']
    assert len(pending) == 1
    assert pending[0]['disposition'] == 'scope-question'


def should_bind_rebuttal_to_the_current_probe_revision(review, disputed_round, codex_reply):
    from test_phase1b_rebuttal import rebut
    # given an append-only reassessment replacing an inconclusive probe
    directory, job, citation, log = disputed_round
    assert record_rejections(review, directory, job, ['F-1'], 'inconclusive').exit_code == 0
    current = review('status', str(directory)).envelope['payload']
    decision = dict(kind='finding-resolution', disposition='reassess', reference=dict(round=job.name, local_id='F-1'),
                    checkpoint_sha256=current['checkpoint_sha256'], probes_sha256=current['probes_sha256'],
                    evidence_ids=[citation], rationale='New evidence permits another probe')
    (directory / 'staging' / 'decision.json').write_text(json.dumps(decision))
    assert review('continue', str(directory), '--expected-generation', str(current['generation']),
                  '--decision-file', 'staging/decision.json').exit_code == 0
    assert record_rejections(review, directory, job, ['F-1']).exit_code == 0
    codex_reply(dict(decisions=[dict(id='F-1', classification='introduced', outcome='withdrawn', rationale='Counterexample holds')], new_findings=[]))
    # when a rebuttal binds the current probes digest exposed by status, then it may launch
    result = rebut(review, disputed_round, {'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log})
    assert result.exit_code == 0, result


@pytest.mark.parametrize('qualified_new_runtime,changed_dependencies', [(False, False), (True, False), (True, True)])
def should_reset_author_credit_and_require_recapture_after_runtime_change(
        review, disputed_round, monkeypatch, qualified_new_runtime, changed_dependencies):
    import os
    from codex_review import counting, execution, loop_driver, loops
    from qualification_fixture import qualify_fixture
    # given a qualified counterexample whose clean credit relies on its author runtime
    directory, job, _, _ = disputed_round
    assert record_rejections(review, directory, job, ['F-1']).exit_code == 0
    current = loops.read(str(directory))
    current = loops.append(str(directory), current['generation'], dict(current,
        counting=counting.round_finished(current['counting'], effort='xhigh', eligible=True, accepted=0, blockers=False)),
        dict(kind='offline-credit-fixture'))
    monkeypatch.setenv('PATH', '/new-runtime:' + os.environ['PATH'])
    target = json.loads((job / 'target.json').read_text())
    selected = execution.key(target)
    if qualified_new_runtime:
        qualify_fixture(job.parent, job, selected)
    # when checking before credit, then old controls cannot stand for the changed runtime
    updated, requirements = loop_driver.check_author_qualifications(str(directory), current)
    assert updated['counting']['streak'] == 0
    assert updated['pause']['kind'] == 'qualification-key-changed'
    assert any(q['key'] == selected and q['valid'] == qualified_new_runtime for q in requirements)
    # a recorded transition freezes the selected runtime, but only matching controls permit recapture
    from codex_review import apply, evidence
    (job / 'staging' / 'runtime.txt').write_text('The runtime change is understood and authorized')
    citation = evidence.seal(str(job), job.name, 'staging/runtime.txt')['evidence_id']
    status = review('status', str(directory)).envelope['payload']
    decision = dict(kind='drift-resolution', round=job.name, checkpoint_sha256=status['checkpoint_sha256'],
                    target_sha256=images.sha256_file(str(job / 'target.json')), candidate_sha256=apply.identity_sha(target),
                    old_head=None, new_head=None, authority='Author authorized this runtime transition',
                    evidence_ids=[citation], rationale='Use qualified current runtime with fresh credit')
    if changed_dependencies:
        from codex_review import context
        runtime = directory.parent.parent / 'replacement-runtime'
        runtime.mkdir()
        (runtime / 'runtime.txt').write_text('new declared runtime\n')
        declarations = [dict(id='runtime', path=str(runtime))]
        declaration_file = directory / 'staging' / 'dependencies.json'
        declaration_file.write_text(json.dumps(declarations))
        dependencies, _ = context.dependencies(str(declaration_file), None, str(directory.parent), 'persistent')
        qualify_fixture(job.parent, job, execution.key(dict(target, dependencies=dependencies)))
        decision.update(scope=dict(dependencies=declarations), scope_authority='Author authorized the new runtime root',
                        launch_key=loop_driver.launch_key(dict(target, dependencies=dependencies)))
    (directory / 'staging' / 'decision.json').write_text(json.dumps(decision))
    transitioned = review('continue', str(directory), '--expected-generation', str(status['generation']),
                         '--decision-file', 'staging/decision.json')
    assert transitioned.exit_code == 0, transitioned
    captured = review('recapture', str(directory), '--expected-generation', str(transitioned.envelope['payload']['generation']))
    assert captured.exit_code == (0 if qualified_new_runtime else 18), captured
    if qualified_new_runtime:
        assert captured.envelope['payload']['clean_streak']['streak'] == 0
        assert loops.read(str(directory))['author_qualifications'] == []


@pytest.mark.parametrize('disposition', ['inconclusive', 'defer'])
def should_resolve_an_unfinished_probe_with_an_append_only_reassessment(review, disputed_round, disposition):
    # given an unresolved finding before apply, with its original probe record retained
    directory, job, citation, _ = disputed_round
    assert record_rejections(review, directory, job, ['F-1'], disposition).exit_code == 0
    before = (job / 'probes.json').read_bytes()
    current = review('status', str(directory)).envelope['payload']
    decision = dict(kind='finding-resolution', disposition='reassess',
                    reference=dict(round=job.name, local_id='F-1'),
                    checkpoint_sha256=current['checkpoint_sha256'], probes_sha256=current['probes_sha256'],
                    evidence_ids=[citation], rationale='The additional counterexample resolves the open question')
    (directory / 'staging' / 'decision.json').write_text(json.dumps(decision))
    # when reopening author assessment, then only a new probe revision may be recorded
    result = review('continue', str(directory), '--expected-generation', str(current['generation']),
                    '--decision-file', 'staging/decision.json')
    assert result.exit_code == 0, result
    assert result.envelope['payload']['next_verb'] == 'record-probes'
    assert result.envelope['payload']['previous_probes_sha256'] == images.sha256_bytes(before)
    assert result.envelope['payload']['pending_findings']
    revised = record_rejections(review, directory, job, ['F-1'])
    assert revised.exit_code == 0, revised
    assert revised.envelope['payload']['pending_findings'] == []
    assert revised.envelope['payload']['findings'][0]['status'] == 'refuted'
    assert revised.envelope['payload']['budget']['launched'] == 1
    assert (job / 'probes.json').read_bytes() == before


@pytest.mark.parametrize('disputed_round', ['introduced', 'pre-existing'], indirect=True)
@pytest.mark.parametrize('successor_read', ['unsupported', 'confirmed-pre-existing'])
@pytest.mark.parametrize('successor_classification', ['introduced', 'pre-existing', 'pre-existing-scope', 'pre-existing-excluded'])
def should_resolve_an_older_inconclusive_claim_from_current_equivalent_probe_evidence(
        review, disputed_round, state_root, codex_reply, tmp_path, successor_read, successor_classification, relation="refuted"):
    import uuid
    from codex_review import evidence
    from test_record_probes import RESPONSE
    # given a completed inconclusive round that cannot be rewritten
    directory, origin, citation, _ = disputed_round
    reported = json.loads((origin / 'report.json').read_text())
    introduced = reported['findings'][0]['classification'] == 'introduced'
    def status():
        return review('status', str(directory)).envelope['payload']
    def invoke(verb, *args):
        current = status()
        member = [] if verb in ('continue', 'recapture') else ['--round', current['round']]
        return review(verb, str(directory), *member, '--expected-generation', str(current['generation']), *args)
    def decide(decision):
        (directory / 'staging' / 'decision.json').write_text(json.dumps(dict(decision, checkpoint_sha256=status()['checkpoint_sha256'])))
        return invoke('continue', '--decision-file', 'staging/decision.json')
    assert record_rejections(review, directory, origin, ['F-1'], ('defer' if relation == 'recurring' else 'inconclusive') if introduced else None,
                             scope_question=not introduced).exit_code == 0
    for name, payload in [
        ('apply', dict(report_sha256=status()['report_sha256'], probes_sha256=status()['probes_sha256'], results=[])),
        ('verification', None),
    ]:
        if payload is None:
            payload = dict(apply_sha256=images.sha256_file(str(origin / 'apply.json')), verification=dict(result='not-run-explicitly'))
        (origin / 'staging' / (name + '.json')).write_text(json.dumps(payload))
        assert invoke('record-' + name, '--' + name + '-file', 'staging/' + name + '.json').exit_code == 0
    assert invoke('continue').exit_code == 0
    assert status()['pause']['kind'] == 'unresolved-findings'
    original = {p.name: p.read_bytes() for p in origin.glob('*.json')}
    origin_sha = images.sha256_file(str(origin / 'probes.json'))
    resolved = decide(dict(kind='finding-resolution', disposition='reassess',
                          reference=dict(round=origin.name, local_id='F-1'), probes_sha256=origin_sha,
                          evidence_ids=[citation], rationale='Independent fresh probing will investigate this retained open claim'))
    assert resolved.exit_code == 0, resolved
    assert status()['pending_findings']
    assert invoke('recapture').exit_code == 0
    current = status()
    job = state_root / current['round']
    log = tmp_path / 'second.jsonl'
    log.write_text(json.dumps(dict(type='thread.started', thread_id=str(uuid.uuid4()))) + '\n{"type":"turn.completed"}\n')
    successor_introduced = successor_classification == 'introduced'
    codex_reply(RESPONSE if successor_introduced else dict(RESPONSE, verdict='approve',
        findings=[dict(RESPONSE['findings'][0], classification='pre-existing')]))
    assert review('run', str(directory), '--round', job.name, '--expected-generation', str(current['generation']),
                  fake={'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log}).exit_code == 0
    assert decide(dict(kind='read-assessment', round=job.name, disposition='no-observed-breach',
                       report_sha256=status()['report_sha256'], log_sha256=images.sha256_file(str(log)),
                       previous_assessment_sha256=None, event_lines=[], rationale='Complete offline no-read fixture')).exit_code == 0
    successor_scope = successor_classification in ('pre-existing-scope', 'pre-existing-excluded') and successor_read == 'confirmed-pre-existing'
    assert record_rejections(review, directory, job, ['F-1'], 'rejected' if successor_introduced else None,
                             read=successor_read, scope_question=successor_scope).exit_code == 0
    (job / 'staging' / 'relation.txt').write_text('Same claim and unchanged input; current probe supplies its counterexample')
    citation = evidence.seal(str(job), job.name, 'staging/relation.txt')['evidence_id']
    if successor_scope and successor_classification == 'pre-existing-excluded':
        excluded = decide(dict(kind='finding-resolution', disposition='out-of-scope',
            reference=dict(round=job.name, local_id='F-1'), probes_sha256=status()['probes_sha256'], evidence_ids=[citation],
            asserted_by='user', authorization='User excluded the existing issue from this change', rationale='Separate scope decision'))
        assert excluded.exit_code == 0, excluded
    # when explicitly binding the two local IDs and their probe records, then the old question can close
    result = decide(dict(kind='finding-resolution', disposition=relation,
                         reference=dict(round=origin.name, local_id='F-1'),
                         related_reference=dict(round=job.name, local_id='F-1'),
                         origin_probes_sha256=origin_sha, probes_sha256=status()['probes_sha256'],
                         evidence_ids=[citation], rationale='The equivalent current probe refutes this same claim'))
    if relation == 'recurring':
        assert result.exit_code == 0, result
        excluded = decide(dict(kind='finding-resolution', disposition='out-of-scope',
            reference=dict(round=job.name, local_id='F-1'), probes_sha256=status()['probes_sha256'], evidence_ids=[citation],
            asserted_by='user', authorization='Exclude only the successor pre-existing issue', rationale='Separate scope decision'))
        assert excluded.exit_code == 0, excluded
        assert [f['reference'] for f in status()['pending_findings']] == [dict(round=origin.name, local_id='F-1')]
        assert {p.name: p.read_bytes() for p in origin.glob('*.json')} == original
        return
    if not introduced and successor_read == 'confirmed-pre-existing':
        assert result.exit_code == 8, result
        assert result.envelope['error']['code'] == 'refutation-not-probed'
        assert status()['pending_findings'][0]['disposition'] == 'scope-question'
    else:
        assert result.exit_code == 0, result
        if successor_scope and successor_classification == 'pre-existing-scope':
            assert [f['reference'] for f in status()['pending_findings']] == [dict(round=job.name, local_id='F-1')]
        else:
            assert status()['pending_findings'] == []
        assert status()['findings'][0]['resolution']['related_reference']['round'] == job.name
    assert {p.name: p.read_bytes() for p in origin.glob('*.json')} == original


@pytest.mark.parametrize('disputed_round', ['pre-existing'], indirect=True)
@pytest.mark.parametrize('revise_probes', [False, True])
def should_require_user_authority_to_resolve_a_preexisting_scope_question(review, disputed_round, codex_reply, revise_probes):
    directory, job, citation, _ = disputed_round
    # given a probed pre-existing issue with an explicit open scope question
    assert record_rejections(review, directory, job, ['F-1'], scope_question=True).exit_code == 0
    current = review('status', str(directory)).envelope['payload']
    decision = dict(kind='finding-resolution', disposition='out-of-scope',
                    reference=dict(round=job.name, local_id='F-1'), probes_sha256=current['probes_sha256'],
                    checkpoint_sha256=current['checkpoint_sha256'], evidence_ids=[citation],
                    rationale='This existing behavior belongs outside the agreed change')
    def submit():
        (directory / 'staging' / 'decision.json').write_text(json.dumps(decision))
        return review('continue', str(directory), '--expected-generation', str(current['generation']),
                      '--decision-file', 'staging/decision.json')
    # when no scope authority is cited, then the finding cannot disappear
    assert submit().envelope['error']['code'] == 'user-decision-required'
    decision.update(asserted_by='user', authorization='User confirmed this pre-existing behavior is excluded')
    result = submit()
    assert result.exit_code == 0, result
    assert not result.envelope['payload']['pending_findings']
    assert result.envelope['payload']['findings'][0]['status'] == 'out-of-scope'
    if revise_probes:
        from test_phase1b_rebuttal import rebut
        from test_record_probes import RESPONSE
        resolved = result.envelope['payload']['findings'][0]
        original_probes = (job / 'probes.json').read_bytes()
        codex_reply(dict(decisions=[dict(id='F-1', classification='pre-existing', outcome='maintained',
            rationale='The existing behavior is confirmed')], new_findings=[dict(RESPONSE['findings'][0], id='F-2',
            classification='pre-existing', claim='A separate scope question')]))
        assert rebut(review, disputed_round, {'CODEX_FAKE_RESPONSE': codex_reply.path,
            'CODEX_FAKE_STDOUT': disputed_round[3]}).exit_code == 0
        revised = record_rejections(review, directory, job, ['F-1', 'F-2'], scope_question=True)
        assert revised.exit_code == 0, revised
        ledger = {f['reference']['local_id']: f for f in revised.envelope['payload']['findings']}
        assert ledger['F-1']['status'] == 'out-of-scope'
        assert ledger['F-1']['resolution'] == resolved['resolution']
        assert ledger['F-2']['status'] == 'open'
        assert (job / 'probes.json').read_bytes() == original_probes
        return
    # when the round closes, the original scope-question annotation cannot resurrect the settled decision
    for name, payload in [('apply', dict(report_sha256=current['report_sha256'], probes_sha256=current['probes_sha256'], results=[])),
                          ('verification', None)]:
        if payload is None:
            payload = dict(apply_sha256=images.sha256_file(str(job / 'apply.json')), verification=dict(result='not-run-explicitly'))
        (job / 'staging' / (name + '.json')).write_text(json.dumps(payload))
        current = review('status', str(directory)).envelope['payload']
        assert review('record-' + name, str(directory), '--round', job.name, '--expected-generation', str(current['generation']),
                      '--' + name + '-file', 'staging/' + name + '.json').exit_code == 0
    current = review('status', str(directory)).envelope['payload']
    result = review('continue', str(directory), '--expected-generation', str(current['generation']))
    assert result.exit_code == 0, result
    assert result.envelope['payload']['clean_streak']['streak'] == 1


@pytest.mark.parametrize('changed', ['other-artifact', 'context-only', 'claimed-artifact', 'claimed-context',
                                     'scope', 'policy', 'dependency', 'companion', 'verification', 'no-artifact'])
def should_limit_cited_finding_applicability_to_unrelated_path_changes(monkeypatch, changed):
    import copy
    from codex_review import finding_resolution, response
    # given a frozen finding in b.py and one changed input in a later capture
    original = dict(mode='code', exec_dir='/repo', artifacts_detail=[
        dict(id='A0001', path='a.py', before='base', after='old'),
        dict(id='A0002', path='b.py', before='base', after='unchanged')],
        context_manifest={'a.py': 'old', 'b.py': 'unchanged', 'notes.txt': 'old'})
    target = copy.deepcopy(original)
    path = 'a.py'
    if changed in ('other-artifact', 'no-artifact'):
        target['artifacts_detail'][0]['after'] = 'fixed'
        target['context_manifest']['a.py'] = 'fixed'
    elif changed == 'context-only':
        path = 'notes.txt'
        target['context_manifest'][path] = 'new'
    elif changed == 'claimed-artifact':
        path = 'b.py'
        target['artifacts_detail'][1]['after'] = 'fixed'
    elif changed == 'claimed-context':
        path = 'b.py'
        target['context_manifest'][path] = 'fixed'
    elif changed == 'scope':
        target['artifacts_detail'].pop(0)
    elif changed == 'policy':
        target['policy_equivalence_sha256'] = 'new'
    elif changed == 'dependency':
        target['dependencies'] = [dict(id='runtime', manifest={'lib.py': 'new'}, exclude=[])]
    elif changed == 'companion':
        target['companions'] = [dict(id='authority', image='new')]
    else:
        target['verify_command'] = 'new command'
    assessment = dict(changed_paths=[path], evidence_ids=['sealed'], rationale='Independent inputs; see sealed inspection')
    inspected = []
    monkeypatch.setattr(finding_resolution, 'citations', lambda job, value: inspected.append((job, value)))
    # when granting applicability, then only a cited unrelated path change can pass
    call = lambda: finding_resolution.assessment_applicable('/job', original, target,
                                                            dict(artifact=None if changed == 'no-artifact' else 'A0002'),
                                                            dict(applicability=assessment))
    if changed in ('other-artifact', 'context-only'):
        assert call()
        assert inspected == [('/job', assessment)]
    else:
        with pytest.raises(response.ContractViolation) as error:
            call()
        assert error.value.code == 'refutation-not-equivalent'
        assert inspected == []


@pytest.mark.parametrize('assessment', [None, {}, True, dict(changed_paths=[]),
    dict(changed_paths=['a.py', 'unmentioned.py'], rationale='Incomplete inventory'),
    dict(changed_paths=['a.py', 'a.py'], rationale='Duplicate'),
    dict(changed_paths=['a.py'], rationale=' ')])
def should_require_an_exact_changed_path_inventory_and_rationale_for_finding_applicability(assessment):
    from codex_review import finding_resolution, response
    # given unchanged finding bytes but changed repository context
    original = dict(mode='code', exec_dir='/repo', artifacts_detail=[dict(id='A1', path='b.py')], context_manifest={'a.py': 'old'})
    target = dict(original, context_manifest={'a.py': 'new'})
    # when a purported applicability assessment omits its complete explanation, then it refuses
    with pytest.raises(response.ContractViolation) as error:
        finding_resolution.assessment_applicable('/job', original, target, dict(artifact='A1'), dict(applicability=assessment))
    assert error.value.code == 'finding-applicability-required'


def should_preserve_a_deferred_introduced_finding_when_its_successor_is_excluded(
        review, disputed_round, state_root, codex_reply, tmp_path):
    should_resolve_an_older_inconclusive_claim_from_current_equivalent_probe_evidence(
        review, disputed_round, state_root, codex_reply, tmp_path,
        'confirmed-pre-existing', 'pre-existing-scope', relation='recurring')
