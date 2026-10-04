# Shared review core with a Claude/Opus adapter — revision 6

Revised from revision 5 (`47dfd06`) on 2026-09-23 to remove explicit reviewer CLI
version collection and binary identity requirements under the user's revised D10.
This is the canonical plan for the adapter work; reviewer adapter implementation
remains deferred.
The running workflow remains defined by [SKILL.md](../../SKILL.md).

## Outcome and scope

Let Codex request an independent Claude Code review using Opus at `high`, through
a sibling `claude-review` skill and the existing review lifecycle. Keep one core
for capture, policy, prompting, response validation, evidence and host adjudication;
put CLI invocation and provider-specific behavior behind reviewer adapters.

Deliver the adapter and single-pass workflow first. Autonomous rounds, convergence,
rebuttal/resumption and escalation remain in the [Phase 1b proposal](phase-1b.md).
Its approved decisions are owned there. This plan grants no cold-round credit
to single-pass jobs and does not implement a loop.

The initial files-only proposal has been expanded by the user's later request for
reviewer-controlled Git inspection. Supply raw files, captured changes and, when
allowed, reviewer-selected Git queries. Host context identifies scope and contracts;
it must not substitute a narrative of prior findings or fixes for primary evidence.
Git commit messages can themselves contain such narratives.

The reviewer does not apply changes. The host retains finding probes, adjudication,
authorized edits and verification. Preserve `--no-apply` and existing permission
boundaries; installing a skill does not grant additional execution authority.

## Evidence and implementation prerequisite

