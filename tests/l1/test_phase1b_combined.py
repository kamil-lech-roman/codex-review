"""Combined capture preserves committed, staged and worktree versions of one path."""
import subprocess

import pytest

from codex_review import projections


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout.strip()


def should_capture_all_projections_without_collapsing_repeated_paths(git_repo):
    # given committed, staged and unstaged changes to the same path plus an untracked file
    path = git_repo / 'README.md'
    base = git(git_repo, 'rev-parse', 'HEAD')
    original = path.read_bytes()
    path.write_text('committed\n')
    git(git_repo, 'add', 'README.md')
    git(git_repo, 'commit', '-qm', 'candidate')
    path.write_text('staged\n')
    git(git_repo, 'add', 'README.md')
    path.write_text('worktree\n')
    (git_repo / 'new.py').write_text('untracked\n')
    # when the loop freezes its complete candidate
    entries = projections.combined_changes(str(git_repo), base)
    # then each before/after pair remains distinct and complete
    versions = {(entry['projection'], entry['path']):
                (projections.read_side(str(git_repo), entry['_before_source']),
                 projections.read_side(str(git_repo), entry['_after_source'])) for entry in entries}
    assert versions == {
        ('committed', 'README.md'): (original, b'committed\n'),
        ('staged', 'README.md'): (b'committed\n', b'staged\n'),
        ('worktree', 'README.md'): (b'staged\n', b'worktree\n'),
        ('untracked', 'new.py'): (b'', b'untracked\n'),
    }


def should_assert_the_complete_captured_path_set_without_filtering(review, git_repo, tmp_path):
    import json
    # given two changed files and an assertion naming only one
    (git_repo / 'README.md').write_text('changed\n')
    (git_repo / 'extra.py').write_text('extra\n')
    assertion = tmp_path / 'paths.json'
    assertion.write_text(json.dumps(['README.md']))
    # when capturing the whole uncommitted change
    refused = review('prepare', 'code', '--uncommitted', '--expect-paths', str(assertion),
                     '--no-verify', '--reason', 'inspection', cwd=git_repo)
    # then refuse the mismatch rather than silently excluding the extra file
    assert refused.exit_code == 3, refused
    assert refused.envelope['error']['code'] == 'paths-mismatch'
    assertion.write_text(json.dumps(['README.md', 'extra.py']))
    accepted = review('prepare', 'code', '--uncommitted', '--expect-paths', str(assertion),
                      '--no-verify', '--reason', 'inspection', cwd=git_repo)
    assert accepted.exit_code == 0, accepted
    assert accepted.envelope['payload']['target']['paths'] == ['README.md', 'extra.py']


def should_retain_scoped_paths_without_replacing_existing_projections(git_repo):
    # given an unchanged tracked path, an absent prior addition, and a current delta
    base = git(git_repo, 'rev-parse', 'HEAD')
    (git_repo / 'changed.py').write_text('new\n')
    # when re-examining the agreed scope, then unchanged and absent paths remain explicit snapshots
    entries = projections.combined_changes(str(git_repo), base, retained_paths=['README.md', 'gone.py', 'changed.py'])
    assert [(e['path'], e['projection']) for e in entries] == [
        ('README.md', 'retained'), ('changed.py', 'untracked'), ('gone.py', 'retained')]
    for entry in [e for e in entries if e['projection'] == 'retained']:
        assert entry['before'] == entry['after']
    assert entries[-1]['after']['kind'] == 'absent'
    assert projections.read_side(str(git_repo), entries[-1]['_after_source']) == b''


def should_detect_reappearance_of_a_retained_absent_path(git_repo):
    # given a retained absence hidden from the ordinary Git-visible identity by ignore rules
    (git_repo / '.gitignore').write_text('gone.py\n')
    git(git_repo, 'add', '.gitignore')
    git(git_repo, 'commit', '-qm', 'ignore generated path')
    base = git(git_repo, 'rev-parse', 'HEAD')
    entries = projections.combined_changes(str(git_repo), base, retained_paths=['gone.py'])
    identity = projections.repo_identity(str(git_repo))
    assert projections.confirm_correspondence(str(git_repo), entries, identity, [b''])
    # when the absent file reappears during capture, then correspondence must fail despite unchanged Git identity
    (git_repo / 'gone.py').write_text('appeared\n')
    assert projections.repo_identity(str(git_repo)) == identity
    assert not projections.confirm_correspondence(str(git_repo), entries, identity, [b''])


@pytest.mark.parametrize('aliased', [False, True])
def should_track_ignored_retained_images_in_candidate_and_apply_checks(git_repo, aliased):
    from codex_review import apply, context
    # given a retained absence outside the Git-visible manifest
    relative = 'gone.py'
    if aliased:
        (git_repo / 'real').mkdir()
        (git_repo / 'alias').symlink_to('real', target_is_directory=True)
        relative = 'alias/gone.py'
    (git_repo / '.git/info/exclude').write_text('gone.py\n')
    root = str(git_repo)
    entries = projections.combined_changes(root, 'HEAD', retained_paths=[relative])
    target = dict(mode='code', capture_contract=2, repo_root=root,
                  repo_identity=projections.repo_identity(root), artifacts_detail=entries)
    captured_sha = apply.identity_sha(target)
    baseline = apply.baseline(target)
    assert context.applicable(target)
    assert apply.changed_paths(target, baseline) == []
    # when ignored bytes reappear, then all candidate and apply guards see the scoped change
    path = git_repo / ('real/gone.py' if aliased else 'gone.py')
    path.write_text('regenerated\n')
    assert not context.applicable(target)
    assert apply.identity_sha(target) != captured_sha
    assert apply.changed_paths(target, baseline) == [str(path)]
    assert not context.apply_applicable(target)
    assert context.apply_applicable(target, allowed=[str(path)])
    # and a fresh baseline measures the next edit, rather than always diffing the old capture
    current = apply.baseline(target)
    assert apply.changed_paths(target, current) == []
    path.write_text('second generation\n')
    assert apply.changed_paths(target, current) == [str(path)]
