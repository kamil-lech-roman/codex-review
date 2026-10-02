"""Sections 8 and 11 — recording what the apply actually did, then verifying it."""

import hashlib
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


def probe_a_job(review, plan_file_factory, outside_any_repo, codex_reply, *prepare_args):
    """A plan job carried to `probed` with one accepted finding, ready for record-apply."""
    plan = plan_file_factory("# a plan\n")
    prepared = review("prepare", "plan", str(plan), *prepare_args, cwd=outside_any_repo)
    job_id = prepared.envelope["job_id"]
    job_dir = Path(prepared.envelope["job_dir"])
    codex_reply(RESPONSE)
    review("run", job_id, fake={"CODEX_FAKE_RESPONSE": codex_reply.path})

    (job_dir / "staging").mkdir(parents=True, exist_ok=True)
    (job_dir / "staging" / "probe-F-1.sh").write_text("echo checking\n")
    command = review("seal", job_id, "staging/probe-F-1.sh").envelope["payload"]["evidence_id"]
    (job_dir / "probes").mkdir(parents=True, exist_ok=True)
    (job_dir / "probes" / "F-1.out").write_text("observed\n")
    (job_dir / "probes" / "F-1.rc").write_text("0\n")
    out = review("seal", job_id, "probes/F-1.out").envelope["payload"]["evidence_id"]
    rc = review("seal", job_id, "probes/F-1.rc").envelope["payload"]["evidence_id"]

    submission = {
        "report_sha256": hashlib.sha256((job_dir / "report.json").read_bytes()).hexdigest(),
        "annotations": [{
            "finding_id": "F-1", "probe_execution": "executed",
            "command_evidence_id": command, "output_evidence_id": out, "rc_evidence_id": rc,
            "read": "confirmed-introduced", "disposition": "accepted",
            "fix_paths": [plan.name],
        }],
    }
    path = job_dir / "staging" / "probes.json"
    path.write_text(json.dumps(submission))
    recorded = review("record-probes", job_id, "--probes-file", str(path))
    assert recorded.exit_code == 0, recorded
    return job_id, job_dir, plan


@pytest.fixture
def probed(review, plan_file_factory, outside_any_repo, codex_reply):
    return probe_a_job(review, plan_file_factory, outside_any_repo, codex_reply)


@pytest.fixture
def probed_with_verify(review, plan_file_factory, outside_any_repo, codex_reply):
    """The same job, with a verify command configured — a denial is only reachable
    where a command exists that could have been denied, and `not-applicable` is only the
    derived result where one exists to be skipped."""
    return probe_a_job(review, plan_file_factory, outside_any_repo, codex_reply,
                       "--verify", "true")


def apply_payload(job_dir, results):
    return {
        "report_sha256": hashlib.sha256((job_dir / "report.json").read_bytes()).hexdigest(),
        "probes_sha256": hashlib.sha256((job_dir / "probes.json").read_bytes()).hexdigest(),
        "results": results,
    }


def submit_apply(review, job_id, job_dir, results, extra=None):
    payload = apply_payload(job_dir, results)
    payload.update(extra or {})
    path = job_dir / "staging" / "apply.json"
    path.write_text(json.dumps(payload))
    return review("record-apply", job_id, "--apply-file", str(path))


def should_record_a_completed_apply(review, probed):
    """
    given an accepted fix that landed inside its declared paths
    when the apply is recorded
    then apply.json reports applied-complete and the stage becomes applied-unverified
    """
    job_id, job_dir, plan = probed
    plan.write_text("# a plan\n\nfixed.\n")

    result = submit_apply(review, job_id, job_dir,
                          [{"finding_id": "F-1", "result": "applied"}])

    assert result.exit_code == 0, result
    recorded = json.loads((job_dir / "apply.json").read_text())
    assert recorded["application_result"] == "applied-complete"
    assert recorded["changed_paths"] == [str(plan)]
    assert review("status", job_id).envelope["payload"]["stage"] == "applied-unverified"


