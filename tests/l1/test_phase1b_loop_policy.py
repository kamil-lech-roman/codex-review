"""Explicit policy configuration and authority-bound budget changes."""
import json


def should_capture_configured_limits_and_require_authority_for_an_extension(review, plan_file_factory, state_root):
    # given user-configured policy ceilings
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--loop',
                      '--no-verify', '--reason', 'inspection', '--xhigh-clean', '4',
                      '--judgment-budget', '8', '--retry-budget', '2', '--launch-budget', '10')
    assert prepared.exit_code == 0, prepared
    assert prepared.envelope['payload']['clean_streak']['xhigh_required'] == 4
    assert prepared.envelope['payload']['budget']['remaining_judgments'] == 8
    loop_id = prepared.envelope['job_id']
    directory = state_root / loop_id
    (directory / 'staging').mkdir(exist_ok=True)
    def submit(authority):
        current = review('status', loop_id).envelope['payload']
        decision = dict(kind='budget-extension', rationale='More calls requested',
                        limits=dict(judgments=9, no_judgment=2, launches=11),
                        checkpoint_sha256=current['checkpoint_sha256'], **authority)
        (directory / 'staging' / 'decision.json').write_text(json.dumps(decision))
        return review('continue', loop_id, '--expected-generation', str(current['generation']),
                      '--decision-file', 'staging/decision.json')
    # when authority is missing, then no limit changes
    assert submit({}).envelope['error']['code'] == 'user-decision-required'
    # when the explicit user decision is cited, then only the ceilings grow
    extended = submit(dict(asserted_by='user', authorization='User requested one additional judgment and launch'))
    assert extended.exit_code == 0, extended
    assert extended.envelope['payload']['budget']['remaining_judgments'] == 9
    assert extended.envelope['payload']['budget']['launched'] == 0


def should_refuse_loop_policy_options_on_a_one_pass_capture(review, plan_file_factory):
    # given a policy setting with no durable loop to own it
    # when preparing, then the option is rejected rather than ignored
    result = review('prepare', 'plan', str(plan_file_factory()), '--no-repo',
                    '--no-verify', '--reason', 'inspection', '--retry-budget', '2')
    assert result.exit_code == 2
    assert result.envelope['error']['code'] == 'bad-option'
