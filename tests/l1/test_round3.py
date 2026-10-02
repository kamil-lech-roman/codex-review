"""Findings from the third review."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest


def git(root, *args):
    return subprocess.run(("git",) + args, cwd=str(root), check=True,
                          capture_output=True, text=True).stdout


def target_of(result):
    return json.loads((Path(result.envelope["job_dir"]) / "target.json").read_text())


def should_accept_the_advertised_workflow_options(review, plan_file_factory,
                                                  outside_any_repo):
    """
    given the options the argument hint advertises
    when prepare runs
    then they are accepted rather than refused as unknown
    """
    plan = plan_file_factory()
    assert review("prepare", "plan", str(plan), "--no-verify",
                  cwd=outside_any_repo).exit_code == 0
    assert review("prepare", "plan", str(plan), "--no-apply",
                  cwd=outside_any_repo).exit_code == 0
    result = review("prepare", "plan", str(plan), "--verify", "pytest -q",
                    cwd=outside_any_repo)
    assert result.exit_code == 0, result
    assert target_of(result)["verify_command"] == "pytest -q"


def should_carry_model_and_effort_from_prepare_to_run(review, plan_file_factory,
                                                      outside_any_repo, codex_reply,
                                                      codex_argv):
    """
    given --model and --effort at prepare
    when run invokes the reviewer
    then those values are used, not the defaults
    """
    prepared = review("prepare", "plan", str(plan_file_factory()),
                      "--model", "gpt-6-astra", "--effort", "low", cwd=outside_any_repo)
    codex_reply({"verdict": "approve", "findings": [], "declared_scope": ["A0001"],
                 "verified_claims": []})

    review("run", prepared.envelope["job_id"],
           fake={"CODEX_FAKE_ARGV": codex_argv, "CODEX_FAKE_RESPONSE": codex_reply.path})

    argv = json.loads(codex_argv.read_text())
    assert "model_reasoning_effort=low" in argv
    assert argv[argv.index("-m") + 1] == "gpt-6-astra"


def should_refuse_an_oversize_plan(review, plan_file_factory, outside_any_repo):
    """
    given a plan beyond the per-artifact cap
    when prepare runs
    then it is refused, as a code artifact of the same size would be
    """
    plan = plan_file_factory("x" * (70 * 1024))

    result = review("prepare", "plan", str(plan), cwd=outside_any_repo)

    assert result.exit_code == 6, result


def should_capture_an_untracked_file_behind_a_staged_deletion(review, make_repo):
    """
    given a path removed from the index while its file remains on disk
    when uncommitted work is captured
    then the file's current contents are still captured
    """
    repo = make_repo({"settings.py": "SETTING = 1\n"})
    git(repo, "rm", "--cached", "-q", "settings.py")
    (repo / "settings.py").write_text("SETTING = 2\n")

    result = review("prepare", "code", "--uncommitted", cwd=repo)

    assert result.exit_code == 0, result
    job_dir = Path(result.envelope["job_dir"])
    bodies = [(job_dir / "artifacts" / item["id"] / "after").read_text()
              for item in target_of(result)["artifacts_detail"]
              if item["path"] == "settings.py"]
    assert any("SETTING = 2" in body for body in bodies), bodies


def should_not_treat_a_declared_symlink_change_as_out_of_scope(review, make_repo,
                                                               codex_reply):
    """
    given an accepted fix to a tracked symlink
    when the link is repointed inside the repository
    then it reconciles rather than reading as a scope violation
    """
    repo = make_repo({"a.py": "A = 1\n", "b.py": "B = 1\n"})
    (repo / "link").symlink_to("a.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "link")
    base = git(repo, "rev-parse", "HEAD~1").strip()

    prepared = review("prepare", "code", "--base", base, cwd=repo)
    job_id = prepared.envelope["job_id"]
    job_dir = Path(prepared.envelope["job_dir"])
    artifacts = target_of(prepared)["artifacts"]
    codex_reply({"verdict": "changes-required",
                 "findings": [{"id": "F-1", "classification": "introduced",
                               "severity": "major", "claim": "c", "failure_scenario": "f",
                               "evidence": "e", "artifact": artifacts[0],
                               "suggested_fix": "s"}],
                 "declared_scope": artifacts})
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
                  "fix_paths": ["link"]}
    annotation.update(ids)
    probes_path = job_dir / "staging" / "probes.json"
    probes_path.write_text(json.dumps({
        "report_sha256": hashlib.sha256((job_dir / "report.json").read_bytes()).hexdigest(),
        "annotations": [annotation]}))
    assert review("record-probes", job_id,
                  "--probes-file", str(probes_path)).exit_code == 0

    (repo / "link").unlink()
    (repo / "link").symlink_to("b.py")

    apply_path = job_dir / "staging" / "apply.json"
    apply_path.write_text(json.dumps({
        "report_sha256": hashlib.sha256((job_dir / "report.json").read_bytes()).hexdigest(),
        "probes_sha256": hashlib.sha256((job_dir / "probes.json").read_bytes()).hexdigest(),
        "results": [{"finding_id": "F-1", "result": "applied"}]}))
    result = review("record-apply", job_id, "--apply-file", str(apply_path))

    assert result.exit_code == 0, result


def should_require_evidence_for_a_verification_that_ran(review, plan_file_factory,
                                                        outside_any_repo, codex_reply):
    """
    given a verification claiming it passed
    when it carries no command, output or return-code evidence
    then it is refused — passed is derived from the sealed rc, never asserted
    """
    plan = plan_file_factory("# a plan\n")
    prepared = review("prepare", "plan", str(plan), "--verify", "true",
                      cwd=outside_any_repo)
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
        "report_sha256": hashlib.sha256((job_dir / "report.json").read_bytes()).hexdigest(),
        "annotations": [annotation]}))
    assert review("record-probes", job_id,
                  "--probes-file", str(probes_path)).exit_code == 0
    plan.write_text("# a plan\n\nfixed.\n")
    apply_path = job_dir / "staging" / "apply.json"
    apply_path.write_text(json.dumps({
        "report_sha256": hashlib.sha256((job_dir / "report.json").read_bytes()).hexdigest(),
        "probes_sha256": hashlib.sha256((job_dir / "probes.json").read_bytes()).hexdigest(),
        "results": [{"finding_id": "F-1", "result": "applied"}]}))
    assert review("record-apply", job_id, "--apply-file", str(apply_path)).exit_code == 0

    verify_path = job_dir / "staging" / "verification.json"
    verify_path.write_text(json.dumps({
        "apply_sha256": hashlib.sha256((job_dir / "apply.json").read_bytes()).hexdigest(),
        "verification": {"result": "passed"}}))
    result = review("record-verification", job_id, "--verification-file", str(verify_path))

    assert result.exit_code == 8, result