def should_report_the_apply_digest_that_record_verification_requires(review, probed):
    """
    given a recorded apply
    when the envelope is read
    then it carries the sha256 of the apply.json it wrote, which the verification payload
    must quote
    """
    job_id, job_dir, plan = probed
    plan.write_text("# a plan\n\nfixed.\n")

    result = submit_apply(review, job_id, job_dir,
                          [{"finding_id": "F-1", "result": "applied"}])

    expected = hashlib.sha256((job_dir / "apply.json").read_bytes()).hexdigest()
    assert result.envelope["payload"]["apply_sha256"] == expected


def should_normalise_an_applied_claim_that_moved_nothing(review, probed):
    """
    given a finding claiming applied while none of its own paths changed
    when the apply is recorded
    then it normalises to no-change and the apply is incomplete
    """
    job_id, job_dir, _plan = probed

    result = submit_apply(review, job_id, job_dir,
                          [{"finding_id": "F-1", "result": "applied"}])

    assert result.exit_code == 11, result
    recorded = json.loads((job_dir / "apply.json").read_text())
    assert recorded["application_result"] == "apply-incomplete"


def should_reject_a_fabricated_verification_key(review, probed):
    """
    given an apply payload carrying a verification result
    when it is recorded
    then it is refused — verification has not run yet, so it could only be fabricated
    """
    job_id, job_dir, plan = probed
    plan.write_text("# a plan\n\nfixed.\n")

    result = submit_apply(review, job_id, job_dir,
                          [{"finding_id": "F-1", "result": "applied"}],
                          extra={"verification": {"result": "passed"}})

    assert result.exit_code == 8, result


def should_refuse_a_change_nothing_accounts_for(review, probed):
    """
    given a declared path that moved while its only declarer reports no-change
    when the apply is recorded
    then it is refused — nothing accounts for those bytes
    """
    job_id, job_dir, plan = probed
    plan.write_text("# a plan\n\nsomething moved.\n")

    result = submit_apply(review, job_id, job_dir,
                          [{"finding_id": "F-1", "result": "no-change"}])

    assert result.exit_code == 8, result


def should_treat_a_denied_attempt_that_moved_bytes_as_a_partial_fix(review, probed):
    """
    given a denied attempt whose declared path nonetheless moved
    when the apply is recorded
    then it is apply-incomplete, not a contract violation
    """
    job_id, job_dir, plan = probed
    plan.write_text("# a plan\n\npartly fixed.\n")

    result = submit_apply(review, job_id, job_dir, [{
        "finding_id": "F-1", "result": "denied",
        "denial_kind": "interactive", "asserted_by": "orchestrator",
    }])

    assert result.exit_code == 11, result
    assert json.loads((job_dir / "apply.json").read_text())["changed_paths"] == [str(plan)]


