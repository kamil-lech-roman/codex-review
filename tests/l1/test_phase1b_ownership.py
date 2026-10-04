"""Apply ownership survives individual commands and refuses competing writers."""
from pathlib import Path

import pytest

from codex_review import jobs


def should_refuse_a_nonce_symlink_that_escapes_state(tmp_path):
    # given a syntactically valid job name pointing outside the state root
    root = tmp_path / 'state'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / ('a' * 16)).symlink_to(outside, target_is_directory=True)
    # when resolving, then it is not an addressable job
    assert jobs.resolve(str(root), 'a' * 16) is None


def should_reject_absolute_parent_traversal_even_when_it_normalizes_inside_state(tmp_path):
    # given an existing direct child reached through a parent-traversal spelling
    root = tmp_path / 'state'
    job = root / ('a' * 16)
    job.mkdir(parents=True)
    alias = str(root / 'unused' / '..' / job.name)
    # when resolving, then the shared public grammar rejects traversal before normalization
    assert jobs.resolve(str(root), alias) is None


def should_hold_ownership_until_explicit_release(tmp_path):
    from codex_review import ownership
    # given two jobs targeting the same worktree
    root = tmp_path / 'state'
    checkout = tmp_path / 'repo'
    checkout.mkdir()
    # when the first job claims ownership across commands
    ownership.claim(str(root), str(checkout), 'a' * 16)
    ownership.require_owner(str(root), str(checkout), 'a' * 16)
    # then another job cannot claim or release it
    with pytest.raises(ownership.Conflict):
        ownership.claim(str(root), str(checkout), 'b' * 16)
    with pytest.raises(ownership.Conflict):
        ownership.release(str(root), str(checkout), 'b' * 16)
    ownership.release(str(root), str(checkout), 'a' * 16)
    ownership.claim(str(root), str(checkout), 'b' * 16)
    ownership.require_owner(str(root), str(checkout), 'b' * 16)


@pytest.mark.parametrize('nested_first', [False, True])
def should_refuse_overlapping_execution_directories(tmp_path, nested_first):
    from codex_review import ownership
    # given two editing scopes that overlap although their directory names differ
    root = str(tmp_path / 'state')
    parent = str(tmp_path / 'repo')
    child = str(tmp_path / 'repo/docs')
    first, second = (child, parent) if nested_first else (parent, child)
    ownership.claim(root, first, 'a' * 16)
    # when another job claims the overlapping scope, then ownership refuses it
    with pytest.raises(ownership.Conflict):
        ownership.claim(root, second, 'b' * 16)
    ownership.release(root, first, 'a' * 16)
    ownership.claim(root, second, 'b' * 16)


def should_refuse_absent_or_unknown_ownership(tmp_path):
    from codex_review import ownership
    # given no durable holder record
    # when a job attempts to record an apply
    with pytest.raises(ownership.Conflict):
        ownership.require_owner(str(tmp_path), str(tmp_path / 'repo'), 'a' * 16)
    # then corruption does not permit stealing a lock
    ownership.claim(str(tmp_path), str(tmp_path / 'repo'), 'a' * 16)
    next((tmp_path / 'owners').glob('*.json')).write_text('not json')
    with pytest.raises(ownership.Conflict):
        ownership.claim(str(tmp_path), str(tmp_path / 'repo'), 'b' * 16)


@pytest.mark.parametrize('new_holder', [False, True])
def should_finish_terminal_release_without_deleting_a_successor(tmp_path, new_holder):
    from codex_review import ownership
    # given a terminal owner whose release was interrupted, or is being retried
    root = tmp_path / 'state'
    first = root / ('a' * 16)
    first.mkdir(parents=True)
    (first / 'target.json').write_text('{}')
    checkout = tmp_path / 'repo'
    checkout.mkdir()
    ownership.claim(str(root), str(checkout), first.name)
    (first / 'abandoned.json').write_text('{}')
    if new_holder:
        ownership.claim(str(root), str(checkout), 'b' * 16)
    else:
        ownership.release(str(root), str(checkout), first.name)
    # when completing the old terminal release, then it is idempotent and preserves a new owner
    ownership.release(str(root), str(checkout), first.name)
    if new_holder:
        ownership.require_owner(str(root), str(checkout), 'b' * 16)


