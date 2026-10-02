"""Section 2 — job addressability and the per-job mutex."""

import contextlib
import fcntl
import os

from codex_review import state


def resolve(root, argument):
    """A job is a published direct child of the state root whose name is a nonce (§2).

    Returns the absolute job directory, or None. A name that is not a nonce resolves to
    None — reserved names, `.staging/`, and any directory another tool keeps in the root:
    "inside the state root" alone would accept all of them.
    """
    if not argument:
        return None
    root = os.path.abspath(root)
    if os.path.isabs(argument):
        job_dir = os.path.abspath(argument)
        if os.path.dirname(job_dir) != root:
            return None
        name = os.path.basename(job_dir)
    else:
        name = argument
        if os.sep in name:
            return None
        job_dir = os.path.join(root, name)
    if not state.is_job_id(name):
        return None
    if not os.path.isdir(job_dir):
        return None
    return job_dir


@contextlib.contextmanager
def mutex(job_dir, exclusive=True):
    """Guards a job's mutating verbs. Write-once is not self-enforcing across processes (§13)."""
    fd = os.open(os.path.join(job_dir, ".mutex"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
