#!/usr/bin/env python3
"""1b PoC: does carrying one Codex thread across rounds cost less than a cold round?

Phase 1b behaviour, measured from outside the driver. The driver passes --ephemeral and
never resumes (§3), so this drives `codex exec` directly rather than changing it.

    spike.py <job_dir_round1> <job_dir_round2> [...]

Round 1 runs cold but persists its thread; each later round resumes that thread and is told,
explicitly, that the target has moved — a resumed thread still holds the previous job's
frozen artifact paths, and those files are still on disk to be read by mistake.

Writes nothing into the job directories: the driver owns those, and a spike must not leave
state a real review would later read.
"""
import json
import os
import subprocess
import sys
import time

CODEX = os.path.expanduser(os.environ.get(
    "CODEX_BIN", "~/.codex/plugins/.plugin-appserver/codex"))
OUT = os.path.expanduser(os.environ.get(
    "SPIKE_OUT", "~/.codex-review/experiments/1b-poc"))

# `resume` accepts neither -s nor -C: the sandbox and working directory stay those of the
# session being resumed. So the cwd still points at the PREVIOUS round's job, every relative
# path resolves there, and those files are all still on disk. Absolute paths are the only
# way to name this round's target, and the preamble has to say so outright.
MOVED = """The target has changed since the previous round in this session.

**Your working directory still points at the previous round's job, not this one.** Every
relative path — `target.json`, `artifacts/...` — resolves to a target that is no longer under
review. Use absolute paths only, under `{job_dir}/`.

Review ONLY the target described in `{job_dir}/target.json`, reading artifacts only from
`{job_dir}/artifacts/`. Artifact ids are assigned per target: `A0001` here is a different file
from `A0001` in any earlier round. Do not carry a finding forward without re-deriving it
against this target.

"""


def usage(job_dir):
    with open(os.path.join(job_dir, "prompt.txt"), encoding="utf-8") as handle:
        return handle.read()


def metrics(stdout):
    usage_row, items, thread = None, 0, None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "item.started":
            items += 1
        elif kind == "turn.completed":
            usage_row = event["usage"]
        elif kind == "thread.started":
            thread = event.get("thread_id") or event.get("thread", {}).get("id")
    return usage_row, items, thread


def main():
    jobs = sys.argv[1:]
    if len(jobs) < 2:
        sys.exit(__doc__)
    # Summary rows append; their evidence must not be overwritten by a later run of the same
    # jobs, or a recorded finding count loses the answer it was counted from. One directory
    # per invocation, named in every row it produced.
    invocation = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-{}".format(os.getpid())
    run_dir = os.path.join(OUT, invocation)
    os.makedirs(run_dir, exist_ok=True)
    thread, rows = None, []

    for index, job_dir in enumerate(jobs, start=1):
        job_dir = os.path.abspath(job_dir.rstrip("/"))
        job_id = os.path.basename(job_dir)
        schema = os.path.join(job_dir, "staging", "response.schema.json")
        # Named by round as well as job: one invocation may review the same job twice —
        # a cold round and a resumed one is the comparison this spike exists to make.
        evidence = "round{:02d}-{}".format(index, job_id)
        answer = os.path.join(run_dir, evidence + ".last-message.txt")
        prompt = usage(job_dir)
        shared = ["--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
                  "-m", "gpt-reserve", "-c", "model_reasoning_effort=xhigh",
                  "--output-schema", schema, "-o", answer, "--json"]

        if thread is None:
            argv = ([CODEX, "exec", "-s", "read-only", "-C", job_dir] + shared + [prompt])
        else:
            # No -s and no -C here: resume rejects both and inherits the original session's.
            argv = ([CODEX, "exec", "resume", thread] + shared
                    + [MOVED.format(job_dir=job_dir) + prompt])

        started = time.time()
        # codex reads stdin for additional input; an inherited one that never closes hangs
        # the round, as it did in the driver.
        done = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        elapsed = round(time.time() - started, 1)

        with open(os.path.join(run_dir, evidence + ".jsonl"), "w") as handle:
            handle.write(done.stdout)
        if done.returncode != 0 and not done.stdout.strip():
            with open(os.path.join(run_dir, evidence + ".stderr"), "w") as handle:
                handle.write(done.stderr)
        usage_row, items, started_thread = metrics(done.stdout)
        if thread is None:
            thread = started_thread

        findings = None
        if os.path.exists(answer):
            try:
                with open(answer, encoding="utf-8") as handle:
                    findings = len(json.load(handle).get("findings") or [])
            except ValueError:
                findings = "unparseable"

        row = {"round": index, "job_id": job_id, "invocation": invocation,
               "evidence": evidence,
               "mode": "cold" if index == 1 else "resumed",
               "thread": thread, "returncode": done.returncode, "seconds": elapsed,
               "response_items": items, "findings": findings,
               "input_tokens": (usage_row or {}).get("input_tokens"),
               "cached_input_tokens": (usage_row or {}).get("cached_input_tokens"),
               "output_tokens": (usage_row or {}).get("output_tokens")}
        rows.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)

    with open(os.path.join(OUT, "spike.jsonl"), "a") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