def should_close_the_job_on_a_scope_violation(review, make_repo, codex_reply):
    """
    given a code target and a change outside every accepted fix path
    when the apply is recorded
    then apply-error.json closes the job and apply.json is never written

    A plan target cannot reach this: its delta universe is the plan file alone (section 3),
    which is also its only legal fix path, so the union always covers it.
    """
    import subprocess

    repo = make_repo({"a.py": "A = 1\n", "other.py": "B = 1\n"})
    base = subprocess.run(("git", "rev-parse", "HEAD"), cwd=str(repo),
                          capture_output=True, text=True).stdout.strip()
    (repo / "a.py").write_text("A = 2\n")
    subprocess.run(("git", "add", "-A"), cwd=str(repo), check=True, capture_output=True)
    subprocess.run(("git", "commit", "-qm", "two"), cwd=str(repo), check=True,
                   capture_output=True)

    prepared = review("prepare", "code", "--base", base, cwd=repo)
    job_id = prepared.envelope["job_id"]
    job_dir = Path(prepared.envelope["job_dir"])
    codex_reply({
        "verdict": "changes-required",
        "findings": [{"id": "F-1", "classification": "introduced", "severity": "major",
                      "claim": "c", "failure_scenario": "f", "evidence": "e",
                      "artifact": "A0001", "suggested_fix": "s"}],
        "declared_scope": ["A0001"],
    })
    assert review("run", job_id,
                  fake={"CODEX_FAKE_RESPONSE": codex_reply.path}).exit_code == 0

    (job_dir / "staging").mkdir(parents=True, exist_ok=True)
    (job_dir / "staging" / "probe-F-1.sh").write_text("echo checking\n")
    command = review("seal", job_id, "staging/probe-F-1.sh").envelope["payload"]["evidence_id"]
    (job_dir / "probes").mkdir(parents=True, exist_ok=True)
    (job_dir / "probes" / "F-1.out").write_text("observed\n")
    (job_dir / "probes" / "F-1.rc").write_text("0\n")
    out = review("seal", job_id, "probes/F-1.out").envelope["payload"]["evidence_id"]
    rc = review("seal", job_id, "probes/F-1.rc").envelope["payload"]["evidence_id"]
    probes_path = job_dir / "staging" / "probes.json"
    probes_path.write_text(json.dumps({
        "report_sha256": hashlib.sha256((job_dir / "report.json").read_bytes()).hexdigest(),
        "annotations": [{"finding_id": "F-1", "probe_execution": "executed",
                         "command_evidence_id": command, "output_evidence_id": out,
                         "rc_evidence_id": rc, "read": "confirmed-introduced",
                         "disposition": "accepted", "fix_paths": ["a.py"]}],
    }))
    assert review("record-probes", job_id,
                  "--probes-file", str(probes_path)).exit_code == 0

    # The fix lands, but so does an edit to a path nobody declared.
    (repo / "a.py").write_text("A = 3\n")
    (repo / "other.py").write_text("B = 99\n")

    result = submit_apply(review, job_id, job_dir,
                          [{"finding_id": "F-1", "result": "applied"}])

    assert result.exit_code == 10, result
    assert (job_dir / "apply-error.json").is_file()
    assert not (job_dir / "apply.json").exists()
    assert review("status", job_id).envelope["payload"]["stage"] == "failed"


def should_close_with_not_applicable_when_nothing_landed(review, probed_with_verify):
    """
    given a verify command configured but no fix landed
    when verification is recorded
    then the result is not-applicable — derived from the job, and the submission agrees
    """
    job_id, job_dir, _plan = probed_with_verify
    submit_apply(review, job_id, job_dir,
                 [{"finding_id": "F-1", "result": "no-change", "note": "could not fix"}])
    payload = {
        "apply_sha256": hashlib.sha256((job_dir / "apply.json").read_bytes()).hexdigest(),
        "verification": {"result": "not-applicable"},
    }
    path = job_dir / "staging" / "verification.json"
    path.write_text(json.dumps(payload))

    result = review("record-verification", job_id, "--verification-file", str(path))

    assert (job_dir / "verification.json").is_file(), result
    recorded = json.loads((job_dir / "verification.json").read_text())
    assert recorded["verification_result"] == "not-applicable"
    assert review("status", job_id).envelope["payload"]["stage"] == "applied"


def should_refuse_not_applicable_when_no_verify_command_makes_it_not_run_explicitly(
        review, probed):
    """
    given a job prepared with no verify command, where nothing landed
    when the operator submits `not-applicable`
    then it is refused naming both results, rather than recorded as `not-run-explicitly`
    """
    job_id, job_dir, _plan = probed
    submit_apply(review, job_id, job_dir,
                 [{"finding_id": "F-1", "result": "no-change", "note": "could not fix"}])
    payload = {
        "apply_sha256": hashlib.sha256((job_dir / "apply.json").read_bytes()).hexdigest(),
        "verification": {"result": "not-applicable"},
    }
    path = job_dir / "staging" / "verification.json"
    path.write_text(json.dumps(payload))

    result = review("record-verification", job_id, "--verification-file", str(path))

    assert result.exit_code == 8, result
    error = result.envelope["error"]
    assert error["code"] == "verification-result-contradicted", result
    assert error["details"]["submitted"] == "not-applicable", result
    assert error["details"]["derived"] == "not-run-explicitly", result
    assert not (job_dir / "verification.json").exists(), result


