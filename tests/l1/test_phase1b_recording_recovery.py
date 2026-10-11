"""Durable round records survive interruption before their loop transition."""
import json
import uuid

import pytest

from codex_review import images, loop_driver, loops
from test_phase1b_loop_interface import qualify_cold


@pytest.mark.parametrize('paths', [['a.py'], ['a.py', 'b.py']])
def should_adopt_probe_revisions_with_their_retained_amendments(tmp_path, paths):
    import copy
    from codex_review import counting, lifecycle
    # given immutable revised probes and an earlier authorized addition to this finding
    report = tmp_path / 'report.json'
    report.write_text(json.dumps(dict(findings=[dict(id='F-1')])))
    record = dict(report_sha256=images.sha256_file(str(report)), annotations=[dict(
        finding_id='F-1', disposition='accepted', fix_paths=paths)])
    path = tmp_path / 'probes.json'
    path.write_text(json.dumps(record))
    prior_bytes = path.read_bytes()
    current = dict(current_round=tmp_path.name, phase='reviewed', findings=[], counting=counting.initial(),
                   amendments={tmp_path.name: [dict(additions=[dict(finding_id='F-1', paths=['b.py'])])]})
    before = copy.deepcopy(current)
    # when adopting the published revision, then effective scope is preserved exactly once without rewriting evidence
    adopted = lifecycle.adopted(current, str(tmp_path), dict(name='probes', path=str(path), phase='probed'))
    assert adopted['findings'][0]['fix_paths'] == ['a.py', 'b.py']
    assert adopted['findings'][0]['assessment']['fix_paths'] == ['a.py', 'b.py']
    assert current == before
    assert path.read_bytes() == prior_bytes


@pytest.mark.parametrize('interrupted', ['probes', 'apply', 'verification'])
def should_adopt_a_published_record_without_replaying_it(
        review, plan_file_factory, state_root, codex_reply, tmp_path, monkeypatch, interrupted):
    # given a qualified, assessed cold round ready for the author lifecycle
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'offline recording control')
    directory = state_root / prepared.envelope['job_id']
    job = state_root / prepared.envelope['payload']['round']
    qualify_cold(state_root, job)
    codex_reply(dict(verdict='approve', findings=[], declared_scope=['A0001'], verified_claims=[]))
    log = tmp_path / 'log.jsonl'
    log.write_text(json.dumps(dict(type='thread.started', thread_id=str(uuid.uuid4()))) + '\n{"type":"turn.completed"}\n')
    assert review('run', str(directory), '--round', job.name, '--expected-generation', '0',
                  fake={'CODEX_FAKE_RESPONSE': codex_reply.path, 'CODEX_FAKE_STDOUT': log}).exit_code == 0
    def status():
        return review('status', str(directory)).envelope['payload']
    def sha(path):
        return images.sha256_file(str(path))
    decision = dict(kind='read-assessment', round=job.name, disposition='no-observed-breach',
                    rationale='Complete offline no-read control', event_lines=[], previous_assessment_sha256=None,
                    report_sha256=sha(job / 'report.json'), log_sha256=sha(log),
                    checkpoint_sha256=status()['checkpoint_sha256'])
    (directory / 'staging').mkdir(exist_ok=True)
    (directory / 'staging' / 'decision.json').write_text(json.dumps(decision))
    assert review('continue', str(directory), '--expected-generation', str(status()['generation']),
                  '--decision-file', 'staging/decision.json').exit_code == 0
    for name in ('probes', 'apply', 'verification'):
        payload = (dict(report_sha256=sha(job / 'report.json'), annotations=[]) if name == 'probes' else
                   dict(report_sha256=sha(job / 'report.json'), probes_sha256=sha(job / 'probes.json'), results=[])
                   if name == 'apply' else dict(apply_sha256=sha(job / 'apply.json'),
                                                verification=dict(result='not-run-explicitly')))
        (job / 'staging' / (name + '.json')).write_text(json.dumps(payload))
        args = [str(directory), '--round', job.name, '--expected-generation', str(status()['generation']),
                '--' + name + '-file', 'staging/' + name + '.json']
        if name != interrupted:
            assert review('record-' + name, *args).exit_code == 0
            continue
        # when the durable record is written but the loop transition crashes
        append = loops._append_locked
        def crash(*args):
            raise RuntimeError('interrupted before loop append')
        monkeypatch.setenv('CODEX_REVIEW_STATE', str(state_root))
        monkeypatch.setattr(loops, '_append_locked', crash)
        with pytest.raises(RuntimeError, match='interrupted before loop append'):
            loop_driver.route('record-' + name, args)
        monkeypatch.setattr(loops, '_append_locked', append)
        record = job / (name + '.json')
        retained = record.read_bytes()
        # then status identifies recovery, and continue adopts exactly once without rewriting evidence
        assert status()['next_verb'] == 'continue'
        resumed = review('continue', str(directory), '--expected-generation', str(status()['generation']))
        assert resumed.exit_code == 0, resumed
        assert status()['stage'] == {'probes': 'probed', 'apply': 'applied', 'verification': 'round-complete'}[name]
        assert record.read_bytes() == retained
        assert status()['clean_streak']['streak'] == 0
        assert review('record-' + name, *args).envelope['error']['code'] == 'stale-generation'
        break
