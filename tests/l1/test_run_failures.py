"""Sections 8 and 12 — a reviewer that never answered has violated no contract."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def prepared(review, plan_file_factory, outside_any_repo):
    result = review("prepare", "plan", str(plan_file_factory()), cwd=outside_any_repo)
    assert result.exit_code == 0, result
    return result.envelope["job_id"], Path(result.envelope["job_dir"])


def should_require_every_schema_property_for_a_plan_target():
    """
    given the response schema
    when the API validates it
    then `required` names every key in `properties`, which structured output demands
    """
    from codex_review import reviewer

    schema = reviewer.response_schema("plan")
    assert set(schema["required"]) == set(schema["properties"])
    assert "verified_claims" in schema["properties"]


def should_omit_verified_claims_for_a_code_target():
    """
    given a code target, which has no verified claims
    when the schema is built
    then the key is absent from properties rather than optional
    """
    from codex_review import reviewer

    schema = reviewer.response_schema("code")
    assert "verified_claims" not in schema["properties"]
    assert set(schema["required"]) == set(schema["properties"])


def should_report_codex_failure_as_unavailable_not_as_a_contract_violation(
    review, prepared, codex_reply
):
    """
    given codex exiting non-zero without producing a response
    when run handles it
    then it exits 7, not 8 — nothing was reported, so no contract was violated
    """
    job_id, _ = prepared
    result = review("run", job_id, fake={"CODEX_FAKE_RC": "1"})

    assert result.exit_code == 7, result


def should_leave_the_job_retryable_when_codex_never_answered(review, prepared):
    """
    given a failed codex invocation
    when run gives up
    then no response.json is written and the job stays at prepared
    """
    job_id, job_dir = prepared
    review("run", job_id, fake={"CODEX_FAKE_RC": "1"})

    assert not (job_dir / "response.json").exists()
    assert not (job_dir / "run-error.json").exists()
    assert review("status", job_id).envelope["payload"]["stage"] == "prepared"


def should_report_an_empty_reply_as_unavailable(review, prepared, codex_reply):
    """
    given codex exiting zero but producing no last message
    when run handles it
    then it is still exit 7 — an absent answer is not a malformed one
    """
    job_id, job_dir = prepared
    codex_reply("")

    result = review("run", job_id, fake={"CODEX_FAKE_RESPONSE": codex_reply.path})

    assert result.exit_code == 7, result
    assert not (job_dir / "response.json").exists()


def should_carry_the_reviewer_stderr_into_the_error(review, prepared):
    """
    given a failed invocation
    when the driver reports it
    then the reviewer's own diagnostics reach the caller rather than being discarded
    """
    job_id, _ = prepared
    result = review("run", job_id, fake={"CODEX_FAKE_RC": "3"})

    details = result.envelope["error"]["details"]
    assert details["returncode"] == 3


def _usage_limit_log(tmp_path, when):
    """What codex prints on stdout under --json when the account's usage limit is hit."""
    message = ("You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
               "visit https://chatgpt.com/codex/settings/usage to purchase more credits or try "
               "again at {}.".format(when))
    path = tmp_path / "usage-limit.jsonl"
    path.write_text(json.dumps({"type": "error", "message": message}) + "\n"
                    + json.dumps({"type": "turn.failed", "error": {"message": message}}) + "\n")
    return path


def should_report_when_a_dated_usage_limit_resets(review, prepared, tmp_path):
    """
    given codex refusing with a multi-day limit, "try again at Sep 19th, 2026 10:08 AM"
    when run reports the failure
    then details.retry_after is that moment, so no caller has to grep the log for it —
    a waiter that matched only the time-of-day form gave up on exactly this message
    """
    job_id, _ = prepared
    log = _usage_limit_log(tmp_path, "Sep 19th, 2026 10:08 AM")

    result = review("run", job_id, fake={"CODEX_FAKE_RC": "1", "CODEX_FAKE_STDOUT": log})

    assert result.exit_code == 7, result
    assert result.envelope["error"]["details"]["retry_after"].startswith("2026-09-19T10:08:00")


def should_report_a_time_of_day_reset_as_its_next_occurrence(review, prepared, tmp_path):
    """
    given codex refusing with "try again at 3:35 PM"
    when run reports the failure
    then retry_after is the next 15:35 — today if it is still ahead, otherwise tomorrow
    """
    from datetime import datetime, timedelta

    job_id, _ = prepared
    log = _usage_limit_log(tmp_path, "3:35 PM")

    result = review("run", job_id, fake={"CODEX_FAKE_RC": "1", "CODEX_FAKE_STDOUT": log})

    retry_after = datetime.fromisoformat(result.envelope["error"]["details"]["retry_after"])
    now = datetime.now().astimezone()
    assert (retry_after.hour, retry_after.minute) == (15, 35)
    assert now - timedelta(minutes=1) <= retry_after <= now + timedelta(days=1)


def should_roll_a_time_of_day_reset_that_has_passed_to_tomorrow():
    """
    given it is 16:00 and codex says "try again at 3:35 PM"
    when the reset is read
    then it is 15:35 tomorrow, not a moment already behind us — which a waiter would
    treat as "retry now" and hit the same wall
    """
    from datetime import datetime

    from codex_review import reviewer

    now = datetime(2026, 9, 14, 16, 0)
    reset = datetime.fromisoformat(reviewer.retry_after("try again at 3:35 PM.", now=now))

    assert (reset.year, reset.month, reset.day, reset.hour, reset.minute) == (2026, 9, 15, 15, 35)


def should_report_no_reset_time_for_a_failure_that_named_none(review, prepared):
    """
    given a codex failure whose output names no reset time
    when run reports it
    then retry_after is null rather than a guess
    """
    job_id, _ = prepared

    result = review("run", job_id, fake={"CODEX_FAKE_RC": "1"})

    assert result.envelope["error"]["details"]["retry_after"] is None
