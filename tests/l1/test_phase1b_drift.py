"""Recapture adopts only an explicitly bound candidate transition."""
import json
import subprocess

import pytest

from codex_review import apply, evidence, images, loops


@pytest.mark.parametrize('recovered', [False, True])
def should_pause_and_preserve_findings_when_candidate_moves_during_review(
        review, plan_file_factory, state_root, codex_reply, fake_codex, recovered):
    from test_phase1b_loop_interface import qualify_cold
    from test_record_probes import RESPONSE
    # given a reviewer that returns a finding after the candidate changes
    plan = plan_file_factory()
    prepared = review('prepare', 'plan', str(plan), '--no-repo', '--loop', '--no-verify', '--reason', 'drift control')
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    qualify_cold(state_root, job)
    fake_codex.write_text(fake_codex.read_text().replace('import json, os, sys',
        'import json, os, sys\nfrom pathlib import Path\nPath(' + repr(str(plan)) + ').write_text("changed during review\\n")'))
    codex_reply(RESPONSE)
    result = review('run', str(directory), '--round', job.name, '--expected-generation', '0',
                    fake={'CODEX_FAKE_RESPONSE': codex_reply.path})
    assert result.exit_code == 4, result
    if recovered:
        # interruption after job publication, before the loop's completion transition
        (directory / 'transitions' / '00000002.json').unlink()
        pending = review('status', str(directory)).envelope['payload']
        decision = dict(kind='resolve-call', attempt=pending['latest_attempt']['id'],
                        checkpoint_sha256=pending['checkpoint_sha256'], rationale='Recover the persisted review result')
        (directory / 'staging').mkdir(exist_ok=True)
        (directory / 'staging' / 'resolve.json').write_text(json.dumps(decision))
        result = review('continue', str(directory), '--expected-generation', str(pending['generation']),
                        '--decision-file', 'staging/resolve.json')
        assert result.exit_code == 0, result
    # then normal completion and recovery both require resolution and retain unprobed findings
    status = review('status', str(directory)).envelope['payload']
    assert status['pause'] == dict(kind='candidate-drift')
    assert status['budget']['judgments'] == 1
    assert status['pending_findings'][0]['reference'] == dict(round=job.name, local_id='F-1')
    refused = review('recapture', str(directory), '--expected-generation', str(status['generation']))
    assert refused.exit_code != 0
    assert loops.read(str(directory))['rounds'] == [job.name]


def git(repo, *args):
    return subprocess.check_output(['git', *args], cwd=str(repo)).decode().strip()


def should_refuse_unexplained_drift_after_an_invalid_response(
        review, plan_file_factory, state_root, codex_reply):
    from test_phase1b_loop_interface import qualify_cold
    # given a terminal malformed response with no verification record
    plan = plan_file_factory()
    prepared = review('prepare', 'plan', str(plan), '--no-repo', '--loop', '--no-verify', '--reason', 'invalid response control')
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    qualify_cold(state_root, job)
    codex_reply('{malformed')
    assert review('run', str(directory), '--round', job.name, '--expected-generation', '0',
                  fake={'CODEX_FAKE_RESPONSE': codex_reply.path}).exit_code == 8
    plan.write_text('external change after failed review\n')
    # when recapturing without attribution, then the missing verification cannot bypass drift resolution
    status = review('status', str(directory)).envelope['payload']
    result = review('recapture', str(directory), '--expected-generation', str(status['generation']))
    assert result.exit_code == 18, result
    assert result.envelope['error']['details']['reason'] == 'candidate-drift'


@pytest.fixture
def committed_candidate(review, make_repo, state_root):
    repo = make_repo({'a.py': 'old\n'})
    original_head = git(repo, 'rev-parse', 'HEAD')
    (repo / 'a.py').write_text('candidate\n')
    result = review('prepare', 'code', '--loop', '--base', original_head, '--head', original_head,
                    '--no-verify', '--reason', 'offline transition fixture', cwd=repo)
    assert result.exit_code == 0, result
    directory = state_root / result.envelope['job_id']
    job = state_root / result.envelope['payload']['round']
    git(repo, 'add', 'a.py')
    git(repo, 'commit', '-qm', 'authorized candidate commit')
    target = json.loads((job / 'target.json').read_text())
    (job / 'staging').mkdir(exist_ok=True)
    (job / 'staging' / 'transition.txt').write_text('The authorized commit contains the candidate, with no extra paths')
    citation = evidence.seal(str(job), job.name, 'staging/transition.txt')['evidence_id']
    return repo, directory, job, target, original_head, citation


