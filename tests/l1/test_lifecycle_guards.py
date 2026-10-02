"""Guards the tool's own review found missing."""

import json
import subprocess
from pathlib import Path

import pytest

RESPONSE = {
    "verdict": "changes-required",
    "findings": [{"id": "F-1", "classification": "introduced", "severity": "major",
                  "claim": "c", "failure_scenario": "f", "evidence": "e",
                  "artifact": "A0001", "suggested_fix": "s"}],
    "declared_scope": ["A0001"],
    "verified_claims": [],
}


@pytest.fixture
def prepared(review, plan_file_factory, outside_any_repo):
    result = review("prepare", "plan", str(plan_file_factory()), cwd=outside_any_repo)
    assert result.exit_code == 0, result
    return result.envelope["job_id"], Path(result.envelope["job_dir"])


def should_refuse_run_on_a_job_already_closed(review, prepared, codex_reply):
    """
    given a job closed by abandon
    when run is invoked
    then it refuses, naming the record — terminal state is checked before anything else
    """
    job_id, job_dir = prepared
    review("abandon", job_id, "--reason", "changed my mind")
    codex_reply(RESPONSE)

    result = review("run", job_id, fake={"CODEX_FAKE_RESPONSE": codex_reply.path})

    assert result.exit_code != 0
    assert "abandoned.json" in json.dumps(result.envelope["error"])
    assert not (job_dir / "response.json").exists()


def should_not_accept_a_previous_invocations_reply(review, prepared, codex_reply):
    """
    given a stale last-message from an earlier failed invocation
    when a retry produces no new answer
    then the stale text is not adopted as this run's response
    """
    job_id, job_dir = prepared
    stale = job_dir / "staging"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "last-message.txt").write_text(json.dumps(RESPONSE))

    result = review("run", job_id, fake={"CODEX_FAKE_RC": "1"})

    assert result.exit_code == 7, result
    assert not (job_dir / "response.json").exists()


def should_require_an_artifact_on_an_introduced_finding(review, prepared, codex_reply):
    """
    given an introduced finding mapping to no artifact
    when the response is validated
    then it is refused — only a pre-existing finding may map to none
    """
    job_id, _ = prepared
    unmapped = json.loads(json.dumps(RESPONSE))
    unmapped["findings"][0]["artifact"] = None
    codex_reply(unmapped)

    result = review("run", job_id, fake={"CODEX_FAKE_RESPONSE": codex_reply.path})

    assert result.exit_code == 8, result
    assert result.envelope["error"]["code"] == "introduced-without-artifact"


def should_record_movement_for_a_code_target(review, make_repo):
    """
    given a code job whose captured file has since changed
    when it is abandoned
    then the record names the movement, as it does for a plan
    """
    repo = make_repo({"a.py": "A = 1\n"})
    base = subprocess.run(("git", "rev-parse", "HEAD"), cwd=str(repo),
                          capture_output=True, text=True).stdout.strip()
    (repo / "a.py").write_text("A = 2\n")
    subprocess.run(("git", "add", "-A"), cwd=str(repo), check=True, capture_output=True)
    subprocess.run(("git", "commit", "-qm", "two"), cwd=str(repo), check=True,
                   capture_output=True)
    prepared_result = review("prepare", "code", "--base", base, cwd=repo)
    job_id = prepared_result.envelope["job_id"]
    job_dir = Path(prepared_result.envelope["job_dir"])

    (repo / "a.py").write_text("A = 3\n")
    review("abandon", job_id, "--reason", "moved on")

    record = json.loads((job_dir / "abandoned.json").read_text())
    assert record["current_identity"]["worktree_digest"]
    assert record["changed_paths"]


def should_answer_an_unbuilt_verb_with_an_envelope(review, prepared):
    """
    given a verb the milestone has not built
    when it is invoked
    then it still answers with an envelope rather than a traceback
    """
    job_id, _ = prepared
    result = review("record-probes", job_id)

    assert result.envelope is not None, result
    assert result.envelope["verb"] == "record-probes"
    assert "error" in result.envelope


def should_treat_equivalent_spellings_as_the_same_command_script(review, prepared):
    """
    given a command script named with a redundant path prefix
    when it is sealed
    then it still gets the complete-preview treatment
    """
    job_id, job_dir = prepared
    staging = job_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "verify.sh").write_text("echo " + "x" * 500 + "\n")

    result = review("seal", job_id, "./staging/verify.sh", "--bytes", "100")

    assert result.exit_code == 0, result
    assert result.envelope["payload"]["truncated"] is False
    assert result.envelope["payload"]["source_path"] == "staging/verify.sh"
