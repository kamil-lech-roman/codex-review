#!/usr/bin/env python3
"""Read only the ordered jobs named in a manifest; emit measurements to stdout."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def read_record(job, name, hashes):
    path = job / (name + ".json")
    if not path.exists():
        return None
    raw = path.read_bytes()
    hashes[path.name] = hashlib.sha256(raw).hexdigest()
    record = json.loads(raw)
    if not isinstance(record, dict):
        raise ValueError("Expected an object in " + str(path))
    return record


def measure_round(job):
    hashes = {}
    target = read_record(job, "target", hashes)
    if target is None:
        raise ValueError("Missing target.json for " + job.name)
    report = read_record(job, "report", hashes)
    probes = read_record(job, "probes", hashes)
    if report is not None and (
        report.get("verdict") not in ("approve", "changes-required")
        or not isinstance(report.get("findings"), list)
    ):
        raise ValueError("Invalid report for " + job.name)
    if report is not None and probes is not None:
        findings = [f["id"] for f in report["findings"]]
        annotations = [a["finding_id"] for a in probes["annotations"]]
        if (len(set(findings)) != len(findings) or len(set(annotations)) != len(annotations)
                or set(findings) != set(annotations)):
            raise ValueError("Findings and annotations do not match for " + job.name)
    metadata = report.get("run_metadata", {}) if report else {}
    return {
        "job_id": job.name,
        "base": target.get("base_ref"),
        "head": target.get("repo_identity", {}).get("head_oid"),
        "target_sha": target.get("target_sha"),
        "model": metadata.get("model"),
        "effort": metadata.get("effort"),
        "verdict": report["verdict"] if report else None,
        "findings": len(report["findings"]) if report else None,
        "accepted": sum(a.get("disposition") == "accepted" for a in probes["annotations"])
        if probes and report else None,
        "source_sha256": hashes,
    }


def threshold(rounds, count):
    streak = 0
    for index, row in enumerate(rounds):
        if row["verdict"] is None:
            break
        streak = streak + 1 if row["verdict"] == "approve" else 0
        if streak == count:
            later = rounds[index + 1:]
            complete = all(r["verdict"] is not None and r["accepted"] is not None for r in later)
            return {
                "stop_after": row["job_id"],
                "saved_calls": len(later) if complete else None,
                "missed_accepted": sum(r["accepted"] for r in later) if complete else None,
            }
    return {"stop_after": None, "saved_calls": None, "missed_accepted": None}


def validate_jobs(jobs):
    if (not isinstance(jobs, list) or not jobs
            or any(not isinstance(j, str) or not re.fullmatch(r"[0-9a-f]{16}", j) for j in jobs)
            or len(set(jobs)) != len(jobs)):
        raise ValueError("Expected a nonempty ordered list of unique full job ids")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path.home() / ".codex-review")
    parser.add_argument("--jobs", type=Path, required=True)
    args = parser.parse_args()
    jobs = json.loads(args.jobs.read_text())
    validate_jobs(jobs)
    rounds = [measure_round(args.state / job) for job in jobs]
    print(json.dumps({"rounds": rounds, "thresholds": {
        str(count): threshold(rounds, count) for count in (1, 2)
    }}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
