"""Sections 4 and 5 — two-pass status: validate, then derive."""

import json
from pathlib import Path

import pytest

VALID_RESPONSE = {
    "verdict": "approve", "findings": [], "declared_scope": ["A0001"], "verified_claims": [],
}


@pytest.fixture
def job(review, plan_file_factory, outside_any_repo):
    result = review("prepare", "plan", str(plan_file_factory()), cwd=outside_any_repo)
    assert result.exit_code == 0, result
    return result.envelope["job_id"], Path(result.envelope["job_dir"])


def should_derive_prepared_for_a_freshly_published_job(review, job):
    """
    given a job carrying only target.json
    when status runs
    then the stage is prepared and the next verb is run
    """
    job_id, _ = job
    result = review("status", job_id)

    assert result.exit_code == 0, result
    assert result.envelope["payload"]["stage"] == "prepared"
    assert result.envelope["payload"]["next_verb"] == "run"


def should_derive_reviewed_once_a_report_exists(review, job, codex_reply):
    """
    given a job whose reviewer has answered
    when status runs
    then the stage is reviewed
    """
    job_id, _ = job
    codex_reply(VALID_RESPONSE)
    review("run", job_id, fake={"CODEX_FAKE_RESPONSE": codex_reply.path})

    result = review("status", job_id)

    assert result.envelope["payload"]["stage"] == "reviewed"


def should_derive_failed_from_a_run_error(review, job, codex_reply):
    """
    given a job closed by a malformed response
    when status runs
    then the stage is failed and no verb follows
    """
    job_id, _ = job
    codex_reply('{"verdict": "approve", "findings": [{"id": "F-1", '
                '"classification": "introduced", "severity": "major", "claim": "c", '
                '"failure_scenario": "f", "evidence": "e", "artifact": "A0001", '
                '"suggested_fix": "s"}], "declared_scope": ["A0001"], "verified_claims": []}')
    review("run", job_id, fake={"CODEX_FAKE_RESPONSE": codex_reply.path})

    result = review("status", job_id)

    assert result.envelope["payload"]["stage"] == "failed"
    assert result.envelope["payload"]["next_verb"] is None


def should_report_inconsistent_when_a_prerequisite_is_missing(review, job, codex_reply):
    """
    given report.json present but response.json removed
    when status validates
    then pass 1 fails, naming the violation
    """
    job_id, job_dir = job
    codex_reply(VALID_RESPONSE)
    review("run", job_id, fake={"CODEX_FAKE_RESPONSE": codex_reply.path})
    (job_dir / "response.json").unlink()

    result = review("status", job_id)

    payload = result.envelope["payload"]
    assert payload["stage"] == "inconsistent"
    assert payload["resumable"] is False
    assert "response.json" in json.dumps(payload["violations"])


def should_report_inconsistent_for_two_terminal_records(review, job):
    """
    given two terminal records in one job
    when status validates
    then mutual exclusion fails
    """
    job_id, job_dir = job
    (job_dir / "run-error.json").write_text("{}")
    (job_dir / "abandoned.json").write_text("{}")

    result = review("status", job_id)

    assert result.envelope["payload"]["stage"] == "inconsistent"


def should_report_inconsistent_when_the_capture_is_gone(review, job):
    """
    given a job whose target.json has been deleted
    when status runs
    then it is inconsistent — every artifact requires the capture
    """
    job_id, job_dir = job
    (job_dir / "target.json").unlink()

    result = review("status", job_id)

    assert result.envelope["payload"]["stage"] == "inconsistent"
    assert result.envelope["job_id"] == job_id  # addressability does not depend on target.json


def should_derive_abandoned_from_a_degraded_record(review, job):
    """
    given a job with no usable target.json closed by a record carrying target_unavailable
    when status runs
    then it derives abandoned, not inconsistent — the capture requirement is suspended
    """
    job_id, job_dir = job
    (job_dir / "target.json").unlink()
    (job_dir / "abandoned.json").write_text(json.dumps({
        "reason": "operator broke a stuck job",
        "abandoned_at": "2026-09-10T00:00:00Z",
        "asserted_by": "user",
        "target_unavailable": True,
    }))

    result = review("status", job_id)

    assert result.envelope["payload"]["stage"] == "abandoned", result
    assert result.envelope["payload"]["next_verb"] is None


def should_list_open_jobs_for_a_bare_status(review, job):
    """
    given a state root with one open job
    when status runs with no job id
    then it exits 0 with category-4 null identity and a listing
    """
    job_id, _ = job

    result = review("status")

    assert result.exit_code == 0, result
    assert result.envelope["job_id"] is None
    assert result.envelope["job_dir"] is None
    listed = [entry["job_id"] for entry in result.envelope["payload"]["open_jobs"]]
    assert job_id in listed


def should_not_list_a_directory_that_is_not_a_job(review, job, state_root):
    """
    given a state root holding a real job and a directory whose name is not a nonce —
        the experiment recorder writes one there
    when status lists open jobs
    then only the job is listed, not a phantom that can never be closed
    """
    job_id, _ = job
    (state_root / "experiments").mkdir()
    (state_root / "experiments" / "effort-ab.jsonl").write_text("{}\n")

    result = review("status")

    assert result.exit_code == 0, result
    listed = [entry["job_id"] for entry in result.envelope["payload"]["open_jobs"]]
    assert listed == [job_id], listed


def should_refuse_to_resolve_a_name_that_is_not_a_nonce(review, job, state_root):
    """
    given a directory in the state root whose name is not a nonce
    when a verb names it as a job
    then it does not resolve, as §2 says a job's name is a nonce
    """
    (state_root / "experiments").mkdir()

    for name in ("experiments", str(state_root / "experiments")):
        result = review("status", name)

        assert result.exit_code == 2, (name, result)
        assert result.envelope["error"]["code"] == "unresolvable-job", (name, result)
