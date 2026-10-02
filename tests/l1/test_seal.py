"""Section 9 — sealing evidence: copy first, then render from the copy."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def job(review, plan_file_factory, outside_any_repo):
    result = review("prepare", "plan", str(plan_file_factory()), cwd=outside_any_repo)
    assert result.exit_code == 0, result
    return result.envelope["job_id"], Path(result.envelope["job_dir"])


def write(job_dir, relative, data):
    path = job_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def should_seal_a_probe_output_and_report_its_digest(review, job):
    """
    given a probe output inside the job
    when seal runs
    then it returns an evidence id with the byte count and digest of the stored copy
    """
    job_id, job_dir = job
    write(job_dir, "probes/F-1.out", "hello\n")

    result = review("seal", job_id, "probes/F-1.out")

    assert result.exit_code == 0, result
    payload = result.envelope["payload"]
    assert payload["bytes"] == 6
    assert payload["source_path"] == "probes/F-1.out"
    assert len(payload["sha256"]) == 64
    stored = job_dir / "evidence" / payload["evidence_id"]
    assert stored.is_dir()


def should_record_origin_binding_beside_the_sealed_bytes(review, job):
    """
    given a sealed object
    when the job is inspected
    then the metadata binds it to this job and its source path
    """
    job_id, job_dir = job
    write(job_dir, "probes/F-1.out", "hello\n")

    result = review("seal", job_id, "probes/F-1.out")

    evidence_id = result.envelope["payload"]["evidence_id"]
    meta = json.loads((job_dir / "evidence" / evidence_id / "metadata.json").read_text())
    assert meta["job_id"] == job_id
    assert meta["source_path"] == "probes/F-1.out"
    assert meta["bytes"] == 6
    assert meta["sealed_at"]


def should_refuse_an_absolute_path(review, job):
    """
    given an absolute path
    when seal runs
    then it is refused — evidence must originate inside the job
    """
    job_id, _ = job
    result = review("seal", job_id, "/etc/hosts")
    assert result.exit_code == 2, result


def should_refuse_a_path_containing_dot_dot(review, job):
    """
    given a path escaping the job with ..
    when seal runs
    then it is refused
    """
    job_id, _ = job
    result = review("seal", job_id, "../elsewhere.txt")
    assert result.exit_code == 2, result


def should_refuse_a_symlink_escaping_the_job(review, job, tmp_path):
    """
    given a path inside the job that is a symlink to a file outside it
    when seal runs
    then it is refused — resolve first, then require containment
    """
    job_id, job_dir = job
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    (job_dir / "probes").mkdir(parents=True, exist_ok=True)
    (job_dir / "probes" / "escape.out").symlink_to(outside)

    result = review("seal", job_id, "probes/escape.out")

    assert result.exit_code == 2, result


def should_escape_control_bytes_in_the_preview(review, job):
    """
    given output containing control bytes and invalid UTF-8
    when seal renders a preview
    then the preview is escape-safe rather than raw
    """
    job_id, job_dir = job
    write(job_dir, "probes/F-1.out", b"ok\x00\x1b[31m\xff\xfe done\n")

    result = review("seal", job_id, "probes/F-1.out")

    preview = result.envelope["payload"]["preview"]
    assert "\x00" not in preview and "\x1b" not in preview
    assert "\\x00" in preview


def should_bound_ordinary_evidence_and_report_the_true_total(review, job):
    """
    given output larger than the preview cap
    when seal renders it
    then truncation is marked and the true byte count is still reported
    """
    job_id, job_dir = job
    write(job_dir, "probes/F-1.out", "a" * 10_000)

    result = review("seal", job_id, "probes/F-1.out", "--bytes", "100")

    payload = result.envelope["payload"]
    assert payload["truncated"] is True
    assert payload["bytes"] == 10_000
    assert len(payload["preview"]) < 10_000


def should_never_bound_a_command_script(review, job):
    """
    given a probe command script larger than the ordinary cap
    when seal renders it
    then it renders in full — a partly shown script cannot support an approval
    """
    job_id, job_dir = job
    script = "echo " + "x" * 500 + "\n"
    write(job_dir, "staging/probe-F-1.sh", script)

    result = review("seal", job_id, "staging/probe-F-1.sh", "--bytes", "100")

    payload = result.envelope["payload"]
    assert payload["truncated"] is False
    assert payload["preview"].count("x") == 500


def should_refuse_an_oversize_command_script(review, job):
    """
    given a command script exceeding the complete-preview cap
    when seal runs
    then it exits 6 rather than showing part of it
    """
    job_id, job_dir = job
    write(job_dir, "staging/probe-F-1.sh", "echo " + "x" * (64 * 1024) + "\n")

    result = review("seal", job_id, "staging/probe-F-1.sh")

    assert result.exit_code == 6, result
