"""Findings from the sixth review — defects in the fifth round's symlink resolution."""

import os
import subprocess
from pathlib import Path


def commit_all(repo):
    for argv in (("git", "add", "-A"), ("git", "commit", "-qm", "x")):
        subprocess.run(argv, cwd=str(repo), check=True, capture_output=True)


def agents_text(result):
    return (Path(result.envelope["job_dir"]) / "AGENTS.md").read_text()


def should_refuse_a_policy_link_that_traverses_a_directory_symlink(review,
                                                                   plan_file_factory,
                                                                   make_repo):
    """
    given AGENTS.md -> alias/../notes.md where alias is itself a symlink
    when the policy is captured
    then it is refused rather than resolved by a rule that must model the filesystem
    """
    repo = make_repo({"trusted/rules/keep.md": "x\n",
                      "trusted/notes.md": "# Real\n\nThe policy that applies.\n",
                      "notes.md": "# Decoy\n\nAn unrelated document.\n"})
    (repo / "alias").symlink_to("trusted/rules")
    (repo / "AGENTS.md").symlink_to("alias/../notes.md")
    commit_all(repo)

    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 5, result
    assert "An unrelated document." not in result.stdout


def should_ignore_an_unrelated_symlink_whose_target_is_not_utf8(review, plan_file_factory,
                                                                make_repo):
    """
    given a policy-free repository holding an unrelated symlink with non-UTF-8 target bytes
    when a plan is prepared against it
    then capture succeeds — an irrelevant link is never read
    """
    repo = make_repo({"src/app.py": "x = 1\n"})
    os.symlink(b"legacy-\xff.dat", bytes(repo / "asset.dat"))
    commit_all(repo)

    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 0, result
    assert "No policy files were present" in agents_text(result)
