"""Section 3 — resolving the `codex` executable."""

import os
import shutil


def resolve():
    """`$CODEX_BIN`, else `PATH`. Returns an absolute path, or None if unresolvable.

    An override is required because the binary is often installed outside PATH; the resolved
    path is recorded in the job so an ambient lookup becomes a recorded fact (§3).
    """
    override = os.environ.get("CODEX_BIN")
    if override:
        candidate = os.path.abspath(override)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        return None
    found = shutil.which("codex")
    return os.path.abspath(found) if found else None
