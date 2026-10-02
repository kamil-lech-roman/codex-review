"""Section 3 — which policy files are captured, refused, or merely noted."""

import json
from pathlib import Path

CLAUDE = "# House rules\n\nNever silently ignore configuration.\n"
AGENTS = "# Agent rules\n\nPrefer evidence over defaults.\n"


def agents_text(result):
    return (Path(result.envelope["job_dir"]) / "AGENTS.md").read_text()


def target_of(result):
    return json.loads((Path(result.envelope["job_dir"]) / "target.json").read_text())


def should_capture_a_root_claude_file(review, plan_file_factory, make_repo):
    """
    given a repository whose policy commit has CLAUDE.md
    when prepare runs
    then its text is carried inside the trusted block
    """
    repo = make_repo({"CLAUDE.md": CLAUDE})
    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 0, result
    assert "Never silently ignore configuration." in agents_text(result)
    assert target_of(result)["policy_sources"] == ["CLAUDE.md"]


def should_capture_a_dot_claude_file(review, plan_file_factory, make_repo):
    """
    given .claude/CLAUDE.md rather than a root CLAUDE.md
    when prepare runs
    then it is captured just the same
    """
    repo = make_repo({".claude/CLAUDE.md": CLAUDE})
    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 0, result
    assert target_of(result)["policy_sources"] == [".claude/CLAUDE.md"]


def should_refuse_both_claude_locations(review, plan_file_factory, make_repo):
    """
    given CLAUDE.md and .claude/CLAUDE.md both present
    when prepare runs
    then it exits 5 naming both paths
    """
    repo = make_repo({"CLAUDE.md": CLAUDE, ".claude/CLAUDE.md": CLAUDE})
    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 5, result
    named = json.dumps(result.envelope["error"]["details"])
    assert "CLAUDE.md" in named and ".claude/CLAUDE.md" in named


def should_refuse_coexisting_policy_families(review, plan_file_factory, make_repo):
    """
    given a Claude-family and an AGENTS-family file both present
    when prepare runs
    then it exits 5 — the reviewer must not be handed two competing policies
    """
    repo = make_repo({"CLAUDE.md": CLAUDE, "AGENTS.md": AGENTS})
    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 5, result


def should_accept_an_agents_symlink_to_the_claude_file(review, plan_file_factory, make_repo):
    """
    given AGENTS.md is a tracked symlink to CLAUDE.md, so both names resolve to one file
    when prepare runs
    then it is captured rather than refused — two names are not two competing policies
    """
    import subprocess

    repo = make_repo({"CLAUDE.md": CLAUDE})
    (repo / "AGENTS.md").symlink_to("CLAUDE.md")
    for argv in (("git", "add", "-A"), ("git", "commit", "-qm", "symlink")):
        subprocess.run(argv, cwd=str(repo), check=True, capture_output=True)

    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 0, result


def should_read_the_policy_through_a_symlinked_claude_file(review, plan_file_factory,
                                                          make_repo):
    """
    given CLAUDE.md is a tracked symlink and AGENTS.md holds the real policy
    when prepare runs
    then the trusted block carries the policy text, not the symlink's target path
    """
    import subprocess

    repo = make_repo({"AGENTS.md": AGENTS})
    (repo / "CLAUDE.md").symlink_to("AGENTS.md")
    for argv in (("git", "add", "-A"), ("git", "commit", "-qm", "symlink")):
        subprocess.run(argv, cwd=str(repo), check=True, capture_output=True)

    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 0, result
    assert "Prefer evidence over defaults." in agents_text(result)


def should_prefer_an_agents_override(review, plan_file_factory, make_repo):
    """
    given AGENTS.override.md alongside AGENTS.md
    when prepare runs
    then the override is captured and the plain file is not
    """
    repo = make_repo({"AGENTS.md": AGENTS, "AGENTS.override.md": "# Override\n\nUse this.\n"})
    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 0, result
    assert target_of(result)["policy_sources"] == ["AGENTS.override.md"]
    assert "Use this." in agents_text(result)


def should_refuse_claude_rules_directories(review, plan_file_factory, make_repo):
    """
    given any .claude/rules/**/*.md
    when prepare runs
    then it exits 5 — nested rule files are not captured
    """
    repo = make_repo({"CLAUDE.md": CLAUDE, ".claude/rules/style.md": "# rules\n"})
    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 5, result


def should_refuse_an_import_directive(review, plan_file_factory, make_repo):
    """
    given a captured policy file containing an @import
    when prepare runs
    then it exits 5 rather than following a four-hop recursion
    """
    repo = make_repo({"CLAUDE.md": CLAUDE + "\n@import ./other.md\n"})
    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 5, result


def should_refuse_an_oversize_synthesized_file(review, plan_file_factory, make_repo):
    """
    given a policy file large enough to push the synthesized file past 32 KiB
    when prepare runs
    then it exits 5, measuring the final file including wrapper text
    """
    repo = make_repo({"CLAUDE.md": "x" * (33 * 1024)})
    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 5, result


def should_refuse_a_plan_target_that_is_itself_captured_policy(review, make_repo):
    """
    given `plan <path>` naming the repository's own policy file
    when prepare runs
    then it exits 5 — the target must not also be the instructions
    """
    repo = make_repo({"CLAUDE.md": CLAUDE})
    result = review("prepare", "plan", str(repo / "CLAUDE.md"), cwd=repo)

    assert result.exit_code == 5, result


def should_note_but_not_capture_a_local_override(review, plan_file_factory, make_repo):
    """
    given CLAUDE.local.md present beside a captured CLAUDE.md
    when prepare runs
    then it is recorded as noted, never captured
    """
    repo = make_repo({"CLAUDE.md": CLAUDE, "CLAUDE.local.md": "# local\n\nSecret preference.\n"})
    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 0, result
    assert "CLAUDE.local.md" in target_of(result)["policy_noted"]
    assert "Secret preference." not in agents_text(result)


def should_leave_no_staging_residue_when_policy_is_refused(
    review, plan_file_factory, make_repo, state_root
):
    """
    given a refusal that occurs after synthesis is attempted
    when prepare exits 5
    then no .staging build remains, because a refusal is not a crash
    """
    repo = make_repo({"CLAUDE.md": "x" * (33 * 1024)})
    result = review("prepare", "plan", str(plan_file_factory()), cwd=repo)

    assert result.exit_code == 5
    staging = state_root / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []
