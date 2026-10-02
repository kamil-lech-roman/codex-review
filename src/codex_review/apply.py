"""Section 11 — reconciling what was attempted against what the bytes say."""

import os

from codex_review import images, probes, projections, response

require = response._require
ContractViolation = response.ContractViolation

RESULTS = ("applied", "no-change", "denied", "failed")

#: Bumped when the shape of `pre_apply_baseline` changes. A baseline written by an older
#: driver carries no version and is read as the worktree manifest it was.
BASELINE_VERSION = 2

#: What a verification record cites, so the outcome stays checkable after
#: staging is gone.
VERIFICATION_CITATIONS = ("command_evidence_id", "output_evidence_id",
                          "rc_evidence_id", "denial_kind", "asserted_by")
#: Which results account for a change in their declared paths. `no-change` positively
#: claims nothing landed, so it is the one outcome the bytes can refute.
ACCOUNTS = {"applied": True, "denied": True, "failed": True, "no-change": False}

VERIFICATION_RESULTS = ("passed", "failed", "denied", "not-run-explicitly",
                        "not-applicable", "not-reached")


def identity_sha(target):
    """pre_fix_sha / post_fix_sha digest the whole identity (§3)."""
    if target["mode"] == "plan":
        path = target["plan_identity"]["plan_path"]
        parts = ["plan:" + path, repr(sorted(images.image_of(path).items()))]
        if target.get("context_repository"):
            parts.append(repr(sorted(
                projections.repo_identity(target["context_repository"]).items())))
    else:
        parts = [repr(sorted(projections.repo_identity(target["repo_root"]).items()))]
    return images.sha256_bytes("\n".join(parts).encode("utf-8"))


def baseline(target):
    """The pre-apply sample, bound at record-probes (§11). What the apply is measured against.

    Both git projections, because both can hold a defect and each is corrected on its own:
    staging a fix moves the index and not the worktree.
    """
    if target["mode"] == "plan":
        path = target["plan_identity"]["plan_path"]
        return {path: images.image_of(path)}
    root = target["repo_root"]
    return {"baseline_version": BASELINE_VERSION,
            "projections": {"worktree": projections.worktree_manifest(root),
                            "index": projections.index_manifest(root)}}


def _sampled_projections(captured):
    """Which projections a recorded baseline holds, over every shape one can have.

    Versioned baselines say so. One shape predates the marker: a bare
    ``{"projections": {"worktree": …, "index": …}}``, written by jobs prepared between the
    two changes. Everything else is the original path-to-image manifest — including one
    holding a path called ``projections``, which is why the marker exists at all.
    """
    if not isinstance(captured, dict):
        return {"worktree": captured}
    if captured.get("baseline_version") == BASELINE_VERSION:
        return captured["projections"]
    inner = captured.get("projections")
    if (set(captured) == {"projections"} and isinstance(inner, dict)
            and set(inner) == {"worktree", "index"}):
        return inner
    return {"worktree": captured}


def changed_paths(target, base=None):
    """What moved since the pre-apply baseline, over the target's delta universe (§2).

    Measured against the baseline rather than the capture: a change made before probing was
    not this apply's doing, and crediting it would let an untouched fix report as applied.
    """
    if target["mode"] == "plan":
        path = target["plan_identity"]["plan_path"]
        before = (base or {}).get(path, target["plan_identity"]["plan_image"])
        if images.image_of(path) != before:
            return [path]
        return []
    root = target["repo_root"]
    captured = base if base is not None else (target.get("worktree_manifest") or {})
    sampled = _sampled_projections(captured)
    now = {"worktree": projections.worktree_manifest(root),
           "index": projections.index_manifest(root)}
    absent = {"kind": images.KIND_ABSENT, "git_mode": None, "content_identity": None}
    moved = set()
    for projection, before in sampled.items():
        after = now[projection]
        for path in set(before) | set(after):
            if after.get(path, absent) != before.get(path, absent):
                moved.add(os.path.join(root, path))
    return sorted(moved)


