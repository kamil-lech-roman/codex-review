# Does `max` effort earn its cost over `xhigh`?

**Effort decision, 2026-09-11: `xhigh`.** The current stopping and cost rules live in
[When the loop is done](../../SKILL.md#when-the-loop-is-done).

Closed as a decision rather than as a measurement, because the measurement did not separate the
arms and buying the precision that would is not worth what it costs. **Inconclusive, not null:**
the means differ by 0.27 findings against a standard error of 1.49, and repeating this same design
does shrink that — to 0.64 at twenty runs per arm, to 0.29 at a hundred. Separating an effect this
small needs roughly 900 runs per arm, which at about a million input tokens a run is a billion
tokens to choose between two settings whose observed difference is a quarter of one finding.
`max` bought no recall that `xhigh` did not also reach, cost about twice as much, and — the fact
that decides it — varied as much within itself as between the two. What replaces the precision
nobody is going to buy is the stopping rule: repeated cheap rounds, each judged cold, converging
on a fresh conversation that finds nothing. A rule that survives noisy single runs is worth more
here than an effort setting chosen from them. That was this experiment's rationale;
the linked skill section owns the current loop procedure, including the later cost trial.

The data below stands as the evidence for that, including where it is thin.

This public summary retains the measured results. The raw job records and experimental
runner remain private; the tables are not a publicly reproducible benchmark.

Eight runs on one `target_sha`, reserve, effort the only difference, all verified comparable (one
normalised prompt hash, one policy hash). Five `xhigh` runs, not three: two were driven by
an experimental runner rather than by the driver, on the same model, effort, target and
prompt.

| effort | findings | input tokens |
|---|---|---|
| xhigh | **2, 3, 5, 5, 7** | 1,431,892 · 464,110 · 893,234 · 1,863,594 · 550,799 |
| max | 3, 4, 7 | 2,132,781 · 2,691,066 · 1,462,945 |

| | mean findings | range | findings per million |
|---|---|---|---|
| xhigh | 4.40 | 2–7 | **4.23** |
| max | 4.67 | 3–7 | 2.23 |

**The arms do not separate.** `xhigh`'s range contains `max`'s entirely and the means differ by 0.27
across a spread of five, against a standard error of 1.49. What separates them is price: `max`
never ran under 1.46M input tokens while four of five `xhigh` runs did, so per token spent `xhigh` returns about 1.9x more.

**Cost does not predict recall inside either arm.** `xhigh` bought 7 findings for 550,799 and 2 for
1,431,892; `max`'s best run was also its cheapest. Whatever more reasoning buys is not visible here.

Two earlier revisions of this file drew a conclusion the next run reversed — one from two `max` runs,
before the third crossed the means over; one from three `xhigh` runs, before the spike's two widened
the range from 3–5 to 2–7. Both are recorded rather than overwritten, because concluding at n=3 is
the pattern most likely to repeat.

## Design

**Superseded.** This began as one effort per conversation chosen at random, thirty conversations per
arm, reasoning that a conversation's rounds share a cacheable prefix and that the conversation is the
unit a reviewer's accumulated context belongs to. Both hold. Neither survives the variance:
conversations review different code, so effort would be confounded with the delta, and at a 2–7
spread on *identical* input sixty unpaired conversations — thirty per arm, a standard error of
0.52 — could not separate arms whose means differ by 0.27.

What replaced it, and where the numbers above come from: **repeated runs against one frozen
`target_sha`**, effort the only difference. Because the driver content-addresses its targets a pair
can be built after the fact — `git worktree add --detach <dir> <commit>`, then prepare from there,
reproduces an earlier `target_sha` exactly.

## Reading the rows honestly

`outcome` distinguishes `reported` from `run-error`, `abandoned` and `incomplete`. **Only
`reported` rows carry a findings count.** An early version of the recorder wrote `findings: 0`
for a job that had produced no report at all, which would have counted two failed runs as two
clean reviews — the difference between "found nothing" and "produced nothing" is the whole
question being asked here.

## Why the unpaired design was abandoned

Three confounds, all of which the paired design removes and none of which more samples would have:

* **Every conversation reviews different code.** Effort would not be the only thing differing
  between two conversations; the delta would differ too, and at 2–7 findings on identical input that
  variance is larger than the effect.
* **A conversation is one sample, not one round**, so thirty conversations per arm is thirty
  unpaired samples — against means that turned out to differ by 0.27.
* **The reviewing agent learns as a conversation runs**, so a fix written in round eight comes from
  someone who has read the file seven times. `round_in_conversation` is still recorded, because that
  effect is real whatever the design.

The paired runs above answer the question these could not: one frozen target, effort the only
difference, and the `prompt_sha256_normalised`, `effective_policy_hash` and `driver_hash` columns to
prove a pair really was one.

## Counting findings is the wrong measure; here is a better one

Severity does not separate the arms — majors ran 3, 4, 3 for `xhigh` and 2, 3, 6 for `max`, which
tracks the raw counts. It is also self-assigned by the reviewer, so it is a weak proxy for value.

Two stronger measures, both available because every finding was checked by hand against the code:

**Precision was 100% in both arms.** All eight distinct defects the six runs reported were real,
and all eight were fixed. Neither arm produced a false positive, so no finding cost anything to
triage away. On this target, "value" reduces to recall.

**Recall against the eight defects the six runs found between them:**

| effort | per run | union of 3 runs | input to get there |
|---|---|---|---|
| xhigh | 5, 5, 3 | **7 of 8** | 3,220,938 |
| max | 3, 4, 7 | **8 of 8** | 6,286,792 |

`max` covered the whole set; `xhigh`'s three runs together missed one — a major, that run-error
rows lose the metadata saying which model and effort actually ran, so a failed run gets filed in
the wrong arm. An `xhigh` run did find it later, on a different target, so this is not something
the effort cannot see.

**Within `max`, cost did not predict findings.** Its 7-of-8 run was its *cheapest* — 1,462,945
input tokens against 2,132,781 and 2,691,066 for the runs that found 3 and 4. More reasoning
bought no more defects; the spread is run-to-run noise, not depth.

## How much a single run can be trusted

Four runs against one identical target found **seven distinct defects between them, and no run
found more than five.** Two defects were reported by all four; three were reported by exactly one.

| defect | 4 runs |
|---|---|
| effort labelled from prepare-time options, not the run | 4/4 |
| round index assigned in random job-id order | 4/4 |
| `target_sha` alone does not establish identical input | 3/4 |
| `turns` counted response items | 2/4 |
| `--conversation` labelled jobs from other conversations | 1/4 |
| the append is a read-then-write race | 1/4 |
| an `incomplete` row burns a still-retryable job's id | 1/4 |

So **a clean round is not evidence of clean code.** A single run finds the obvious defects
reliably and the long tail about a quarter of the time, which means an `approve` after one run
says more about the run than about the target. Where a target matters, review it more than once;
where a loop reports convergence, treat it as "nothing found this time".

## Status

Rows cover the first dogfooding session across `gpt-6-astra`, `gpt-5.6-luna` and `gpt-reserve`,
plus the replicated target above. The headline measurements are in the two sections above; the
working decision is `xhigh`.

Renaming a column does not migrate rows already written. The recorder only appends, so after a
schema change delete `effort-ab.jsonl` and re-record — job directories are the source of truth
and every row can be rebuilt from them.

One early signal, unrelated to effort: two runs ended `run-error` on the same two-artifact target,
one on luna and one on reserve. Both were `declared_scope` rejections — independent corroboration
that the rubric never told the reviewer what that field must contain, which is what
`test_rubric_states_requirements.py` was written for.
