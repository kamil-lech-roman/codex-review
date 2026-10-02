#!/usr/bin/env python3
"""Extract one metrics row per completed review job.

Rows go to $CODEX_REVIEW_STATE/experiments/effort-ab.jsonl, deliberately outside the
repository: the file changes on every run, and anything inside the repository lands in
the next review's delta and is re-read at cost.
"""
import fcntl
import hashlib
import json
import os
import re
import sys

STATE = os.path.expanduser(os.environ.get("CODEX_REVIEW_STATE", "~/.codex-review"))
OUT = os.path.join(STATE, "experiments", "effort-ab.jsonl")


def row(job_dir):
    job_id = os.path.basename(job_dir.rstrip("/"))
    try:
        with open(os.path.join(job_dir, "target.json")) as handle:
            target = json.load(handle)
    except OSError:
        return None

    usage, items = None, 0
    try:
        with open(os.path.join(job_dir, "logs", "codex.jsonl")) as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "item.started":
                    items += 1
                elif event.get("type") == "turn.completed":
                    usage = event["usage"]
    except OSError:
        pass
    if usage is None:
        return None  # never completed a turn — quota, crash; no comparable measurement

    # A job without a report did not find nothing — it produced nothing. Recording the two
    # the same way turns a failed run into evidence of a clean one.
    findings, verdict, outcome, meta = None, None, None, {}
    try:
        with open(os.path.join(job_dir, "report.json")) as handle:
            report = json.load(handle)
        findings = report.get("findings") or []
        verdict = report.get("verdict")
        meta = report.get("run_metadata") or {}
        outcome = "reported"
    except OSError:
        for name, label in (("run-error.json", "run-error"),
                            ("abandoned.json", "abandoned")):
            path = os.path.join(job_dir, name)
            if os.path.exists(path):
                outcome = label
                try:
                    with open(path) as handle:
                        meta = json.load(handle).get("run_metadata") or {}
                except (OSError, ValueError):
                    meta = {}
                break
        else:
            # The job completed a Codex turn but has written no terminal record, so it is
            # still retryable. Recording it now would burn its id in `seen` and lose the
            # successful report the retry produces.
            return None

    introduced = ([f for f in findings if f.get("classification") == "introduced"]
                  if findings is not None else None)
    return {
        "outcome": outcome,
        "job_id": job_id,
        "target_sha": target.get("target_sha"),
        "mode": target.get("mode"),
        # `run` accepts --model/--effort overrides, and run_metadata records what actually
        # ran. Labelling the arm from prepare-time options files an overridden run under the
        # effort it was prepared with, which is the one thing this experiment must not do.
        "model": meta.get("model") or target.get("model") or "gpt-6-astra",
        "effort": meta.get("effort") or target.get("effort") or "xhigh",
        # target_sha covers the artifacts alone. Two runs sharing it still differ if the
        # policy, prompt or driver moved, so a pair is only comparable when these match too.
        "prompt_sha256": meta.get("prompt_sha256"),
        "prompt_sha256_normalised": _normalised_prompt_sha(job_dir),
        "effective_policy_hash": meta.get("effective_policy_hash"),
        "driver_hash": meta.get("driver_hash"),
        "artifacts": len(target.get("artifacts") or []),
        "verdict": verdict,
        "findings": len(findings) if findings is not None else None,
        "introduced": len(introduced) if introduced is not None else None,
        "blockers": (len([f for f in introduced if f.get("severity") == "blocker"])
                     if introduced is not None else None),
        "majors": (len([f for f in introduced if f.get("severity") == "major"])
                   if introduced is not None else None),
        "response_items": items,
        "input_tokens": usage["input_tokens"],
        "cached_input_tokens": usage["cached_input_tokens"],
        "output_tokens": usage["output_tokens"],
        "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
    }


def _normalised_prompt_sha(job_dir):
    """The prompt with this job's own directory replaced by a placeholder.

    `prompt_sha256` embeds the job nonce, so two runs of one target never share it and a
    rule requiring them to match would reject every pair that could ever exist. What must
    match is the prompt apart from where the job happens to live.
    """
    try:
        with open(os.path.join(job_dir, "prompt.txt"), "rb") as handle:
            prompt = handle.read()
    except OSError:
        return None
    prompt = prompt.replace(os.path.realpath(job_dir).encode("utf-8"), b"<job>")
    prompt = prompt.replace(job_dir.rstrip("/").encode("utf-8"), b"<job>")
    return hashlib.sha256(prompt).hexdigest()


def _finished_at(job_dir):
    """When the job last produced a record, so rounds sort by completion."""
    for name in ("report.json", "run-error.json", "abandoned.json", "target.json"):
        path = os.path.join(job_dir, name)
        if os.path.exists(path):
            return os.path.getmtime(path)
    return 0.0


def main():
    argv = sys.argv[1:]
    conversation = None
    if "--conversation" in argv:
        index = argv.index("--conversation")
        conversation = argv[index + 1]
        argv = argv[:index] + argv[index + 2:]
    if conversation and not argv:
        sys.exit("--conversation needs the job ids from that conversation: the state directory is "
                 "shared, and labelling every unrecorded job in it would file other "
                 "conversations' runs under this one.")
    if argv:
        jobs = argv
    else:
        # Job ids are random nonces, so listing order is arbitrary. Round indices have to
        # follow completion order or the learning-curve column describes nothing.
        candidates = [
            os.path.join(STATE, name) for name in os.listdir(STATE)
            if os.path.isdir(os.path.join(STATE, name)) and not name.startswith(".")
            and re.fullmatch(r"[0-9a-f]{16}", name)  # a job id, as the driver defines one
        ]
        jobs = sorted(candidates, key=_finished_at)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # Read-then-append is a race: two recorders can both see a job unrecorded and write it.
    lock = open(OUT + ".lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    seen, in_conversation = set(), 0
    if os.path.exists(OUT):
        with open(OUT) as handle:
            for line in handle:
                try:
                    previous = json.loads(line)
                except ValueError:
                    continue
                seen.add(previous.get("job_id"))
                if conversation and previous.get("conversation") == conversation:
                    in_conversation += 1
    added = 0
    with open(OUT, "a") as handle:
        for job_dir in jobs:
            job_id = os.path.basename(job_dir.rstrip("/"))
            if job_id in seen:
                continue
            record = row(job_dir)
            if record is None:
                continue
            # Analysis has to separate the effort from the reviewer's own learning curve:
            # later rounds of a conversation are fixed by someone who has read this code
            # several times already, which moves findings on its own.
            record["conversation"] = conversation
            in_conversation += 1
            record["round_in_conversation"] = in_conversation if conversation else None
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            seen.add(job_id)
            added += 1
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    print("{} row(s) added -> {}".format(added, OUT))


if __name__ == "__main__":
    main()
