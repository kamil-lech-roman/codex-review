"""Section 3 — capturing a code target as projections over a repository."""

import json
import subprocess
from pathlib import Path

import pytest


def git(root, *args):
    return subprocess.run(("git",) + args, cwd=str(root), check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def repo_with_history(make_repo):
    root = make_repo({"src/thing.py": "def thing():\n    return 1\n"})
    base = git(root, "rev-parse", "HEAD").strip()
    (root / "src" / "thing.py").write_text("def thing():\n    return 2\n")
    (root / "src" / "added.py").write_text("NEW = True\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "change thing, add added")
    return root, base


def target_of(result):
    return json.loads((Path(result.envelope["job_dir"]) / "target.json").read_text())


def should_refuse_code_with_no_base_and_no_upstream(review, make_repo):
    """
    given a repository with no upstream and no --base
    when prepare code runs
    then it exits 3 — target resolution refused
    """
    repo = make_repo()
    result = review("prepare", "code", cwd=repo)

    assert result.exit_code == 3, result
    assert result.envelope["job_id"] is None


def should_capture_the_committed_projection_against_a_base(review, repo_with_history):
    """
    given a base ref and two commits of changes
    when prepare code runs
    then one artifact is captured per changed path
    """
    repo, base = repo_with_history

    result = review("prepare", "code", "--base", base, cwd=repo)

    assert result.exit_code == 0, result
    target = target_of(result)
    assert target["mode"] == "code"
    assert target["base_ref"] == base
    paths = sorted(a["path"] for a in target["artifacts_detail"])
    assert paths == ["src/added.py", "src/thing.py"]
    assert result.envelope["payload"]["target"]["artifact_count"] == 2


def should_record_before_and_after_images_per_artifact(review, repo_with_history):
    """
    given a modified file and an added file
    when they are captured
    then each artifact records a preimage and postimage, absence included
    """
    repo, base = repo_with_history

    result = review("prepare", "code", "--base", base, cwd=repo)

    job_dir = Path(result.envelope["job_dir"])
    detail = {a["path"]: a for a in target_of(result)["artifacts_detail"]}
    modified = detail["src/thing.py"]
    assert modified["before"]["kind"] == "regular"
    assert modified["after"]["kind"] == "regular"
    assert (job_dir / "artifacts" / modified["id"] / "after").read_text() == (
        "def thing():\n    return 2\n"
    )
    added = detail["src/added.py"]
    assert added["before"]["kind"] == "absent"
    assert added["after"]["kind"] == "regular"


def should_record_a_repo_identity_with_four_projections(review, repo_with_history):
    """
    given a code capture
    when target.json is written
    then it carries the sampled RepoIdentity
    """
    repo, base = repo_with_history

    result = review("prepare", "code", "--base", base, cwd=repo)

    identity = target_of(result)["repo_identity"]
    assert len(identity["head_oid"]) == 40
    for field in ("ref_digest", "index_digest", "worktree_digest"):
        assert len(identity[field]) == 64


def should_refuse_an_oversize_artifact(review, repo_with_history):
    """
    given a changed file beyond the per-artifact cap
    when prepare code runs
    then it exits 6 naming the path and the limit
    """
    repo, base = repo_with_history
    (repo / "big.txt").write_text("x" * 70_000)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "big")

    result = review("prepare", "code", "--base", base, cwd=repo)

    assert result.exit_code == 6, result
    assert "big.txt" in json.dumps(result.envelope["error"]["details"])


def should_refuse_an_oversize_bundle(review, repo_with_history):
    """
    given many changes whose total exceeds the bundle cap
    when prepare code runs
    then it exits 6
    """
    repo, base = repo_with_history
    for index in range(10):
        (repo / "file{}.txt".format(index)).write_text("y" * 40_000)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "bulk")

    result = review("prepare", "code", "--base", base, cwd=repo)

    assert result.exit_code == 6, result


def should_capture_uncommitted_work(review, make_repo):
    """
    given staged, worktree and untracked changes
    when prepare code --uncommitted runs
    then all three reach the artifact set
    """
    repo = make_repo({"a.py": "A = 1\n"})
    (repo / "a.py").write_text("A = 2\n")
    (repo / "b.py").write_text("B = 1\n")
    git(repo, "add", "b.py")
    (repo / "c.py").write_text("C = 1\n")

    result = review("prepare", "code", "--uncommitted", cwd=repo)

    assert result.exit_code == 0, result
    paths = sorted(a["path"] for a in target_of(result)["artifacts_detail"])
    assert paths == ["a.py", "b.py", "c.py"]


