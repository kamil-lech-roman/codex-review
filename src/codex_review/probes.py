"""Section 8 — probe annotations, their integrity rules, and fix_paths canonicalisation."""

import os
import posixpath

from codex_review import images, response

READS_INTRODUCED = ("confirmed-introduced", "confirmed-pre-existing",
                    "unsupported", "inconclusive")
READS_PRE_EXISTING = ("confirmed-pre-existing", "unsupported", "inconclusive")
DISPOSITIONS = ("accepted", "rejected", "inconclusive")
DENIAL_KINDS = ("policy", "interactive")

require = response._require
ContractViolation = response.ContractViolation


EVIDENCE_ID = __import__("re").compile(r"^[0-9a-f]{16}$")


def _rehash(job_dir, job_id, evidence_id, expected_source):
    """Sealed evidence is rehashed on citation, and bound to its job and slot (§9)."""
    require(isinstance(evidence_id, str) and EVIDENCE_ID.match(evidence_id or ""),
            "bad-evidence-id", "evidence id is not an evidence id",
            evidence_id=evidence_id)
    directory = os.path.join(job_dir, "evidence", evidence_id)
    meta_path = os.path.join(directory, "metadata.json")
    require(os.path.isfile(meta_path), "unknown-evidence",
            "cited evidence does not exist", evidence_id=evidence_id)
    import json
    with open(meta_path, encoding="utf-8") as handle:
        meta = json.load(handle)
    require(meta.get("job_id") == job_id, "evidence-wrong-job",
            "cited evidence belongs to another job",
            evidence_id=evidence_id, owner=meta.get("job_id"))
    require(meta["source_path"] == expected_source, "evidence-wrong-slot",
            "cited evidence did not originate from the slot it fills",
            evidence_id=evidence_id, expected=expected_source, actual=meta["source_path"])
    with open(os.path.join(directory, "content"), "rb") as handle:
        actual = images.sha256_bytes(handle.read())
    require(actual == meta["sha256"], "evidence-tampered",
            "sealed bytes no longer match their recorded digest", evidence_id=evidence_id)
    return meta


def resolve_keeping_link(path):
    """Resolve ancestors but keep the final component's own name: a symlink's identity is
    the link, so repointing it is a change to that path, not to its referent."""
    return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))


def canonicalise_fix_paths(target, paths, finding_id):
    """Resolve first, then require containment; a plan admits exactly one legal path (§8)."""
    require(isinstance(paths, list) and paths, "empty-fix-paths",
            "an accepted finding needs at least one fix path", id=finding_id)
    if target["mode"] == "plan":
        root = os.path.dirname(target["plan_identity"]["plan_path"])
        allowed = os.path.realpath(target["plan_identity"]["plan_path"])
    else:
        root = target["repo_root"]
        allowed = None

    seen = []
    for raw in paths:
        require(isinstance(raw, str) and raw, "bad-fix-path", "fix path must be a string",
                id=finding_id)
        require(not os.path.isabs(raw), "absolute-fix-path",
                "fix paths must be relative", id=finding_id, path=raw)
        normalised = posixpath.normpath(raw)
        require(".." not in normalised.split("/"), "escaping-fix-path",
                "fix path escapes its root", id=finding_id, path=raw)
        resolved = resolve_keeping_link(os.path.join(root, normalised))
        require(not os.path.isdir(resolved), "directory-fix-path",
                "fix path names a directory", id=finding_id, path=raw)
        if allowed is not None:
            require(resolved == allowed, "fix-path-not-the-plan",
                    "a plan target admits exactly one legal fix path",
                    id=finding_id, path=raw)
        else:
            require(resolved == root or resolved.startswith(root + os.sep),
                    "fix-path-outside-repo", "fix path resolves outside the repository",
                    id=finding_id, path=raw)
        require(normalised not in seen, "duplicate-fix-path",
                "a finding may not repeat a fix path", id=finding_id, path=raw)
        seen.append(normalised)
    return seen