def should_refuse_competing_apply_and_release_on_abandon(review, plan_file_factory, codex_reply):
    from test_record_probes import RESPONSE, accepted, submit
    # given two reviewed jobs over the same execution directory
    plan = plan_file_factory()
    jobs_ready = []
    codex_reply(RESPONSE)
    for _ in range(2):
        prepared = review('prepare', 'plan', str(plan), '--no-repo', '--no-verify', '--reason', 'inspection')
        directory = Path(prepared.envelope['job_dir'])
        job_id = prepared.envelope['job_id']
        assert review('run', job_id, fake={'CODEX_FAKE_RESPONSE': codex_reply.path}).exit_code == 0
        jobs_ready.append((job_id, directory, accepted(review, job_id, directory, plan)))
    # when the first job declares accepted fixes
    first, second = jobs_ready
    assert submit(review, first[0], first[1], [first[2]]).exit_code == 0
    # then the second cannot open an overlapping editing interval
    refusal = submit(review, second[0], second[1], [second[2]])
    assert refusal.exit_code == 16, refusal
    assert not (second[1] / 'probes.json').exists()
    assert review('abandon', first[0], '--reason', 'superseded').exit_code == 0
    assert submit(review, second[0], second[1], [second[2]]).exit_code == 0


@pytest.mark.parametrize('context_kind', ['containing', 'unrelated'])
def should_own_the_same_plan_independently_of_context_selection(review, make_repo, codex_reply, context_kind):
    from test_record_probes import RESPONSE, accepted, submit
    # given a plan reviewed with an explicit repository and without repository context
    repo = make_repo({'docs/P.md': '# plan\n'})
    context_repo = repo if context_kind == 'containing' else make_repo({'README.md': '# context\n'})
    plan = repo / 'docs/P.md'
    codex_reply(RESPONSE)
    ready = []
    for options in [('--repo', str(context_repo)), ('--no-repo',)]:
        prepared = review('prepare', 'plan', str(plan), *options, '--no-verify', '--reason', 'inspection')
        job = Path(prepared.envelope['job_dir'])
        assert review('run', job.name, fake={'CODEX_FAKE_RESPONSE': codex_reply.path}).exit_code == 0
        ready.append((job, accepted(review, job.name, job, plan)))
    first, second = ready
    assert submit(review, first[0].name, first[0], [first[1]]).exit_code == 0
    # when the second accepts an edit to the identical plan, then it cannot gain ownership
    refused = submit(review, second[0].name, second[0], [second[1]])
    assert refused.exit_code == 16, refused.envelope
    assert not (second[0] / 'probes.json').exists()


def should_refuse_distinct_plan_owners_in_the_same_worktree(review, make_repo, codex_reply):
    from test_record_probes import RESPONSE, accepted, submit
    # given two context-free plans in different directories of the same worktree
    repo = make_repo({'one/P.md': '# first\n', 'two/P.md': '# second\n'})
    codex_reply(RESPONSE)
    for index, path in enumerate(['one/P.md', 'two/P.md']):
        plan = repo / path
        prepared = review('prepare', 'plan', str(plan), '--no-repo', '--no-verify', '--reason', 'inspection')
        job = Path(prepared.envelope['job_dir'])
        assert review('run', job.name, fake={'CODEX_FAKE_RESPONSE': codex_reply.path}).exit_code == 0
        annotation = accepted(review, job.name, job, plan)
        # when both request edit ownership, then only the first gets it
        result = submit(review, job.name, job, [annotation])
        assert result.exit_code == (0 if index == 0 else 16), result.envelope


def should_refuse_apply_without_its_durable_owner(review, plan_file_factory, outside_any_repo, codex_reply, state_root):
    from test_record_apply import probe_a_job, submit_apply
    # given an editing interval whose ownership record was lost
    job_id, directory, plan = probe_a_job(review, plan_file_factory, outside_any_repo, codex_reply)
    next((state_root / 'owners').glob('*.json')).unlink()
    plan.write_text('fixed\n')
    # when claiming a successful apply
    result = submit_apply(review, job_id, directory, [{'finding_id': 'F-1', 'result': 'applied'}])
    # then bytes alone cannot establish ownership
    assert result.exit_code == 16, result
    assert not (directory / 'apply.json').exists()


def should_refuse_a_commit_during_the_authorized_editing_interval(review, make_repo, codex_reply):
    import subprocess
    from test_record_apply import code_job_with_a_staged_defect, submit_apply
    # given an accepted index-only fix against the original HEAD
    repo, job_id, directory = code_job_with_a_staged_defect(review, make_repo, codex_reply)
    subprocess.run(['git', 'add', 'f.py'], cwd=repo, check=True, capture_output=True)
    subprocess.run(['git', 'commit', '--allow-empty', '-qm', 'external commit'], cwd=repo, check=True, capture_output=True)
    # when applying against the moved commit
    result = submit_apply(review, job_id, directory, [dict(finding_id='F-1', result='applied')])
    # then intended index changes do not grant authority to adopt a changed HEAD
    assert result.exit_code == 4, result
    assert not (directory / 'apply.json').exists()
