"""Authorized fixes may overlap governing inputs without admitting unrelated drift."""
import json
import subprocess
from pathlib import Path

import pytest

from codex_review import images
from test_record_probes import RESPONSE, accepted, submit
from test_record_apply import submit_apply


@pytest.mark.parametrize('mode', ['code', 'plan-repo', 'plan-only'])
@pytest.mark.parametrize('change', ['modify', 'create', 'delete'])
def should_measure_bound_inputs_in_the_apply_baseline(make_repo, mode, change):
    from codex_review import apply
    # given an ignored input directory and an unchanged plan/code target
    repo = make_repo({'P.md': '# plan\n', '.gitignore': 'runtime/\n'})
    runtime = repo / 'runtime'
    runtime.mkdir()
    existing = runtime / 'input.py'
    existing.write_text('VALUE = 1\n')
    target = dict(mode='code', repo_root=str(repo), capture_contract=2,
                  dependencies=[dict(id='runtime', path=str(runtime), exclude=[])])
    if mode != 'code':
        target.update(mode='plan', plan_identity=dict(plan_path=str(repo / 'P.md')),
                      context_repository=str(repo) if mode == 'plan-repo' else None)
    before = apply.baseline(target)
    assert apply.changed_paths(target, before) == []
    # when a bound ignored file moves, then the primitive returns its absolute path
    changed = runtime / 'new.py' if change == 'create' else existing
    if change == 'delete':
        changed.unlink()
    else:
        changed.write_text('VALUE = 2\n')
    assert apply.changed_paths(target, before) == [str(changed)]


@pytest.mark.parametrize('no_apply', [False, True])
@pytest.mark.parametrize('kind', ['companion', 'dependency-file', 'dependency-directory'])
def should_account_for_ignored_bound_edits(review, make_repo, codex_reply, tmp_path, kind, no_apply):
    # given an ignored runtime file explicitly bound into the review
    repo = make_repo({'f.py': 'VALUE = 0\n', '.gitignore': 'runtime/\n'})
    (repo / 'f.py').write_text('VALUE = 2\n')
    runtime = repo / 'runtime'
    runtime.mkdir()
    source = runtime / 'value.py'
    source.write_text('VALUE = 2\n')
    declarations = tmp_path / 'inputs.json'
    declarations.write_text(json.dumps([dict(id='runtime', path=str(runtime if kind == 'dependency-directory' else source))]))
    flag = '--companions-file' if kind == 'companion' else '--dependencies-file'
    options = ['--no-apply'] if no_apply else []
    prepared = review('prepare', 'code', '--uncommitted', '--no-verify', '--reason', 'inspection',
                      flag, str(declarations), *options, cwd=repo)
    assert prepared.exit_code == 0, prepared.envelope
    job = Path(prepared.envelope['job_dir'])
    codex_reply({k: v for k, v in RESPONSE.items() if k != 'verified_claims'})
    assert review('run', job.name, fake={'CODEX_FAKE_RESPONSE': codex_reply.path}).exit_code == 0
    annotation = accepted(review, job.name, job, source)
    annotation['fix_paths'] = ['runtime/value.py']
    assert submit(review, job.name, job, [annotation]).exit_code == 0
    # when the ignored input changes, then reconciliation sees it just like a tracked edit
    source.write_text('VALUE = 1\n')
    result = submit_apply(review, job.name, job, [dict(finding_id='F-1', result='applied')])
    if no_apply:
        assert result.exit_code == 8, result.envelope
        assert result.envelope['error']['code'] == 'no-apply-moved'
        assert not (job / 'apply.json').exists()
    else:
        assert result.exit_code == 0, result.envelope
        assert result.envelope['payload']['application_result'] == 'applied-complete'
        assert result.envelope['payload']['changed_paths'] == [str(source)]
        assert json.loads((job / 'apply.json').read_text())['post_apply_context']


