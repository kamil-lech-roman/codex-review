"""Sections 4 and 5 — artifact classes, the dependency graph, and stage derivation."""

import json
import os

#: Lifecycle artifact → what the graph requires (§4).
REQUIRES = (
    ("target.json", None),
    ("response.json", "target.json"),
    ("report.json", "response.json"),
    ("run-error.json", "response.json"),
    ("probes.json", "report.json"),
    ("apply-input.json", "probes.json"),
    ("apply.json", "apply-input.json"),
    ("apply-error.json", "apply-input.json"),
    ("verification.json", "apply.json"),
    ("abandoned.json", "target.json"),
)

TERMINAL_RECORDS = (
    "verification.json", "apply-error.json", "run-error.json",
    "invalidated.json", "abandoned.json",
)

#: Pass 2, in order. The first artifact present decides (§5).
DERIVATION = (
    ("abandoned.json", "abandoned"),
    ("verification.json", "applied"),
    ("apply-error.json", "failed"),
    ("run-error.json", "failed"),
    ("invalidated.json", "failed"),
    ("apply.json", "applied-unverified"),
)

NEXT_VERB = {
    "prepared": "run",
    "reviewed": "record-probes",
    "probed": "record-apply",
    "applied-unverified": "record-verification",
}


def _present(job_dir, name):
    return os.path.exists(os.path.join(job_dir, name))


def _target_unavailable(job_dir):
    """A degraded abandonment suspends the target.json requirement for the whole job (§5)."""
    path = os.path.join(job_dir, "abandoned.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle).get("target_unavailable") is True
    except (ValueError, OSError):
        return False


def validate(job_dir):
    """Pass 1 — structural validation. Returns the list of violations."""
    violations = []
    capture_waived = _target_unavailable(job_dir)

    for artifact, required in REQUIRES:
        if required is None or not _present(job_dir, artifact):
            continue
        if capture_waived and required == "target.json":
            continue
        if not _present(job_dir, required):
            violations.append({
                "rule": "prerequisites",
                "artifact": artifact,
                "requires": required,
            })

    terminal = [name for name in TERMINAL_RECORDS if _present(job_dir, name)]
    if len(terminal) > 1:
        violations.append({"rule": "mutual exclusion", "records": terminal})

    if _present(job_dir, "response.json") and not any(
        _present(job_dir, name)
        for name in ("report.json", "run-error.json", "invalidated.json")
    ):
        violations.append({"rule": "response resolution", "artifact": "response.json"})

    if _present(job_dir, "apply-input.json") and not any(
        _present(job_dir, name) for name in ("apply.json", "apply-error.json")
    ):
        violations.append({"rule": "apply resolution", "artifact": "apply-input.json"})

    return violations


def derive(job_dir):
    """Pass 2 — only on a structurally valid job."""
    for artifact, stage in DERIVATION:
        if _present(job_dir, artifact):
            return stage
    if _present(job_dir, "apply-input.json"):
        return "inconsistent"
    if _present(job_dir, "probes.json"):
        return "probed"
    if _present(job_dir, "report.json"):
        return "reviewed"
    if _present(job_dir, "target.json"):
        return "prepared"
    return "inconsistent"


def is_terminal(stage):
    return stage in ("abandoned", "applied", "failed")


def describe(job_dir):
    violations = validate(job_dir)
    if violations:
        return {
            "stage": "inconsistent",
            "resumable": False,
            "next_verb": None,
            "violations": violations,
        }
    stage = derive(job_dir)
    return {
        "stage": stage,
        "resumable": stage in NEXT_VERB,
        "next_verb": NEXT_VERB.get(stage),
        "violations": [],
    }
