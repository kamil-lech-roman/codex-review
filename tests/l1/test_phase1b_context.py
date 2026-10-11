"""Captured governing companions preserve identity, bytes and portable IDs."""
import json
from pathlib import Path

import pytest


def prepare(review, plan, declarations, **kwargs):
    return review('prepare', 'plan', str(plan), '--no-verify', '--reason', 'inspection',
                  '--companions-file', str(declarations), **kwargs)


@pytest.mark.parametrize('kind', ['companion', 'dependency'])
def should_normalize_declared_identity_before_reading_context_bytes(tmp_path, monkeypatch, kind):
    from codex_review import context, images
    # given relative locators, reversed IDs and a final symlink with distinct capture semantics
    source = tmp_path / 'source.txt'
    source.write_text('governing bytes\n')
    link = tmp_path / 'alias.txt'
    link.symlink_to(source)
    declaration = tmp_path / 'declarations.json'
    declaration.write_text(json.dumps([dict(id='z', path='alias.txt'), dict(id='a', path='source.txt')]))
    def unexpected_read(path):
        pytest.fail('Scope normalization must precede reading newly supplied context bytes')
    monkeypatch.setattr(images, 'image_of', unexpected_read)
    # when normalizing scope, then defaults/order resolve without erasing dependency symlink identity
    normalized = getattr(context, kind + '_declarations')(str(declaration), str(tmp_path))
    assert [e['id'] for e in normalized] == ['a', 'z']
    assert normalized[0]['path'] == str(source)
    assert normalized[1]['path'] == str(source if kind == 'companion' else link)
    if kind == 'dependency':
        assert all(e['exclude'] == [] for e in normalized)


def should_freeze_companion_bytes_and_portable_identity(review, git_repo, tmp_path):
    # given a governing contract identified independently of its absolute location
    contract = git_repo / 'contract.md'
    contract.write_text('rules v1\n')
    declarations = tmp_path / 'companions.json'
    declarations.write_text(json.dumps([{'id': 'contract', 'path': 'contract.md'}]))
    # when capture publishes
    result = prepare(review, git_repo / 'README.md', declarations, cwd=git_repo)
    # then bytes and readable locators survive subsequent source edits
    assert result.exit_code == 0, result
    job = Path(result.envelope['job_dir'])
    target = json.loads((job / 'target.json').read_text())
    item = target['companions'][0]
    assert item['id'] == 'contract'
    assert item['path'] == str(contract)
    assert item['repo_relative_path'] == 'contract.md'
    frozen = job / item['content_path']
    assert frozen.read_bytes() == b'rules v1\n'
    contract.write_text('rules v2\n')
    assert frozen.read_bytes() == b'rules v1\n'


@pytest.mark.parametrize('entries,code,exit_code', [
    (['contract.md'], 'bad-companions', 2),
    ([{'id': 'c', 'path': 'contract.md'}, {'id': 'c', 'path': 'contract.md'}], 'bad-companions', 2),
    ([{'id': 'c', 'path': 'absent.md'}], 'companion-unreadable', 3),
    ([{'id': 'c', 'path': '/tmp/rule\x00.md'}], 'bad-companions', 2),
    ([{'id': 'c', 'path': 'rule\x00.md'}], 'bad-companions', 2),
    ([{'id': 'c', 'path': '/tmp/rule\ud800.md'}], 'bad-companions', 2),
])
def should_refuse_invalid_companions_before_publication(review, git_repo, tmp_path, entries, code, exit_code):
    # given a malformed or unreadable declaration
    declarations = tmp_path / 'companions.json'
    declarations.write_text(json.dumps(entries))
    # when preparing
    result = prepare(review, git_repo / 'README.md', declarations, cwd=git_repo)
    # then no incomplete job is published
    assert result.exit_code == exit_code, result
    assert result.envelope['error']['code'] == code
    assert result.envelope['job_id'] is None


