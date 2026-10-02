"""Findings from the fourth review."""

import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def probed_with(review, plan_file_factory, outside_any_repo, codex_reply, *options):
    """Drive a plan job to `probed` with F-1 accepted, leaving the plan untouched."""
    plan = plan_file_factory("# a plan\n")
    prepared = review("prepare", "plan", str(plan), *options, cwd=outside_any_repo)
    job_id = prepared.envelope["job_id"]
    job_dir = Path(prepared.envelope["job_dir"])
    codex_reply({"verdict": "changes-required",
                 "findings": [{"id": "F-1", "classification": "introduced",
                               "severity": "major", "claim": "c", "failure_scenario": "f",
                               "evidence": "e", "artifact": "A0001", "suggested_fix": "s"}],
                 "declared_scope": ["A0001"], "verified_claims": []})
    assert review("run", job_id,
                  fake={"CODEX_FAKE_RESPONSE": codex_reply.path}).exit_code == 0

    (job_dir / "staging").mkdir(parents=True, exist_ok=True)
    (job_dir / "staging" / "probe-F-1.sh").write_text("echo hi\n")
    (job_dir / "probes").mkdir(parents=True, exist_ok=True)
    (job_dir / "probes" / "F-1.out").write_text("ok\n")
    (job_dir / "probes" / "F-1.rc").write_text("0\n")
    ids = {k: review("seal", job_id, v).envelope["payload"]["evidence_id"]
           for k, v in (("command_evidence_id", "staging/probe-F-1.sh"),
                        ("output_evidence_id", "probes/F-1.out"),
                        ("rc_evidence_id", "probes/F-1.rc"))}
    annotation = {"finding_id": "F-1", "probe_execution": "executed",
                  "read": "confirmed-introduced", "disposition": "accepted",
                  "fix_paths": [plan.name]}
    annotation.update(ids)
    probes_path = job_dir / "staging" / "probes.json"
    probes_path.write_text(json.dumps({
        "report_sha256": digest(job_dir / "report.json"),
        "annotations": [annotation]}))
    assert review("record-probes", job_id,
                  "--probes-file", str(probes_path)).exit_code == 0
    return plan, job_id, job_dir


def record_apply(review, job_id, job_dir, result="applied"):
    apply_path = job_dir / "staging" / "apply.json"
    apply_path.write_text(json.dumps({
        "report_sha256": digest(job_dir / "report.json"),
        "probes_sha256": digest(job_dir / "probes.json"),
        "results": [{"finding_id": "F-1", "result": result}]}))
    return review("record-apply", job_id, "--apply-file", str(apply_path))


def seal_verification(review, job_id, job_dir, rc, command="true"):
    (job_dir / "staging" / "verify.sh").write_text(command + "\n")
    (job_dir / "verify.out").write_text("out\n")
    (job_dir / "verify.rc").write_text(rc)
    return {k: review("seal", job_id, v).envelope["payload"]["evidence_id"]
            for k, v in (("command_evidence_id", "staging/verify.sh"),
                         ("output_evidence_id", "verify.out"),
                         ("rc_evidence_id", "verify.rc"))}


def submit_verification(review, job_id, job_dir, block):
    path = job_dir / "staging" / "verification.json"
    path.write_text(json.dumps({
        "apply_sha256": digest(job_dir / "apply.json"),
        "verification": block}))
    return review("record-verification", job_id, "--verification-file", str(path))


def should_refuse_a_passed_result_the_sealed_return_code_contradicts(
        review, plan_file_factory, outside_any_repo, codex_reply):
    """
    given a verification submitting `passed`
    when the sealed verify.rc holds a nonzero status
    then it is refused — passed is derived from the sealed rc, never asserted
    """
    plan, job_id, job_dir = probed_with(review, plan_file_factory, outside_any_repo,
                                        codex_reply, "--verify", "false")
    plan.write_text("# a plan\n\nfixed.\n")
    assert record_apply(review, job_id, job_dir).exit_code == 0
    block = seal_verification(review, job_id, job_dir, "1\n", command="false")
    block["result"] = "passed"

    result = submit_verification(review, job_id, job_dir, block)

    assert result.exit_code == 8, result


def should_accept_a_failed_result_the_sealed_return_code_agrees_with(
        review, plan_file_factory, outside_any_repo, codex_reply):
    """
    given a verification submitting `failed`
    when the sealed verify.rc holds the matching nonzero status
    then the job closes on the verify-failed terminal carrying that result
    """
    plan, job_id, job_dir = probed_with(review, plan_file_factory, outside_any_repo,
                                        codex_reply, "--verify", "false")
    plan.write_text("# a plan\n\nfixed.\n")
    assert record_apply(review, job_id, job_dir).exit_code == 0
    block = seal_verification(review, job_id, job_dir, "1\n", command="false")
    block["result"] = "failed"

    result = submit_verification(review, job_id, job_dir, block)

    assert result.exit_code == 12, result
    assert json.loads((job_dir / "verification.json").read_text())[
        "verification_result"] == "failed"


def should_refuse_return_code_evidence_sealed_from_another_slot(
        review, plan_file_factory, outside_any_repo, codex_reply):
    """
    given return-code evidence sealed from a probe slot
    when it is cited as the verification return code
    then it is refused — each citation is bound to its own slot
    """
    plan, job_id, job_dir = probed_with(review, plan_file_factory, outside_any_repo,
                                        codex_reply, "--verify", "true")
    plan.write_text("# a plan\n\nfixed.\n")
    assert record_apply(review, job_id, job_dir).exit_code == 0
    block = seal_verification(review, job_id, job_dir, "0\n")
    block["rc_evidence_id"] = review(
        "seal", job_id, "probes/F-1.rc").envelope["payload"]["evidence_id"]
    block["result"] = "passed"

    result = submit_verification(review, job_id, job_dir, block)

    assert result.exit_code == 8, result


def should_skip_the_apply_workflow_when_no_apply_was_requested(
        review, plan_file_factory, outside_any_repo, codex_reply):
    """
    given a job prepared with --no-apply
    when the apply is recorded with nothing edited
    then the outcome is the skip, distinguishable from an incomplete apply
    """
    plan, job_id, job_dir = probed_with(review, plan_file_factory, outside_any_repo,
                                        codex_reply, "--no-apply")

    result = record_apply(review, job_id, job_dir, result="no-change")

    assert result.exit_code == 0, result
    assert json.loads((job_dir / "apply.json").read_text())[
        "application_result"] == "skipped-no-apply-flag"
