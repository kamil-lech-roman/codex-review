"""D01/D06 acceptance examples, independent of persistence or reviewer transport."""
import pytest


def should_converge_after_two_xhigh_and_one_max():
    from codex_review import counting
    state = counting.initial()
    # given a fresh sequence
    # when two complete eligible clean xhigh rounds finish
    for _ in range(2):
        state = counting.round_finished(state, effort='xhigh', eligible=True, accepted=0, blockers=False)
    # then exactly one max confirmation is due
    assert state['next_effort'] == 'max'
    assert not state['converged']
    state = counting.round_finished(state, effort='max', eligible=True, accepted=0, blockers=False)
    assert state['converged']


def should_require_three_clean_max_after_a_defective_confirmation():
    from codex_review import counting
    state = counting.initial()
    for _ in range(2):
        state = counting.round_finished(state, effort='xhigh', eligible=True, accepted=0, blockers=False)
    # given a max confirmation with an accepted defect
    state = counting.round_finished(state, effort='max', eligible=True, accepted=1, blockers=False)
    # when two clean max rounds follow, then one more is still required
    for _ in range(2):
        state = counting.round_finished(state, effort='max', eligible=True, accepted=0, blockers=False)
        assert not state['converged']
    state = counting.round_finished(state, effort='max', eligible=True, accepted=0, blockers=False)
    assert state['converged']


def should_reset_credit_on_any_accepted_defect_or_disqualification():
    from codex_review import counting
    # given one clean round
    original = counting.round_finished(counting.initial(), effort='xhigh', eligible=True, accepted=0, blockers=False)
    # when even a minor defect is accepted or a round loses eligibility
    for eligible, accepted in [(True, 1), (False, 0)]:
        reset = counting.round_finished(original, effort='xhigh', eligible=eligible, accepted=accepted, blockers=False)
        # then previous credit cannot close the next round
        assert reset['streak'] == 0
        assert not reset['converged']


def should_keep_availability_and_judgment_budgets_separate():
    from codex_review import counting
    # given three quota refusals and fifteen valid reports
    outcomes = ['no-judgment'] * 3 + ['judgment'] * 15
    # when checking capacity, then one of either outcome remains
    remaining = counting.budget(outcomes)
    assert remaining == {'launched': 18, 'judgments': 15, 'no_judgment': 3,
                         'remaining_judgments': 1, 'remaining_no_judgment': 1,
                         'pending': 0, 'remaining': 2, 'may_launch': True}
    assert not counting.budget(outcomes + ['no-judgment'])['may_launch']
    assert not counting.budget(outcomes + ['judgment'])['may_launch']
    assert not counting.budget(outcomes + ['pending'])['may_launch']


def should_keep_unknown_retired_attempt_charged():
    from codex_review import counting
    # given a retired attempt with no recoverable outcome
    result = counting.budget(['retired-unknown'])
    # then it consumes total and failure allowance without asserting no model work
    assert result['launched'] == 1
    assert result['no_judgment'] == 1


def should_partition_charged_attempts_without_conflating_retirement_with_no_judgment():
    from codex_review import counting
    # given each durable outcome, including an explicitly unlaunched attempt
    attempts = [dict(id='valid', outcome='judgment'), dict(id='quota', outcome='no-judgment', failure_kind='unavailable'),
                dict(id='invalid', outcome='no-judgment', failure_kind='other'), dict(id='pending', outcome='pending'),
                dict(id='retired', outcome='retired-unknown'), dict(id='not-started', outcome='not-launched', charged=False)]
    # when status partitions calls, then every charged attempt belongs to exactly one honest outcome class
    partition = counting.partition(attempts)
    assert partition == dict(judgment=['valid'], unavailable_no_judgment=['quota'], other_no_judgment=['invalid'],
                             pending_completion=['pending'], retired_unknown=['retired'])
    assert sum(map(len, partition.values())) == counting.budget([a['outcome'] for a in attempts if a.get('charged', True)])['launched']


@pytest.mark.parametrize('phase', ['xhigh', 'confirmation', 'max'])
def should_break_consecutive_credit_when_a_completed_round_has_blockers(phase):
    from codex_review import counting
    # given clean credit before a judgment with unresolved findings
    current = counting.initial('max' if phase == 'max' else 'xhigh')
    for _ in range(2 if phase == 'confirmation' else 1):
        current = counting.round_finished(current, effort=current['next_effort'], eligible=True, accepted=0, blockers=False)
    # when a complete but blocked round is counted
    blocked = counting.round_finished(current, effort=current['next_effort'], eligible=True, accepted=0, blockers=True)
    # then later refutation cannot make the two separated clean runs consecutive
    assert blocked['streak'] == 0
    assert blocked['next_effort'] == ('xhigh' if phase == 'xhigh' else 'max')
    following = counting.round_finished(blocked, effort=blocked['next_effort'], eligible=True, accepted=0, blockers=False)
    assert following['streak'] == 1
    assert not following['converged']
