"""Every response field the driver validates must be described in the rubric.

`response.py` refuses a reply whose `declared_scope` does not contain each artifact
id as an element. Nothing told the reviewer that: the JSON schema types the field as
a bare array of strings and the rubric never named it. Two different models
independently answered with one prose sentence that *mentioned* both ids, and both
replies were rejected as `declared-scope-incomplete` after minutes of real work.

A validated format that is never stated is a contract the reviewer cannot satisfy.
"""
from codex_review import rubric


def should_tell_the_reviewer_that_declared_scope_lists_artifact_ids():
    """
    given the rubric sent to the reviewer for a code target
    when it is built
    then it names declared_scope and says the entries are artifact ids
    """
    prompt = rubric.build(job_dir="/tmp/job", mode="code", context_free=False)

    assert "declared_scope" in prompt
    scope_text = prompt[prompt.index("declared_scope"):]
    assert "artifact id" in scope_text[:400], scope_text[:400]
