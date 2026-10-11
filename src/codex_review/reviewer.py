"""Section 3 — invoking Codex over the frozen target."""

import json
import os
from datetime import datetime

from codex_review import images, rubric, state, transport

DEFAULT_MODEL = "gpt-6-astra"
DEFAULT_EFFORT = "xhigh"

# The global instruction sources Codex additionally loads (§3, A10 — sampled, not attested).
GLOBAL_POLICY_SOURCES = ("AGENTS.md", "AGENTS.override.md")

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


def execution_home():
    return os.path.realpath(os.environ.get('CODEX_HOME', os.path.expanduser('~/.codex')))


def global_policy_sources():
    """Sample instruction sources in the configured execution home, including absence."""
    home = execution_home()
    sources = {}
    for source in GLOBAL_POLICY_SOURCES:
        path = os.path.join(home, source)
        if not os.path.lexists(path):
            sources[source] = 'absent'
            continue
        try:
            sources[source] = images.sha256_file(path)
        except OSError:
            sources[source] = 'unresolved'
    return sources


def effective_policy_hash(synthesized_sha256):
    """Sampled instruction identity, not an attestation of what the CLI loaded."""
    return images.sha256_bytes(json.dumps(dict(synthesized=synthesized_sha256,
                                               global_sources=global_policy_sources()), sort_keys=True).encode('utf-8'))


def build_prompt(job_dir, target):
    return rubric.build(
        job_dir=job_dir,
        mode=target["mode"],
        context_free=target.get("context_repository") is None,
        note=target.get("note"),
    )


def invoke(codex_path, job_dir, target, model, effort, attempt_id=None, resume=None):
    """Plain `codex exec`, never `exec review` — the latter reviews the current repository (§3)."""
    os.makedirs(os.path.join(job_dir, "staging"), exist_ok=True)
    attempt_dir = os.path.join(job_dir, "reviewer-attempts", attempt_id or state.new_nonce())
    os.makedirs(attempt_dir, exist_ok=False)
    schema_path = os.path.join(attempt_dir, "response.schema.json")
    prompt_path = os.path.join(job_dir, "prompt.txt")
    out_path = os.path.join(attempt_dir, "last-message.txt")
    log_path = os.path.join(job_dir, "logs", "codex.jsonl")
    for path in (schema_path, out_path, log_path):
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory)

    if os.path.exists(out_path):
        os.unlink(out_path)  # a retry must never adopt an earlier invocation's answer

    prompt = resume['prompt'] if resume else build_prompt(job_dir, target)
    schema = resume['schema'] if resume else response_schema(target["mode"])
    schema_text = json.dumps(schema, indent=2, sort_keys=True)
    if not resume:
        with open(prompt_path, "w", encoding="utf-8") as handle:
            handle.write(prompt)
    with open(schema_path, "w", encoding="utf-8") as handle:
        handle.write(schema_text)

    argv = [
        codex_path, "exec",
        "-s", "read-only",
        "-C", job_dir,
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "-m", model,
        "-c", "model_reasoning_effort=" + effort,
        "--output-schema", schema_path,
        "-o", out_path,
        "--json",
        prompt,
    ]
    if not target.get("loop_id"):
        argv.insert(2, "--ephemeral")
    if resume:
        argv = [codex_path, 'exec', 'resume', '-c', 'sandbox_mode="read-only"',
                '--skip-git-repo-check', '--ignore-user-config', '--ignore-rules',
                '-m', model, '-c', 'model_reasoning_effort=' + effort, '--output-schema', schema_path,
                '-o', out_path, '--json', resume['thread_id'], prompt]
    with open(os.path.join(attempt_dir, 'prompt.txt'), 'w', encoding='utf-8') as handle:
        handle.write(prompt)
    home = execution_home()
    state.atomic_json(os.path.join(attempt_dir, 'invocation.json'),
                      dict(model=model, effort=effort, codex_path=codex_path, cwd=job_dir,
                           execution_home=home, kind='rebuttal' if resume else 'cold'))
    transport.launch(argv, attempt_dir, job_dir, env=dict(os.environ, CODEX_HOME=home))
    return collected(attempt_dir)


def _attempt_bytes(attempt_dir, name, allow_missing):
    try:
        with open(os.path.join(attempt_dir, name), 'rb') as handle:
            return handle.read()
    except FileNotFoundError:
        if not allow_missing:
            raise
        return None


