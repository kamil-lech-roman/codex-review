"""Section 3 — no child the driver starts may inherit its stdin.

The driver never feeds a child anything on stdin. A child that reads it anyway — codex
does, for "additional input" — blocks forever on whatever the caller left open: a pipe,
a socket, a CI runner. A live review hung three and a half hours that way. The
behavioural test in test_run.py covers the codex call; this one covers every spawn site,
including the next one added — in the driver and in the experiment scripts beside it,
which drive codex directly and hang the same way.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = (ROOT / "src" / "codex_review", ROOT / "experiments")
SPAWNERS = {"run", "Popen", "call", "check_call", "check_output"}


def spawns_without_stdin():
    missing = []
    for path in sorted(p for base in SCANNED for p in base.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in SPAWNERS
                    and isinstance(func.value, ast.Name) and func.value.id == "subprocess"):
                continue
            if not any(keyword.arg == "stdin" for keyword in node.keywords):
                missing.append("{}:{}".format(path.relative_to(ROOT), node.lineno))
    return missing


def should_give_every_child_process_an_explicit_stdin():
    """
    given every subprocess call in the driver and the experiment scripts
    when each is inspected for a stdin argument
    then none inherits the caller's stdin by omission
    """
    assert spawns_without_stdin() == []


def should_actually_find_the_spawn_sites_it_inspects():
    """
    given the driver's source, which does start child processes
    when the guard's own search runs
    then it finds them — a search that matches nothing would pass vacuously
    """
    sites = 0
    for path in (p for base in SCANNED for p in base.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in SPAWNERS and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"):
                sites += 1
    assert sites >= 4, sites
