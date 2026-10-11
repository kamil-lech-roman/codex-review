"""A loop is discoverable and its rounds cannot be mistaken for standalone jobs."""
import json
from pathlib import Path
import pytest


def should_refuse_loop_preparation_without_a_target_mode(review):
    # given a loop request without its positional target mode
    # when preparing, then malformed input gets the public usage envelope
    result = review('prepare', '--loop', '--no-verify', '--reason', 'inspection')
    assert result.exit_code == 2, result
    assert result.envelope['verb'] == 'prepare'
    assert result.envelope['error']['code'] == 'no-mode'


def should_prepare_flat_loop_and_list_only_its_public_handle(review, plan_file_factory, state_root):
    # given a plan review with explicit loop intent
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'inspection')
    # when initial capture publishes
    assert prepared.exit_code == 0, prepared
    assert prepared.envelope['schema_version'] == 2
    loop_id = prepared.envelope['job_id']
    round_id = prepared.envelope['payload']['round']
    # then loop and round are separate direct children with explicit membership
    assert loop_id != round_id
    assert (state_root / loop_id / 'loop.json').is_file()
    target = json.loads((state_root / round_id / 'target.json').read_text())
    assert target['loop_id'] == loop_id
    listed = review('status')
    assert [entry['job_id'] for entry in listed.envelope['payload']['open_jobs']] == [loop_id]
    status = review('status', loop_id)
    assert status.envelope['payload']['round'] == round_id
    assert status.envelope['payload']['generation'] == 0
    assert status.envelope['payload']['target_sha256'] == target['target_sha']
    assert status.envelope['payload']['target_record_sha256']
    assert status.envelope['payload']['bound_context']['companions'] == []
    assert status.envelope['payload']['latest_read_assessment'] is None
    assert status.envelope['payload']['call_outcomes']['pending_completion'] == []
    assert review('run', round_id).exit_code == 2


@pytest.mark.parametrize('verb,option', [('status', '--round'), ('continue', '--round'),
                                      ('recapture', '--round'), ('abandon', '--round'),
                                      ('status', '--expected-generation')])
def should_refuse_loop_options_the_operation_cannot_honor(review, plan_file_factory, verb, option):
    # given a discoverable loop and an inapplicable option
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'inspection')
    # when attempting to pass a round selector to a loop-wide operation
    result = review(verb, prepared.envelope['job_id'], option, prepared.envelope['payload']['round'])
    # then no selector is silently ignored
    assert result.exit_code == 2, result
    assert result.envelope['error']['code'] == 'bad-option'


def should_keep_a_damaged_loop_discoverable_without_crashing_status(review, plan_file_factory, state_root):
    # given a damaged loop record alongside normally resolvable state
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'inspection')
    loop_id = prepared.envelope['job_id']
    path = state_root / loop_id / 'loop.json'
    record = json.loads(path.read_text())
    del record['current_round']
    path.write_text(json.dumps(record))
    # when listing and inspecting, then the handle remains visible and damage is a structured refusal
    listed = review('status')
    assert listed.exit_code == 0, listed
    assert listed.envelope['payload']['open_jobs'][0]['stage'] == 'inconsistent'
    inspected = review('status', loop_id)
    assert inspected.exit_code == 8, inspected
    assert inspected.envelope['error']['code'] == 'invalid-loop-state'


def should_require_full_head_pin_and_base_for_code_loop(review, git_repo):
    # given a loop code request without the agreed candidate pin
    # when preparing, then no unpinned loop can publish
    result = review('prepare', 'code', '--loop', '--base', 'HEAD',
                    '--no-verify', '--reason', 'inspection', cwd=git_repo)
    assert result.exit_code == 2
    assert result.envelope['error']['code'] == 'loop-capture-options'