def should_refuse_not_run_explicitly_when_a_verify_command_exists_but_nothing_landed(
        review, probed_with_verify):
    """
    given a job with a verify command, where nothing landed
    when the operator submits `not-run-explicitly`
    then it is refused naming both results, since `not-applicable` is what the bytes say
    """
    job_id, job_dir, _plan = probed_with_verify
    submit_apply(review, job_id, job_dir,
                 [{"finding_id": "F-1", "result": "no-change", "note": "could not fix"}])
    path = job_dir / "staging" / "verification.json"
    path.write_text(json.dumps({
        "apply_sha256": hashlib.sha256((job_dir / "apply.json").read_bytes()).hexdigest(),
        "verification": {"result": "not-run-explicitly"},
    }))

    result = review("record-verification", job_id, "--verification-file", str(path))

    assert result.exit_code == 8, result
    details = result.envelope["error"]["details"]
    assert (details["submitted"], details["derived"]) == ("not-run-explicitly",
                                                         "not-applicable"), result
    assert not (job_dir / "verification.json").exists(), result


def should_refuse_passed_when_no_verify_command_exists(review, probed):
    """
    given a job with no verify command, where a fix landed
    when the operator submits `passed`
    then it is refused naming both results, rather than recorded as `not-run-explicitly`
    """
    job_id, job_dir, plan = probed
    plan.write_text("# a plan\n\nfixed.\n")
    submit_apply(review, job_id, job_dir, [{"finding_id": "F-1", "result": "applied"}])
    path = job_dir / "staging" / "verification.json"
    path.write_text(json.dumps({
        "apply_sha256": hashlib.sha256((job_dir / "apply.json").read_bytes()).hexdigest(),
        "verification": {"result": "passed"},
    }))

    result = review("record-verification", job_id, "--verification-file", str(path))

    assert result.exit_code == 8, result
    details = result.envelope["error"]["details"]
    assert (details["submitted"], details["derived"]) == ("passed", "not-run-explicitly"), result
    assert not (job_dir / "verification.json").exists(), result


def record_verification(review, job_id, job_dir, block, raw_result=None):
    """Submit `block`; `raw_result` splices literal JSON text in place of its `result`."""
    block = dict(block)
    if raw_result is not None:
        block["result"] = "@@raw@@"
    text = json.dumps({
        "apply_sha256": hashlib.sha256((job_dir / "apply.json").read_bytes()).hexdigest(),
        "verification": block,
    })
    if raw_result is not None:
        text = text.replace('"@@raw@@"', raw_result)
    path = job_dir / "staging" / "verification.json"
    path.write_text(text)
    return review("record-verification", job_id, "--verification-file", str(path))


def nothing_landed(review, job_id, job_dir):
    return submit_apply(review, job_id, job_dir,
                        [{"finding_id": "F-1", "result": "no-change", "note": "could not fix"}])


def should_close_with_not_run_explicitly_when_no_verify_command_and_nothing_landed(
        review, probed):
    """
    given a job with no verify command, where nothing landed
    when the derived result `not-run-explicitly` is submitted
    then the job closes with it — no verify command decides before nothing landing does
    """
    job_id, job_dir, _plan = probed
    nothing_landed(review, job_id, job_dir)

    result = record_verification(review, job_id, job_dir, {"result": "not-run-explicitly"})

    assert result.exit_code == 11, result
    recorded = json.loads((job_dir / "verification.json").read_text())
    assert recorded["verification_result"] == "not-run-explicitly"


