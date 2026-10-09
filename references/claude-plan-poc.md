# Claude plan review PoC

Implement a separate, single-round Claude launcher for plan reviews. Reuse the
existing rubric, response schema and validator; leave the Codex job lifecycle
unchanged. This is the first runnable slice of a future reviewer adapter.

The caller selects a plan and either a context repository or explicit context-free
mode. Capture the plan before launch in a new evidence directory outside that
repository. Default to Fable at high effort, with no fallback model. Start a fresh
session with only Read, Glob and Grep; Git commands and test execution are outside
this PoC. Mark claims that need execution as unverified. Repository context is live,
not a frozen checkout; use an isolated checkout when repeatability matters.

Build the child environment from an allowlist. Subscription login is required;
inherited API keys and Claude session configuration are excluded. An identity
preflight is an additional refusal condition, not proof of which credential a
request ultimately uses. API authentication is outside this PoC. Do not query CLI
versions, hash executables, or make updates a reason to reset a review.

Save the prompt, target, schema, raw stream, stderr and terminal result. Normalize
structured output through the shared response validator. Process failure, timeout,
missing or invalid output, unexpected tools and reported permission/tool errors
are incomplete reviews, never approval. Retain reviewer text even on failure.
Use a bounded subprocess wait with process-group termination; no automatic retries.
A complete report awaits host adjudication and does not close a review loop.

Acceptance: fake-CLI tests exercise capture, invocation, environment/auth refusal,
response normalization and failure retention; targeted mutants must fail those
tests. A live invocation through the new entry point exercises the actual CLI.

## Run

An authenticated Claude Code CLI must be available. No Codex login is needed for
this entry point. The CLI must support the flags used in
[`claude_plan.py`](../src/codex_review/claude_plan.py).

```sh
/bin/bash /path/to/codex-review/claude-review.sh /path/to/plan.md \
  --repo /path/to/repository --model fable --effort high
```

Use `--no-repo` instead of `--repo` for a context-free review. Set `--claude-bin`
to select a CLI executable, `--output` to choose a new evidence directory, and
`--timeout` to change the review deadline in seconds.
Auth preflight has a separate bounded wait. Output inside `--repo` and reuse of an
existing output directory are refused. The default output is a fresh directory
under `~/.codex-review-judges/`.

The command prints the captured plan path and SHA before contacting Claude, then
prints a final result. Exit `0` means a report passed the shared validator, including
when its verdict is `changes-required`; `2` is CLI usage, `7` is preparation/auth
failure, and `8` is an incomplete review. No model or effort fallback is attempted.

Read `report.json` for validated findings and `run.json` for status and model
selection. `stream.jsonl` and `stderr.txt` hold raw review output after the process
finishes; while running they are `review.stdout` and `review.stderr`. `report.txt`
extracts the assistant's narrative when the stream can be parsed, while
`response.json` retains structured output before validation. A failed parse leaves
raw output intact. Auth stdout/stderr are saved separately. Keep this directory
private: it contains source, CLI output and account metadata.

The helper ends at `awaiting-adjudication`. Check each finding and record your
observations using the [Claude-only procedure](claude-only-rounds.md). `run.json`
is launcher evidence, not a completed host `round.json`. It never applies fixes or
calls Codex's recording verbs.

## Limits

This PoC does not implement code reviews, loops, retries, API auth, policy-file
synthesis, shell/Git inspection, session resumption or full sandbox qualification.
It uses the shared rubric directly in the prompt and disables ambient settings.
The CLI may still list built-in skills/plugins in its init metadata; this is not
an attestation that all customization is absent. The reported tool set and
permission mode are checked after the call, not used as an OS security boundary.
History exclusion is a prompt instruction, not a guarantee of a cold review.

Any reported tool error makes this PoC incomplete, even a recoverable missing-file
read. Process-group termination is best effort and does not certify cleanup of
children that detach from that group. An interrupted run retains its evidence;
there is no automatic retry. Repository context is not copied or fingerprinted.

## Check

```sh
uv run pytest tests/l1/test_claude_plan.py
```

The fake CLI cannot establish subscription billing, real model availability or
sandbox behavior. A live invocation uses subscription quota and checks only the
capabilities actually exercised in its retained stream.