def should_refuse_unqualified_launch_without_charging_or_starting_reviewer(review, plan_file_factory, tmp_path):
    # given a loop without live qualification evidence
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'inspection')
    payload = prepared.envelope['payload']
    marker = tmp_path / 'launched.json'
    # when run is requested with a current checkpoint
    result = review('run', prepared.envelope['job_id'], '--round', payload['round'],
                    '--expected-generation', '0', fake={'CODEX_FAKE_ARGV': marker})
    # then the caller gets a checkable missing capability and no budget charge
    assert result.exit_code == 18, result
    assert result.envelope['error']['details']['reason'] == 'qualification-required'
    status = review('status', prepared.envelope['job_id']).envelope['payload']
    assert status['budget']['launched'] == 0
    assert status['required_qualifications'][0]['key']['capability'] == 'cold-launch'
    assert status['next_verb'] is None
    assert status['pause']['kind'] == 'qualification-required'
    assert 'record-qualification' in status['resolver_kinds']
    assert not marker.exists()


def qualify_cold(root, job):
    from codex_review import loop_driver
    from qualification_fixture import qualify_fixture
    target = json.loads((job / 'target.json').read_text())
    return qualify_fixture(root, job, loop_driver.launch_key(target))


def should_collect_probe_evidence_without_authorizing_execution_before_read_assessment(
        review, plan_file_factory, state_root, codex_reply):
    from qualification_fixture import qualify_author
    # given a completed judgment awaiting its read assessment, with author qualification available
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'assessment gate control')
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    qualify_cold(state_root, job)
    qualify_author(job)
    codex_reply(dict(verdict='approve', findings=[], declared_scope=['A0001'], verified_claims=[]))
    assert review('run', str(directory), '--round', job.name, '--expected-generation', '0',
                  fake={'CODEX_FAKE_RESPONSE': codex_reply.path}).exit_code == 0
    current = review('status', str(directory)).envelope['payload']
    (job / 'staging' / 'probe-F1.sh').write_text('echo checked\n')
    # when sealing a prospective probe, then collection succeeds but execution stays behind the assessment gate
    result = review('seal', str(directory), '--round', job.name, '--expected-generation', str(current['generation']),
                    'staging/probe-F1.sh')
    assert result.exit_code == 0, result
    assert not result.envelope['payload']['execution']['ready']
    assert result.envelope['payload']['execution']['reason'] == 'read-assessment-required'
    assert (job / 'evidence' / result.envelope['payload']['evidence_id'] / 'content').is_file()
    assert not (job / 'executions').exists()


@pytest.mark.parametrize('verb', ['continue', 'recapture', 'run', 'seal', 'record-qualification'])
def should_refuse_mutations_of_closed_loops(review, plan_file_factory, state_root, verb):
    # given a closed loop whose result and round records must remain stable
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'terminal control')
    loop_id = prepared.envelope['job_id']
    assert review('abandon', loop_id, '--expected-generation', '0', '--reason', 'finished').exit_code == 0
    before = review('status', loop_id).envelope['payload']
    # when a mutation is requested, then it refuses without adding a transition
    arguments = ['--round', before['round']] if verb in ('run', 'seal', 'record-qualification') else []
    if verb == 'seal':
        arguments += ['staging/evidence.txt']
    if verb == 'record-qualification':
        arguments += ['--qualification-file', 'staging/qualification.json']
    result = review(verb, loop_id, '--expected-generation', str(before['generation']), *arguments)
    assert result.exit_code == 8, result
    assert result.envelope['error']['code'] == 'already-terminal'
    assert review('status', loop_id).envelope['payload'] == before


