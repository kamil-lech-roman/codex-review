"""Section 3 — path images. Every preimage and postimage is {kind, git_mode, content_identity}."""

import contextlib
import hashlib
import os
import stat

CHUNK = 1 << 20  # hashlib.file_digest is 3.11+, so read in chunks (§2)

KIND_REGULAR = "regular"
KIND_SYMLINK = "symlink"
KIND_GITLINK = "gitlink"
KIND_ABSENT = "absent"


@contextlib.contextmanager
def regular_reader(path):
    # Nonblocking open closes the stat/open race when a regular file becomes a FIFO.
    if not stat.S_ISREG(os.stat(path).st_mode):
        raise OSError('not a regular file: ' + os.fspath(path))
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError('not a regular file: ' + os.fspath(path))
        handle = os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise
    with handle:
        yield handle


def sha256_file(path):
    digest = hashlib.sha256()
    with regular_reader(path) as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def image_of(path):
    """The image triple for a filesystem path, without consulting git."""
    if os.path.islink(path):
        link_text = os.fsencode(os.readlink(path))
        return {
            "kind": KIND_SYMLINK,
            "git_mode": "120000",
            "content_identity": "sha256:" + sha256_bytes(link_text),
        }
    if not os.path.exists(path):
        return {"kind": KIND_ABSENT, "git_mode": None, "content_identity": None}
    if os.path.isdir(path):
        # A checked-out submodule is a gitlink: its revision IS its content identity, so a
        # revision change is visible rather than hidden behind a bare directory.
        return {"kind": KIND_GITLINK, "git_mode": "160000",
                "content_identity": _submodule_revision(path)}
    executable = os.access(path, os.X_OK)
    return {
        "kind": KIND_REGULAR,
        "git_mode": "100755" if executable else "100644",
        "content_identity": "sha256:" + sha256_file(path),
    }


def target_sha(path, image):
    """`target_sha` covers the whole image plus the path (§3)."""
    parts = [
        path,
        image["kind"],
        image["git_mode"] or "",
        image["content_identity"] or "",
    ]
    return sha256_bytes("\x00".join(parts).encode("utf-8"))


def _submodule_revision(path):
    import subprocess

    try:
        completed = subprocess.run(
            ("git", "-C", path, "rev-parse", "HEAD"),
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return "git:" + completed.stdout.strip()