@pytest.mark.parametrize('path', ['/tmp/runtime\x00/module.py', 'runtime\x00/module.py', '/tmp/runtime\ud800/module.py'])
def should_refuse_invalid_dependency_paths_with_an_argument_envelope(review, git_repo, tmp_path, path):
    # given a JSON string that cannot name a filesystem path
    declaration = tmp_path / 'dependencies.json'
    declaration.write_text(json.dumps([dict(id='runtime', path=path)]))
    # when preparing, then reject the declaration before filesystem resolution or job publication
    result = review('prepare', 'plan', str(git_repo / 'README.md'), '--no-verify', '--reason',
                    'inspection', '--dependencies-file', str(declaration), cwd=git_repo)
    assert result.exit_code == 2, result
    assert result.envelope['error']['code'] == 'bad-dependencies'
    assert result.envelope['job_id'] is None


def should_stop_before_reviewer_launch_when_companion_changed(review, git_repo, tmp_path, codex_reply):
    # given a prepared review whose governing contract subsequently changes
    contract = git_repo / 'contract.md'
    contract.write_text('before\n')
    declarations = tmp_path / 'companions.json'
    declarations.write_text(json.dumps([{'id': 'contract', 'path': 'contract.md'}]))
    prepared = prepare(review, git_repo / 'README.md', declarations, cwd=git_repo)
    contract.write_text('after\n')
    launched = tmp_path / 'argv.json'
    codex_reply({'verdict': 'approve', 'findings': [], 'declared_scope': ['A0001'], 'verified_claims': []})
    # when run would use that stale review context
    result = review('run', prepared.envelope['job_id'], fake={
        'CODEX_FAKE_ARGV': launched, 'CODEX_FAKE_RESPONSE': codex_reply.path})
    # then stop before paying for a review whose applicability is already false
    assert result.exit_code == 4, result
    assert not launched.exists()


def should_stop_stale_findings_before_probe_recording(review, git_repo, codex_reply):
    from test_record_probes import RESPONSE, accepted, submit
    # given a reviewed plan and its captured repository context
    plan = git_repo / 'README.md'
    prepared = review('prepare', 'plan', str(plan), '--no-verify', '--reason', 'inspection', cwd=git_repo)
    job = Path(prepared.envelope['job_dir'])
    codex_reply(RESPONSE)
    assert review('run', str(job), fake={'CODEX_FAKE_RESPONSE': codex_reply.path}).exit_code == 0
    annotation = accepted(review, job.name, job, plan)
    # when unrelated context changes before a stale finding is accepted
    (git_repo / 'new-context.py').write_text('changed = True\n')
    result = submit(review, job.name, job, [annotation])
    # then do not adopt the new bytes as a pre-apply baseline
    assert result.exit_code == 4, result
    assert not (job / 'probes.json').exists()


def should_bind_ignored_dependency_bytes_and_detect_source_drift(review, git_repo, tmp_path):
    # given a declared package root ignored by Git
    (git_repo / '.gitignore').write_text('venv/\n')
    package = git_repo / 'venv'
    package.mkdir()
    (package / 'module.py').write_text('VALUE = 1\n')
    declaration = tmp_path / 'dependencies.json'
    declaration.write_text(json.dumps([{'id': 'runtime', 'path': 'venv'}]))
    # when captured, then included bytes remain retrievable independently of the source
    result = review('prepare', 'plan', str(git_repo / 'README.md'), '--no-verify', '--reason',
                    'inspection', '--dependencies-file', str(declaration), cwd=git_repo)
    assert result.exit_code == 0, result
    job = Path(result.envelope['job_dir'])
    target = json.loads((job / 'target.json').read_text())
    entry = target['dependencies'][0]['manifest']['module.py']
    assert (job / entry['content_path']).read_text() == 'VALUE = 1\n'
    (package / 'module.py').write_text('VALUE = 2\n')
    refusal = review('run', str(job))
    assert refusal.exit_code == 4, refusal


