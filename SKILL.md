---
name: codex-review
description: Automatically review significant code changes with Codex before final handoff,
  or review a plan or code target on request. Verify each finding, apply accepted fixes and
  re-verify. Edits the working tree by default; pass --no-apply for a review-only run.
argument-hint: "(plan <path> | code [--base <ref>|--uncommitted] [--head <commit>]) [COMMON] |
  status [job] |
  seal <job> <path> | record-probes|record-apply|record-verification <job> --*-file F |
  abandon <job> --reason R [--denied-interactively]
  ⟨COMMON = --verify CMD|--no-verify, --no-apply, --repo D|--no-repo, --model M, --effort E,
  --max-bundle N, --max-artifact N⟩"
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
$R prepare plan <path> [COMMON]
$R prepare code [--base <ref> | --uncommitted] [--head <commit>] [COMMON]
```

**`--head <commit>`** names the commit you mean to review, and `prepare` refuses with
`head-moved` if the repository has moved off it. The committed projection is `base..HEAD` of the
working directory, so a commit landing between choosing the base and preparing widens the target
in silence: in one round that captured a parallel session's files and returned findings about
them. Pass it whenever anything else may commit in the same checkout — another agent, a hook, the
user — or prepare from a worktree pinned with `git worktree add --detach <dir> <commit>`, which
gives the round a HEAD nothing else can move.

**`--note TEXT`** carries orienting context the capture cannot express — which paths are reference
material rather than the change, what not to spend turns on, what an earlier round settled. It is
context, not instruction: it cannot change what counts as a finding or how the verdict follows.
Prefer it to narrowing the capture: a note redirects attention, where dropping paths from the
target removes them from review entirely and the reviewer is never told they existed. What the
capture holds is what was looked at, and that is the claim the report makes.

Print the resolved path and `target_sha` to the user before going further. Refusals here are
final and cheap: `2` arguments, `3` target resolution, `5` policy, `6` unsupported or oversize,
`7` codex unresolvable, `9` preflight.

**Choose the base deliberately — it is the single biggest lever on what a review costs.**
`--base` should name the commit the last review saw, so the target is the work done since. The
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
* **Size is not the reason to split.** `--max-artifact` and `--max-bundle` take the cap you need;
  the defaults are fallbacks, not limits. Raise them to fit the delta and check the largest single
  file against `--max-artifact` — a test module quietly grows past 64 KiB.

Reach for a group only when the delta genuinely holds unrelated work — two features that share no
rule and no reader — and say so in `--note`.

### 2. Run — one Codex call

```
$R run <job>
```

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
rc=0; { ( cd "$exec_dir" && bash "$job_dir/evidence/$ev_cmd/content" ) > "$job_dir/probes/F-N.out" 2>&1; } || rc=$?
printf '%s\n' "$rc" > "$job_dir/probes/F-N.rc"
$R seal <job> probes/F-N.out
$R seal <job> probes/F-N.rc
```

`$exec_dir` is `target.json`'s, never the ambient shell's. The `{ ( … ) } || rc=$?` form is
required, not stylistic: under inherited `errexit` the plain form writes `.out`, never writes
`.rc`, and exits.

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

The payload is `{ "report_sha256": "…", "annotations": [ … ] }`; every recording verb carries
the digest of each record it consumes, and a stale or missing one is refused. Quote the digest
the previous verb's envelope reported: `report_sha256` from `run`, `probes_sha256` from
`record-probes`, `apply_sha256` from `record-apply`.

**Change nothing in the target before `record-probes` returns.** It binds the pre-apply baseline
that `record-apply` diffs against, so a fix written first vanishes into that baseline and apply
reports `apply-incomplete` with `changed_paths: []`.

One annotation per reported finding — no omissions, no duplicates. `accepted` requires all four:
the finding is `introduced`, the probe `executed`, the read `confirmed-introduced`, and
`fix_paths` non-empty. Anything less is `rejected` or `inconclusive` with a `rationale`.

Exit `14` means at least one probe was denied; probing is `incomplete` but the job continues.

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

### 5. Verify, then close

Verification runs **only now**, after scope validation, because it executes repository code.

