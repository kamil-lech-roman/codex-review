# codex-review

A skill and Python driver for evidence-based code and plan review with Codex.
The host agent checks findings, records their disposition, applies authorized
fixes, and verifies the result. A review approval does not establish correctness.

## Requirements

- Python 3.9 or newer, Git, and Bash on a POSIX system.
- An authenticated Codex CLI available as `codex` on `PATH`, or selected with
  `CODEX_BIN=/absolute/path/to/codex`.
- A host agent such as Claude Code or Codex that can execute the skill's workflow.

The runtime uses only Python's standard library. Tests require pytest. The driver
defaults to `gpt-6-astra` at `xhigh`; use `--model` and `--effort` to select settings
available to your account. Your CLI must support the flags used in
[`reviewer.py`](src/codex_review/reviewer.py), including ephemeral execution and
structured output. Model calls use your existing account and may incur usage costs.

## Install

Clone the whole repository so the skill can find its driver and references:

```sh
git clone https://github.com/kamil-lech-roman/codex-review.git "$HOME/src/codex-review"
```

For **Claude Code**, register the directory as a personal skill:

```sh
mkdir -p "$HOME/.claude/skills"
ln -s "$HOME/src/codex-review" "$HOME/.claude/skills/codex-review"
```

For **Codex**, register it in the user skills directory:

```sh
mkdir -p "$HOME/.agents/skills"
ln -s "$HOME/src/codex-review" "$HOME/.agents/skills/codex-review"
```

These commands assume the destination does not already exist. Keep an existing
installation until you have deliberately chosen which copy to use. See the
[Claude Code skill documentation](https://code.claude.com/docs/en/skills) and
[Codex skill documentation](https://learn.chatgpt.com/docs/build-skills) for discovery
and project-local installation options.

## Use

Ask your host agent to review a target, naming the skill and the range explicitly:

```text
Use codex-review to review my uncommitted changes. Do not apply fixes.
Use codex-review to review the code since origin/main and run the project's tests.
Use codex-review to review the plan at docs/plan.md.
```

The canonical workflow, options and completion policy are in [SKILL.md](SKILL.md).
Use `--no-apply` for a review-only run; the normal workflow can edit files after
the host has verified a finding. Commit authorization stays with the user.

The driver exposes individual steps; it is not an unattended end-to-end reviewer.
For example, prepare a review from the repository you want inspected:

```sh
/bin/bash "$HOME/src/codex-review/review.sh" prepare code \
  --uncommitted --no-apply --verify 'python3 -m pytest -q'
```

Preparation prints the resolved target, its hash, and a job id without spending
model tokens. The host follows the remaining steps in the skill, starting with
`review.sh run <job>`, then records its evidence and closes the job. Do not treat
`run` alone as completion of the workflow.

## Review records and privacy

Jobs are stored in `~/.codex-review/`, or the directory selected by
`CODEX_REVIEW_STATE`. They contain captured source, paths, prompts and outputs.
Keep that directory private. A Codex round submits review material to the configured
Codex backend, with the repository available as review context; review only material
you are authorized to submit.

The [cost policy](SKILL.md#when-the-loop-is-done) can switch a loop to Claude-only
rounds. Those rounds are host-managed and have a
[separate recording procedure](references/claude-only-rounds.md); the driver itself
always invokes Codex.

## Development

```sh
uv sync --frozen
uv run pytest
```

The default suite uses a fake Codex executable and does not call a model. Tests
marked `l2` start a real Claude session and are opt-in with `uv run pytest -m l2`.
They consume model usage and require an authenticated Claude CLI.

[Documentation](docs/README.md) includes the review workflow and experiment summaries.
This public release starts from a clean snapshot. Private development history,
review transcripts, archived target captures and maintainer-specific plans are not
included. Archive-integrity checks that require those private artifacts are not part
of the public suite; the repository policy check is retained.

## License

No license has been selected yet.