def _declared(target, annotation):
    """A finding's own declared paths, absolute."""
    if target["mode"] == "plan":
        root = os.path.dirname(target["plan_identity"]["plan_path"])
    else:
        root = target["repo_root"]
    return [probes.resolve_keeping_link(os.path.join(root, p))
            for p in annotation.get("fix_paths", [])]


def reconcile(target, probes_record, payload, moved):
    """Returns (results, application_result, violating_paths)."""
    require(isinstance(payload, dict), "bad-payload", "apply payload is not an object")
    require("verification" not in payload, "verification-in-apply",
            "verification has not run yet, so a result for it could only be fabricated")

    results = payload.get("results")
    require(isinstance(results, list), "bad-results", "results must be a list")

    accepted = {a["finding_id"]: a for a in probes_record["annotations"]
                if a.get("disposition") == "accepted"}
    seen = []
    by_finding = {}
    for item in results:
        require(isinstance(item, dict), "bad-result", "a result is not an object")
        finding_id = item.get("finding_id")
        require(finding_id in accepted, "unknown-result-finding",
                "result names a finding that was not accepted", finding_id=finding_id)
        require(finding_id not in seen, "duplicate-result",
                "one result per accepted finding", finding_id=finding_id)
        seen.append(finding_id)
        require(item.get("result") in RESULTS, "bad-result-value",
                "unrecognised result", finding_id=finding_id)
        if item["result"] == "denied":
            require(item.get("denial_kind") in ("policy", "interactive"), "bad-denial-kind",
                    "a denial needs a kind", finding_id=finding_id)
            require(item.get("asserted_by"), "denial-unattributed",
                    "a denial needs an asserter", finding_id=finding_id)
        by_finding[finding_id] = dict(item)

    missing = sorted(set(accepted) - set(seen))
    require(not missing, "missing-results", "every accepted finding needs a result",
            missing=missing)

    union = set()
    for finding_id, annotation in accepted.items():
        union.update(_declared(target, annotation))

    violating = sorted(path for path in moved if path not in union)
    if violating:
        return list(by_finding.values()), "scope-violation", violating

    if not accepted:
        # Nothing was authorised, and by the check above nothing moved.
        return [], "none-accepted", []

    # Normalisation: an `applied` claim whose own paths did not move becomes `no-change`.
    for finding_id, item in by_finding.items():
        own = set(_declared(target, accepted[finding_id]))
        item["own_paths_moved"] = sorted(own & set(moved))
        if item["result"] == "applied" and not item["own_paths_moved"]:
            item["result"] = "no-change"
            item["normalised_from"] = "applied"

    # Every changed in-scope path needs at least one accounting declarer.
    for path in moved:
        declarers = [
            finding_id for finding_id, item in by_finding.items()
            if path in set(_declared(target, accepted[finding_id])) and ACCOUNTS[item["result"]]
        ]
        require(declarers, "unaccounted-change",
                "a change inside declared scope that no result accounts for", path=path)

    if target.get("no_apply"):
        # The flag promised a review-only run, so a moved byte refutes the promise
        # rather than becoming a partial fix. Normalisation has already run, so an
        # `applied` claim that moved nothing reads as `no-change` here too.
        require(not moved, "no-apply-moved",
                "a review-only run edited the working tree", changed_paths=sorted(moved))
        return list(by_finding.values()), "skipped-no-apply-flag", []

    incomplete = any(item["result"] in ("no-change", "denied", "failed")
                     for item in by_finding.values())
    result = "apply-incomplete" if incomplete else "applied-complete"
    return list(by_finding.values()), result, []


EVIDENCE_MATRIX = {
    "passed": (True, True, True, False),
    "failed": (True, True, True, False),
    "denied": (True, False, False, True),
    "not-run-explicitly": (False, False, False, False),
    "not-applicable": (False, False, False, False),
    "not-reached": (False, False, False, False),
}


