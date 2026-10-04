"""Favorable author dispositions require the same explicit reasoning as rejection."""
import pytest

from codex_review import apply, probes, response


@pytest.mark.parametrize('paths,expected', [(['alias/module.py'], ['src/module.py']),
    (['alias/new.py'], ['src/new.py']), (['alias/link.py'], ['src/link.py']),
    (['alias/module.py', 'src/module.py'], 'duplicate-fix-path'),
    (['outside/module.py'], 'fix-path-outside-repo')])
def should_store_resolved_fix_identity_without_following_the_final_symlink(tmp_path, paths, expected):
    # given a repository with a directory alias and a final-component symlink
    root = tmp_path / 'repo'
    (root / 'src').mkdir(parents=True)
    (root / 'src/module.py').write_text('old bytes\n')
    (root / 'src/link.py').symlink_to('module.py')
    (root / 'alias').symlink_to('src', target_is_directory=True)
    (root / 'outside').symlink_to(tmp_path, target_is_directory=True)
    target = dict(mode='code', repo_root=str(root))
    # when declarations are frozen, then aliases share one identity and escaping ancestors still refuse
    if isinstance(expected, list):
        assert probes.canonicalise_fix_paths(target, paths, 'F-1') == expected
    else:
        with pytest.raises(response.ContractViolation) as error:
            probes.canonicalise_fix_paths(target, paths, 'F-1')
        assert error.value.code == expected


@pytest.mark.parametrize('missing', ['rationale', 'false_result', 'path_exercised'])
def should_require_acceptance_reasoning_before_evidence_can_gain_credit(missing):
    # given an accepted loop finding with a missing judgment field
    annotation = dict(finding_id='F001', disposition='accepted', rationale='checked',
                      false_result='assertion would pass', path_exercised='output contains the target marker')
    del annotation[missing]
    # when validating author reasoning, then favorable disposition has no cheap path
    with pytest.raises(response.ContractViolation) as error:
        probes.loop_reasoning(annotation)
    assert error.value.details['field'] == missing


def should_require_sweep_evidence_for_related_sites():
    # given a sweep that found related sites but offers no check evidence
    sweep = dict(searched_scope=['src/'], related_sites=['src/other.py'], rationale='searched siblings')
    # when recording, then a search claim alone is insufficient
    with pytest.raises(response.ContractViolation, match='evidence'):
        apply.check_sweep('/unused', 'a' * 16, sweep)


@pytest.mark.parametrize('classification,value', [('introduced', True), ('pre-existing', 'yes')])
def should_restrict_scope_questions_to_explicit_preexisting_booleans(classification, value):
    # given an introduced finding or a non-boolean scope request
    report = dict(findings=[dict(id='F1', classification=classification)])
    payload = dict(report_sha256='digest', annotations=[dict(finding_id='F1', scope_question=value)])
    # when validating, then reject this before any evidence or disposition can waive the claim
    with pytest.raises(response.ContractViolation) as error:
        probes.validate('/unused', 'a' * 16, {}, report, payload, 'digest')
    assert error.value.code == 'bad-scope-question'


def should_not_exclude_an_accepted_defect_even_with_user_scope_authority():
    from codex_review import finding_resolution
    # given an accepted introduced defect, when attempting scope dismissal, then fixed/refuted proof is still required
    item = dict(status='open', disposition='accepted')
    with pytest.raises(response.ContractViolation) as error:
        finding_resolution.exclude('/unused', {}, dict(asserted_by='user', authorization='Exclude it'), item)
    assert error.value.code == 'not-scope-question'


@pytest.mark.parametrize('result', apply.VERIFICATION_RESULTS)
@pytest.mark.parametrize('execution', [True, {}, {'qualification': 'forged'}])
def should_reject_caller_owned_execution_bindings_before_verification_evidence(result, execution):
    # given a result carrying a field reserved for derived driver evidence
    # when validating input, then neither malformed nor plausible caller metadata can survive
    with pytest.raises(response.ContractViolation) as error:
        apply.check_verification_evidence(result, dict(result=result, execution=execution))
    assert error.value.code == 'caller-execution-metadata'
