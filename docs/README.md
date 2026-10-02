# Documentation

- [Getting started](../README.md) — requirements, installation, usage and tests.
- [Skill workflow](../SKILL.md) — the canonical review and completion instructions.
- [Claude-only rounds](../references/claude-only-rounds.md) — host-managed records.
- [Approval-streak trial](../experiments/approval-streak/README.md) — public evidence
  summary, limitations and how to measure your own loop.
- [Effort experiment](../experiments/effort-ab/README.md) — observations behind the
  default effort setting.
- [Review-scope experiment](../experiments/review-scope/README.md) — measured costs
  and limits of the comparison.

The runtime is implemented in `src/codex_review/`; `review.sh` sets up its import
path and invokes the driver. The host agent performs the judgment and editing steps.
Private transcripts and archived development inputs are not distributed here.
