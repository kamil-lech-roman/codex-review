"""Section 3 — RepoIdentity and the four projections of a code target."""

import os

from codex_review import git, images

MAX_ARTIFACT_BYTES = 64 * 1024
MAX_BUNDLE_BYTES = 256 * 1024

UNBORN = "unborn"


class CaptureRefused(Exception):
    def __init__(self, code, message, exit_code, **details):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details


def worktree_manifest(root):
    """Every Git-visible path and its image: the delta universe for a code target (§3)."""
    tracked = git.run(("ls-files", "-z"), cwd=root).stdout
    untracked = git.run(
        ("ls-files", "-z", "--others", "--exclude-standard"), cwd=root
    ).stdout
    manifest = {}
    for path in sorted(set(p for p in (tracked + untracked).split("\0") if p)):
        manifest[path] = images.image_of(os.path.join(root, path))
    return manifest


def index_manifest(root):
    """The index as an image per path (§3). A staged defect is corrected by staging its fix,
    which moves nothing in the worktree — so the index needs a sample of its own."""
    raw = git.run(("ls-files", "--stage", "-z"), cwd=root).stdout
    manifest = {}
    for chunk in raw.split("\0"):
        if not chunk:
            continue
        head, _, path = chunk.partition("\t")
        mode, oid = head.split()[0], head.split()[1]
        manifest[path] = {
            "kind": images.KIND_SYMLINK if mode == "120000" else images.KIND_REGULAR,
            "git_mode": mode,
            "content_identity": "git:" + oid,
        }
    return manifest


def repo_identity(root):
    head = git.head_oid(root) or UNBORN
    refs = git.run(("for-each-ref",), cwd=root).stdout
    index = git.run(("ls-files", "--stage", "-z"), cwd=root).stdout
    tracked = git.run(("ls-files", "-z"), cwd=root).stdout
    untracked = git.run(
        ("ls-files", "-z", "--others", "--exclude-standard"), cwd=root
    ).stdout

    worktree_parts = []
    for path in sorted(p for p in (tracked + untracked).split("\0") if p):
        absolute = os.path.join(root, path)
        image = images.image_of(absolute)
        worktree_parts.append(
            "\0".join([path, image["kind"], image["git_mode"] or "",
                       image["content_identity"] or ""])
        )
    return {
        "head_oid": head,
        "ref_digest": images.sha256_bytes(refs.encode("utf-8")),
        "index_digest": images.sha256_bytes(index.encode("utf-8")),
        "worktree_digest": images.sha256_bytes("\n".join(worktree_parts).encode("utf-8")),
    }


def refuse_bad_index_states(root):
    """Unmerged entries and intent-to-add are refused (exit 6)."""
    unmerged = git.run(("ls-files", "-u"), cwd=root).stdout.strip()
    if unmerged:
        raise CaptureRefused("unmerged-index", "the index has unmerged entries", 6)
    raw = git.run(("diff-files", "--raw"), cwd=root).stdout
    for line in raw.split("\n"):
        if not line.startswith(":"):
            continue
        fields = line.split()
        if len(fields) >= 5 and fields[4].startswith("A"):
            raise CaptureRefused("intent-to-add", "the index has intent-to-add entries", 6)


def _blob(root, oid):
    return git.run_bytes(("cat-file", "blob", oid), cwd=root, check=False).stdout


def _image_from_mode(mode, oid, root):
    if mode == "000000":
        return {"kind": images.KIND_ABSENT, "git_mode": None, "content_identity": None}
    kind = {
        "120000": images.KIND_SYMLINK,
        "160000": images.KIND_GITLINK,
    }.get(mode, images.KIND_REGULAR)
    return {"kind": kind, "git_mode": mode, "content_identity": "git:" + oid}


def committed_changes(root, base):
    """The committed projection: base → HEAD."""
    raw = git.run(("diff", "--raw", "--no-ext-diff", "--no-textconv", "-z", base, "HEAD"),
                  cwd=root).stdout
    return _parse_raw_z(raw, root, after_from_worktree=False)


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _staged_base(root):
    """Before the first commit there is no HEAD to diff against, and everything staged is
    an addition — which is what the empty tree says."""
    if git.run(("rev-parse", "--verify", "-q", "HEAD"), cwd=root, check=False).returncode:
        return EMPTY_TREE
    return "HEAD"


