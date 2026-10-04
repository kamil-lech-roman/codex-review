"""Additional fix paths must be declared before they change, against the same baseline."""
import json
import subprocess

import pytest

from codex_review import apply, findings, images, loops, ownership, state


@pytest.fixture
def probed_loop(review, make_repo, state_root, tmp_path, request):
    kind = getattr(request, 'param', None)
    files = {'original.py': 'old\n', 'sibling.py': 'old\n'}
    if kind:
        files['.gitignore'] = 'runtime/\n'
    repo = make_repo(files)
    flags = []
    if kind:
        runtime = repo / 'runtime'
        runtime.mkdir()
        sibling = runtime / 'sibling.py'
        sibling.write_text('old\n')
        declarations = tmp_path / 'inputs.json'
        declarations.write_text(json.dumps([dict(id='runtime', path=str(runtime if kind == 'dependency-directory' else sibling))]))
        flags = ['--companions-file' if kind == 'companion' else '--dependencies-file', str(declarations)]
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=str(repo)).decode().strip()
    (repo / 'original.py').write_text('new defect\n')
    prepared = review('prepare', 'code', '--loop', '--base', head, '--head', head,
                      '--no-verify', '--reason', 'offline scope control', *flags, cwd=repo)
    assert prepared.exit_code == 0, prepared
    directory = state_root / prepared.envelope['job_id']
    current = loops.read(str(directory))
    job = state_root / current['current_round']
    target = json.loads((job / 'target.json').read_text())
    annotation = dict(finding_id='F-1', disposition='accepted', fix_paths=['original.py'])
    state.write_json(str(job / 'report.json'), dict(findings=[dict(id='F-1')]))
    state.write_json(str(job / 'probes.json'), dict(annotations=[annotation], owns_apply=True,
        pre_apply_baseline=apply.baseline(target), pre_fix_sha=apply.identity_sha(target), probe_completion='complete'))
    ownership.claim(str(state_root), target['exec_dir'], job.name)
    loops.append(str(directory), 0, dict(current, phase='probed', findings=findings.add([], job.name, [annotation])),
                 dict(kind='offline-probed-fixture'))
    (job / 'staging').mkdir(exist_ok=True)
    return repo, directory, job


def amendment(review, directory, job, path='sibling.py'):
    status = review('status', str(directory)).envelope['payload']
    payload = dict(checkpoint_sha256=status['checkpoint_sha256'],
                   report_sha256=images.sha256_file(str(job / 'report.json')),
                   probes_sha256=images.sha256_file(str(job / 'probes.json')),
                   additions=[dict(finding_id='F-1', paths=[path],
                                   rationale='Same defect in the sibling implementation',
                                   authority='User authorized correcting this feature in the repository')])
    (job / 'staging' / 'amendment.json').write_text(json.dumps(payload))
    return review('amend-paths', str(directory), '--round', job.name,
                  '--expected-generation', str(status['generation']), '--amendment-file', 'staging/amendment.json')


def should_apply_a_predeclared_sibling_fix_without_rewriting_the_original_baseline(review, probed_loop):
    # given an accepted finding and its original pre-apply baseline
    repo, directory, job = probed_loop
    original = (job / 'probes.json').read_bytes()
    (repo / 'original.py').write_text('corrected originally declared site\n')
    # when declaring an unchanged sibling before editing it
    amended = amendment(review, directory, job)
    assert amended.exit_code == 0, amended
    (repo / 'sibling.py').write_text('corrected\n')
    payload = dict(report_sha256=images.sha256_file(str(job / 'report.json')),
                   probes_sha256=images.sha256_file(str(job / 'probes.json')),
                   amendments_sha256=amended.envelope['payload']['amendments_sha256'],
                   results=[dict(finding_id='F-1', result='applied', sibling_sweep=dict(
                       searched_scope=['original.py', 'sibling.py'], related_sites=[],
                       rationale='Both implementations inspected; no further sites'))])
    (job / 'staging' / 'apply.json').write_text(json.dumps(payload))
    applied = review('record-apply', str(directory), '--round', job.name,
                     '--expected-generation', str(amended.envelope['payload']['generation']),
                     '--apply-file', 'staging/apply.json')
    # then the original baseline measures the amendment and remains byte-identical
    assert applied.exit_code == 0, applied
    assert applied.envelope['payload']['changed_paths'] == [str(repo / 'original.py'), str(repo / 'sibling.py')]
    assert (job / 'probes.json').read_bytes() == original
    assert json.loads((job / 'apply.json').read_text())['amendments_sha256'] == payload['amendments_sha256']


