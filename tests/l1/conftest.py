import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REVIEW_SH = REPO_ROOT / "review.sh"


class Invocation:
    """One `review.sh` run: its exit status and the envelope it printed."""

    def __init__(self, completed):
        self.exit_code = completed.returncode
        self.stdout = completed.stdout
        self.stderr = completed.stderr
        try:
            self.envelope = json.loads(completed.stdout)
        except json.JSONDecodeError:
            self.envelope = None

    def __repr__(self):
        return f"Invocation(exit={self.exit_code}, stdout={self.stdout!r}, stderr={self.stderr!r})"


@pytest.fixture
def state_root(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def fake_codex(tmp_path):
    """L1 runs offline against a fake `codex` (§14). It records argv and emits a scripted reply.

    The reply is whatever `CODEX_FAKE_RESPONSE` names; argv lands in `argv.json` beside it.
    """
    path = tmp_path / "fake-codex"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "argv = sys.argv[1:]\n"
        "if argv and argv[0] == '--version':\n"
        "    print('codex-cli 0.0.0-fake'); sys.exit(0)\n"
        "if os.environ.get('CODEX_FAKE_READ_STDIN'):\n"
        "    sys.stdin.read()  # as codex does: 'Reading additional input from stdin...'\n"
        "record = os.environ.get('CODEX_FAKE_ARGV')\n"
        "if record:\n"
        "    json.dump(argv, open(record, 'w'))\n"
        "out = None\n"
        "for i, a in enumerate(argv):\n"
        "    if a in ('-o', '--output-last-message') and i + 1 < len(argv):\n"
        "        out = argv[i + 1]\n"
        "reply = os.environ.get('CODEX_FAKE_RESPONSE')\n"
        "body = open(reply).read() if reply and os.path.exists(reply) else '{}'\n"
        "if out:\n"
        "    open(out, 'w').write(body)\n"
        "stdout = os.environ.get('CODEX_FAKE_STDOUT')\n"
        "if stdout:\n"
        "    sys.stdout.write(open(stdout).read())\n"
        "sys.exit(int(os.environ.get('CODEX_FAKE_RC', '0')))\n"
    )
    path.chmod(0o755)
    return path


@pytest.fixture
def codex_argv(tmp_path):
    """Where the fake records the argv it was given."""
    return tmp_path / "codex-argv.json"


@pytest.fixture
def codex_reply(tmp_path):
    """Writes the reply the fake will emit; returns a setter."""
    path = tmp_path / "codex-reply.json"

    def set_reply(value):
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return path

    set_reply.path = path
    return set_reply


@pytest.fixture
def review(state_root, fake_codex):
    """Runs the driver against an isolated state root, as a real subprocess."""

    def run(*args, cwd=None, codex_bin=fake_codex, fake=None):
        env = dict(os.environ, CODEX_REVIEW_STATE=str(state_root))
        for key, value in (fake or {}).items():
            env[key] = str(value)
        if codex_bin is None:
            env.pop("CODEX_BIN", None)
            # System tools only: has dirname and python3 for the shim, never codex.
            env["PATH"] = "/usr/bin:/bin"
        else:
            env["CODEX_BIN"] = str(codex_bin)
        return Invocation(
            subprocess.run(
                [str(REVIEW_SH), *args],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(cwd) if cwd else None,
            )
        )

    return run


@pytest.fixture
def plan_file_factory(tmp_path):
    counter = {"n": 0}

    def make(text="# a plan\n"):
        counter["n"] += 1
        path = tmp_path / "plan-{}.md".format(counter["n"])
        path.write_text(text)
        return path

    return make


@pytest.fixture
def git_repo(tmp_path):
    """A real git repository with one commit, for context resolution."""
    root = tmp_path / "repo"
    root.mkdir()
    run = lambda *a: subprocess.run(a, cwd=str(root), check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "Test")
    (root / "README.md").write_text("# repo\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "initial")
    return root


@pytest.fixture
def outside_any_repo(tmp_path):
    """A directory guaranteed not to sit inside a git repository."""
    path = tmp_path / "no-repo"
    path.mkdir()
    return path


@pytest.fixture
def make_repo(tmp_path):
    """Builds a git repository whose HEAD contains the given {relative path: text} files."""
    counter = {"n": 0}

    def make(files=None):
        counter["n"] += 1
        root = tmp_path / "repo-{}".format(counter["n"])
        root.mkdir()
        run = lambda *a: subprocess.run(a, cwd=str(root), check=True, capture_output=True)
        run("git", "init", "-q")
        run("git", "config", "user.email", "t@example.com")
        run("git", "config", "user.name", "Test")
        (root / "README.md").write_text("# repo\n")
        for relative, text in (files or {}).items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        run("git", "add", "-A")
        run("git", "commit", "-qm", "initial")
        return root

    return make
