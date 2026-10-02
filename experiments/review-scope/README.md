# Whole-repository passes and delta rounds, as they actually ran here

A record of what the two review scopes have cost in this repository. **No conclusion is
drawn about which to prefer**, because the data cannot support one: scope and repository age
are the same variable for three of the four whole-repository passes, and the two obvious cost
denominators point in opposite directions.

This public summary retains measured results; the raw review job records remain private.

Three earlier versions of this file did draw conclusions. Thirteen review findings across
three rounds established that all of them overstated the tables below, every time in the same
direction. What is left is the tables and the things that have been tested and ruled out.

## Scope

Every round here is `gpt-6-astra` at `xhigh` against `~/src/codex-review`. The state
directory is shared with other repositories and the model and effort have varied over time;
rounds failing any of those filters are excluded, which is why these totals are smaller than
the row store. Seventeen rounds qualify.

## The two denominators disagree

| scope | rounds | findings | input tokens | per round | per reported finding |
|---|---|---|---|---|---|
| whole repository | 4 | 41 | 5,314,908 | **1,328,727** | **129,632** |
| delta | 13 | 24 | 7,890,645 | **606,973** | **328,777** |

A whole-repository pass cost **2.19 times** a delta round to run, and **0.39 times** as much
per finding it reported. Neither number is the cost of a review on its own; which one matters
depends on whether the budget is a round or a defect.

## The four whole-repository passes

| job | position | artifacts | findings | findings per artifact | input tokens |
|---|---|---|---|---|---|
| `be032a10` | 1 | 33 | 12 | 0.364 | 755,009 |
| `9e681729` | 2 | 39 | 11 | 0.282 | 1,415,140 |
| `e7a62a20` | 3 | 43 | 9 | 0.209 | 1,022,132 |
| `6a7e02e4` | 14 | 59 | 9 | 0.153 | 2,122,627 |

**The yield fell.** 12 to 9 findings, 25% lower; 0.364 to 0.153 per artifact, 58% lower.

Rows come from `$CODEX_REVIEW_STATE/experiments/effort-ab.jsonl` joined to each job's
`target.json` — `repo_root` for the repository, `base_ref` against the empty tree for the
scope — and `report.json` for the findings, model and effort.

## Tested and ruled out

**Age is not controlled, and cannot be controlled from this data.** The sequence is
`W W W . . . . . . . . . . W . . .`. Three of the four whole-repository passes are the three
oldest rounds. Splitting the sequence in half does not separate them — in the first half they
are again positions 1 to 3, ahead of every delta round there. Nor does dropping them: the one
remaining pass sits at position 14 with 10 delta rounds before it and 3 after, which is a
single point against a group spanning the whole history.

**Passes 2 and 3 were mixtures, not first exposure and not re-reads.** `9e681729` carried
24 of its 39 artifacts unchanged from `be032a10` and introduced 6 that no completed review
had seen, `apply.py` among them; `e7a62a20` carried 33 of 43 unchanged and introduced 4,
including `SKILL.md`. Only the first pass had no prior exposure at all. Nothing here
separates what a pass found in code it was seeing for the first time from what it found in
code an earlier pass had already read.

**"A delta round sees a diff where a whole pass sees a file" is false.** Four of
`6a7e02e4`'s nine findings were in `apply.py` and `projections.py` — `F-1` and `F-6` in the
first, `F-2` and `F-3` in the second — three of them major. Five earlier delta rounds
under these filters had captured one or both files without reporting them — six, counting one
`gpt-reserve` round excluded by the model filter. In `ce88448287095be9`, the earliest, the
reviewer ran `diff -u` on both artifacts **and then** `nl -ba` on both complete after-images,
181 lines of `apply.py` and 234 of `projections.py`. It had the whole of both files.

## What would be needed to conclude anything

Whole-repository passes and delta rounds interleaved in the same stretch of history, assigned
without regard to what anyone expected to find, with findings weighed rather than counted.
None of that is true of what is recorded above.
