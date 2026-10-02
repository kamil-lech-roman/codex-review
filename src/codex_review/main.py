"""codex-review driver. Stdlib only, Python >= 3.9 (§2)."""

import datetime
import json
import os
import sys
import time

from codex_review import apply as apply_module
from codex_review import codex, evidence, git, images, jobs, policy, probes, projections, response, reviewer, stage, state

SCHEMA_VERSION = 1
DRIVER_VERSION = "0.1.0"

#: The ten driver verbs (§7).
VERBS = (
    "prepare",
    "run",
    "seal",
    "record-probes",
    "record-apply",
    "record-verification",
    "status",
    "accept-delta",
    "abandon",
    "break-lease",
)

EXIT_USAGE = 2
EXIT_TARGET_REFUSED = 3
EXIT_UNSUPPORTED = 6
EXIT_INVALIDATED = 4
EXIT_POLICY_REFUSED = 5
EXIT_CODEX_UNAVAILABLE = 7
EXIT_CONTRACT = 8
EXIT_SCOPE_VIOLATION = 10
EXIT_APPLY_INCOMPLETE = 11
EXIT_VERIFY_FAILED = 12
EXIT_VERIFY_DENIED = 13
EXIT_PROBES_INCOMPLETE = 14


def envelope(verb, exit_code, job_id=None, job_dir=None, error=None, payload=None):
    """The §8 envelope. `error` and `payload` are absent rather than null when unset."""
    result = {
        "schema_version": SCHEMA_VERSION,
        "verb": verb,
        "driver_version": DRIVER_VERSION,
        "exit": exit_code,
        "job_id": job_id,
        "job_dir": job_dir,
    }
    if error is not None:
        result["error"] = error
    if payload is not None:
        result["payload"] = payload
    return result


def usage_error(verb, code, message, **details):
    return envelope(
        verb=verb,
        exit_code=EXIT_USAGE,
        error={
            "code": code,
            "category": "usage",
            "message": message,
            "details": details,
        },
    )


def policy_refused(refusal):
    return envelope(
        verb="prepare",
        exit_code=EXIT_POLICY_REFUSED,
        error={
            "code": refusal.code,
            "category": "policy",
            "message": refusal.message,
            "details": refusal.details,
        },
    )


def target_refused(code, message, **details):
    return envelope(
        verb="prepare",
        exit_code=EXIT_TARGET_REFUSED,
        error={
            "code": code,
            "category": "target-resolution",
            "message": message,
            "details": details,
        },
    )


def resolve_limits(options):
    """The capture limits, or the usage refusal that says which one is not a number.

    Shared, because both modes read the same two options: the guard belongs with the
    options, not with whichever mode happened to be fixed first.
    """
    limits = {}
    for name, fallback in (("max_artifact", projections.MAX_ARTIFACT_BYTES),
                           ("max_bundle", projections.MAX_BUNDLE_BYTES)):
        try:
            limits[name] = int(options.get(name, fallback))
        except (TypeError, ValueError):
            return None, usage_error("prepare", "bad-limit",
                                     "capture limit is not a number",
                                     option="--" + name.replace("_", "-"),
                                     value=options.get(name))
    return limits, None


def prepare(argv):
    """Resolve and freeze a target (§3). Publishes a job, or refuses before one exists."""
    if not argv:
        return usage_error(
            "prepare", "no-target", "prepare needs `plan <path>` or `code [...]`"
        )
    codex_path = codex.resolve()
    if codex_path is None:
        # Preflight, before any capture: no job exists to carry the failure (§3, §12).
        return envelope(
            verb="prepare",
            exit_code=EXIT_CODEX_UNAVAILABLE,
            error={
                "code": "codex-unresolvable",
                "category": "preflight",
                "message": "codex is not resolvable via $CODEX_BIN or PATH",
                "details": {"codex_bin": os.environ.get("CODEX_BIN")},
            },
        )

    mode = argv[0]
    rest, options, bad = parse_common(argv[1:])
    if bad is not None:
        return usage_error("prepare", "bad-option", "unrecognised or incomplete option", option=bad)
    if mode == "plan":
        return prepare_plan(rest, codex_path, options)
    if mode == "code":
        return prepare_code(rest, codex_path, options)
    return usage_error("prepare", "unrecognised-mode", "unrecognised target mode", mode=mode)