The archive-naming repair is implemented: both recorded policy inputs now have
inert `.txt` names, with unchanged bytes and original-input hashes and updated
archive locators. The nested-policy guard is unchanged. The
[archive convention and regression checks](../README.md#archived-policy-inputs)
own the naming and verification requirements, including Git-index coverage.
This prerequisite repair does not implement the reviewer adapters below.

Historical Claude/Haiku observations reported a job-write hole, a later job deny,
and a Git-metadata read refusal. The original sandbox traces were not recovered;
none establishes current enforcement. The [authentication diagnostic](../evidence/claude-auth/README.md)
retains a separate, bounded status/request observation and portable probe. Its
private sealed-selector archive is not required public context and does not satisfy
the missing sandbox-probe gate.

Re-run the missing probes from Codex with the explicit child-environment allowlist,
and commit reproducible fixtures before implementation relies on those observations
and before final Opus acceptance. Record complete argv/settings,
OS, parent host, environment names, raw non-secret events and independent filesystem
checks. Earlier inherited Claude-host state is not qualification of this launch.
The bounded probe gate in [delivery](#delivery-and-acceptance) owns this requirement.
Opus behavior under the final controls, command permissions, linked-worktree
protections, malformed-settings handling and usage-limit envelopes remain unqualified.

## Binding architecture and interface

- The shared core owns lifecycle, captured scope, synthesized policy bytes, rubrics,
  schemas, evidence integrity and eligibility rules. Drivers own binary resolution,
  argv/environment, policy delivery, process execution, provider envelopes, tool-event
  normalization and provider-specific provenance. Host judgment remains outside both.
- Extract the existing Codex behavior unchanged behind this boundary before adding
  Claude. Keep existing job addressing and `~/.codex-review` / `CODEX_REVIEW_STATE`;
  show provider and access policy in status and projections.
- Parse `prepare --reviewer codex|claude` before resolving any binary. Omission and
  legacy jobs mean Codex. Support `CLAUDE_BIN`. Freeze provider at preparation;
  `run --model/--effort` cannot change it. Refuse unsupported provider-specific
  options with usage exit 2; let the selected CLI resolve model identifiers instead
  of maintaining a guessed model-name denylist.
- Claude defaults to `opus`, effort `high`, with resolved identity recorded.
  Effort vocabulary and escalation policy are provider-specific; equal labels do
  not establish equivalent effort. Preserve the existing Codex defaults.
- Claude preparation accepts `--auth-route subscription|api`, defaulting to
  `subscription`. API billing requires explicit `--auth-route api`; inherited
  credentials are not opt-in. Persist the route in job identity and show it in
  status/projections. Changing it requires a new preparation. This Claude-specific
  setting does not change existing Codex authentication behavior.
- Keep exit 7 for reviewer unavailability/execution failure and exit 8 for an
  invalid successful response. Add provider-neutral `reviewer_path`,
  `details.reviewer` and provider-specific log locations.
  Preserve existing Codex codes and non-version `codex_*` metadata for Codex jobs only;
  apply [D10](phase-1b.md#reviewer-cli-compatibility) after extraction. Historical
  metadata remains readable. Claude gets `claude-*` codes.
  Reconcile public CLI/skill error documentation.
- The shared `codex-review` skill follows its
  [automatic code review trigger](../../SKILL.md#automatic-code-review) in both hosts.
  The new Codex-hosted `claude-review` skill also supports automatic selection and
  delegates to the same installed core. Verify actual discovery and execution from
  an isolated worktree.

Module names and internal classes remain indicative; these ownership boundaries
and observable behavior are binding.

## History access and review eligibility

Add `prepare --history-access allowed|denied` and persist the resolved value with
the captured job. It cannot be changed at run time; changing it requires a new job.
The shared core owns this policy, independent of the chosen reviewer.

| Policy | Required behavior |
| --- | --- |
| `allowed` | Default for the requested Claude single-pass workflow. Give the reviewer repository access and supported read-only Git inspection. Do not force history consumption or supply prior-round narratives. |
| `denied` | Files and captured changes remain available, while Git metadata and declared out-of-scope review-history artifacts are inaccessible through every enabled tool. For Claude, omit Bash. Refuse preparation if the driver cannot enforce this policy. |

Legacy Codex jobs keep their original access behavior; do not rewrite records or
claim retroactive confinement. The unchanged Codex driver does not gain a history
barrier through a metadata field: until demonstrated support exists, an explicit
`denied` request must refuse before a model call.

Here `denied` is a defined access policy, not a claim that every readable source
file is free of historical narrative. Bind the denied paths and resolved repository
layout, including linked-worktree metadata. Known review archives outside target
scope must be covered explicitly. Never silently remove a target or normative
companion to obtain cold eligibility; disclose the conflict instead.

[Phase 1b D03](phase-1b.md#decisions-awaiting-the-user)
continues to own cold eligibility. Availability of Git is not proof of consumption;
the historical probe reported consumption of a planted prior-review subject, but
its missing trace cannot now be checked. Access
denial is not by itself proof of a cold round either. Retain cited read assessment,
its unresolved state, and the disqualification/reset rules for known prohibited
history consumption. Targeted rechecks never earn clean-round credit.

When Phase 1b is implemented, cold launches should request `denied` where supported;
unsupported enforcement must be explicit rather than silently downgraded. Existing
Codex D03 input discipline remains accurately described as non-confining. General
source-history inspection and prior findings/fix/verdict consumption must not be
silently conflated in either prompts or adjudication.

## Claude launch, isolation and evidence protection

Launch from the job directory. Grant the resolved repository with `--add-dir` for
repository-backed jobs; grant none for context-free jobs. Resolve real filesystem
paths and, for worktrees, both the per-worktree Git directory and common Git directory.
Grant only the Git reads required by the allowed policy. Prove file reach with exact
marker bytes and test refusal outside the declared read scope through every enabled
tool; cwd alone is not a read boundary.

Pin the launch controls together: `--restricted`, `--safe-mode`,
`--setting-sources ""`, `--strict-mcp-config`, empty explicit MCP configuration,
`--permission-mode dontAsk`, `--permission-prompts none`, and driver-generated
`--settings`. Managed policy still applies. Use `Read,Glob,Grep` for denied history;
add Bash only for allowed history after its acceptance gate passes. The schema can
inject StructuredOutput, so neither rubric nor validation assumes an exact tool count.

Required sandbox settings for Bash include:

- `enabled: true`, `failIfUnavailable: true`, `allowUnsandboxedCommands: false`;
- `autoAllowBashIfSandboxed: false`, filesystem isolation enabled, no excluded
  commands that could bypass the sandbox, and no reviewer-command network domains;
- explicit write denials for the entire job, target/context repository, resolved
  worktree and common Git directories, and any other granted evidence directories.

The parent driver writes logs and outputs outside the child's write authority;
protecting the job from Bash does not prohibit those parent writes. Keep CLI scratch
space separate from captured evidence. Do not describe this as a completely
write-free process: the protected objects and allowed scratch locations are explicit.

Sandbox confinement and command authorization are separate requirements. Configure
permissions for the supported Git inspection operations, including the exact `git -C`
forms used. A broad `Bash(git *)` rule or auto-approved sandbox is not a Git-only
contract. Test denied test runners, interpreters, shell composition, executable
helpers and Git options that can invoke external programs. Git configuration,
external diff/textconv and pagers must not silently expand execution authority.
Do not claim repository test execution is unavailable until those negative cases
establish it. If native rules cannot enforce the required boundary, this launch
design is blocked; return the concrete evidence before changing to a different
execution interface or broadening scope.

Claude's [sandbox documentation](https://code.claude.com/docs/en/sandboxing)
documents writable cwd/additional directories and the independent auto-allow mode.
Its [permissions documentation](https://code.claude.com/docs/en/permissions)
describes rule matching. Verify the required behavior through the controls below.

Validate generated settings before every launch, including known keys, value types,
resolved paths and required invariants. Bind the exact settings bytes and tested
launch controls. The inspected CLI's `--help` warned that invalid settings files are
silently ignored in print mode; do not depend on a settings error being visible in
the model stream.
Acceptance must exercise malformed/unsupported settings and demonstrate refusal
before a review is accepted. Establish the applicable runtime enforcement through
positive and negative probes of the actual launch configuration. A launch
configuration without that evidence remains unsupported. The inspected init events
report no sandbox state:
retain the distinction between requested settings, validated configuration and
observed enforcement. Do not manufacture per-run sandbox attestation from argv.

Before and after each run, compare a manifest of immutable job inputs plus repository
and resolved Git state. Include content, existence, file type, mode and symlink target
where relevant; do not substitute `git status` for Git-metadata coverage. List exact
parent-owned output paths exempted from the job comparison, such as the current
attempt's logs and response; never exempt the whole job or all untracked files.
Detected drift prevents acceptance and preserves diagnostics. It does not authorize
rollback, attribute the change to the reviewer, or justify overwriting concurrent work.
Unchanged hashes are an integrity observation, not proof of sandbox activation;
write-and-restore and network activity require prevention, not a final checksum.

Deliver synthesized policy explicitly with `--append-system-prompt-file` because
safe mode disables automatic discovery. Preserve the 32 KiB cap and define
`synthesized_policy_sha256` over the exact delivered bytes. Driver-owned ambient
provenance records disabled discovery honestly; do not hash `~/.codex/AGENTS.md` as
Claude policy. Record managed-policy limitations rather than asserting complete
ambient isolation. Authentication selection is governed by the contract below,
not by whichever credential the parent environment happens to expose.
Do not use `--bare`, which would break subscription authentication.

### Child environment

Construct the Claude child environment from an empty mapping and an explicit,
versioned allowlist. Do not copy the parent environment and subtract known hazards.
Start with named platform needs (`HOME`, `PATH`, `USER`, `LANG`, `LC_ALL`,
`LC_CTYPE`, `TMPDIR`), pin driver-owned paths, and admit supported proxy variables
by exact name (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY` and their
lowercase forms). Any additional certificate, connectivity or authentication
variable needs a documented purpose and a qualification case. Missing connectivity
must produce an actionable failure, never a fallback to the full parent environment.

Add only credentials required by the selected, qualified authentication route.
No whole-prefix inheritance of `CLAUDE*`, `ANTHROPIC*` or other provider variables.
In particular, do not inherit `CLAUDE_EFFORT`, nested-session/host-auth controls
such as `CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH`, `CLAUDE_CODE_CHILD_SESSION` and
`USE_LOCAL_OAUTH`, or endpoint overrides. Driver-required controls are set explicitly
and tested; model and effort come from the persisted provider-specific job settings.
Do not claim that an environment allowlist disables managed policy or keychain access.

Use the same construction for identity preflight and model invocation. Record the
allowlist version, admitted variable names and non-secret configuration provenance;
never record credential values, secret-derived hashes or credentials in proxy URLs.
Fake-CLI tests must inject unknown and conflicting parent variables and inspect the
actual child environment. Live qualification must exercise subscription connectivity
without inherited Claude-host state. This change is Claude-specific; preserve the
existing Codex driver during extraction.

## Authentication and billing route

Subscription is the default; API billing requires explicit preparation-time opt-in.
This is a user-selected policy, independent of credential precedence in any one host.
Anthropic's
[authentication precedence documentation](https://support.claude.com/en/articles/12304248-manage-api-key-environment-variables-in-claude-code)
describes environment-key precedence and API billing, but it does not qualify the
actual Codex-hosted launch. The archived offline probe calls one extracted API-key
selector with synthetic credentials and stubbed host/configuration functions. It
does not exercise the complete request-authentication path. The supplied contrary
result came from a Claude desktop child with inherited host-auth controls; host
authentication is a plausible explanation, not a demonstrated cause. Neither result
alone establishes precedence in the adapter. See the committed diagnostic for the
separate observation from Codex and its narrow scope.

The new Codex-hosted diagnostic used an allowlisted environment and normal keychain
access. Auth status reported `claude.ai` with the synthetic invalid key present;
`subscriptionType` changed from `pro` to `null`. The subsequent tool-free Haiku
request failed with `Invalid API key`, exit 1 and reported cost 0. This supports
API-key selection in that configuration, not universal
precedence or complete route enforcement. No HTTP 401 was exposed by the captured
CLI result. Full adapter authentication qualification remains an acceptance gate.

For `subscription`, construct the child environment/settings so API keys, API-key
helpers, alternate auth tokens, provider-selection flags and endpoint overrides
cannot select a different billing route. Preserve the supported Claude subscription
login mechanism. Change only the child launch configuration, not the user's shell,
saved credentials or CLI settings. For `api`, require supported, explicitly selected
API authentication; missing API credentials must not fall back to subscription.
Unsupported API backends or helpers must refuse rather than silently select another
route. Neither route may silently fall back to the other on quota or auth failure.

Use `claude auth status --json` as a non-billable identity/configuration preflight
under the same child environment/settings. Its `authMethod`, `subscriptionType`
and `apiProvider` are reported account/backend hints, not proof of the credential
used by a subsequent request. `apiKeySource` in either auth status or init reports
key availability; it does not attest the selected request route. Even a successful
status command or `loggedIn: true` does not validate a synthetic key.

In subscription mode, treat a missing or null `subscriptionType` as an additional
pre-request refusal condition: subscription identity is unconfirmed. Keep the route
unchanged and report the reason; do not fall back to API authentication. A non-null
type only clears this negative check and never proves the request route. The supplied
nested-Claude-host observation retained `pro` with a key present, so neither the
`pro`-to-null transition nor its absence is a universal credential selector. The
subscription environment must still exclude API keys independently of this hint.
Step 3 must qualify this refusal check under the supported CLI and host configuration.

Before a production review request, establish the intended route using qualified
launch controls and supported non-billable checks, not auth status alone. Bind the
requested route and available non-secret account/backend identity; record observed
auth provenance separately and refuse a mismatch or unresolved route. API-key
presence or a successful model response alone does not prove subscription selection.
If a CLI/configuration cannot establish the route before a review request, it remains
unsupported. The diagnostic qualification calls below do not substitute for this
per-attempt gate or silently authorize a production route.
Do not store credentials, secret-derived hashes, or raw auth diagnostics containing
secrets. Revalidate before every attempt, including retries.

Subscription-mode reviews use the selected Claude account's applicable subscription
usage policy. API-mode reviews use the selected API account/backend's billing and
limits. Report usage and `retry_after` in the context of the bound route; never infer
account charges from a model-reported cost field alone.

## Response, logs and process lifecycle

Use `--output-format stream-json --verbose` from the first Claude job. Final-only
JSON cannot supply the tool evidence needed later, and missing historical traces
cannot be reconstructed. Preserve every attempt's raw stdout JSONL and stderr.
Normalize tool use/results with IDs and raw event references; preserve unknown events.
Index Claude events without inventing Codex `command_execution` events.

Take a successful terminal event's `structured_output`, serialize deterministically
to the existing response shape, and run shared validation. Define raw-log and
normalized-response digests separately. Missing or malformed structured output on
an otherwise successful invocation is exit 8, not an approval. Preserve reviewer
text when structured validation fails; the retained raw stream remains available.

Detect denial/error evidence in tool results, including sandbox violations, with
`permission_denials` as supplementary evidence. A denied required read cannot become
an approval simply because the model returns an approving object. Distinguish required
from optional reads; preserve unavailable claims as `not-verifiable` and use the
shared validation/adjudication gate when required scope could not be established.

Prefer observed init/result fields for cwd, model identity, tools and
loaded integrations; retain requested values separately. Record reported model usage,
cost, turns and denials when supplied, without treating reported dollar cost as a
subscription charge. Include the requested and observed authentication route from
the authentication contract. No fallback model or silent effort downgrade.

Pass schema JSON inline, not as a file path. Supply the prompt through controlled
stdin and close it; do not inherit the host's interactive stdin. Keep policy file
delivery and stdout response capture distinct from Codex's output-file interface.

Add a configurable 1,200-second wall-clock timeout to both drivers. This is a policy
default, not measured Opus latency. Use `Popen(start_new_session=True)` on supported
POSIX systems; signal the original process group with `killpg` (TERM, then KILL if
needed), retain outputs and reap the direct process. This is only part of cleanup:
retry requires verified termination of the reviewer and **all its descendants**,
including children in separate sessions/process groups. Original-group disappearance,
parent reaping or pipe EOF alone cannot establish that condition. Claude 2.1.268's
Bash provider spawns detached children; the
[detached-process contract](https://nodejs.org/api/child_process.html#optionsdetached)
places those children in a separate session and process group on POSIX.

Before enabling a platform, select and prove containment or descendant tracking
that accounts for detachment and reparenting; choose its mechanics through TDD.
If complete cleanup cannot be established, retain the partial logs, record cleanup
as unresolved and refuse retry. An unsupported platform must refuse rather than
claim that killing the original group satisfies this contract. Acceptance must
force KILL after an unresponsive TERM, include a detached descendant that survives
original-group termination, and establish both successful complete cleanup and
retry refusal when a survivor or uncertainty remains.

Populate `retry_after` only from evidenced provider responses; otherwise retain
`null`. Distinguish unavailable/usage-limited calls from invalid successful responses.
Do not intentionally exhaust quota, invent an envelope, or retry automatically.
The bound authentication route determines the account's applicable quota/billing.
Single-pass launches remain nonpersistent; session IDs do not prove persistence.
Explicit resume/fork isolation stays a Phase 1b acceptance obligation, including
policy protection and authentication-route validation on resumed launches.

## Delivery and acceptance

Use isolated worktrees and iterative red–green–refactor development. Write meaningful
integration acceptance first where feasible, then one failing unit condition at a
time; use `should_*` names and given/when/then structure.

1. Preserve the completed archive-naming repair and run the existing archive-integrity
   and repository-policy checks before preparing new captures.
2. Add contract coverage, then extract Codex behind the port without changing its
   observable defaults, lifecycle, error compatibility or normalized response.
3. Before relying on the lost observations, re-run bounded Haiku inspection probes
   from Codex using the allowlisted environment and exact proposed controls. Commit
   a fixture directory containing probe source, input fixtures, full configuration,
   sanitized provenance, raw non-secret events, filesystem observations and a hash
   manifest. Include the job-write hole as a disposable negative control, its fix,
   read/history boundaries, command/network denial, and ordinary/linked Git layouts.
   Requalify settings handling and authentication under this environment, including
   the [subscription-identity refusal check](#authentication-and-billing-route):
   compare a key-free subscription baseline with the synthetic-key diagnostic,
   and retain the observed subscription-type fields. Historical hashes or a
   successful nested-Claude-host run cannot satisfy the gate. This step
   must complete before step 6; test-driven implementation may refine the probes,
   but unsupported behavior must not be enabled on the strength of missing traces.
4. Implement Claude, preparation-time provider/history/authentication routing, normalization,
   protections and timeout handling. Apply D10 to both adapters: remove the inherited
   Codex version query and version metadata; add no Claude equivalent. Use fake CLIs
   that record argv/stdin/environment and emit realistic success, failure, denial
   and partial-stream fixtures. Use only synthetic credentials in those fixtures;
   verify route binding, allowlist exclusion
   of unknown/conflicting parent variables, and secret exclusion.
5. Add the sibling skill and installation/discovery coverage. Reconcile CLI help,
   status, projections and provider-neutral public documentation.
6. After the committed probe gate, run bounded live acceptance using the exact
   combined settings and Opus at `high` with the allowlisted environment.
   Preserve command/configuration, logs, expected results and observed
   filesystem state. Live proof must cover both history policies, ordinary and linked
   worktrees, job/repository/Git write denial, command and network denial, exact file
   reach, Git queries when allowed, policy delivery, subscription-route preflight
   and structured results. Qualify explicit API mode separately; any live billable
   API acceptance call requires explicit opt-in.
7. Exercise a complete host lifecycle through finding probes, apply and verification,
   including `--no-apply`; the reviewer remains unable to perform those host actions.
   Reconcile Phase 1b's future driver-specific traces, effort, persistence and timing
   requirements without redefining its approved loop decisions.

Meaningful negative checks must be capable of failing: include an intentionally
missing job deny, denied-mode metadata access, an invalid settings key/value, a
detached descendant surviving forced original-group termination, unresolved cleanup
that must prevent retry, changed immutable inputs and a model that claims approval
after losing required evidence. Do destructive probes only against disposable
fixtures. A successful Haiku response does not establish Opus acceptance.
Fake-CLI coverage must also establish D10: no version query or required version field,
legacy records remain readable, and a compatible executable replacement does not
invalidate a review or require recapture. CLI version text naturally present in raw
logs may remain; it has no decision-making role.

Authentication acceptance must cover a subscription login coexisting with an API
key: omitted `--auth-route` still selects subscription, while explicit `api` selects
only the qualified API route. Also cover API-only credentials in subscription mode,
missing API credentials in API mode, conflicting helper/backend/endpoint settings,
changed identity between attempts, unknown or mismatched preflight provenance, and
no silent fallback after auth/quota failure. Fake-CLI checks must establish refusal
before a model call for missing/null subscription type in subscription mode, and
that retaining `pro` cannot override conflicting or unresolved route evidence.
Forbidden or unresolved routes must refuse before a model request. Negative controls
use synthetic credentials and fake endpoints, with assertions that no billable
request or credential logging occurs.

Qualify precedence from the actual Codex host, keeping a bounded request with a
synthetic invalid key distinct from a production subscription launch (which excludes
that key). Preserve source, exact settings/environment names and diagnostic results
in committed fixtures. An attributable API rejection of the invalid key establishes
that it was tried on that tested path; it does not establish all fallback behavior.
Success establishes that the invalid key was not the sole authenticating credential,
not which alternative credential or billing route was used. A generic 401, transport
failure or status/init source field is insufficient to decide precedence. Repeat
affected qualification when the supported host, auth controls or relevant launch
configuration changes, or observed behavior contradicts the contract. An executable
upgrade alone is not a trigger under D10. This diagnostic does not opt into live
requests using a real API key.

## Decision register

| ID | Status and decision | Reason / rejected alternative | Reopen when |
| --- | --- | --- | --- |
| A01 | Agreed: one core, provider adapters, sibling host skills; adapter first. | User's architecture and sequencing; duplicating the lifecycle would let contracts drift. | A concrete provider requirement cannot fit without weakening the shared contract. |
| A02 | Agreed: Claude defaults to Opus/high; automatic Codex skill selection. | User's selected reviewer; effort labels do not translate across providers. | User changes the preference or measured compatibility blocks it. |
| A03 | Agreed in revision 4: immutable per-job history policy; allowed for the requested independent review, enforced denied option. D03 separately governs cold eligibility. | User requested raw Git access, then approved explicit policy. Reject both unconditional Git prohibition and automatic cold credit. | A policy cannot be enforced, required evidence conflicts with it, or D03 changes. |
| A04 | Binding acceptance gate: protected job/repository/Git paths, command restrictions, no unsandboxed fallback. | Historical reports motivated the job denial and command restrictions; missing traces must be replaced with committed probes. A successful review response is insufficient. | The exact launch fails a positive or negative enforcement test. |
| A05 | Binding: stream logs from the first Claude job; observed provenance and integrity checks retain their limits. | Final-only JSON loses tool evidence; hashes and init events do not attest sandbox activation. | CLI evidence or an explicit contract revision supports a different mechanism. |
| A06 | Policy default: configurable 1,200-second timeout; retry requires confirmed termination of every reviewer descendant, including detached groups. Unresolved cleanup refuses retry. | Bounds hanging work; original-group cleanup leaves detached children alive. The duration is not an empirically optimal limit. | Measurements or evidence about containment, detachment or reparenting require adjustment. |
| A07 | User-selected: subscription by default; API billing requires explicit preparation-time opt-in. Bind and revalidate the route; no silent fallback. | The user's billing preference holds independently of host-specific precedence. Reject implicit API opt-in, route claims based only on status/init and unconditional shared-subscription-quota claims. | The user changes policy, requests another billing backend, or CLI evidence invalidates route qualification. |
| A08 | Revision 5 requirement: construct the Claude child environment from an explicit allowlist, plus qualified route-specific authentication. | Unknown inherited controls can change effort, auth or connectivity; a variable denylist cannot bound them. | A measured platform/authentication need requires a narrowly documented addition. |

Authority for A01–A03 is the user's conversation decisions, including approval of
the revision-4 recommendations. A04–A05 remain acceptance requirements; their lost
probe evidence is explicitly pending replacement rather than treated as current proof.
A06 preserves revision 3's explicit policy default. A07 records the user's choice
during the Astra max review. A08 incorporates the latest environment-isolation feedback.
None of these rows authorizes adapter implementation or changes
the independent Phase 1b decisions.
