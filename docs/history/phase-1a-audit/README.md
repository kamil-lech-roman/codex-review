# Phase 1a audit — 2026-09-17

**Conclusion:** the single-pass review/probe/apply/verify workflow is implemented,
but Phase 1a is **not complete against its original contract**. Missing behavior
cannot be called superseded merely because the current skill omits it. The next
work is in the [Phase 1b proposal](../../plans/phase-1b.md).

## Baseline and scope

- Repository: `codex-review`, clean commit `9057382` at audit start. Work was
  performed in a separate worktree. Production code and skill behavior were not changed.
- Original source: `~/.claude/plans/if-you-take-a-keen-melody.md`, preserved
  byte-for-byte as [the historical plan](../codex-review-plan-2026-09-17.md).
  SHA-256: `61ff6f807d23fa4a711db35d51b009dbe0a219daf49054b298120c5fd7c9f3a2`.
- Examined the skill, driver paths, test assertions, commit history and relevant
  local user clarifications. This is a contract/coverage audit, not a new external
  model review or an empirical effectiveness study.

## Checks and their limits

The existing default suite passed: **185 passed, 2 deselected**, using the
repository's existing Python environment and this worktree's tests/source.
[JUnit output](offline-tests.xml) records the cases. The two deselected tests
require live Claude sessions and a fake Codex; no live session was run here.
Their offline negative controls are included in the 185, not evidence that the
host itself completed those paths.

The [offline audit probe](probe.py) creates temporary repositories and jobs and
uses a fake reviewer. [Its JSON output](probe-results.json) records these
observations, each contrary to a named old requirement:

| Observation | Original obligation | Evidence |
|---|---|---|
| `accept-delta` and `break-lease` return `verb-not-built`, exit 2. | Recovery and shared apply lease | `main.cli`; probe keys with those verb names |
| Two jobs can record accepted findings against the same plan. | T-93 contention refusal | `two_jobs_accept_same_plan_without_lease`; both return 0 |
| A state root inside the reviewed repository is accepted. | T-21 / T-107 | `state_inside_repository`, exit 0 |
| Prepare accepts apply-enabled work without either verification option. | T-53 | `missing_verify_option`, exit 0 |
| A response with empty evidence, extra fields and `status` is accepted. | T-24 / T-29 | `malformed_semantic_response`, exit 0 |
| Review and probing can proceed after the plan changes; status remains resumable. | T-71 / T-72 | `run_after_target_drift`, `record_probes_after_drift_from_external_payload`, `status_after_drift` |
| A probe payload outside job staging is accepted. | T-48 | The same external-payload probe, exit 0 |

The probe's prepared evidence is synthetic input for mechanical validation, not
an assertion that a model independently confirmed a real defect. It does not
test network behavior, model quality, or host permissions. Reproduce with:

```bash
python3 docs/history/phase-1a-audit/probe.py
python3 -m pytest
```

Other gaps follow from code/test inspection: the five original drift checkpoints
are not all implemented; `status` lacks the old recovery/lease arms; `run` holds
its per-job mutex over the model call; bare listing does not take job locks;
capture has a correspondence check but no promised retry. These are bounded
inspection findings, not claims that a concurrency stress test was run.

The [acceptance inventory](acceptance-inventory.json) accounts for **all 108 T
rows and five E rows**. Its paths are repository-relative unless they name an
artifact in this directory; `:symbol` identifies the inspected function or probe
key. Assessments mean:

- `covered`: the stated row has direct evidence in the executed suite.
- `partial`: useful implementation and/or checks exist, but the entire row's
  matrix has not been demonstrated. This does not mean the whole feature is absent.
- `gap`: identified missing behavior or an observed contract violation.
- `changed-contract`: an explicit different behavior exists in code/tests; this
  is a reconciliation obligation, not automatic permission to drop the old rule.
- `not-established`: required live or specialized evidence was not found/run.

Nine rows are covered, 59 partial, 24 gaps, two changed-contract and 19
not-established. These counts classify demanding multi-case **plan rows**, not
the 185 individual passing tests. They are not percentages of implementation done.

