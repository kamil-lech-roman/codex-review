"""Durable reviewer transport and per-child timing evidence."""
import json
from pathlib import Path
import pytest

from codex_review import reviewer


def should_parse_streamed_log_records_once_across_unchanged_polls(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from codex_review import transport
    # given a verbose log already written while its child remains running across several polls
    thread = 'a2c80d10-8dbb-4426-9089-f1d0b3a88e23'
    log = (json.dumps(dict(type='thread.started', thread_id=thread)) + '\n' +
           json.dumps(dict(type='item.completed', noise='x' * 1024 * 1024)) + '\n').encode()
    def launch(argv, **kwargs):
        kwargs['stdout'].write(log)
        return SimpleNamespace(pid=12345, returncode=None)
    monkeypatch.setattr(transport.subprocess, 'Popen', launch)
    monkeypatch.setattr(transport, 'process_start', lambda pid: 'fixture-start')
    polls = iter([(0, 0, None)] * 4 + [(12345, 0, SimpleNamespace(ru_utime=0, ru_stime=0))])
    monkeypatch.setattr(transport.os, 'wait4', lambda *args: next(polls), raising=False)
    monkeypatch.setattr(transport.time, 'sleep', lambda seconds: None)
    loads, decoded = json.loads, []
    def observed_loads(data, *args, **kwargs):
        if isinstance(data, bytes) and b'"noise"' in data:
            decoded.append(len(data))
        return loads(data, *args, **kwargs)
    monkeypatch.setattr(transport.json, 'loads', observed_loads)
    # when polling to completion, then the unchanged large event is decoded only once
    result = transport.launch(['fixture'], str(tmp_path), str(tmp_path))
    assert result['thread_id'] == thread
    assert len(decoded) == 1


def should_track_partial_duplicate_and_conflicting_thread_events_incrementally(tmp_path):
    from codex_review import transport
    # given a thread-start event split across writes
    first = 'a2c80d10-8dbb-4426-9089-f1d0b3a88e23'
    second = '11111111-1111-4111-8111-111111111111'
    event = lambda identity: json.dumps(dict(type='thread.started', thread_id=identity)).encode()
    path = tmp_path / 'stdout.jsonl'
    path.write_bytes(event(first)[:30])
    reader = transport._ThreadLog()
    assert reader.read(str(path)) is None
    assert reader.read(str(path)) is None
    # when the event completes, duplicates and malformed events cannot change its identity
    with path.open('ab') as handle:
        handle.write(event(first)[30:] + b'\n')
    assert reader.read(str(path)) == first
    with path.open('ab') as handle:
        handle.write(b'not-json\nnull\n[]\n' + event('bad-uuid') + b'\n' + event(first) + b'\n')
    assert reader.read(str(path)) == first
    with path.open('ab') as handle:
        handle.write(event(second))
    assert reader.read(str(path)) == first
    # then a complete trailing record is considered at EOF, and conflicting identities become non-resumable
    assert reader.read(str(path), final=True) is None
    assert transport._thread(str(path)) is None
    path.write_bytes(event(second))
    assert reader.read(str(path), final=True) == second
    assert transport._thread(str(path)) == second


@pytest.mark.parametrize('kind', ['one-pass', 'cold', 'resume'])
@pytest.mark.parametrize('setting', ['relative', 'absolute', 'default', 'symlink'])
def should_launch_with_the_same_home_and_policy_as_qualification(tmp_path, monkeypatch, kind, setting):
    import os
    from codex_review import context, images, loop_driver
    # given a host-relative home and a distinct job cwd
    monkeypatch.chdir(tmp_path)
    user = tmp_path / 'user'
    home = user / '.codex'
    home.mkdir(parents=True)
    policy = home / 'AGENTS.md'
    policy.write_text('Synthetic home policy\n')
    monkeypatch.setenv('HOME', str(user))
    alias = tmp_path / 'home-alias'
    alias.symlink_to(home, target_is_directory=True)
    if setting == 'default':
        monkeypatch.delenv('CODEX_HOME', raising=False)
    else:
        monkeypatch.setenv('CODEX_HOME', {'relative': 'user/.codex', 'absolute': str(home), 'symlink': 'home-alias'}[setting])
    original_setting = os.environ.get('CODEX_HOME')
    executable = tmp_path / 'reviewer'
    executable.write_text('''#!/usr/bin/env python3
import hashlib, json, os, sys
args = sys.argv[1:]
home = os.path.realpath(os.environ.get('CODEX_HOME', os.path.expanduser('~/.codex')))
policy = os.path.join(home, 'AGENTS.md')
digest = hashlib.sha256(open(policy, 'rb').read()).hexdigest() if os.path.isfile(policy) else None
open(args[args.index('-o') + 1], 'w').write(json.dumps(dict(home=home, policy=digest)))
print('{"type":"thread.started","thread_id":"a2c80d10-8dbb-4426-9089-f1d0b3a88e23"}')
print('{"type":"turn.completed"}')
''')
    executable.chmod(0o755)
    job = tmp_path / 'job'
    job.mkdir()
    target = dict(mode='plan', context_repository=None)
    if kind != 'one-pass':
        target['loop_id'] = 'a' * 16
    qualified = loop_driver.launch_key(target, 'resume' if kind == 'resume' else 'cold-launch')['settings']
    cache = context.exclusion_key(dict(id='fixture', path=str(home), exclude=[], manifest={}), [],
                                  'one-pass' if kind == 'one-pass' else 'persistent')['settings']
    assert cache['execution_home'] == qualified['execution_home']
    resume = dict(thread_id='a2c80d10-8dbb-4426-9089-f1d0b3a88e23', prompt='fixture', schema={}) if kind == 'resume' else None
    # when the actual child starts in the job, then qualification, policy sampling and launch refer to one home
    result = reviewer.invoke(str(executable), str(job), target, 'test', 'max', resume=resume)
    observed = json.loads(result['raw'])
    assert observed['home'] == qualified['execution_home'] == str(home)
    assert observed['policy'] == qualified['global_policy_sources']['AGENTS.md'] == images.sha256_file(str(policy))
    invocation = json.loads((Path(result['attempt_dir']) / 'invocation.json').read_text())
    assert invocation['execution_home'] == str(home)
    assert os.environ.get('CODEX_HOME') == original_setting


def should_retain_each_attempt_and_bind_only_its_emitted_session(tmp_path):
    # given a reviewer that emits its own thread identity and a valid answer
    executable = tmp_path / 'reviewer'
    executable.write_text('''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
open(args[args.index('-o') + 1], 'w').write('{"verdict":"approve"}')
print('{"type":"thread.started","thread_id":"a2c80d10-8dbb-4426-9089-f1d0b3a88e23"}', flush=True)
print('{"type":"turn.completed"}', flush=True)
''')
    executable.chmod(0o755)
    job = tmp_path / 'job'
    job.mkdir()
    target = {'mode': 'plan', 'context_repository': None}
    # when two independent invocations occur
    first = reviewer.invoke(str(executable), str(job), target, 'test', 'xhigh')
    second = reviewer.invoke(str(executable), str(job), target, 'test', 'xhigh')
    # then both raw logs, launch identities and measured CPU survive separately
    assert first['attempt_dir'] != second['attempt_dir']
    for result in (first, second):
        assert result['thread_id'] == 'a2c80d10-8dbb-4426-9089-f1d0b3a88e23'
        directory = Path(result['attempt_dir'])
        assert (directory / 'stdout.jsonl').read_text().startswith('{"type":"thread.started"')
        launch = json.loads((directory / 'launch.json').read_text())
        completion = json.loads((directory / 'completion.json').read_text())
        assert launch['pid'] > 0
        assert completion['returncode'] == 0
        timing = result['timing']
        assert timing['wall_seconds'] >= 0
        assert timing['cpu_user_seconds'] >= 0
        assert timing['cpu_system_seconds'] >= 0
        assert timing['method'] == 'wait4'


def should_record_an_unlaunched_attempt_without_a_session_or_cpu_claim(tmp_path):
    # given an executable that cannot launch
    job = tmp_path / 'job'
    job.mkdir()
    # when invocation fails before Popen returns
    result = reviewer.invoke(str(tmp_path / 'absent'), str(job),
                             {'mode': 'plan', 'context_repository': None}, 'test', 'xhigh')
    # then evidence proves no process launched and measurements remain unknown
    assert result['launched'] is False
    assert result['thread_id'] is None
    assert result['timing']['cpu_user_seconds'] is None
    assert result['failed']


def should_validate_an_emitted_judgment_even_if_the_process_exits_nonzero(review, plan_file_factory, codex_reply):
    # given a complete judgment followed by a transport failure
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--no-verify', '--reason', 'inspection')
    codex_reply(dict(verdict='approve', findings=[], declared_scope=['A0001'], verified_claims=[]))
    # when the response is validated
    result = review('run', prepared.envelope['job_id'], fake={
        'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_RC': '3'})
    # then a usable judgment is retained, with the process failure still visible
    assert result.exit_code == 0, result
    assert result.envelope['payload']['run_metadata']['returncode'] == 3