def parse_common(argv):
    """Splits the COMMON options (§1) from positional arguments."""
    positional, options = [], {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--repo":
            if index + 1 >= len(argv):
                return None, None, token
            options["repo"] = argv[index + 1]
            index += 2
        elif token == "--denied-interactively":
            options["denied_interactively"] = True
            index += 1
        elif token in ("--model", "--effort", "--bytes", "--reason"):
            if index + 1 >= len(argv):
                return None, None, token
            options[token[2:]] = argv[index + 1]
            index += 2
        elif token in ("--uncommitted", "--no-apply", "--no-verify"):
            options[token[2:].replace("-", "_")] = True
            index += 1
        elif token in ("--base", "--head", "--verify", "--max-artifact", "--max-bundle", "--note",
                       "--probes-file", "--apply-file", "--verification-file"):
            if index + 1 >= len(argv):
                return None, None, token
            options[token[2:].replace("-", "_")] = argv[index + 1]
            index += 2
        elif token == "--no-repo":
            options["no_repo"] = True
            index += 1
        elif token.startswith("--"):
            return None, None, token
        else:
            positional.append(token)
            index += 1
    return positional, options, None


def resolve_context(options, cwd):
    """`--repo` → `--no-repo` → repo containing cwd → context-free (§3)."""
    if options.get("repo"):
        return git.repo_root(os.path.abspath(options["repo"]))
    if options.get("no_repo"):
        return None
    return git.repo_root(cwd)


def refuse_unpinned_head(repo_root, pinned, sampled):
    """Refuse when the repository is not on the commit the caller pinned (§3).

    The committed projection is `base..HEAD` of the working directory, so a commit landing
    between choosing the base and preparing widens the target without saying so — in one
    round that captured another session's files and produced findings about them.
    """
    if not pinned:
        return None
    resolved = git.run(("rev-parse", "--verify", "-q", pinned + "^{commit}"),
                       cwd=repo_root, check=False)
    if resolved.returncode != 0:
        return target_refused("unresolvable-head", "--head does not resolve", head=pinned)
    resolved = resolved.stdout.strip()
    if resolved != sampled:
        return target_refused(
            "head-moved", "the repository is not on the pinned commit",
            pinned=pinned, pinned_oid=resolved, head_oid=sampled)
    return None


def prepare_code(argv, codex_path, options):
    """Capture a code target as projections over one sampled RepoIdentity (§3)."""
    cwd = os.getcwd()
    repo_root = git.repo_root(options.get("repo") and os.path.abspath(options["repo"]) or cwd)
    if repo_root is None:
        return target_refused("no-repository", "no repository contains the working directory",
                              cwd=cwd)

    uncommitted = bool(options.get("uncommitted"))
    base = options.get("base")
    if not uncommitted:
        if not base:
            upstream = git.run(("rev-parse", "--abbrev-ref", "@{upstream}"),
                               cwd=repo_root, check=False)
            if upstream.returncode != 0:
                return target_refused(
                    "no-base", "no upstream is configured and no --base was given",
                    repo_root=repo_root)
            base = upstream.stdout.strip()
        resolved = git.run(("rev-parse", base), cwd=repo_root, check=False)
        if resolved.returncode != 0:
            return target_refused("unresolvable-base", "--base does not resolve", base=base)
        base = resolved.stdout.strip()

    identity = projections.repo_identity(repo_root)
    refusal = refuse_unpinned_head(repo_root, options.get("head"), identity["head_oid"])
    if refusal is not None:
        return refusal
    limits, refusal = resolve_limits(options)
    if refusal is not None:
        return refusal

    try:
        projections.refuse_bad_index_states(repo_root)
        entries = (projections.uncommitted_changes(repo_root) if uncommitted
                   else projections.committed_changes(repo_root, base))
        if not entries:
            # A review of nothing cannot find anything, and reports `approve` — which reads
            # as convergence rather than as the vacuous answer it is.
            return target_refused(
                "empty-target", "the target contains no artifacts",
                repo_root=repo_root,
                **({"uncommitted": True} if uncommitted else {"base": base}))
        sides = []
        for entry in entries:
            before = projections.read_side(repo_root, entry["_before_source"])
            after = projections.read_side(repo_root, entry["_after_source"])
            sides.append((before, after))
        projections.enforce_limits(
            [(entry["path"], len(before) + len(after))
             for entry, (before, after) in zip(entries, sides)],
            limits["max_artifact"], limits["max_bundle"],
        )
    except projections.CaptureRefused as refusal:
        return envelope(
            verb="prepare", exit_code=refusal.exit_code,
            error={"code": refusal.code, "category": "capture",
                   "message": refusal.message, "details": refusal.details},
        )

    # An unborn HEAD names no tree. Its policy is empty, which is what the empty tree holds.
    policy_commit = base if not uncommitted else identity["head_oid"]
    if policy_commit == projections.UNBORN:
        policy_commit = projections.EMPTY_TREE

    try:
        sources, texts, noted = policy.capture(repo_root, policy_commit)
    except policy.PolicyRefused as refusal:
        return policy_refused(refusal)

    root = state.state_root()
    nonce = state.new_nonce()
    job_dir = os.path.join(root, nonce)
    try:
        agents_bytes = policy.synthesize(
            job_dir=job_dir, mode="code", context_repository=repo_root,
            captured_sources=sources, captured_texts=texts)
    except policy.PolicyRefused as refusal:
        return policy_refused(refusal)

    detail = []
    for number, (entry, (before, after)) in enumerate(zip(entries, sides), start=1):
        detail.append({
            "id": "A{:04d}".format(number),
            "path": entry["path"],
            "projection": entry.get("projection", "committed"),
            "before": entry["before"],
            "after": entry["after"],
        })

    target = {
        "mode": "code",
        "repo_root": repo_root,
        "base_ref": None if uncommitted else base,
        "uncommitted": uncommitted,
        "artifacts": [item["id"] for item in detail],
        "artifacts_detail": detail,
        "verify_command": None if options.get("no_verify") else options.get("verify"),
        "no_apply": bool(options.get("no_apply")),
        "note": options.get("note"),
        "model": options.get("model"),
        "effort": options.get("effort"),
        "exec_dir": repo_root,
        "target_sha": images.sha256_bytes(json.dumps(
            [[i["id"], i["path"], i["before"], i["after"]] for i in detail],
            sort_keys=True).encode("utf-8")),
        "codex_path": codex_path,
        "repo_identity": identity,
        "worktree_manifest": projections.worktree_manifest(repo_root),
        "context_repository": repo_root,
        "policy_commit": policy_commit,
        "policy_sources": list(sources),
        "policy_noted": list(noted),
        "synthesized_policy_sha256": images.sha256_bytes(agents_bytes),
    }

    # Capture coherence: the published set must correspond to one sampled identity (§3).
    if not projections.confirm_correspondence(
            repo_root, entries, identity, [after for _before, after in sides]):
        return envelope(
            verb="prepare", exit_code=EXIT_INVALIDATED,
            error={"code": "capture-race", "category": "capture",
                   "message": "the repository moved during capture", "details": {}},
        )

    os.makedirs(os.path.join(root, state.STAGING), exist_ok=True)
    build = state.staging_dir(root, nonce)
    with open(os.path.join(build, "AGENTS.md"), "wb") as handle:
        handle.write(agents_bytes)
    for item, (before, after) in zip(detail, sides):
        directory = os.path.join(build, "artifacts", item["id"])
        os.makedirs(directory)
        with open(os.path.join(directory, "before"), "wb") as handle:
            handle.write(before)
        with open(os.path.join(directory, "after"), "wb") as handle:
            handle.write(after)
        state.write_json(os.path.join(directory, "metadata.json"), item)
    state.write_json(os.path.join(build, "target.json"), target)
    state.publish(root, nonce, build)

    return envelope(
        verb="prepare", exit_code=0, job_id=nonce, job_dir=job_dir,
        payload={"target": {
            "mode": "code",
            "target_sha": target["target_sha"],
            "artifact_count": len(detail),
            "bytes": sum(len(b) + len(a) for b, a in sides),
            "resolved_path": repo_root,
        }},
    )


def prepare_plan(argv, codex_path, options):
    """Freeze a plan target and publish a job. Everything refusable happens before any write."""
    if not argv:
        return usage_error("prepare", "no-plan-path", "`plan` needs a path")
    if options.get("head"):
        # A plan target is one file, not a diff, so there is no HEAD for a pin to constrain;
        # honouring it silently would report a pin that never held.
        return usage_error("prepare", "head-not-applicable",
                           "`--head` pins a code target's HEAD; a plan target has none")
    given = os.path.abspath(argv[0])
    if not os.path.exists(given):
        return target_refused("missing-plan-path", "plan path does not exist", path=given)
    if os.path.islink(given):
        path = given
        # A symlinked final component would make the target two files at once (§3).
        return envelope(
            verb="prepare",
            exit_code=EXIT_UNSUPPORTED,
            error={
                "code": "symlink-plan-target",
                "category": "unsupported-target",
                "message": "plan target is a symlink; pass the referent instead",
                "details": {"path": path, "referent": os.path.realpath(path)},
            },
        )
    # One spelling from here on: an ancestor symlink must not make the target two names.
    path = os.path.realpath(given)

    context_repository = resolve_context(options, os.getcwd())
    policy_commit = git.head_oid(context_repository) if context_repository else None

    root = state.state_root()
    nonce = state.new_nonce()
    job_dir = os.path.join(root, nonce)

    try:
        sources, texts, noted = ((), (), ())
        if policy_commit:
            sources, texts, noted = policy.capture(context_repository, policy_commit, path)
        agents_bytes = policy.synthesize(
            job_dir=job_dir,
            mode="plan",
            context_repository=context_repository,
            captured_sources=sources,
            captured_texts=texts,
        )
    except policy.PolicyRefused as refusal:
        return policy_refused(refusal)

    image = images.image_of(path)
    # Freeze the bytes, not merely the identity: the reviewer must read what was captured,
    # not whatever the live file holds when it gets there (§3).
    with open(path, "rb") as handle:
        plan_bytes = handle.read()
    limits, refusal = resolve_limits(options)
    if refusal is not None:
        return refusal
    max_artifact, max_bundle = limits["max_artifact"], limits["max_bundle"]
    if len(plan_bytes) > min(max_artifact, max_bundle):
        return envelope(
            verb="prepare", exit_code=EXIT_UNSUPPORTED,
            error={"code": "artifact-too-large", "category": "capture",
                   "message": "the plan exceeds the capture cap",
                   "details": {"path": path, "bytes": len(plan_bytes),
                               "limit": min(max_artifact, max_bundle)}})
    # Correspondence: the copy must match the confirming sample, not merely the sample
    # before it — bytes changed and restored around the read would satisfy that.
    if images.image_of(path) != image or (
            image["content_identity"] != "sha256:" + images.sha256_bytes(plan_bytes)):
        return envelope(
            verb="prepare", exit_code=EXIT_INVALIDATED,
            error={"code": "capture-race", "category": "capture",
                   "message": "the plan moved during capture", "details": {"path": path}})
    artifact = {
        "id": "A0001",
        "path": os.path.basename(path),
        "before": {"kind": images.KIND_ABSENT, "git_mode": None, "content_identity": None},
        "after": image,
    }
    target = {
        "mode": "plan",
        "repo_root": None,
        "base_ref": None,
        "artifacts": ["A0001"],
        "artifacts_detail": [artifact],
        "verify_command": None if options.get("no_verify") else options.get("verify"),
        "no_apply": bool(options.get("no_apply")),
        "note": options.get("note"),
        "model": options.get("model"),
        "effort": options.get("effort"),
        # Plan + repo → the repository root; context-free → the plan file's directory (§9).
        "exec_dir": context_repository or os.path.dirname(path),
        "target_sha": images.target_sha(path, image),
        "codex_path": codex_path,
        "plan_identity": {"plan_path": path, "plan_image": image},
        "context_repository": context_repository,
        "policy_commit": policy_commit,
        "policy_sources": list(sources),
        "policy_noted": list(noted),
        "synthesized_policy_sha256": images.sha256_bytes(agents_bytes),
    }

    os.makedirs(os.path.join(root, state.STAGING), exist_ok=True)
    build = state.staging_dir(root, nonce)
    with open(os.path.join(build, "AGENTS.md"), "wb") as handle:
        handle.write(agents_bytes)
    directory = os.path.join(build, "artifacts", "A0001")
    os.makedirs(directory)
    with open(os.path.join(directory, "before"), "wb") as handle:
        handle.write(b"")
    with open(os.path.join(directory, "after"), "wb") as handle:
        handle.write(plan_bytes)
    state.write_json(os.path.join(directory, "metadata.json"), artifact)
    state.write_json(os.path.join(build, "target.json"), target)
    state.publish(root, nonce, build)

    return envelope(
        verb="prepare",
        exit_code=0,
        job_id=nonce,
        job_dir=job_dir,
        payload={
            "target": {
                "mode": "plan",
                "target_sha": target["target_sha"],
                "artifact_count": 1,
                "bytes": len(plan_bytes),
                "resolved_path": path,
            }
        },
    )


def driver_hash():
    """The driver's own identity: a rubric or capture change alters results like a model change."""
    here = os.path.dirname(os.path.abspath(__file__))
    digest = []
    for name in sorted(os.listdir(here)):
        if name.endswith(".py"):
            digest.append(name + ":" + images.sha256_file(os.path.join(here, name)))
    return images.sha256_bytes("\n".join(digest).encode("utf-8"))


def load_target(job_dir):
    """§3's usability predicate: the capture must yield identity, delta universe and current
    identity. A capture that reads but cannot yield those is unusable, same as an absent one."""
    try:
        with open(os.path.join(job_dir, "target.json"), encoding="utf-8") as handle:
            target = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(target, dict) or "mode" not in target:
        return None
    if target["mode"] == "plan" and not target.get("plan_identity", {}).get("plan_path"):
        return None
    return target


def abandon(argv):
    """Close a job deliberately (§7). Movement is recorded only when it can be determined."""
    rest, options, bad = parse_common(argv)
    if bad is not None:
        return usage_error("abandon", "bad-option", "unrecognised option", option=bad)
    if not rest:
        return usage_error("abandon", "no-job", "`abandon` needs a job")
    if not options.get("reason"):
        return usage_error("abandon", "no-reason", "`abandon` needs --reason")

    root = state.state_root()
    job_dir = jobs.resolve(root, rest[0])
    if job_dir is None:
        return usage_error("abandon", "unresolvable-job", "job does not resolve",
                           argument=rest[0])
    job_id = os.path.basename(job_dir)

    with jobs.mutex(job_dir):
        for name in stage.TERMINAL_RECORDS:
            if os.path.exists(os.path.join(job_dir, name)):
                return envelope(
                    verb="abandon", exit_code=EXIT_CONTRACT, job_id=job_id, job_dir=job_dir,
                    error={"code": "already-terminal", "category": "lifecycle",
                           "message": "the job is already closed",
                           "details": {"record": name}},
                )

        record = {
            "reason": options["reason"],
            "abandoned_at": datetime.datetime.utcnow().isoformat() + "Z",
            "asserted_by": "user",
        }
        if options.get("denied_interactively"):
            record["denial_kind"] = "interactive"

        target = load_target(job_dir)
        if target is None:
            # The delta universe is unknowable, so movement cannot be stated (§13).
            record["target_unavailable"] = True
        else:
            current, changed = current_movement(target)
            if changed:
                record["current_identity"] = current
                record["changed_paths"] = changed

        state.write_json(os.path.join(job_dir, "abandoned.json"), record)

    return envelope(verb="abandon", exit_code=0, job_id=job_id, job_dir=job_dir,
                    payload={"abandoned_at": record["abandoned_at"],
                             "reason": record["reason"]})


def current_movement(target):
    """Re-sample the target's identity and report what moved since capture (§2, §3)."""
    if target["mode"] == "code":
        root = target["repo_root"]
        if not os.path.isdir(root):
            return None, []
        current = projections.repo_identity(root)
        if current == target["repo_identity"]:
            return current, []
        moved = []
        for item in target.get("artifacts_detail", []):
            if images.image_of(os.path.join(root, item["path"])) != item["after"]:
                moved.append(item["path"])
        return current, moved or ["<repository moved>"]
    path = target["plan_identity"]["plan_path"]
    image = images.image_of(path)
    current = {"plan_path": path, "plan_image": image}
    if image != target["plan_identity"]["plan_image"]:
        return current, [path]
    return current, []


def next_attempt(job_dir):
    directory = os.path.join(job_dir, "attempts")
    if not os.path.isdir(directory):
        os.makedirs(directory)
    existing = sorted(name for name in os.listdir(directory) if name.isdigit())
    return os.path.join(directory, "{:03d}".format(len(existing) + 1))


def record_attempt(job_dir, verb, raw, error):
    """A rejected submission writes only attempts/NNN/; the job stays open (§12)."""
    directory = next_attempt(job_dir)
    os.makedirs(directory)
    if raw is not None:
        with open(os.path.join(directory, "input.raw"), "w", encoding="utf-8") as handle:
            handle.write(raw)
    state.write_json(os.path.join(directory, "error.json"), dict(error, verb=verb))


def record_probes(argv):
    """Record one annotation per reported finding, then derive probe_completion (§8)."""
    rest, options, bad = parse_common(argv)
    if bad is not None:
        return usage_error("record-probes", "bad-option", "unrecognised option", option=bad)
    if not rest:
        return usage_error("record-probes", "no-job", "`record-probes` needs a job")
    if not options.get("probes_file"):
        return usage_error("record-probes", "no-probes-file",
                           "`record-probes` needs --probes-file")

    root = state.state_root()
    job_dir = jobs.resolve(root, rest[0])
    if job_dir is None:
        return usage_error("record-probes", "unresolvable-job", "job does not resolve",
                           argument=rest[0])
    job_id = os.path.basename(job_dir)

    with jobs.mutex(job_dir):
        for name in stage.TERMINAL_RECORDS:
            if os.path.exists(os.path.join(job_dir, name)):
                return envelope(
                    verb="record-probes", exit_code=EXIT_CONTRACT, job_id=job_id,
                    job_dir=job_dir,
                    error={"code": "already-terminal", "category": "lifecycle",
                           "message": "the job is already closed",
                           "details": {"record": name}})

        report_path = os.path.join(job_dir, "report.json")
        if not os.path.exists(report_path):
            return envelope(
                verb="record-probes", exit_code=EXIT_CONTRACT, job_id=job_id, job_dir=job_dir,
                error={"code": "not-reviewed", "category": "lifecycle",
                       "message": "this job has no report to annotate", "details": {}})

        raw = None
        try:
            with open(options["probes_file"], encoding="utf-8") as handle:
                raw = handle.read()
            payload = json.loads(raw)
            with open(report_path, encoding="utf-8") as handle:
                report = json.load(handle)
            target = load_target(job_dir)
            if os.path.exists(os.path.join(job_dir, "probes.json")):
                raise probes.ContractViolation(
                    "already-recorded", "this job already has probes.json")
            annotations, completion = probes.validate(
                job_dir, job_id, target, report, payload,
                images.sha256_file(report_path))
        except (OSError, ValueError) as error:
            violation = probes.ContractViolation(
                "unreadable-submission", "probes submission could not be read",
                detail=str(error))
            record_attempt(job_dir, "record-probes", raw, {
                "code": violation.code, "message": violation.message,
                "details": violation.details})
            return envelope(
                verb="record-probes", exit_code=EXIT_CONTRACT, job_id=job_id, job_dir=job_dir,
                error={"code": violation.code, "category": "contract",
                       "message": violation.message, "details": violation.details})
        except probes.ContractViolation as violation:
            record_attempt(job_dir, "record-probes", raw, {
                "code": violation.code, "message": violation.message,
                "details": violation.details})
            return envelope(
                verb="record-probes", exit_code=EXIT_CONTRACT, job_id=job_id, job_dir=job_dir,
                error={"code": violation.code, "category": "contract",
                       "message": violation.message, "details": violation.details})

        state.write_json(os.path.join(job_dir, "probes.json"), {
            "report_sha256": payload["report_sha256"],
            "pre_fix_sha": apply_module.identity_sha(target),
            "pre_apply_baseline": apply_module.baseline(target),
            "annotations": annotations,
            "probe_completion": completion,
        })

    counts = {
        "accepted": sum(1 for a in annotations if a.get("disposition") == "accepted"),
        "rejected": sum(1 for a in annotations if a.get("disposition") == "rejected"),
        "inconclusive": sum(1 for a in annotations if a.get("disposition") == "inconclusive"),
    }
    return envelope(
        verb="record-probes",
        exit_code=EXIT_PROBES_INCOMPLETE if completion == "incomplete" else 0,
        job_id=job_id, job_dir=job_dir,
        payload={"probe_completion": completion, "counts": counts,
                 "probes_sha256": images.sha256_file(os.path.join(job_dir, "probes.json"))})


def _open_recording(verb, argv, option, requires):
    """Shared preamble: resolve the job, refuse a closed one, load what is consumed."""
    rest, options, bad = parse_common(argv)
    if bad is not None:
        return None, usage_error(verb, "bad-option", "unrecognised option", option=bad)
    if not rest:
        return None, usage_error(verb, "no-job", "`{}` needs a job".format(verb))
    if not options.get(option):
        return None, usage_error(verb, "no-input", "`{}` needs --{}".format(
            verb, option.replace("_", "-")))

    root = state.state_root()
    job_dir = jobs.resolve(root, rest[0])
    if job_dir is None:
        return None, usage_error(verb, "unresolvable-job", "job does not resolve",
                                 argument=rest[0])
    job_id = os.path.basename(job_dir)
    for name in requires:
        if not os.path.exists(os.path.join(job_dir, name)):
            return None, envelope(
                verb=verb, exit_code=EXIT_CONTRACT, job_id=job_id, job_dir=job_dir,
                error={"code": "wrong-stage", "category": "lifecycle",
                       "message": "this job is not at the stage {} needs".format(verb),
                       "details": {"missing": name}})
    return (job_dir, job_id, options), None


def refuse_if_terminal(verb, job_dir, job_id):
    """Must be called INSIDE the job mutex: a concurrent close would otherwise slip between
    the check and the write, leaving two terminal records."""
    for name in stage.TERMINAL_RECORDS:
        if os.path.exists(os.path.join(job_dir, name)):
            return envelope(
                verb=verb, exit_code=EXIT_CONTRACT, job_id=job_id, job_dir=job_dir,
                error={"code": "already-terminal", "category": "lifecycle",
                       "message": "the job is already closed", "details": {"record": name}})
    return None


def _contract_failure(verb, job_dir, job_id, raw, violation):
    record_attempt(job_dir, verb, raw, {
        "code": violation.code, "message": violation.message, "details": violation.details})
    return envelope(verb=verb, exit_code=EXIT_CONTRACT, job_id=job_id, job_dir=job_dir,
                    error={"code": violation.code, "category": "contract",
                           "message": violation.message, "details": violation.details})


def record_apply(argv):
    """Recompute what the bytes say and reconcile it against what was attempted (§11)."""
    resolved, refusal = _open_recording("record-apply", argv, "apply_file",
                                        ("probes.json",))
    if refusal is not None:
        return refusal
    job_dir, job_id, options = resolved

    with jobs.mutex(job_dir):
        closed = refuse_if_terminal("record-apply", job_dir, job_id)
        if closed is not None:
            return closed
        if os.path.exists(os.path.join(job_dir, "apply.json")):
            return envelope(verb="record-apply", exit_code=EXIT_CONTRACT, job_id=job_id,
                            job_dir=job_dir,
                            error={"code": "already-recorded", "category": "contract",
                                   "message": "this job already has apply.json",
                                   "details": {}})
        raw = None
        target = load_target(job_dir)
        try:
            with open(options["apply_file"], encoding="utf-8") as handle:
                raw = handle.read()
            payload = json.loads(raw)
            apply_module.require(isinstance(payload, dict), "bad-payload",
                                 "apply payload is not an object")
            with open(os.path.join(job_dir, "probes.json"), encoding="utf-8") as handle:
                probes_record = json.load(handle)
            apply_module.require(
                payload.get("probes_sha256") == images.sha256_file(
                    os.path.join(job_dir, "probes.json")),
                "stale-probes-digest", "submission does not carry this job's probes digest")
            apply_module.require(
                payload.get("report_sha256") == images.sha256_file(
                    os.path.join(job_dir, "report.json")),
                "stale-report-digest", "submission does not carry this job's report digest")
            moved = apply_module.changed_paths(
                target, probes_record.get("pre_apply_baseline"))
            results, application_result, violating = apply_module.reconcile(
                target, probes_record, payload, moved)
        except (OSError, ValueError) as error:
            return _contract_failure("record-apply", job_dir, job_id, raw,
                                     apply_module.ContractViolation(
                                         "unreadable-submission",
                                         "apply submission could not be read",
                                         detail=str(error)))
        except apply_module.ContractViolation as violation:
            return _contract_failure("record-apply", job_dir, job_id, raw, violation)

        pre_fix_sha = probes_record.get("pre_fix_sha")
        post_fix_sha = apply_module.identity_sha(target)
        state.write_json(os.path.join(job_dir, "apply-input.json"), payload)

        if application_result == "scope-violation":
            # Detected, never confined: the change stays in the tree (A2).
            state.write_json(os.path.join(job_dir, "apply-error.json"), {
                "application_result": "scope-violation",
                "verification_result": "not-reached",
                "probe_completion": probes_record["probe_completion"],
                "pre_fix_sha": pre_fix_sha,
                "violating_paths": violating,
                "declared_fix_paths": sorted({
                    path for a in probes_record["annotations"]
                    if a.get("disposition") == "accepted" for path in a.get("fix_paths", [])}),
            })
            return envelope(verb="record-apply", exit_code=EXIT_SCOPE_VIOLATION,
                            job_id=job_id, job_dir=job_dir,
                            payload={"application_result": "scope-violation",
                                     "changed_paths": moved,
                                     "violating_paths": violating})

        state.write_json(os.path.join(job_dir, "apply.json"), {
            "application_result": application_result,
            "probe_completion": probes_record["probe_completion"],
            "results": results,
            "changed_paths": moved,
            "pre_fix_sha": pre_fix_sha,
            "post_fix_sha": post_fix_sha,
        })

    return envelope(
        verb="record-apply",
        exit_code=EXIT_APPLY_INCOMPLETE if application_result == "apply-incomplete" else 0,
        job_id=job_id, job_dir=job_dir,
        payload={"application_result": application_result, "changed_paths": moved,
                 "probe_completion": probes_record["probe_completion"],
                 "pre_fix_sha": pre_fix_sha, "post_fix_sha": post_fix_sha,
                 "apply_sha256": images.sha256_file(os.path.join(job_dir, "apply.json"))})


LADDER = (
    ("application_result", "scope-violation", EXIT_SCOPE_VIOLATION),
    ("application_result", "apply-incomplete", EXIT_APPLY_INCOMPLETE),
    ("verification_result", "failed", EXIT_VERIFY_FAILED),
    ("verification_result", "denied", EXIT_VERIFY_DENIED),
    ("probe_completion", "incomplete", EXIT_PROBES_INCOMPLETE),
)


def record_verification(argv):
    """Close the apply workflow, evaluating the precedence ladder once (§12)."""
    resolved, refusal = _open_recording("record-verification", argv, "verification_file",
                                        ("apply.json",))
    if refusal is not None:
        return refusal
    job_dir, job_id, options = resolved

    with jobs.mutex(job_dir):
        closed = refuse_if_terminal("record-verification", job_dir, job_id)
        if closed is not None:
            return closed
        raw = None
        target = load_target(job_dir)
        try:
            with open(options["verification_file"], encoding="utf-8") as handle:
                raw = handle.read()
            payload = json.loads(raw)
            apply_module.require(isinstance(payload, dict), "bad-payload",
                                 "verification payload is not an object")
            apply_module.require(
                isinstance(payload.get("verification"), dict), "missing-verification-block",
                "the verification result belongs in a `verification` block")
            with open(os.path.join(job_dir, "apply.json"), encoding="utf-8") as handle:
                apply_record = json.load(handle)
            apply_module.require(
                payload.get("apply_sha256") == images.sha256_file(
                    os.path.join(job_dir, "apply.json")),
                "stale-apply-digest", "submission does not carry this job's apply digest")
            if apply_module.identity_sha(target) != apply_record["post_fix_sha"]:
                state.write_json(os.path.join(job_dir, "invalidated.json"), {
                    "at_stage": "record-verification",
                    "moved": ["post_fix_sha"],
                    "expected": apply_record["post_fix_sha"],
                })
                return envelope(
                    verb="record-verification", exit_code=EXIT_INVALIDATED,
                    job_id=job_id, job_dir=job_dir,
                    error={"code": "reference-drift", "category": "drift",
                           "message": "the tree moved after the apply was recorded",
                           "details": {"at_stage": "record-verification"}})
            block = payload["verification"]
            submitted = block.get("result")
            resolved_result = apply_module.verification_result(
                submitted, target, apply_record["application_result"],
                apply_record["changed_paths"])
            apply_module.check_verification_evidence(resolved_result, block)
            if resolved_result in ("passed", "failed", "denied"):
                apply_module.check_verification_command(job_dir, job_id, target, block)
            if resolved_result in ("passed", "failed"):
                derived = apply_module.derive_verification(job_dir, job_id, block)
                apply_module.require(
                    derived == resolved_result, "verification-contradicted",
                    "the sealed return code contradicts the submitted result",
                    submitted=resolved_result, derived=derived)
        except (OSError, ValueError) as error:
            return _contract_failure("record-verification", job_dir, job_id, raw,
                                     apply_module.ContractViolation(
                                         "unreadable-submission",
                                         "verification submission could not be read",
                                         detail=str(error)))
        except apply_module.ContractViolation as violation:
            return _contract_failure("record-verification", job_dir, job_id, raw, violation)

        record = {
            "application_result": apply_record["application_result"],
            "verification_result": resolved_result,
            "probe_completion": apply_record["probe_completion"],
            "changed_paths": apply_record["changed_paths"],
            "pre_fix_sha": apply_record["pre_fix_sha"],
            "post_fix_sha": apply_record["post_fix_sha"],
            "verification_evidence": {
                key: block[key] for key in apply_module.VERIFICATION_CITATIONS
                if block.get(key) is not None
            },
        }
        state.write_json(os.path.join(job_dir, "verification.json"), record)

    exit_code = 0
    for field, value, code in LADDER:
        if record[field] == value:
            exit_code = code
            break
    return envelope(verb="record-verification", exit_code=exit_code, job_id=job_id,
                    job_dir=job_dir, payload=record)


def status(argv):
    """Read-only: validate, then derive (§5). Shared locks only."""
    rest, options, bad = parse_common(argv)
    if bad is not None:
        return usage_error("status", "bad-option", "unrecognised option", option=bad)

    root = state.state_root()
    if not rest:
        return envelope(verb="status", exit_code=0, payload=listing(root))

    job_dir = jobs.resolve(root, rest[0])
    if job_dir is None:
        return usage_error("status", "unresolvable-job", "job does not resolve",
                           argument=rest[0])
    job_id = os.path.basename(job_dir)
    with jobs.mutex(job_dir, exclusive=False):
        payload = stage.describe(job_dir)
        payload["artifacts"] = sorted(
            name for name in os.listdir(job_dir) if not name.startswith(".")
        )
    return envelope(verb="status", exit_code=0, job_id=job_id, job_dir=job_dir,
                    payload=payload)


def listing(root):
    """The state root's open jobs and any stale staging builds (§8)."""
    open_jobs, stale = [], []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            # Listing and resolution share one rule, or status lists what no verb can close.
            if jobs.resolve(root, name) is None:
                continue
            described = stage.describe(path)
            if stage.is_terminal(described["stage"]):
                continue
            open_jobs.append({
                "job_id": name,
                "stage": described["stage"],
                "resumable": described["resumable"],
                "next_verb": described["next_verb"],
                "age": round(time.time() - os.path.getmtime(path), 3),
            })
        staging_root = os.path.join(root, state.STAGING)
        if os.path.isdir(staging_root):
            for name in sorted(os.listdir(staging_root)):
                stale.append({
                    "nonce": name,
                    "age": round(time.time() - os.path.getmtime(
                        os.path.join(staging_root, name)), 3),
                })
    return {"open_jobs": open_jobs, "stale_staging": stale}


def seal(argv):
    """Copy evidence into write-once job storage, then render it (§9)."""
    rest, options, bad = parse_common(argv)
    if bad is not None:
        return usage_error("seal", "bad-option", "unrecognised or incomplete option", option=bad)
    if len(rest) < 2:
        return usage_error("seal", "no-path", "`seal` needs a job and a path")

    root = state.state_root()
    job_dir = jobs.resolve(root, rest[0])
    if job_dir is None:
        return usage_error("seal", "unresolvable-job", "job does not resolve", argument=rest[0])
    job_id = os.path.basename(job_dir)

    cap = evidence.DEFAULT_PREVIEW_BYTES
    if options.get("bytes"):
        try:
            cap = int(options["bytes"])
        except ValueError:
            return usage_error("seal", "bad-bytes", "--bytes must be an integer",
                               value=options["bytes"])

    with jobs.mutex(job_dir):
        try:
            payload = evidence.seal(job_dir, job_id, rest[1], cap)
        except evidence.SealRefused as refusal:
            return envelope(
                verb="seal", exit_code=refusal.exit_code,
                job_id=job_id if refusal.exit_code != 2 else job_id,
                job_dir=job_dir,
                error={"code": refusal.code, "category": "evidence",
                       "message": refusal.message, "details": refusal.details},
            )
    return envelope(verb="seal", exit_code=0, job_id=job_id, job_dir=job_dir, payload=payload)


def run(argv):
    """Send the frozen target to Codex, persist the reply, then validate it (§3, §8)."""
    rest, options, bad = parse_common(argv)
    if bad is not None:
        return usage_error("run", "bad-option", "unrecognised or incomplete option", option=bad)
    if not rest:
        return usage_error("run", "no-job", "`run` needs a job")

    root = state.state_root()
    job_dir = jobs.resolve(root, rest[0])
    if job_dir is None:
        return usage_error("run", "unresolvable-job", "job does not resolve", argument=rest[0])

    codex_path = codex.resolve()
    if codex_path is None:
        return envelope(
            verb="run", exit_code=EXIT_CODEX_UNAVAILABLE, job_id=os.path.basename(job_dir),
            job_dir=job_dir,
            error={"code": "codex-unresolvable", "category": "preflight",
                   "message": "codex is not resolvable via $CODEX_BIN or PATH", "details": {}},
        )

    job_id = os.path.basename(job_dir)
    with jobs.mutex(job_dir):
        with open(os.path.join(job_dir, "target.json"), encoding="utf-8") as handle:
            target = json.load(handle)

        for name in stage.TERMINAL_RECORDS:
            if os.path.exists(os.path.join(job_dir, name)):
                return envelope(
                    verb="run", exit_code=EXIT_CONTRACT, job_id=job_id, job_dir=job_dir,
                    error={"code": "already-terminal", "category": "lifecycle",
                           "message": "the job is already closed",
                           "details": {"record": name}},
                )

        if os.path.exists(os.path.join(job_dir, "response.json")):
            return envelope(
                verb="run", exit_code=EXIT_CONTRACT, job_id=job_id, job_dir=job_dir,
                error={"code": "already-run", "category": "write-once",
                       "message": "this job has already been reviewed",
                       "details": {"artifact": "response.json"}},
            )

        model = (options.get("model") or target.get("model")
                 or reviewer.DEFAULT_MODEL)
        effort = (options.get("effort") or target.get("effort")
                  or reviewer.DEFAULT_EFFORT)
        outcome = reviewer.invoke(codex_path, job_dir, target, model, effort)

        if outcome["failed"]:
            # The reviewer never answered, so no contract was violated and nothing is
            # written: the job stays at `prepared` and the review can be retried (§12).
            return envelope(
                verb="run", exit_code=EXIT_CODEX_UNAVAILABLE, job_id=job_id, job_dir=job_dir,
                error={
                    "code": "codex-failed",
                    "category": "reviewer",
                    "message": "codex produced no response",
                    "details": {
                        "returncode": outcome["returncode"],
                        "stderr": outcome["stderr"][-2000:],
                        "log": os.path.join(job_dir, "logs", "codex.jsonl"),
                        "retry_after": outcome["retry_after"],
                    },
                },
            )

        # Persisted BEFORE validation: a rejection loses the machine-readable path,
        # never the reviewer's text (§8).
        with open(os.path.join(job_dir, "response.json"), "w", encoding="utf-8") as handle:
            handle.write(outcome["raw"])

        metadata = {
            "codex_version": reviewer.codex_version(codex_path),
            "codex_path": codex_path,
            "driver_version": DRIVER_VERSION,
            "driver_hash": driver_hash(),
            "model": model,
            "effort": effort,
            "prompt_sha256": outcome["prompt_sha256"],
            "schema_sha256": outcome["schema_sha256"],
            "synthesized_policy_sha256": target["synthesized_policy_sha256"],
            "effective_policy_hash": reviewer.effective_policy_hash(
                target["synthesized_policy_sha256"]
            ),
            # The frozen artifact, never the live file: the target may be gone by now.
            "target_bytes": sum(
                os.path.getsize(os.path.join(job_dir, "artifacts", item["id"], "after"))
                for item in target.get("artifacts_detail", [])),
            "elapsed_seconds": outcome["elapsed_seconds"],
        }

        try:
            parsed = response.validate(outcome["raw"], target)
        except response.ContractViolation as violation:
            state.write_json(os.path.join(job_dir, "run-error.json"), {
                "code": violation.code,
                "message": violation.message,
                "details": violation.details,
                "run_metadata": metadata,
            })
            return envelope(
                verb="run", exit_code=EXIT_CONTRACT, job_id=job_id, job_dir=job_dir,
                error={"code": violation.code, "category": "contract",
                       "message": violation.message, "details": violation.details},
            )

        report = dict(parsed)
        report["run_metadata"] = metadata
        state.write_json(os.path.join(job_dir, "report.json"), report)

        counts = {
            "introduced": sum(1 for f in parsed["findings"]
                              if f["classification"] == "introduced"),
            "pre_existing": sum(1 for f in parsed["findings"]
                                if f["classification"] == "pre-existing"),
        }
        return envelope(
            verb="run", exit_code=0, job_id=job_id, job_dir=job_dir,
            payload={
                "report_path": os.path.join(job_dir, "report.json"),
                "report_sha256": images.sha256_file(os.path.join(job_dir, "report.json")),
                "verdict": parsed["verdict"],
                "findings": parsed["findings"],
                "counts": counts,
                "run_metadata": metadata,
            },
        )


def cli(argv):
    verb = argv[0] if argv else None
    if verb not in VERBS:
        result = envelope(
            verb=None,
            exit_code=EXIT_USAGE,
            error={
                "code": "unrecognised-verb",
                "category": "usage",
                "message": "no recognised verb",
                "details": {"argument": verb},
            },
        )
    elif verb == "prepare":
        result = prepare(argv[1:])
    elif verb == "run":
        result = run(argv[1:])
    elif verb == "seal":
        result = seal(argv[1:])
    elif verb == "status":
        result = status(argv[1:])
    elif verb == "abandon":
        result = abandon(argv[1:])
    elif verb == "record-probes":
        result = record_probes(argv[1:])
    elif verb == "record-apply":
        result = record_apply(argv[1:])
    elif verb == "record-verification":
        result = record_verification(argv[1:])
    else:
        result = envelope(
            verb=verb, exit_code=EXIT_USAGE,
            error={"code": "verb-not-built", "category": "milestone",
                   "message": "this verb is not built yet", "details": {"verb": verb}},
        )

    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return result["exit"]
