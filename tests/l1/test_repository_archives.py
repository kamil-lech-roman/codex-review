"""Keep evidence archives complete, intact and inert as repository policy."""

import hashlib
import json
from pathlib import Path, PurePosixPath

import pytest

from codex_review import git, policy


REPO_ROOT = Path(__file__).resolve().parents[2]
HISTORY_ROOT = REPO_ROOT / "docs" / "history"
CONTROLS = {
    path: json.loads(path.read_text())
    for path in sorted(HISTORY_ROOT.glob("*/control.json"))
}
ARCHIVE_CONTROLS = {
    path: control for path, control in CONTROLS.items() if "archive_sha256" in control
}


def should_keep_the_repository_index_free_of_nested_policy_files():
    # given the repository index, including staged additions and renames
    indexed = git.run_bytes(("ls-files", "-z"), cwd=REPO_ROOT).stdout

    # when policy filenames are checked against the supported root locations
    paths = [path.decode("utf-8", "surrogateescape") for path in indexed.split(b"\0") if path]
    nested = [
        path for path in paths
        if PurePosixPath(path).name in policy.POLICY_BASENAMES
        and path not in policy.CLAUDE_LOCATIONS + policy.AGENTS_LOCATIONS
    ]

    # then archived inputs cannot make this repository unreviewable
    assert not nested, "Archive policy snapshots with an inert .txt suffix: " + repr(nested)


def should_keep_every_registered_archive_under_integrity_checks():
    # given an inventory independent of manifest discovery
    registered = json.loads((HISTORY_ROOT / "archive-inventory.json").read_text())
    assert registered and len(registered) == len(set(registered))

    # when manifests and hash-bearing archives are discovered
    discovered = {path.parent.name for path in CONTROLS}
    covered = {path.parent.name for path in ARCHIVE_CONTROLS}

    # then deleting a manifest, its hash table or the history root cannot erase coverage
    assert discovered, "No archive manifests discovered"
    assert discovered == set(registered)
    assert covered == discovered
    for control in ARCHIVE_CONTROLS.values():
        assert isinstance(control["archive_sha256"], dict) and control["archive_sha256"]


@pytest.mark.parametrize("control_path", ARCHIVE_CONTROLS, ids=lambda path: path.parent.name)
def should_preserve_archived_input_bytes_and_recorded_hashes(control_path):
    # given the recorded input hashes and current archive locators
    root = control_path.parent
    control = ARCHIVE_CONTROLS[control_path]

    # when archive members are compared with the manifest
    files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*") if path.is_file() or path.is_symlink()
    }
    exemptions = {"control.json", "README.md"}

    # then only the self-describing manifest and top-level narrative may be unlisted
    assert files - exemptions == set(control["archive_sha256"]) - exemptions

    # when each archived artifact is read through its locator
    for relative, expected in control["archive_sha256"].items():
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()

        # then all original artifact digests still hold
        assert actual == expected, relative

    # and original input names still identify the same bytes under inert archive names
    for relative, expected in control.get("input_sha256", {}).items():
        archived = "prepared-inputs/" + relative
        if PurePosixPath(relative).name in policy.POLICY_BASENAMES:
            archived += ".txt"
        assert control["archive_sha256"][archived] == expected, relative
