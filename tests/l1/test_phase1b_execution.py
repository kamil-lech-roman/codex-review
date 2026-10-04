"""Author executions need qualified settings and fresh, bound cache allocations."""
import json
from pathlib import Path
import pytest

from codex_review import evidence, execution


@pytest.mark.parametrize('flag', ['-E', '-I'])
def should_refuse_a_redirect_bypassing_command_under_ordinary_command_qualification(
        review, plan_file_factory, state_root, tmp_path, flag):
    import os
    import py_compile
    import shlex
    import subprocess
    import sys
    from qualification_fixture import qualify_fixture, qualify_command
    # given timestamp-valid stale bytecode and ordinary runtime qualification
    source = tmp_path / 'seeded.py'
    source.write_text('VALUE = 2\n')
    timestamp = source.stat().st_mtime_ns
    py_compile.compile(str(source), cfile=str(tmp_path / '__pycache__' / ('seeded.' + sys.implementation.cache_tag + '.pyc')), doraise=True)
    source.write_text('VALUE = 1\n')
    os.utime(source, ns=(timestamp, timestamp))
    expression = 'import sys; sys.path.insert(0, {!r}); import seeded; print(seeded.VALUE)'.format(str(tmp_path))
    environment = dict(os.environ, PYTHONPYCACHEPREFIX=str(tmp_path / 'redirected'))
    assert subprocess.check_output([sys.executable, '-c', expression], env=environment, text=True).strip() == '1'
    assert subprocess.check_output([sys.executable, flag, '-c', expression], env=environment, text=True).strip() == '2'
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--no-verify', '--reason', 'cache control')
    job = Path(prepared.envelope['job_dir'])
    target = json.loads((job / 'target.json').read_text())
    qualify_fixture(state_root, job, execution.key(target))
    slot = 'staging/probe-F1.sh'
    ordinary = shlex.join([sys.executable, '-c', expression]) + '\n'
    qualify_command(job, ordinary)
    (job / slot).write_text(ordinary)
    original = evidence.seal(str(job), job.name, slot)['evidence_id']
    assert execution.prepare(str(job), target, original)['ready']
    (job / slot).write_text(shlex.join([sys.executable, flag, '-c', expression]) + '\n')
    command = evidence.seal(str(job), job.name, slot)['evidence_id']
    # when this different launch profile is sealed, then ordinary-command controls cannot authorize it
    result = execution.prepare(str(job), target, command)
    assert not result['ready']
    assert result['reason'] == 'qualification-required'


def should_exclude_shell_startup_injection_and_requalify_runtime_selection(
        review, plan_file_factory, state_root, tmp_path, monkeypatch):
    import os
    import subprocess
    from qualification_fixture import qualify_fixture
    # given qualified author settings and a shell startup file that defeats both redirects
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--no-verify', '--reason', 'environment control')
    job = Path(prepared.envelope['job_dir'])
    target = json.loads((job / 'target.json').read_text())
    qualify_fixture(state_root, job, execution.key(target))
    startup = tmp_path / 'startup.sh'
    startup.write_text('unset PYTHONPYCACHEPREFIX\nexport NUMBA_CACHE_DIR=/unexpected\n')
    slot = 'staging/probe-F1.sh'
    (job / slot).write_text('printf "%s\\n%s\\n" "${PYTHONPYCACHEPREFIX-unset}" "$NUMBA_CACHE_DIR"\n')
    command = evidence.seal(str(job), job.name, slot)['evidence_id']
    from qualification_fixture import qualify_command
    qualify_command(job, (job / slot).read_text())
    launch = execution.prepare(str(job), target, command)
    injected = dict(os.environ, BASH_ENV=str(startup))
    # when running the qualified argv with later ambient startup injection
    result = subprocess.run(launch['argv'], cwd=launch['cwd'], env=injected, capture_output=True, text=True, check=True)
    # then only the driver-declared redirects reach the command
    assert result.stdout.splitlines() == [launch['environment']['PYTHONPYCACHEPREFIX'], launch['environment']['NUMBA_CACHE_DIR']]
    # and selecting a different runtime search path needs new controls before another execution
    monkeypatch.setenv('PATH', '/different-runtime:' + os.environ['PATH'])
    changed = execution.prepare(str(job), target, command)
    assert not changed['ready']
    assert changed['reason'] == 'qualification-required'


def should_seal_without_granting_unqualified_execution(review, plan_file_factory):
    # given a new job and a probe script without cache controls
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--no-verify', '--reason', 'inspection')
    job = Path(prepared.envelope['job_dir'])
    (job / 'staging').mkdir(exist_ok=True)
    (job / 'staging' / 'probe-F001.sh').write_text('python -c "print(1)"\n')
    command = evidence.seal(str(job), job.name, 'staging/probe-F001.sh')['evidence_id']
    target = json.loads((job / 'target.json').read_text())
    # when requesting an execution binding, then evidence collection is retained but launch is unqualified
    result = execution.prepare(str(job), target, command)
    assert not result['ready']
    assert result['reason'] == 'qualification-required'
    assert result['qualification']['key']['capability'] == 'cache-execution'
    assert not (job / 'executions').exists()


