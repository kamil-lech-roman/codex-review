"""Sections 7 and 13 — closing a job deliberately."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def job(review, plan_file_factory, outside_any_repo):
    result = review("prepare", "plan", str(plan_file_factory()), cwd=outside_any_repo)
    assert result.exit_code == 0, result
    return result.envelope["job_id"], Path(result.envelope["job_dir"])


def record_of(job_dir):
    return json.loads((job_dir / "abandoned.json").read_text())


def should_close_a_job_with_a_reason(review, job):
    """
    given an open job
    when abandon runs with a reason
    then a terminal record is written and the stage becomes abandoned
    """
    job_id, job_dir = job

    result = review("abandon", job_id, "--reason", "superseded by a newer plan")

    assert result.exit_code == 0, result
    record = record_of(job_dir)
    assert record["reason"] == "superseded by a newer plan"
    assert record["asserted_by"] == "user"
    assert record["abandoned_at"]
    assert review("status", job_id).envelope["payload"]["stage"] == "abandoned"


def should_require_a_reason(review, job):
    """
    given abandon with no --reason
    when the driver runs
    then it exits 2 — a terminal record with no stated reason records nothing
    """
    job_id, _ = job
    assert review("abandon", job_id).exit_code == 2


def should_refuse_after_a_terminal_record(review, job):
    """
    given an already-closed job
    when abandon runs again
    then it refuses, naming the record that closed it
    """
    job_id, _ = job
    review("abandon", job_id, "--reason", "first")

    result = review("abandon", job_id, "--reason", "second")

    assert result.exit_code != 0
    assert "abandoned.json" in json.dumps(result.envelope["error"])


def should_omit_no_movement_fields_when_the_plan_is_untouched(review, job):
    """
    given a plan that has not changed since capture
    when abandon runs
    then no movement fields are written, because nothing moved
    """
    job_id, job_dir = job

    review("abandon", job_id, "--reason", "done")

    record = record_of(job_dir)
    assert "current_identity" not in record
    assert "changed_paths" not in record


def should_record_movement_when_the_plan_has_changed(review, job, plan_file_factory):
    """
    given a plan edited after capture
    when abandon runs
    then the record carries the current identity and the changed path
    """
    job_id, job_dir = job
    target = json.loads((job_dir / "target.json").read_text())
    plan = Path(target["plan_identity"]["plan_path"])
    plan.write_text("# a plan\n\nnow different\n")

    review("abandon", job_id, "--reason", "moved on")

    record = record_of(job_dir)
    assert record["current_identity"]["plan_image"]["kind"] == "regular"
    assert record["changed_paths"] == [str(plan)]


def should_write_a_degraded_record_when_the_capture_is_unusable(review, job):
    """
    given a job whose target.json is gone
    when abandon runs
    then it records target_unavailable and omits the movement fields, which are unknowable
    """
    job_id, job_dir = job
    (job_dir / "target.json").unlink()

    result = review("abandon", job_id, "--reason", "capture lost")

    assert result.exit_code == 0, result
    record = record_of(job_dir)
    assert record["target_unavailable"] is True
    assert "current_identity" not in record
    assert "changed_paths" not in record
    assert "revoked_by_break_lease" not in record
    assert review("status", job_id).envelope["payload"]["stage"] == "abandoned"


def should_record_an_interactive_denial(review, job):
    """
    given abandon --denied-interactively
    when the record is written
    then it carries the denial kind and the user as asserter
    """
    job_id, job_dir = job

    review("abandon", job_id, "--reason", "declined the prompt", "--denied-interactively")

    record = record_of(job_dir)
    assert record["denial_kind"] == "interactive"
    assert record["asserted_by"] == "user"
