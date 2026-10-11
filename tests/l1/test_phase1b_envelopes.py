"""Loop protocol identity survives refusals at every dispatch layer."""
import pytest

from codex_review import loop_driver, main


@pytest.mark.parametrize('request_kind', ['prepare', 'loop', 'one-pass', 'unresolved'])
def should_format_only_identified_loop_responses_without_mutating_the_result(tmp_path, monkeypatch, request_kind):
    # given an original usage refusal and a parseable request with or without loop identity
    monkeypatch.setenv('CODEX_REVIEW_STATE', str(tmp_path))
    directory = tmp_path / ('a' * 16)
    directory.mkdir()
    if request_kind == 'loop':
        (directory / 'loop.json').write_text('{}')
    verb, argv = ('prepare', ['code', '--loop']) if request_kind == 'prepare' else (
        'status', [directory.name if request_kind != 'unresolved' else 'b' * 16])
    original = main.usage_error(verb, 'example', 'Example refusal')
    # when selecting the protocol, then preserve content and bind only an existing loop
    result = loop_driver.response_for_request(verb, argv, original)
    assert result['schema_version'] == (2 if request_kind in ('prepare', 'loop') else 1)
    assert result['job_id'] == (directory.name if request_kind == 'loop' else None)
    assert result['job_dir'] == (str(directory) if request_kind == 'loop' else None)
    assert result['error'] == original['error']
    assert original['schema_version'] == 1
    assert original['job_id'] is original['job_dir'] is None


@pytest.mark.parametrize('case', ['intent', 'pin', 'capture', 'preflight', 'arguments', 'override', 'generation'])
def should_keep_loop_protocol_on_preparation_and_operation_refusals(
        review, plan_file_factory, git_repo, state_root, case):
    # given either an unpublished loop request or an existing immutable checkpoint
    plan = plan_file_factory()
    identity = (None, None)
    before = None
    options = {}
    if case in ('intent', 'pin', 'capture', 'preflight'):
        argv = ['prepare', 'plan', str(plan), '--no-repo', '--loop']
        if case != 'intent':
            argv += ['--no-verify', '--reason', 'inspection']
        if case == 'pin':
            argv = ['prepare', 'code', '--loop', '--base', 'HEAD', '--no-verify', '--reason', 'inspection']
            options['cwd'] = git_repo
        if case == 'capture':
            argv[2] = str(plan.parent / 'missing.md')
        if case == 'preflight':
            options['codex_bin'] = None
        expected_exit = {'intent': 2, 'pin': 2, 'capture': 3, 'preflight': 7}[case]
    else:
        prepared = review('prepare', 'plan', str(plan), '--no-repo', '--loop',
                          '--no-verify', '--reason', 'inspection').envelope
        identity = (prepared['job_id'], prepared['job_dir'])
        before = {str(p): p.read_bytes() for p in state_root.rglob('*') if p.is_file()}
        argv = ['run', identity[0], '--round', prepared['payload']['round'], '--expected-generation', '0']
        if case == 'arguments':
            argv = ['status', identity[0], '--base', 'HEAD']
        elif case == 'override':
            argv += ['--effort', 'max']
        else:
            argv[-1] = '99'
        expected_exit = 8 if case == 'generation' else 2
    # when a refusal occurs, then its protocol and resolved identity remain truthful
    result = review(*argv, **options)
    assert result.exit_code == expected_exit, result
    assert result.envelope['schema_version'] == 2
    assert (result.envelope['job_id'], result.envelope['job_dir']) == identity
    if before is not None:
        assert {str(p): p.read_bytes() for p in state_root.rglob('*') if p.is_file()} == before


def should_leave_one_pass_and_unresolved_handle_refusals_on_the_one_pass_protocol(review, plan_file_factory):
    # given an ordinary job and a nonexistent handle
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo',
                      '--no-verify', '--reason', 'inspection').envelope
    # when invalid arguments refuse before one-pass resolution, then no loop identity is invented
    for handle in (prepared['job_id'], 'a' * 16):
        result = review('status', handle, '--base', 'HEAD')
        assert result.exit_code == 2, result
        assert result.envelope['schema_version'] == 1
        assert result.envelope['job_id'] is result.envelope['job_dir'] is None
