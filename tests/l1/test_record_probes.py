"""Section 8 — recording probe annotations over the reported findings."""

import hashlib
import json
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
def reviewed(review, plan_file_factory, outside_any_repo, codex_reply):
    plan = plan_file_factory("# a plan\n")
    prepared = review("prepare", "plan", str(plan), cwd=outside_any_repo)
    job_id = prepared.envelope["job_id"]
    job_dir = Path(prepared.envelope["job_dir"])
    codex_reply(RESPONSE)
    ran = review("run", job_id, fake={"CODEX_FAKE_RESPONSE": codex_reply.path})
    assert ran.exit_code == 0, ran
    return job_id, job_dir, plan


def report_sha(job_dir):
    return hashlib.sha256((job_dir / "report.json").read_bytes()).hexdigest()


def seal_probe(review, job_id, job_dir, finding="F-1", executed=True):
    """Write → seal command, output and rc, as section 9's flow requires."""
    (job_dir / "staging").mkdir(parents=True, exist_ok=True)
    (job_dir / "staging" / "probe-{}.sh".format(finding)).write_text("echo checking\n")
    ids = {"command_evidence_id": review(
        "seal", job_id, "staging/probe-{}.sh".format(finding)
    ).envelope["payload"]["evidence_id"]}
    if executed:
        (job_dir / "probes").mkdir(parents=True, exist_ok=True)
        (job_dir / "probes" / "{}.out".format(finding)).write_text("observed\n")
        (job_dir / "probes" / "{}.rc".format(finding)).write_text("0\n")
        ids["output_evidence_id"] = review(
            "seal", job_id, "probes/{}.out".format(finding)
        ).envelope["payload"]["evidence_id"]
        ids["rc_evidence_id"] = review(
            "seal", job_id, "probes/{}.rc".format(finding)
        ).envelope["payload"]["evidence_id"]
    return ids


def submit(review, job_id, job_dir, annotations, sha=None):
    payload = {"report_sha256": sha or report_sha(job_dir), "annotations": annotations}
    path = job_dir / "staging" / "probes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return review("record-probes", job_id, "--probes-file", str(path))


def accepted(review, job_id, job_dir, plan):
    ids = seal_probe(review, job_id, job_dir)
    annotation = {"finding_id": "F-1", "probe_execution": "executed",
                  "read": "confirmed-introduced", "disposition": "accepted",
                  "fix_paths": [plan.name]}
    annotation.update(ids)
    return annotation


def should_record_an_accepted_finding(review, reviewed):
    """
    given an accepted annotation meeting all four preconditions
    when it is recorded
    then probes.json is written and the stage becomes probed
    """
    job_id, job_dir, plan = reviewed

    result = submit(review, job_id, job_dir, [accepted(review, job_id, job_dir, plan)])

    assert result.exit_code == 0, result
    assert (job_dir / "probes.json").is_file()
    assert review("status", job_id).envelope["payload"]["stage"] == "probed"
    assert result.envelope["payload"]["probe_completion"] == "complete"


def should_report_the_probes_digest_that_record_apply_requires(review, reviewed):
    """
    given an accepted annotation
    when it is recorded
    then the envelope carries the sha256 of the probes.json it wrote, which the apply
    payload must quote — so the caller never has to rehash the record itself
    """
    job_id, job_dir, plan = reviewed

    result = submit(review, job_id, job_dir, [accepted(review, job_id, job_dir, plan)])

    expected = hashlib.sha256((job_dir / "probes.json").read_bytes()).hexdigest()
    assert result.envelope["payload"]["probes_sha256"] == expected


def should_reject_a_stale_report_digest(review, reviewed):
    """
    given a submission carrying the wrong report_sha256
    when it is recorded
    then it is refused — annotations bind to exactly one review
    """
    job_id, job_dir, plan = reviewed

    result = submit(review, job_id, job_dir,
                    [accepted(review, job_id, job_dir, plan)], sha="0" * 64)

    assert result.exit_code == 8, result