def decide_transition(review, fixture, **overrides):
    repo, directory, job, target, original_head, citation = fixture
    status = review('status', str(directory)).envelope['payload']
    payload = dict(kind='authorized-commit', checkpoint_sha256=status['checkpoint_sha256'], round=job.name,
                   target_sha256=images.sha256_file(str(job / 'target.json')),
                   candidate_sha256=apply.identity_sha(target), old_head=original_head, new_head=git(repo, 'rev-parse', 'HEAD'),
                   authority='User authorized committing this candidate', rationale='Commit transition checked against sealed scope evidence',
                   evidence_ids=[citation])
    payload.update(overrides)
    (directory / 'staging').mkdir(exist_ok=True)
    (directory / 'staging' / 'decision.json').write_text(json.dumps(payload))
    return review('continue', str(directory), '--expected-generation', str(status['generation']),
                  '--decision-file', 'staging/decision.json')


def should_recapture_an_authorized_commit_without_repinning_to_later_changes(review, committed_candidate):
    # given an explicit original head pin and a separately authorized commit
    repo, directory, job, target, _, _ = committed_candidate
    original = (job / 'target.json').read_bytes()
    # when recording that exact candidate transition and recapturing
    authorized = decide_transition(review, committed_candidate)
    assert authorized.exit_code == 0, authorized
    result = review('recapture', str(directory), '--expected-generation', str(authorized.envelope['payload']['generation']))
    # then the original record remains immutable, the new pin matches the authorized commit, and credit resets
    assert result.exit_code == 0, result
    new_job = directory.parent / result.envelope['payload']['round']
    captured = json.loads((new_job / 'target.json').read_text())
    assert captured['repo_identity']['head_oid'] == git(repo, 'rev-parse', 'HEAD')
    assert captured['base_ref'] == target['base_ref']
    assert result.envelope['payload']['clean_streak']['streak'] == 0
    assert (job / 'target.json').read_bytes() == original


def should_refuse_movement_after_recording_candidate_authority(review, committed_candidate):
    # given authority bound to one observed candidate
    repo, directory, job, _, _, _ = committed_candidate
    authorized = decide_transition(review, committed_candidate)
    assert authorized.exit_code == 0, authorized
    (repo / 'a.py').write_text('later unapproved bytes\n')
    # when recapturing, then authority does not silently follow the moving checkout
    result = review('recapture', str(directory), '--expected-generation', str(authorized.envelope['payload']['generation']))
    assert result.exit_code == 18, result
    assert result.envelope['error']['details']['reason'] == 'candidate-drift'
    assert loops.read(str(directory))['current_round'] == job.name


@pytest.mark.parametrize('changed_context', [False, True])
def should_retain_credit_only_for_a_proven_equivalent_base_rebuild(review, make_repo, state_root, changed_context):
    from codex_review import counting
    # given a frozen uncommitted candidate and one prior clean credit
    repo = make_repo({'a.py': 'old\n'})
    head = git(repo, 'rev-parse', 'HEAD')
    (repo / 'a.py').write_text('candidate\n')
    prepared = review('prepare', 'code', '--loop', '--base', head, '--head', head,
                      '--no-verify', '--reason', 'base rebuild control', cwd=repo)
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    current = loops.read(str(directory))
    one_clean = counting.round_finished(current['counting'], effort='xhigh', eligible=True, accepted=0, blockers=False)
    loops.append(str(directory), 0, dict(current, counting=one_clean), dict(kind='offline-credit-fixture'))
    tree = git(repo, 'rev-parse', 'HEAD^{tree}')
    rebuilt = git(repo, 'commit-tree', tree, '-m', 'new metadata, same tree')
    if changed_context:
        (repo / 'README.md').write_text('unrelated context changed\n')
    target = json.loads((job / 'target.json').read_text())
    (job / 'staging').mkdir(exist_ok=True)
    (job / 'staging' / 'evidence.txt').write_text('Original and rebuilt comparison trees were inspected')
    citation = evidence.seal(str(job), job.name, 'staging/evidence.txt')['evidence_id']
    status = review('status', str(directory)).envelope['payload']
    decision = dict(kind='drift-resolution', round=job.name, checkpoint_sha256=status['checkpoint_sha256'],
                    target_sha256=images.sha256_file(str(job / 'target.json')), candidate_sha256=apply.identity_sha(target),
                    old_head=head, new_head=head, new_base=rebuilt, equivalence='matching',
                    authority='Existing target scope remains authorized', evidence_ids=[citation],
                    rationale='Request matching-input recapture; driver must independently compare')
    (directory / 'staging').mkdir(exist_ok=True)
    (directory / 'staging' / 'decision.json').write_text(json.dumps(decision))
    resolved = review('continue', str(directory), '--expected-generation', str(status['generation']),
                      '--decision-file', 'staging/decision.json')
    assert resolved.exit_code == 0, resolved
    # when recapturing, then only equal content/context retains credit despite a different base OID
    result = review('recapture', str(directory), '--expected-generation', str(resolved.envelope['payload']['generation']))
    if changed_context:
        assert result.exit_code == 8, result
        assert result.envelope['error']['code'] == 'equivalence-mismatch'
        assert len(loops.read(str(directory))['rounds']) == 1
        paused = loops.read(str(directory))
        assert paused['pause']['kind'] == 'equivalence-mismatch'
        assert paused['counting']['streak'] == 0
    else:
        assert result.exit_code == 0, result
        assert result.envelope['payload']['clean_streak']['streak'] == 1
        recaptured = json.loads((state_root / result.envelope['payload']['round'] / 'target.json').read_text())
        assert recaptured['base_ref'] == rebuilt and rebuilt != head


