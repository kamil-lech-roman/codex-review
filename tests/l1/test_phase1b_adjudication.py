"""Adjudication cannot invent original IDs, change classification or claim a fix."""
import copy
import json

import pytest

from codex_review import response
from test_record_probes import RESPONSE


@pytest.mark.parametrize('field,value,code', [('id', 'missing', 'unknown-adjudication-id'),
    ('classification', 'pre-existing', 'changed-classification'),
    ('outcome', 'confirmed-fixed', 'bad-adjudication-outcome')])
def should_refuse_adjudication_that_changes_the_original_claim_identity(field, value, code):
    from codex_review import adjudication
    # given an original claim and an invalid attempted adjudication
    answer = dict(decisions=[dict(id='F-1', classification='introduced', outcome='withdrawn', rationale='evidence')],
                  new_findings=[])
    answer['decisions'][0][field] = value
    # when validating, then the response is refused against the retained original report
    with pytest.raises(response.ContractViolation) as refusal:
        adjudication.validate(json.dumps(answer), dict(mode='plan', artifacts=['A0001']), RESPONSE, ['F-1'])
    assert refusal.value.code == code


def should_require_unused_local_ids_for_new_rebuttal_findings():
    from codex_review import adjudication
    # given a new finding attempting to reuse an original ID
    answer = dict(decisions=[dict(id='F-1', classification='introduced', outcome='maintained', rationale='still holds')],
                  new_findings=copy.deepcopy(RESPONSE['findings']))
    # when validating, then the reused ID cannot replace the original claim
    with pytest.raises(response.ContractViolation, match='unused'):
        adjudication.validate(json.dumps(answer), dict(mode='plan', artifacts=['A0001']), RESPONSE, ['F-1'])


@pytest.mark.parametrize('new_id', ['F-1', 'retained-aaaaaaaaaaaaaaaa-F-9', 'F-2'])
@pytest.mark.parametrize('published', ['absent', 'matching', 'conflicting'])
def should_validate_all_effective_ids_before_publishing_adjudication(tmp_path, new_id, published):
    from codex_review import adjudication
    # given an original report and a larger effective report retained by the author
    job = tmp_path / ('b' * 16)
    attempt_dir = job / 'reviewer-attempts' / ('c' * 16)
    attempt_dir.mkdir(parents=True)
    (job / 'target.json').write_text(json.dumps(dict(mode='plan', artifacts=['A0001'])))
    (job / 'report.json').write_text(json.dumps(RESPONSE))
    carried = dict(RESPONSE['findings'][0], id='retained-aaaaaaaaaaaaaaaa-F-9')
    base = dict(RESPONSE, findings=RESPONSE['findings'] + [carried])
    (job / 'work.json').write_text(json.dumps(base))
    answer = dict(decisions=[dict(id='F-1', classification='introduced', outcome='maintained', rationale='Evidence')],
                  new_findings=[dict(carried, id=new_id)])
    result_path = attempt_dir / 'adjudication.json'
    if published != 'absent':
        result_path.write_text(json.dumps(answer if published == 'matching' else dict(answer, decisions=[])))
    retained_bytes = result_path.read_bytes() if result_path.exists() else None
    attempt = dict(id=attempt_dir.name, round=job.name, kind='rebuttal', outcome='pending', charged=True,
                   thread_id='bound-thread', return_phase='probed', claims=[dict(id='F-1')])
    current = dict(attempts=[attempt], round_records={job.name: dict(report='work.json', probes='probes.json')})
    outcome = dict(attempt_dir=str(attempt_dir), timing={}, launched=True, failed=False,
                   thread_id='bound-thread', raw=json.dumps(answer))
    # when completing the reply, then invalid IDs cannot publish a judgment or leave the call pending
    updated, code, error = adjudication.completed_state(str(tmp_path / ('d' * 16)), current, attempt, outcome)
    valid = new_id == 'F-2' and published != 'conflicting'
    assert code == (0 if valid else 8)
    assert updated['attempts'][-1]['outcome'] == ('judgment' if valid else 'no-judgment')
    assert result_path.exists() == (valid or published != 'absent')
    if retained_bytes is not None:
        assert result_path.read_bytes() == retained_bytes
    assert (attempt_dir / 'effective-report.json').exists() == valid
    assert json.loads((job / 'work.json').read_text()) == base
    if not valid:
        assert error['code'] == ('adjudication-conflict' if new_id == 'F-2' else 'reused-finding-id')
        assert updated['pause']['kind'] == 'stalled-dispute'
        assert updated['round_records'] == current['round_records']
