# Does carrying one Codex thread across rounds pay?

Historical experiment. The [current 1b proposal](../../docs/plans/phase-1b.md)
separates the observations below from their broader, unproven generalizations;
the amendment referenced here is preserved in the
[original plan](../../docs/history/codex-review-plan-2026-09-17.md#amendment--measured-2026-09-11-the-round-is-a-step-not-a-job).

**No.** Measured, and the plan amended accordingly — see *Amendment — measured, 2026-09-11* in
Phase 1b of the plan.

`spike.py` drives `codex exec` directly rather than through the driver, because 1a passes
`--ephemeral` and never resumes (§3). Give it two prepared job directories: round 1 runs cold and
persists its thread, round 2 resumes it.

```
CODEX_BIN=~/.codex/plugins/.plugin-appserver/codex \
  python3 experiments/1b-poc/spike.py ~/.codex-review/<job1> ~/.codex-review/<job2>
```

## What it found

| round | input | response items | findings |
|---|---|---|---|
| cold | 782,703 | 17 | 5 |
| resumed | 1,290,752 | 9 | 3 |

Resume halved the turns and tripled the context each turn carried. The resumed round found a
strict subset at 65% more input.

**Every `resume` invocation re-ingests the whole thread as uncached.** Resuming that same thread
with the prompt `Reply with the single word: ok` returned five output tokens and cost 139,999
uncached input against 9,984 cached. The thread resumes logically; the prompt cache does not
survive the process boundary. Round *n* re-ingests rounds 1..*n*-1, so a resumed loop is quadratic
in rounds and pays it in the most expensive token class — the reverse of the reason to reach for
it.

The saving on output does not cover it: resume spent 8,073 fewer output tokens against 54,161 more
uncached input and 453,888 more cached, so it pays only where output costs more than 12x input at
a 10% cache rate, or more than 6.7x with cached tokens free.

## Two things worth keeping

The experiment used inherited sandbox and working-directory settings with `codex exec resume`,
whose help offers neither `-s` nor `-C`. It did not test redirection through config overrides;
see the [later version-bound check](../../docs/evidence/resume-help-2026-09-20.json).
A round whose target lives in a **new** job directory therefore resumed in this experiment with its
working directory pointing at the previous round's job, where every relative path resolves and
every stale artifact is still readable. Told outright that its working directory was stale, the
reviewer did use absolute paths throughout — survivable by prompting, but it is why the amendment
puts rounds inside one job directory rather than giving each its own.

Resumption is still **required** for 1b's rebuttal round: a reviewer can only withdraw or maintain
a claim it made itself. That is a matter of logic, not of quality — the resumed round reviewed
worse here, and at a 2-to-7 spread on identical input it could not have shown otherwise. Resume
once, for rebuttal, never as the loop's normal mode.

## Where the evidence for a row lives

Every row carries an `invocation` and an `evidence` name, and its answer, log and stderr sit
at `$SPIKE_OUT/<invocation>/<evidence>.*`, where `<evidence>` is `round<NN>-<job_id>` — the
round matters because one invocation may review the same job twice, a cold round against a
resumed one being the comparison this spike exists to make. This was not always so: the first version of `spike.py` named
those files by `job_id` alone and opened them `"w"`, while appending summary rows. Running
the same frozen jobs twice — which the effort comparison does by design — therefore kept
both rows and only the later run's evidence. Two jobs in the recorded data are in that
state: for `6d8237ef753d4b2e` the rows read 7 findings and 2, and only one answer file
survives, so the 7 cannot be checked against what produced it.

That number is one of the five in the `xhigh` arm of the effort comparison. It stands as
recorded and is not re-derivable; the correction stops the next run from doing the same.
