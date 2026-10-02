"""Section 7 — `prepare` argument handling, before any job exists."""

import pytest


def should_refuse_prepare_with_no_target(review):
    """
    given `prepare` with no target arguments
    when the driver runs
    then it exits 2 with null identity, because no job was created
    """
    result = review("prepare")

    assert result.exit_code == 2
    assert result.envelope["verb"] == "prepare"
    assert result.envelope["job_id"] is None
    assert result.envelope["job_dir"] is None
    assert "error" in result.envelope


def should_refuse_a_plan_target_whose_path_is_missing(review, tmp_path):
    """
    given `prepare plan` naming a path that does not exist
    when the driver runs
    then it exits 3 — target resolution refused — with null identity
    """
    result = review("prepare", "plan", str(tmp_path / "absent.md"))

    assert result.exit_code == 3
    assert result.envelope["verb"] == "prepare"
    assert result.envelope["job_id"] is None
    assert result.envelope["job_dir"] is None


def should_refuse_a_plan_target_with_no_path(review):
    """
    given `prepare plan` with no path argument
    when the driver runs
    then it exits 2, an argument error rather than a resolution refusal
    """
    result = review("prepare", "plan")

    assert result.exit_code == 2


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def should_refuse_a_blank_verify_command_before_any_job_exists(
        review, plan_file_factory, outside_any_repo, blank):
    """
    given `--verify` with an empty or whitespace-only command
    when prepare runs
    then it exits 2 with no job — a blank command is not a command, and would otherwise be
    recorded as configured, so a later `not-run-explicitly` would be refused as contradicting
    """
    plan = plan_file_factory("# a plan\n")

    result = review("prepare", "plan", str(plan), "--verify", blank, cwd=outside_any_repo)

    assert result.exit_code == 2, result
    assert result.envelope["job_id"] is None
    assert result.envelope["error"]["code"] == "blank-verify", result