@pytest.mark.parametrize("block", [
    {}, {"result": None}, {"status": "not-applicable"},
], ids=["no-block-fields", "null-result", "mistyped-key"])
def should_close_with_the_derived_result_when_the_result_is_omitted(review, probed, block):
    """
    given a calling agent delivering partial results, with no `result` to submit
    when verification is recorded where the derivation decides alone
    then the job closes with the derived result — an omission contradicts nothing
    """
    job_id, job_dir, _plan = probed
    nothing_landed(review, job_id, job_dir)

    result = record_verification(review, job_id, job_dir, block)

    assert result.exit_code == 11, result
    recorded = json.loads((job_dir / "verification.json").read_text())
    assert recorded["verification_result"] == "not-run-explicitly"


@pytest.mark.parametrize("raw_result", ["5", "[1]", '{"a": 1}', "true", "NaN", "Infinity"])
def should_refuse_a_result_that_is_not_a_string_without_breaking_the_envelope(
        review, probed, raw_result):
    """
    given a submitted result that is not a string, including JSON that Python accepts
    but strict parsers reject
    when verification is recorded
    then it is refused and the envelope is still strictly valid JSON
    """
    job_id, job_dir, _plan = probed
    nothing_landed(review, job_id, job_dir)

    def reject_constant(name):
        raise AssertionError("non-JSON constant in the envelope: " + name)

    result = record_verification(review, job_id, job_dir, {}, raw_result=raw_result)

    assert result.exit_code == 8, result
    envelope = json.loads(result.stdout, parse_constant=reject_constant)
    assert envelope["error"]["code"] == "verification-result-contradicted", result
    assert envelope["error"]["details"]["submitted"] is None, result
    assert not (job_dir / "verification.json").exists(), result


def should_refuse_not_applicable_when_a_verify_command_ran_naming_what_is_allowed(
        review, probed_with_verify):
    """
    given a job with a verify command, where a fix landed
    when the operator submits `not-applicable`
    then it is refused with the same code as the other tiers, naming what the derivation
    allows, rather than a different code that says the command ran
    """
    job_id, job_dir, plan = probed_with_verify
    plan.write_text("# a plan\n\nfixed.\n")
    submit_apply(review, job_id, job_dir, [{"finding_id": "F-1", "result": "applied"}])

    result = record_verification(review, job_id, job_dir, {"result": "not-applicable"})

    assert result.exit_code == 8, result
    error = result.envelope["error"]
    assert error["code"] == "verification-result-contradicted", result
    assert error["details"]["submitted"] == "not-applicable", result
    assert error["details"]["allowed"] == ["passed", "failed", "denied"], result
    assert not (job_dir / "verification.json").exists(), result


def should_leave_the_job_open_and_journal_the_attempt_when_a_result_is_refused(
        review, probed):
    """
    given a refused contradicting submission
    when the derived result is then submitted
    then the refusal had recorded no verification, only an attempt, and the job closes
    """
    job_id, job_dir, _plan = probed
    nothing_landed(review, job_id, job_dir)

    refused = record_verification(review, job_id, job_dir, {"result": "not-applicable"})

    assert refused.exit_code == 8, refused
    assert not (job_dir / "verification.json").exists(), refused
    assert (job_dir / "attempts").is_dir(), refused
    status = review("status", job_id).envelope["payload"]
    assert status["stage"] == "applied-unverified", status
    corrected = record_verification(review, job_id, job_dir,
                                    {"result": refused.envelope["error"]["details"]["derived"]})
    assert corrected.exit_code == 11, corrected
    assert (job_dir / "verification.json").is_file(), corrected