def should_allocate_distinct_empty_cache_directories_for_each_execution(review, plan_file_factory, state_root):
    from qualification_fixture import qualify_fixture
    # given passing fixture controls for exactly this author launch policy
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--no-verify', '--reason', 'inspection')
    job = Path(prepared.envelope['job_dir'])
    target = json.loads((job / 'target.json').read_text())
    qualify_fixture(state_root, job, execution.key(target))
    (job / 'staging' / 'probe-F001.sh').write_text('python -c "print(1)"\n')
    command = evidence.seal(str(job), job.name, 'staging/probe-F001.sh')['evidence_id']
    # when preparing two executions of the same sealed command
    from qualification_fixture import qualify_command
    qualify_command(job, (job / 'staging/probe-F001.sh').read_text())
    first = execution.prepare(str(job), target, command)
    second = execution.prepare(str(job), target, command)
    # then scratch locators differ while the bound policy is identical
    assert first['ready'] and second['ready']
    assert first['execution_id'] != second['execution_id']
    assert first['environment'] != second['environment']
    for item in (first, second):
        assert item['cache_policy'] == target['cache_policy']
        assert set(item['environment']) == {'PYTHONPYCACHEPREFIX', 'NUMBA_CACHE_DIR'}
        assert all(Path(path).is_dir() and list(Path(path).iterdir()) == [] for path in item['environment'].values())
        assert execution.validate(str(job), target, command, item['execution_id'])['environment'] == item['environment']


def should_refuse_unqualified_executed_probe_recording(review, plan_file_factory, codex_reply):
    from test_record_probes import RESPONSE, report_sha
    # given syntactically sound sealed probe evidence, but no cache-execution qualification
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--no-verify', '--reason', 'inspection')
    job = Path(prepared.envelope['job_dir'])
    codex_reply(RESPONSE)
    assert review('run', job.name, fake={'CODEX_FAKE_RESPONSE': codex_reply.path}).exit_code == 0
    citations = {}
    for kind, slot, data in [('command', 'staging/probe-F-1.sh', 'echo checked\n'),
                             ('output', 'probes/F-1.out', 'checked\n'), ('rc', 'probes/F-1.rc', '0\n')]:
        (job / slot).parent.mkdir(exist_ok=True)
        (job / slot).write_text(data)
        sealed = review('seal', job.name, slot)
        assert sealed.exit_code == 0
        citations[kind + '_evidence_id'] = sealed.envelope['payload']['evidence_id']
    annotation = dict(finding_id='F-1', disposition='rejected', read='unsupported', probe_execution='executed',
                      rationale='The asserted failure was not reproduced', **citations)
    (job / 'staging' / 'probes.json').write_text(json.dumps(dict(report_sha256=report_sha(job), annotations=[annotation])))
    # when evidence is submitted, then sealing alone cannot make the execution qualified
    result = review('record-probes', job.name, '--probes-file', 'staging/probes.json')
    assert result.exit_code == 18, result
    assert result.envelope['error']['details']['reason'] == 'qualification-required'
    assert not (job / 'probes.json').exists()


def should_read_source_instead_of_seeded_stale_bytecode_with_a_fresh_prefix(tmp_path):
    import os
    import py_compile
    import subprocess
    import sys
    # given a valid in-tree pyc whose timestamp and size still match changed source
    source = tmp_path / 'seeded.py'
    source.write_text('VALUE = 2\n')
    timestamp = source.stat().st_mtime_ns
    cached = tmp_path / '__pycache__' / ('seeded.' + sys.implementation.cache_tag + '.pyc')
    cached.parent.mkdir()
    py_compile.compile(str(source), cfile=str(cached), doraise=True)
    source.write_text('VALUE = 1\n')
    os.utime(source, ns=(timestamp, timestamp))
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    environment = {k: v for k, v in os.environ.items() if k not in ('PYTHONPYCACHEPREFIX', 'NUMBA_CACHE_DIR')}
    command = [sys.executable, '-c', 'import seeded; print(seeded.VALUE)']
    # when importing with in-tree cache reads and then a fresh redirected prefix
    baseline = subprocess.run([sys.executable, '-c', 'import sys; sys.pycache_prefix = None; import seeded; print(seeded.VALUE)'],
                              cwd=tmp_path, env=environment, capture_output=True, text=True, check=True)
    fresh = tmp_path.parent / (tmp_path.name + '-fresh-cache')
    redirected = subprocess.run(command, cwd=tmp_path,
        env=dict(environment, PYTHONPYCACHEPREFIX=str(fresh)), capture_output=True, text=True, check=True)
    # then the control distinguishes stale bytecode consumption from source evaluation without changing bound inputs
    assert baseline.stdout == '2\n'
    assert redirected.stdout == '1\n'
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
