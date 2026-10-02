"""§1 — `--note` carries orienting context into the prompt."""

import json
from pathlib import Path


def target_of(result):
    return json.loads((Path(result.envelope["job_dir"]) / "target.json").read_text())


def prompt_of(result):
    return (Path(result.envelope["job_dir"]) / "prompt.txt").read_text()


def should_record_a_note_on_the_target(review, plan_file_factory, outside_any_repo):
    """
    given --note at prepare
    when the target is written
    then the text is recorded, so a round's prompt is reproducible from its target
    """
    result = review("prepare", "plan", str(plan_file_factory()),
                    "--note", "experiments/ is reference material", cwd=outside_any_repo)

    assert result.exit_code == 0, result
    assert target_of(result)["note"] == "experiments/ is reference material"


def should_carry_the_note_into_the_prompt_as_context(review, plan_file_factory,
                                                     outside_any_repo, codex_reply):
    """
    given a job prepared with a note
    when run builds the prompt
    then the note appears, fenced as context rather than as instruction
    """
    prepared = review("prepare", "plan", str(plan_file_factory()),
                      "--note", "the lockfile is generated", cwd=outside_any_repo)
    codex_reply({"verdict": "approve", "findings": [], "declared_scope": ["A0001"],
                 "verified_claims": []})

    review("run", prepared.envelope["job_id"],
           fake={"CODEX_FAKE_RESPONSE": codex_reply.path})

    prompt = prompt_of(prepared)
    assert "the lockfile is generated" in prompt
    fence = prompt[:prompt.index("the lockfile is generated")]
    assert "context" in fence.lower(), fence[-300:]


def should_omit_the_note_section_when_none_was_given(review, plan_file_factory,
                                                     outside_any_repo, codex_reply):
    """
    given a job prepared without a note
    when run builds the prompt
    then no empty note section is emitted
    """
    prepared = review("prepare", "plan", str(plan_file_factory()), cwd=outside_any_repo)
    codex_reply({"verdict": "approve", "findings": [], "declared_scope": ["A0001"],
                 "verified_claims": []})

    review("run", prepared.envelope["job_id"],
           fake={"CODEX_FAKE_RESPONSE": codex_reply.path})

    assert target_of(prepared)["note"] is None
    assert "OPERATOR NOTE" not in prompt_of(prepared)
