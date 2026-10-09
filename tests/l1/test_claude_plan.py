import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPORT = {
    'verdict': 'approve', 'findings': [], 'declared_scope': ['A0001'],
    'verified_claims': [],
}
AUTH = {
    'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty',
    'subscriptionType': 'pro', 'apiKeySource': None,
}
INIT = {
    'type': 'system', 'subtype': 'init', 'model': 'claude-fable-test',
    'tools': ['Glob', 'Grep', 'Read', 'StructuredOutput'], 'permissionMode': 'dontAsk',
}


@pytest.fixture
def claude(tmp_path):
    binary = tmp_path / 'claude'
    binary.write_text('#!' + sys.executable + '\n' + '''
import json, os, signal, sys, time
from pathlib import Path
root = Path(__file__).parent
config = json.loads((root / 'fake.json').read_text())
argv = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as log:
    log.write(json.dumps({'argv': argv, 'env_names': sorted(os.environ), 'cwd': os.getcwd()}) + '\\n')
if 'auth' in argv:
    print(json.dumps(config['auth']))
    sys.exit(config.get('auth_rc', 0))
(root / 'stdin.txt').write_text(sys.stdin.read())
if config.get('spawn_child'):
    if os.fork() == 0:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        (root / 'child.pid').write_text(str(os.getpid()))
        time.sleep(60)
        os._exit(0)
    while not (root / 'child.pid').exists():
        time.sleep(.01)
for event in config['events']:
    print(json.dumps(event, ensure_ascii=False), flush=True)
time.sleep(config.get('sleep', 0))
print('fake stderr preserved', file=sys.stderr)
sys.exit(config.get('rc', 0))
''')
    binary.chmod(0o755)
    repo = tmp_path / 'repo'
    repo.mkdir()
    plan = repo / 'plan.md'
    plan.write_bytes(b'# Plan\r\nImplement a small launcher.\r\n')
    config = {'auth': dict(AUTH), 'events': [dict(INIT), {
        'type': 'result', 'subtype': 'success', 'is_error': False,
        'structured_output': REPORT, 'result': '',
        'modelUsage': {'auxiliary-model': {}, 'claude-fable-test': {}},
    }]}
    output = tmp_path / 'round'

    def run(*extra, cwd=None, plan_arg=None):
        (tmp_path / 'fake.json').write_text(json.dumps(config))
        result = subprocess.run([
            '/bin/bash', str(ROOT / 'claude-review.sh'), str(plan) if plan_arg is None else plan_arg,
            '--claude-bin', str(binary), '--output', str(output), *extra,
        ], capture_output=True, text=True, timeout=15, cwd=cwd)
        return result

    def calls():
        path = tmp_path / 'calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    run.config, run.repo, run.plan = config, repo, plan
    run.output, run.calls = output, calls
    return run


def should_capture_plan_and_run_fable_high_with_shared_contract(claude):
    """Given a plan; when the CLI answers; then the captured target and report survive."""
    result = claude('--repo', str(claude.repo))

    assert result.returncode == 0, result.stderr + result.stdout
    record = json.loads((claude.output / 'run.json').read_text())
    target = json.loads((claude.output / 'target.json').read_text())
    assert (claude.output / 'plan.md').read_bytes() == claude.plan.read_bytes()
    assert target['artifacts']['A0001']['sha256'] == hashlib.sha256(claude.plan.read_bytes()).hexdigest()
    assert target['context_repository'] == str(claude.repo)
    assert record['status'] == 'awaiting-adjudication'
    assert record['requested_model'] == 'fable'
    assert record['effort'] == 'high'
    assert record['resolved_model'] == 'claude-fable-test'
    assert json.loads((claude.output / 'report.json').read_text()) == REPORT
    calls = claude.calls()
    assert len(calls) == 2
    assert calls[0]['argv'][-3:] == ['auth', 'status', '--json']
    argv = calls[1]['argv']
    for flag, expected in {
        '--model': 'fable', '--effort': 'high', '--tools': 'Read,Glob,Grep',
        '--allowedTools': 'Read,Glob,Grep', '--setting-sources': '',
        '--mcp-config': '{"mcpServers":{}}', '--settings': '{}',
        '--permission-mode': 'dontAsk', '--permission-prompts': 'none',
        '--add-dir': str(claude.repo), '--output-format': 'stream-json',
    }.items():
        assert argv[argv.index(flag) + 1] == expected
    for flag in ('--safe-mode', '--restricted', '--strict-mcp-config',
                 '--no-session-persistence', '--print', '--verbose'):
        assert flag in argv
    schema = json.loads(argv[argv.index('--json-schema') + 1])
    assert 'verified_claims' in schema['required']
    assert calls[1]['cwd'] == str(claude.output)
    assert (claude.output.parent / 'stdin.txt').read_text() == (claude.output / 'brief.txt').read_text()
    assert (claude.output / 'stderr.txt').read_text() == 'fake stderr preserved\n'