def should_report_not_run_explicitly_without_a_verify_command(review, probed):
    """
    given no verify_command configured and a fix that landed
    when verification is recorded
    then configuration decides before any runtime outcome
    """
    job_id, job_dir, plan = probed
    plan.write_text("# a plan\n\nfixed.\n")
    submit_apply(review, job_id, job_dir, [{"finding_id": "F-1", "result": "applied"}])
    payload = {
        "apply_sha256": hashlib.sha256((job_dir / "apply.json").read_bytes()).hexdigest(),
        "verification": {"result": "not-run-explicitly"},
    }
    path = job_dir / "staging" / "verification.json"
    path.write_text(json.dumps(payload))

    result = review("record-verification", job_id, "--verification-file", str(path))

    assert result.exit_code == 0, result
    recorded = json.loads((job_dir / "verification.json").read_text())
    assert recorded["verification_result"] == "not-run-explicitly"
    assert recorded["application_result"] == "applied-complete"
    assert recorded["probe_completion"] == "complete"


def should_refuse_a_submission_that_is_not_an_object(review, probed, tmp_path):
    """
    given a submission that is valid JSON but an array
    when a recording verb consumes it at the stage that reads its fields
    then it refuses with an envelope, rather than raising on the first field access
    """
    job_id, _job_dir, _plan = probed
    payload = tmp_path / "array.json"
    payload.write_text("[]\n")

    result = review("record-apply", job_id, "--apply-file", str(payload))

    assert result.envelope is not None, result
    assert result.envelope["error"]["code"] == "bad-payload", result
    assert result.stderr == "", result.stderr


def should_refuse_a_verification_submission_that_is_not_an_object(review, probed, tmp_path):
    """
    given an applied job and a verification submission that is an array
    when verification is recorded
    then it refuses with an envelope
    """
    job_id, job_dir, plan = probed
    plan.write_text("# a plan\n\nfixed.\n")
    submit_apply(review, job_id, job_dir, [{"finding_id": "F-1", "result": "applied"}])
    payload = tmp_path / "array.json"
    payload.write_text("[]\n")

    result = review("record-verification", job_id, "--verification-file", str(payload))

    assert result.envelope is not None, result
    assert result.envelope["error"]["code"] == "bad-payload", result
    assert result.stderr == "", result.stderr


def should_refuse_a_verification_submission_with_no_verification_block(review, probed):
    """
    given a submission whose result sits at the top level instead of in a block
    when verification is recorded
    then it refuses, rather than reading an absent block as an empty one
    """
    job_id, job_dir, plan = probed
    plan.write_text("# a plan\n\nfixed.\n")
    submit_apply(review, job_id, job_dir, [{"finding_id": "F-1", "result": "applied"}])
    payload = {
        "apply_sha256": hashlib.sha256((job_dir / "apply.json").read_bytes()).hexdigest(),
        "result": "not-run-explicitly",
    }
    path = job_dir / "staging" / "verification.json"
    path.write_text(json.dumps(payload))

    result = review("record-verification", job_id, "--verification-file", str(path))

    assert result.exit_code == 8, result
    assert result.envelope["error"]["code"] == "missing-verification-block", result
    assert not (job_dir / "verification.json").exists(), result


def should_refuse_a_denial_whose_kind_is_not_one_of_the_two(review, probed_with_verify):
    """
    given a denied verification naming a kind the contract does not define
    when it is recorded
    then it refuses, as the same two fields are refused on a probe annotation
    """
    job_id, job_dir, plan = probed_with_verify
    plan.write_text("# a plan\n\nfixed.\n")
    submit_apply(review, job_id, job_dir, [{"finding_id": "F-1", "result": "applied"}])
    (job_dir / "staging" / "verify.sh").write_text("true\n")
    command = review("seal", job_id, "staging/verify.sh").envelope["payload"]["evidence_id"]
    payload = {
        "apply_sha256": hashlib.sha256((job_dir / "apply.json").read_bytes()).hexdigest(),
        "verification": {"result": "denied", "command_evidence_id": command,
                         "denial_kind": "garbage", "asserted_by": "user"},
    }
    path = job_dir / "staging" / "verification.json"
    path.write_text(json.dumps(payload))

    result = review("record-verification", job_id, "--verification-file", str(path))

    assert result.exit_code == 8, result
    assert result.envelope["error"]["code"] == "bad-denial-kind", result


