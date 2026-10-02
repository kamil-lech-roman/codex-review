"""Section 9 — sealing: copy the bytes into write-once storage, then render from the copy."""

import datetime
import os
import posixpath
import re
import secrets
import shutil

from codex_review import images, state

DEFAULT_PREVIEW_BYTES = 4096
#: A command script renders in full or not at all; beyond this it is refused (exit 6).
COMPLETE_PREVIEW_CAP = 32 * 1024

#: Slots whose preview is never bounded (§9).
COMMAND_SCRIPT = re.compile(r"^staging/(probe-.+|verify)\.sh$")


class SealRefused(Exception):
    def __init__(self, code, message, exit_code, **details):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details


def normalise(relative):
    """One spelling per path, so a slot cannot be dodged by writing it differently."""
    return posixpath.normpath(relative.replace(os.sep, "/"))


def contained_path(job_dir, relative):
    """Resolve first, then require containment — the same rule as fix_paths and the target (§3)."""
    if os.path.isabs(relative):
        raise SealRefused("absolute-path", "evidence path must be relative to the job", 2,
                          path=relative)
    if ".." in relative.split("/"):
        raise SealRefused("parent-traversal", "evidence path may not contain ..", 2,
                          path=relative)
    resolved = os.path.realpath(os.path.join(job_dir, relative))
    root = os.path.realpath(job_dir)
    if resolved != root and not resolved.startswith(root + os.sep):
        raise SealRefused("escapes-job", "evidence path resolves outside the job", 2,
                          path=relative, resolved=resolved)
    if not os.path.isfile(resolved):
        raise SealRefused("missing-evidence", "evidence path does not exist", 2, path=relative)
    return resolved


def render(data, cap):
    """Escape-safe always. Control bytes, NULs and invalid UTF-8 become escapes."""
    shown = data if cap is None else data[:cap]
    text = shown.decode("utf-8", errors="backslashreplace")
    out = []
    for character in text:
        if character in ("\n", "\t"):
            out.append(character)
        elif ord(character) < 0x20 or ord(character) == 0x7F:
            out.append("\\x{:02x}".format(ord(character)))
        else:
            out.append(character)
    return "".join(out)


def seal(job_dir, job_id, relative, cap=DEFAULT_PREVIEW_BYTES):
    relative = normalise(relative)
    source = contained_path(job_dir, relative)
    total = os.path.getsize(source)
    is_command_script = bool(COMMAND_SCRIPT.match(relative))

    if is_command_script and total > COMPLETE_PREVIEW_CAP:
        raise SealRefused("command-script-too-large",
                          "a command script must render in full or not at all", 6,
                          path=relative, bytes=total, limit=COMPLETE_PREVIEW_CAP)

    evidence_id = secrets.token_hex(8)
    directory = os.path.join(job_dir, "evidence", evidence_id)
    os.makedirs(directory)
    stored = os.path.join(directory, "content")
    shutil.copyfile(source, stored)

    # Render from the copy: the bytes shown and the bytes stored are the same object.
    with open(stored, "rb") as handle:
        data = handle.read()

    digest = images.sha256_bytes(data)
    metadata = {
        "job_id": job_id,
        "source_path": relative,
        "sha256": digest,
        "bytes": len(data),
        "sealed_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    state.write_json(os.path.join(directory, "metadata.json"), metadata)

    truncated = (not is_command_script) and len(data) > cap
    return {
        "evidence_id": evidence_id,
        "job_id": job_id,
        "source_path": relative,
        "sha256": digest,
        "bytes": len(data),
        "truncated": truncated,
        "preview": render(data, None if is_command_script else cap),
    }
