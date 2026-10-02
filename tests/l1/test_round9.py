"""Findings from the ninth review — the first run on gpt-reserve."""

import subprocess
from pathlib import Path


def should_refuse_a_policy_file_that_is_not_utf8(review, plan_file_factory, make_repo):
    """
    given a committed CLAUDE.md whose bytes are not valid UTF-8
    when a plan is prepared against that repository
    then it is refused as policy, not raised as a decoding crash

    No symlink is needed to reach this: the reviewer framed it as symlink-mediated,
    but the plain policy file is enough, which makes it older than that resolution.
    """
    repo = make_repo({"keep.txt": "x\n"})
    (repo / "CLAUDE.md").write_bytes(b"policy \xff\xfe bytes\n")
    for argv in (("git", "add", "-A"), ("git", "commit", "-qm", "binary")):
        subprocess.run(argv, cwd=str(repo), check=True, capture_output=True)

    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 5, result
    assert "policy-not-utf8" in result.stdout
