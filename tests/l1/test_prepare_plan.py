"""Sections 2 and 3 — publishing a job for a plan target."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def plan_file(tmp_path):
    path = tmp_path / "design.md"
    path.write_text("# a plan\n\nSome content.\n")
    return path


def should_publish_a_job_directly_under_the_state_root(review, state_root, plan_file):
    """
    given a plan target that exists
    when prepare runs
    then a job is published as a direct child of the state root, carrying target.json
    """
    result = review("prepare", "plan", str(plan_file))

    assert result.exit_code == 0, result
    job_dir = Path(result.envelope["job_dir"])
    assert job_dir.is_dir()
    assert job_dir.parent == state_root
    assert job_dir.name == result.envelope["job_id"]
    assert (job_dir / "target.json").is_file()


def should_leave_nothing_behind_in_staging(review, state_root, plan_file):
    """
    given a prepare that completes
    when the job is published
    then the .staging build has been renamed away, not copied
    """
    review("prepare", "plan", str(plan_file))

    staging = state_root / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []


def should_report_the_resolved_target_in_the_payload(review, plan_file):
    """
    given a plan target
    when prepare succeeds
    then the payload names the mode, resolved path, digest and size (§8)
    """
    result = review("prepare", "plan", str(plan_file))

    target = result.envelope["payload"]["target"]
    assert target["mode"] == "plan"
    assert target["resolved_path"] == str(plan_file)
    assert target["bytes"] == plan_file.stat().st_size
    assert len(target["target_sha"]) == 64


def should_record_a_plan_image_of_kind_regular(review, plan_file):
    """
    given an ordinary plan file
    when prepare captures it
    then target.json records a PlanIdentity whose image kind is `regular` (§3)
    """
    result = review("prepare", "plan", str(plan_file))

    target = json.loads((Path(result.envelope["job_dir"]) / "target.json").read_text())
    assert target["mode"] == "plan"
    assert target["plan_identity"]["plan_path"] == str(plan_file)
    assert target["plan_identity"]["plan_image"]["kind"] == "regular"


def should_refuse_a_non_numeric_capture_limit(review, plan_file_factory, outside_any_repo):
    """
    given a plan target and a capture limit that is not a number
    when prepare runs
    then it refuses with an envelope — the same guard code mode already has
    """
    plan = plan_file_factory("# a plan\n")

    for flag in ("--max-artifact", "--max-bundle"):
        result = review("prepare", "plan", str(plan), "--no-repo", flag, "bad",
                        cwd=outside_any_repo)

        assert result.envelope is not None, (flag, result)
        assert result.exit_code == 2, (flag, result)
        assert result.envelope["error"]["code"] == "bad-limit", (flag, result)
        assert result.stderr == "", (flag, result.stderr)
