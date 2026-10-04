"""Section 7 — each verb accepts only the options it consumes.

An option meant for another verb must be refused, never silently dropped: `run <job>
--no-apply` once left a job in apply mode while the operator believed it was review-only.
"""

import json
from pathlib import Path

import pytest


@pytest.fixture
def job(review, plan_file_factory, outside_any_repo):
    result = review("prepare", "plan", str(plan_file_factory()), cwd=outside_any_repo)
    assert result.exit_code == 0, result
    return result.envelope["job_id"], Path(result.envelope["job_dir"])


def assert_refused_option(result, verb, option):
    assert result.exit_code == 2, result
    assert result.envelope["verb"] == verb
    assert result.envelope["error"]["code"] == "bad-option"
    assert result.envelope["error"]["details"]["option"] == option


def should_refuse_a_prepare_option_passed_to_run(review, job, codex_reply):
    """
    given a prepared job
    when run is given --no-apply, which only prepare consumes
    then it exits 2 with bad-option naming --no-apply, and the reviewer is never invoked
    """
    job_id, job_dir = job
    codex_reply({"verdict": "approve", "findings": [], "declared_scope": ["A0001"],
                 "verified_claims": []})

    result = review("run", job_id, "--no-apply",
                    fake={"CODEX_FAKE_RESPONSE": codex_reply.path})

    assert_refused_option(result, "run", "--no-apply")
    assert not (job_dir / "response.json").exists()


def should_refuse_a_prepare_option_passed_to_seal(review, job):
    """
    given a prepared job holding a file to seal
    when seal is given --model, which seal does not consume
    then it exits 2 with bad-option naming --model, and nothing is sealed
    """
    job_id, job_dir = job
    (job_dir / "probes").mkdir()
    (job_dir / "probes" / "F-1.out").write_text("hello\n")

    result = review("seal", job_id, "probes/F-1.out", "--model", "gpt-6-astra")

    assert_refused_option(result, "seal", "--model")
    assert not (job_dir / "evidence").exists()


def should_refuse_another_recording_file_passed_to_record_probes(review, job, tmp_path):
    """
    given a job and an apply file
    when record-probes is given --apply-file, which only record-apply reads
    then it exits 2 with bad-option naming --apply-file
    """
    job_id, _ = job
    recording = tmp_path / "recording.json"
    recording.write_text("{}")

    result = review("record-probes", job_id, "--probes-file", str(recording),
                    "--apply-file", str(recording))

    assert_refused_option(result, "record-probes", "--apply-file")


def should_refuse_another_recording_file_passed_to_record_apply(review, job, tmp_path):
    """
    given a job and a probes file
    when record-apply is given --probes-file, which only record-probes reads
    then it exits 2 with bad-option naming --probes-file
    """
    job_id, _ = job
    recording = tmp_path / "recording.json"
    recording.write_text("{}")

    result = review("record-apply", job_id, "--apply-file", str(recording),
                    "--probes-file", str(recording))

    assert_refused_option(result, "record-apply", "--probes-file")


def should_refuse_a_prepare_option_passed_to_record_verification(review, job, tmp_path):
    """
    given a job and a verification file
    when record-verification is given --no-verify, which only prepare consumes
    then it exits 2 with bad-option naming --no-verify
    """
    job_id, _ = job
    recording = tmp_path / "recording.json"
    recording.write_text("{}")

    result = review("record-verification", job_id, "--verification-file", str(recording),
                    "--no-verify")

    assert_refused_option(result, "record-verification", "--no-verify")


def should_refuse_any_option_passed_to_status(review, job):
    """
    given a prepared job
    when status is given --no-apply, and status consumes no option at all
    then it exits 2 with bad-option naming --no-apply
    """
    job_id, _ = job

    result = review("status", job_id, "--no-apply")

    assert_refused_option(result, "status", "--no-apply")


def should_refuse_a_prepare_option_passed_to_abandon(review, job):
    """
    given a prepared job
    when abandon is given --no-apply, which only prepare consumes
    then it exits 2 with bad-option naming --no-apply, and the job stays open
    """
    job_id, job_dir = job

    result = review("abandon", job_id, "--reason", "superseded", "--no-apply")

    assert_refused_option(result, "abandon", "--no-apply")
    assert not (job_dir / "abandoned.json").exists()


def should_refuse_another_verbs_option_passed_to_prepare(review, plan_file_factory,
                                                        outside_any_repo):
    """
    given a plan to prepare
    when prepare is given --reason, which only abandon consumes
    then it exits 2 with bad-option naming --reason, and no job is created
    """
    result = review("prepare", "plan", str(plan_file_factory()), "--reason", "r",
                    cwd=outside_any_repo)

    assert_refused_option(result, "prepare", "--reason")
    assert result.envelope["job_id"] is None


def should_let_run_override_the_prepared_model_and_effort(review, job, codex_reply, codex_argv):
    """
    given a job prepared with the default model and effort
    when run is given --model and --effort, the two options run consumes
    then the reviewer is invoked with those values
    """
    job_id, _ = job
    codex_reply({"verdict": "approve", "findings": [], "declared_scope": ["A0001"],
                 "verified_claims": []})

    result = review("run", job_id, "--model", "gpt-6-astra", "--effort", "low",
                    fake={"CODEX_FAKE_ARGV": codex_argv,
                          "CODEX_FAKE_RESPONSE": codex_reply.path})

    assert result.exit_code == 0, result
    argv = json.loads(codex_argv.read_text())
    assert argv[argv.index("-m") + 1] == "gpt-6-astra"
    assert "model_reasoning_effort=low" in argv


def should_name_the_options_the_verb_accepts_when_refusing(review, job):
    """
    given a prepared job
    when run is given --no-apply
    then the refusal lists the options run does accept, so the caller can tell where it belongs
    """
    job_id, _ = job

    result = review("run", job_id, "--no-apply")

    assert result.envelope["error"]["details"]["accepted"] == ["--effort", "--model"]


CONSUMED = {
    "prepare": {"--repo", "--no-repo", "--model", "--effort", "--uncommitted", "--no-apply",
                "--no-verify", "--verify", "--base", "--head", "--max-artifact",
                "--max-bundle", "--note"},
    "run": {"--model", "--effort"},
    "seal": {"--bytes"},
    "record-probes": {"--probes-file"},
    "record-apply": {"--apply-file"},
    "record-verification": {"--verification-file"},
    "status": set(),
    "abandon": {"--reason", "--denied-interactively"},
}
EVERY_OPTION = sorted(set().union(*CONSUMED.values()))


@pytest.mark.parametrize("verb", sorted(CONSUMED))
@pytest.mark.parametrize("option", EVERY_OPTION)
def should_accept_exactly_the_options_each_verb_consumes(verb, option):
    """
    given any verb and any option the driver knows
    when that verb's arguments are parsed
    then the option is parsed only if the verb consumes it, and refused by name otherwise
    """
    from codex_review import main

    positional, options, bad = main.parse_options(verb, ["job", option, "value"])

    if option in CONSUMED[verb]:
        assert bad is None
        assert option[2:].replace("-", "_") in options
    else:
        assert bad == option


def should_assign_every_known_option_to_the_verbs_that_consume_it():
    """
    given the driver's table of known options
    when it is compared with the per-verb table above
    then they name the same options, so a new option cannot skip the allowlists
    """
    from codex_review import main

    assert sorted(main.OPTIONS) == EVERY_OPTION
    assert {verb: set(options) for verb, options in main.VERB_OPTIONS.items()} == CONSUMED
