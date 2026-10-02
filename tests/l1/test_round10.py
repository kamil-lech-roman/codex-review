"""Findings from the tenth review — the first astra round after the reserve loop."""

import subprocess


def should_refuse_a_target_with_no_artifacts(review, make_repo):
    """
    given a base that resolves to HEAD, so nothing changed
    when prepare runs
    then it is refused — a review of nothing can only approve
    """
    repo = make_repo({"src/app.py": "x = 1\n"})

    result = review("prepare", "code", "--repo", str(repo), "--base", "HEAD", cwd=repo)

    assert result.exit_code == 3, result
    assert "empty-target" in result.stdout


def should_refuse_an_empty_uncommitted_target(review, make_repo):
    """
    given a clean working tree
    when an uncommitted target is prepared
    then it is refused for the same reason
    """
    repo = make_repo({"src/app.py": "x = 1\n"})

    result = review("prepare", "code", "--repo", str(repo), "--uncommitted", cwd=repo)

    assert result.exit_code == 3, result
    assert "empty-target" in result.stdout
