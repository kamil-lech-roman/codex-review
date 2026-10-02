"""Sections 3, 8 and 12 — invoking the reviewer and validating its response."""

import json
from pathlib import Path

import pytest

VALID_RESPONSE = {
    "verdict": "changes-required",
    "findings": [
        {
            "id": "F-1",
            "classification": "introduced",
            "severity": "major",
            "claim": "The digest is compared against the wrong domain.",
            "failure_scenario": "A mode-only change leaves the hashes equal, so it passes.",
            "evidence": "section 5 line 630",
            "artifact": "A0001",
            "suggested_fix": "Compare whole images.",
        }
    ],
    "declared_scope": ["A0001"],
    "verified_claims": [
        {"claim": "X holds", "result": "not-verifiable", "evidence": "no repo", "finding_id": None}
    ],
}


@pytest.fixture
def prepared_job(review, plan_file_factory, outside_any_repo):
    result = review("prepare", "plan", str(plan_file_factory()), cwd=outside_any_repo)
    assert result.exit_code == 0, result
    return result.envelope["job_id"], Path(result.envelope["job_dir"])


def should_refuse_run_with_no_job_argument(review):
    """
    given `run` with no job
    when the driver runs
    then it exits 2 with null identity
    """
    result = review("run")

    assert result.exit_code == 2
    assert result.envelope["job_id"] is None


def should_refuse_a_job_that_does_not_exist(review):
    """
    given a nonce naming no published job
    when `run` resolves it
    then it exits 2 with null identity, because nothing was resolved
    """
    result = review("run", "0123456789abcdef")

    assert result.exit_code == 2
    assert result.envelope["job_id"] is None
    assert result.envelope["job_dir"] is None


def should_invoke_codex_with_the_mandatory_flags(
    review, prepared_job, codex_argv, codex_reply
):
    """
    given a prepared job
    when `run` invokes the reviewer
    then every flag section 3 marks mandatory is present
    """
    job_id, job_dir = prepared_job
    codex_reply(VALID_RESPONSE)

    review("run", job_id, fake={
        "CODEX_FAKE_ARGV": codex_argv, "CODEX_FAKE_RESPONSE": codex_reply.path
    })

    argv = json.loads(codex_argv.read_text())
    assert argv[0] == "exec"
    assert "-s" in argv and argv[argv.index("-s") + 1] == "read-only"
    assert "-C" in argv and argv[argv.index("-C") + 1] == str(job_dir)
    for flag in ("--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                 "--ignore-rules", "--json", "--output-schema", "-o"):
        assert flag in argv, flag
    assert "review" not in argv  # plain `exec`, never `exec review` (§3)


def should_set_effort_through_the_config_key(review, prepared_job, codex_argv, codex_reply):
    """
    given a run
    when codex is invoked
    then reasoning effort is passed as -c model_reasoning_effort, there being no --effort flag
    """
    job_id, _ = prepared_job
    codex_reply(VALID_RESPONSE)

    review("run", job_id, fake={
        "CODEX_FAKE_ARGV": codex_argv, "CODEX_FAKE_RESPONSE": codex_reply.path
    })

    argv = json.loads(codex_argv.read_text())
    assert any(a.startswith("model_reasoning_effort=") for a in argv)


def should_write_a_report_for_a_valid_response(review, prepared_job, codex_reply):
    """
    given a well-formed response
    when `run` validates it
    then it exits 0 and writes both response.json and report.json
    """
    job_id, job_dir = prepared_job
    codex_reply(VALID_RESPONSE)

    result = review("run", job_id, fake={"CODEX_FAKE_RESPONSE": codex_reply.path})

    assert result.exit_code == 0, result
    assert (job_dir / "response.json").is_file()
    assert (job_dir / "report.json").is_file()
    assert result.envelope["payload"]["verdict"] == "changes-required"


def should_persist_the_raw_response_before_validating_it(review, prepared_job, codex_reply):
    """
    given a response that fails validation
    when `run` rejects it
    then response.json still holds the reviewer's text, and run-error.json closes the job
    """
    job_id, job_dir = prepared_job
    codex_reply({"verdict": "approve", "findings": [], "declared_scope": ["A0001"],
                 "verified_claims": [], "unexpected": "x"})
    codex_reply('{"verdict": "approve", "findings": [{"id": "F-1", '
                '"classification": "introduced", "severity": "major", "claim": "c", '
                '"failure_scenario": "f", "evidence": "e", "artifact": "A0001", '
                '"suggested_fix": "s"}], "declared_scope": ["A0001"], "verified_claims": []}')

    result = review("run", job_id, fake={"CODEX_FAKE_RESPONSE": codex_reply.path})

    assert result.exit_code == 8, result
    assert (job_dir / "response.json").is_file()
    assert (job_dir / "run-error.json").is_file()
    assert not (job_dir / "report.json").exists()


def should_not_hang_when_the_caller_leaves_stdin_open(
        prepared_job, fake_codex, state_root, codex_reply):
    """
    given a reviewer that reads stdin to its end before answering, as codex does
    when run is started with a stdin that stays open — a pipe, a socket, a CI runner
    then it still completes, because the driver gives the reviewer no stdin to wait on
    """
    import os
    import subprocess

    job_id, _job_dir = prepared_job
    codex_reply(VALID_RESPONSE)
    review_sh = Path(__file__).resolve().parents[2] / "review.sh"
    env = dict(os.environ, CODEX_REVIEW_STATE=str(state_root), CODEX_BIN=str(fake_codex),
               CODEX_FAKE_RESPONSE=str(codex_reply.path), CODEX_FAKE_READ_STDIN="1")

    process = subprocess.Popen([str(review_sh), "run", job_id], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               env=env)
    try:
        process.wait(timeout=15)
        hung = False
    except subprocess.TimeoutExpired:
        hung = True
        process.kill()
        process.wait()
    finally:
        process.stdin.close()  # EOF releases any reviewer still blocked on it

    assert not hung, "run blocked on the reviewer reading an open stdin"
    assert process.returncode == 0, process.stderr.read()
