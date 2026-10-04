"""Review policy: credit and charged launches are independent counters."""
from codex_review.response import _require

LIMITS = dict(judgments=16, no_judgment=4, launches=20)
OPTIONS = dict(judgment_budget='judgments', retry_budget='no_judgment', launch_budget='launches',
               xhigh_clean='xhigh_required', max_clean='max_required')


def initial(effort='xhigh', xhigh_required=2, max_required=3):
    _require(effort in ('xhigh', 'max'), 'bad-effort', 'loop effort must be xhigh or max')
    _require(type(xhigh_required) is int and xhigh_required > 0 and
             type(max_required) is int and max_required > 0,
             'bad-threshold', 'clean thresholds must be positive integers')
    return dict(next_effort=effort, streak=0, phase='max' if effort == 'max' else 'xhigh',
                converged=False, xhigh_required=xhigh_required, max_required=max_required)


def reset(state):
    result = dict(state, streak=0, converged=False)
    result.pop('qualification_refs', None)
    result.pop('read_bindings', None)
    result.pop('verification_bindings', None)
    result.pop('probe_bindings', None)
    result.pop('snapshot_rounds', None)
    if result['phase'] == 'confirmation':
        result.update(phase='max', next_effort='max')
    return result


def round_finished(state, *, effort, eligible, accepted, blockers):
    _require(effort == state['next_effort'], 'effort-transition', 'round effort does not match policy')
    _require(not state['converged'], 'already-converged', 'closed sequences gain no more credit')
    if accepted or not eligible or blockers:
        return reset(state)
    result = dict(state)
    if result['phase'] == 'confirmation':
        result['converged'] = True
    else:
        result['streak'] += 1
        if result['phase'] == 'xhigh' and result['streak'] >= result['xhigh_required']:
            result.update(phase='confirmation', next_effort='max')
        elif result['phase'] == 'max' and result['streak'] >= result['max_required']:
            result['converged'] = True
    return result


def budget(outcomes, judgments=16, no_judgment=4, launches=20):
    _require(all(type(n) is int and n > 0 for n in (judgments, no_judgment, launches)),
             'bad-budget', 'budget ceilings must be positive integers')
    _require(all(o in ('judgment', 'no-judgment', 'pending', 'retired-unknown') for o in outcomes),
             'bad-attempt-outcome', 'attempt outcome is not accounted for')
    completed = outcomes.count('judgment')
    failed = outcomes.count('no-judgment') + outcomes.count('retired-unknown')
    pending = outcomes.count('pending')
    return dict(launched=len(outcomes), judgments=completed, no_judgment=failed, pending=pending,
                remaining=max(0, launches - len(outcomes)),
                remaining_judgments=max(0, judgments - completed - pending),
                remaining_no_judgment=max(0, no_judgment - failed - pending),
                may_launch=not pending and completed < judgments and failed < no_judgment and len(outcomes) < launches)


def partition(attempts):
    result = dict(judgment=[], unavailable_no_judgment=[], other_no_judgment=[], pending_completion=[], retired_unknown=[])
    for attempt in attempts:
        if not attempt.get('charged', True):
            continue
        outcome = attempt['outcome']
        group = ('unavailable_no_judgment' if attempt.get('failure_kind') == 'unavailable' else 'other_no_judgment') if outcome == 'no-judgment' else {
            'judgment': 'judgment', 'pending': 'pending_completion', 'retired-unknown': 'retired_unknown'}[outcome]
        result[group].append(attempt['id'])
    return result


def causes(current):
    outcomes = partition(current['attempts'])
    ledger = current.get('findings', [])
    return dict(accepted_defects=[f['reference'] for f in ledger if f['disposition'] == 'accepted'],
                disputes=[f['reference'] for f in ledger if f['status'] == 'open' and f['disposition'] != 'accepted'],
                unavailability=outcomes['unavailable_no_judgment'],
                other_no_judgment=outcomes['other_no_judgment'],
                pending_completion=outcomes['pending_completion'], retired_unknown=outcomes['retired_unknown'])