@pytest.mark.parametrize('exclusion', [['*.pyc'], ['../outside'], ['/absolute'], ['cache']])
def should_refuse_malformed_or_unqualified_dependency_exclusions(review, git_repo, tmp_path, exclusion):
    # given a directory exclusion without passing qualification
    declaration = tmp_path / 'dependencies.json'
    declaration.write_text(json.dumps([{'id': 'runtime', 'path': '.', 'exclude': exclusion}]))
    # when preparing, then an exclusion cannot silently remove bound bytes
    result = review('prepare', 'plan', str(git_repo / 'README.md'), '--no-verify', '--reason',
                    'inspection', '--dependencies-file', str(declaration), cwd=git_repo)
    assert result.exit_code == 2, result
    assert result.envelope['error']['code'] == 'bad-dependencies'


def should_refuse_verification_after_an_external_companion_changes(review, plan_file_factory, outside_any_repo, codex_reply, tmp_path):
    from test_record_apply import probe_a_job, submit_apply
    # given a correctly applied plan fix with a bound external authority
    authority = tmp_path / 'authority.md'
    authority.write_text('required rule\n')
    declaration = tmp_path / 'companions.json'
    declaration.write_text(json.dumps([dict(id='authority', path=str(authority))]))
    job_id, job, plan = probe_a_job(review, plan_file_factory, outside_any_repo, codex_reply,
                                  '--companions-file', str(declaration))
    plan.write_text('fixed plan\n')
    assert submit_apply(review, job_id, job, [dict(finding_id='F-1', result='applied')]).exit_code == 0
    # when authority changes after apply was bound
    authority.write_text('different rule\n')
    import hashlib
    payload = dict(apply_sha256=hashlib.sha256((job / 'apply.json').read_bytes()).hexdigest(),
                   verification=dict(result='not-run-explicitly'))
    (job / 'staging' / 'verification.json').write_text(json.dumps(payload))
    result = review('record-verification', job_id, '--verification-file', 'staging/verification.json')
    # then stale verification cannot close the changed contract
    assert result.exit_code == 4, result
    assert not (job / 'verification.json').exists()


def should_detect_out_of_scope_repository_edits_during_plan_apply(review, git_repo, codex_reply):
    from test_record_probes import RESPONSE, accepted, submit
    from test_record_apply import submit_apply
    # given accepted plan-only edits in a repository context
    plan = git_repo / 'README.md'
    prepared = review('prepare', 'plan', str(plan), '--no-verify', '--reason', 'inspection', cwd=git_repo)
    job = Path(prepared.envelope['job_dir'])
    codex_reply(RESPONSE)
    assert review('run', job.name, fake={'CODEX_FAKE_RESPONSE': codex_reply.path}).exit_code == 0
    assert submit(review, job.name, job, [accepted(review, job.name, job, plan)]).exit_code == 0
    # when the authorized plan edit is accompanied by an unrelated source edit
    plan.write_text('fixed plan\n')
    extra = git_repo / 'unrelated.py'
    extra.write_text('unauthorized = True\n')
    result = submit_apply(review, job.name, job, [dict(finding_id='F-1', result='applied')])
    # then the full repository baseline exposes the scope violation
    assert result.exit_code == 10, result
    assert str(extra) in result.envelope['payload']['violating_paths']


