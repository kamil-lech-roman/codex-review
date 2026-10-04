"""Reproduce the 2026-09-17 audit observations offline; these are not desired tests.

Run with Python 3 from any directory. All jobs and repositories are temporary.
Only a fake reviewer is executed. The JSON records observations, not approval.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]


def main():
    observations = {}
    with tempfile.TemporaryDirectory(prefix="review-audit-") as tmp:
        scratch = Path(tmp)
        fake = scratch / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\nimport json,os,sys\n"
            "if '--version' in sys.argv: print('audit-fake'); sys.exit(0)\n"
            "p=sys.argv[sys.argv.index('-o')+1]\n"
            "open(p,'w').write(open(os.environ['AUDIT_RESPONSE']).read())\n"
        )
        fake.chmod(0o755)
        response = scratch / "response.json"
        response.write_text(json.dumps({
            "verdict": "approve", "findings": [], "declared_scope": ["A0001"],
            "verified_claims": [],
        }))
        env = dict(os.environ, CODEX_BIN=str(fake), AUDIT_RESPONSE=str(response),
                   CODEX_REVIEW_STATE=str(scratch / "state"))

        def invoke(*args, cwd=scratch):
            completed = subprocess.run([str(ROOT / "review.sh"), *map(str, args)],
                                       cwd=cwd, env=env, text=True,
                                       capture_output=True, stdin=subprocess.DEVNULL,
                                       check=False)
            value = json.loads(completed.stdout)
            assert value["exit"] == completed.returncode
            return value

        def prepared(name):
            plan = scratch / (name + ".md")
            plan.write_text("# Captured plan\n")
            result = invoke("prepare", "plan", plan, "--no-repo", "--no-verify")
            assert result["exit"] == 0
            return plan, result["job_id"], Path(result["job_dir"])

        def write_payload(name, value):
            path = scratch / name
            path.write_text(json.dumps(value))
            return path

        for verb in ("accept-delta", "break-lease"):
            observations[verb] = invoke(verb, "0000000000000000")

        plan = scratch / "without-verify.md"
        plan.write_text("# Plan\n")
        observations["missing_verify_option"] = invoke(
            "prepare", "plan", plan, "--no-repo")

        repo = scratch / "repo"
        repo.mkdir()
        for args in (("init", "-q"), ("config", "user.email", "audit@example.invalid"),
                     ("config", "user.name", "Audit")):
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                           stdin=subprocess.DEVNULL)
        (repo / "a.txt").write_text("base\n")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True,
                       capture_output=True, stdin=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True,
                       capture_output=True, stdin=subprocess.DEVNULL)
        (repo / "a.txt").write_text("changed\n")
        env["CODEX_REVIEW_STATE"] = str(repo / "state")
        observations["state_inside_repository"] = invoke(
            "prepare", "code", "--uncommitted", "--no-verify", cwd=repo)
        env["CODEX_REVIEW_STATE"] = str(scratch / "state")

        plan, job, directory = prepared("drift")
        plan.write_text("# Changed before review\n")
        observations["run_after_target_drift"] = invoke("run", job)
        report_sha = hashlib.sha256((directory / "report.json").read_bytes()).hexdigest()
        payload = write_payload("external-probes.json", {
            "report_sha256": report_sha, "annotations": []})
        observations["record_probes_after_drift_from_external_payload"] = invoke(
            "record-probes", job, "--probes-file", payload)
        observations["status_after_drift"] = invoke("status", job)

        _, job, _ = prepared("schema")
        response.write_text(json.dumps({
            "verdict": "changes-required", "declared_scope": ["A0001"],
            "verified_claims": [], "extra": "not in schema",
            "findings": [{"id": "F-1", "classification": "introduced",
                          "severity": "major", "artifact": "A0001",
                          "claim": "", "failure_scenario": "", "evidence": "",
                          "suggested_fix": "", "status": "withdrawn"}],
        }))
        observations["malformed_semantic_response"] = invoke("run", job)

        # Two accepted findings on the same plan expose the missing shared apply lease.
        plan = scratch / "shared.md"
        plan.write_text("# Shared\n")
        response.write_text(json.dumps({
            "verdict": "changes-required", "declared_scope": ["A0001"],
            "verified_claims": [],
            "findings": [{"id": "F-1", "classification": "introduced",
                          "severity": "major", "artifact": "A0001",
                          "claim": "c", "failure_scenario": "f", "evidence": "e",
                          "suggested_fix": "s"}],
        }))
        accepted = []
        for number in range(2):
            created = invoke("prepare", "plan", plan, "--no-repo", "--no-verify")
            job, directory = created["job_id"], Path(created["job_dir"])
            reviewed = invoke("run", job)
            ids = []
            for path, content in (("staging/probe-F-1.sh", "true\n"),
                                  ("probes/F-1.out", "observed\n"),
                                  ("probes/F-1.rc", "0\n")):
                target = directory / path
                target.parent.mkdir(exist_ok=True)
                target.write_text(content)
                ids.append(invoke("seal", job, path)["payload"]["evidence_id"])
            payload = write_payload("shared-probes-{}.json".format(number), {
                "report_sha256": reviewed["payload"]["report_sha256"],
                "annotations": [{"finding_id": "F-1", "probe_execution": "executed",
                                 "command_evidence_id": ids[0], "output_evidence_id": ids[1],
                                 "rc_evidence_id": ids[2], "read": "confirmed-introduced",
                                 "disposition": "accepted", "fix_paths": [plan.name]}],
            })
            accepted.append(invoke("record-probes", job, "--probes-file", payload))
        observations["two_jobs_accept_same_plan_without_lease"] = accepted

    print(json.dumps(observations, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
