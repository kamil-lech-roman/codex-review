"""Sections 11 and 12 — what the apply is measured against, and when it is too late."""

import hashlib
import json
import subprocess
import threading
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


def digests(job_dir):
    return (hashlib.sha256((job_dir / "report.json").read_bytes()).hexdigest(),
            hashlib.sha256((job_dir / "probes.json").read_bytes()).hexdigest())


@pytest.fixture
def probed(review, plan_file_factory, outside_any_repo, codex_reply):
    """A job at `probed` with F-1 accepted against the plan file."""
    def build(disposition="accepted"):
        plan = plan_file_factory("# a plan\n")
        prepared = review("prepare", "plan", str(plan), cwd=outside_any_repo)
        job_id = prepared.envelope["job_id"]
        job_dir = Path(prepared.envelope["job_dir"])
        codex_reply(RESPONSE)
        review("run", job_id, fake={"CODEX_FAKE_RESPONSE": codex_reply.path})
        (job_dir / "staging").mkdir(parents=True, exist_ok=True)
        (job_dir / "staging" / "probe-F-1.sh").write_text("echo hi\n")
        (job_dir / "probes").mkdir(parents=True, exist_ok=True)
        (job_dir / "probes" / "F-1.out").write_text("observed\n")
        (job_dir / "probes" / "F-1.rc").write_text("0\n")
        ids = {k: review("seal", job_id, v).envelope["payload"]["evidence_id"]
               for k, v in (("command_evidence_id", "staging/probe-F-1.sh"),
                            ("output_evidence_id", "probes/F-1.out"),
                            ("rc_evidence_id", "probes/F-1.rc"))}
        annotation = {"finding_id": "F-1", "probe_execution": "executed", "read":
                      "confirmed-introduced" if disposition == "accepted" else "inconclusive"}
        annotation.update(ids)
        if disposition == "accepted":
            annotation["disposition"] = "accepted"
            annotation["fix_paths"] = [plan.name]
        else:
            annotation["disposition"] = "rejected"
            annotation["rationale"] = "not a real defect"
        path = job_dir / "staging" / "probes.json"
        path.write_text(json.dumps({
            "report_sha256": hashlib.sha256((job_dir / "report.json").read_bytes()).hexdigest(),
            "annotations": [annotation]}))
        assert review("record-probes", job_id, "--probes-file", str(path)).exit_code == 0
        return job_id, job_dir, plan
    return build


def submit_apply(review, job_id, job_dir, results):
    report_sha, probes_sha = digests(job_dir)
    path = job_dir / "staging" / "apply.json"
    path.write_text(json.dumps({"report_sha256": report_sha,
                                "probes_sha256": probes_sha, "results": results}))
    return review("record-apply", job_id, "--apply-file", str(path))


def should_scope_check_even_when_nothing_was_accepted(review, probed):
    """
    given every finding rejected and a file changed anyway
    when the apply is recorded
    then it is a scope violation — an empty union authorises nothing
    """
    job_id, job_dir, plan = probed(disposition="rejected")
    plan.write_text("# a plan\n\nchanged by something\n")

    result = submit_apply(review, job_id, job_dir, [])

    assert result.exit_code == 10, result


def should_still_report_none_accepted_when_nothing_moved(review, probed):
    """
    given every finding rejected and nothing changed
    when the apply is recorded
    then it closes cleanly as none-accepted
    """
    job_id, job_dir, _plan = probed(disposition="rejected")

    result = submit_apply(review, job_id, job_dir, [])

    assert result.exit_code == 0, result
    assert json.loads((job_dir / "apply.json").read_text())[
        "application_result"] == "none-accepted"