VERIFICATION_SLOTS = {
    "command_evidence_id": "staging/verify.sh",
    "output_evidence_id": "verify.out",
    "rc_evidence_id": "verify.rc",
}


def sealed_bytes(job_dir, evidence_id):
    with open(os.path.join(job_dir, "evidence", evidence_id, "content"), "rb") as handle:
        return handle.read()


def check_verification_command(job_dir, job_id, target, block):
    """The sealed script must be the configured command byte-for-byte — no trimming and no
    equivalence rule, because each such rule is a place two different commands compare
    equal. A denial rests on it too: declining to run a script presupposes the script."""
    probes._rehash(job_dir, job_id, block.get("command_evidence_id"), "staging/verify.sh")
    expected = (target["verify_command"] + "\n").encode("utf-8")
    sealed = sealed_bytes(job_dir, block["command_evidence_id"])
    require(sealed == expected, "verify-command-mismatch",
            "the sealed verification script is not the configured command",
            configured=target["verify_command"],
            sealed_sha256=images.sha256_bytes(sealed),
            expected_sha256=images.sha256_bytes(expected))


def derive_verification(job_dir, job_id, block):
    """`passed`/`failed` come from the sealed `verify.rc`, so each citation is bound to its
    slot and rehashed before the status is read out of it (§9)."""
    for key in ("output_evidence_id", "rc_evidence_id"):
        probes._rehash(job_dir, job_id, block.get(key), VERIFICATION_SLOTS[key])
    raw = sealed_bytes(job_dir, block["rc_evidence_id"])
    require(probes._canonical_rc(raw), "bad-return-code",
            "verification return-code evidence must be a single canonical shell status",
            bytes=len(raw))
    return "passed" if int(raw.decode("ascii").strip()) == 0 else "failed"


def check_verification_evidence(result, block):
    """The matrix decides which citations a result may carry; `passed`/`failed` are then
    derived from the sealed rc by `derive_verification`, never asserted."""
    wants_command, wants_output, wants_rc, wants_denial = EVIDENCE_MATRIX[result]
    for key, wanted in (("command_evidence_id", wants_command),
                        ("output_evidence_id", wants_output),
                        ("rc_evidence_id", wants_rc)):
        present = block.get(key) is not None
        require(present == wanted, "verification-evidence",
                "verification evidence does not match what this result requires",
                result=result, field=key, present=present, required=wanted)
    for key in ("denial_kind", "asserted_by"):
        present = block.get(key) is not None
        require(present == wants_denial, "verification-evidence",
                "verification denial fields do not match this result",
                result=result, field=key, present=present, required=wants_denial)
    if wants_denial:
        require(block.get("denial_kind") in probes.DENIAL_KINDS, "bad-denial-kind",
                "a denial needs a kind the contract defines",
                kind=block.get("denial_kind"), allowed=list(probes.DENIAL_KINDS))
        require(block.get("asserted_by"), "denial-unattributed",
                "a denial needs an asserter")


def verification_result(submitted, target, application_result, moved):
    """One precedence, total over every combination (§8).

    The derivation is the source of truth for the non-run results: a submission that names a
    different one is refused, naming both, rather than replaced. `scope-violation` never
    reaches here — record-apply closes it as `not-reached` without an apply.json to verify.
    """
    if application_result == "scope-violation":
        return "not-reached"
    if target.get("verify_command") is None:
        return _agree_with_derivation(submitted, "not-run-explicitly")
    if not moved:
        return _agree_with_derivation(submitted, "not-applicable")
    require(submitted in ("passed", "failed", "denied"), "bad-verification-result",
            "the command ran, so the result must say how it went", result=submitted)
    return submitted


def _agree_with_derivation(submitted, derived):
    require(submitted == derived, "verification-result-contradicted",
            "the submitted result contradicts the one derived from the job; the derivation "
            "decides (no verify command → not-run-explicitly, then nothing landed → "
            "not-applicable)",
            submitted=submitted, derived=derived)
    return derived
