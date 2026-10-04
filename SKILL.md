---
name: codex-review
description: Automatically review significant code changes with Codex before final handoff,
  or review a plan or code target on request. Verify each finding, apply accepted fixes and
  re-verify. Edits the working tree by default; pass --no-apply for a review-only run.
argument-hint: "(plan <path> | code [--base <ref>|--uncommitted] [--head <commit>]) [PREPARE] |
  run <job> [--model M] [--effort E] | status [job] |
  seal <job> <path> [--bytes N] | record-probes|record-apply|record-verification <job> --*-file F |
  abandon <job> --reason R [--denied-interactively]
  ⟨PREPARE = --verify CMD|--no-verify --reason R, --no-apply, --repo D|--no-repo, --model M, --effort E,
  --max-bundle N, --max-artifact N, --note T, --companions-file F, --dependencies-file F⟩"
allowed-tools:
  - Bash(/bin/bash ${CLAUDE_SKILL_DIR}/review.sh:*)
  - Read
  - Grep
  - Glob
---

# codex-review

## Automatic code review

Run this skill after each completed batch of significant code changes, before reporting the
work finished. Significant changes affect behavior, public interfaces, shared primitives or
architecture, including non-trivial bug fixes. Run relevant local checks first. Cosmetic-only
edits need review only when requested; explicit plan and code reviews remain supported.

The implementing host starts the review. A subprocess already acting as the reviewer must
return its report without launching another review. Report any refusal or execution failure
as an incomplete review, never as approval.

`review.sh` performs the Codex job's mechanical steps and validates its payloads. You perform
the steps that need judgement: designing probes, judging findings, editing files, and deciding
what comes next. **Never work around a refusal** — a non-zero exit is the contract holding, and the fix is
to correct the submission, not to route around the verb.

Locate `review.sh` beside this `SKILL.md`, following any skill-directory symlink. Claude
provides that directory as `${CLAUDE_SKILL_DIR}`; in Codex, derive it from the loaded skill's
path instead. In the examples below, `$R` means `/bin/bash "/absolute/skill/path/review.sh"`:
invoke the executable and quoted script path as separate arguments, not one command string.
Use the host's file and shell tools for the Read/Write/Bash steps; the `allowed-tools`
frontmatter is Claude-specific and does not grant permissions in Codex.

Every verb prints one JSON envelope whose
`exit` equals the process exit status. Read `job_dir` from `prepare`; it is absolute and survives
turns and sandbox transitions.

## The flow

