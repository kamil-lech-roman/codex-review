# Reviewer development documents

- [Approval-streak trial evidence](../experiments/approval-streak/README.md) —
  checked job counts, Claude judge attribution and remeasurement for the skill's cost rule.
- [Claude-only round records](../references/claude-only-rounds.md) — host-managed
  procedure selected by the [loop policy](../SKILL.md#when-the-loop-is-done).
- [Claude/Opus adapter plan](plans/claude-opus-adapter.md) — revision 7; shared
  review core, explicit history/authentication policies and an allowlisted Claude
  environment; replacement probe evidence required, implementation deferred.
- [Curated design evidence](evidence/README.md) — bounded controls and measurements,
  with private workflow details excluded and derivations explicit.
- [Phase 1b implementation contract](plans/phase-1b.md) — authorized implementation
  in progress; decisions are settled, while live qualification and release acceptance
  remain required.
- [Phase 1a audit](history/phase-1a-audit/README.md) — checked implementation,
  remaining obligations, reproducible probes and test evidence.
- [Original phased plan](history/codex-review-plan-2026-09-17.md) — immutable
  historical snapshot; its freeze notices and implementation status are obsolete.

The running single-pass workflow is defined in [SKILL.md](../SKILL.md) and the
driver. The plans do not silently change that behavior.

## Evidence and working records

Keep review journals, round/disposition logs, temporary captures and session transcripts
outside the repository (driver jobs already default to `~/.codex-review/`). For local
scratch work, `/.review-work/` is ignored. Git ignore controls tracking, not access;
review-history restrictions still apply wherever records live.

Track current decisions and rationale, unresolved requirements, regression tests and
curated evidence supporting a maintained claim. Extract unique useful material before
retiring a working record; do not link public docs to private scratch files. Preserve
raw hash-bound originals; label publication extracts as derivatives with their own
bytes and source digests. Equal bytes in different evidence roles are not disposable
duplicates. Retire a registered bundle together with its inventory entry and references.

The original plan and Phase 1a audit remain until their outstanding acceptance rows
have direct checks or explicit justified retirement under the [release contract](plans/phase-1b.md#acceptance-and-release-evidence).

## Archived policy inputs

Store captured policy files with an inert `.txt` suffix, including inside
`prepared-inputs/`: for example, `AGENTS.md.txt`. Preserve their bytes and the
original names and digests in `input_sha256`; update only the current file locators
in `archive_sha256`. Reconstruct original filenames only in a separate temporary
reproduction directory.

The [archive inventory](history/archive-inventory.json) independently lists every
manifest-backed archive directory. Register new archives there. The
[repository archive tests](../tests/l1/test_repository_archives.py) require a
non-empty exact match with discovered `docs/history/*/control.json` files; every
manifest must contain a non-empty `archive_sha256` mapping. A missing directory,
manifest or hash table therefore fails instead of silently removing coverage.

Every file below each covered directory must be listed and hash-checked, except
its root `control.json` (which cannot hash itself) and root `README.md` (narrative).
A README already listed remains hash-checked; nested README files are not exempt.
Original-input mappings are checked when `input_sha256` is present. This establishes
inventory and byte integrity, not whether recorded content contains credentials.
The tests also scan the Git index for unsupported nested policy filenames using the driver's policy constants.
Staged additions and renames are covered before commit.
The driver continues to reject nested policy files; there is no archive exception.

Preparation reads committed policy: plan and uncommitted-code reviews use `HEAD`,
while committed-code reviews use `--base`. Commit archive repairs before checking
preparation, and use a repaired baseline for subsequent committed-code reviews.
