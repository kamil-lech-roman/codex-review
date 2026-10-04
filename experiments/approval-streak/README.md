# Approval-streak trial evidence

Kamil chose the trial on **2026-10-01**. The canonical rule is in
[When the loop is done](../../SKILL.md#when-the-loop-is-done); this file owns the
measurement and how to repeat it. The driver is unchanged. A read-only script is
preferable to hand-transcribed counts: it distinguishes a missing report from zero
findings, counts accepted annotations, and makes the cutoff arithmetic reproducible.

## One observed loop: quant-factory time of day and time zones

Checked on 2026-10-02. Loop starting base: `1e92ef5`; Codex model `gpt-6-astra`,
effort `max`. Most rounds reviewed successive deltas, not that same frozen range;
the final two reviewed from the loop base. The actual base/head pairs and input
digests are in [measurement.json](measurement.json), generated from the ordered
[jobs.json](jobs.json). This loop does not replace the skill's `xhigh` default.

| Job prefix | Base → head | Verdict | Findings | Accepted Codex findings |
|---|---|---|---:|---:|
| 9c1a2690 | 1e92ef5 → 4e0f3b7 | approve | 0 | 0 |
| 0b45b809 | 4e0f3b7 → 31238b4 | changes-required | 1 | 1 |
| b596875a | 31238b4 → 3283001 | approve | 0 | 0 |
| 7db4d59e | 3283001 → 90c5135 | changes-required | 2 | 1 |
| 023d71e4 | 90c5135 → 6461c04 | approve | 1 | 0 |
| 7f259f4a | 6461c04 → 408cb8b | approve | 0 | 0 |
| b8502062 | 408cb8b → 46c1968 | approve | 0 | 0 |
| 07859d21 | 46c1968 → 78186fd | approve | 0 | 0 |
| d3bb1664 | 78186fd → 1676299 | approve | 0 | 0 |
| 0e3eaa26 | 1e92ef5 → 1676299 | approve | 0 | 0 |
| 13c228b4 | 1e92ef5 → 5f14c76 | approve | 0 | 0 |

Sources: `~/.codex-review/<full-job-id>/target.json`, `report.json` → `findings`,
and `probes.json` → `annotations[].disposition == "accepted"`. `023d71e4`'s
observation was **confirmed pre-existing, not accepted**: its annotation has no
`disposition`, so calling it a recorded rejection would misstate the source.
The second observation in `7db4d59e` has the same distinction.

The first-approval cutoff is after `9c1a2690`: it would miss **2 accepted Codex
findings**, one each in `0b45b809` and `7db4d59e`. The second-consecutive-approval
cutoff is after `7f259f4a`, following `023d71e4`. Through `0e3eaa26`, that saves
**4 subsequent Codex calls**, not 5. Including the now-finished `13c228b4`, it saves
**5**, with **0 accepted Codex findings missed**. The triggering second call is
not a saved call. This is a retrospective count along the observed fix sequence,
not proof that removing Codex would leave the judge's behavior unchanged.

### What the Claude judges found

In the five rounds `7f259f4a` through `0e3eaa26`, the judges' own observations
produced **6 accepted and fixed defects in 4 rounds**, while Codex reported none.
These counts include pre-existing defects and prose/test defects; they are not
six newly introduced runtime failures. The acceptance/fix attribution comes from
the Claude reports and host decisions, not the empty Codex findings arrays.

| Round | Accepted own observations | Fix commit in quant-factory |
|---|---|---|
| 7f259f4a | Zoned dates passed the ledger schema or raised pandas' error (pre-existing schema gap); host completed the judge's interrupted checks | 46c1968 |
| b8502062 | Sparse date crash introduced by the zone check; pre-existing unpinned validation order | 78186fd |
| 07859d21 | Sparse refusal incorrectly named a time zone (introduced) | 1676299 |
| d3bb1664 | None accepted | — |
| 0e3eaa26 | Two introduced prose errors: pandas' ability to build the 1677 midnight; whether an unmatched exit ever reaches the normaliser | 5f14c76 |

Verified provenance: Claude conversation `cf886829-7517-47df-9e37-96a770c4900e`
under `~/.claude/projects/-Users-kamilroman-AmibrokerCustomFormulas-quant-factory/`.
Host messages `59964d9e-2bc2-4c62-9a88-02d7958a7a53`,
`f8c67746-072e-4a03-85e8-f8c741ad0b38` and
`e64d4460-9c97-4fce-8e6f-8c373cd0b910` record the checked fixes. Judge reports are
in that conversation's `subagents/agent-acd0c7fe3867c944b.jsonl` (b8502062),
`agent-af09fa8f2daa1de79.jsonl` (07859d21), `agent-a14cea36b045f3534.jsonl`
(d3bb1664) and `agent-a3c4fa503b296067e.jsonl` (0e3eaa26). The commit diffs
confirm the six corrections; the historical probes were not re-executed for this amendment.

**The previously running round finished:** `13c228b4` approved with zero findings
and zero accepted annotations. Its job is closed with verification
`not-run-explicitly`, not a driver claim of passing tests. Claude's handback in
`agent-a09ccce15f7b8a3df.jsonl`, message `da24a685-d029-4bec-959f-df222ca3c90e`,
reports no defect from its own checks. This records the outcome without claiming
that the host conversation met the skill's fresh-first-round completion condition.

## Re-measure

From the skill repository root:

```sh
python3 experiments/approval-streak/measure.py \
  --state "$HOME/.codex-review" \
  --jobs experiments/approval-streak/jobs.json > /tmp/approval-streak-measurement.json
diff -u experiments/approval-streak/measurement.json /tmp/approval-streak-measurement.json
```

The script reads only the full job ids explicitly listed, in their supplied order;
it never discovers, resumes, closes or writes a job. It emits source SHA-256 hashes
and counts to stdout. Missing report/probe evidence stays `null`; incomplete evidence
does not establish savings or zero missed findings. Malformed records fail loudly.
It is a measurement of recorded dispositions, not a revalidation of the probes.

For another loop, supply a separate ordered manifest. Retain the exact range,
brief, models/efforts, judge session freshness, independent observations and checked
dispositions using the [round record](../../references/claude-only-rounds.md).
Count unique accepted defects separately by source and introduced/pre-existing
classification; do not add the judge's observations to Codex's count. Measure calls,
tokens/time when available, and accepted defects after each candidate cutoff.

**Small sample: one evolving loop.** The later defects show why the demanding judge
brief matters here; they do not establish either model's general superiority or a
zero false-negative rate. Under the trial, omitted Codex calls have unknown outcomes.
To test missed late Codex defects prospectively, ask Kamil to authorize a bounded
audit that temporarily continues Codex after the cutoff, with the same recorded
inputs and an independent judge. Do not silently spend calls in breach of the rule.
Revisit the threshold when additional loops or an authorized audit reveal accepted
late Codex findings, materially different costs, or a changed model/brief.