Before preparing a job, consult [the loop policy](#when-the-loop-is-done) and the saved loop
state. Steps 1–5 below apply to Codex rounds; use the linked recording procedure for Claude-only rounds.

### 1. Prepare — no tokens spent

```
$R prepare plan <path> [PREPARE]
$R prepare code [--base <ref> | --uncommitted] [--head <commit>] [PREPARE]
```

**Pass each option to the verb that consumes it.** `--no-apply`, `--verify`/`--no-verify`,
`--note`, the capture limits and the target options are frozen into the job here and nowhere
else; an option outside the verb’s supported argument set refuses with exit `2`,
`bad-option`, naming the option in `error.details.option` and listing what that verb accepts in `error.details.accepted`.
The refusal exists because `run <job> --no-apply` was once silently dropped, leaving a job in
apply mode that its operator believed was review-only.

**`--head <commit>`** names the commit you mean to review, and `prepare` refuses with
`head-moved` if the repository has moved off it. The committed projection is `base..HEAD` of the
working directory, so a commit landing between choosing the base and preparing widens the target
in silence: in one round that captured a parallel session's files and returned findings about
them. Pass it whenever anything else may commit in the same checkout — another agent, a hook, the
user — or prepare from a worktree pinned with `git worktree add --detach <dir> <commit>`, which
gives the round a HEAD nothing else can move.

New preparations require exactly one verification intent: `--verify CMD` or
`--no-verify --reason R`; an opt-in loop also accepts `--defer-verify --reason R`.
Declining is explicit and cannot be changed into later command selection.
Code capture rejects conflicting `--base`/`--uncommitted`; every verb rejects options it cannot honor.

Before a plan review, reconcile its named governing authorities with the supplied companion list;
resolve omissions or ambiguity before preparing. Pass `--companions-file F`, a JSON array of
`{"id": "stable-name", "path": "repo-relative-or-absolute"}` entries; without a repository, use
absolute paths. Capture binds supplied identities and bytes, not completeness of the list.

**`--note TEXT`** carries orienting context the capture cannot express — which paths are reference
material rather than the change, the current governing decisions and their rationale. For a qualifying cold round, exclude
prior findings, verdicts and dispute narratives; classify mixed documents passage by passage under
[D03](docs/plans/phase-1b.md#decisions). The note cannot suppress findings or change the verdict rules.
Prefer it to narrowing the capture: a note redirects attention, where dropping paths from the
target removes them from review entirely and the reviewer is never told they existed. What the
capture holds is what was looked at, and that is the claim the report makes.

Print the resolved path and `target_sha` to the user before going further. Refusals here are
final and cheap: `2` arguments, `3` target resolution, `5` policy, `6` unsupported or oversize,
`7` codex unresolvable, `9` preflight.

**Choose the base deliberately — it is the single biggest lever on what a review costs.**
For one-pass reviews, `--base` should name the commit the last review saw, so the target is
the work done since. The
empty tree makes every file an artifact and re-reviews the whole repository; on this repo that
cost roughly twice as much per round and bought nothing, because every finding across two such
rounds landed in a file the incremental delta already contained. Breadth is not free: attention
spread over forty files is shallower per file than attention on six, and depth is where findings
live.

A whole-repo pass is still worth running occasionally — after a refactor that crosses module
boundaries, or before a milestone — because one class of defect is structurally invisible to a
delta review: an inconsistency where the *unchanged* side is the wrong one. The repository stays
available as read-only context either way, so a delta review can still consult code it did not
capture.

**Review the whole delta in one round. Do not split it into file groups.** Narrowing the target
below the delta looks like the same lever as choosing a later base, and it is not: a base excludes
work already reviewed, while a group excludes work that has *never* been reviewed and does not
tell the reviewer it exists. Splitting one delta across N rounds costs more and finds less.

* **It multiplies the fixed cost.** Each round re-reads the same orienting material — the contract,
  the plan, the design docs — and spends a fresh reasoning budget doing it. The cost law is
  `turns × context`, and N groups lose on both factors against one round over the union.
* **It hides sibling defects, which are the delta's most likely defects.** The same author fixing
  one bug writes the same bug into its neighbour; a shared rule gets implemented in one reader and
  not the other. Two halves of such a pair in *different* rounds is worse than either extreme,
  because by the time one is fixed the other's round has already closed. Measured on one repo: a
  four-group rotation returned four findings in its last round, **three of them siblings of holes
  the previous round had just fixed in a file the split had put elsewhere** — a truncation guard
  added to one HTML reader and not its neighbour, a number grammar hardened in one parser and not
  the other, a `Decimal`-context fix applied to a function whose sibling's docstring claimed "the
  same reasoning". All three are the unchanged-side class above, manufactured by the split itself.
* **Size is not the reason to split.** There are no default byte caps. Optional `--max-artifact`
  and `--max-bundle` impose positive explicit limits; check the submitted scope and size before launch.

Reach for a group only when the delta genuinely holds unrelated work — two features that share no
rule and no reader — and say so in `--note`.

### 2. Run — one Codex call

```
$R run <job> [--model M] [--effort E]
```

For one-pass jobs, `--model` and `--effort` here override the prepared values for this call only: `run_metadata`
records what was used, while `target.json` keeps what `prepare` froze. Prefer setting them at
`prepare`, so the job records its own reviewer. Loop runs refuse these overrides: their
model and effort come from the loop policy.

Exit `0` gives `verdict`, `findings[]` and `run_metadata`. Exit `7` means the reviewer never
answered — **the job stays at `prepared` and you may retry it**. When codex named a usage-limit
reset, `error.details.retry_after` is that moment (ISO 8601, local); otherwise it is `null`. Exit `8` means it answered
incoherently: the job is closed by `run-error.json` and a new `prepare` is needed.

### 3. Probe every finding — before believing any of it

For each finding `F-N`, decide what observation would settle whether the claim is true, then:

```
mkdir -p "$job_dir/staging" "$job_dir/probes"
Write   <job_dir>/staging/probe-F-N.sh        # the command, via the Write tool
$R seal <job> staging/probe-F-N.sh            # → ev_cmd, sealed BEFORE it runs
Run     execution.argv in execution.cwd    # host tool; capture probes/F-N.out and F-N.rc as below
$R seal <job> probes/F-N.out
$R seal <job> probes/F-N.rc
```

For every new-job author execution, `seal` returns `execution`. Require `ready: true`;
otherwise collect the matching [qualification controls](docs/plans/phase-1b.md#state-isolation-and-recovery)
and reseal. Both runtime controls and the returned command-specific key must qualify;
generic runtime evidence alone does not qualify a new command. Sealing alone grants no
execution authority. Control collection uses separate
staging slots and earns no review credit. Do not invent passing controls or bypass a refusal.

Run the returned argument vector in its recorded cwd through the host's permission flow.
It executes the sealed copy with fresh `PYTHONPYCACHEPREFIX` and `NUMBA_CACHE_DIR` values.
Author launches clear the ambient environment and preserve only the explicitly returned
`inherited_environment` (`PATH`, `HOME`, `TMPDIR`, locale, `TZ`, `VIRTUAL_ENV` when present),
bound into qualification. Shell startup hooks and exported functions are not inherited.
Additional runtime settings must be explicit in the sealed command and covered by its controls.
Use the exact returned values; reseal for each execution, including retries. Commands that
override or ignore those settings require separate qualification. Preserve both combined
stdout/stderr and the shell status even when inherited `errexit` is set:

```sh
rc=0; { ( cd "$execution_cwd" && "${execution_argv[@]}" ) > "$output_path" 2>&1; } || rc=$?
printf '%s\n' "$rc" > "$rc_path"
```

Build `execution_argv` as an array from the returned JSON argv, preserving each argument;
never reconstruct it with `eval` or omit environment arguments. Use its returned cwd. Output/rc slots
are `probes/F-N.out` / `probes/F-N.rc` for a probe, and `verify.out` / `verify.rc` for
verification. The command evidence ID binds the retained execution settings; no new
recording field is needed. Legacy captures without execution metadata retain their original
sealed-command procedure.

**Execute the sealed copy, never the original.** The sealed artifact *is* what ran.

If a probe is refused by the permission flow, record `probe_execution: "denied"` with its
`denial_kind` and keep `command_evidence_id` — the script was sealed before the attempt, so a
denial proves exactly what was refused. **Do not fabricate the absence**: `probes/F-N.out` and
`.rc` must genuinely not exist, and the driver checks.

Then write the payload and submit:

```
Write   <job_dir>/staging/probes.json
$R record-probes <job> --probes-file <job_dir>/staging/probes.json
```

All recording payload paths resolve against the job, independently of cwd, and must remain
under its `staging/` directory. An absolute path inside that directory is also accepted;
traversal and symlink escapes are refused. `seal` uses job-relative evidence paths.

The payload is `{ "report_sha256": "…", "annotations": [ … ] }`; every recording verb carries
the digest of each record it consumes, and a stale or missing one is refused. Quote the digest
the previous verb's envelope reported: `report_sha256` from `run` (or current `status` after adjudication), `probes_sha256` from
`record-probes`, `apply_sha256` from `record-apply`.

Accepted fixes acquire durable exclusive ownership of the target's worktree (or plan directory
outside Git), independently of repository-context selection, until closure or
abandonment. Exit 16 refuses absent, unknown or conflicting ownership; another job may not steal it.
Changed captured inputs before recording probes invalidate the job (exit 4).

**Change nothing in the target before `record-probes` returns.** It binds the pre-apply baseline
that `record-apply` diffs against, so a fix written first vanishes into that baseline and apply
reports `apply-incomplete` with `changed_paths: []`.

One annotation per reported finding — no omissions, no duplicates. `accepted` requires all four:
the finding is `introduced`, the probe `executed`, the read `confirmed-introduced`, and
`fix_paths` non-empty. Anything less is `rejected` or `inconclusive` with a `rationale`.

Exit `14` means at least one probe was denied; probing is `incomplete` but the job continues.

For a loop dispute, use `rebut LOOP --round R --expected-generation G --rebuttal-file staging/rebuttal.json`
after read assessment and before apply, following the plan's
[rebuttal contract](docs/plans/phase-1b.md#findings-corrections-and-rebuttals). The payload is:

```json
{
  "checkpoint_sha256": "from status",
  "report_sha256": "original report digest",
  "probes_sha256": null,
  "claims": [{"reference": {"round": "origin round nonce", "local_id": "F-1"},
              "rationale": "Why the cited evidence contradicts this claim",
              "evidence_ids": ["sealed evidence nonce"]}]
}
```

Use the probes digest instead of null if probes were already recorded. The reply exposes
`adjudication` and an effective `report_sha256` through `status`; original reports stay intact.
First follow `read_assessment_requirements` through the plan's
[per-judgment read gate](docs/plans/phase-1b.md#state-isolation-and-recovery).
Probe every effective finding, including new IDs, using that report digest and
`adjudication_sha256`. If status exposes `previous_probes_sha256`, include it to append
a probe revision. A rejection of a maintained claim additionally needs
`reopening: {"rationale": "new actionable basis", "evidence_ids": ["sealed new evidence"]}`;
this also applies to later-round refutation or recurrence decisions and carried probe
annotations. Explicit user direction may instead add `asserted_by: "user"` and
`authorization` to that block. Resealing identical evidence cannot reopen the dispute. A reviewer withdrawal alone
does not substitute for the author's probe.

A stalled dispute uses `continue --decision-file F` with `kind: "dispute-resolution"`,
`round`, rebuttal `attempt`, `checkpoint_sha256`, `rationale`, `evidence_ids` and
`asserted_by: "author"` for new actionable evidence, or `"user"` with a cited
`authorization`. This releases investigation; it neither withdraws findings nor grants
another rebuttal. Retiring an irrecoverable rebuttal preserves the original findings.

### 4. Apply what was accepted

Edit only the paths declared in `fix_paths`. Develop fixes the way you would any change here —
failing test first, then the change. Then:

```
Write   <job_dir>/staging/apply.json          # results only; a verification block is refused
$R record-apply <job> --apply-file <job_dir>/staging/apply.json
```

The payload is `{ "report_sha256": "…", "probes_sha256": "…", "results": [ … ] }`.

The driver recomputes what actually changed and reconciles it against what you reported. Say what
happened, not what you intended: a `denied` or `failed` attempt that still moved bytes is a
partial fix and is recordable. Exit `10` is a scope violation and closes the job. Exit `11` is
`apply-incomplete` — real, and the job continues.
Declared fixes may overlap companions or dependencies. `record-apply` retains their
post-fix context separately for verification; unrelated context changes still refuse.
Changed dependency bytes require matching execution qualification before verification.

For a loop, each result also carries `sibling_sweep` with `searched_scope` and
`related_sites` (path lists), `rationale`, and `evidence_ids` (sealed check IDs,
required when related sites are listed). An empty site list still needs its rationale. Apply the plan's
[pre-edit amendment rule](docs/plans/phase-1b.md#findings-corrections-and-rebuttals)
through `amend-paths LOOP --round R --expected-generation G --amendment-file staging/amendment.json`:

```json
{
  "checkpoint_sha256": "from status",
  "report_sha256": "from run",
  "probes_sha256": "from record-probes",
  "additions": [{"finding_id": "F-1", "paths": ["sibling.py"],
                 "rationale": "Why this finding needs the added path",
                 "authority": "The existing authorization covering this edit"}]
}
```

After an amendment, include the returned `amendments_sha256` in the apply payload.
The original probes record and baseline remain the evidence cited by `probes_sha256`.

### 5. Verify, then close

Verification runs **only now**, after scope validation, because it executes repository code.

```
Write   <job_dir>/staging/verify.sh           # exactly UTF-8(verify_command) + one "\n"
$R seal <job> staging/verify.sh               # → ev_cmd
Run     execution.argv in execution.cwd    # same sealed-execution procedure; verify.out and verify.rc
$R seal <job> verify.out ; $R seal <job> verify.rc
Write   <job_dir>/staging/verification.json
$R record-verification <job> --verification-file <job_dir>/staging/verification.json
```

The result goes **inside a `verification` block**, not at the top level — a payload without
one is refused rather than read as an empty block:

```json
{ "apply_sha256": "…",
  "verification": { "result": "passed",
                    "command_evidence_id": "…", "output_evidence_id": "…",
                    "rc_evidence_id": "…" } }
```

`passed` and `failed` are **derived from the sealed `verify.rc`**, never asserted, and the
driver enforces which evidence each result requires: `passed`/`failed` need all three ids,
`denied` needs the command id plus its denial fields, and the non-run results take none. `not-reached` is driver-owned: a scope violation
closes at apply and never reaches verification submission.

A valid `record-verification` closes the job. No configured command derives
`not-run-explicitly`; otherwise a one-pass job with no changes derives `not-applicable`.
Configured loop verification still runs on unchanged candidates. In either derived
non-run case `result` may be omitted. Otherwise sealed execution determines `passed`,
`failed` or `denied`. A contradictory submission refuses with exit `8`,
`verification-result-contradicted`; the job stays open and the refused attempt is
retained. Configuration/changed-path contradictions are checked before tree drift;
sealed-return-code contradictions are checked afterward. Drift returns exit `4` and
closes the job. Contradiction details name `derived` or `allowed`, and `submitted`
(strings only; other values use `submitted_type`).
Closure exits are `12` failed, `13` denied, `14` probes incomplete, or `11` apply incomplete;
they are outcomes, not errors to work around.

## Resuming

```
$R status            # every open job
$R status <job>      # stage, resumable, next_verb
```

`next_verb` is what to run next; `null` means the job is closed or needs a decision. Resume by
doing that verb's step, never by repeating a completed one — every record is write-once.

One-pass jobs use `status` and the indicated recording verb. Opt-in Phase 1b loops use
`status LOOP`, explicit `--round R` for round operations and `--expected-generation G`
for every mutation. `continue` records decisions and returns the next operation; it does
not launch a reviewer. Do not invoke member rounds through the one-pass interface.
The [Phase 1b interface](docs/plans/phase-1b.md#skilldriver-interface) defines the new
workflow; implementation acceptance remains outstanding. Neither workflow breaks another
job's apply ownership.

If `status.recording_recovery` is present, run `continue` to adopt the already-published
author record. Do not repeat its command or edit.

For `authorized-commit` or `drift-resolution`, the staged decision carries `round`,
`checkpoint_sha256`, `target_sha256`, observed `candidate_sha256`, `old_head`, `new_head`,
`authority`, `rationale` and sealed same-round `evidence_ids`. Optional `new_base` names a
replacement comparison base. `equivalence` is `reset` (default), `matching` or `unresolved`;
the driver independently compares matching recaptures. Optional `scope` replaces
`companions`, `dependencies` or `expected_paths` (null removes the assertion), using
the prepare declaration formats and a cited `scope_authority`. It records locator mappings
and freezes newly declared inputs before recapture; later movement requires a new decision.
Changed launch settings additionally require the exact `launch_key` exposed in
`status.required_qualifications`; qualification alone does not authorize the transition.

Fixed `finding-resolution` decisions bind `reference`, `reviewer_round`, `verification_round`,
their `report_sha256` / `verification_sha256` and a rationale. When fresh verification
is needed after the fix round, also bind `fix_round` and its `fix_apply_sha256`;
earlier changed paths do not substitute for current qualified verification.

Other `finding-resolution` dispositions also cite `evidence_ids` sealed in the current
round and a rationale. `reassess` binds the current `reference` and `probes_sha256`:
before apply it opens a new probe revision; after an unresolved completed round it
permits recapture while retaining open findings. `recurring` / `refuted` bind an older
`reference`, current `related_reference`, `origin_probes_sha256` and `probes_sha256`.
For an unassessed finding retained across candidate drift, bind `origin_report_sha256`
instead of the absent original probe digest; its original finding remains pending until resolved.
Refutation requires an executed, qualified current counterexample on equivalent inputs;
changed bytes require fixed closure. Recurrence links keep introduced and accepted predecessors open
until independently closed, and cannot downgrade an accepted defect.
When an earlier finding is absent from the current report, independent `refuted` or
`accepted` decisions use an `assessment` with the normal probe annotation schema, `finding_id` and sealed
slots named `retained-<origin-round>-<local-id>`. Bind `origin_report_sha256` and, if present,
`origin_probes_sha256`; omit `related_reference`. The driver checks the original classification,
equivalent inputs and qualified current execution, retaining the assessment separately from
both reviewer reports. An inconclusive read cannot close the finding.
Independent acceptance follows current `record-probes` and precedes apply. It adds an
explicitly attributed retained finding to a separate work report, then returns
`pending_probe_submission` for `record-probes`. Record that submission before editing:
normal ownership, baseline, apply and verification checks still govern the fix, and the
ledger keeps its original round/local-ID reference. The reviewer need not report it again.
`out-of-scope` applies only to a pre-existing scope question: bind its `reference`
and `probes_sha256`, cite current-round evidence, and supply `asserted_by: "user"`
with the explicit `authorization`. It cannot waive an introduced defect.

For an availability pause, `continue LOOP --expected-generation G` clears it only
after the recorded `retry_after`. It neither launches nor refunds an attempt.
`resolve-call` recovers the same deadline from retained output, anchored to the call's
completion date even when recovery happens later.

Call recovery decisions name `attempt`, `checkpoint_sha256` and `rationale`.
`retire-attempt` additionally requires `asserted_by: "user"` and the explicit
`authorization`. With no usable launch identity, also supply same-host
`quiescence_host` and sealed `quiescence_evidence_ids` from inspected process evidence.
That is a cited user assessment, never proof of non-launch; a matching live child
still refuses retirement. Use `resolve-call` when a completion receipt exists.
After identified process quiescence, a complete validated answer can also be recovered
without a receipt; missing exit status and wall/CPU accounting remain unknown. Such
an answer cannot be retired as unusable merely because its receipt was lost.
Late retired output appears in `status.late_evidence`. Reconcile it through
`resolve-call` with `attempt_evidence_sha256` from that entry after process quiescence;
invalid/empty output requires explicit `disposition: "unusable"`. Valid late answers
remain charged, earn no credit and retain unassessed findings without reopening the
old job. Independently probe them in the current round, then use the finding relation
above with `origin_report_sha256` in place of the absent original probe digest.

## Closing without finishing

```
$R abandon <job> --reason "…" [--denied-interactively]
```

Use it when the review is superseded or the user declines to continue. It is the only honest way
to end an open job.

## When the loop is done

The host selects the workflow. **When Codex executes this skill, the Claude-only cost
trial below does not apply:** do not switch to Claude judges or stop Codex at its trial
threshold. For opt-in driver loops, use the completion contract below.

A single `approve` establishes only that call's result. For an opt-in Phase 1b loop,
use the driver's completion state under [D01/D06](docs/plans/phase-1b.md#decisions),
not a manual count of approvals. Refuted findings can qualify; accepted findings,
unresolved decisions and missing closure evidence cannot disappear behind later empty reports.
Fresh input discipline is defined by D03; a targeted recheck earns no clean-round credit.

The [reserve effort experiment](experiments/effort-ab/) does not establish a cost or recall
optimum for other models. Record observed usage by model/effort without extrapolating it.

### Claude Code cost trial

**Claude Code host only — cost trial, 2026-10-01:** stop calling Codex on a piece of work after
**two consecutive Codex rounds return `approve`**. Count the verdict, even when an approval
includes an unaccepted finding; a non-approval or failed round breaks the streak. Track it across
conversation handoffs and fixes to that work. Once the threshold is reached, Codex stays off for
the rest of that loop, including fixes found by the judge.

From then on, run **Claude-only rounds**: launch a new Claude subagent with no conversation
history for each round. Give it the same agreed range (with the head advanced to include fixes),
repository context and demanding judge brief as the combined round, removing only the Codex
steps.
Do not substitute a Codex agent for Claude; if a fresh Claude judge is unavailable, report that
the remaining review is incomplete.

Keep these obligations in the judge brief in both modes:

- Enumerate input classes; do not stop at "I could not construct an input".
- Mutation-test each guard, with an unmodified control; record surviving mutants and limitations.
- Measure every factual claim in the prose against the library and the pre-change code.
- Report the judge's own observations separately from Codex's findings, and say whether each
  was introduced by the range or pre-existing. Check each claim before accepting it.

Claude-only rounds run **outside the driver** for this trial: `run` always calls Codex.
Use the [round recording procedure](references/claude-only-rounds.md); do not create a fake
Codex report or insert the judge's observations into a Codex job. The
[trial evidence and remeasurement procedure](experiments/approval-streak/README.md) record
one loop only: a small sample, not a general recall guarantee.

For this host-managed trial, done means a fresh host conversation's first round reports
no finding that survives checking. Subagent freshness alone does not establish host
freshness. Use one-pass jobs and the linked outside-driver records; do not combine this
trial with the opt-in driver's convergence count. Default to `xhigh` unless the user
selects another effort.

## Handing off to a new session

**Offer to commit the fixes this review applied, before the conversation ends — and commit only
on the user's word.** Committing is theirs to authorise, and the offer covers only what the review
itself changed: a `--uncommitted` target is the user's own working tree, which this skill captured
for reading and must never commit, and under `--no-apply` nothing was applied at all, so there is
nothing to offer.

Make the offer, because the base is what a round costs and what it covers. `--base` captures
`base..HEAD` and nothing else: leave the fixes uncommitted and they are **invisible** to the next
round, which sees an unmoved HEAD and an empty delta — refused now, rather than approving a review
of nothing. Reach for `--uncommitted` instead and the opposite bites: every round recaptures every
uncommitted file, so each round's own regression tests accumulate in the next round's target and
the cost ratchets up round after round.

Committing is what makes the two agree — HEAD moves, so `--base <last reviewed>` is both the whole
of the new work and only the new work.

A commit the user does authorise also makes the handoff legible. A new session inherits the
repository, not this conversation: with the work committed, `git log` says what was reviewed and
`--base` picks up exactly where the last round stopped. With it uncommitted, a fresh session sees
a pile of modified files and no record of which of them anything has already looked at.

## Reporting back

Give the user the verdict, each finding with its disposition and the evidence that settled it,
what was applied, and the verification outcome. Surface **rejected, inconclusive and pre-existing
findings explicitly** — they are the ones needing a human, and silently dropping them defeats the
point of probing. For a Codex job, never claim a fix landed that the driver did not confirm.
For an outside-driver judge round, identify that mode and cite the recorded diff and verification.
