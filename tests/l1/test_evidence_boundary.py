"""Section 9 — evidence belongs to its job and its slot; neither is negotiable."""

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


def make_reviewed(review, plan, outside, codex_reply, response=None):
    prepared = review("prepare", "plan", str(plan), cwd=outside)
    job_id = prepared.envelope["job_id"]
    job_dir = Path(prepared.envelope["job_dir"])
    codex_reply(response or RESPONSE)
    assert review("run", job_id,
                  fake={"CODEX_FAKE_RESPONSE": codex_reply.path}).exit_code == 0
    return job_id, job_dir


def seal_all(review, job_id, job_dir, finding="F-1"):
    (job_dir / "staging").mkdir(parents=True, exist_ok=True)
    (job_dir / "staging" / "probe-{}.sh".format(finding)).write_text("echo hi\n")
    (job_dir / "probes").mkdir(parents=True, exist_ok=True)
    (job_dir / "probes" / "{}.out".format(finding)).write_text("observed\n")
    (job_dir / "probes" / "{}.rc".format(finding)).write_text("0\n")
    return {
        "command_evidence_id": review("seal", job_id, "staging/probe-{}.sh".format(finding))
        .envelope["payload"]["evidence_id"],
        "output_evidence_id": review("seal", job_id, "probes/{}.out".format(finding))
        .envelope["payload"]["evidence_id"],
        "rc_evidence_id": review("seal", job_id, "probes/{}.rc".format(finding))
        .envelope["payload"]["evidence_id"],
    }


def submit(review, job_id, job_dir, annotations):
    path = job_dir / "staging" / "probes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "report_sha256": hashlib.sha256((job_dir / "report.json").read_bytes()).hexdigest(),
        "annotations": annotations,
    }))
    return review("record-probes", job_id, "--probes-file", str(path))


def should_refuse_evidence_sealed_by_another_job(
    review, plan_file_factory, outside_any_repo, codex_reply
):
    """
    given two jobs each carrying a finding F-1
    when one cites the other's sealed evidence
    then it is refused — evidence must belong to this job
    """
    first_id, first_dir = make_reviewed(
        review, plan_file_factory("# one\n"), outside_any_repo, codex_reply)
    stolen = seal_all(review, first_id, first_dir)
    second_id, second_dir = make_reviewed(
        review, plan_file_factory("# two\n"), outside_any_repo, codex_reply)
    seal_all(review, second_id, second_dir)

    # Physically place the first job's sealed objects under the second job, so the only
    # thing that can refuse them is the recorded owner.
    import shutil
    for evidence_id in stolen.values():
        shutil.copytree(first_dir / "evidence" / evidence_id,
                        second_dir / "evidence" / evidence_id)

    annotation = {"finding_id": "F-1", "probe_execution": "executed",
                  "read": "inconclusive", "disposition": "rejected", "rationale": "no"}
    annotation.update(stolen)

    result = submit(review, second_id, second_dir, [annotation])

    assert result.exit_code == 8, result
    assert result.envelope["error"]["code"] == "evidence-wrong-job"


def should_refuse_a_traversing_evidence_id(
    review, plan_file_factory, outside_any_repo, codex_reply
):
    """
    given an evidence id spelled as a relative traversal
    when it is cited
    then it is refused before any path is joined
    """
    job_id, job_dir = make_reviewed(
        review, plan_file_factory(), outside_any_repo, codex_reply)
    ids = seal_all(review, job_id, job_dir)
    annotation = {"finding_id": "F-1", "probe_execution": "executed",
                  "read": "inconclusive", "disposition": "rejected", "rationale": "no"}
    annotation.update(ids)
    annotation["command_evidence_id"] = "../../etc"

    assert submit(review, job_id, job_dir, [annotation]).exit_code == 8


def should_apply_the_complete_preview_rule_to_any_finding_id(
    review, plan_file_factory, outside_any_repo, codex_reply
):
    """
    given a finding whose id is not of the form F-N
    when its probe script is sealed
    then the complete-preview rule still applies to it
    """
    response = json.loads(json.dumps(RESPONSE))
    response["findings"][0]["id"] = "bug1"
    job_id, job_dir = make_reviewed(
        review, plan_file_factory(), outside_any_repo, codex_reply, response)
    (job_dir / "staging").mkdir(parents=True, exist_ok=True)
    (job_dir / "staging" / "probe-bug1.sh").write_text("echo " + "x" * 500 + "\n")

    result = review("seal", job_id, "staging/probe-bug1.sh", "--bytes", "100")

    assert result.envelope["payload"]["truncated"] is False, result


def should_reconcile_a_plan_reached_through_a_symlinked_ancestor(
    review, plan_file_factory, outside_any_repo, codex_reply, tmp_path
):
    """
    given a plan prepared through a symlinked ancestor, which prepare accepts
    when its accepted fix is recorded
    then the path reconciles rather than reading as a scope violation
    """
    real_dir = tmp_path / "real-plans"
    real_dir.mkdir()
    plan = real_dir / "design.md"
    plan.write_text("# a plan\n")
    linked = tmp_path / "linked-plans"
    linked.symlink_to(real_dir, target_is_directory=True)

    job_id, job_dir = make_reviewed(review, linked / "design.md", outside_any_repo,
                                    codex_reply)
    ids = seal_all(review, job_id, job_dir)
    annotation = {"finding_id": "F-1", "probe_execution": "executed",
                  "read": "confirmed-introduced", "disposition": "accepted",
                  "fix_paths": ["design.md"]}
    annotation.update(ids)
    assert submit(review, job_id, job_dir, [annotation]).exit_code == 0

    plan.write_text("# a plan\n\nfixed.\n")
    apply_path = job_dir / "staging" / "apply.json"
    apply_path.write_text(json.dumps({
        "report_sha256": hashlib.sha256((job_dir / "report.json").read_bytes()).hexdigest(),
        "probes_sha256": hashlib.sha256((job_dir / "probes.json").read_bytes()).hexdigest(),
        "results": [{"finding_id": "F-1", "result": "applied"}],
    }))
    result = review("record-apply", job_id, "--apply-file", str(apply_path))

    assert result.exit_code == 0, result
    assert result.envelope["payload"]["application_result"] == "applied-complete"
