"""Section 8 — the envelope every verb returns, success or failure."""


def should_refuse_an_unrecognised_verb_with_a_null_verb_field(review):
    """
    given an invocation naming no recognised verb
    when the driver runs
    then it exits 2, reports `verb: null`, and carries an error
    """
    result = review("frobnicate")

    assert result.exit_code == 2
    assert result.envelope["verb"] is None
    assert result.envelope["exit"] == 2
    assert "error" in result.envelope


def should_report_null_identity_when_no_job_was_created_or_resolved(review):
    """
    given an invocation refused before any job exists (null-identity category 1)
    when the driver runs
    then both identity fields are null
    """
    result = review("frobnicate")

    assert result.envelope["job_id"] is None
    assert result.envelope["job_dir"] is None


def should_never_echo_a_rejected_argument_into_an_identity_field(review):
    """
    given a rejected argument
    when the driver refuses it
    then the argument appears only in error.details, never in job_id or job_dir
    """
    result = review("frobnicate")

    assert result.envelope["job_id"] is None
    assert result.envelope["job_dir"] is None
    assert "frobnicate" in json_text(result.envelope["error"]["details"])


def json_text(value):
    import json

    return json.dumps(value)


def should_refuse_a_non_numeric_capture_limit(review, git_repo):
    """
    given a capture limit that is not a number
    when prepare runs on a target it would otherwise capture
    then it refuses with an envelope, rather than raising inside the conversion
    """
    (git_repo / "changed.py").write_text("x = 1\n")

    result = review("prepare", "code", "--uncommitted", "--max-artifact", "bad",
                    cwd=git_repo)

    assert result.envelope is not None, result
    assert result.exit_code == 2, result
    assert result.envelope["error"]["category"] == "usage", result
    assert result.stderr == "", result.stderr