def should_charge_qualified_cold_call_and_route_judgment_to_assessment(
        review, plan_file_factory, state_root, codex_reply, codex_argv, tmp_path):
    # given fixture qualification for a persistent launch
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'inspection')
    loop_id = prepared.envelope['job_id']
    round_id = prepared.envelope['payload']['round']
    qualify_cold(state_root, state_root / round_id)
    codex_reply({'verdict': 'approve', 'findings': [], 'declared_scope': ['A0001'], 'verified_claims': []})
    log = tmp_path / 'events.jsonl'
    log.write_text('{"type":"thread.started","thread_id":"a2c80d10-8dbb-4426-9089-f1d0b3a88e23"}\n{"type":"turn.completed"}\n')
    # when a cold call finishes with a valid judgment
    result = review('run', loop_id, '--round', round_id, '--expected-generation', '0', fake={
        'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log, 'CODEX_FAKE_ARGV': codex_argv})
    # then it costs one judgment, persists the thread and requires assessment before probes
    assert result.exit_code == 0, result
    assert '--ephemeral' not in json.loads(codex_argv.read_text())
    status = review('status', loop_id).envelope['payload']
    assert status['budget']['judgments'] == 1
    assert status['next_verb'] == 'continue'
    assert status['latest_attempt']['thread_id'] == 'a2c80d10-8dbb-4426-9089-f1d0b3a88e23'
    assert status['clean_streak']['streak'] == 0


def should_disqualify_a_cold_judgment_that_reuses_an_earlier_session(
        review, plan_file_factory, state_root, codex_reply, tmp_path):
    from codex_review import counting, loops
    # given earlier clean credit from the same emitted conversation identity
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'freshness control')
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    qualify_cold(state_root, job)
    current = loops.read(str(directory))
    thread = 'a2c80d10-8dbb-4426-9089-f1d0b3a88e23'
    credited = counting.round_finished(current['counting'], effort='xhigh', eligible=True, accepted=0, blockers=False)
    loops.append(str(directory), 0, dict(current, counting=credited,
                 attempts=[dict(id='b' * 16, round='a' * 16, kind='cold', outcome='judgment', thread_id=thread)]),
                 dict(kind='offline-earlier-session-fixture'))
    log = tmp_path / 'reused.jsonl'
    log.write_text(json.dumps(dict(type='thread.started', thread_id=thread)) + '\n{"type":"turn.completed"}\n')
    codex_reply(dict(verdict='approve', findings=[], declared_scope=['A0001'], verified_claims=[]))
    # when an ostensibly cold launch emits the old ID, then its judgment survives but loses clean eligibility
    result = review('run', str(directory), '--round', job.name, '--expected-generation', '1',
                    fake={'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log})
    assert result.exit_code == 0, result
    assert (job / 'report.json').exists()
    assert result.envelope['payload']['budget']['judgments'] == 2
    assert result.envelope['payload']['clean_streak']['streak'] == 0
    assert job.name in loops.read(str(directory))['disqualified_rounds']


@pytest.mark.parametrize('stale_at', [None, 'probes', 'apply', 'verification', 'finish'])
def should_assess_then_close_clean_round_without_replaying_records(
        review, plan_file_factory, state_root, codex_reply, tmp_path, stale_at):
    import hashlib
    # given a qualified cold judgment
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'inspection')
    loop_id, round_id = prepared.envelope['job_id'], prepared.envelope['payload']['round']
    job, loop_dir = state_root / round_id, state_root / loop_id
    qualify_cold(state_root, job)
    codex_reply({'verdict': 'approve', 'findings': [], 'declared_scope': ['A0001'], 'verified_claims': []})
    log = tmp_path / 'events.jsonl'
    log.write_text('{"type":"thread.started","thread_id":"a2c80d10-8dbb-4426-9089-f1d0b3a88e23"}\n{"type":"turn.completed"}\n')
    assert review('run', loop_id, '--round', round_id, '--expected-generation', '0', fake={
        'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log}).exit_code == 0
    def status():
        return review('status', loop_id).envelope['payload']
    current = status()
    # when trying finding work before read assessment, then it is blocked
    refused = review('record-probes', loop_id, '--round', round_id,
                     '--expected-generation', str(current['generation']), '--probes-file', 'staging/probes.json')
    assert refused.exit_code == 18
    # when a bound assessment clears the gate and the existing lifecycle records close
    decision = dict(kind='read-assessment', round=round_id, disposition='no-observed-breach',
                    rationale='Inspected complete fixture log; no command events.', event_lines=[],
                    report_sha256=hashlib.sha256((job / 'report.json').read_bytes()).hexdigest(),
                    log_sha256=hashlib.sha256(log.read_bytes()).hexdigest(), previous_assessment_sha256=None,
                    checkpoint_sha256=current['checkpoint_sha256'])
    (loop_dir / 'staging').mkdir(exist_ok=True)
    (loop_dir / 'staging' / 'decision.json').write_text(json.dumps(decision))
    assert review('continue', loop_id, '--expected-generation', str(current['generation']),
                  '--decision-file', 'staging/decision.json').exit_code == 0
    def invalidate_and_reassess(verb, args):
        from codex_review import qualification
        retained_log = Path(status()['latest_attempt']['attempt_dir']) / 'stdout.jsonl'
        retained_log.write_text(retained_log.read_text() + '{"type":"item.completed","item":{"type":"command_execution","command":"cat context"}}\n')
        refused = review(verb, loop_id, '--expected-generation', str(status()['generation']), *args)
        assert refused.exit_code == 18, refused.envelope
        assert status()['pause']['kind'] == 'read-unresolved'
        replacement = dict(decision, previous_assessment_sha256=qualification.digest(decision),
                           log_sha256=hashlib.sha256(retained_log.read_bytes()).hexdigest(), event_lines=[3],
                           checkpoint_sha256=status()['checkpoint_sha256'])
        (loop_dir / 'staging/decision.json').write_text(json.dumps(replacement))
        assert review('continue', loop_id, '--expected-generation', str(status()['generation']),
                      '--decision-file', 'staging/decision.json').exit_code == 0
    for verb, name, payload in [
        ('record-probes', 'probes', {'report_sha256': decision['report_sha256'], 'annotations': []}),
        ('record-apply', 'apply', None), ('record-verification', 'verification', None),
    ]:
        if name == 'apply':
            payload = dict(report_sha256=decision['report_sha256'],
                           probes_sha256=hashlib.sha256((job / 'probes.json').read_bytes()).hexdigest(), results=[])
        if name == 'verification':
            payload = dict(apply_sha256=hashlib.sha256((job / 'apply.json').read_bytes()).hexdigest(),
                           verification={'result': 'not-run-explicitly'})
        (job / 'staging' / (name + '.json')).write_text(json.dumps(payload))
        if stale_at == name:
            invalidate_and_reassess(verb, ['--round', round_id, '--' + name + '-file', 'staging/' + name + '.json'])
            assert not (job / (name + '.json')).exists()
        outcome = review(verb, loop_id, '--round', round_id, '--expected-generation', str(status()['generation']),
                         '--' + name + '-file', 'staging/' + name + '.json')
        assert outcome.exit_code == 0, outcome
    if stale_at == 'finish':
        invalidate_and_reassess('continue', [])
    result = review('continue', loop_id, '--expected-generation', str(status()['generation']))
    assert result.exit_code == 0, result
    assert status()['clean_streak']['streak'] == 1
    before = (job / 'verification.json').read_bytes()
    repeated = review('continue', loop_id, '--expected-generation', str(status()['generation']))
    assert repeated.exit_code == 0
    assert status()['clean_streak']['streak'] == 1
    assert (job / 'verification.json').read_bytes() == before


def should_recapture_a_finished_round_without_rewriting_it(review, plan_file_factory, state_root):
    from codex_review import loops
    # given a completed round ready for another fresh review
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'inspection')
    loop_id, round_id = prepared.envelope['job_id'], prepared.envelope['payload']['round']
    directory = state_root / loop_id
    before = (state_root / round_id / 'target.json').read_bytes()
    (state_root / round_id / 'abandoned.json').write_text(json.dumps({'reason': 'fixture completion'}))
    current = loops.read(str(directory))
    # This store fixture isolates recapture publication from reviewer transport.
    loops.append(str(directory), 0, dict(current, phase='recapture'), {'kind': 'fixture-completion'})
    # when recapture publishes another immutable member
    result = review('recapture', loop_id, '--expected-generation', '1')
    # then the old capture remains byte-identical and the new member is explicitly addressed
    assert result.exit_code == 0, result
    new_round = result.envelope['payload']['round']
    assert new_round != round_id
    assert (state_root / new_round / 'target.json').exists()
    assert (state_root / round_id / 'target.json').read_bytes() == before
    assert result.envelope['payload']['generation'] == 3


@pytest.mark.parametrize('name,spelling', [('dependencies', 'defaults'), ('dependencies', 'relative'),
    ('companions', 'relative'), ('dependencies', 'order'), ('companions', 'order')])
def should_recapture_equivalent_context_declarations_without_new_scope_authority(
        review, git_repo, tmp_path, state_root, name, spelling):
    from codex_review import loops
    # given a frozen context set with defaults, relative locators or noncanonical declaration order
    source = git_repo / 'contract.txt'
    source.write_text('bound governing bytes\n')
    other = git_repo / 'other.txt'
    other.write_bytes(source.read_bytes())
    path = source.name if spelling == 'relative' else str(source)
    entries = [dict(id='z', path=path)]
    if spelling == 'order':
        entries.append(dict(id='a', path=path))
    if name == 'dependencies' and spelling != 'defaults':
        entries = [dict(e, exclude=[]) for e in entries]
    declaration = tmp_path / 'declarations.json'
    declaration.write_text(json.dumps(entries))
    prepared = review('prepare', 'plan', str(git_repo / 'README.md'), '--loop', '--no-verify',
                      '--reason', 'recapture fixture', '--' + name + '-file', str(declaration), cwd=git_repo)
    assert prepared.exit_code == 0, prepared
    directory = state_root / prepared.envelope['job_id']
    old = state_root / prepared.envelope['payload']['round']
    before = (old / 'target.json').read_bytes()
    (old / 'abandoned.json').write_text(json.dumps(dict(reason='Offline completed-round fixture')))
    current = loops.read(str(directory))
    loops.append(str(directory), 0, dict(current, phase='recapture'), dict(kind='offline-recapture-fixture'))
    for changed in ([dict(entries[0], id='different')] + entries[1:],
                    [dict(entries[0], path=str(other))] + entries[1:], [],
                    entries + [dict(entries[0], id='additional')]):
        declaration.write_text(json.dumps(changed))
        refused = review('recapture', str(directory), '--expected-generation', '1',
                         '--' + name + '-file', str(declaration), cwd=tmp_path)
        assert refused.exit_code == 8, refused
        assert refused.envelope['error']['code'] == 'scope-not-authorized'
        assert loops.read(str(directory))['generation'] == 1
    declaration.write_text(json.dumps(entries))
    # when supplying the same file, then normalized identity retains the exact set without rewriting the old round
    captured = review('recapture', str(directory), '--expected-generation', '1',
                     '--' + name + '-file', str(declaration), cwd=tmp_path)
    assert captured.exit_code == 0, captured
    target = json.loads((state_root / captured.envelope['payload']['round'] / 'target.json').read_text())
    assert target[name] == json.loads(before)[name]
    assert (old / 'target.json').read_bytes() == before


@pytest.mark.parametrize('intervening_failure', [False, True, 'older-evidence-loss', 'older-log-loss', 'global-policy-change', 'current-snapshot-loss', 'older-snapshot-loss', 'current-target-rewrite', 'older-target-rewrite', 'aliased-state-root'])
def should_converge_only_with_uninterrupted_qualification(
        review, plan_file_factory, state_root, codex_reply, codex_argv, tmp_path, intervening_failure, monkeypatch):
    import hashlib
    import uuid
    # given the user-selected two-xhigh-plus-max policy
    if intervening_failure == 'aliased-state-root':
        real_state = tmp_path / 'real-state'
        real_state.mkdir()
        state_root.symlink_to(real_state, target_is_directory=True)
    if intervening_failure == 'global-policy-change':
        home = tmp_path / 'execution-home'
        home.mkdir()
        instructions = home / 'AGENTS.md'
        instructions.write_text('Original global instructions\n')
        monkeypatch.setenv('CODEX_HOME', str(home))
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'inspection')
    loop_id = prepared.envelope['job_id']
    loop_dir = state_root / loop_id
    def status():
        return review('status', loop_id).envelope['payload']
    qualify_cold(state_root, state_root / status()['round'])
    codex_reply({'verdict': 'approve', 'findings': [], 'declared_scope': ['A0001'], 'verified_claims': []})
    efforts = []
    # when three independent cold rounds complete the whole lifecycle
    rounds = 5 if isinstance(intervening_failure, str) and intervening_failure != 'aliased-state-root' else 4 if intervening_failure is True else 3
    old_evidence = None
    old_log = None
    for n in range(rounds):
        current = status()
        round_id = current['round']
        job = state_root / round_id
        if intervening_failure in (True, 'older-evidence-loss') and n == 1:
            from codex_review import qualification
            selected = current['required_qualifications'][0]
            record = json.loads((state_root / 'qualifications' / (selected['digest'] + '.json')).read_text())
            evidence_job = state_root / record.pop('evidence_job')
            record.update(supersedes=selected['digest'])
            if intervening_failure == 'older-evidence-loss':
                from codex_review import evidence
                old_evidence = evidence_job / 'evidence' / record['controls'][0]['output_evidence_id'] / 'content'
                slot = 'staging/independent-replacement-control'
                (evidence_job / slot).write_text('independent passing control fixture')
                record['controls'][0]['output_evidence_id'] = evidence.seal(str(evidence_job), evidence_job.name, slot)['evidence_id']
            else:
                record['controls'][0]['outcome'] = 'fail'
            qualification.record(str(state_root), str(evidence_job), record)
            qualify_cold(state_root, job)
        if intervening_failure == 'older-evidence-loss' and n == 2:
            old_evidence.write_text('lost earlier control evidence')
            assert qualification.lookup(str(state_root), current['required_qualifications'][0]['key'])['valid']
        if intervening_failure == 'older-log-loss' and n == 2:
            old_log.unlink()
        log = tmp_path / ('events-' + str(n))
        log.write_text(json.dumps({'type': 'thread.started', 'thread_id': str(uuid.uuid4())}) + '\n{"type":"turn.completed"}\n')
        ran = review('run', loop_id, '--round', round_id, '--expected-generation', str(current['generation']),
                     fake={'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log, 'CODEX_FAKE_ARGV': codex_argv})
        assert ran.exit_code == 0, ran
        if n == 0:
            old_log = Path(status()['latest_attempt']['attempt_dir']) / 'stdout.jsonl'
        efforts.append(next(v for v in json.loads(codex_argv.read_text()) if v.startswith('model_reasoning_effort=')))
        report_sha = hashlib.sha256((job / 'report.json').read_bytes()).hexdigest()
        decision = dict(kind='read-assessment', round=round_id, disposition='no-observed-breach',
                        rationale='Complete fixture log inspected; no command events.', event_lines=[],
                        report_sha256=report_sha, log_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),
                        previous_assessment_sha256=None, checkpoint_sha256=status()['checkpoint_sha256'])
        (loop_dir / 'staging').mkdir(exist_ok=True)
        (loop_dir / 'staging' / 'decision.json').write_text(json.dumps(decision))
        assert review('continue', loop_id, '--expected-generation', str(status()['generation']),
                      '--decision-file', 'staging/decision.json').exit_code == 0
        for verb, name in [('record-probes', 'probes'), ('record-apply', 'apply'), ('record-verification', 'verification')]:
            if name == 'probes':
                payload = dict(report_sha256=report_sha, annotations=[])
            elif name == 'apply':
                payload = dict(report_sha256=report_sha, probes_sha256=hashlib.sha256((job / 'probes.json').read_bytes()).hexdigest(), results=[])
            else:
                payload = dict(apply_sha256=hashlib.sha256((job / 'apply.json').read_bytes()).hexdigest(), verification={'result': 'not-run-explicitly'})
            (job / 'staging' / (name + '.json')).write_text(json.dumps(payload))
            assert review(verb, loop_id, '--round', round_id, '--expected-generation', str(status()['generation']),
                          '--' + name + '-file', 'staging/' + name + '.json').exit_code == 0
        if intervening_failure in ('current-snapshot-loss', 'older-snapshot-loss', 'current-target-rewrite', 'older-target-rewrite') and n == 2:
            from codex_review import loops
            retained = loops.read(str(loop_dir))
            damaged = job if intervening_failure.startswith('current') else state_root / retained['rounds'][0]
            if intervening_failure.endswith('snapshot-loss'):
                (damaged / 'artifacts/A0001/after').unlink()
            else:
                target_path = damaged / 'target.json'
                target = json.loads(target_path.read_text())
                target['retained_files'] = {}
                target_path.write_text(json.dumps(target))
            # when the final count loses current or prior frozen evidence, then no convergence survives
            finished = review('continue', loop_id, '--expected-generation', str(status()['generation']))
            retained = loops.read(str(loop_dir))
            assert not retained['counting']['converged'], finished
            assert retained['counting']['streak'] == 0, finished
            assert retained['phase'] != 'closed'
            return
        if intervening_failure == 'global-policy-change' and n == 2:
            # A new global policy cannot complete the streak earned under its predecessor.
            instructions.write_text('Changed global instructions\n')
            finished = review('continue', loop_id, '--expected-generation', str(status()['generation']))
            assert finished.exit_code == 18, finished
            assert status()['clean_streak']['streak'] == 0
            assert not status()['clean_streak']['converged']
            assert status()['pause']['kind'] == 'qualification-key-changed'
            return
        assert review('continue', loop_id, '--expected-generation', str(status()['generation'])).exit_code == 0
        if n < rounds - 1:
            assert not status()['clean_streak']['converged']
            result = review('recapture', loop_id, '--expected-generation', str(status()['generation']))
            assert result.exit_code == 0, result
    # then the confirmation is max, the loop closes and every round remains retained
    expected = (['xhigh', 'xhigh', 'max', 'max', 'max'] if isinstance(intervening_failure, str) and intervening_failure != 'aliased-state-root'
                else ['xhigh'] * (rounds - 1) + ['max'])
    assert efforts == ['model_reasoning_effort=' + effort for effort in expected]
    assert status()['stage'] == 'closed'
    assert status()['clean_streak']['converged']
    assert status()['budget']['judgments'] == rounds
    assert review('status').envelope['payload']['open_jobs'] == []


@pytest.mark.parametrize('limit', ['max-artifact', 'max-bundle'])
def should_inherit_explicit_capture_limits_on_recapture(
        review, plan_file_factory, state_root, monkeypatch, limit):
    from codex_review import context, loop_driver, loops
    # given a size-limited closed round and an independently authorized larger candidate
    plan = plan_file_factory('a' * 50)
    prepared = review('prepare', 'plan', str(plan), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'capture limit control', '--' + limit, '100')
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    plan.write_text('a' * 120)
    (job / 'abandoned.json').write_text('{}')
    monkeypatch.setattr(context, 'applicable', lambda target: True)
    monkeypatch.setenv('CODEX_REVIEW_STATE', str(state_root))
    current = loops.read(str(directory))
    loops.append(str(directory), 0, dict(current, phase='recapture'), dict(kind='offline-recapture-fixture'))
    # when capturing the next round, then the original limit still refuses its publication
    result = loop_driver.recapture(str(directory), {}, loops.read(str(directory)))
    assert result['exit'] == 6, result
    assert result['payload']['round'] == job.name
    assert result['payload']['pause']['kind'] == 'capture-refused'


@pytest.mark.parametrize('terminal_member', [False, True])
def should_preserve_interactive_denial_on_loop_abandonment(
        review, plan_file_factory, state_root, terminal_member):
    from codex_review import loops
    # given an explicitly declined loop, possibly with an already terminal member
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'denial control')
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    if terminal_member:
        (job / 'response.json').write_text('{}')
        (job / 'run-error.json').write_text('{}')
    # when the host records the user's interactive denial
    result = review('abandon', str(directory), '--expected-generation', '0',
                    '--reason', 'User declined continuation', '--denied-interactively')
    # then loop and any newly closed member retain that provenance
    assert result.exit_code == 0, result
    decision = json.loads((directory / 'transitions' / '00000001.json').read_text())['decision']
    assert decision['denial_kind'] == 'interactive'
    assert decision['asserted_by'] == 'user'
    if not terminal_member:
        record = json.loads((job / 'abandoned.json').read_text())
        assert record['denial_kind'] == 'interactive'
        assert record['asserted_by'] == 'user'
