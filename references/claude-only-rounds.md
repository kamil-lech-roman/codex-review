# Recording a Claude-only round

Use this procedure when [the loop policy](../SKILL.md#when-the-loop-is-done) selects a
Claude-only round. This is a host-managed review, not a new driver mode. The current
`run` resolves Codex and calls `reviewer.invoke`; introducing a second backend would
change the driver's lifecycle and evidence contracts for a trial that does not need it.

1. Create a uniquely named round directory in a task-owned evidence location outside
   the captured target, for example `~/.codex-review-judges/<loop-id>/<round-id>/`.
   Never reuse another session's directory or mutate its jobs. Keep the loop's ordered
   Codex job ids, streak and mode in the handoff so a fresh host does not restart Codex.
2. Save `brief.txt` before launching the judge. Keep the agreed review range, context,
   verification requirements and the judge obligations in the skill; remove the Codex
   prepare/run/probe-recording/closure steps. Pin full base and head commit ids. For an
   uncommitted target, also retain its patch, untracked file contents and hashes so the
   reviewed bytes can be recovered. A commit still needs the user's authorization.
3. Start a new Claude subagent without inherited conversation history; do not resume
   an earlier judge. Give it the saved brief and repository inputs, not the prior
   debate or expected answer. Use an isolated checkout for probes that change files.
   Save its unedited report and the commands, outputs and exit codes of its checks.
4. Check each observation against evidence. Record source (`claude`), classification
   (`introduced` or `pre-existing`), disposition (`accepted`, `rejected` or `inconclusive`),
   rationale and evidence paths separately. With no Codex call, `codex_findings` is
   `null`, not an empty result attributed to Codex. After accepted fixes, save the diff
   or commit ids and verification commands, results and exit codes. Keep both the
   reviewed snapshot and the post-fix identity.
5. Save `round.json` with the fields below, and hand off its durable path. A failed or
   incomplete judge is not a clean round. Record whether this was the host conversation's
   first round separately from whether the subagent started fresh; one does not prove
   the other. Apply the skill's completion criterion after judging the report.

Minimum round record (fill in actual values; `null` means unknown or not applicable):

```json
{
  "loop_id": "...",
  "round_id": "...",
  "mode": "claude-only",
  "base": "full commit id",
  "head": "full commit id",
  "snapshot": "path and sha256 of any uncommitted inputs",
  "brief": {"path": "brief.txt", "sha256": "..."},
  "judge": {"model": "resolved Claude model", "session_id": "...", "fresh": true},
  "host_conversation_first_round": false,
  "status": "complete",
  "report": {"path": "report.txt", "sha256": "..."},
  "codex_findings": null,
  "observations": [],
  "fixes": [],
  "verification": [],
  "completion_assessment": "..."
}
```

These are host records, not driver-validated payloads. Do not use `record-probes`,
`record-apply` or `record-verification` to smuggle them into a Codex job. In a combined
round, finish the Codex job normally and store the judge's own observations separately
using the same evidence fields, with mode `combined` and a link to that Codex job.
