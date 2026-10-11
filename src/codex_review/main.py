"""codex-review driver. Stdlib only, Python >= 3.9 (§2)."""

import datetime
import json
import os
import sys
import time

from codex_review import apply as apply_module
from codex_review import codex, context, evidence, execution, ownership, git, images, jobs, policy, probes, projections, qualification, response, reviewer, stage, state

SCHEMA_VERSION = 1
DRIVER_VERSION = "0.1.0"

VERBS = (
    "prepare",
    "continue",
    "recapture",
    "amend-paths",
    "rebut",
    "run",
    "seal",
    "record-qualification",
    "record-probes",
    "record-apply",
    "record-verification",
    "status",
    "abandon",
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
    for name in ("max_artifact", "max_bundle"):
        try:
            limits[name] = int(options[name]) if name in options else None
            if limits[name] is not None and limits[name] <= 0:
                raise ValueError("nonpositive limit")
        except (TypeError, ValueError):
            return None, usage_error("prepare", "bad-limit", "capture limit must be a positive integer",
                                     option="--" + name.replace("_", "-"), value=options.get(name))
    return limits, None


def prepare(argv, loop_id=None, verification_contract=None, publisher=None, retained_paths=()):
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
    repository_context = resolve_context(options, os.getcwd())
    repositories = {repository_context}
    if mode == "plan" and rest:
        repositories.add(git.repo_root(os.path.dirname(os.path.realpath(rest[0]))))
    root = os.path.realpath(state.state_root())
    for repository in repositories - {None}:
        if os.path.commonpath((root, os.path.realpath(repository))) == os.path.realpath(repository):
            return envelope("prepare", 9, error={
                "code": "state-inside-repository", "category": "preflight",
                "message": "state must live outside the target repository",
                "details": {"state_root": root, "repository": repository}})
    try:
        captured, blobs = context.companions(options.get("companions_file"), repository_context)
        manifest, context_blobs = context.repository_snapshot(repository_context)
        blobs.update(context_blobs)
        dependencies, dependency_blobs = context.dependencies(options.get("dependencies_file"), repository_context, root,
                                                             'persistent' if loop_id else 'one-pass')
        blobs.update(dependency_blobs)
    except projections.CaptureRefused as refusal:
        return envelope("prepare", refusal.exit_code, error={
            "code": refusal.code, "category": "capture", "message": refusal.message,
            "details": refusal.details})
    options["_publisher"] = publisher
    options["_loop_id"] = loop_id
    options["_retained_paths"] = retained_paths
    options["_verification_contract"] = verification_contract
    options["_dependencies"] = dependencies
    options["_context_manifest"] = manifest
    options["_context_repo_identity"] = projections.repo_identity(repository_context) if repository_context else None
    options["_companions"] = captured
    options["_input_blobs"] = blobs
    try:
        if mode == "plan":
            return prepare_plan(rest, codex_path, options)
        if mode == "code":
            return prepare_code(rest, codex_path, options)
    except projections.CaptureRefused as refusal:
        return envelope('prepare', refusal.exit_code, error=dict(
            code=refusal.code, category='capture', message=refusal.message, details=refusal.details))
    return usage_error("prepare", "unrecognised-mode", "unrecognised target mode", mode=mode)


# Syntax and applicability are separate: one tokenizer, one operation allowlist.
OPTIONS = {
    "--repo": True, "--no-repo": False, "--model": True, "--effort": True,
    "--uncommitted": False, "--no-apply": False, "--no-verify": False, "--verify": True,
    "--defer-verify": False, "--loop": False,
    "--base": True, "--head": True, "--max-artifact": True, "--max-bundle": True,
    "--note": True, "--bytes": True, "--reason": True, "--denied-interactively": False,
    "--probes-file": True, "--apply-file": True, "--verification-file": True,
    "--companions-file": True, "--dependencies-file": True, "--qualification-file": True,
    "--round": True, "--expected-generation": True, "--decision-file": True,
    "--amendment-file": True, "--rebuttal-file": True, "--expect-paths": True,
    "--judgment-budget": True, "--retry-budget": True, "--launch-budget": True,
    "--xhigh-clean": True, "--max-clean": True,
}


def parse_common(argv):
    """Normalize options once; reject unknown, repeated or incomplete spellings."""
    positional, options = [], {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token.startswith("--"):
            name = token[2:].replace("-", "_")
            if token not in OPTIONS or name in options:
                return None, None, token
            if OPTIONS[token]:
                if index + 1 >= len(argv):
                    return None, None, token
                options[name] = argv[index + 1]
                index += 2
            else:
                options[name] = True
                index += 1
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
        entries = (projections.combined_changes(repo_root, base, options.get("_retained_paths", ())) if options.get("_loop_id") else
                   projections.uncommitted_changes(repo_root) if uncommitted else
                   projections.committed_changes(repo_root, base))
        if not entries:
            # A review of nothing cannot find anything, and reports `approve` — which reads
            # as convergence rather than as the vacuous answer it is.
            return target_refused(
                "empty-target", "the target contains no artifacts",
                repo_root=repo_root,
                **({"uncommitted": True} if uncommitted else {"base": base}))
        assertion, assertion_bytes = context.expected_paths(options.get("expect_paths"), [entry["path"] for entry in entries])
        if assertion is not None:
            options["_input_blobs"]["inputs/" + assertion["sha256"]] = assertion_bytes
        sides = []
        for entry in entries:
            after = projections.read_side(repo_root, entry["_after_source"])
            before = after if entry.get('projection') == 'retained' else projections.read_side(repo_root, entry["_before_source"])
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
        "capture_mode": "combined" if options.get("_loop_id") else "uncommitted" if uncommitted else "committed",
        "expected_paths": assertion,
        "artifacts": [item["id"] for item in detail],
        "artifacts_detail": detail,
        "loop_id": options.get("_loop_id"),
        "dependencies": options.get("_dependencies", []),
        "cache_policy": context.CACHE_POLICY,
        "capture_contract": 2,
        "context_manifest": options.get("_context_manifest", {}),
        "context_repo_identity": options.get("_context_repo_identity"),
        "companions": options.get("_companions", []),
        "verification_intent": "deferred" if options.get("defer_verify") else "declined" if options.get("no_verify") else "configured",
        "verification_reason": options.get("reason"),
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

    from codex_review import verification
    target["verification_contract"] = options.get("_verification_contract") or verification.contract(target)
    target["verify_command"] = target["verification_contract"]["command"]
    target["driver_hash"] = driver_hash()
    target["global_policy_sources"] = reviewer.global_policy_sources()
    target["policy_equivalence_sha256"] = images.sha256_bytes(agents_bytes.replace(job_dir.encode("utf-8"), b"<round>"))
    target['retained_files'] = context.retained_manifest(agents_bytes, zip(detail, sides), options.get('_input_blobs', {}))
    target["audit_sha256"] = qualification.digest({k: v for k, v in target.items() if k != "codex_path"})
    os.makedirs(os.path.join(root, state.STAGING), exist_ok=True)
    if not context.applicable(target):
        return envelope("prepare", 4, error={"code": "capture-race", "category": "capture",
                        "message": "companion moved during capture", "details": {}})
    build = state.staging_dir(root, nonce)
    context.write_blobs(build, options.get("_input_blobs", {}))
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
    if options.get('_publisher'):
        options['_publisher'](root, nonce, build, target)
    else:
        state.publish(root, nonce, build)

    return envelope(
        verb="prepare", exit_code=0, job_id=nonce, job_dir=job_dir,
        payload={"target": {
            "mode": "code",
            "target_sha": target["target_sha"],
            "artifact_count": len(detail),
            "bytes": sum(len(b) + len(a) for b, a in sides),
            "resolved_path": repo_root,
            "paths": sorted({item["path"] for item in detail}),
            "base_oid": target["base_ref"], "head_oid": identity["head_oid"],
            "before_bytes": sum(len(b) for b, _ in sides),
            "after_bytes": sum(len(a) for _, a in sides),
            "audit_sha256": target["audit_sha256"],
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
    try:
        projections.enforce_limits([(path, len(plan_bytes))], max_artifact, max_bundle)
    except projections.CaptureRefused as refusal:
        return envelope("prepare", refusal.exit_code, error={
            "code": refusal.code, "category": "capture", "message": refusal.message,
            "details": refusal.details})
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
        "loop_id": options.get("_loop_id"),
        "dependencies": options.get("_dependencies", []),
        "cache_policy": context.CACHE_POLICY,
        "capture_contract": 2,
        "context_manifest": options.get("_context_manifest", {}),
        "context_repo_identity": options.get("_context_repo_identity"),
        "companions": options.get("_companions", []),
        "verification_intent": "deferred" if options.get("defer_verify") else "declined" if options.get("no_verify") else "configured",
        "verification_reason": options.get("reason"),
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
        "apply_scope": git.repo_root(os.path.dirname(path)) or os.path.dirname(path),
        "context_repository": context_repository,
        "policy_commit": policy_commit,
        "policy_sources": list(sources),
        "policy_noted": list(noted),
        "synthesized_policy_sha256": images.sha256_bytes(agents_bytes),
    }

    from codex_review import verification
    target["verification_contract"] = options.get("_verification_contract") or verification.contract(target)
    target["verify_command"] = target["verification_contract"]["command"]
    target["driver_hash"] = driver_hash()
    target["global_policy_sources"] = reviewer.global_policy_sources()
    target["policy_equivalence_sha256"] = images.sha256_bytes(agents_bytes.replace(job_dir.encode("utf-8"), b"<round>"))
    target['retained_files'] = context.retained_manifest(agents_bytes, [(artifact, (b'', plan_bytes))], options.get('_input_blobs', {}))
    target["audit_sha256"] = qualification.digest({k: v for k, v in target.items() if k != "codex_path"})
    os.makedirs(os.path.join(root, state.STAGING), exist_ok=True)
    if not context.applicable(target):
        return envelope("prepare", 4, error={"code": "capture-race", "category": "capture",
                        "message": "companion moved during capture", "details": {}})
    build = state.staging_dir(root, nonce)
    context.write_blobs(build, options.get("_input_blobs", {}))
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
    if options.get('_publisher'):
        options['_publisher'](root, nonce, build, target)
    else:
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
                "audit_sha256": target["audit_sha256"],
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


def abandon(argv, probes_path=None):
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

        state.atomic_json(os.path.join(job_dir, "abandoned.json"), record)
        release_apply(job_dir, target, probes_path)

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




def refuse_stale(verb, job_dir, target):
    if target.get("capture_contract") == 2 and not context.applicable(target):
        state.atomic_json(os.path.join(job_dir, "invalidated.json"), {"at_stage": verb})
        return envelope(verb, 4, job_id=os.path.basename(job_dir), job_dir=job_dir,
                        error={"code": "reference-drift", "category": "drift",
                               "message": "captured inputs changed", "details": {"at_stage": verb}})
    return None


def qualification_handback(verb, job_dir, selected):
    return envelope(verb, 18, job_id=os.path.basename(job_dir), job_dir=job_dir,
                    error=dict(code='needs-user-decision', category='qualification',
                               message='author execution requires matching qualification',
                               details=dict(reason='qualification-required', next_verb=None, qualification=selected)))


def apply_ownership(verb, job_dir, target, action):
    try:
        getattr(ownership, action)(state.state_root(), ownership.scope(target), os.path.basename(job_dir))
    except ownership.Conflict as error:
        return envelope(verb, 16, job_id=os.path.basename(job_dir), job_dir=job_dir,
                        error={"code": "apply-not-owned", "category": "ownership",
                               "message": str(error), "details": {"checkout": ownership.scope(target)}})
    return None


def release_apply(job_dir, target, probes_path=None):
    path = probes_path or os.path.join(job_dir, "probes.json")
    if target and os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
        if record.get("owns_apply"):
            ownership.release(state.state_root(), ownership.scope(target), os.path.basename(job_dir))



def record_qualification(argv):
    resolved, refusal = _open_recording("record-qualification", argv, "qualification_file", ("target.json",))
    if refusal is not None:
        return refusal
    job_dir, job_id, options = resolved
    raw = None
    with jobs.mutex(job_dir):
        closed = refuse_if_terminal("record-qualification", job_dir, job_id)
        if closed is not None:
            return closed
        try:
            path = evidence.payload_path(job_dir, options["qualification_file"], "qualification_file")
            with open(path, encoding="utf-8") as handle:
                raw = handle.read()
            sha = qualification.record(state.state_root(), job_dir, json.loads(raw))
        except response.ContractViolation as error:
            return _contract_failure("record-qualification", job_dir, job_id, raw, error)
        except (OSError, ValueError, TypeError, KeyError) as error:
            return _contract_failure("record-qualification", job_dir, job_id, raw,
                                     response.ContractViolation("bad-qualification", str(error),
                                                                field="qualification_file", expected="schema-v1 record"))
    return envelope("record-qualification", 0, job_id=job_id, job_dir=job_dir,
                    payload={"qualification_sha256": sha})


def record_probes(argv, selected_records=None):
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
    selected_records = selected_records or dict(report=os.path.join(job_dir, 'report.json'),
                                                probes=os.path.join(job_dir, 'probes.json'), prior_probes=None)

    with jobs.mutex(job_dir):
        for name in stage.TERMINAL_RECORDS:
            if os.path.exists(os.path.join(job_dir, name)):
                return envelope(
                    verb="record-probes", exit_code=EXIT_CONTRACT, job_id=job_id,
                    job_dir=job_dir,
                    error={"code": "already-terminal", "category": "lifecycle",
                           "message": "the job is already closed",
                           "details": {"record": name}})

        report_path = selected_records['report']
        if not os.path.exists(report_path):
            return envelope(
                verb="record-probes", exit_code=EXIT_CONTRACT, job_id=job_id, job_dir=job_dir,
                error={"code": "not-reviewed", "category": "lifecycle",
                       "message": "this job has no report to annotate", "details": {}})

        raw = None
        try:
            with open(evidence.payload_path(job_dir, options["probes_file"], "probes_file"),
                      encoding="utf-8") as handle:
                raw = handle.read()
            payload = json.loads(raw)
            with open(report_path, encoding="utf-8") as handle:
                report = json.load(handle)
            target = load_target(job_dir)
            if os.path.exists(selected_records['probes']):
                raise probes.ContractViolation(
                    "already-recorded", "this job already has probes.json")
            annotations, completion = probes.validate(
                job_dir, job_id, target, report, payload,
                images.sha256_file(report_path))
            prior = None
            if selected_records.get('prior_probes'):
                prior_path = selected_records['prior_probes']
                response._require(payload.get('previous_probes_sha256') == images.sha256_file(prior_path),
                                  'stale-prior-probes', 'probe revision must cite its predecessor')
                with open(prior_path, encoding='utf-8') as handle:
                    prior = json.load(handle)
            drift = refuse_stale('record-probes', job_dir, target)
            if drift is not None:
                return drift
            if target.get('capture_contract', 1) >= 2:
                for annotation in annotations:
                    if annotation['probe_execution'] == 'executed':
                        annotation['execution'] = execution.from_command(job_dir, target, annotation['command_evidence_id'])
        except execution.Unqualified as refusal:
            return qualification_handback('record-probes', job_dir, refusal.selected)
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

        drift = refuse_stale("record-probes", job_dir, target)
        if drift is not None:
            return drift
        owns_apply = bool(prior and prior.get('owns_apply')) or (not target.get("no_apply") and any(a.get("disposition") == "accepted" for a in annotations))
        if owns_apply:
            conflict = apply_ownership("record-probes", job_dir, target, "claim")
            if conflict is not None:
                return conflict
        os.makedirs(os.path.dirname(selected_records['probes']), exist_ok=True)
        state.atomic_json(selected_records['probes'], {
            "owns_apply": owns_apply,
            "report_sha256": payload["report_sha256"],
            **({'previous_probes_sha256': payload['previous_probes_sha256']} if prior else {}),
            "pre_fix_sha": prior['pre_fix_sha'] if prior else apply_module.identity_sha(target),
            "pre_apply_baseline": prior['pre_apply_baseline'] if prior else apply_module.baseline(target),
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
                 "probes_sha256": images.sha256_file(selected_records['probes'])})


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


def record_apply(argv, path_amendments=None, selected_records=None):
    """Recompute what the bytes say and reconcile it against what was attempted (§11)."""
    resolved, refusal = _open_recording("record-apply", argv, "apply_file",
                                        (selected_records['probes'] if selected_records else "probes.json",))
    if refusal is not None:
        return refusal
    job_dir, job_id, options = resolved
    selected_records = selected_records or dict(report=os.path.join(job_dir, 'report.json'), probes=os.path.join(job_dir, 'probes.json'))

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
            with open(evidence.payload_path(job_dir, options["apply_file"], "apply_file"),
                      encoding="utf-8") as handle:
                raw = handle.read()
            payload = json.loads(raw)
            apply_module.require(isinstance(payload, dict), "bad-payload",
                                 "apply payload is not an object")
            with open(selected_records['probes'], encoding="utf-8") as handle:
                probes_record = json.load(handle)
            if path_amendments:
                from codex_review import amendments
                response._require(payload.get('amendments_sha256') == qualification.digest(path_amendments),
                                  'stale-amendments-digest', 'apply must bind the current path amendments')
                probes_record = amendments.effective(probes_record, path_amendments)
            else:
                response._require('amendments_sha256' not in payload, 'unexpected-amendments',
                                  'this round has no path amendments')
            apply_module.require(
                payload.get("probes_sha256") == images.sha256_file(selected_records['probes']),
                "stale-probes-digest", "submission does not carry this job's probes digest")
            apply_module.require(
                payload.get("report_sha256") == images.sha256_file(selected_records['report']),
                "stale-report-digest", "submission does not carry this job's report digest")
            if probes_record.get("owns_apply"):
                conflict = apply_ownership("record-apply", job_dir, target, "require_owner")
                if conflict is not None:
                    return conflict
            allowed = apply_module.declared_paths(target, probes_record)
            if target.get('capture_contract', 1) >= 2 and not context.apply_applicable(target, allowed):
                state.atomic_json(os.path.join(job_dir, 'invalidated.json'),
                                 dict(at_stage='record-apply', moved=['references-or-external-context']))
                return envelope('record-apply', 4, job_id=job_id, job_dir=job_dir,
                                error=dict(code='reference-drift', category='drift',
                                           message='references or undeclared external context moved during apply', details={}))
            moved = apply_module.changed_paths(
                target, probes_record.get("pre_apply_baseline"))
            results, application_result, violating = apply_module.reconcile(
                target, probes_record, payload, moved)
            if target.get('loop_id'):
                for item in results:
                    apply_module.check_sweep(job_dir, job_id, item.get('sibling_sweep'))
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
        post_context = None
        if application_result != 'scope-violation' and target.get('capture_contract', 1) >= 2:
            try:
                post_context = context.retain_applied_context(job_dir, target)
                if (not context.apply_applicable(target, allowed) or
                        apply_module.identity_sha(target) != post_fix_sha or
                        apply_module.changed_paths(target, probes_record.get('pre_apply_baseline')) != moved):
                    raise projections.CaptureRefused('capture-race', 'candidate moved during apply capture', 4)
            except projections.CaptureRefused as refusal:
                return envelope('record-apply', refusal.exit_code, job_id=job_id, job_dir=job_dir,
                                error=dict(code=refusal.code, category='capture', message=refusal.message, details=refusal.details))
        state.atomic_json(os.path.join(job_dir, "apply-input.json"), payload)

        if application_result == "scope-violation":
            # Detected, never confined: the change stays in the tree (A2).
            state.atomic_json(os.path.join(job_dir, "apply-error.json"), {
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

        state.atomic_json(os.path.join(job_dir, "apply.json"), {
            "report_sha256": payload['report_sha256'],
            "probes_sha256": payload['probes_sha256'],
            **({'amendments_sha256': qualification.digest(path_amendments)} if path_amendments else {}),
            "application_result": application_result,
            "probe_completion": probes_record["probe_completion"],
            "results": results,
            "changed_paths": moved,
            "pre_fix_sha": pre_fix_sha,
            "post_fix_sha": post_fix_sha,
            **({'post_apply_context': post_context} if post_context is not None else {}),
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


def record_verification(argv, verification_contract=None, probes_path=None):
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
        if verification_contract is not None:
            from codex_review import verification
            target = verification.effective_target(target, verification_contract)
        try:
            with open(evidence.payload_path(job_dir, options["verification_file"], "verification_file"),
                      encoding="utf-8") as handle:
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
            block = payload["verification"]
            resolved_result = apply_module.verification_result(
                block.get("result"), target, apply_record["changed_paths"])
            if apply_module.identity_sha(target) != apply_record["post_fix_sha"]:
                state.atomic_json(os.path.join(job_dir, "invalidated.json"), {
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
            apply_module.check_verification_evidence(resolved_result, block)
            if resolved_result in ("passed", "failed", "denied"):
                apply_module.check_verification_command(job_dir, job_id, target, block)
            if resolved_result in ("passed", "failed"):
                derived = apply_module.derive_verification(job_dir, job_id, block)
                apply_module.require(
                    derived == resolved_result, "verification-result-contradicted",
                    "the sealed return code contradicts the submitted result",
                    submitted=resolved_result, derived=derived)
                if target.get('capture_contract', 1) >= 2:
                    block['execution'] = execution.from_command(job_dir, target, block['command_evidence_id'])
        except execution.Unqualified as refusal:
            return qualification_handback('record-verification', job_dir, refusal.selected)
        except (OSError, ValueError) as error:
            return _contract_failure("record-verification", job_dir, job_id, raw,
                                     apply_module.ContractViolation(
                                         "unreadable-submission",
                                         "verification submission could not be read",
                                         detail=str(error)))
        except apply_module.ContractViolation as violation:
            return _contract_failure("record-verification", job_dir, job_id, raw, violation)

        record = {
            "apply_sha256": payload['apply_sha256'],
            "application_result": apply_record["application_result"],
            "verification_contract": target.get('verification_contract'),
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
        state.atomic_json(os.path.join(job_dir, "verification.json"), record)
        release_apply(job_dir, target, probes_path)

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
            if os.path.exists(os.path.join(path, "loop.json")):
                from codex_review import loop_driver
                try:
                    described = loop_driver.status_payload(path)
                except (response.ContractViolation, OSError, ValueError, KeyError, TypeError) as error:
                    described = dict(stage='inconsistent', resumable=False, next_verb=None,
                                     error=dict(code=getattr(error, 'code', 'invalid-loop-state'), message=str(error)))
                if described["stage"] != "closed":
                    open_jobs.append(dict(described, job_id=name))
                continue
            target = load_target(path)
            if target and target.get("loop_id"):
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


def seal(argv, verification_contract=None, execution_blocked=None):
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
            target = load_target(job_dir)
            if target and verification_contract is not None:
                from codex_review import verification
                target = verification.effective_target(target, verification_contract)
            if target and target.get('capture_contract', 1) >= 2 and evidence.COMMAND_SCRIPT.fullmatch(payload['source_path']):
                try:
                    prepared = (dict(ready=False, reason=execution_blocked) if execution_blocked else
                                execution.prepare(job_dir, target, payload['evidence_id']))
                except response.ContractViolation as refusal:
                    prepared = dict(ready=False, reason=refusal.code, details=refusal.details)
                payload['execution'] = prepared
                if prepared['ready']:
                    state.write_json(os.path.join(job_dir, 'evidence', payload['evidence_id'], 'execution.json'),
                                     dict(execution_id=prepared['execution_id']))
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

    with jobs.mutex(job_dir):
        return run_review(job_dir, codex_path, options)


def run_review(job_dir, codex_path, options):
    """Caller supplies single-writer coordination; loop callers reserve under a short lock."""
    job_id = os.path.basename(job_dir)
    with open(os.path.join(job_dir, "target.json"), encoding="utf-8") as handle:
        target = json.load(handle)
    try:
        context.verify_retained(job_dir, target)
    except projections.CaptureRefused as refusal:
        return envelope('run', refusal.exit_code, job_id=job_id, job_dir=job_dir,
                        error=dict(code=refusal.code, category='capture', message=refusal.message, details=refusal.details))
    for selected in context.exclusion_qualifications(target, state.state_root()):
        if not selected['valid']:
            return qualification_handback('run', job_dir, selected)

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

    drift = refuse_stale("run", job_dir, target)
    if drift is not None:
        return drift
    model = (options.get("model") or target.get("model")
             or reviewer.DEFAULT_MODEL)
    effort = (options.get("effort") or target.get("effort")
              or reviewer.DEFAULT_EFFORT)
    outcome = reviewer.invoke(codex_path, job_dir, target, model, effort, options.get("_attempt_id"))

    if target.get('loop_id'):
        with jobs.mutex(job_dir):
            return publish_review(job_dir, codex_path, target, model, effort, outcome)
    return publish_review(job_dir, codex_path, target, model, effort, outcome)


def publish_review(job_dir, codex_path, target, model, effort, outcome):
    """Publish one durable transport result, including recovery without another call."""
    job_id = os.path.basename(job_dir)
    existing_report = os.path.join(job_dir, 'report.json')
    if os.path.isfile(existing_report):
        with open(existing_report, encoding='utf-8') as handle:
            existing = json.load(handle)
        if existing.get('run_metadata', {}).get('attempt_dir') != outcome['attempt_dir']:
            return usage_error('run', 'attempt-mismatch', 'a different attempt already published this round')
        return envelope('run', 0, job_id=job_id, job_dir=job_dir,
                        payload=dict(report_path=existing_report, report_sha256=images.sha256_file(existing_report)))
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
                    "attempt_dir": outcome["attempt_dir"],
                    "launched": outcome["launched"],
                    "timing": outcome["timing"],
                },
            },
        )

    # Persisted BEFORE validation: a rejection loses the machine-readable path,
    # never the reviewer's text (§8).
    state.atomic_bytes(os.path.join(job_dir, "response.json"), outcome["raw"])

    metadata = {
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
        "timing": outcome["timing"],
        "thread_id": outcome["thread_id"],
        "attempt_dir": outcome["attempt_dir"],
        "returncode": outcome["returncode"],
        "completion_evidence": outcome.get("completion_evidence", "receipt"),
    }

    try:
        parsed = response.validate(outcome["raw"], target)
    except response.ContractViolation as violation:
        state.atomic_json(os.path.join(job_dir, "run-error.json"), {
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
    state.atomic_json(os.path.join(job_dir, "report.json"), report)

    drift = refuse_stale("run", job_dir, target)
    if drift is not None:
        return drift
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


# Options are recognized once, then checked against the selected operation.
_PREPARE_OPTIONS = {"repo", "no_repo", "verify", "no_verify", "defer_verify", "reason", "no_apply",
                    "model", "effort", "note", "max_artifact", "max_bundle", "companions_file", "dependencies_file", "loop"}
_VERB_OPTIONS = {
    "rebut": {"rebuttal_file"},
    "amend-paths": {"amendment_file"},
    "continue": {"decision_file"},
    "recapture": {"base", "head", "companions_file", "dependencies_file", "expect_paths"},
    "run": {"model", "effort"}, "seal": {"bytes"}, "status": set(),
    "abandon": {"reason", "denied_interactively"},
    "record-qualification": {"qualification_file"},
    "record-probes": {"probes_file"}, "record-apply": {"apply_file"},
    "record-verification": {"verification_file"},
}


def accepted_options(verb, rest, options):
    if verb == "prepare":
        from codex_review import counting
        mode = rest[0] if rest else None
        allowed = _PREPARE_OPTIONS | ({"base", "head", "uncommitted", "expect_paths"} if mode == "code" else set())
        if options.get('loop'):
            allowed |= set(counting.OPTIONS)
        return allowed
    allowed = _VERB_OPTIONS[verb]
    if verb != 'status':
        allowed = allowed | {'expected_generation'}
    if verb in ('run', 'seal', 'record-qualification', 'record-probes', 'record-apply', 'record-verification', 'amend-paths', 'rebut'):
        allowed = allowed | {'round'}
    return allowed


def parse_options(verb, argv):
    rest, options, bad = parse_common(argv)
    if bad is None:
        allowed = accepted_options(verb, rest, options)
        bad = next(("--" + name.replace("_", "-") for name in options if name not in allowed), None)
    return rest, options, bad


def validate_arguments(verb, argv):
    rest, options, bad = parse_options(verb, argv)
    if bad is not None:
        accepted = accepted_options(verb, rest or [], options or {})
        return usage_error(verb, "bad-option", "unrecognised, duplicate, incomplete or inapplicable option",
                           option=bad, accepted=sorted("--" + name.replace("_", "-") for name in accepted))
    mode = rest[0] if rest else None
    maximum = (2 if mode == "plan" else 1) if verb == "prepare" else (2 if verb == "seal" else 1)
    if rest and (verb.startswith('record-') or verb in ('rebut', 'amend-paths')):
        field = next(name for name in _VERB_OPTIONS[verb] if name.endswith('_file'))
        if not options.get(field):
            return usage_error(verb, 'no-' + field.replace('_', '-'), 'the operation requires its staged payload file',
                               field=field, expected='path under job/round staging')
    if verb == "prepare":
        from codex_review import counting
        for name in set(options) & set(counting.OPTIONS):
            parsed = response.decimal_integer(options[name])
            if parsed is None or parsed <= 0:
                return usage_error(verb, 'bad-policy-limit', 'policy ceilings must be positive integers',
                                   field=name, expected='positive integer')
        for first, second in (("base", "uncommitted"), ("repo", "no_repo"), ("verify", "no_verify")):
            if first in options and second in options:
                return usage_error(verb, "conflicting-options", "options are mutually exclusive",
                                   options=["--" + x.replace("_", "-") for x in (first, second)])
        if mode == "code" and options.get("no_repo"):
            return usage_error(verb, "option-not-applicable", "code capture requires a repository",
                               option="--no-repo")
        if "verify" in options and not options["verify"].strip():
            return usage_error(verb, "blank-verify", "--verify needs a nonempty command", option="--verify")
        configured = "verify" in options
        declined = bool(options.get("no_verify"))
        deferred = bool(options.get("defer_verify"))
        if deferred and not options.get("loop"):
            return usage_error(verb, "option-not-applicable", "deferred verification requires a loop")
        if (sum((configured, declined, deferred)) != 1 or
                (configured and not options["verify"].strip()) or
                ((declined or deferred) and not options.get("reason", "").strip()) or
                (configured and "reason" in options)):
            return usage_error(verb, "verification-intent",
                               "choose --verify CMD, --no-verify --reason R, or loop --defer-verify --reason R",
                               field="verification_intent", expected="one explicit nonempty intent")
    if len(rest) > maximum:
        return usage_error(verb, "extra-arguments", "unexpected positional arguments",
                           arguments=rest[maximum:])
    return None


def cli(argv):
    from codex_review import loop_driver
    verb = argv[0] if argv else None
    if verb in ('accept-delta', 'break-lease'):
        result = usage_error(verb, 'retired-verb',
                             'use exclusive apply ownership and authorized recapture',
                             decision='D02')
    elif verb not in VERBS:
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
    elif (refusal := validate_arguments(verb, argv[1:])) is not None:
        result = loop_driver.response_for_request(verb, argv[1:], refusal)
    elif (loop_result := loop_driver.route(verb, argv[1:])) is not None:
        result = loop_result
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
    elif verb == "record-qualification":
        result = record_qualification(argv[1:])
    elif verb == "record-probes":
        result = record_probes(argv[1:])
    elif verb == "record-apply":
        result = record_apply(argv[1:])
    elif verb == "record-verification":
        result = record_verification(argv[1:])
    else:
        result = usage_error(verb, 'loop-required', 'this operation requires a loop handle')

    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return result["exit"]
