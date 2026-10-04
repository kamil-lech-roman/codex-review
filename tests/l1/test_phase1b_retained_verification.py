"""Retained results need their original sealed bytes and execution bindings."""
import json

import pytest

from codex_review import evidence, images, response, verification


@pytest.mark.parametrize('damaged', [None, 'command', 'output', 'rc', 'pointer', 'manifest', 'contradiction', 'missing-execution-id'])
def should_revalidate_the_original_verification_chain(tmp_path, damaged):
    # given a recorded passing result with real seals and a retained execution snapshot
    job = tmp_path / ('a' * 16)
    (job / 'staging').mkdir(parents=True)
    block = {}
    for kind, path, text in [('command', 'staging/verify.sh', 'echo checked\n'),
                             ('output', 'verify.out', 'checked\n'), ('rc', 'verify.rc', '0\n')]:
        (job / path).write_text(text)
        block[kind + '_evidence_id'] = evidence.seal(str(job), job.name, path)['evidence_id']
    command = block['command_evidence_id']
    execution_id = 'b' * 16
    manifest = dict(execution_id=execution_id, command_evidence_id=command,
                    command_sha256=images.sha256_bytes(b'echo checked\n'))
    execution_path = job / 'executions' / execution_id / 'manifest.json'
    execution_path.parent.mkdir(parents=True)
    execution_path.write_text(json.dumps(manifest))
    pointer = job / 'evidence' / command / 'execution.json'
    pointer.write_text(json.dumps(dict(execution_id=execution_id)))
    block['execution'] = manifest
    (job / 'apply.json').write_text('{}')
    record = dict(verification_result='passed', verification_evidence=block,
                  verification_contract=dict(command='echo checked'),
                  apply_sha256=images.sha256_file(str(job / 'apply.json')))
    if damaged in ('command', 'output', 'rc'):
        (job / 'evidence' / block[damaged + '_evidence_id'] / 'content').unlink()
    elif damaged == 'pointer':
        pointer.write_text(json.dumps(dict(execution_id='c' * 16)))
    elif damaged == 'manifest':
        execution_path.write_text(json.dumps(dict(manifest, command_sha256='changed')))
    elif damaged == 'contradiction':
        (job / 'verify.rc').write_text('1\n')
        block['rc_evidence_id'] = evidence.seal(str(job), job.name, 'verify.rc')['evidence_id']
    elif damaged == 'missing-execution-id':
        block['execution']['execution_id'] = None
    # when relying on the old result, then lost bindings or a nonzero status cannot stay passed
    if damaged:
        with pytest.raises((response.ContractViolation, OSError)):
            verification.revalidate(str(job), record)
    else:
        verification.revalidate(str(job), record)