def should_capture_the_initial_commit_before_any_commit_exists(review, tmp_path):
    """
    given a repository whose HEAD is unborn and whose first file is staged
    when uncommitted work is captured
    then the file is an addition, rather than a git error escaping without an envelope
    """
    root = tmp_path / "unborn"
    root.mkdir()
    run = lambda *a: subprocess.run(a, cwd=str(root), check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "Test")
    (root / "f.py").write_text("def f():\n    return 1\n")
    run("git", "add", "f.py")

    result = review("prepare", "code", "--uncommitted", cwd=root)

    assert result.envelope is not None, result
    assert result.stderr == "", result.stderr
    assert result.exit_code == 0, result
    detail = json.loads((Path(result.envelope["job_dir"]) / "target.json").read_text())
    paths = {a["path"]: a for a in detail["artifacts_detail"]}
    assert paths["f.py"]["before"]["kind"] == "absent", paths["f.py"]


def should_refuse_a_target_whose_head_is_not_the_pinned_commit(review, repo_with_history):
    """
    given --head naming a commit the repository has since moved off
    when prepare code runs
    then it exits 3 with `head-moved`, before a job exists

    `prepare` diffs the base against the HEAD of its working directory, so a commit landing
    between choosing the base and preparing silently widens the target to someone else's work.
    """
    repo, base = repo_with_history

    result = review("prepare", "code", "--base", base, "--head", base, cwd=repo)

    assert result.exit_code == 3, result
    assert result.envelope["job_id"] is None
    assert result.envelope["error"]["code"] == "head-moved"
    assert result.envelope["error"]["details"]["pinned"] == base


def should_refuse_uncommitted_capture_when_head_is_not_the_pinned_commit(
    review, repo_with_history, state_root
):
    """
    given staged changes and a pin naming the commit before the current HEAD
    when prepare code --uncommitted runs with that pin
    then it refuses with head-moved before publishing a job
    """
    repo, stale_head = repo_with_history
    head = git(repo, "rev-parse", "HEAD").strip()
    (repo / "src" / "thing.py").write_text("def thing():\n    return 3\n")
    git(repo, "add", "src/thing.py")

    result = review("prepare", "code", "--uncommitted", "--head", stale_head,
                    "--no-verify", cwd=repo)

    assert result.exit_code == 3, result
    assert result.envelope["error"]["code"] == "head-moved"
    assert result.envelope["error"]["details"]["pinned_oid"] == stale_head
    assert result.envelope["error"]["details"]["head_oid"] == head
    assert result.envelope["job_id"] is None
    assert result.envelope["job_dir"] is None
    assert not state_root.exists()


def should_capture_a_target_whose_head_is_the_pinned_commit(review, repo_with_history):
    """
    given --head naming the commit the repository is actually on
    when prepare code runs
    then the target is captured, so the pin refuses only a moved HEAD
    """
    repo, base = repo_with_history
    head = git(repo, "rev-parse", "HEAD").strip()

    result = review("prepare", "code", "--base", base, "--head", head, cwd=repo)

    assert result.exit_code == 0, result
    assert target_of(result)["repo_identity"]["head_oid"] == head


def should_accept_a_pinned_head_named_by_an_abbreviation(review, repo_with_history):
    """
    given --head naming the current commit by its short form
    when prepare code runs
    then it resolves the pin before comparing, rather than failing on spelling
    """
    repo, base = repo_with_history
    short = git(repo, "rev-parse", "--short", "HEAD").strip()

    result = review("prepare", "code", "--base", base, "--head", short, cwd=repo)

    assert result.exit_code == 0, result


def should_refuse_a_pinned_head_that_does_not_resolve(review, repo_with_history):
    """
    given --head naming something the repository cannot resolve
    when prepare code runs
    then it exits 3 with `unresolvable-head`, not a git error
    """
    repo, base = repo_with_history

    result = review("prepare", "code", "--base", base, "--head", "no-such-ref", cwd=repo)

    assert result.exit_code == 3, result
    assert result.stderr == "", result.stderr
    assert result.envelope["error"]["code"] == "unresolvable-head"


def should_refuse_a_pinned_head_on_a_plan_target(review, plan_file_factory, git_repo):
    """
    given --head passed with a plan target
    when prepare runs
    then it exits 2 rather than accepting a pin it cannot apply
    """
    plan = plan_file_factory()

    result = review("prepare", "plan", str(plan), "--head", "HEAD", cwd=git_repo)

    assert result.exit_code == 2, result
    assert result.envelope["error"]["code"] == "head-not-applicable"