def validate(job_dir, job_id, target, report, payload, report_digest):
    """Check a probes submission. Returns (annotations, probe_completion)."""
    require(isinstance(payload, dict), "bad-payload", "probes payload is not an object")
    require(payload.get("report_sha256") == report_digest, "stale-report-digest",
            "submission does not carry this job's report digest")

    annotations = payload.get("annotations")
    require(isinstance(annotations, list), "bad-annotations", "annotations must be a list")

    reported = {finding["id"]: finding for finding in report["findings"]}
    seen = []
    for annotation in annotations:
        require(isinstance(annotation, dict), "bad-annotation", "an annotation is not an object")
        finding_id = annotation.get("finding_id")
        require(finding_id in reported, "unknown-finding", "annotation names no reported finding",
                finding_id=finding_id)
        require(finding_id not in seen, "duplicate-annotation",
                "exactly one annotation per finding", finding_id=finding_id)
        seen.append(finding_id)

        finding = reported[finding_id]
        pre_existing = finding["classification"] == "pre-existing"
        execution = annotation.get("probe_execution")
        require(execution in ("executed", "denied"), "bad-probe-execution",
                "unrecognised probe_execution", finding_id=finding_id)

        _rehash(job_dir, job_id, annotation.get("command_evidence_id"),
                "staging/probe-{}.sh".format(finding_id))

        out_path = os.path.join(job_dir, "probes", "{}.out".format(finding_id))
        rc_path = os.path.join(job_dir, "probes", "{}.rc".format(finding_id))
        if execution == "denied":
            for key in ("output_evidence_id", "rc_evidence_id"):
                require(key not in annotation, "denied-carries-output",
                        "a denied probe carries no output evidence", finding_id=finding_id)
            # Withholding the ids is not a proof: a command that ran leaves both files.
            require(not os.path.exists(out_path) and not os.path.exists(rc_path),
                    "denial-refuted", "probe output exists, so the probe was not denied",
                    finding_id=finding_id)
            require(annotation.get("denial_kind") in DENIAL_KINDS, "bad-denial-kind",
                    "a denial needs a kind", finding_id=finding_id)
            require(annotation.get("asserted_by"), "denial-unattributed",
                    "a denial needs an asserter", finding_id=finding_id)
            require(annotation.get("read") == "inconclusive", "denial-read",
                    "a denied probe forces read: inconclusive", finding_id=finding_id)
        else:
            _rehash(job_dir, job_id, annotation.get("output_evidence_id"),
                    "probes/{}.out".format(finding_id))
            rc_meta = _rehash(job_dir, job_id, annotation.get("rc_evidence_id"),
                              "probes/{}.rc".format(finding_id))
            with open(os.path.join(job_dir, "evidence", annotation["rc_evidence_id"], "content"),
                      "rb") as handle:
                raw_rc = handle.read()
            require(_canonical_rc(raw_rc), "bad-return-code",
                    "return-code evidence must be a single canonical shell status",
                    finding_id=finding_id, bytes=len(raw_rc))
            del rc_meta

        reads = READS_PRE_EXISTING if pre_existing else READS_INTRODUCED
        require(annotation.get("read") in reads, "bad-read", "unrecognised read",
                finding_id=finding_id)

        if pre_existing:
            for key in ("disposition", "fix_paths"):
                require(key not in annotation, "pre-existing-carries-disposition",
                        "a pre-existing finding carries no disposition or fix paths",
                        finding_id=finding_id)
            require(annotation.get("rationale"), "missing-rationale",
                    "a pre-existing finding needs a rationale", finding_id=finding_id)
            continue

        disposition = annotation.get("disposition")
        require(disposition in DISPOSITIONS, "bad-disposition", "unrecognised disposition",
                finding_id=finding_id)
        if disposition == "accepted":
            require(execution == "executed", "accepted-without-execution",
                    "acceptance requires an executed probe", finding_id=finding_id)
            require(annotation.get("read") == "confirmed-introduced", "accepted-without-read",
                    "acceptance requires read: confirmed-introduced", finding_id=finding_id)
            require("fix_paths" in annotation, "accepted-without-fix-paths",
                    "acceptance requires fix paths", finding_id=finding_id)
            annotation["fix_paths"] = canonicalise_fix_paths(
                target, annotation["fix_paths"], finding_id)
        else:
            require("fix_paths" not in annotation, "unaccepted-carries-fix-paths",
                    "only an accepted finding carries fix paths", finding_id=finding_id)
            require(annotation.get("rationale"), "missing-rationale",
                    "a rejected or inconclusive finding needs a rationale",
                    finding_id=finding_id)

    missing = sorted(set(reported) - set(seen))
    require(not missing, "missing-annotations", "every reported finding needs an annotation",
            missing=missing)

    # Derived, never reported.
    completion = "incomplete" if any(
        a["probe_execution"] == "denied" for a in annotations
    ) else "complete"
    return annotations, completion


def _canonical_rc(raw):
    text = raw.decode("ascii", errors="replace")
    if not text.endswith("\n") or text.count("\n") != 1:
        return False
    body = text[:-1]
    if not body.isdigit() or (len(body) > 1 and body[0] == "0"):
        return False
    return 0 <= int(body) <= 255
