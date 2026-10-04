"""Qualification persistence checks cited controls; it does not perform their judgment."""
import copy
import json
from pathlib import Path

import pytest

from codex_review import evidence, response


def candidate(tmp_path):
    from codex_review import qualification as q
    job = tmp_path / ('a' * 16)
    (job / 'staging').mkdir(parents=True)
    controls = []
    for case in q.CASES['cache-execution']:
        citations = {}
        for kind, data in [('command', b'echo control\n'), ('output', b'observed\n'), ('rc', b'0\n')]:
            slot = 'staging/' + case + '-' + kind
            (job / slot).write_bytes(data)
            citations[kind + '_evidence_id'] = evidence.seal(str(job), job.name, slot)['evidence_id']
        controls.append(dict(case=case, outcome='pass', **citations))
    key = q.key('cache-execution', {'launch_profile': 'one-pass', 'runtime': []})
    record = dict(schema_version=1, capability='cache-execution', key=key, controls=controls,
                  rationale='Isolated test fixture for evidence validation, not host qualification.', supersedes=None)
    return job, record


def should_append_cas_qualification_and_detect_later_evidence_tampering(tmp_path):
    from codex_review import qualification as q
    # given complete cited fixture controls
    job, record = candidate(tmp_path)
    # when recording a qualification
    digest = q.record(str(tmp_path), str(job), record)
    # then its current validity is tied to the retained evidence
    assert q.lookup(str(tmp_path), record['key'])['valid'] is True
    assert (tmp_path / 'qualifications' / (digest + '.json')).is_file()
    with pytest.raises(response.ContractViolation):
        q.record(str(tmp_path), str(job), record)
    output = record['controls'][0]['output_evidence_id']
    (job / 'evidence' / output / 'content').write_text('tampered')
    assert q.lookup(str(tmp_path), record['key'])['valid'] is False


def should_keep_failed_supersession_and_never_select_prior_pass(tmp_path):
    from codex_review import qualification as q
    # given an existing passing record
    job, record = candidate(tmp_path)
    original = q.record(str(tmp_path), str(job), record)
    # when a later assessment records a failed control
    record['supersedes'] = original
    record['controls'][0]['outcome'] = 'fail'
    newer = q.record(str(tmp_path), str(job), record)
    # then old evidence remains but cannot qualify the current action
    status = q.lookup(str(tmp_path), record['key'])
    assert status['digest'] == newer
    assert not status['valid']
    assert (tmp_path / 'qualifications' / (original + '.json')).is_file()


def should_refuse_incomplete_control_suite(tmp_path):
    from codex_review import qualification as q
    # given an omitted required case
    job, record = candidate(tmp_path)
    record['controls'].pop()
    # when recording, then an incomplete suite cannot become qualification
    with pytest.raises(response.ContractViolation, match='case'):
        q.record(str(tmp_path), str(job), record)


def should_check_historical_evidence_when_retaining_qualification_credit(tmp_path):
    from codex_review import qualification as q
    # given a used passing qualification and a later passing replacement with new evidence
    job, record = candidate(tmp_path)
    original = q.record(str(tmp_path), str(job), record)
    bound = q.lookup(str(tmp_path), record['key'])
    record = copy.deepcopy(record)
    old_output = record['controls'][0]['output_evidence_id']
    source = 'staging/replacement-output'
    (job / source).write_text('new independent observed control')
    record['controls'][0]['output_evidence_id'] = evidence.seal(str(job), job.name, source)['evidence_id']
    record['supersedes'] = original
    q.record(str(tmp_path), str(job), record)
    selected = q.lookup(str(tmp_path), record['key'])
    assert q.continuous(str(tmp_path), bound, selected)
    # when the originally relied-on evidence is damaged, then latest-pass validity cannot preserve old credit
    (job / 'evidence' / old_output / 'content').write_text('damaged')
    assert q.lookup(str(tmp_path), record['key'])['valid']
    assert not q.continuous(str(tmp_path), bound, selected)


def should_bind_auth_environment_presence_without_retaining_credentials(monkeypatch):
    from codex_review import loop_driver
    # given the same home and flags, without environment-selected API authentication
    for name in ('OPENAI_API_KEY', 'CODEX_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    original = loop_driver.launch_key({})
    # when a key selects a different authentication source, qualification changes without recording its value
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-secret')
    selected = loop_driver.launch_key({})
    assert selected != original
    assert 'synthetic-test-secret' not in json.dumps(selected)
    monkeypatch.setenv('OPENAI_API_KEY', 'rotated-test-secret')
    assert loop_driver.launch_key({}) == selected