def should_refuse_a_denial_that_names_no_asserter(review, probed_with_verify):
    """
    given a denied verification whose asserter is an empty string
    when it is recorded
    then it refuses: present is not the same as attributed
    """
    job_id, job_dir, plan = probed_with_verify
    plan.write_text("# a plan\n\nfixed.\n")
    submit_apply(review, job_id, job_dir, [{"finding_id": "F-1", "result": "applied"}])
    (job_dir / "staging" / "verify.sh").write_text("true\n")
    command = review("seal", job_id, "staging/verify.sh").envelope["payload"]["evidence_id"]
    payload = {
        "apply_sha256": hashlib.sha256((job_dir / "apply.json").read_bytes()).hexdigest(),
        "verification": {"result": "denied", "command_evidence_id": command,
                         "denial_kind": "policy", "asserted_by": ""},
    }
    path = job_dir / "staging" / "verification.json"
    path.write_text(json.dumps(payload))

    result = review("record-verification", job_id, "--verification-file", str(path))

    assert result.exit_code == 8, result
    assert result.envelope["error"]["code"] == "denial-unattributed", result


def should_keep_the_evidence_that_justified_the_verification(review, probed_with_verify):
    """
    given a denied verification carrying its command evidence
    when it is recorded
    then the terminal record names that evidence, not only the outcome
    """
    job_id, job_dir, plan = probed_with_verify
    plan.write_text("# a plan\n\nfixed.\n")
    submit_apply(review, job_id, job_dir, [{"finding_id": "F-1", "result": "applied"}])
    (job_dir / "staging" / "verify.sh").write_text("true\n")
    command = review("seal", job_id, "staging/verify.sh").envelope["payload"]["evidence_id"]
    payload = {
        "apply_sha256": hashlib.sha256((job_dir / "apply.json").read_bytes()).hexdigest(),
        "verification": {"result": "denied", "command_evidence_id": command,
                         "denial_kind": "interactive", "asserted_by": "user"},
    }
    path = job_dir / "staging" / "verification.json"
    path.write_text(json.dumps(payload))

    review("record-verification", job_id, "--verification-file", str(path))

    recorded = json.loads((job_dir / "verification.json").read_text())
    assert recorded["verification_evidence"]["command_evidence_id"] == command, recorded
    assert recorded["verification_evidence"]["denial_kind"] == "interactive", recorded
    assert recorded["verification_evidence"]["asserted_by"] == "user", recorded


def code_job_with_a_staged_defect(review, make_repo, codex_reply):
    """A code job whose index holds the defect and whose worktree already holds the fix,
    so that `git add` is the whole of the correction."""
    repo = make_repo({"f.py": "def d(a, b):\n    return a / b\n"})
    (repo / "f.py").write_text("def d(a, b):\n    return a / 0\n")
    subprocess.run(("git", "add", "f.py"), cwd=str(repo), check=True, capture_output=True)
    (repo / "f.py").write_text("def d(a, b):\n    return a / b\n")

    prepared = review("prepare", "code", "--uncommitted", cwd=repo)
    assert prepared.exit_code == 0, prepared
    job_id = prepared.envelope["job_id"]
    job_dir = Path(prepared.envelope["job_dir"])
    captured = json.loads((job_dir / "target.json").read_text())
    reply = dict(RESPONSE, declared_scope=list(captured["artifacts"]))
    codex_reply(reply)
    ran = review("run", job_id, fake={"CODEX_FAKE_RESPONSE": codex_reply.path})
    assert ran.exit_code == 0, ran

    (job_dir / "staging").mkdir(parents=True, exist_ok=True)
    (job_dir / "staging" / "probe-F-1.sh").write_text("echo checking\n")
    command = review("seal", job_id, "staging/probe-F-1.sh").envelope["payload"]["evidence_id"]
    (job_dir / "probes").mkdir(parents=True, exist_ok=True)
    (job_dir / "probes" / "F-1.out").write_text("observed\n")
    (job_dir / "probes" / "F-1.rc").write_text("0\n")
    out = review("seal", job_id, "probes/F-1.out").envelope["payload"]["evidence_id"]
    rc = review("seal", job_id, "probes/F-1.rc").envelope["payload"]["evidence_id"]
    submission = {
        "report_sha256": hashlib.sha256((job_dir / "report.json").read_bytes()).hexdigest(),
        "annotations": [{
            "finding_id": "F-1", "probe_execution": "executed",
            "command_evidence_id": command, "output_evidence_id": out, "rc_evidence_id": rc,
            "read": "confirmed-introduced", "disposition": "accepted", "fix_paths": ["f.py"],
        }],
    }
    path = job_dir / "staging" / "probes.json"
    path.write_text(json.dumps(submission))
    assert review("record-probes", job_id, "--probes-file", str(path)).exit_code == 0
    return repo, job_id, job_dir


