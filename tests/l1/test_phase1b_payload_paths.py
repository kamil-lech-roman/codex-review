"""Recording payloads have one job-relative staging channel (T-48)."""
import json
from pathlib import Path

import pytest


def reviewed_job(review, plan_file_factory, codex_reply):
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo',
                      '--no-verify', '--reason', 'inspection')
    job = Path(prepared.envelope['job_dir'])
    codex_reply({'verdict': 'approve', 'findings': [], 'declared_scope': ['A0001'],
                 'verified_claims': []})
    result = review('run', str(job), fake={'CODEX_FAKE_RESPONSE': codex_reply.path})
    return job, {'report_sha256': result.envelope['payload']['report_sha256'], 'annotations': []}


def should_resolve_staged_payload_from_job_not_cwd(review, plan_file_factory, codex_reply, tmp_path):
    # given a staged payload and a different ambient directory
    job, payload = reviewed_job(review, plan_file_factory, codex_reply)
    (job / 'staging' / 'probes.json').write_text(json.dumps(payload))
    # when recording by its job-relative name
    result = review('record-probes', str(job), '--probes-file', 'staging/probes.json', cwd=tmp_path)
    # then the intended payload is consumed
    assert result.exit_code == 0, result
    assert (job / 'probes.json').exists()


@pytest.mark.parametrize('channel', ['external', 'root', 'symlink', 'traversal'])
def should_refuse_payload_outside_staging(review, plan_file_factory, codex_reply, tmp_path, channel):
    # given valid payload bytes in a forbidden channel
    job, payload = reviewed_job(review, plan_file_factory, codex_reply)
    outside = tmp_path / 'outside.json'
    outside.write_text(json.dumps(payload))
    if channel == 'external':
        path = str(outside)
    elif channel == 'root':
        (job / 'input.json').write_text(json.dumps(payload))
        path = 'input.json'
    elif channel == 'symlink':
        (job / 'staging' / 'link.json').symlink_to(outside)
        path = 'staging/link.json'
    else:
        (job / 'input.json').write_text(json.dumps(payload))
        path = 'staging/../input.json'
    # when recording
    result = review('record-probes', str(job), '--probes-file', path)
    # then refuse the channel before consuming the payload
    assert result.exit_code in (2, 8), result
    assert result.envelope['error']['code'] == 'payload-channel'
    assert not (job / 'probes.json').exists()
