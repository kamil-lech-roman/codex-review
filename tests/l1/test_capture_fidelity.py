"""Section 3 — the captured artifacts must be the bytes the repository actually held."""

import json
import subprocess
from pathlib import Path

import pytest


def git(root, *args):
    return subprocess.run(("git",) + args, cwd=str(root), check=True,
                          capture_output=True, text=True).stdout


def target_of(result):
    return json.loads((Path(result.envelope["job_dir"]) / "target.json").read_text())


def should_freeze_the_plan_bytes_into_the_job(review, plan_file_factory, outside_any_repo):
    """
    given a plan target
    when prepare captures it
    then the bytes are copied into the job, so a later edit cannot change what is reviewed
    """
    plan = plan_file_factory("# original\n")
    result = review("prepare", "plan", str(plan), cwd=outside_any_repo)
    plan.write_text("# replaced\n")

    job_dir = Path(result.envelope["job_dir"])
    artifact = target_of(result)["artifacts"][0]
    frozen = (job_dir / "artifacts" / artifact / "after").read_text()
    assert frozen == "# original\n"


def should_preserve_binary_content(review, make_repo):
    """
    given a committed binary file
    when it is captured
    then its bytes survive byte for byte
    """
    repo = make_repo()
    base = git(repo, "rev-parse", "HEAD").strip()
    payload = bytes(range(256)) * 4
    (repo / "image.bin").write_bytes(payload)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "binary")

    result = review("prepare", "code", "--base", base, cwd=repo)

    assert result.exit_code == 0, result
    job_dir = Path(result.envelope["job_dir"])
    detail = {a["path"]: a for a in target_of(result)["artifacts_detail"]}
    stored = (job_dir / "artifacts" / detail["image.bin"]["id"] / "after").read_bytes()
    assert stored == payload


def should_preserve_carriage_returns(review, make_repo):
    """
    given a committed CRLF file
    when it is captured
    then the line endings are not translated
    """
    repo = make_repo()
    base = git(repo, "rev-parse", "HEAD").strip()
    (repo / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "crlf")

    result = review("prepare", "code", "--base", base, cwd=repo)

    job_dir = Path(result.envelope["job_dir"])
    detail = {a["path"]: a for a in target_of(result)["artifacts_detail"]}
    stored = (job_dir / "artifacts" / detail["crlf.txt"]["id"] / "after").read_bytes()
    assert stored == b"one\r\ntwo\r\n"


def should_record_the_destination_of_a_rename(review, make_repo):
    """
    given a committed rename
    when it is captured
    then the destination path is in scope, not only the source
    """
    repo = make_repo({"pkg/old.py": "CONTENT = 1\n" * 40})
    base = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "mv", "pkg/old.py", "moved.py")
    git(repo, "commit", "-qm", "rename")

    result = review("prepare", "code", "--base", base, cwd=repo)

    assert result.exit_code == 0, result
    paths = sorted(a["path"] for a in target_of(result)["artifacts_detail"])
    assert "moved.py" in paths


def should_capture_a_symlink_as_its_link_text(review, make_repo, tmp_path):
    """
    given an untracked symlink pointing outside the repository
    when uncommitted work is captured
    then the link text is stored, never the referent's private bytes
    """
    secret = tmp_path / "private.txt"
    secret.write_text("PRIVATE MATERIAL\n")
    repo = make_repo({"a.py": "A = 1\n"})
    (repo / "link").symlink_to(secret)

    result = review("prepare", "code", "--uncommitted", cwd=repo)

    assert result.exit_code == 0, result
    job_dir = Path(result.envelope["job_dir"])
    detail = {a["path"]: a for a in target_of(result)["artifacts_detail"]}
    stored = (job_dir / "artifacts" / detail["link"]["id"] / "after").read_bytes()
    assert b"PRIVATE MATERIAL" not in stored
    assert stored == str(secret).encode()


def should_image_a_directory_without_crashing(tmp_path):
    """
    given a directory, as a checked-out submodule appears in the worktree
    when its image is taken
    then it is described rather than hashed as a file
    """
    from codex_review import images

    directory = tmp_path / "submodule"
    directory.mkdir()
    image = images.image_of(str(directory))
    assert image["kind"] == images.KIND_GITLINK
