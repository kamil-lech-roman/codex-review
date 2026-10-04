"""Retained refutations depend on the complete sealed counterexample, not a status label."""
import json

import pytest

from codex_review import evidence, images, probe_integrity


@pytest.mark.parametrize('fault', [None, 'command', 'output', 'rc', 'record', 'report', 'manifest', 'pointer'])
@pytest.mark.parametrize('independent', [False, True])
def should_retain_only_intact_probe_evidence(tmp_path, fault, independent):
    # given a sealed counterexample with an immutable execution snapshot and record binding
    job = tmp_path / ('a' * 16)
    (job / 'staging').mkdir(parents=True)
    (job / 'probes').mkdir()
    annotation = dict(finding_id='F-1', probe_execution='executed', read='unsupported', disposition='rejected')
    for kind, path, value in [('command', 'staging/probe-F-1.sh', 'false\n'),
                              ('output', 'probes/F-1.out', 'counterexample\n'), ('rc', 'probes/F-1.rc', '1\n')]:
        (job / path).write_text(value)
        annotation[kind + '_evidence_id'] = evidence.seal(str(job), job.name, path)['evidence_id']
    command = annotation['command_evidence_id']
    manifest = dict(execution_id='b' * 16, command_evidence_id=command, command_sha256=images.sha256_bytes(b'false\n'))
    manifest_path = job / 'executions' / manifest['execution_id'] / 'manifest.json'
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest))
    pointer = job / 'evidence' / command / 'execution.json'
    pointer.write_text(json.dumps(dict(execution_id=manifest['execution_id'])))
    annotation['execution'] = manifest
    (job / 'report.json').write_text('{}')
    (job / 'probes.json').write_text(json.dumps(dict(report_sha256=images.sha256_file(str(job / 'report.json')),
                                                   annotations=[annotation])))
    bound = (dict(round=job.name, annotation=annotation) if independent else
             probe_integrity.binding({}, str(job), 'F-1'))
    # when sealed bytes, the execution binding, or the original record are lost
    if fault in ('command', 'output', 'rc'):
        (job / 'evidence' / annotation[fault + '_evidence_id'] / 'content').write_text('corrupted')
    elif fault in ('record', 'report'):
        (job / ('probes.json' if fault == 'record' else 'report.json')).write_text('{"changed": true}')
    elif fault in ('manifest', 'pointer'):
        (manifest_path if fault == 'manifest' else pointer).unlink()
    # then nonzero probe status is valid, but lost evidence is not; an independent assessment binds its own annotation
    assert probe_integrity.retained(str(tmp_path), bound) == (fault is None or independent and fault in ('record', 'report'))
