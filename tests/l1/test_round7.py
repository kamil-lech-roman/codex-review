"""Findings from the seventh review — the resolver's remaining traversal assumptions."""

import subprocess
from pathlib import Path


def commit_all(repo):
    for argv in (("git", "add", "-A"), ("git", "commit", "-qm", "x")):
        subprocess.run(argv, cwd=str(repo), check=True, capture_output=True)


def agents_text(result):
    return (Path(result.envelope["job_dir"]) / "AGENTS.md").read_text()


def should_refuse_a_policy_link_naming_a_parent_of_the_repository(review,
                                                                  plan_file_factory,
                                                                  make_repo):
    """
    given AGENTS.md -> ../notes.md, naming a file above the repository
    when the policy is captured
    then it is refused, not silently satisfied by a same-named file inside the repository
    """
    repo = make_repo({"notes.md": "# Decoy\n\nAn unrelated document.\n"})
    (repo / "AGENTS.md").symlink_to("../notes.md")
    commit_all(repo)

    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 5, result
    assert "An unrelated document." not in result.stdout


def should_refuse_a_policy_link_traversing_a_regular_file(review, plan_file_factory,
                                                          make_repo):
    """
    given AGENTS.md -> plain.txt/../notes.md, where plain.txt is not a directory
    when the policy is captured
    then it is refused — real traversal would fail with ENOTDIR
    """
    repo = make_repo({"plain.txt": "not a directory\n",
                      "notes.md": "# Decoy\n\nAn unrelated document.\n"})
    (repo / "AGENTS.md").symlink_to("plain.txt/../notes.md")
    commit_all(repo)

    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 5, result
    assert "An unrelated document." not in result.stdout


def should_capture_a_repository_holding_a_non_utf8_filename(review, plan_file_factory,
                                                            make_repo):
    """
    given a commit carrying a filename that is not valid UTF-8
    when a plan is prepared against that repository
    then capture reads the listing byte-faithfully instead of raising
    """
    repo = make_repo({"keep.txt": "x\n"})
    oid = subprocess.run(("git", "hash-object", "-w", "--stdin"), cwd=str(repo),
                         input=b"data\n", stdout=subprocess.PIPE,
                         check=True).stdout.decode().strip()
    subprocess.run([b"git", b"update-index", b"--add", b"--cacheinfo",
                    b"100644," + oid.encode() + b",legacy-\xff.dat"],
                   cwd=str(repo), check=True, capture_output=True)
    subprocess.run(("git", "commit", "-qm", "odd"), cwd=str(repo), check=True,
                   capture_output=True)

    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 0, result
    assert "No policy files were present" in agents_text(result)