@pytest.mark.parametrize('changed_after_authority', [False, True])
def should_bind_an_authorized_companion_set_before_recapture(
        review, committed_candidate, tmp_path, changed_after_authority):
    # given scope authority for a governing file outside the prior capture
    repo, directory, job, _, _, _ = committed_candidate
    companion = tmp_path / 'contract.md'
    companion.write_text('governing rule\n')
    selected = decide_transition(review, committed_candidate,
                                 scope=dict(companions=[dict(id='contract', path=str(companion))]),
                                 scope_authority='User requested inclusion of this governing contract')
    assert selected.exit_code == 0, selected
    if changed_after_authority:
        companion.write_text('later unapproved rule\n')
    # when recapturing, then the newly declared set is frozen and cannot silently follow later edits
    result = review('recapture', str(directory), '--expected-generation', str(selected.envelope['payload']['generation']))
    if changed_after_authority:
        assert result.exit_code == 18, result
        assert result.envelope['error']['details']['reason'] == 'candidate-drift'
        assert loops.read(str(directory))['current_round'] == job.name
    else:
        assert result.exit_code == 0, result
        target = json.loads((directory.parent / result.envelope['payload']['round'] / 'target.json').read_text())
        assert [(c['id'], c['image']['content_identity']) for c in target['companions']] == [
            ('contract', 'sha256:' + images.sha256_file(str(companion)))]
        assert result.envelope['payload']['clean_streak']['streak'] == 0


@pytest.mark.parametrize('change', ['home', 'global-instructions'])
def should_require_authorized_recapture_for_qualified_changed_launch_settings(
        review, plan_file_factory, state_root, tmp_path, monkeypatch, codex_reply, change):
    from test_phase1b_loop_interface import qualify_cold
    # given an initial capture bound to one execution home and its instructions
    home = tmp_path / 'execution-home'
    home.mkdir()
    (home / 'AGENTS.md').write_text('Original instructions\n')
    monkeypatch.setenv('CODEX_HOME', str(home))
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'home transition control')
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    original = (job / 'target.json').read_bytes()
    qualify_cold(state_root, job)
    assert review('run', str(directory), '--round', job.name, '--expected-generation', '0').exit_code == 7
    generation = review('status', str(directory)).envelope['payload']['generation']
    if change == 'home':
        monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'other-home'))
    else:
        (home / 'AGENTS.md').write_text('Changed instructions\n')
    qualify_cold(state_root, job)
    codex_reply(dict(verdict='approve', findings=[], declared_scope=['A0001'], verified_claims=[]))
    marker = tmp_path / 'launched.json'
    # when the new home is qualified, that alone does not authorize replacing the captured configuration
    refused = review('run', str(directory), '--round', job.name, '--expected-generation', str(generation),
                     fake={'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_ARGV': marker})
    assert refused.exit_code == 18, refused
    assert not marker.exists()
    current = review('status', str(directory)).envelope['payload']
    assert current['pause']['kind'] == 'qualification-key-changed'
    assert current['resolver_kinds'] == ['drift-resolution']
    (job / 'staging' / 'transition.txt').write_text('The user authorized this tested execution home')
    citation = evidence.seal(str(job), job.name, 'staging/transition.txt')['evidence_id']
    decision = dict(kind='drift-resolution', round=job.name, checkpoint_sha256=current['checkpoint_sha256'],
                    target_sha256=images.sha256_file(str(job / 'target.json')),
                    candidate_sha256=apply.identity_sha(json.loads(original)), old_head=None, new_head=None,
                    authority='User authorized the configured home change', evidence_ids=[citation],
                    launch_key=current['required_qualifications'][0]['key'], rationale='The new home has matching controls')
    (directory / 'staging').mkdir(exist_ok=True)
    (directory / 'staging' / 'decision.json').write_text(json.dumps(decision))
    assert review('continue', str(directory), '--expected-generation', str(current['generation']),
                  '--decision-file', 'staging/decision.json').exit_code == 0
    current = review('status', str(directory)).envelope['payload']
    captured = review('recapture', str(directory), '--expected-generation', str(current['generation']))
    assert captured.exit_code == 0, captured
    current = captured.envelope['payload']
    # then a new immutable round binds the authorized settings and can launch without inheriting credit
    launched = review('run', str(directory), '--round', current['round'], '--expected-generation', str(current['generation']),
                      fake={'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_ARGV': marker})
    assert launched.exit_code == 0, launched
    assert marker.exists()
    assert (job / 'target.json').read_bytes() == original
