# Approval-streak trial evidence

The maintainer selected this cost trial on **2026-10-01**. The canonical rule is in
[When the loop is done](../../SKILL.md#when-the-loop-is-done). This document contains
a public summary of the evidence and a procedure for measuring another loop.

## Observed loop

One evolving review loop concerned date/time validation in a Python research project.
All eleven Codex rounds used `gpt-6-astra` at `max`. Most reviewed successive deltas;
the final two reviewed the complete range. This does not change the skill's `xhigh`
default. The private job records were checked on 2026-10-02, using the same fields
read by [`measure.py`](measure.py).

| Round | Codex verdict | Findings | Accepted Codex findings |
|---|---|---:|---:|
| 1 | approve | 0 | 0 |
| 2 | changes-required | 1 | 1 |
| 3 | approve | 0 | 0 |
| 4 | changes-required | 2 | 1 |
| 5 | approve | 1 | 0 |
| 6 | approve | 0 | 0 |
| 7 | approve | 0 | 0 |
| 8 | approve | 0 | 0 |
| 9 | approve | 0 | 0 |
| 10 | approve | 0 | 0 |
| 11 | approve | 0 | 0 |

The observation in round 5 was confirmed pre-existing and not accepted, rather than
recorded as rejected. Round 4 also contained one confirmed pre-existing observation.
An approval and an empty findings list are separate facts.

A first-approval cutoff after round 1 would miss **2 accepted Codex findings**.
A two-consecutive-approval cutoff after round 6 would save **4 subsequent calls
through round 10**, or **5 including round 11**, with **0 accepted Codex findings
missed**. The call that reaches the threshold is paid for, not saved.

In rounds 6–10, Claude judges' own observations produced **6 accepted and fixed
defects in 4 rounds**, while Codex reported none. These included a pre-existing
schema gap, a sparse date handling exception, missing coverage of validation order,
a misleading refusal message, and two inaccurate prose claims. They were not six
new runtime failures. In round 6, the host completed the judge's interrupted checks.
The last round finished with no Codex findings and no defect reported by the judge.
Its driver verification was explicitly not run; the judge reported separate checks.

**Evidence boundary:** the raw jobs, transcripts and source project remain private.
This is a maintainer-reported summary of checked records, not a publicly reproducible
benchmark. The script lets another maintainer measure their own available records;
it does not reconstruct this private loop. A clean final report alone does not prove
the host conversation met the skill's completion condition.

## Measure your own loop

Create a JSON file containing the full 16-character job ids from one loop, in actual
round order. Include only jobs you intend to measure. Then run:

```sh
python3 experiments/approval-streak/measure.py --state "$HOME/.codex-review" \
  --jobs /path/to/your-loop.json > /tmp/approval-streak-measurement.json
```

The script reads `target.json`, `report.json` and `probes.json` from the named job
directories and emits source SHA-256 hashes, verdicts, findings counts and accepted
annotation counts. Accepted means `annotations[].disposition == "accepted"`.
It neither discovers unrelated jobs nor changes any job. Missing report/probe
evidence stays `null`; incomplete evidence cannot establish savings or zero missed
findings. It measures recorded dispositions, not the correctness of the probes.

Record range, brief, model/effort, judge freshness and the judge's independent
observations using the [round procedure](../../references/claude-only-rounds.md).
Count unique accepted defects by source and introduced/pre-existing classification;
do not add the judge's observations to Codex's findings. Compare calls, available
tokens/time and accepted defects after each candidate cutoff.

**Small sample: one evolving loop.** These retrospective counts follow the observed
fix sequence; they do not prove that removing Codex leaves judge behavior unchanged,
establish either model's general superiority, or guarantee zero missed defects.
Omitted Codex calls have unknown outcomes. A prospective comparison needs the
maintainer's authorization for a bounded audit continuing Codex after the cutoff.
Revisit the threshold for additional evidence, materially different costs, or changes
to the model or judge brief. Do not silently spend audit calls in breach of the rule.