def uncommitted_changes(root):
    """Staged and worktree are separate projections (§3): collapsing them loses the stage
    a plain `git commit` would actually commit."""
    staged = _parse_raw_z(
        git.run(("diff", "--raw", "--no-ext-diff", "--no-textconv", "-z", "--cached",
                 _staged_base(root)), cwd=root).stdout,
        root, after_from_worktree=False)
    for entry in staged:
        entry["projection"] = "staged"
    worktree = _parse_raw_z(
        git.run(("diff-files", "--raw", "--no-ext-diff", "--no-textconv", "-z"),
                cwd=root).stdout,
        root, after_from_worktree=True)
    for entry in worktree:
        entry["projection"] = "worktree"
    entries = staged + worktree
    # A path whose projections all end absent is not represented on disk, so an untracked
    # file there is still unseen: `git rm --cached` must not hide the file it leaves behind.
    seen = set(entry["path"] for entry in entries
               if entry["after"]["kind"] != images.KIND_ABSENT)
    untracked = git.run(("ls-files", "-z", "--others", "--exclude-standard"), cwd=root).stdout
    for path in sorted(p for p in untracked.split("\0") if p):
        if path in seen:
            continue
        entries.append({
            "path": path,
            "projection": "untracked",
            "before": {"kind": images.KIND_ABSENT, "git_mode": None,
                       "content_identity": None},
            "after": images.image_of(os.path.join(root, path)),
            "_after_source": worktree_source(root, path),
            "_before_source": None,
        })
    return sorted(entries, key=lambda entry: (entry["path"], entry.get("projection", "")))


def _parse_raw_z(raw, root, after_from_worktree):
    """`git diff --raw -z` emits NUL-separated metadata and path fields."""
    fields = raw.split("\0")
    entries = []
    index = 0
    while index < len(fields):
        head = fields[index]
        if not head.startswith(":"):
            index += 1
            continue
        parts = head[1:].split()
        before_mode, after_mode, before_oid, after_oid = parts[0], parts[1], parts[2], parts[3]
        status = parts[4] if len(parts) > 4 else ""
        destination_is_new = status[:1] in ("R", "C")
        if destination_is_new:
            # A rename carries two path fields. Both paths changed, so both are in scope.
            source_path, path = fields[index + 1], fields[index + 2]
            index += 3
            if status[:1] == "R":
                entries.append({
                    "path": source_path,
                    "before": _image_from_mode(before_mode, before_oid, root),
                    "after": {"kind": images.KIND_ABSENT, "git_mode": None,
                              "content_identity": None},
                    "_before_source": ("blob", before_oid),
                    "_after_source": None,
                })
        else:
            path = fields[index + 1]
            index += 2
        after = _image_from_mode(after_mode, after_oid, root)
        after_source = ("blob", after_oid)
        if after_from_worktree and after_oid.strip("0") == "":
            after = images.image_of(os.path.join(root, path))
            after_source = worktree_source(root, path)
        # before_mode/before_oid describe the *source* of a rename or copy. The destination
        # path did not exist at the base, so its preimage is absence — reusing the source's
        # would make the destination read as a file that already held those bytes.
        before = ({"kind": images.KIND_ABSENT, "git_mode": None, "content_identity": None}
                  if destination_is_new
                  else _image_from_mode(before_mode, before_oid, root))
        entries.append({
            "path": path,
            "before": before,
            "after": after,
            "_before_source": (None if destination_is_new or before_mode == "000000"
                               else ("blob", before_oid)),
            "_after_source": None if after["kind"] == images.KIND_ABSENT else after_source,
        })
    return sorted(entries, key=lambda entry: entry["path"])


def read_side(root, source):
    """Type-aware: bytes for a file, link text for a symlink, never the referent (§3)."""
    if source is None:
        return b""
    kind, value = source
    if kind == "blob":
        return _blob(root, value)
    if kind == "linktext":
        return os.readlink(os.path.join(root, value)).encode("utf-8")
    if kind == "gitlink":
        return b""
    with open(os.path.join(root, value), "rb") as handle:
        return handle.read()


def worktree_source(root, path):
    """Choose how to read a worktree path, by what it is."""
    absolute = os.path.join(root, path)
    if os.path.islink(absolute):
        return ("linktext", path)
    if os.path.isdir(absolute):
        return ("gitlink", path)
    return ("worktree", path)


def enforce_limits(sizes, max_artifact, max_bundle):
    for path, size in sizes:
        if size > max_artifact:
            raise CaptureRefused("artifact-too-large", "an artifact exceeds the per-file cap",
                                 6, path=path, bytes=size, limit=max_artifact)
    total = sum(size for _path, size in sizes)
    if total > max_bundle:
        raise CaptureRefused("bundle-too-large", "the capture exceeds the bundle cap", 6,
                             bytes=total, limit=max_bundle)


def confirm_correspondence(root, entries, identity, copied=None):
    """Section 3: the published set must correspond to ONE sampled identity.

    Recomputing the identity either side of the copy is not sufficient — a path changed and
    restored between the samples satisfies that while the copy holds bytes from neither. So
    every captured worktree postimage is compared against what the confirming sample holds.
    """
    if repo_identity(root) != identity:
        return False
    for position, entry in enumerate(entries):
        source = entry.get("_after_source")
        if source is None or source[0] == "blob":
            continue  # immutable objects cannot drift behind their oid
        current = images.image_of(os.path.join(root, entry["path"]))
        if current != entry["after"]:
            return False
        if copied is None:
            continue
        # The copy itself must match the confirming sample. Comparing the live file to the
        # recorded image would pass for bytes changed and restored while the copy ran.
        identity_of_copy = current.get("content_identity")
        if identity_of_copy and identity_of_copy.startswith("sha256:"):
            if identity_of_copy != "sha256:" + images.sha256_bytes(copied[position]):
                return False
    return True
