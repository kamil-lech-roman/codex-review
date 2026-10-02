"""Section 3 — invoking Codex over the frozen target."""

import json
import os
import subprocess
import time

from codex_review import images, rubric

DEFAULT_MODEL = "gpt-6-astra"
DEFAULT_EFFORT = "xhigh"

# The global instruction sources Codex additionally loads (§3, A10 — sampled, not attested).
GLOBAL_POLICY_SOURCES = ("~/.codex/AGENTS.md",)

_BASE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "findings", "declared_scope"],
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "changes-required"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "classification", "severity", "claim",
                             "failure_scenario", "evidence", "artifact", "suggested_fix"],
                "properties": {
                    "id": {"type": "string"},
                    "classification": {"type": "string",
                                       "enum": ["introduced", "pre-existing"]},
                    "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
                    "claim": {"type": "string"},
                    "failure_scenario": {"type": "string"},
                    "evidence": {"type": "string"},
                    "artifact": {"type": ["string", "null"]},
                    "suggested_fix": {"type": "string"},
                },
            },
        },
        "declared_scope": {"type": "array", "items": {"type": "string"}},
    },
}

_VERIFIED_CLAIMS = {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["claim", "result", "evidence", "finding_id"],
                "properties": {
                    "claim": {"type": "string"},
                    "result": {"type": "string",
                               "enum": ["holds", "refuted", "not-verifiable"]},
                    "evidence": {"type": "string"},
                    "finding_id": {"type": ["string", "null"]},
                },
            },
}


def response_schema(mode):
    """Structured output requires `required` to name every key in `properties`, so a
    mode that has no verified claims omits the key rather than making it optional."""
    schema = json.loads(json.dumps(_BASE_SCHEMA))
    if mode == "plan":
        schema["properties"]["verified_claims"] = json.loads(json.dumps(_VERIFIED_CLAIMS))
    schema["required"] = sorted(schema["properties"])
    return schema


def effective_policy_hash(synthesized_sha256):
    """Synthesized bytes plus the global sources as sampled at prepare (§3, A10).

    An unreadable source is recorded as unresolved, never omitted: an unknown input must not
    hash identically to an absent one.
    """
    parts = ["synthesized:" + synthesized_sha256]
    for source in GLOBAL_POLICY_SOURCES:
        path = os.path.expanduser(source)
        if not os.path.exists(path):
            parts.append(source + ":absent")
            continue
        try:
            parts.append(source + ":" + images.sha256_file(path))
        except OSError:
            parts.append(source + ":unresolved")
    return images.sha256_bytes("\n".join(parts).encode("utf-8"))


def codex_version(codex_path):
    try:
        completed = subprocess.run([codex_path, "--version"], capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL)
        return completed.stdout.strip() or None
    except OSError:
        return None


def build_prompt(job_dir, target):
    return rubric.build(
        job_dir=job_dir,
        mode=target["mode"],
        context_free=target.get("context_repository") is None,
        note=target.get("note"),
    )


def invoke(codex_path, job_dir, target, model, effort):
    """Plain `codex exec`, never `exec review` — the latter reviews the current repository (§3)."""
    schema_path = os.path.join(job_dir, "staging", "response.schema.json")
    prompt_path = os.path.join(job_dir, "prompt.txt")
    out_path = os.path.join(job_dir, "staging", "last-message.txt")
    log_path = os.path.join(job_dir, "logs", "codex.jsonl")
    for path in (schema_path, out_path, log_path):
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory)

    if os.path.exists(out_path):
        os.unlink(out_path)  # a retry must never adopt an earlier invocation's answer

    prompt = build_prompt(job_dir, target)
    schema = response_schema(target["mode"])
    schema_text = json.dumps(schema, indent=2, sort_keys=True)
    with open(prompt_path, "w", encoding="utf-8") as handle:
        handle.write(prompt)
    with open(schema_path, "w", encoding="utf-8") as handle:
        handle.write(schema_text)

    argv = [
        codex_path, "exec",
        "-s", "read-only",
        "-C", job_dir,
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "-m", model,
        "-c", "model_reasoning_effort=" + effort,
        "--output-schema", schema_path,
        "-o", out_path,
        "--json",
        prompt,
    ]
    started = time.time()
    # codex reads stdin for additional input, and an inherited one that never reaches EOF —
    # a pipe, a socket, a CI runner — blocks the review forever.
    completed = subprocess.run(argv, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL)
    elapsed = time.time() - started

    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(completed.stdout)

    raw = ""
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as handle:
            raw = handle.read()

    return {
        "raw": raw,
        "failed": completed.returncode != 0 or not raw.strip(),
        "returncode": completed.returncode,
        "stderr": completed.stderr,
        "elapsed_seconds": round(elapsed, 3),
        "prompt_sha256": images.sha256_bytes(prompt.encode("utf-8")),
        "schema_sha256": images.sha256_bytes(schema_text.encode("utf-8")),
        "retry_after": retry_after(completed.stdout),
    }


def retry_after(log_text, now=None):
    """The usage-limit reset codex named, as local ISO 8601, or None when it named none.

    Codex prints two forms: a same-day `try again at 3:35 PM`, and for a multi-day limit
    `try again at Sep 19th, 2026 10:08 AM`. A caller matching only the first gave up on the
    second, so the driver reads both once instead of every caller grepping the log.
    """
    import re
    from datetime import datetime, timedelta

    now = now or datetime.now()
    dated = re.findall(r"try again at ([A-Z][a-z]{2}) (\d{1,2})(?:st|nd|rd|th), (\d{4}) "
                       r"(\d{1,2}:\d{2} [AP]M)", log_text)
    if dated:
        month, day, year, clock = dated[-1]
        moment = datetime.strptime(f"{month} {day} {year} {clock}", "%b %d %Y %I:%M %p")
        return moment.astimezone().isoformat()
    timed = re.findall(r"try again at (\d{1,2}:\d{2} [AP]M)", log_text)
    if timed:
        clock = datetime.strptime(timed[-1], "%I:%M %p")
        moment = now.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
        if moment < now - timedelta(minutes=1):
            moment += timedelta(days=1)
        return moment.astimezone().isoformat()
    return None
