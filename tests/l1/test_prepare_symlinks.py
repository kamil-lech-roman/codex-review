"""Section 3 — a symlinked final component is refused; a symlinked ancestor is not."""

import os
from pathlib import Path


def should_refuse_a_plan_target_whose_final_component_is_a_symlink(review, tmp_path):
    """
    given a plan path whose last component is a symlink to a real file
    when prepare runs
    then it exits 6 and names both the link and its referent
    """
    referent = tmp_path / "real-plan.md"
    referent.write_text("# real\n")
    link = tmp_path / "link-plan.md"
    link.symlink_to(referent)

    result = review("prepare", "plan", str(link))

    assert result.exit_code == 6, result
    details = result.envelope["error"]["details"]
    assert details["path"] == str(link)
    assert details["referent"] == str(referent)


def should_accept_a_plan_target_beneath_a_symlinked_ancestor(review, tmp_path):
    """
    given a plan file inside a directory reached through a symlinked ancestor
    when prepare runs
    then it is captured normally — an ancestor only gives the file another name
    """
    real_dir = tmp_path / "real-dir"
    real_dir.mkdir()
    plan = real_dir / "design.md"
    plan.write_text("# a plan\n")
    linked_dir = tmp_path / "linked-dir"
    linked_dir.symlink_to(real_dir, target_is_directory=True)

    result = review("prepare", "plan", str(linked_dir / "design.md"))

    assert result.exit_code == 0, result
    assert result.envelope["job_id"] is not None
