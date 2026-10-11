"""Phase 1b input refusals: invalid requests publish no job."""
import pytest


@pytest.mark.parametrize('value,expected', [('0', 0), ('02', 2), ('٢', 2), ('²', None),
    ('', None), ('-1', None), ('+1', None), (' 1', None), ('1.0', None), (None, None), (2, None)])
def should_parse_only_decimal_integer_arguments(value, expected):
    from codex_review import response
    # given decimal input or a malformed numeric spelling
    # when parsing, then invalid values are data for a refusal, never an exception
    assert response.decimal_integer(value) == expected


def should_refuse_a_nondecimal_expected_generation_without_changing_state(review, plan_file_factory, state_root):
    from codex_review import loops
    # given an open loop and a numeric-looking generation that int cannot parse
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--loop', '--no-repo',
                      '--no-verify', '--reason', 'generation control')
    directory = state_root / prepared.envelope['job_id']
    before = loops.read(str(directory))
    # when requesting a mutation, then the ordinary stale-generation guard refuses it
    refused = review('continue', str(directory), '--expected-generation', '²')
    assert refused.exit_code == 8, refused
    assert refused.envelope['error']['code'] == 'stale-generation'
    assert loops.read(str(directory)) == before


@pytest.mark.parametrize('option', ['--judgment-budget', '--retry-budget', '--launch-budget', '--xhigh-clean', '--max-clean'])
def should_refuse_nondecimal_numeric_policy_input_without_a_traceback(review, plan_file_factory, option):
    # given a Unicode digit that int cannot parse as a decimal integer
    # when preparing, then argument validation returns its normal envelope before publication
    result = review('prepare', 'plan', str(plan_file_factory()), '--loop', '--no-repo',
                    '--no-verify', '--reason', 'numeric argument control', option, '²')
    assert result.exit_code == 2, result
    assert result.envelope['error']['code'] == 'bad-policy-limit'
    assert result.envelope['job_id'] is None
    assert not result.stderr


@pytest.mark.parametrize('verb', ['accept-delta', 'break-lease'])
def should_explain_retired_lease_verbs_without_promising_later_implementation(review, verb):
    # given a historical command explicitly retired by D02
    # when invoked, then refusal identifies its replacement rather than an unfinished milestone
    result = review(verb)
    assert result.exit_code == 2
    assert result.envelope['error']['code'] == 'retired-verb'
    assert result.envelope['error']['details']['decision'] == 'D02'


@pytest.mark.parametrize('args', [('--head', 'HEAD'), ('--base', 'x'), ('--no-verify',),
                                 ('--model', 'x'), ('--bytes', '4')])
def should_refuse_options_status_cannot_honor(review, args):
    # given an option that has meaning only for another verb
    # when status receives it
    result = review('status', *args)
    # then it must not silently discard the option
    assert result.exit_code == 2
    assert result.envelope['error']['code'] == 'bad-option'
    assert result.envelope['error']['details']['option'] == args[0]


@pytest.mark.parametrize('option,value', [('--model', 'x'), ('--effort', 'max')])
def should_refuse_loop_launch_overrides_as_usage_errors_without_mutating_state(
        review, plan_file_factory, state_root, tmp_path, option, value):
    from codex_review import loops
    # given a prepared loop whose model and effort belong to its policy
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'argument control')
    directory = state_root / prepared.envelope['job_id']
    before = loops.read(str(directory))
    argv = tmp_path / 'reviewer-argv.json'
    # when a run attempts an override, then the invalid request launches nothing and changes no state
    result = review('run', str(directory), '--round', before['current_round'], '--expected-generation',
                    str(before['generation']), option, value, fake={'CODEX_FAKE_ARGV': argv})
    assert result.exit_code == 2, result
    assert result.envelope['error']['code'] == 'option-not-applicable'
    assert result.envelope['error']['category'] == 'usage'
    assert loops.read(str(directory)) == before
    assert not argv.exists()


@pytest.mark.parametrize('flags', [(), ('--no-verify',), ('--defer-verify', '--loop')])
def should_require_one_meaningful_verification_intent(review, plan_file_factory, flags):
    # given a plan and missing, incomplete or conflicting intent
    plan = plan_file_factory()
    # when preparing
    result = review('prepare', 'plan', str(plan), '--no-repo', *flags)
    # then no job exists
    assert result.exit_code == 2
    assert result.envelope['error']['code'] == 'verification-intent'
    assert result.envelope['job_id'] is None


@pytest.mark.parametrize('flags', [('--base', 'HEAD', '--uncommitted'),
                                  ('--repo', '.', '--no-repo')])
def should_refuse_conflicting_capture_flags(review, git_repo, flags):
    # given conflicting capture or context selection
    # when preparing a code target
    result = review('prepare', 'code', *flags, '--no-verify', '--reason', 'inspection', cwd=git_repo)
    # then the conflict cannot be silently resolved by precedence
    assert result.exit_code == 2
    assert result.envelope['error']['code'] == 'conflicting-options'


def should_refuse_state_root_inside_repository(review, git_repo):
    # given state placed under the target, including through a resolved alias
    # when preparing a plan with that repository context
    result = review('prepare', 'plan', str(git_repo / 'README.md'), '--no-verify',
                    '--reason', 'inspection', cwd=git_repo,
                    fake={'CODEX_REVIEW_STATE': git_repo / 'review-state'})
    # then refuse before publishing state into the input universe
    assert result.exit_code == 9
    assert result.envelope['error']['code'] == 'state-inside-repository'
    assert not (git_repo / 'review-state').exists()


def should_have_no_default_byte_cap_but_honor_explicit_limits(review, plan_file_factory):
    # given a plan larger than both historical default limits
    plan = plan_file_factory('x' * (300 * 1024))
    # when no cap is requested
    result = review('prepare', 'plan', str(plan), '--no-repo', '--no-verify', '--reason', 'inspection')
    # then the complete capture is published, but an explicit cap still refuses
    assert result.exit_code == 0
    assert result.envelope['payload']['target']['bytes'] == 300 * 1024
    capped = review('prepare', 'plan', str(plan), '--no-repo', '--no-verify', '--reason',
                    'inspection', '--max-artifact', '100')
    assert capped.exit_code == 6


@pytest.mark.parametrize('value', ['-1', '0', 'no'])
def should_refuse_nonpositive_or_invalid_capture_limits(review, plan_file_factory, value):
    # given a capture limit with no meaningful positive bound
    # when preparing
    result = review('prepare', 'plan', str(plan_file_factory()), '--no-repo',
                    '--no-verify', '--reason', 'inspection', '--max-bundle', value)
    # then it is an argument error
    assert result.exit_code == 2
    assert result.envelope['error']['code'] == 'bad-limit'


@pytest.mark.parametrize('verb,field', [('record-probes', 'probes_file'), ('record-apply', 'apply_file'),
                                      ('record-verification', 'verification_file'), ('record-qualification', 'qualification_file'),
                                      ('rebut', 'rebuttal_file'), ('amend-paths', 'amendment_file')])
def should_refuse_missing_payload_options_before_dispatching_loop_work(review, plan_file_factory, verb, field):
    # given a valid loop handle but no required payload channel
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop', '--no-verify', '--reason', 'argument control')
    current = prepared.envelope['payload']
    # when dispatching any payload-recording operation, then a missing option is a usage error, independent of stage
    result = review(verb, prepared.envelope['job_id'], '--round', current['round'], '--expected-generation', '0')
    assert result.exit_code == 2, result
    assert result.envelope['error']['code'] == 'no-' + field.replace('_', '-')
    assert result.envelope['error']['details']['field'] == field
