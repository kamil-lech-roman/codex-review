"""Section 3 — the four projections genuinely differ, and the copy must match its sample."""

import json
import subprocess
from pathlib import Path


def git(root, *args):
    return subprocess.run(("git",) + args, cwd=str(root), check=True,
                          capture_output=True, text=True).stdout


def target_of(result):
    return json.loads((Path(result.envelope["job_dir"]) / "target.json").read_text())


def should_capture_staged_and_worktree_as_separate_projections(review, make_repo):
    """
    given a file whose staged content differs from both HEAD and the worktree
    when uncommitted work is captured
    then the staged version is not lost behind the worktree one
    """
    repo = make_repo({"f.py": "def f():\n    return 1\n"})
    (repo / "f.py").write_text("def f():\n    return 1 / 0\n")
    git(repo, "add", "f.py")
    (repo / "f.py").write_text("def f():\n    return 2\n")

    result = review("prepare", "code", "--uncommitted", cwd=repo)

    assert result.exit_code == 0, result
    job_dir = Path(result.envelope["job_dir"])
    bodies = [
        (job_dir / "artifacts" / item["id"] / "after").read_text()
        for item in target_of(result)["artifacts_detail"] if item["path"] == "f.py"
    ]
    assert any("1 / 0" in body for body in bodies), bodies
    assert any("return 2" in body for body in bodies), bodies


def should_detect_a_submodule_revision_change(review, make_repo, tmp_path):
    """
    given a checked-out submodule whose revision moves
    when its image is taken
    then the change is visible rather than hidden behind a bare directory
    """
    from codex_review import images

    inner = tmp_path / "inner"
    inner.mkdir()
    git(inner, "init", "-q")
    git(inner, "config", "user.email", "t@example.com")
    git(inner, "config", "user.name", "T")
    (inner / "x.txt").write_text("one\n")
    git(inner, "add", "-A")
    git(inner, "commit", "-qm", "one")
    first = images.image_of(str(inner))

    (inner / "x.txt").write_text("two\n")
    git(inner, "add", "-A")
    git(inner, "commit", "-qm", "two")
    second = images.image_of(str(inner))

    assert first["content_identity"] is not None
    assert first != second


def should_reject_a_copy_that_does_not_match_the_confirming_sample(tmp_path):
    """
    given bytes copied while the file changed and changed back
    when correspondence is confirmed
    then it fails — the sample agreeing with itself proves nothing about the copy
    """
    from codex_review import projections

    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    (root / "a.txt").write_text("A\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "a")

    identity = projections.repo_identity(str(root))
    entries = [{"path": "a.txt", "after": {"kind": "regular", "git_mode": "100644",
                                           "content_identity": "sha256:deadbeef"},
                "_after_source": ("worktree", "a.txt")}]
    # The copy holds bytes that never matched the recorded image.
    copied = [b"B\n"]

    assert not projections.confirm_correspondence(str(root), entries, identity, copied)


def should_close_a_job_whose_plan_was_deleted_after_review(
    review, plan_file_factory, outside_any_repo, codex_reply
):
    """
    given a plan deleted after capture
    when the reviewer answers
    then the frozen review is still recorded, because the bytes were frozen
    """
    plan = plan_file_factory("# a plan\n")
    prepared = review("prepare", "plan", str(plan), cwd=outside_any_repo)
    job_id = prepared.envelope["job_id"]
    job_dir = Path(prepared.envelope["job_dir"])
    plan.unlink()
    codex_reply({"verdict": "approve", "findings": [], "declared_scope": ["A0001"],
                 "verified_claims": []})

    result = review("run", job_id, fake={"CODEX_FAKE_RESPONSE": codex_reply.path})

    assert result.exit_code == 0, result
    assert (job_dir / "report.json").is_file()


def should_capture_a_rename_destination_as_an_addition(review, make_repo):
    """
    given a commit that only renames a file
    when the delta is captured
    then the destination is an addition, not a file that already held those bytes
    """
    repo = make_repo({"old.py": "x = 1\n" * 40})
    base = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "mv", "old.py", "new.py")
    git(repo, "-c", "user.email=t@example.com", "-c", "user.name=Test",
        "commit", "-qm", "rename")

    result = review("prepare", "code", "--base", base, cwd=repo)

    assert result.exit_code == 0, result
    by_path = {a["path"]: a for a in target_of(result)["artifacts_detail"]}
    assert by_path["new.py"]["before"]["kind"] == "absent", by_path["new.py"]
    assert by_path["new.py"]["before"] != by_path["new.py"]["after"], by_path["new.py"]
    assert by_path["old.py"]["after"]["kind"] == "absent", by_path["old.py"]
