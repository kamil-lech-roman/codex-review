"""Section 3 — context resolution and policy capture."""

import hashlib
import json
from pathlib import Path


def target_of(result):
    return json.loads((Path(result.envelope["job_dir"]) / "target.json").read_text())


def should_resolve_context_free_when_cwd_is_outside_any_repository(
    review, plan_file_factory, outside_any_repo
):
    """
    given a plan target and a cwd inside no repository
    when prepare runs
    then no context repository is resolved and no policy commit is read
    """
    result = review("prepare", "plan", str(plan_file_factory()), cwd=outside_any_repo)

    assert result.exit_code == 0, result
    target = target_of(result)
    assert target["context_repository"] is None
    assert target["policy_commit"] is None


def should_still_write_a_synthesized_agents_file_context_free(
    review, plan_file_factory, outside_any_repo
):
    """
    given context-free resolution
    when prepare runs
    then AGENTS.md is still written, stating that no repository policy was captured
    """
    result = review("prepare", "plan", str(plan_file_factory()), cwd=outside_any_repo)

    agents = Path(result.envelope["job_dir"]) / "AGENTS.md"
    assert agents.is_file()
    assert "no repository policy was captured" in agents.read_text()


def should_hash_exactly_the_bytes_the_driver_wrote(
    review, plan_file_factory, outside_any_repo
):
    """
    given a synthesized AGENTS.md
    when prepare records synthesized_policy_sha256
    then it digests exactly that file, so an audit of the job reproduces it
    """
    result = review("prepare", "plan", str(plan_file_factory()), cwd=outside_any_repo)

    agents = Path(result.envelope["job_dir"]) / "AGENTS.md"
    expected = hashlib.sha256(agents.read_bytes()).hexdigest()
    assert target_of(result)["synthesized_policy_sha256"] == expected


def should_resolve_the_repository_containing_cwd(review, plan_file_factory, git_repo):
    """
    given a cwd inside a git repository
    when prepare runs for a plan target
    then that repository is the context and its HEAD is the policy commit
    """
    result = review("prepare", "plan", str(plan_file_factory()), cwd=git_repo)

    assert result.exit_code == 0, result
    target = target_of(result)
    assert target["context_repository"] == str(git_repo.resolve())
    assert len(target["policy_commit"]) == 40


def should_prefer_an_explicit_repo_flag_over_cwd(
    review, plan_file_factory, git_repo, outside_any_repo
):
    """
    given --repo naming a repository and a cwd outside any repository
    when prepare runs
    then --repo wins, because it is first in the resolution ladder
    """
    result = review(
        "prepare", "plan", str(plan_file_factory()), "--repo", str(git_repo),
        cwd=outside_any_repo,
    )

    assert result.exit_code == 0, result
    assert target_of(result)["context_repository"] == str(git_repo.resolve())