## Milestones and supersession

| Old promise | Assessment / disposition |
|---|---|
| M1 usable single pass | The core path exists with real historical usage. The old M1 also promised refusing a second concurrent job, which is missing; do not mark the entire milestone complete. |
| M2 general lease/recovery, drift, permission matrix | Incomplete. Recovery/lease stubs and missing drift protections are observable. D02 in the new plan proposes a narrower replacement; approval remains pending. |
| M3 controlled E-1–E-5 evaluation | Not established. Local dogfooding jobs include recorded applied-and-verified runs, but no identified seeded evaluation proves all five original cases. |
| Two target kinds and context-free plans | Implemented beyond the original M1 scope; retained as the starting point, not scheduled for rebuilding. |
| Probe/apply/verification schemas and reconciliation | Core mechanisms implemented with regression tests. Missing semantic/schema/channel checks remain explicit in increment 0. |
| Blanket index movement invalidates apply | Current code intentionally accounts for staged corrections (`apply.baseline`; `should_see_a_correction_that_lands_in_the_index`). Retain that ability and define unrelated reference drift separately. |
| Separate no-findings vs none-accepted apply results | Current reconciliation uses `none-accepted` for both; the raw report distinguishes them. The new plan preserves that information without pretending the old outcome row passed. |
| New author conversation required for convergence | Superseded by the user's 2026-09-14 clarification below. |
| Normal rounds all resume; each uses a new job directory | Superseded within the old plan by its later amendment. The new proposal preserves cold normal rounds and stable resume context, while retaining old snapshots. |
| One round completion terminates all work | Correct for existing one-pass jobs. The new loop needs separate round and loop terminality; legacy jobs stay closed. |
| An isolated arbiter fixes A1/A2/A5/A6/A7 | Overclaim in the old plan. Instruction isolation does not prevent author writes, enlarge observed scope or defeat coherent same-user tampering. The new plan states those boundaries separately. |
| Live matrix replaced by two L2 tests | No evidence of such a scope decision. The current two cells do not cover the matrix and cannot retire it. |

## Decision provenance

Relevant user directions were checked against the local conversation records,
not inferred from a later assistant summary:

- **2026-09-11 10:19 UTC**, task record `d24f1b48-997c-4504-a1e8-69094f962ca8`:
  use `xhigh` and continue until a new conversation's first round has no agreed
  issues. This establishes freshness, not a required count of three.
- **2026-09-14 12:52 UTC**, task record `34269dff-93c7-4e45-96ab-a6fb9fd29b6a`:
  “a fresh conversation for codex, without prompting it that we have fixed this
  and this”. The author need not start another conversation.
- **2026-09-17 16:45 UTC**, task record `d52ec47f-a8b0-470b-9a65-50229b914daa`:
  `xhigh` should precede the extra `max` pass; once `max` has started, continue
  there. This corrects the immediately preceding request to begin at `max`.

The user's recorded rejection of a trivial-finding exemption is also retained
in the existing local feedback memory `trivial-findings-still-restart-the-count.md`.
This audit did not independently replay that discussion; the new plan makes the
consequence explicit rather than presenting it as an experimental result.

The 1b PoC's current CLI help was checked locally on
`codex-cli 0.154.0-alpha.6.2`: `exec resume --help` advertises neither `-s` nor `-C`.
That is a help-surface observation, not a fresh runtime proof about inherited
sandbox/cwd or persistence permissions. No model was invoked. The small old
experiment cannot justify its universal uncached-resume claims; at least one
early answer was overwritten, as its own README acknowledges.

## Revision receipt

The live proposal removes the implemented single-pass specification, old freeze
notice, repeated schemas and completed build descriptions. Their exact original
bytes remain in history, and this inventory preserves unfinished obligations.
Reconciled the new plan's scope, version binding, cold-round definition,
rebuttals, stopping conditions, isolation claims, implementation increments and
release evidence. D01/D02 are explicitly proposals awaiting user choice.

No claim is made that all of Phase 1a was completed or superseded, or that the
proposed Phase 1b loop has already been implemented or independently approved.