def should_allowlist_child_environment(claude, monkeypatch):
    """Given inherited session and API settings; when launching; then neither reaches Claude."""
    for name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'CLAUDE_EFFORT',
                 'CLAUDE_CODE_CHILD_SESSION', 'USE_LOCAL_OAUTH', 'UNRELATED_SECRET'):
        monkeypatch.setenv(name, 'synthetic-value')

    result = claude('--no-repo')

    assert result.returncode == 0, result.stdout
    for call in claude.calls():
        assert set(call['env_names']) - {'__CF_USER_TEXT_ENCODING'} <= {
            'HOME', 'PATH', 'USER', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TMPDIR',
            'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
            'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
        }
        assert {'HOME', 'PATH'} <= set(call['env_names'])


@pytest.mark.parametrize('change', [
    {'loggedIn': False}, {'authMethod': 'api_key'}, {'apiProvider': 'bedrock'},
    {'subscriptionType': None}, {'apiKeySource': 'ANTHROPIC_API_KEY'},
])
def should_refuse_non_subscription_identity_before_model_call(claude, change):
    """Given an unsuitable identity; when preflight runs; then no model request starts."""
    claude.config['auth'].update(change)

    result = claude('--no-repo')

    assert result.returncode == 7
    assert len(claude.calls()) == 1
    record = json.loads((claude.output / 'run.json').read_text())
    assert record['status'] == 'incomplete'
    assert 'subscription' in record['error']
    assert not (claude.output / 'report.json').exists()


def should_reject_failed_preflight_despite_its_success_shaped_output(claude):
    """Given an auth command failure; when its output looks suitable; then it still refuses."""
    claude.config['auth_rc'] = 1

    result = claude('--no-repo')

    assert result.returncode == 7
    assert len(claude.calls()) == 1
    assert json.loads((claude.output / 'run.json').read_text())['status'] == 'incomplete'


@pytest.mark.parametrize('failure', ['process', 'terminal', 'missing-result', 'missing-structured',
                                    'incoherent', 'context-free-claim', 'bad-types'])
def should_keep_raw_evidence_without_approval_on_invalid_response(claude, failure):
    """Given a failed or incoherent answer; when normalized; then evidence survives but no report passes."""
    terminal = claude.config['events'][-1]
    terminal['structured_output'] = dict(REPORT)
    if failure == 'process':
        claude.config['rc'] = 1
    elif failure == 'terminal':
        terminal.update(is_error=True, subtype='error_during_execution')
    elif failure == 'missing-result':
        claude.config['events'].pop()
    elif failure == 'missing-structured':
        terminal.pop('structured_output')
    elif failure == 'incoherent':
        terminal['structured_output']['verdict'] = 'changes-required'
    elif failure == 'context-free-claim':
        terminal['structured_output']['verified_claims'] = [
            {'claim': 'Runs', 'result': 'holds', 'evidence': 'assumed', 'finding_id': None}]
    elif failure == 'bad-types':
        terminal['structured_output']['declared_scope'] = [{}]
    claude.config['events'].insert(1, {
        'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Original review text'}]},
    })

    result = claude('--no-repo')

    assert result.returncode == 8, result.stdout + result.stderr
    record = json.loads((claude.output / 'run.json').read_text())
    assert record['status'] == 'incomplete'
    assert (claude.output / 'report.txt').read_text() == 'Original review text\n'
    assert 'Original review text' in (claude.output / 'stream.jsonl').read_text()
    assert not (claude.output / 'report.json').exists()


