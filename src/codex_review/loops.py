"""Flat loop handles and append-only, generation-checked durable transitions."""
import json
import os

from codex_review import context, counting, images, jobs, projections, response, state, verification

require = response._require


def _json(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def create(root, loop_id, round_id, request):
    build = state.staging_dir(root, loop_id)
    initial = dict(kind='loop', schema_version=2, loop_id=loop_id, generation=0,
                   current_round=round_id, rounds=[round_id], phase='prepared', request=request,
                   round_targets={round_id: request.get('staged_target_sha256')},
                   counting=counting.initial(request.get('effort', 'xhigh'), **request.get('thresholds', {})),
                   attempts=[], assessments={}, decisions=[], processed_rounds=[],
                   limits=request.get('limits', dict(counting.LIMITS)), pause=None,
                   bound_launch_key=request.get('launch_key'),
                   verification_contract=verification.contract(request['initial_target']) if request.get('initial_target') else None)
    state.write_json(os.path.join(build, 'loop.json'), initial)
    os.mkdir(os.path.join(build, 'transitions'))
    return state.publish(root, loop_id, build)


def _read(directory):
    previous = os.path.join(directory, 'loop.json')
    current = _json(previous)
    require(isinstance(current, dict) and current.get('kind') == 'loop' and
            current.get('generation') == 0, 'bad-loop', 'initial loop record is invalid')
    transition_dir = os.path.join(directory, 'transitions')
    for generation, name in enumerate(sorted(n for n in os.listdir(transition_dir) if n.endswith('.json')), 1):
        require(name == '{:08d}.json'.format(generation), 'broken-loop-chain', 'loop generation chain has a gap')
        path = os.path.join(transition_dir, name)
        transition = _json(path)
        require(transition.get('previous_sha256') == images.sha256_file(previous),
                'broken-loop-chain', 'loop generation chain digest mismatch')
        current = transition.get('state')
        require(isinstance(current, dict) and current.get('generation') == generation,
                'broken-loop-chain', 'loop generation chain has an invalid state')
        previous = path
    return current, previous


def read(directory):
    with jobs.mutex(directory, exclusive=False):
        return _read(directory)[0]


def append(directory, expected_generation, updated, decision):
    with jobs.mutex(directory):
        current, previous = _read(directory)
        require(type(expected_generation) is int and current['generation'] == expected_generation,
                'stale-generation', 'loop generation changed', field='expected_generation', expected=current['generation'])
        return _append_locked(directory, current, previous, updated, decision)


def _append_locked(directory, current, previous, updated, decision):
    require(updated['loop_id'] == current['loop_id'] and updated['kind'] == 'loop',
            'bad-loop', 'transition cannot change loop identity')
    generation = current['generation'] + 1
    updated = dict(updated, generation=generation)
    path = os.path.join(directory, 'transitions', '{:08d}.json'.format(generation))
    require(not os.path.exists(path), 'stale-generation', 'loop generation already exists')
    state.atomic_json(path, dict(previous_sha256=images.sha256_file(previous), decision=decision, state=updated))
    return updated


def resolve(root, argument, round_id=None):
    directory = jobs.resolve(root, argument)
    require(directory is not None and os.path.isfile(os.path.join(directory, 'loop.json')),
            'unresolvable-loop', 'loop handle does not resolve')
    current = read(directory)
    round_dir = None
    if round_id is not None:
        require(isinstance(round_id, str) and state.is_job_id(round_id) and round_id in current['rounds'],
                'round-not-member', 'round nonce is not a member of this loop')
        round_dir = jobs.resolve(root, round_id)
        require(round_dir is not None and _json(os.path.join(round_dir, 'target.json')).get('loop_id') == current['loop_id'],
                'round-not-member', 'round binding does not match this loop')
    return directory, round_dir


def mutate(directory, expected_generation, operation):
    """Run a short local operation under the loop lock, then retain its transition."""
    with jobs.mutex(directory):
        current, previous = _read(directory)
        require(current['generation'] == expected_generation, 'stale-generation',
                'loop generation changed', field='expected_generation', expected=current['generation'])
        updated, result, decision = operation(current)
        if updated is not None:
            _append_locked(directory, current, previous, updated, decision)
    return result


def adopt_pending(current):
    pending = current['pending_capture']
    return dict(current, phase='prepared', current_round=pending['round'],
                rounds=current['rounds'] + [pending['round']], pending_capture=None,
                round_targets=dict(current.get('round_targets', {}), **{pending['round']: pending['target_sha256']}),
                recapture_authorization=None,
                bound_launch_key=pending.get('launch_key', current.get('bound_launch_key')),
                launch_qualification=None if not pending['equivalent'] else current.get('launch_qualification'),
                author_qualifications=[] if not pending['equivalent'] else current.get('author_qualifications', []),
                counting=current['counting'] if pending['equivalent'] else counting.reset(current['counting']))


def publish_capture(directory, expected_generation, round_id, build, equivalent, launch_key=None):
    with jobs.mutex(directory):
        current, previous = _read(directory)
        require(current['generation'] == expected_generation and current['phase'] == 'recapture',
                'stale-generation', 'loop moved while recapture was being frozen')
        state.fsync_tree(build)
        pending = dict(round=round_id, target_sha256=images.sha256_file(os.path.join(build, 'target.json')),
                       previous_round=current['current_round'], equivalent=equivalent, launch_key=launch_key)
        current = _append_locked(directory, current, previous,
                                 dict(current, phase='capture-pending', pending_capture=pending),
                                 dict(kind='reserve-capture', **pending))
        state.publish(os.path.dirname(directory), round_id, build)
        current, previous = _read(directory)
        _append_locked(directory, current, previous, adopt_pending(current), dict(kind='publish-capture', **pending))


def snapshot_retained(root, current, round_id):
    expected = current.get('round_targets', {}).get(round_id)
    if not expected:
        return False
    directory = os.path.join(root, round_id)
    path = os.path.join(directory, 'target.json')
    try:
        if images.sha256_file(path) != expected:
            return False
        context.verify_retained(directory, _json(path))
        return True
    except (OSError, ValueError, TypeError, projections.CaptureRefused):
        return False
