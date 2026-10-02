"""§14 L2 — does SKILL.md read as instructions a session can execute?

The driver's own suite proves the verbs work. What no L1 test can reach is whether a
session, given only the skill, reaches them at all. Assertions are contracts: the stage
the **driver itself** reports, an artifact present, an envelope recorded. Never exact
content.

These prompts invoke the skill by name so the test explicitly selects this workflow.

Each cell is paired with negative controls at the foot of this file: a clean stub that
must make the cell **pass**, and one stub per assertion that breaks exactly that
assertion and must make it fail *with that assertion's own message*. One control per
cell is not enough — a cell with six assertions raises `AssertionError` from whichever
one it reaches first, so a single control leaves the other five free to be deleted with
the controls still green. That is measured, not assumed: before this pairing, eleven of
the twelve assertions here survived deletion.
"""
import json
import os
import subprocess

import pytest

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def jobs_under(state):
    return [p for p in state.iterdir() if p.is_dir() and not p.name.startswith(".")]


def driver_status(state, job_id):
    """Ask the driver what stage the job reached. Which files exist is not the stage —
    `report.json` is on disk at `reviewed`, at `applied`, and in an `inconsistent` job."""
    completed = subprocess.run(
        ["/bin/bash", os.path.join(SKILL_ROOT, "review.sh"), "status", job_id],
        env=dict(os.environ, CODEX_REVIEW_STATE=str(state)),
        capture_output=True, text=True)
    return json.loads(completed.stdout)["payload"]


@pytest.mark.l2
def should_prepare_and_run_a_plan_review_from_the_skill_alone(session, tmp_path):
    """
    given a session with the skill available and a plan to review
    when it is asked to review that plan, and to stop after the report
    then the driver reports a job at `reviewed`, with a report on disk
    """
    plan = tmp_path / "plan.md"
    plan.write_text("# A plan\n\nAdd a cache with a 60 second TTL.\n")

    completed, state = session(
        "/codex-review review the plan at {}. "
        "Run prepare and run only — stop after the report exists and tell me the "
        "verdict. Do not probe, apply or verify.".format(plan))

    assert completed.returncode == 0, "the session exited non-zero: {}".format(
        completed.stderr[-2000:])
    found = jobs_under(state)
    assert found, "no job directory was created: {}".format(completed.stdout[-2000:])
    job = found[0]
    assert (job / "report.json").is_file(), "the job has no report: {}".format(
        sorted(p.name for p in job.iterdir()))
    report = json.loads((job / "report.json").read_text())
    assert report["verdict"] in ("approve", "changes-required"), \
        "the report carries no usable verdict: {}".format(report)
    described = driver_status(state, job.name)
    assert described["stage"] == "reviewed", \
        "the driver does not call the job reviewed: {}".format(described)


@pytest.mark.l2
def should_refuse_to_work_around_a_refusal(session, tmp_path):
    """
    given a target that the driver refuses — a plan path that does not exist
    when a session is told to review it and to report the refusal
    then it reports the driver's own refusal envelope, and the target is still absent
    """
    missing = tmp_path / "absent.md"
    reported = tmp_path / "refusal.json"

    completed, state = session(
        "/codex-review review the plan at {}. If the skill refuses, write the refusal "
        "envelope verbatim to {} and stop.".format(missing, reported))

    assert completed.returncode == 0, "the session exited non-zero: {}".format(
        completed.stderr[-2000:])
    assert not jobs_under(state), "a refused target produced a job"
    assert not missing.exists(), "the session created the target it was refused"
    assert reported.is_file(), "no refusal was reported: {}".format(
        completed.stdout[-2000:])
    envelope = json.loads(reported.read_text())
    assert envelope["verb"] == "prepare", \
        "the refusal came from another verb: {}".format(envelope)
    assert envelope["exit"] == 3, \
        "not a target-resolution refusal: {}".format(envelope)


# --- negative controls -------------------------------------------------------------
#
# Offline, deterministic and unmarked, so they run in the default suite: they exercise
# the assertions above, not a session. They live here rather than under `tests/l1/`
# because what they guard is the wording a few lines up, and a guard that drifts away
# from the thing it guards stops guarding it.