def should_require_persistent_exclusion_controls_again_before_loop_launch(
        review, plan_file_factory, state_root, tmp_path, codex_reply):
    from codex_review import qualification
    from qualification_fixture import qualify_fixture
    from test_phase1b_loop_interface import qualify_cold
    # given an excluded cache subtree and a separate qualification-evidence job
    plan = plan_file_factory()
    bootstrap = review('prepare', 'plan', str(plan), '--no-repo', '--no-verify', '--reason', 'offline control records')
    evidence_job = state_root / bootstrap.envelope['job_id']
    runtime = tmp_path / 'runtime'
    (runtime / 'cache').mkdir(parents=True)
    (runtime / 'module.py').write_text('value = 1\n')
    (runtime / 'cache' / 'generated.bin').write_bytes(b'cache')
    declarations = tmp_path / 'dependencies.json'
    declarations.write_text(json.dumps([dict(id='runtime', path=str(runtime), exclude=['cache'])]))
    args = ('prepare', 'plan', str(plan), '--no-repo', '--loop', '--no-verify', '--reason', 'exclusion control',
            '--dependencies-file', str(declarations))
    refused = review(*args)
    assert refused.exit_code == 2
    required = refused.envelope['error']['details']['qualification']
    assert required['key']['settings']['launch_profile'] == 'persistent'
    qualified = qualify_fixture(state_root, evidence_job, required['key'])
    prepared = review(*args)
    assert prepared.exit_code == 0, prepared
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    qualify_cold(state_root, job)
    # when the relied-on exclusion controls fail after capture
    record = json.loads((state_root / 'qualifications' / (qualified + '.json')).read_text())
    record.pop('evidence_job')
    record.update(supersedes=qualified)
    record['controls'][0]['outcome'] = 'fail'
    qualification.record(str(state_root), str(evidence_job), record)
    marker = tmp_path / 'unqualified-launch'
    codex_reply(dict(verdict='approve', findings=[], declared_scope=['A0001'], verified_claims=[]))
    result = review('run', str(directory), '--round', job.name, '--expected-generation', '0',
                    fake={'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_ARGV': marker})
    # then preparing successfully never waives the dependent launch gate
    assert result.exit_code == 18, result
    assert result.envelope['error']['details']['reason'] == 'qualification-required'
    assert not marker.exists()
    status = review('status', str(directory)).envelope['payload']
    exclusions = [q for q in status['required_qualifications'] if q['key']['capability'] == 'cache-exclusion']
    assert len(exclusions) == 1 and not exclusions[0]['valid']


@pytest.mark.parametrize('mode', ['code', 'plan'])
@pytest.mark.parametrize('source', ['AGENTS.md', 'AGENTS.override.md'])
def should_bind_configured_global_instructions_to_capture_and_equivalence(
        review, git_repo, tmp_path, monkeypatch, mode, source):
    from codex_review import images, loop_driver, reviewer
    # given configured instructions separate from the default home and memory
    home = tmp_path / 'execution-home'
    home.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    instruction = home / source
    instruction.write_text('Original governing instructions\n')
    args = ['plan', str(git_repo / 'README.md')] if mode == 'plan' else ['code', '--uncommitted']
    (git_repo / 'README.md').write_text('Candidate change\n')
    def capture():
        result = review('prepare', *args, '--no-verify', '--reason', 'instruction control', cwd=git_repo)
        assert result.exit_code == 0, result
        return json.loads((Path(result.envelope['job_dir']) / 'target.json').read_text())
    before = capture()
    before_key = loop_driver.launch_key(before)
    before_hash = reviewer.effective_policy_hash('same-synthesized-policy')
    assert before['global_policy_sources'][source] == images.sha256_file(str(instruction))
    (home / 'memory.md').write_text('Memory is allowed and not an instruction-source identity')
    assert loop_driver.launch_key(before) == before_key
    # when the configured global source changes, both live qualification and captured equivalence change
    instruction.write_text('Changed governing instructions\n')
    after = capture()
    assert loop_driver.launch_key(before) != before_key
    assert loop_driver.launch_key(before, captured=True) == before_key
    assert loop_driver.equivalence(before) != loop_driver.equivalence(after)
    assert reviewer.effective_policy_hash('same-synthesized-policy') != before_hash
    # then deletion and an unreadable source remain distinguishable in the audit input
    instruction.unlink()
    assert reviewer.global_policy_sources()[source] == 'absent'
    instruction.mkdir()
    assert reviewer.global_policy_sources()[source] == 'unresolved'