@pytest.mark.parametrize('failure', ['unexpected-tool', 'missing-init', 'permission-mode', 'denial', 'tool-error'])
def should_refuse_reviews_with_unexpected_capabilities_or_inspection_errors(claude, failure):
    """Given evidence of capability drift or an unsuccessful read; when finishing; then review is incomplete."""
    if failure == 'unexpected-tool':
        claude.config['events'][0]['tools'] = ['Read', 'Glob', 'Grep', 'Bash', 'StructuredOutput']
    elif failure == 'missing-init':
        claude.config['events'].pop(0)
    elif failure == 'permission-mode':
        claude.config['events'][0]['permissionMode'] = 'bypassPermissions'
    elif failure == 'denial':
        claude.config['events'][-1]['permission_denials'] = [{'tool_name': 'Read'}]
    elif failure == 'tool-error':
        claude.config['events'].insert(1, {'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'is_error': True, 'content': 'Access denied'}]}})

    result = claude('--no-repo')

    assert result.returncode == 8, result.stdout
    assert not (claude.output / 'report.json').exists()
    assert json.loads((claude.output / 'run.json').read_text())['status'] == 'incomplete'


def should_bound_a_review_and_preserve_its_partial_output(claude):
    """Given a reviewer that does not finish; when the deadline expires; then its answer is not approved."""
    claude.config['sleep'] = 2

    result = claude('--no-repo', '--timeout', '0.4')

    assert result.returncode == 8, result.stdout + result.stderr
    record = json.loads((claude.output / 'run.json').read_text())
    assert record['timed_out'] is True
    assert record['returncode'] != 0
    assert record['status'] == 'incomplete'
    assert (claude.output / 'stream.jsonl').read_text()
    assert not (claude.output / 'report.json').exists()


def should_refuse_evidence_inside_the_context_repository(claude):
    """Given an output under the context root; when preparing; then the target stays untouched."""
    output = claude.repo / 'evidence'

    result = claude('--repo', str(claude.repo), '--output', str(output))

    assert result.returncode == 7
    assert not output.exists()
    assert claude.calls() == []


def should_preserve_an_existing_round_without_relaunching(claude):
    """Given completed evidence; when the output directory is reused; then it remains byte-identical."""
    assert claude('--no-repo').returncode == 0
    before = {path.name: path.read_bytes() for path in claude.output.iterdir()}

    result = claude('--no-repo')

    assert result.returncode == 7
    assert len(claude.calls()) == 2
    assert {path.name: path.read_bytes() for path in claude.output.iterdir()} == before


def should_accept_a_valid_finding_and_explicit_model_effort(claude):
    """Given a grounded finding; when a caller selects Opus; then the finding and selection survive."""
    report = {
        'verdict': 'changes-required', 'declared_scope': ['A0001'],
        'findings': [{'id': 'F1', 'classification': 'introduced', 'severity': 'major',
                      'claim': 'Missing input', 'failure_scenario': 'Cannot run',
                      'evidence': 'plan.md line 1', 'artifact': 'A0001', 'suggested_fix': 'Add input'}],
        'verified_claims': [],
    }
    claude.config['events'][-1]['structured_output'] = report

    result = claude('--no-repo', '--model', 'opus', '--effort', 'max')

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((claude.output / 'report.json').read_text()) == report
    argv = claude.calls()[-1]['argv']
    assert argv[argv.index('--model') + 1] == 'opus'
    assert argv[argv.index('--effort') + 1] == 'max'
    assert '--add-dir' not in argv


@pytest.mark.parametrize('shadow', ['package', 'stdlib'])
def should_ignore_imports_from_the_callers_repository_and_keep_relative_paths(claude, shadow):
    """Given hostile caller imports; when reviewing a relative path; then only the trusted launcher executes."""
    marker = claude.repo / 'shadow-executed'
    payload = 'from pathlib import Path\nPath("shadow-executed").write_text("yes")\n'
    if shadow == 'package':
        package = claude.repo / 'codex_review'
        package.mkdir()
        (package / '__init__.py').write_text(payload)
        (package / 'claude_plan.py').write_text('')
    else:
        (claude.repo / 'json.py').write_text(payload)

    result = claude('--no-repo', cwd=claude.repo, plan_arg='plan.md')

    assert not marker.exists()
    assert result.returncode == 0, result.stdout + result.stderr
    assert (claude.output / 'plan.md').read_bytes() == claude.plan.read_bytes()


def should_kill_same_group_children_even_when_the_leader_exits_on_term(claude):
    """Given a TERM-resistant child; when its leader exits at timeout; then group cleanup still reaches it."""
    claude.config.update(spawn_child=True, sleep=2)
    child = None
    try:
        result = claude('--no-repo', '--timeout', '0.4')
        child = int((claude.output.parent / 'child.pid').read_text())
        assert result.returncode == 8, result.stdout + result.stderr
        deadline = time.monotonic() + 3
        while True:
            state = subprocess.run(['/bin/ps', '-p', str(child), '-o', 'stat='],
                                   capture_output=True, text=True).stdout.strip()
            if not state or state.startswith('Z') or time.monotonic() >= deadline:
                break
            time.sleep(.02)
        assert not state or state.startswith('Z'), state
    finally:
        if child is not None:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass


def should_preserve_raw_unicode_separators_inside_jsonl_strings(claude):
    """Given Unicode separators inside a JSON string; when parsing JSONL; then the text remains one record."""
    narrative = 'Alpha\u0085Beta\u2028Gamma\u2029Delta'
    claude.config['events'].insert(1, {
        'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': narrative}]},
    })

    result = claude('--no-repo')

    assert narrative.encode('utf-8') in (claude.output / 'stream.jsonl').read_bytes()
    assert result.returncode == 0, result.stdout + result.stderr
    assert (claude.output / 'report.txt').read_text() == narrative + '\n'
    assert json.loads((claude.output / 'report.json').read_text()) == REPORT
