"""Section 3 — executable resolution: $CODEX_BIN, else PATH; unresolvable is exit 7 at preflight."""

import json
from pathlib import Path


def should_refuse_at_preflight_when_codex_cannot_be_resolved(review, plan_file_factory):
    """
    given no CODEX_BIN and no codex on PATH
    when prepare runs
    then it exits 7 before any capture, with null identity because no job exists
    """
    result = review("prepare", "plan", str(plan_file_factory()), codex_bin=None)

    assert result.exit_code == 7, result
    assert result.envelope["job_id"] is None
    assert result.envelope["job_dir"] is None


def should_prefer_codex_bin_over_path(review, plan_file_factory, fake_codex):
    """
    given CODEX_BIN naming an executable
    when prepare runs
    then it resolves that binary and records its absolute path in target.json
    """
    result = review("prepare", "plan", str(plan_file_factory()))

    assert result.exit_code == 0, result
    target = json.loads((Path(result.envelope["job_dir"]) / "target.json").read_text())
    assert target["codex_path"] == str(fake_codex)


def should_refuse_a_codex_bin_that_is_not_executable(review, plan_file_factory, tmp_path):
    """
    given CODEX_BIN naming a file that exists but is not executable
    when prepare runs
    then it exits 7 — present is not the same as usable
    """
    not_executable = tmp_path / "inert-codex"
    not_executable.write_text("#!/bin/bash\n")
    not_executable.chmod(0o644)

    result = review("prepare", "plan", str(plan_file_factory()), codex_bin=not_executable)

    assert result.exit_code == 7, result