def should_require_exactly_one_annotation_per_finding(review, reviewed):
    """
    given a submission omitting a reported finding
    when it is recorded
    then it is refused
    """
    job_id, job_dir, _plan = reviewed
    result = submit(review, job_id, job_dir, [])
    assert result.exit_code == 8, result


def should_reject_an_unknown_finding_id(review, reviewed):
    """
    given an annotation naming a finding no report carries
    when it is recorded
    then it is refused
    """
    job_id, job_dir, plan = reviewed
    annotation = accepted(review, job_id, job_dir, plan)
    annotation["finding_id"] = "F-9"

    assert submit(review, job_id, job_dir, [annotation]).exit_code == 8


def should_derive_denial_rather_than_believe_it(review, reviewed):
    """
    given an annotation claiming denial while the probe output exists
    when it is recorded
    then it is refused — a command that ran leaves both files
    """
    job_id, job_dir, _plan = reviewed
    ids = seal_probe(review, job_id, job_dir, executed=True)
    annotation = {"finding_id": "F-1", "probe_execution": "denied",
                  "denial_kind": "policy", "asserted_by": "orchestrator",
                  "read": "inconclusive", "disposition": "rejected",
                  "rationale": "refused by policy",
                  "command_evidence_id": ids["command_evidence_id"]}

    assert submit(review, job_id, job_dir, [annotation]).exit_code == 8


def should_accept_a_genuine_denial_and_mark_probing_incomplete(review, reviewed):
    """
    given a denial with no probe output on disk
    when it is recorded
    then it is accepted and probe_completion is derived as incomplete
    """
    job_id, job_dir, _plan = reviewed
    ids = seal_probe(review, job_id, job_dir, executed=False)
    annotation = {"finding_id": "F-1", "probe_execution": "denied",
                  "denial_kind": "policy", "asserted_by": "orchestrator",
                  "read": "inconclusive", "disposition": "rejected",
                  "rationale": "refused by policy",
                  "command_evidence_id": ids["command_evidence_id"]}

    result = submit(review, job_id, job_dir, [annotation])

    assert result.exit_code == 14, result
    assert result.envelope["payload"]["probe_completion"] == "incomplete"


def should_refuse_acceptance_without_a_confirmed_read(review, reviewed):
    """
    given an accepted finding whose read is inconclusive
    when it is recorded
    then it is refused — acceptance has four preconditions
    """
    job_id, job_dir, plan = reviewed
    annotation = accepted(review, job_id, job_dir, plan)
    annotation["read"] = "inconclusive"

    assert submit(review, job_id, job_dir, [annotation]).exit_code == 8


def should_reject_a_fix_path_that_is_not_the_plan(review, reviewed):
    """
    given a plan target and a fix path naming something else
    when it is recorded
    then it is refused — a plan admits exactly one legal fix path
    """
    job_id, job_dir, plan = reviewed
    annotation = accepted(review, job_id, job_dir, plan)
    annotation["fix_paths"] = ["somewhere-else.md"]

    assert submit(review, job_id, job_dir, [annotation]).exit_code == 8


def should_refuse_a_second_recording(review, reviewed):
    """
    given a job that already has probes.json
    when a second submission arrives
    then it is refused rather than duplicated
    """
    job_id, job_dir, plan = reviewed
    submit(review, job_id, job_dir, [accepted(review, job_id, job_dir, plan)])

    result = submit(review, job_id, job_dir, [accepted(review, job_id, job_dir, plan)])

    assert result.exit_code == 8, result


def should_keep_the_job_open_after_a_rejection(review, reviewed):
    """
    given a refused submission
    when the job is inspected
    then it stays at reviewed with an attempt recorded, so a correction can follow
    """
    job_id, job_dir, _plan = reviewed
    submit(review, job_id, job_dir, [])

    assert review("status", job_id).envelope["payload"]["stage"] == "reviewed"
    assert (job_dir / "attempts" / "001").is_dir()
