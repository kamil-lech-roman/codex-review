"""A crash between publishing a loop and its round must remain discoverable."""
import os

import pytest

from codex_review import loop_driver, main, state


def should_not_republish_a_round_already_recovered_by_another_writer(
        review, plan_file_factory, state_root, fake_codex, monkeypatch):
    from codex_review import loops
    # given a second writer recovering the visible loop before prepare regains its lock
    monkeypatch.setenv('CODEX_REVIEW_STATE', str(state_root))
    monkeypatch.setenv('CODEX_BIN', str(fake_codex))
    create = loops.create
    def recovered_before_return(*args):
        directory = create(*args)
        loop_driver.recover_publication(directory, loops.read(directory))
        return directory
    monkeypatch.setattr(loops, 'create', recovered_before_return)
    argv = ['plan', str(plan_file_factory()), '--no-repo', '--loop', '--no-verify', '--reason', 'inspection']
    _, options, _ = main.parse_common(argv)
    # when the original prepare resumes, then it accepts the identical publication without replaying it
    prepared = loop_driver.prepare(argv, options)
    assert prepared['exit'] == 0
    assert prepared['payload']['generation'] == 1
    assert prepared['payload']['stage'] == 'prepared'


@pytest.mark.parametrize('finish', ['continue', 'abandon'])
def should_recover_initial_round_publication_without_recapturing(review, plan_file_factory, state_root, fake_codex, monkeypatch, finish):
    # given capture that finishes writing but is interrupted before the round rename
    monkeypatch.setenv('CODEX_REVIEW_STATE', str(state_root))
    monkeypatch.setenv('CODEX_BIN', str(fake_codex))
    plan = plan_file_factory('the captured version\n')
    argv = ['plan', str(plan), '--no-repo', '--loop', '--no-verify', '--reason', 'inspection']
    _, options, _ = main.parse_common(argv)
    publish = state.publish
    def interrupt(root, nonce, build):
        if os.path.exists(os.path.join(build, 'target.json')):
            raise RuntimeError('simulated crash before round publication')
        return publish(root, nonce, build)
    monkeypatch.setattr(state, 'publish', interrupt)
    with pytest.raises(RuntimeError, match='simulated crash'):
        loop_driver.prepare(argv, options)
    monkeypatch.setattr(state, 'publish', publish)
    # when bare status is consulted, then the recoverable loop is visible without knowing its ID
    listed = review('status')
    assert listed.exit_code == 0, listed
    assert len(listed.envelope['payload']['open_jobs']) == 1
    pending = listed.envelope['payload']['open_jobs'][0]
    assert pending['stage'] == 'capture-pending'
    assert pending['next_verb'] == 'continue'
    plan.write_text('later source edit must not be silently recaptured\n')
    # when recovery publishes the retained snapshot, then the original bytes survive
    extra = ['--reason', 'user canceled this unpublished review'] if finish == 'abandon' else []
    resumed = review(finish, pending['job_id'], '--expected-generation', str(pending['generation']), *extra)
    assert resumed.exit_code == 0, resumed
    if finish == 'abandon':
        assert resumed.envelope['payload']['stage'] == 'closed'
        assert review('status').envelope['payload']['open_jobs'] == []
        return
    round_dir = state_root / resumed.envelope['payload']['round']
    assert (round_dir / 'artifacts' / 'A0001' / 'after').read_text() == 'the captured version\n'
    assert resumed.envelope['payload']['budget']['launched'] == 0


@pytest.mark.parametrize('after_publish', [False, True])
def should_recover_recapture_on_either_side_of_the_round_rename(
        review, plan_file_factory, state_root, fake_codex, monkeypatch, after_publish):
    import json
    from codex_review import loops
    # given a closed first round and a ready recapture checkpoint
    monkeypatch.setenv('CODEX_REVIEW_STATE', str(state_root))
    monkeypatch.setenv('CODEX_BIN', str(fake_codex))
    plan = plan_file_factory('version retained by recapture\n')
    prepared = review('prepare', 'plan', str(plan), '--no-repo', '--loop', '--no-verify', '--reason', 'inspection')
    directory = str(state_root / prepared.envelope['job_id'])
    current = loops.read(directory)
    old_round = state_root / current['current_round']
    original_bytes = (old_round / 'target.json').read_bytes()
    (old_round / 'abandoned.json').write_text(json.dumps(dict(reason='offline closed-round fixture')))
    loops.append(directory, 0, dict(current, phase='recapture'), dict(kind='fixture-stage'))
    publish = state.publish
    def interrupt(root, nonce, build):
        if os.path.exists(os.path.join(build, 'target.json')):
            if after_publish:
                publish(root, nonce, build)
            raise RuntimeError('simulated recapture crash')
        return publish(root, nonce, build)
    monkeypatch.setattr(state, 'publish', interrupt)
    with pytest.raises(RuntimeError, match='simulated recapture'):
        loop_driver.recapture(directory, {}, loops.read(directory))
    monkeypatch.setattr(state, 'publish', publish)
    # when recovering, then publication uses the reserved member rather than freezing newer source bytes
    pending = review('status', directory).envelope['payload']
    assert pending['stage'] == 'capture-pending'
    plan.write_text('later bytes\n')
    result = review('continue', directory, '--expected-generation', str(pending['generation']))
    assert result.exit_code == 0, result
    new_round = state_root / result.envelope['payload']['round']
    assert new_round != old_round
    assert (new_round / 'artifacts' / 'A0001' / 'after').read_text() == 'version retained by recapture\n'
    assert (old_round / 'target.json').read_bytes() == original_bytes
    assert len(loops.read(directory)['rounds']) == 2


@pytest.mark.parametrize('damaged', ['artifacts/A0001/after', 'AGENTS.md', 'artifacts/A0001/metadata.json'])
def should_refuse_a_corrupted_retained_capture_before_publication(
        review, plan_file_factory, state_root, fake_codex, monkeypatch, damaged):
    from codex_review import loops
    # given a completed snapshot interrupted before publication
    monkeypatch.setenv('CODEX_REVIEW_STATE', str(state_root))
    monkeypatch.setenv('CODEX_BIN', str(fake_codex))
    argv = ['plan', str(plan_file_factory()), '--no-repo', '--loop', '--no-verify', '--reason', 'integrity control']
    _, options, _ = main.parse_common(argv)
    publish = state.publish
    def interrupt(root, nonce, build):
        if os.path.isfile(os.path.join(build, 'target.json')):
            raise RuntimeError('publication interrupted')
        return publish(root, nonce, build)
    monkeypatch.setattr(state, 'publish', interrupt)
    with pytest.raises(RuntimeError):
        loop_driver.prepare(argv, options)
    monkeypatch.setattr(state, 'publish', publish)
    listed = review('status').envelope['payload']['open_jobs'][0]
    directory = state_root / listed['job_id']
    current = loops.read(str(directory))
    build = state_root / '.staging' / current['current_round']
    (build / damaged).write_bytes(b'corrupted retained bytes')
    # when continuing, then an intact target.json does not authorize corrupted artifacts or policy
    result = review('continue', str(directory), '--expected-generation', str(current['generation']))
    assert result.exit_code == 8, result
    assert result.envelope['error']['code'] == 'capture-bytes-corrupt'
    assert not (state_root / current['current_round']).exists()