@pytest.mark.parametrize('index_only', [False, True])
def should_refuse_retrospective_path_amendment(review, probed_loop, index_only):
    # given a sibling edited without a prior declaration
    repo, directory, job = probed_loop
    (repo / 'sibling.py').write_text('already edited\n')
    if index_only:
        subprocess.run(['git', 'add', 'sibling.py'], cwd=str(repo), check=True)
        (repo / 'sibling.py').write_text('old\n')
    # when attempting retrospective scope expansion, then generation and declarations stay unchanged
    result = amendment(review, directory, job)
    assert result.exit_code == 8, result
    assert result.envelope['error']['code'] == 'amendment-after-edit'
    assert loops.read(str(directory))['generation'] == 1


def should_refuse_amendment_without_apply_ownership(review, probed_loop, state_root):
    # given lost apply ownership, even if bytes and generation still match
    repo, directory, job = probed_loop
    ownership.release(str(state_root), str(repo), job.name)
    # when asking to add a path, then ownership is required before any scope mutation
    result = amendment(review, directory, job)
    assert result.exit_code == 16, result
    assert loops.read(str(directory))['generation'] == 1


def should_pause_the_loop_after_a_terminal_apply_scope_violation(review, probed_loop):
    # given a change outside the predeclared fix paths
    repo, directory, job = probed_loop
    (repo / 'sibling.py').write_text('unauthorized change\n')
    payload = dict(report_sha256=images.sha256_file(str(job / 'report.json')),
                   probes_sha256=images.sha256_file(str(job / 'probes.json')),
                   results=[dict(finding_id='F-1', result='no-change', sibling_sweep=dict(
                       searched_scope=['original.py'], related_sites=[], rationale='Fixture scope failure'))])
    (job / 'staging' / 'apply.json').write_text(json.dumps(payload))
    # when recording refuses terminally, then status cannot direct another apply into the closed round
    result = review('record-apply', str(directory), '--round', job.name, '--expected-generation', '1',
                    '--apply-file', 'staging/apply.json')
    assert result.exit_code == 10, result
    status = review('status', str(directory)).envelope['payload']
    assert status['pause']['kind'] == 'scope-violation'
    assert status['next_verb'] is None


def should_refuse_an_added_path_outside_the_baselines_visible_universe(review, probed_loop):
    from codex_review import amendments, response
    # given an ignored new path that the Git-visible baseline cannot observe
    repo, directory, job = probed_loop
    (repo / '.git' / 'info' / 'exclude').write_text('ignored.py\n')
    target = json.loads((job / 'target.json').read_text())
    record = json.loads((job / 'probes.json').read_text())
    # when declaring it, then lack of an observable baseline refuses rather than authorizing invisible edits
    with pytest.raises(response.ContractViolation, match='baseline'):
        amendments.validate(target, record, [], dict(additions=[dict(finding_id='F-1', paths=['ignored.py'],
                            rationale='additional site', authority='same task')]))


@pytest.mark.parametrize('probed_loop', ['companion', 'dependency-file', 'dependency-directory'], indirect=True)
@pytest.mark.parametrize('edited_first', [False, True])
def should_amend_an_ignored_path_already_bound_by_the_baseline(review, probed_loop, edited_first):
    # given an ignored governing input retained before the author editing interval
    repo, directory, job = probed_loop
    original = (job / 'probes.json').read_bytes()
    if edited_first:
        (repo / 'runtime/sibling.py').write_text('already edited\n')
    # when declaring it before editing, then the observable path can join the accepted fix
    amended = amendment(review, directory, job, 'runtime/sibling.py')
    if edited_first:
        assert amended.exit_code == 8, amended.envelope
        assert amended.envelope['error']['code'] == 'reference-drift'
        assert loops.read(str(directory))['generation'] == 1
        assert (job / 'probes.json').read_bytes() == original
        return
    assert amended.exit_code == 0, amended.envelope
    (repo / 'runtime/sibling.py').write_text('corrected\n')
    payload = dict(report_sha256=images.sha256_file(str(job / 'report.json')),
                   probes_sha256=images.sha256_file(str(job / 'probes.json')),
                   amendments_sha256=amended.envelope['payload']['amendments_sha256'],
                   results=[dict(finding_id='F-1', result='applied', sibling_sweep=dict(
                       searched_scope=['original.py', 'runtime/sibling.py'], related_sites=[],
                       rationale='Both sites inspected; no further sites'))])
    (job / 'staging/apply.json').write_text(json.dumps(payload))
    applied = review('record-apply', str(directory), '--round', job.name,
                     '--expected-generation', str(amended.envelope['payload']['generation']),
                     '--apply-file', 'staging/apply.json')
    assert applied.exit_code == 0, applied.envelope
    assert applied.envelope['payload']['changed_paths'] == [str(repo / 'runtime/sibling.py')]
    assert (job / 'probes.json').read_bytes() == original
