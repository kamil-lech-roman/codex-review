"""Findings from the eighth review."""

import json
import subprocess
from pathlib import Path


def commit_all(repo):
    for argv in (("git", "add", "-A"), ("git", "commit", "-qm", "x")):
        subprocess.run(argv, cwd=str(repo), check=True, capture_output=True)


def should_refuse_a_policy_link_with_a_dot_component(review, plan_file_factory, make_repo):
    """
    given AGENTS.md -> notes.md/. where notes.md is a regular file
    when the policy is captured
    then it is refused — real traversal through a file returns ENOTDIR
    """
    repo = make_repo({"notes.md": "# Decoy\n\nAn unrelated document.\n"})
    (repo / "AGENTS.md").symlink_to("notes.md/.")
    commit_all(repo)

    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 5, result
    assert "An unrelated document." not in result.stdout


def should_refuse_a_denial_citing_evidence_that_does_not_exist(review, plan_file_factory,
                                                               outside_any_repo, codex_reply):
    """
    given a denied verification
    when its command evidence names an id that was never sealed
    then it is refused — a denial still rests on the script it declined to run
    """
    from test_round4 import probed_with, record_apply, submit_verification

    plan, job_id, job_dir = probed_with(review, plan_file_factory, outside_any_repo,
                                        codex_reply, "--verify", "true")
    plan.write_text("# a plan\n\nfixed.\n")
    assert record_apply(review, job_id, job_dir).exit_code == 0

    result = submit_verification(review, job_id, job_dir, {
        "result": "denied", "command_evidence_id": "0" * 16,
        "denial_kind": "policy", "asserted_by": "user"})

    assert result.exit_code == 8, result
    assert not (job_dir / "verification.json").exists()
