"""Keep repository instruction files within the driver's supported root locations."""
from pathlib import Path, PurePosixPath

from codex_review import git, policy


REPO_ROOT = Path(__file__).resolve().parents[2]


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

    # then nested instructions cannot make this repository unreviewable
    assert not nested, "Unsupported nested policy files: " + repr(nested)