@pytest.mark.parametrize('path,expected', [
    ('/repo/runtime/value.py', 'frozen'), ('/repo/runtime/deleted.py', 'absent'),
    ('/repo/runtime/empty/deleted.py', 'absent'), ('/repo/runtime/cache/missing.py', None),
    ('/repo/runtime/link/hidden.py', None), ('/repo/runtime2/value.py', None),
    ('/repo/runtime', None), ('/repo/one.py/child', None), ('/repo/one.py', 'frozen'),
    ('/repo/companion.py', 'frozen'),
])
@pytest.mark.parametrize('directory_entries', [False, True])
def should_distinguish_captured_bound_images_from_unbound_or_excluded_absence(monkeypatch, path, expected, directory_entries):
    from codex_review import context, images
    # given immutable declared images and a directory manifest with an excluded subtree and symlink
    frozen = dict(kind='regular', git_mode='100644', content_identity='frozen')
    target = dict(companions=[dict(path='/repo/companion.py', image=frozen)], dependencies=[
        dict(path='/repo/runtime', exclude=['cache'], manifest={
            'value.py': dict(image=frozen), 'link': dict(image=dict(kind='symlink', content_identity='link'))}),
        dict(path='/repo/one.py', exclude=[], manifest={'.': dict(image=frozen)})])
    if directory_entries:
        directory = dict(kind='directory', git_mode='040000', content_identity=None)
        target['dependencies'][0]['manifest'].update({'.': dict(image=directory), 'empty': dict(image=directory)})
        if path == '/repo/runtime':
            assert context.captured_bound_image(target, path) == directory
            return
    def live_read(_):
        raise AssertionError('Frozen image lookup must not sample the live filesystem')
    monkeypatch.setattr(images, 'image_of', live_read)
    # when deciding whether re-examination captured this input, then an unobserved path is not proof of absence
    result = context.captured_bound_image(target, path)
    if expected is None:
        assert result is None
    elif expected == 'absent':
        assert result == dict(kind='absent', git_mode=None, content_identity=None)
    else:
        assert result == frozen


@pytest.mark.parametrize('change', ['create', 'delete'])
def should_bind_empty_dependency_directories_through_applicability_and_equivalence(
        review, plan_file_factory, tmp_path, change):
    from codex_review import apply, context, execution, loop_driver
    # given a declared directory whose empty child controls runtime behavior
    root = tmp_path / 'runtime'
    root.mkdir()
    feature = root / 'feature_enabled'
    if change == 'delete':
        feature.mkdir()
    declaration = tmp_path / 'dependencies.json'
    declaration.write_text(json.dumps([dict(id='runtime', path=str(root))]))
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--no-verify',
                      '--reason', 'directory control', '--dependencies-file', str(declaration))
    assert prepared.exit_code == 0, prepared
    job = Path(prepared.envelope['job_dir'])
    target = json.loads((job / 'target.json').read_text())
    baseline, identity = apply.baseline(target), apply.identity_sha(target)
    # when only the empty directory changes, then every dependent identity observes it
    feature.rmdir() if change == 'delete' else feature.mkdir()
    assert not context.applicable(target)
    assert apply.identity_sha(target) != identity
    assert apply.changed_paths(target, baseline) == [str(feature)]
    roots, _ = context.dependencies(str(declaration), None, str(job.parent), 'one-pass')
    captured = dict(target, dependencies=roots)
    assert loop_driver.equivalence(captured) != loop_driver.equivalence(target)
    assert execution.key(captured) != execution.key(target)
    refused = review('run', job.name)
    assert refused.exit_code == 4, refused


def should_omit_only_explicitly_excluded_directory_structure(tmp_path):
    from codex_review import context
    # given a bound root with one explicit excluded subtree
    root = dict(id='runtime', path=str(tmp_path), exclude=['cache'])
    before, _ = context.dependency_manifest(root, [root])
    # when an excluded empty subtree appears, then its structure does not enter the manifest
    (tmp_path / 'cache/nested').mkdir(parents=True)
    after, _ = context.dependency_manifest(root, [root])
    assert after == before
    # a non-excluded empty child does change identity without inventing retained file bytes
    (tmp_path / 'feature').mkdir()
    changed, blobs = context.dependency_manifest(root, [root])
    assert set(changed) == {'.', 'feature'}
    assert changed['feature'] == dict(image=dict(kind='directory', git_mode='040000', content_identity=None),
                                      bytes=0, content_path=None)
    assert blobs == {}
