"""Malformed provider payloads are contract refusals, never Python exceptions."""
import copy
import json

import pytest

from codex_review import response
from test_run import VALID_RESPONSE


@pytest.mark.parametrize('field,value', [('id', []), ('artifact', {}), ('claim', 3),
                                        ('severity', []), ('suggested_fix', None)])
def should_refuse_wrong_finding_types(field, value):
    # given a syntactically valid JSON report with an invalid field type
    report = copy.deepcopy(VALID_RESPONSE)
    report['findings'][0][field] = value
    # when validating, then report a field-specific contract refusal
    with pytest.raises(response.ContractViolation) as caught:
        response.validate(json.dumps(report), {'mode': 'plan', 'artifacts': ['A0001']})
    assert caught.value.details['field'] == 'findings[0].' + field
    assert 'expected' in caught.value.details


@pytest.mark.parametrize('scope', [[{}], ['A0001', 'extra'], ['A0001', 'A0001']])
def should_require_exact_unique_artifact_scope(scope):
    # given unknown, duplicate or non-string artifact declarations
    report = copy.deepcopy(VALID_RESPONSE)
    report['declared_scope'] = scope
    # when validating, then no malformed declaration establishes coverage
    with pytest.raises(response.ContractViolation):
        response.validate(json.dumps(report), {'mode': 'plan', 'artifacts': ['A0001']})


@pytest.mark.parametrize('location', ['report', 'finding', 'claim'])
def should_reject_unknown_provider_fields(location):
    # given a response carrying a field outside the emitted schema
    report = copy.deepcopy(VALID_RESPONSE)
    if location == 'report':
        item = report
    elif location == 'finding':
        item = report['findings'][0]
    else:
        report['verified_claims'] = [dict(claim='bounded claim', result='not-verifiable',
                                          evidence='no repository context', finding_id=None)]
        item = report['verified_claims'][0]
    item['unexpected'] = 'silently discarded policy'
    # when validating, then the driver enforces additionalProperties itself
    with pytest.raises(response.ContractViolation, match='unknown fields'):
        response.validate(json.dumps(report), {'mode': 'plan', 'artifacts': ['A0001']})
