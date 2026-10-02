"""Section 2 — the state root, the job namespace, and atomic publication."""

import json
import os
import re
import secrets

STAGING = ".staging"


def state_root():
    configured = os.environ.get("CODEX_REVIEW_STATE")
    if configured:
        return os.path.abspath(configured)
    return os.path.join(os.path.expanduser("~"), ".codex-review")


JOB_ID = re.compile(r"[0-9a-f]{16}")


def new_nonce():
    return secrets.token_hex(8)


def is_job_id(name):
    """Whether a name is one `new_nonce` could have produced. Anything else in the state
    root — a reserved lock, `.staging`, a directory another tool keeps there — is not a job."""
    return bool(JOB_ID.fullmatch(name))


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_tree(root):
    for current, _dirs, files in os.walk(root, topdown=False):
        for name in files:
            fd = os.open(os.path.join(current, name), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        _fsync_dir(current)


def staging_dir(root, nonce):
    path = os.path.join(root, STAGING, nonce)
    os.makedirs(path, exist_ok=False)
    return path


def publish(root, nonce, build_dir):
    """Rename a completed .staging build into place. A job never exists without target.json."""
    fsync_tree(build_dir)
    published = os.path.join(root, nonce)
    os.rename(build_dir, published)
    _fsync_dir(root)
    return published