def _stub(state_dir, behaviour, returncode=0):
    state_dir.mkdir(exist_ok=True)

    def run(prompt, timeout=600):
        behaviour(state_dir)
        return (subprocess.CompletedProcess(["claude"], returncode, "stdout", "stderr"),
                state_dir)

    return run


def _review_stub(tmp_path, returncode=0, job=True, report=True,
                 verdict="approve", consistent=True):
    """A session that did the review cell's work, with at most one thing wrong."""
    def behaviour(state):
        if not job:
            return
        directory = state / "0123456789abcdef"
        directory.mkdir()
        (directory / "target.json").write_text("{}")
        if consistent:
            (directory / "response.json").write_text("{}")
        if report:
            (directory / "report.json").write_text(
                json.dumps({"verdict": verdict, "findings": []}))

    return _stub(tmp_path / "state", behaviour, returncode)


def _refusal_stub(tmp_path, returncode=0, job=False, invent=False, report=True,
                  verb="prepare", exit_code=3):
    """A session that did the refusal cell's work, with at most one thing wrong."""
    def behaviour(state):
        if job:
            (state / "0123456789abcdef").mkdir()
        if invent:
            (tmp_path / "absent.md").write_text("# invented\n")
        if report:
            (tmp_path / "refusal.json").write_text(json.dumps(
                {"verb": verb, "exit": exit_code, "job_id": None,
                 "error": {"code": "missing-plan-path", "category": "target-resolution"}}))

    return _stub(tmp_path / "state", behaviour, returncode)


def should_pass_the_review_cell_when_the_session_did_the_work(tmp_path):
    """
    given a stub session whose job is exactly what the cell asks for
    when the review cell runs against it
    then it passes — without this, every control below could be green for being broken
    """
    should_prepare_and_run_a_plan_review_from_the_skill_alone(
        _review_stub(tmp_path), tmp_path)


def should_pass_the_refusal_cell_when_the_session_did_the_work(tmp_path):
    """
    given a stub session that reported the refusal and left the target alone
    when the refusal cell runs against it
    then it passes
    """
    should_refuse_to_work_around_a_refusal(_refusal_stub(tmp_path), tmp_path)


REVIEW_CONTROLS = (
    ("exited non-zero", {"returncode": 1}, "exited non-zero"),
    ("created no job", {"job": False}, "no job directory was created"),
    ("wrote no report", {"report": False}, "the job has no report"),
    ("reported no usable verdict", {"verdict": "maybe"}, "carries no usable verdict"),
    ("left the job inconsistent", {"consistent": False}, "does not call the job reviewed"),
)

REFUSAL_CONTROLS = (
    ("exited non-zero", {"returncode": 1}, "exited non-zero"),
    ("produced a job anyway", {"job": True}, "produced a job"),
    ("invented the refused target", {"invent": True}, "created the target it was refused"),
    ("reported no refusal", {"report": False}, "no refusal was reported"),
    ("reported another verb's refusal", {"verb": "run"}, "came from another verb"),
    ("reported some other refusal", {"exit_code": 5}, "not a target-resolution refusal"),
)


@pytest.mark.parametrize("broken,expected", [c[1:] for c in REVIEW_CONTROLS],
                         ids=[c[0] for c in REVIEW_CONTROLS])
def should_fail_the_review_cell_on_each_assertion_in_turn(broken, expected, tmp_path):
    """
    given a stub session with exactly one thing wrong
    when the review cell runs against it
    then it fails at that assertion, named — not merely somewhere
    """
    with pytest.raises(AssertionError, match=expected):
        should_prepare_and_run_a_plan_review_from_the_skill_alone(
            _review_stub(tmp_path, **broken), tmp_path)


@pytest.mark.parametrize("broken,expected", [c[1:] for c in REFUSAL_CONTROLS],
                         ids=[c[0] for c in REFUSAL_CONTROLS])
def should_fail_the_refusal_cell_on_each_assertion_in_turn(broken, expected, tmp_path):
    """
    given a stub session with exactly one thing wrong
    when the refusal cell runs against it
    then it fails at that assertion, named — not merely somewhere
    """
    with pytest.raises(AssertionError, match=expected):
        should_refuse_to_work_around_a_refusal(
            _refusal_stub(tmp_path, **broken), tmp_path)