def should_not_credit_a_change_made_before_probing(review, probed, plan_file_factory,
                                                   outside_any_repo, codex_reply):
    """
    given a plan changed after capture but before record-probes
    when an applied result is submitted with no further edit
    then the earlier change is not counted as the fix
    """
    plan = plan_file_factory("# a plan\n")
    prepared = review("prepare", "plan", str(plan), cwd=outside_any_repo)
    job_id = prepared.envelope["job_id"]
    job_dir = Path(prepared.envelope["job_dir"])
    codex_reply(RESPONSE)
    review("run", job_id, fake={"CODEX_FAKE_RESPONSE": codex_reply.path})

    plan.write_text("# a plan\n\nchanged before probing\n")

    (job_dir / "staging").mkdir(parents=True, exist_ok=True)
    (job_dir / "staging" / "probe-F-1.sh").write_text("echo hi\n")
    (job_dir / "probes").mkdir(parents=True, exist_ok=True)
    (job_dir / "probes" / "F-1.out").write_text("observed\n")
    (job_dir / "probes" / "F-1.rc").write_text("0\n")
    ids = {k: review("seal", job_id, v).envelope["payload"]["evidence_id"]
           for k, v in (("command_evidence_id", "staging/probe-F-1.sh"),
                        ("output_evidence_id", "probes/F-1.out"),
                        ("rc_evidence_id", "probes/F-1.rc"))}
    annotation = {"finding_id": "F-1", "probe_execution": "executed",
                  "read": "confirmed-introduced", "disposition": "accepted",
                  "fix_paths": [plan.name]}
    annotation.update(ids)
    path = job_dir / "staging" / "probes.json"
    path.write_text(json.dumps({
        "report_sha256": hashlib.sha256((job_dir / "report.json").read_bytes()).hexdigest(),
        "annotations": [annotation]}))
    assert review("record-probes", job_id, "--probes-file", str(path)).exit_code == 0

    result = submit_apply(review, job_id, job_dir,
                          [{"finding_id": "F-1", "result": "applied"}])

    assert result.exit_code == 11, result
    assert json.loads((job_dir / "apply.json").read_text())["changed_paths"] == []


def should_refuse_verification_after_the_tree_moved_again(review, probed):
    """
    given the applied tree changing between record-apply and record-verification
    when verification is recorded
    then it is refused — the result would describe bytes that are no longer there
    """
    job_id, job_dir, plan = probed()
    plan.write_text("# a plan\n\nfixed.\n")
    assert submit_apply(review, job_id, job_dir,
                        [{"finding_id": "F-1", "result": "applied"}]).exit_code == 0

    plan.write_text("# a plan\n\nsomething else entirely.\n")

    path = job_dir / "staging" / "verification.json"
    path.write_text(json.dumps({
        "apply_sha256": hashlib.sha256((job_dir / "apply.json").read_bytes()).hexdigest(),
        "verification": {"result": "not-run-explicitly"}}))
    result = review("record-verification", job_id, "--verification-file", str(path))

    assert result.exit_code == 4, result
    assert (job_dir / "invalidated.json").is_file()
    assert not (job_dir / "verification.json").exists()


def should_write_exactly_one_terminal_record_under_contention(review, probed):
    """
    given abandon and record-verification racing
    when both run
    then exactly one terminal record exists — the check is inside the mutex
    """
    job_id, job_dir, plan = probed()
    plan.write_text("# a plan\n\nfixed.\n")
    submit_apply(review, job_id, job_dir, [{"finding_id": "F-1", "result": "applied"}])
    path = job_dir / "staging" / "verification.json"
    path.write_text(json.dumps({
        "apply_sha256": hashlib.sha256((job_dir / "apply.json").read_bytes()).hexdigest(),
        "verification": {"result": "not-run-explicitly"}}))

    outcomes = []
    def close_by_verification():
        outcomes.append(review("record-verification", job_id,
                               "--verification-file", str(path)).exit_code)
    def close_by_abandon():
        outcomes.append(review("abandon", job_id, "--reason", "racing").exit_code)

    threads = [threading.Thread(target=close_by_verification),
               threading.Thread(target=close_by_abandon)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    terminal = [name for name in ("verification.json", "abandoned.json",
                                  "invalidated.json", "apply-error.json", "run-error.json")
                if (job_dir / name).exists()]
    assert len(terminal) == 1, terminal