```
Write   <job_dir>/staging/verify.sh           # exactly UTF-8(verify_command) + one "\n"
$R seal <job> staging/verify.sh               # → ev_cmd
rc=0; { ( cd "$exec_dir" && bash "$job_dir/evidence/$ev_cmd/content" ) > "$job_dir/verify.out" 2>&1; } || rc=$?
printf '%s\n' "$rc" > "$job_dir/verify.rc"
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
`denied` needs the command id plus its denial fields, and the non-run results take none.
`not-reached` is the third non-run result, but the driver sets it alone (a scope violation
closes at `record-apply`), so it is never submitted.

`record-verification` **always** closes the job, including under `--no-verify`
(`not-run-explicitly`) and when nothing landed (`not-applicable`). Exits `12` failed, `13`
denied, `14` probes incomplete, and `11` when the apply was `apply-incomplete` (the job is
closed, unlike at `record-apply`) — all real outcomes, none of them errors to be worked around.

**The derivation decides the result, in this order:**

1. no verify command (`--no-verify`, or no `--verify` given; a blank `--verify` is refused at
   `prepare` with `blank-verify`, not read as no command) → `not-run-explicitly`, **even
   when nothing landed**;
2. otherwise nothing landed → `not-applicable`;
3. otherwise the command ran, and the sealed evidence picks `passed`, `failed` or `denied`
   (a command the operator was refused is `denied`).

Submit the derived result; where 1 or 2 decides alone you may omit `result` and the job
closes with it. A `result` that names anything else — `not-applicable` on a job with no verify
command, `not-run-explicitly` where a command exists but nothing landed, `passed` where no
command exists, `not-applicable` where the command ran, or `passed` against a sealed `rc` that
says `failed` — is refused with exit `8` and `verification-result-contradicted`. Its details
name both: `submitted` (only when it is a string; `submitted_type` otherwise), and `derived`
or, in tier 3, `allowed`. No verification is recorded and the job stays open for a corrected
submission; the refused attempt is journaled under `attempts/`. This check runs before the
drift check, so a submission that both contradicts and meets a moved tree is refused as
contradicting first, and meets the drift (exit `4`) when corrected.

## Resuming

```
$R status            # every open job
$R status <job>      # stage, resumable, next_verb
```

`next_verb` is what to run next; `null` means the job is closed or needs a decision. Resume by
doing that verb's step, never by repeating a completed one — every record is write-once.

There is no `continue` verb and no `break-lease`: `status` plus the next verb is the whole
resumption path, and a job you cannot finish is closed with `abandon`.

## Closing without finishing

```
$R abandon <job> --reason "…" [--denied-interactively]
```

Use it when the review is superseded or the user declines to continue. It is the only honest way
to end an open job.

## When the loop is done

**A Codex `approve` is not convergence or evidence that the code is right.** Measured on one
frozen target, six runs found eight distinct defects between them and no single run found more
than seven. In a four-run replication
of the same design, where the per-defect counts were recorded, three of the seven defects found
were reported by exactly one run. A clean round says what this run found; it does not establish
correctness.

**Done is when a fresh conversation's first round reports nothing you agree with.** A round late
in a conversation is judged by someone who has been arguing about this code for hours and will
recognise a finding as one already discussed; a first round is not. Anything that round does
find — that survives checking — restarts the count, so consecutive clean firsts accumulate into
the evidence a single `approve` never was.

"You agree with" is load-bearing. A finding checked against the code and refuted is not a defect,
and must not block the loop; record the refutation and move on. See **Reporting back**.

**Cost trial — Kamil's decision, 2026-10-01:** stop calling Codex on a piece of work after
**two consecutive Codex rounds return `approve`**. Count the verdict, even when an approval
includes an unaccepted finding; a non-approval or failed round breaks the streak. Track it across
conversation handoffs and fixes to that work. Once the threshold is reached, Codex stays off for
the rest of that loop, including fixes found by the judge. This changes the cost of the loop,
not the definition of done above.

From then on, run **Claude-only rounds**: launch a new Claude subagent with no conversation
history for each round. Give it the same agreed range (with the head advanced to include fixes),
repository context and demanding judge brief as the combined round, removing only the Codex
steps. Continue until a fresh conversation's first round reports nothing you agree with.
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

Run at `xhigh`. `max` costs roughly twice as much for the same defects, and its best run was its
cheapest — more reasoning did not buy more findings. `experiments/effort-ab/` has the numbers.

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
