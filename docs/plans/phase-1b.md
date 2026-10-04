# Phase 1b — review, rebuttal and convergence

**Implementation authorized, 2026-09-29; decisions below are user-approved.** The
[Phase 1a audit](../history/phase-1a-audit/README.md) is the baseline; the
[previous plan](../history/codex-review-plan-2026-09-17.md) is historical.
The [skill baseline](../../SKILL.md) is revision `4e1b897`.
The single-pass workflow exists, but its original contract is **not complete**.
Resolve increment 0 before enabling the loop; missing work is not superseded.

## Outcome and scope

An opt-in run reviews a plan/code change, probes findings, applies authorized
corrections, re-reviews them, resolves disagreements and reports a truthful result.
The driver owns durable state/checks; the skill owns judgment/orchestration.

Retain the Claude Code skill and one-pass workflow; increment 0 tightens input
contracts. Native Codex skill packaging, hooks, pre-push gates, scheduling,
coordination across separate targets, recursive submodule review and token-optimization
experiments are outside 1b.
The historical gate design is retained in the archive, not an implementation queue.
Use a dedicated worktree for convergence; a branch alone does not isolate files.
This stabilizes edits, not read reach. Shared-checkout loops remain supported but
reset when bound context changes.

## Decisions

User-approved driver policy; acceptance still requires the implementation evidence below.
The [Claude Code host trial](../../SKILL.md#claude-code-cost-trial) is a separate one-pass
workflow, not a replacement for this driver contract.

| ID | Decision | Consequence / alternative |
|---|---|---|
| D01 | Two consecutive eligible clean rounds at `xhigh`, then one clean `max` confirmation, without resetting that streak. If confirmation finds an accepted defect, stay at `max` until three consecutive eligible clean rounds after the last accepted defect. A sequence begun at `max` also needs three clean rounds. | Policy default, not a measured optimum across models; configurable, without effort downgrade. The historical rule specifies cold freshness, not this count. Alternative: user-specified threshold with no default. |
| D02 | Replace unfinished root-wide lease breaking and delta authorization with exclusive apply ownership and pause/re-capture on unexplained drift. | Preserves single-writer and attribution requirements; removes `accept-delta`/`break-lease` from the new workflow. Alternative: implement original M2 in full before the loop. Retire the replaced lease/recovery obligations with their D02 mapping. |
| D06 | At most **16 judgment-bearing calls, four confirmed no-judgment attempts and 20 total launches per loop**, configurable. | Policy default, not a measured optimum. Availability failures consume the retry allowance; every launch remains accounted for. Reopen after cost/completion measurements or threshold changes. |
| D07 | User-directed: remove default bundle/artifact byte caps; retain explicit `--max-bundle` / `--max-artifact` limits across recapture. | Show submitted scope and size before reviewer calls. Replace the skill's interim cap-raising guidance in increment 0. Reopen for measured resource constraints. |
| D08 | Bind declared dependencies including caches; default to qualified fresh cache redirection for Python/Numba author executions. Exclusions remain a qualified option for external writers. | Retained bytes cost storage; uncontrolled bound writes pause/reset. Versions or unchecked exclusions cannot establish equivalence. |
| D09 | Reuse the configured `CODEX_HOME`; do not suppress memory generation or require memory-specific qualification. | User accepts memory as potentially useful context and unobserved carryover as a limitation. D03 still controls supplied material and observed history consumption. Auth, instruction and write controls remain required; memory experiments are outside 1b. |
| D10 | User-directed, revised 2026-09-23: drop explicit reviewer CLI version collection, binary identity checks, pinning and update-triggered resets. | [CLI compatibility](#reviewer-cli-compatibility) owns the contract for both adapters and loops. Executable equality adds operational friction without demonstrating review quality. Accept unobserved implementation changes; reopen only for a concrete compatibility failure that behavioral checks cannot address. |

Other consequential decisions:

- **D03 — user-directed.** Fresh means a new Codex
  conversation supplied a neutral scope/contract note and no findings/fix/verdict
  history or suppression requests. History means prior review/fix/dispute/verdict
  material about this target, including pre-loop reviews (user-confirmed 2026-09-22),
  wherever stored: job records, session rollouts, repository ledgers or commit messages.
  Current decisions, rationale, evidence, rejected alternatives and reopening conditions
  remain legitimate context; their prior-review narratives remain history. Classify
  passages, including mixed documents, rather than exempting whole decision registers.
  Binding a file does not exempt its content; never omit governing material to obtain
  cold eligibility. If it cannot be supplied without history, use a targeted recheck.
  Keep active loop ledgers outside the candidate tree; unrelated technical history
  and code/tree diffs remain legitimate context. A `git log/show` invocation alone
  proves neither consumption nor breach. This is input discipline, not confinement;
  read assessment/counting govern observed breaches, with unlogged access a limitation.
  Targeted rechecks may use history but never count as clean rounds. Authority:
  [2026-09-14 clarification](../history/phase-1a-audit/README.md#decision-provenance).
  Reopen for a user change or evidence that this discipline cannot hold.
  The [Claude/Opus adapter plan](claude-opus-adapter.md#history-access-and-review-eligibility)
  specifies a per-job access policy for the new single-pass adapter. That policy
  does not replace D03 or read assessment: available history is not evidence of
  consumption, and blocked Git metadata alone does not establish cold eligibility.
- **D04 — user-directed.** Start at the configured model
  and `xhigh`; no silent downgrade. User-directed escalation starts a new sequence.
  Authority: 2026-09-17 correction in the same record. Check current availability.
- **D05 — accepted.** Retain every reviewed snapshot/evidence
  and bind exact versions. Use the flat layout below to preserve existing addressing;
  a nested layout needs a demonstrated requirement and a revised public contract.

The small [resume PoC](../../experiments/1b-poc/README.md) does not establish
universal cache behavior or superior quality; its generalizations are not requirements.

## Binding behavior

### Review identity and scope

Bind each round's target images/projections, base/scope, normative companions,
policy, neutral note, prompt/schema, driver version, model, effort, context,
verification contract and relied-on qualification digests. Retain bytes/hashes.
Repository context is the whole
Git-visible manifest: tracked paths plus untracked paths from
`ls-files --others --exclude-standard`. Ignored paths enter only through declared
companions/dependencies; ignored environments, outputs and nested worktrees are not
implicitly bound. Dependency exclusions cannot subtract repository/companion inputs.
This is a policy bound, not a read restriction; unbound consultation cannot qualify.
Under `-s read-only -C <job>`, the
[repository control](../history/phase-1b-context-control-2026-09-20/control.json)
read beyond the job; the
[sibling-history control](../history/phase-1b-sibling-control-2026-09-20/control.json)
also read beyond the target repository.
No narrower technical read boundary is established; reducing this Git-visible
bound requires one that is explicitly enforced and tested.

**Normative companions** are governing files identified by the task/plan, such as
contracts or ADRs. Before each plan capture, the author/skill reconciles the supplied
list with the plan's named governing authorities and resolves omissions or ambiguity.
Binding establishes identity, not completeness; this remains a judgment step using
the companion interface. All new one-pass and loop preparations accept `--companions-file F`:
a JSON array of `{id, path}` entries with unique nonempty stable IDs; omission records `[]`.
Paths may be repository-relative or absolute (absolute required without a repository).
Freeze IDs, resolved absolute paths, in-repo relative paths, images and bytes/hashes;
the driver does not discover authority by following links. Compare ID/image sets for companion
equivalence, not absolute locations or hashes alone; context records use those IDs too.
Locator changes require a recorded mapping; set/content changes reset credit, and
set changes need scope authorization.
Missing/unreadable companions refuse with `companion-unreadable`, exit 3;
malformed lists/duplicate IDs use `bad-companions`, exit 2. Recapture inherits the set.

**Dependencies** use `--dependencies-file F` on all new preparations/recaptures:
`{id, path, exclude?}` entries for files or directory roots; inherit companion
identity/locator/change rules, with prepare omission `[]` and inherited recapture.
Optional `exclude` is a directory-only array of exact root-relative files/subtrees,
without globs, absolute paths or traversal; omission excludes nothing. Named subtrees
cover later children, not new cache directories elsewhere. Freeze exclusions,
cache policy and deterministic included manifests/images, including ignored files
and directory structure. Parent directory creation/removal is accounted for by
changed descendants; standalone empty-directory changes remain bound changes.
Retain included bytes in hash-keyed storage shared across rounds; image descriptors
alone are insufficient. Record symlinks and declare external referents too.
Malformed declarations or unqualified exclusions use `bad-dependencies` (2);
missing/unreadable inputs or undeclared referents use `dependency-unreadable` (3).

Keep caches in declared dependencies bound by default; run author probes/verification with fresh,
empty per-execution `PYTHONPYCACHEPREFIX` and `NUMBA_CACHE_DIR` directories outside
bound roots. Require matching `cache-execution` qualification for the runtime and
launch settings, including descendants; commands overriding/ignoring redirects
are unsupported until separately qualified. These controls limit tested cache writes,
not all filesystem changes. Any uncontrolled bound write still pauses applicability
and resets credit at recapture; external writers remain a limitation.

Exclusions additionally require `cache-exclusion` qualification preventing reads of
pre-existing excluded bytes by reviewer/probe/verification processes; regeneration
must use bound inputs. Source, native dependencies and sourceless bytecode stay
bound. Direct reviewer consultation of excluded files disqualifies.
Declare consulted package/runtime files and interpreter targets; versions/lockfiles
are audit metadata. Recheck included content at repository applicability boundaries.
Set/exclusion changes require authorized recapture and reset; no retroactive credit.
This input contract does not claim hermetic host execution.

A qualifying reviewer starts in its current round workspace with D03's supplied
inputs. A rebuttal resumes that same round's original thread and snapshot;
corrections are reviewed cold in a new round. This avoids resuming a thread against
a different round's relative paths.

Refuse file-group splitting except through
[the skill's unrelated-work exception](../../SKILL.md#1-prepare--no-tokens-spent).
Each loop has one agreed target, reviewed in full on the current candidate by
qualifying rounds. Targets separated under that rule have independent loops,
budgets and streaks; convergence of separate targets does not establish convergence
over their union. Their scope notes follow D03.
`--base <last-reviewed>` may support a targeted fix check but cannot silently
replace that scope. Increment 1 adds a **new combined code capture**, through
`projections.py` and `main.prepare_code`: post-base committed,
staged, working-tree and untracked projections remain distinct, including repeated
paths. Its audit digest covers the round's resolved base OID, `head_oid`, capture
mode, any expected-path assertion hash, canonical projection/path entries and
complete before/after images (kind, mode, content).
Use `prepare code --loop --base B --head H`; uncommitted fixes are visible without committing,
and staged defects cannot hide behind worktree bytes. One-pass capture keeps its
two modes; increment 0 rejects `--base` plus `--uncommitted` (exit 2).
Loop code capture rejects `--uncommitted`.
Prepare exposes resolved base/head, paths/projections, file count and before/after
byte totals. Synthetic comparison commits are supported; `--base` need not be an
ancestor of HEAD. Retain every comparison tree/OID and the original agreed scope.
On recapture, scoped paths with no remaining difference stay as `retained` artifacts:
identical current before/after images, including absence after removing a new file.
This permits re-examination after partial or complete reverts; an initially empty
capture still refuses. An authorized path-set replacement governs the retained scope.

Streak equivalence is separate from audit identity: compare scope, path assertion,
capture mode, projection entries/images, companions/dependencies/context, policy,
note, review instructions, schema, driver/model, qualification keys/validity and
the verification contract below.
Exclude base/head OIDs; normalize only driver-generated round locators in review
instructions, retaining raw inputs for audit. Effort follows D01/D04. After recorded drift resolution,
a rebuilt base with matching inputs needs no new scope authorization and retains credit;
record the comparison and both base/head OIDs. A metadata-only rebuild can qualify.
An unrelated-file commit followed by a rebuilt base still resets unrestricted-context
loops, even with identical projections; this concession does not solve that workflow.
Other rebuilds retain credit only with an enforced unchanged context boundary.
Committing a fix changes its projection kind and resets credit even with identical
file bytes; 1b does not normalize that distinction. Changed content/context resets;
unresolved streak equivalence follows the recovery table.

Initial code-loop prepare requires `--base B --head H`, with a full head OID agreed
for the candidate. Recapture may omit `--head` to inherit the recorded pin; a supplied
value must match it unless an `authorized-commit` or `drift-resolution` decision binds
the replacement, old/new OIDs and scope/attribution evidence. Neither grants commit
or scope authority. Never silently re-pin to live HEAD. Compare the effective pin
to live HEAD before freezing (`head-moved`, exit 3); reuse `confirm_correspondence`
for movement during capture (`capture-race`, exit 4). Neither publishes a round;
recapture refusals pause the existing loop.

Optional `--expect-paths F` asserts the complete captured repository-relative path
set (JSON string array), never filters it. Freeze its bytes/hash; missing or extra
paths refuse publication (`paths-mismatch`, exit 3). Recapture retains the assertion;
changing it requires a recorded authorized scope update. Repeated paths retain distinct projections.

An old report describes its frozen snapshot; it cannot authorize probing changed
context, applying stale findings or approving new bytes. Check applicability at
those boundaries, after review and verification. Unexplained movement pauses;
no new baseline silently adopts it.

### Reviewer CLI compatibility

D10 applies to both reviewer adapters and loops. Resolve the executable to launch
and retain its path in run metadata (`codex_path` for Codex) as provenance.
Do not query its version, hash or pin its binary, or compare CLI identity before
and after a run. CLI path/version/hash are excluded from round equivalence and
qualification keys. An upgrade alone cannot invalidate qualification, discard a
review, reset credit or require recapture. Version text already present in raw logs
may remain there; new metadata omits dedicated CLI version fields. Historical
records remain readable. Remove the existing Codex version query when implementing
this contract; this planning revision does not change the running driver.

Keep response validation and the required launch, permission and authentication
checks. Qualify configured capabilities; investigate observed incompatibility and
recheck affected controls when relevant launch settings or contracts change.
Demonstrated contract violations follow the existing failure/disqualification rules.
This policy accepts that internal CLI behavior changes may go unnoticed.

### Findings, corrections and rebuttals

**Plan-review judgment** belongs in `rubric.py`: distinguish binding requirements
from indicative design and TDD-discoverable details; require a concrete requirement
before treating the latter as binding. Reopening a settled decision needs new
evidence, an authority conflict, an implementation blocker or a newly discovered
material consequence. A register ID or repetition cannot dismiss a valid finding:
test the rationale against the actual objection. Request the minimum remedy.
Reconsider revised requirements across affected plan sections and governing
companions, including superseded wording.

Preserve sealed evidence, digests, declared fix paths and verification. Apply only
accepted introduced findings within authorized scope, through host permissions
and TDD. Honor `--no-apply`; failed/denied/incomplete operations never count as clean.

Disqualification removes clean credit, not finding validity. Independently probe
and fix such findings under the same authorization, applicability, scope and
verification rules; processing them never restores that round's credit.

Loop annotations require a rationale, including acceptance. Accepted claims state
what the probe would show if false, and evidence that the intended path ran. Where feasible,
retain discriminating pre/post-fix runs of the sealed probe; otherwise state the
limitation. Sealing establishes integrity, not validity. Each apply result records
a sibling-defect sweep: searched scope, related sites and check evidence, or why
none applies. A sweep does not authorize additional edits.

Before touching newly discovered fix paths, `amend-paths` records additions per
finding and their rationale within authorized scope. Check ownership, generation
and unchanged added paths against the original pre-apply baseline; preserve that
baseline and prior declarations. Ignored paths already present in its bound-input
images remain eligible. Retrospective authorization is refused; edits
outside all declared paths remain terminal exit 10.

New jobs require exactly one immutable verification intent (otherwise exit 2):
`--verify CMD` configures a command; `--no-verify --reason R` declines verification;
loop-only `--defer-verify --reason R` defers command selection. Declined jobs cannot
select a command later; their verification result remains `not-run-explicitly`,
subject to existing scope-failure precedence. Unresolved deferred intent blocks
fixed closure and clean credit; earlier reviews gain no retroactive credit.

The **verification contract** binds intent, command bytes, execution cwd and cache
policy for the loop. Freeze cache policy at prepare; `configured` freezes command/cwd
then, while `author-selected` freezes them once through `continue --decision-file F`,
citing a command sealed in the current round. Append that resolution without rewriting intent or earlier
rounds. All subsequent rounds inherit it; reject replacement in place. A changed
contract requires a new loop under existing authorization rules.
Record a repo-relative cwd alongside its absolute execution path when possible.
The active cwd remains frozen: 1b does not relocate a live loop to another checkout.
Portable companion identity does not authorize rebinding cwd, ownership or sessions.
Sealed probe executions inherit that cache policy. Record effective cache settings and
exact scratch locators with every execution; compare the fixed allocation policy, not
fresh directory names. Only driver-allocated empty cache locators may be normalized.
Seal before execution, bind each result to post-fix identity, and run after
apply-scope validation. Preserve output and derive pass/fail from sealed return
code. Unbound logs and skipped verification cannot establish fixed closure.

The **driver** maps reviewer-local IDs to immutable `(round, local_id)` references;
the cold reviewer is supplied no round number or history. Rebuttals translate back to
that thread's local IDs; new ones must be unused there. Author/arbiter verification
links recurring issues across rounds. Fixed closure requires a relevant captured
change, verification and reviewer re-examination; repetition, unrelated green
tests or exhaustion never suffice.
The fix round must be at or after the finding, no later than verification, and
strictly before cold re-examination; verification may belong to that re-examination round.
Declared companion/dependency fixes require matching captured and verified post-fix
images; absence counts only inside a captured directory outside exclusions.
A qualifying cold round can supply re-examination and streak credit;
a separate targeted fix-check call is optional.

`defer` is a fourth **author-side disposition** for introduced findings, with a
rationale, no fix paths and `needs-user-decision`. Reviewer classification remains
`introduced|pre-existing`. Adjudication distinguishes a decision needed before
implementation, a constraint to preserve and deferrable detail; none silently
waives a finding or authorizes a fix. Pre-existing scope questions stay separate;
probe revisions preserve recorded user scope decisions.

Send disputed claims, sealed evidence and rationale through **one launched rebuttal
call per originating thread**, even if it yields no judgment. No launched rebuttal
retry is allowed; an unresolved dispute goes to handback. Cold/fix-check retries
remain available. The reviewer withdraws or maintains claims; unknown IDs or altered
classification are contract errors. New findings need new IDs and probing.

Retain rebuttal answers even when disputed. New actionable evidence permits
investigation; recurring blocking disagreement without it requires user handback.
Inconclusive findings, deferred decisions and unresolved scope questions also block.
Failed resumption is not withdrawal.

Cross-round probe assessment normally requires equivalent inputs. For an unchanged
finding artifact and projection scope, `finding-resolution.applicability` may instead
list the exact sorted `changed_paths`, a rationale and sealed `evidence_ids` showing
why every changed path is irrelevant to the original claim. Other equivalence inputs
still match; this judgment never grants streak equivalence or refutes corrected bytes.

### Counting and stopping

An eligible clean round meets the [scope contract](#review-identity-and-scope), follows
D03, has a `no-observed-breach` assessment, settles every probe and has no accepted
findings or blockers.
Evidence-refuted findings allow it; pre-existing issues are surfaced separately and
unresolved scope decisions block.

Any accepted finding (including typos/`minor`), completed round with unresolved blockers,
transition discarding an uncounted judgment-bearing round, change to equivalence inputs, or
disqualification/retirement of a qualifying attempt under the recovery table resets
the streak.
This conservatively requires an uninterrupted qualified sequence; earlier round
records remain valid. Read disqualification concerns judgment-bearing attempts;
confirmed no-judgment attempts use the exemption below.
Targeted fix checks/rebuttals never advance it. D01's
confirmation is the sole effort-transition exception; other model/effort changes
start a new sequence under D04. Preserve separate reviewer/adjudication/loop outcomes
and reported counts/dispositions: zero findings differs from none accepted even
when both qualify as clean (T-79).

One cold round costs one reviewer call; D01 needs three calls with no defects.
With `D` an accepted-defect round, `C` a clean xhigh round and `M` a clean max
check, `D C D C C M` needs six; one judgment-bearing rebuttal and one retry
make eight launches: seven judgments and one no-judgment attempt.

Every launched cold, fix-check, rebuttal or retry consumes the total-launch budget.
A validated report consumes the judgment allowance even if its findings are later
refuted; confirmed absence of a valid report consumes the no-judgment allowance.
Reserve capacity conservatively in both outcome allowances until classified; unknown
outcomes block another launch and remain charged to the total. Retired unknown
outcomes consume the no-judgment allowance without asserting no work occurred.
Reclassification never refunds a launch. Confirmed no-judgment attempts
need no read assessment; empty/partial logs add no assessment pause. They neither
advance nor reset the streak for their read activity, even known history reads;
changed equivalence inputs still reset. Each retry starts a new cold thread;
unobserved carryover is accepted under D09. Failed launch qualification blocks
further clean credit. Retain failed logs without claiming absent reads.
Reserve before launch; cancel only with proof no process launched. Resolve uncertain
call completion through the recovery table; it stays charged.
Exhaustion with unfinished work or a final-call accepted finding requires a user
decision; only an explicit recorded extension increases the budget. Completion
means meeting the review policy, not proof of defect-free code.
An availability response with `retry_after` pauses until that time; no automatic
early retry. Exit 7 alone does not prove that no model work or cost occurred.

### State, isolation and recovery

Separate round and loop completion. Persist outcomes, evidence, attempts, session
identities, budget and streak for interruption recovery. Never replay completed
edits/records/calls; expose uncertain call completion. Loop operations leave legacy
job records byte-identical and never reopen terminal jobs.

Use generation checks and short locks, never a state mutex over a model call.
D02 ownership spans the editing interval and interrupted turns until explicitly
released/resolved; a per-command mutex is insufficient. Refuse conflicting owners
of the same worktree; detect external edits without claiming prevention.

**Qualification records** are immutable `<state>/qualifications/<sha256>.json`.
`record-qualification JOB --qualification-file F` accepts a staged schema-v1 record:
`capability` (`cold-launch`, `resume`, `cache-execution` or `cache-exclusion`),
`key`, `controls[]`, `rationale` and `supersedes` (prior digest or null).
Controls name the driver-supported suite/digest and required case IDs, outcomes
(`pass|fail|unavailable`) and sealed command/output/return-code evidence. Bootstrap
with authorized host-run isolated controls, sealed in a new one-pass job without
exclusions; control collection is outside review gates/counting and earns no clean
credit. Recording grants no execution authority. Under a short lock,
compare-and-swap the prior digest and append; never replace evidence in place.

All keys bind host identity/OS/architecture, driver/control-suite digest, capability scope and
effective non-secret settings hash. `cold-launch`/`resume` add reviewer provider,
execution home, sampled `AGENTS.md`/`AGENTS.override.md` identities there (also bound
in round audit/equivalence), and relevant declared runtime fingerprints; `cache-execution` adds author
runtime/dependency fingerprints and cache policy. Author execution requires both runtime
controls and a command profile adding the sealed command SHA-256; changed command bytes
require a matching profile, with cited controls covering its interpreter flags, overrides
and descendants. Reuse evidence only with an assessment establishing that applicability.
D10 excludes the reviewer executable
path/version/hash from all keys, including settings and runtime fingerprints.
`cache-exclusion` combines those reviewer/author fields with its root/exclusion set.
Transfer disposable-home configuration controls only with a cited mapping proving
only home locators differ; authenticate in each execution home. Normalize only
driver-generated job/round/cache locators, retaining exact expansions; user paths,
settings and execution homes remain exact. No automatic auth/host/runtime transfer.
The driver checks schema, evidence integrity, complete passing cases and current keys;
cited assessment owns whether controls exercised the claim. Stale means superseded,
key-mismatched or evidence-invalid, with no time-based expiry.

Loop rounds require `cold-launch`, `resume` when rebutting, and applicable cache
capabilities. New one-pass Codex jobs require only `cache-exclusion` for declared
exclusions and `cache-execution` for author probes/verification while retaining
the ephemeral, config/rule-ignoring launch profile. This grants neither loop credit
nor memory-isolation assurance; other adapters retain their own launch gates. Key the launch
profile in capability scope; ephemeral controls cannot qualify persistent launches.
Check required capabilities at exclusion capture, before each dependent execution
and before loop clean credit. Retain the original qualification references supporting
every credited round and recheck them until convergence; a newer passing record cannot
replace the evidence on which earlier credit relied.
Missing/stale/failed evidence blocks the dependent action: unqualified exclusions
at prepare/recapture are `bad-dependencies` (2); runtime capability gaps use exit 18 with
reason `qualification-required`, under the shared handback envelope below. Changed keys
or later failed supersession reset affected streaks; prior records remain. Matching-key requalification
can clear a pause but cannot restore forfeited credit. Changed keys require authorized
recapture and matching qualification; immutable verification-contract changes still
require a new loop. Status exposes required capabilities, selected digests/keys,
validity and mismatch reasons.

After every valid cold or resumed loop judgment, **read assessment** through `continue --decision-file`
precedes probes, rebuttal and apply. Bind `attempt` (omission selects the latest judgment),
`report_sha256` for cold or `adjudication_sha256` for resumed judgments, and that
attempt's `command_execution` events,
retaining raw log hash/event references. The author/arbiter submits a disposition
from the recovery table with rationale and cited events/sealed evidence. Assessments
are append-only: resolution cites the prior assessment digest and expected generation;
retain all versions, with the latest valid assessment governing the gate. Evidence
collection/sealing remains allowed while paused. Recheck report/log digests and event
coverage before dependent finding work and credit; stale current bindings require
reassessment, and lost bindings for earlier credited rounds reset their accumulated credit.
An explicit `log_sha256: null` binds an unavailable log; it cannot support
`no-observed-breach`, but permits the recovery table's disqualified finding workflow.
No later assessment restores
forfeited credit or replays completed work. Bound context is permissible subject
to D03. Resolved reads mean permitted bound context or established non-consultation
(such as a listing/failed read), not merely an identified path. Shell strings are not
file-access traces: scripts, symlinks and unlogged access limit assessment.
`no-observed-breach` attests inspected evidence, not confinement.

Use `continue` for the following resolutions; each binds the current checkpoint
and evidence. Read disqualification resets qualifying judgments; retirement resets
qualifying attempts with unknown outcomes. Intentional history in targeted checks/
rebuttals does not reset credit; that exemption still requires complete logs and event coverage.
This table owns recovery, not new authorization:

| Pause / outcome | Resolver and decision kind | Credit / finding effect |
|---|---|---|
| Missing/stale assessment, `read-unresolved` or incomplete judgment log | Author/arbiter appends `read-assessment`: `no-observed-breach` only with complete judgment logs and resolved reads; `history-consumed` for established history use; `read-unresolved` keeps the pause. | The first two release finding work; history consumption disqualifies/resets a would-be qualifying round. |
| Established unbound consultation or reads/logs that cannot be settled | Author/arbiter appends `read-assessment: read-unverified`, citing the unbound input or evidence gap. | Disqualify/reset; release independent finding work without asserting history consumption. Declare new context at authorized recapture for later rounds only; no retroactive credit. |
| Missing/stale/failed qualification | Collect/seal controls and append through `record-qualification`; `continue` rechecks required capabilities. Changed keys require authorized recapture. | No dependent execution/clean credit; reset and recovery follow the qualification contract. No slot is charged without a launch. |
| Uncertain call completion | `resolve-call` reconciles durable process/log/result evidence. A matching live child must finish or be safely stopped before another launch. PID absence alone proves no outcome. | Remains charged; a validated judgment enters assessment, confirmed no-judgment uses its existing exemption. |
| Call outcome irrecoverable after child is quiescent | Explicit user `retire-attempt` records **outcome unknown, no usable judgment**. | Charged; qualifying cold attempts reset credit, targeted rebuttals preserve it. Permit another otherwise allowed call. Late evidence stays attributable and cannot restore credit; valid findings still require independent checking. |
| Unresolved equivalence or candidate drift | `drift-resolution` records proven equivalence, or an authorized recapture with reset; unresolved evidence keeps the pause. `authorized-commit` binds an already authorized candidate transition. | Matching inputs retain credit; changed/unproven inputs reset. Neither waives applicability, ownership or scope checks. |
| Failed/denied verification without finding blockers | `verification-incomplete` advertises `drift-resolution` for a cited authorized recapture. | Reset credit; retain the terminal failure and retry only in a new round. |
| Availability deadline, exhausted budget or unresolved findings/authority | Respect `retry_after`; only explicit user `budget-extension` adds slots. Finding/scope decisions follow existing authorization. | No advance or refund; unresolved decisions still block closure. |

**Increment 2 streams per-attempt stdout/stderr to durable logs during invocation**,
retaining complete JSONL events across driver death. Persist launch identity (host,
child PID/start time, cwd/options, log/result paths) and completion evidence; a
crash before identity publication remains uncertain. Bind `thread.started.thread_id`
from that attempt's stdout log as it arrives, binding round/cwd/read-only policy; never
borrow another attempt's ID. Recovery must distinguish live children, validated
completion and unknown outcomes, including PID reuse and late completion; process identity alone cannot establish a judgment.

Persistent cold loop launches omit `--ephemeral`. Resolve and record `CODEX_HOME`
and its session store under D09; rollouts remain history, not implicit dependencies.
Bind tested auth, instruction and permission settings to the production launch;
a home-specific auth result does not establish another home’s authentication.
With no valid thread ID, record non-resumability and refuse
rebuttal before launch; an emitted ID alone does not prove persistence. Re-prove
`--ignore-user-config`/`--ignore-rules` and bound write permissions. Bind invocation
options to the tested capability controls.

Increment 3 depends on those records; never use `--last`. Check installed resume
options and explicit read-only overrides. Enable rebuttal only after controls prove
cwd, repository-write denial and scoped persistence writes; accepted flags prove
none of these. One-pass launches retain their current behavior.

The restricted adjudicator receives explicit policy-as-data and evidence, not
repository instructions as authority; hostile-instruction controls must prove
that boundary. A subagent does not prevent author scope escapes, observe
ignored/external paths or make same-user state tamper-proof.

### Skill–driver interface

Keep one-pass verbs; new preparations enforce intent/D07, companions/dependencies
and the qualification scope above. Existing jobs gain no inferred sets. Loop requests
require explicit round handles, never an ambient “latest”.
Reject any option the verb/mode cannot honor with exit 2, including capture options
on `status`; a globally recognized option is not permission to ignore it.
`seal` and job/round payload paths resolve against that job/round, never cwd;
payloads must stay under its `staging/` (the T-48 channel). Payload errors name the
field and expected shape/allowed values in `error.details`; document complete
recording payloads in `SKILL.md`.

Loops and rounds occupy separate `<state>/<nonce>/` directories; typed loop
records index their round nonces. `LOOP` accepts a nonce or direct-child absolute
path; `--round R` accepts a member nonce. One shared resolver normalizes once,
checks containment, record type and membership, and rejects nested paths,
traversal, symlink escapes, reserved/staging names and unknown IDs. Listing uses
that resolver: bare `status` reports open legacy jobs and open loops with current
round/next verb, including loops whose last round is terminal. Group member rounds
under their loop; refuse access to them through one-pass verbs without loop context.

| Request | Contract |
|---|---|
| `prepare plan P [--loop]` / `prepare code [--loop]` | All new preparations accept `--companions-file F`, `--dependencies-file F` and one applicable verification intent. Code loops require `--base B --head H` and accept `--expect-paths F`; capture semantics are defined above. |
| Existing `run`, `seal`, `record-probes`, `record-apply`, `record-verification` with `LOOP --round R` | Operate on that round using existing payload/evidence rules and qualification/cache-policy checks. A valid `run` judgment routes to `continue` for assessment before finding work. |
| `record-qualification JOB --qualification-file F` | Validate staged schema-v1 control evidence, append to the qualification registry and return its digest; registry entries are not job/round handles. |
| `rebut LOOP --round R --rebuttal-file F` | Validate staged claim/evidence/rationale input and make the one permitted resumed launch for that originating thread. |
| `amend-paths LOOP --round R --amendment-file F` | Append a scope amendment before editing added paths, subject to the checks above. |
| `recapture LOOP [--base B] [--head H] [--expect-paths F] [--companions-file F] [--dependencies-file F]` | Freeze the next round after completion or drift resolution. Inherit the recorded pin, context sets and verification contract; replacements follow binding/authorization rules above. Plan loops reject code-capture options; unresolved decisions block recapture. |
| `status LOOP` / `abandon LOOP --reason R` | Inspect loop/current-round state / close the loop while retaining all round records. |
| `continue LOOP [--decision-file F]` | Validate the checkpoint and staged, version-bound decision (recovery-table decisions, authorized scope update, finding disposition or deferred-command selection); append it and return the next verb. The skill dispatches it; continuation invents no authority and launches no reviewer itself. |

Loop envelopes use schema version 2 with the existing `verb`, `driver_version`,
`exit`, job identity and `error`/`payload` shape; one-pass envelopes remain version 1.
`status.payload` exposes round, generation, target/manifest digests, next verb,
calls consumed/remaining, clean streak, bound context-set IDs/digests, latest read
assessment, qualification requirements/records/validity, verification intent/selection, findings/dispositions, pending decisions
and pause/resolver kinds.
Partition charged attempts into judgment, unavailable without judgment, other
no-judgment, pending/uncertain completion and retired unknown outcome. Credit eligibility
is separate; reclassification never changes charges or restores credit. Budget handbacks carry
structured causes: accepted defects, disputes, unavailability, or their combination;
a trend is not a convergence estimate.
Loop mutations after prepare require `--expected-generation G` and bind consumed
record digests; refuse stale submissions and never replay completed effects.
Preserve [historical §12](../history/codex-review-plan-2026-09-17.md#12-exit-codes-and-precedence):
use 9 for an inside-repo state root and 16 for absent, unknown or conflicting apply
ownership. Under D02, retire 15/17 without reusing them. Other refusal codes stay unchanged.
Specific refusals take precedence; otherwise loop/qualification handback uses 18 with
`needs-user-decision`, a reason and no next verb.

## Implementation increments

Use an integration red phase where feasible, then one unit condition at a time:
red–green–refactor, `should_*`, given/when/then. Derive expectations from this
contract/audit, not the new implementation's outputs.

| Increment | Observable result | Existing integration points |
|---|---|---|
| 0 — close inherited safety gaps | Refuse inapplicable options (exit 2), conflicting capture flags, state inside the target repo, missing verification intent, out-of-channel payloads and malformed responses; stop stale applicability/conflicting ownership. Add plan-review judgment/companion preflight, the qualification registry and new one-pass context/cache checks, preserving the ephemeral launch without loop-only gates; remove explicit CLI version collection under D10; apply D07 and explain payload errors. Implement D02; distinguish intended index fixes from drift. | `main.py`, `state.py`, `jobs.py`, `response.py`, `probes.py`, `apply.py`, `rubric.py`, `SKILL.md`, capture/lifecycle tests |
| 1 — durable loop and combined capture | Publish separate flat nonce directories for loops/rounds with indexed membership; shared resolution makes open loops discoverable by bare status. Implement continuation, pinned recapture, declared context sets, path assertions, audit identity and context-bound equivalence; retain immutable rounds and legacy jobs. Interrupted publication or duplicate transitions cannot create a clean round. | `projections.py`, `main.py` argument handling, `stage.py`, `state.py`, `jobs.py` |
| 2 — cold correction loop and persistence | Stream durable attempt logs/identity and retain timing diagnostics under Handoff; recover interrupted calls and assessments through the table. Qualify persistent cold sessions, including authentication, instruction and write controls. Reviews see uncommitted fixes, apply D03/D01/D06 and preserve scope; record probe controls, sibling sweeps, scope amendments and immutable verification contracts with deferred selection. | `reviewer.py`, `rubric.py`, `probes.py`, `apply.py`, `SKILL.md` |
| 3 — disputes and restricted adjudication | Depends on increment 2's persistence and instruction/write controls. Validate resumed write denial, local-ID mapping, new findings, author-side defer and stalled-dispute handback. | Response/probe validation, reviewer invocation, skill orchestration |
| 4 — qualification and handoff | Real host sessions drive the whole loop; permission denial, retries and interruptions produce honest durable outcomes. Reconcile documentation and produce a version-bound handoff. | `tests/l2/`, live fixtures, skill and plan documentation |

Internal decomposition and event-log versus immutable-transition storage remain
open to TDD; the public interface and durable-state guarantees do not.

## Acceptance and release evidence

Run the existing offline suite plus direct tests of changed primitives. Add
controlled integration scenarios covering:

1. An accepted defect → verified fix → cold complete-scope review that sees it.
   Test distinct repeated projection hashes and commit-induced resets; conflicting/
   inapplicable options, missing/extra paths and cwd-independent payload resolution;
   initial/inherited/replacement head pins, movement during capture, synthetic
   non-ancestor bases and different OID digests despite identical images. Metadata-only
   rebuilt bases may retain credit; unrelated context changes reset, unbound context
   cannot qualify. Capture companion/dependency sets in one-pass and loop modes;
   exercise malformed/unreadable refusals, symlink referents, relocation, set/content changes and frozen cwd.
   Undeclared ignored-file changes preserve the repository manifest; consultation disqualifies.
   Declared ignored inputs and tracked paths matching ignore rules remain bound.
   A package-source mutation
   with unchanged lock/version metadata must reset. Fresh redirected author executions
   leave bound caches unchanged, ignore seeded stale caches and retain equivalence
   despite different scratch locators. Exercise overrides/descendants and new cache
   directories outside named exclusions; uncontrolled bound changes reset. Controlled
   excluded-cache changes retain equivalence, but direct excluded reads disqualify. Check
   retained-byte retrieval and exclusion-policy resets. Former default caps disappear;
   explicit limits and scope/size summaries still match the captured bytes.
2. Clean/rejected/accepted-minor/inconclusive sequences exercise every counting
   branch: clean `xhigh` → `max` confirmation, accepted-defect confirmation followed
   by three clean `max` rounds, another defect resetting that continuation, and a
   sequence begun at `max`. Include independent targets, attempted credit for their
   union, policy/context drift and user-directed escalation.
   A judgment-bearing qualifying round consuming history loses accumulated credit;
   intentional history use in a targeted recheck/rebuttal does not itself reset it.
3. Withdrawn, maintained and new rebuttal findings; unknown IDs, changed
   classification, unsupported `confirmed-fixed`, final-slot findings and stalled
   disagreement or `defer` cannot close as success; namespace translation preserves
   IDs without supplying round history to a cold reviewer. Missing-ID rebuttal
   refuses without a launch; a launched no-judgment rebuttal stays charged and
   cannot retry. Empty-log attempts without IDs remain non-resumable.
4. Crash before/after reservation, launch identity, first streamed event, response
   publication, recording, verification and recapture. Killing the driver retains
   already-durable events/ID; exercise surviving children, PID reuse, late completion
   and both `resolve-call`/`retire-attempt` paths. Duplicate continuation, simultaneous
   writers and stale generations cannot replay effects or replenish budget. Counters
   partition charged calls, including retired unknown outcomes; honor `retry_after`.
   Confirmed no-judgment needs no assessment and does not reset for reads, including
   known history; drift still resets, and unknown completion follows recovery.
   Exercise every recovery-table exit/reset, including unresolved equivalence.
   Timing controls cover busy/idle/I/O waits, unrelated-child CPU contamination,
   suspended wall-clock intervals, failed calls and unsupported/interrupted accounting;
   missing CPU data stays unknown, never zero or inferred remote work.
   Mixed legacy/loop listings (including terminal members) and member/address
   refusals share rules: every listed handle resolves in its required context.
5. Host-instruction injection, repository/sibling reach and synthetic session-store
   reads test persistent cold/resumed instruction boundaries, auth and scoped writes.
   Memory behavior is not a qualification gate under D09. Successful outside
   reads establish limits, not confinement. Identical same-target history, including
   pre-loop reviews, in a bound repo file, commit narrative or
   rollout disqualifies; code-only Git inspection is the negative control. Assess
   before finding work; missing/stale assessment, incomplete logs and unresolved
   indirect reads pause. Appended resolution clears that pause and preserves prior
   assessments; exercise established unbound consultation and `read-unverified`
   release, independent fixes, permanent disqualification and stale
   digest/generation refusal. Include sealed execution and edit/verification denial.
   Controls must reach the workflow; record host versions/permission cells and leave
   unavailable cells unvalidated, never passing skips.
   Exercise qualification bootstrap, missing/tampered evidence, failed/unavailable
   cases, stale supersession and status reasons; relevant settings/runtime/host/home
   changes block execution and reset credit until qualified recapture.
   Test disposable-home mappings and home-specific
   auth, capability-specific keys and the prepare-2/runtime-18 split;
   requalification never retroactively restores forfeited credit.
   An ephemeral one-pass Codex launch needs no `cold-launch`/`resume` record;
   its exclusions and author executions still require applicable cache qualification.
   Missing loop launch qualification blocks; ephemeral evidence cannot satisfy it.
   Under D10, a compatible replacement executable preserves qualification and credit.
   Check launch-path provenance with fake CLIs receiving no version query;
   missing version metadata must not prevent review or legacy-record reads.
   A malformed response or demonstrated permission/auth failure still follows its own
   refusal/disqualification rule.
6. Refuted findings can be clean; dead-path probes cannot justify acceptance.
   Exercise sweeps and pre-edit amendments versus retrospective widening.
   Missing/conflicting intents and one-pass deferral refuse; declined jobs reject
   later selection. Deferred selection blocks closure/credit until resolved once;
   recapture inherits command/cwd, substitution and stale/uncited evidence refuse,
   and selection cannot retroactively qualify earlier reviews.
7. Plan-review fixtures exercise D03 with rationale alone and mixed rationale/history;
   check read eligibility and independently verify findings/dispositions. Unsupported
   reopening and unforced implementation detail produce no accepted defect;
   evidence-backed challenges survive register-ID dismissal. Revised requirements
   with stale wording or conflicting companions yield the corresponding actionable
   findings. An omitted named governing companion is caught at the author/skill
   preflight. Extend the existing suite; capture/report success alone is insufficient.

Every unresolved T-01–T-108/E-1–E-5 row in the
[inventory](../history/phase-1a-audit/acceptance-inventory.json) needs a direct check
or explicit justified retirement before combined 1a/1b completion. Neither the
audited suite, two L2 cells nor dogfooding replace the missing live matrix/E cases.

When integrating with the **quant-factory** repository, use its repo-relative
`docs/plans/improve-native-planning.md#phase-2--review-skill-compatibility`:
exact version and companions, evidence-backed rebuttals,
revision consistency, stalled-dispute handback, and native authorization. This is
that project's **planning Phase 2**, distinct from the reviewer's deferred gate.
One-pass qualification requires increment 0's companion support; existing jobs do not supply it.

## Handoff

Handoff the reviewed manifest, policy, result, open decisions and validation limits.
Report token usage (input/cached/output/reasoning) by model/effort, reviewer wall time,
author/probe/verification durations and quota waits separately; unavailable measurements
stay unknown. Increment 2 records timing for new one-pass and loop invocations,
including failed/interrupted attempts: retain `elapsed_seconds` as wall-clock time
(including suspension/waits), alongside local child user/system CPU seconds,
measurement method/process scope and completeness. Use per-child accounting, or
aggregate deltas only with proven exclusive attribution; unsupported/lost measurements
stay unknown. CPU and wall-minus-CPU measure neither remote inference nor sleep.
Attribute sleep or other waits only from independent evidence; preserve legacy records.
The [timing evidence and controls](../history/phase-1b-duration-evidence-2026-09-25.json)
bound these claims. The reserve A/B does not establish Astra cost or recall; see the
[checked usage evidence](../evidence/phase-1b-usage-evidence-2026-09-18.json).
Review completion grants no implementation/commit/deployment/risk authority;
user instructions govern, without a new approval lifecycle.