def collected(attempt_dir, *, result=None, publish_log=True):
    if result is None:
        with open(os.path.join(attempt_dir, 'completion.json'), encoding='utf-8') as handle:
            result = json.load(handle)
    with open(os.path.join(attempt_dir, 'invocation.json'), encoding='utf-8') as handle:
        invocation = json.load(handle)
    job_dir = invocation['cwd']
    out_path = os.path.join(attempt_dir, 'last-message.txt')
    log_path = os.path.join(job_dir, 'logs', 'codex.jsonl')
    recovered = result.get('completion_evidence') == 'quiescent-valid-answer'
    prompt = _attempt_bytes(attempt_dir, 'prompt.txt', recovered)
    schema_text = _attempt_bytes(attempt_dir, 'response.schema.json', recovered)
    stdout = _attempt_bytes(attempt_dir, 'stdout.jsonl', recovered)
    stderr = (_attempt_bytes(attempt_dir, 'stderr.txt', recovered) or b'').decode('utf-8', errors='replace')
    if publish_log and invocation.get('kind') != 'rebuttal':
        with open(log_path, "wb") as handle:
            handle.write(stdout)
    if result["error"]:
        stderr += result["error"]

    raw = b""
    if os.path.exists(out_path):
        with open(out_path, "rb") as handle:
            raw = handle.read()

    return {
        "raw": raw,
        "failed": not raw.strip(),
        "returncode": result["returncode"],
        "stderr": stderr,
        "elapsed_seconds": result["timing"]["wall_seconds"],
        "timing": result["timing"],
        "launched": result["launched"],
        "thread_id": result["thread_id"],
        "attempt_dir": attempt_dir,
        "prompt_sha256": images.sha256_bytes(prompt) if prompt is not None else None,
        "schema_sha256": images.sha256_bytes(schema_text) if schema_text is not None else None,
        "completion_evidence": result.get('completion_evidence', 'receipt'),
        "retry_after": None if result.get('completion_evidence') == 'quiescent-valid-answer' else retry_after(stdout.decode("utf-8", errors="replace"),
                                   now=datetime.fromtimestamp(result.get('completed_at',
                                       os.path.getmtime(os.path.join(attempt_dir, 'completion.json'))))),
    }


def unavailable_timing():
    return dict(wall_seconds=None, cpu_user_seconds=None, cpu_system_seconds=None,
                method='unavailable-after-interruption', process_scope='reviewer child', complete=False)


def published_answer(job_dir, attempt_dir, target, attempt, receipt=None):
    from codex_review import response
    rebuttal = attempt.get('kind') == 'rebuttal'
    path = os.path.join(attempt_dir, 'adjudication.json') if rebuttal else os.path.join(job_dir, 'report.json')
    if not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as handle:
        report = json.load(handle)
    if rebuttal:
        from codex_review import adjudication
        with open(os.path.join(job_dir, 'report.json'), encoding='utf-8') as handle:
            original = json.load(handle)
        raw = json.dumps(report).encode('utf-8')
        adjudication.validate(raw, target, original, [c['id'] for c in attempt['claims']])
        # This attempt-local publication follows exact-thread validation; only a receipt establishes timing.
        thread_id, timing = attempt['thread_id'], receipt['timing'] if receipt else unavailable_timing()
    else:
        response._require(isinstance(report, dict), 'bad-published-report', 'published judgment must be an object')
        metadata = report.get('run_metadata', {})
        response._require(isinstance(metadata, dict), 'bad-published-metadata', 'published judgment needs object metadata')
        response._require(metadata.get('attempt_dir') == attempt_dir, 'attempt-mismatch',
                          'published judgment belongs to another attempt')
        raw = json.dumps({k: v for k, v in report.items() if k != 'run_metadata'}).encode('utf-8')
        response.validate(raw, target)
        thread_id, timing = metadata.get('thread_id'), metadata['timing']
    return dict(raw=raw, failed=False, launched=True, attempt_dir=attempt_dir,
                thread_id=thread_id, timing=timing, retry_after=None, completion_evidence='published-judgment')


def answer_without_receipt(attempt_dir, target, attempt):
    """A quiescent child can leave a valid answer without surviving timing/exit accounting."""
    from codex_review import response
    try:
        with open(os.path.join(attempt_dir, 'last-message.txt'), encoding='utf-8') as handle:
            raw = handle.read()
        if attempt.get('kind') == 'rebuttal':
            from codex_review import adjudication
            with open(os.path.join(os.path.dirname(os.path.dirname(attempt_dir)), 'report.json'), encoding='utf-8') as handle:
                original = json.load(handle)
            adjudication.validate(raw, target, original, [c['id'] for c in attempt['claims']])
        else:
            response.validate(raw, target)
        result = dict(launched=True, returncode=None, error=None,
                      thread_id=(transport._thread(os.path.join(attempt_dir, 'stdout.jsonl'))
                                 if os.path.isfile(os.path.join(attempt_dir, 'stdout.jsonl')) else None),
                      completion_evidence='quiescent-valid-answer',
                      timing=unavailable_timing())
    except (OSError, ValueError, KeyError, response.ContractViolation):
        return None
    return collected(attempt_dir, result=result, publish_log=False)


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
