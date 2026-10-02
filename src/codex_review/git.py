"""Section 3 — hermetic git. Every invocation carries the same environment."""

import os
import subprocess

# `--no-ext-diff --no-textconv` are diff-family subcommand options only, never global (§3).
HERMETIC_ARGS = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")

HERMETIC_ENV = {
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/bin/false",
}


def run(args, cwd, check=True, text=True):
    completed = subprocess.run(
        ("git",) + HERMETIC_ARGS + tuple(args),
        cwd=cwd,
        env=dict(os.environ, **HERMETIC_ENV),
        capture_output=True,
        text=text,
        stdin=subprocess.DEVNULL,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr or completed.stdout
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise GitError(detail.strip())
    return completed


class GitError(Exception):
    pass


def repo_root(cwd):
    """The repository containing `cwd`, or None."""
    try:
        completed = run(("rev-parse", "--show-toplevel"), cwd=cwd)
    except (GitError, OSError):
        return None
    root = completed.stdout.strip()
    return root or None


def head_oid(cwd):
    """HEAD's object id, or None in an unborn repository."""
    completed = run(("rev-parse", "HEAD"), cwd=cwd, check=False)
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def run_bytes(args, cwd, check=True):
    """Binary-safe: object contents must survive byte for byte, CRLF and all."""
    return run(args, cwd, check=check, text=False)
