import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "experiments/approval-streak/measure.py"
SPEC = importlib.util.spec_from_file_location("approval_measure", SCRIPT)
measure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measure)


def should_keep_an_unfinished_round_distinct_from_zero_findings(tmp_path):
    # given an explicitly selected job with a target but no report
    job = tmp_path / ("a" * 16)
    job.mkdir()
    (job / "target.json").write_text(json.dumps({"base_ref": "base"}))
    manifest = tmp_path / "jobs.json"
    manifest.write_text(json.dumps([job.name]))

    # when the read-only measurement runs
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--state", str(tmp_path), "--jobs", str(manifest)],
        capture_output=True, text=True,
    )

    # then missing evidence cannot become a clean review or a savings claim
    assert result.returncode == 0, result.stderr
    measured = json.loads(result.stdout)
    assert measured["rounds"][0]["findings"] is None
    assert measured["rounds"][0]["accepted"] is None
    assert measured["thresholds"]["2"]["missed_accepted"] is None


def should_count_accepted_annotations_without_treating_approve_as_no_findings(tmp_path):
    # given an approval with a confirmed pre-existing observation, not a rejection
    (tmp_path / "target.json").write_text(json.dumps({"base_ref": "base"}))
    (tmp_path / "report.json").write_text(json.dumps({
        "verdict": "approve", "findings": [{"id": "F-1"}],
        "run_metadata": {"model": "actual-model", "effort": "max"},
    }))
    (tmp_path / "probes.json").write_text(json.dumps({
        "annotations": [{"finding_id": "F-1", "read": "confirmed-pre-existing"}],
    }))

    # when the original job records are measured
    row = measure.measure_round(tmp_path)

    # then verdict, findings, accepted count and provenance stay independent
    assert (row["verdict"], row["findings"], row["accepted"]) == ("approve", 1, 0)
    assert row["model"] == "actual-model"
    assert row["effort"] == "max"
    assert set(row["source_sha256"]) == {"target.json", "report.json", "probes.json"}


def should_stop_after_the_second_approval_and_count_only_subsequent_calls():
    # given isolated approvals separated by two accepted findings, then a streak
    rounds = [
        {"job_id": str(i), "verdict": verdict, "accepted": accepted}
        for i, (verdict, accepted) in enumerate([
            ("approve", 0), ("changes-required", 1), ("approve", 0),
            ("changes-required", 1), ("approve", 0), ("approve", 0),
            ("approve", 0), ("approve", 0), ("approve", 0), ("approve", 0),
        ])
    ]

    # when both thresholds are evaluated, including the later completed round
    first = measure.threshold(rounds, 1)
    second = measure.threshold(rounds, 2)
    later = measure.threshold(rounds + [{"job_id": "10", "verdict": "approve", "accepted": 0}], 2)

    # then the triggering call is paid for; only later calls are saved
    assert first == {"stop_after": "0", "saved_calls": 9, "missed_accepted": 2}
    assert second == {"stop_after": "5", "saved_calls": 4, "missed_accepted": 0}
    assert later == {"stop_after": "5", "saved_calls": 5, "missed_accepted": 0}


@pytest.mark.parametrize("annotations", [[], [{"finding_id": "F-2", "disposition": "accepted"}]])
def should_refuse_incomplete_or_unrelated_annotations(tmp_path, annotations):
    # given findings whose recorded dispositions do not cover the same ids
    (tmp_path / "target.json").write_text("{}")
    (tmp_path / "report.json").write_text(json.dumps({
        "verdict": "changes-required", "findings": [{"id": "F-1"}],
    }))
    (tmp_path / "probes.json").write_text(json.dumps({"annotations": annotations}))

    # when measured, then untrusted counts cannot become evidence
    with pytest.raises(ValueError, match="annotations"):
        measure.measure_round(tmp_path)


@pytest.mark.parametrize("jobs", [[], ["../another-session"], ["a" * 16, "a" * 16]])
def should_require_unique_explicit_job_ids(jobs):
    # given a manifest that cannot identify a bounded ordered sample
    # when validated, then it must fail before any job is read
    with pytest.raises(ValueError, match="job ids"):
        measure.validate_jobs(jobs)


@pytest.mark.parametrize("report", [{}, {"verdict": "unknown", "findings": []}])
def should_refuse_malformed_reports_instead_of_treating_them_as_unfinished(tmp_path, report):
    # given a report file whose contents are not a review result
    (tmp_path / "target.json").write_text("{}")
    (tmp_path / "report.json").write_text(json.dumps(report))

    # when measured, then bad evidence cannot masquerade as a pending round
    with pytest.raises(ValueError, match="report"):
        measure.measure_round(tmp_path)


def should_require_the_callers_explicit_job_manifest(tmp_path):
    # given a public installation without the maintainer's private job manifest
    # when no manifest is supplied
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--state", str(tmp_path)],
        capture_output=True, text=True,
    )

    # then the CLI asks for the missing argument instead of opening a private default
    assert result.returncode == 2
    assert "required: --jobs" in result.stderr
