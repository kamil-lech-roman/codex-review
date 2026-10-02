"""§14 L2 — a real Claude session drives *this checkout's* skill against a fake Codex.

Not deterministic: a model drives it. Assertions are about contracts — an outcome
recorded, an artifact present, an exit matched — never exact content.
"""
import json
import os
import shutil
import subprocess
import textwrap

import pytest

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def claude_bin():
    """Availability is answering, not exiting zero. An unauthenticated `claude -p` prints
    `Not logged in · Please run /login` and exits 0, which would run every cell below
    against a session that never reached the skill — passing the ones whose assertions a
    session that did nothing also satisfies. §14 makes the L2 cells conditional on
    availability; a session that cannot answer is unavailable in exactly that sense."""
    found = shutil.which("claude")
    if found is None:
        pytest.skip("prerequisite-unavailable: claude is not on PATH")
    try:
        probe = subprocess.run([found, "-p", "Reply with exactly: ok"],
                               capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        pytest.skip("prerequisite-unavailable: claude did not start ({})".format(error))
    if probe.returncode != 0 or "ok" not in (probe.stdout or "").lower():
        detail = (probe.stdout or probe.stderr or "").strip().splitlines()
        pytest.skip("prerequisite-unavailable: claude session did not answer — {}".format(
            detail[0] if detail else "exit {}".format(probe.returncode)))
    return found


@pytest.fixture
def fake_codex_bin(tmp_path):
    """A codex that answers once, so the session spends no reviewer tokens."""
    reply = tmp_path / "reply.json"
    reply.write_text(json.dumps({
        "verdict": "changes-required",
        "findings": [{"id": "F-1", "classification": "introduced", "severity": "major",
                      "claim": "c", "failure_scenario": "f", "evidence": "e",
                      "artifact": "A0001", "suggested_fix": "s"}],
        "declared_scope": ["A0001"],
        "verified_claims": []}))
    binary = tmp_path / "codex"
    binary.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import json, os, sys
        argv = sys.argv[1:]
        if argv[:1] == ["--version"]:
            print("codex 0.0.0-fake"); raise SystemExit(0)
        out = argv[argv.index("-o") + 1] if "-o" in argv else None
        if out:
            with open(os.environ["CODEX_FAKE_RESPONSE"]) as handle:
                body = handle.read()
            with open(out, "w") as handle:
                handle.write(body)
        print(json.dumps({"type": "turn.completed",
                          "usage": {"input_tokens": 0, "cached_input_tokens": 0,
                                    "output_tokens": 0}}))
    """))
    binary.chmod(0o755)
    return binary, reply


@pytest.fixture
def project(tmp_path):
    """The skill under test is this checkout, registered as a project skill of a scratch
    repository. Running in the checkout itself would instead exercise whichever copy
    `~/.claude/skills` happens to point at on the machine running the suite — here a
    symlink to this very directory, which is why the omission was invisible."""
    root = tmp_path / "project"
    (root / ".claude" / "skills").mkdir(parents=True)
    os.symlink(SKILL_ROOT, str(root / ".claude" / "skills" / "codex-review"))
    subprocess.run(["git", "init", "-q", str(root)], check=True,
                   capture_output=True)
    return root


@pytest.fixture
def session(claude_bin, fake_codex_bin, project, tmp_path):
    """Run one non-interactive Claude session against the registered skill.

    The scratch area is writable to it on purpose. A session confined to `project`
    *cannot* create a target outside it, which would make "it did not work around the
    refusal" true of every run whatever the skill said — the workaround has to be within
    reach for refusing it to mean anything."""
    binary, reply = fake_codex_bin
    state = tmp_path / "state"
    state.mkdir()

    def run(prompt, timeout=600):
        environment = dict(
            os.environ,
            CODEX_BIN=str(binary),
            CODEX_FAKE_RESPONSE=str(reply),
            CODEX_REVIEW_STATE=str(state),
        )
        completed = subprocess.run(
            [claude_bin, "-p", prompt, "--permission-mode", "acceptEdits",
             "--add-dir", str(tmp_path)],
            cwd=str(project), env=environment, capture_output=True, text=True,
            timeout=timeout)
        return completed, state

    return run