def should_see_a_correction_that_lands_in_the_index(review, make_repo, codex_reply):
    """
    given a staged defect whose correction is already in the worktree
    when the fix is staged and the apply is recorded
    then reconciliation sees the path move — staging it is what fixing it means here
    """
    repo, job_id, job_dir = code_job_with_a_staged_defect(review, make_repo, codex_reply)
    subprocess.run(("git", "add", "f.py"), cwd=str(repo), check=True, capture_output=True)

    result = submit_apply(review, job_id, job_dir,
                          [{"finding_id": "F-1", "result": "applied"}])

    assert result.exit_code == 0, result
    assert result.envelope["payload"]["changed_paths"] == [str(repo / "f.py")], result
    assert result.envelope["payload"]["application_result"] == "applied-complete", result


def should_read_a_legacy_baseline_that_holds_a_path_called_projections(
        review, make_repo, codex_reply):
    """
    given a baseline bound before the index was sampled, over a repository whose root
        holds a file named `projections`
    when the apply is recorded
    then the old shape is still read as the old shape, rather than as the new one
    """
    repo, job_id, job_dir = code_job_with_a_staged_defect(review, make_repo, codex_reply)
    (repo / "projections").write_text("a tracked file with that name\n")
    subprocess.run(("git", "add", "projections"), cwd=str(repo), check=True,
                   capture_output=True)
    recorded = json.loads((job_dir / "probes.json").read_text())
    recorded["pre_apply_baseline"] = {
        "projections": {"kind": "regular", "git_mode": "100644",
                        "content_identity": "sha256:" + "0" * 64},
    }
    (job_dir / "probes.json").write_text(json.dumps(recorded))
    (repo / "f.py").write_text("def d(a, b):\n    return a / b  # fixed\n")

    result = submit_apply(review, job_id, job_dir,
                          [{"finding_id": "F-1", "result": "applied"}])

    assert result.envelope is not None, result
    assert result.stderr == "", result.stderr
    assert str(repo / "f.py") in result.envelope["payload"]["changed_paths"], result


def should_read_the_unversioned_two_projection_baseline(review, make_repo, codex_reply):
    """
    given a baseline written between the two shape changes, carrying no version
    when the apply is recorded
    then both projections are still compared, rather than the whole record being read
        as a single path
    """
    repo, job_id, job_dir = code_job_with_a_staged_defect(review, make_repo, codex_reply)
    recorded = json.loads((job_dir / "probes.json").read_text())
    recorded["pre_apply_baseline"] = {"projections": recorded["pre_apply_baseline"]["projections"]}
    (job_dir / "probes.json").write_text(json.dumps(recorded))
    subprocess.run(("git", "add", "f.py"), cwd=str(repo), check=True, capture_output=True)

    result = submit_apply(review, job_id, job_dir,
                          [{"finding_id": "F-1", "result": "applied"}])

    assert result.exit_code == 0, result
    assert result.envelope["payload"]["changed_paths"] == [str(repo / "f.py")], result