@pytest.mark.parametrize('drift', [None, 'before-apply', 'before-verification'])
@pytest.mark.parametrize('kind', ['companion', 'dependency-file', 'dependency-directory'])
def should_verify_an_authorized_fix_overlapping_declared_context(
        review, make_repo, codex_reply, tmp_path, kind, drift):
    # given a reviewed file also bound as governing context
    repo = make_repo({'pkg/f.py': 'VALUE = 0\n', 'pkg/other.py': 'OTHER = 0\n'})
    source = repo / 'pkg/f.py'
    source.write_text('VALUE = 2\n')
    declarations = tmp_path / 'context.json'
    entries = [dict(id='governing', path=str(repo / 'pkg' if kind == 'dependency-directory' else source))]
    if kind != 'dependency-directory':
        entries.append(dict(id='other', path=str(repo / 'pkg/other.py')))
    declarations.write_text(json.dumps(entries))
    flag = '--companions-file' if kind == 'companion' else '--dependencies-file'
    prepared = review('prepare', 'code', '--uncommitted', '--verify', 'true', flag, str(declarations), cwd=repo)
    assert prepared.exit_code == 0, prepared
    job = Path(prepared.envelope['job_dir'])
    original = (job / 'target.json').read_bytes()
    codex_reply({k: v for k, v in RESPONSE.items() if k != 'verified_claims'})
    assert review('run', job.name, fake={'CODEX_FAKE_RESPONSE': codex_reply.path}).exit_code == 0
    annotation = accepted(review, job.name, job, source)
    annotation['fix_paths'] = ['pkg/f.py']
    assert submit(review, job.name, job, [annotation]).exit_code == 0
    # when the accepted fix lands, then apply preserves both old capture and post-fix context
    source.write_text('VALUE = 1\n')
    if drift == 'before-apply':
        (repo / 'pkg/other.py').write_text('UNAUTHORIZED = 1\n')
    result = submit_apply(review, job.name, job, [dict(finding_id='F-1', result='applied')])
    if drift == 'before-apply':
        assert result.exit_code == 4, result.envelope
        assert not (job / 'apply.json').exists()
        return
    assert result.exit_code == 0, result.envelope
    assert (job / 'target.json').read_bytes() == original
    assert not (job / 'invalidated.json').exists()
    # verification uses the post-apply context and requalifies changed dependency bytes
    from codex_review import context, execution
    from qualification_fixture import qualify_fixture
    post = context.after_apply(str(job), json.loads(original))
    qualify_fixture(job.parent, job, execution.key(post))
    from qualification_fixture import qualify_command
    qualify_command(job, 'true\n', post)
    (job / 'staging/verify.sh').write_text('true\n')
    if drift == 'before-verification':
        (repo / 'pkg/other.py').write_text('UNAUTHORIZED = 1\n')
    sealed = review('seal', job.name, 'staging/verify.sh').envelope['payload']
    launch = sealed['execution']
    if drift == 'before-verification':
        assert not launch['ready'], launch
        assert launch['reason'] == 'context-drift'
        return
    assert launch['ready'], launch
    completed = subprocess.run(launch['argv'], cwd=launch['cwd'], capture_output=True)
    (job / 'verify.out').write_bytes(completed.stdout + completed.stderr)
    (job / 'verify.rc').write_text(str(completed.returncode) + '\n')
    block = dict(result='passed', command_evidence_id=sealed['evidence_id'])
    for field, slot in [('output', 'verify.out'), ('rc', 'verify.rc')]:
        block[field + '_evidence_id'] = review('seal', job.name, slot).envelope['payload']['evidence_id']
    (job / 'staging/verification.json').write_text(json.dumps(dict(
        apply_sha256=images.sha256_file(str(job / 'apply.json')), verification=block)))
    verified = review('record-verification', job.name, '--verification-file', 'staging/verification.json')
    assert verified.exit_code == 0, verified.envelope


@pytest.mark.parametrize('empty_sibling', [False, True])
def should_account_for_required_parent_directories_without_waiving_empty_sibling_changes(
        review, make_repo, codex_reply, tmp_path, empty_sibling):
    # given a declared new file beneath a bound ignored runtime directory
    repo = make_repo({'f.py': 'VALUE = 0\n', '.gitignore': 'runtime/\n'})
    (repo / 'f.py').write_text('VALUE = 1\n')
    root = repo / 'runtime'
    root.mkdir()
    source = root / 'package/value.py'
    declarations = tmp_path / 'inputs.json'
    declarations.write_text(json.dumps([dict(id='runtime', path=str(root))]))
    prepared = review('prepare', 'code', '--uncommitted', '--no-verify', '--reason', 'directory control',
                      '--dependencies-file', str(declarations), cwd=repo)
    assert prepared.exit_code == 0, prepared
    job = Path(prepared.envelope['job_dir'])
    codex_reply({k: v for k, v in RESPONSE.items() if k != 'verified_claims'})
    assert review('run', job.name, fake={'CODEX_FAKE_RESPONSE': codex_reply.path}).exit_code == 0
    annotation = accepted(review, job.name, job, source)
    annotation['fix_paths'] = ['runtime/package/value.py']
    assert submit(review, job.name, job, [annotation]).exit_code == 0
    # when the file creates its parent, then only an additional empty sibling is unrelated drift
    source.parent.mkdir()
    source.write_text('VALUE = 2\n')
    if empty_sibling:
        (root / 'feature_enabled').mkdir()
    result = submit_apply(review, job.name, job, [dict(finding_id='F-1', result='applied')])
    if empty_sibling:
        assert result.exit_code == 4, result
        assert not (job / 'apply.json').exists()
    else:
        assert result.exit_code == 0, result
        assert result.envelope['payload']['changed_paths'] == [str(source)]
        assert json.loads((job / 'apply.json').read_text())['post_apply_context']
