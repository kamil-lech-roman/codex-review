"""CLI orchestration for explicitly addressed loops and member rounds."""
import json
import os
import re
import time
from datetime import datetime

from codex_review import apply as apply_module
from codex_review import amendments, codex, context, counting, evidence, execution, findings, images, jobs, late_evidence, lifecycle, loops, probe_integrity, probes, projections, qualification, quiescence, records, response, reviewer, state, verification


def availability(current):
    deadlines = [a['retry_after'] for a in current['attempts'] if a.get('retry_after')]
    pause = current.get('pause') or {}
    if pause.get('retry_after'):
        deadlines.append(pause['retry_after'])
    pending = []
    for value in deadlines:
        deadline = datetime.fromisoformat(value)
        response._require(deadline.tzinfo is not None, 'bad-availability-deadline', 'provider deadline must include its timezone')
        if time.time() < deadline.timestamp():
            pending.append((deadline.timestamp(), value))
    return max(pending)[1] if pending else None


def status_payload(directory):
    with jobs.mutex(directory, exclusive=False):
        current, checkpoint = loops._read(directory)
        checkpoint_sha256 = images.sha256_file(checkpoint)
    round_dir = os.path.join(os.path.dirname(directory), current['current_round'])
    pending = not os.path.isfile(os.path.join(round_dir, 'target.json'))
    target = current['request']['initial_target'] if pending else loops._json(os.path.join(round_dir, 'target.json'))
    phase = 'capture-pending' if pending and current['phase'] != 'closed' else current['phase']
    required = qualification.lookup(os.path.dirname(directory), launch_key(target))
    selected = records.selected(current, round_dir)
    publication = lifecycle.pending(current, round_dir)
    report_sha = records.digest(selected['report'])
    unprobed = ([f['id'] for f in loops._json(selected['report'])['findings']]
                if report_sha and phase in ('reviewed', 'assessment') else [])
    requirements = [required] + context.exclusion_qualifications(target, os.path.dirname(directory))
    requirements += [latest for prior, latest in reviewer_requirements(directory, current, target)
                     if prior['key']['capability'] == 'resume']
    if (current['verification_contract']['command'] or author_reliances(current, round_dir) or
            report_sha and loops._json(selected['report'])['findings']):
        requirements.append(qualification.lookup(os.path.dirname(directory), execution.key(target)))
    capacity = counting.budget([a['outcome'] for a in current['attempts'] if a.get('charged', True)], **current['limits'])
    pause = current['pause']
    judgments = assessment_attempts(current, current['current_round'])
    if not pause and phase in ('reviewed', 'probed', 'applied', 'round-complete', 'recapture') and assessment_issue(directory, current):
        pause = dict(kind='read-unresolved')
    retry_after = availability(current)
    if retry_after and not pause:
        pause = dict(kind='availability', retry_after=retry_after)
    late = late_evidence.pending(directory, current)
    if late and phase != 'closed':
        pause = dict(kind='late-evidence')
    if not pause and phase in ('prepared', 'recapture') and not capacity['may_launch']:
        pause = dict(kind='budget-exhausted', causes=counting.causes(current))
    if not pause and phase in ('prepared', 'round-complete'):
        if current.get('bound_launch_key') != required['key']:
            pause = dict(kind='qualification-key-changed')
        elif not required['valid'] or phase == 'round-complete' and any(not q['valid'] for q in requirements):
            pause = dict(kind='qualification-required')
    resolvers = {
        'read-unresolved': ['read-assessment'], 'stalled-dispute': ['dispute-resolution'],
        'budget-exhausted': ['budget-extension'], 'availability': ['continue-after-retry'],
        'candidate-drift': ['drift-resolution'], 'scope-violation': ['drift-resolution'],
        'unresolved-findings': ['finding-resolution'], 'verification-incomplete': ['drift-resolution'],
        'qualification-required': ['record-qualification', 'drift-resolution'],
        'late-evidence': ['resolve-call'],
        'qualification-key-changed': ['drift-resolution'], 'equivalence-unresolved': ['drift-resolution'],
        'equivalence-mismatch': ['drift-resolution'], 'capture-refused': ['drift-resolution'],
    }.get(pause['kind'], []) if pause else (
        ['resolve-call', 'retire-attempt'] if phase == 'calling' else ['read-assessment'] if phase == 'assessment' else [])
    return dict(checkpoint_sha256=checkpoint_sha256, required_qualifications=requirements, latest_attempt=current['attempts'][-1] if current['attempts'] else None,
                target_sha256=target['target_sha'], audit_sha256=target.get('audit_sha256'),
                target_record_sha256=(current['request'].get('staged_target_sha256') if pending else
                                      images.sha256_file(os.path.join(round_dir, 'target.json'))),
                bound_context=dict(repository_manifest_sha256=qualification.digest(target.get('context_manifest', {})),
                                   companions=[dict(id=c['id'], image=c['image']) for c in target.get('companions', [])],
                                   dependencies=[dict(id=d['id'], manifest_sha256=qualification.digest(d['manifest']), exclude=d['exclude'])
                                                 for d in target.get('dependencies', [])]),
                latest_read_assessment=assessment_for(current, judgments[-1]) if judgments else None,
                read_assessment_requirements=[dict(attempt=a['id'], kind=a['kind'],
                    judgment_field=assessment_field(a), judgment_sha256=records.digest(assessment_path(directory, a)),
                    issue=attempt_assessment_issue(directory, a, assessment_for(current, a)),
                    previous_assessment_sha256=qualification.digest(assessment_for(current, a))
                        if assessment_for(current, a) else None) for a in judgments],
                call_outcomes=counting.partition(current['attempts']), findings=current.get('findings', []),
                freshness_violations=current.get('freshness_violations', []),
                report_sha256=report_sha, probes_sha256=records.digest(selected['probes']),
                previous_probes_sha256=records.digest(selected['prior_probes']), unprobed_findings=unprobed,
                pending_probe_submission=(current.get('round_records', {}).get(current['current_round'], {}).get('pending_submission')
                                          if phase == 'reviewed' else None),
                adjudication=current.get('rebuttals', {}).get(current['current_round']),
                amendments_sha256=qualification.digest(current.get('amendments', {}).get(current['current_round'], [])),
                stage=phase, round=current['current_round'], generation=current['generation'],
                recording_recovery=publication,
                next_verb='continue' if publication else None if pause or phase == 'closed' else
                {'prepared': 'run', 'assessment': 'continue', 'reviewed': 'record-probes',
                 'probed': 'record-apply', 'applied': 'record-verification', 'round-complete': 'continue', 'recapture': 'recapture', 'capture-pending': 'continue'}.get(phase),
                resumable=phase not in ('closed', 'calling') and not pause,
                budget=capacity, resolver_kinds=resolvers, late_evidence=late, retry_after=retry_after,
                clean_streak=current['counting'], pause=pause,
                decisions=current['decisions'], verification_contract=current['verification_contract'],
                pending_findings=findings.outstanding(current.get('findings', [])))


def _envelope(verb, directory, exit_code=0, error=None, payload=None):
    from codex_review import main
    if exit_code == 18 and payload is not None:
        payload = dict(payload, next_verb=None, resumable=False)
    result = main.envelope(verb, exit_code, job_id=os.path.basename(directory) if directory else None, job_dir=directory,
                           error=error, payload=payload)
    result['schema_version'] = 2
    return result


def response_for_request(verb, argv, result):
    from codex_review import main
    rest, options, bad = main.parse_common(argv)
    if bad:
        return result
    if verb == 'prepare':
        if not options.get('loop'):
            return result
        directory = result['job_dir']
    else:
        directory = jobs.resolve(state.state_root(), rest[0]) if rest else None
        if directory is None or not os.path.isfile(os.path.join(directory, 'loop.json')):
            return result
    return _envelope(verb, directory, result['exit'], result.get('error'), result.get('payload'))


def prepare(argv, options):
    from codex_review import main
    rest, _, _ = main.parse_common(argv)
    if not rest:
        return main.usage_error('prepare', 'no-mode', 'prepare needs a target mode: plan or code')
    if rest[0] == 'code' and (not options.get('base') or options.get('uncommitted') or
                             not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', options.get('head', ''))):
        return main.usage_error('prepare', 'loop-capture-options',
                                'code loops require --base and a full --head OID, without --uncommitted')
    if options.get('effort', 'xhigh') not in ('xhigh', 'max'):
        return main.usage_error('prepare', 'bad-effort', 'loop effort must be xhigh or max')
    loop_id = state.new_nonce()
    published = []
    def publisher(root, round_id, build, target):
        state.fsync_tree(build)
        directory = loops.create(root, loop_id, round_id,
                                 dict(mode=target['mode'], effort=target.get('effort') or 'xhigh',
                                      initial_target=target, argv=argv, launch_key=launch_key(target, captured=True),
                                      capture_limits=main.resolve_limits(options)[0],
                                      staged_target_sha256=images.sha256_file(os.path.join(build, 'target.json')),
                                      limits=dict(counting.LIMITS, **{field: int(options[name]) for name, field in counting.OPTIONS.items()
                                                                     if name in options and field in counting.LIMITS}),
                                      thresholds={field: int(options[name]) for name, field in counting.OPTIONS.items()
                                                  if name in options and field not in counting.LIMITS}))
        published.append(directory)
        with jobs.mutex(directory):
            latest, _ = loops._read(directory)
            if latest['phase'] != 'closed':
                _publish_pending(directory, latest)
    captured = main.prepare(argv, loop_id=loop_id, publisher=publisher)
    if captured['exit']:
        return captured
    directory = published[0]
    return _envelope('prepare', directory, payload=dict(status_payload(directory), target=captured['payload']['target']))


def route(verb, argv):
    """Return None only when the request belongs to the one-pass interface."""
    result = _route(verb, argv)
    return response_for_request(verb, argv, result) if result is not None else None


def _route(verb, argv):
    from codex_review import main
    rest, options, bad = main.parse_common(argv)
    if bad:
        return None
    if verb == 'prepare':
        return prepare(argv, options) if options.get('loop') else None
    if not rest:
        if options.get('round') is not None or options.get('expected_generation') is not None:
            return main.usage_error(verb, 'option-not-applicable', 'loop options require a loop handle')
        return None
    directory = jobs.resolve(state.state_root(), rest[0])
    if directory is None:
        return None
    if not os.path.exists(os.path.join(directory, 'loop.json')):
        target = main.load_target(directory)
        if target and target.get('loop_id'):
            return main.usage_error(verb, 'round-needs-loop', 'member rounds require LOOP --round R')
        if options.get('round') is not None or options.get('expected_generation') is not None:
            return main.usage_error(verb, 'option-not-applicable', 'loop options require a loop handle')
        return None
    try:
        loops.resolve(state.state_root(), rest[0], options.get('round'))
        if verb == 'status':
            response._require('expected_generation' not in options, 'option-not-applicable', 'status cannot honor expected-generation')
            return _envelope(verb, directory, payload=status_payload(directory))
        current = loops.read(directory)
        supplied = options.get('expected_generation')
        response._require(response.decimal_integer(supplied) == current['generation'], 'stale-generation',
                          'loop mutation requires the current expected generation',
                          field='expected_generation', expected=current['generation'])
        response._require(current['phase'] != 'closed', 'already-terminal', 'loop is closed')
        if late_evidence.pending(directory, current) and verb not in ('continue', 'seal', 'abandon'):
            return handback(verb, directory, 'late-evidence')
        round_dir = os.path.join(os.path.dirname(directory), current['current_round'])
        if lifecycle.pending(current, round_dir) and (verb not in ('continue', 'status', 'seal', 'abandon') or options.get('decision_file')):
            return handback(verb, directory, 'record-recovery-required')
        if verb in ('record-probes', 'record-apply', 'record-verification', 'rebut', 'amend-paths', 'recapture'):
            issue = assessment_issue(directory, current)
            if issue:
                return handback(verb, directory, 'read-assessment-required', assessment_issue=issue)
        if verb == 'run':
            return run(directory, options, current)
        if verb == 'continue':
            return continue_loop(directory, options, current)
        if verb == 'recapture':
            return recapture(directory, options, current)
        if verb == 'amend-paths':
            return amend_paths(directory, options, current)
        if verb == 'rebut':
            from codex_review import adjudication
            return adjudication.rebut(directory, options, current)
        if verb in ('seal', 'record-probes', 'record-apply', 'record-verification', 'record-qualification'):
            return round_operation(verb, directory, rest, options, current)
        if verb == 'abandon':
            return abandon(directory, options, current)
        return main.usage_error(verb, 'option-not-applicable', 'this verb cannot operate on a loop')
    except projections.CaptureRefused as error:
        return _envelope(verb, directory, error.exit_code, error=dict(
            code=error.code, category='capture', message=error.message, details=error.details))
    except execution.Unqualified as error:
        return handback(verb, directory, 'qualification-required', qualifications=[error.selected])
    except (response.ContractViolation, OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, response.ContractViolation) and error.code == 'option-not-applicable':
            return main.usage_error(verb, error.code, error.message, **error.details)
        return _envelope(verb, directory, 8, error={
            'code': getattr(error, 'code', 'invalid-loop-state'), 'category': 'loop',
            'message': str(error), 'details': getattr(error, 'details', {})})


def launch_key(target, capability='cold-launch', captured=False):
    settings = dict(provider='codex', launch_profile='persistent', sandbox='read-only',
                    ignore_user_config=True, ignore_rules=True,
                    authentication=codex.authentication_settings(),
                    global_policy_sources=target.get('global_policy_sources') if captured else reviewer.global_policy_sources(),
                    execution_home=reviewer.execution_home(),
                    runtime=[dict(id=d['id'], manifest=d['manifest']) for d in target.get('dependencies', [])])
    if capability == 'resume':
        settings['resume_config'] = dict(sandbox_mode='read-only', cwd='<origin-round>', session='<origin-thread>')
    return qualification.key(capability, settings)


def handback(verb, directory, reason, **details):
    if reason == 'budget-exhausted':
        details['causes'] = counting.causes(loops.read(directory))
    return _envelope(verb, directory, 18, error={
        'code': 'needs-user-decision', 'category': 'loop', 'message': reason,
        'details': dict(reason=reason, next_verb=None, **details)})


def fixed_evidence_retained(directory, current, item):
    root = os.path.dirname(directory)
    try:
        resolution = item['resolution']
        review_round, verified_round = resolution['reviewer_round'], resolution['verification_round']
        fix_round = resolution.get('fix_round', verified_round)
        if records.digest(os.path.join(root, review_round, 'report.json')) != resolution['report_sha256']:
            return False
        if fix_round != verified_round and records.digest(os.path.join(root, fix_round, 'apply.json')) != resolution['fix_apply_sha256']:
            return False
        if not verification.retained(root, dict(round=verified_round, sha256=resolution['verification_sha256'])):
            return False
        if not all(loops.snapshot_retained(root, current, r) for r in (review_round, verified_round, fix_round)):
            return False
        bindings = item['closure_read_bindings']
        if not bindings or any(assessment_issue(directory, current, b['round'], b['assessment'], b['attempt']) for b in bindings):
            return False
        if (review_round in current.get('disqualified_rounds', []) or
                review_round in current.get('uncredited_rounds', []) or assessment_issue(directory, current, review_round)):
            return False
        return all(qualification.continuous(root, q, qualification.lookup(root, q['key']))
                   for q in item['closure_qualification_refs'])
    except (OSError, ValueError, KeyError, TypeError, response.ContractViolation):
        return False


def check_launch_qualification(directory, current, target):
    root = os.path.dirname(directory)
    selected = qualification.lookup(root, launch_key(target))
    previous = current.get('launch_qualification')
    if previous is None:
        previous = next((a['qualification'] for a in reversed(current['attempts'])
                         if a.get('kind') == 'cold' and a['round'] == current['current_round'] and a.get('qualification')), None)
    bound_key = current.get('bound_launch_key')
    key_changed = bound_key is not None and bound_key != selected['key']
    lost_credit = any(not qualification.continuous(root, relied, qualification.lookup(root, relied['key']))
                      for relied in current['counting'].get('qualification_refs', []))
    lost_reads = any(assessment_issue(directory, current, binding['round'], binding['assessment'], binding.get('attempt'))
                     for binding in current['counting'].get('read_bindings', []))
    lost_verification = any(not verification.retained(root, bound)
                            for bound in current['counting'].get('verification_bindings', []))
    lost_snapshots = any(not loops.snapshot_retained(root, current, round_id)
                         for round_id in current['counting'].get('snapshot_rounds', []) + [current['current_round']])
    lost_fixes = [f['reference'] for f in current.get('findings', [])
                  if f['status'] == 'fixed' and not fixed_evidence_retained(directory, current, f)]
    lost_probes = any(not probe_integrity.retained(root, bound)
                      for bound in current['counting'].get('probe_bindings', []))
    lost_refutations = [f['reference'] for f in current.get('findings', [])
                        if f['status'] in ('refuted', 'reviewed') and
                        not probe_integrity.retained(root, f.get('probe_binding'))]
    reopened = lost_fixes + lost_refutations
    if key_changed or lost_credit or lost_reads or lost_verification or lost_probes or lost_snapshots or reopened or (previous is not None and not qualification.continuous(root, previous, selected)):
        uncredited = set(current.get('uncredited_rounds', []))
        if any(a['round'] == current['current_round'] and a['outcome'] == 'judgment' for a in current['attempts']):
            uncredited.add(current['current_round'])
        pause = current['pause']
        if key_changed or previous is not None and previous['key'] != selected['key']:
            pause = dict(kind='qualification-key-changed', required=selected['key'])
        changed = dict(current, counting=counting.reset(current['counting']), pause=pause,
                       uncredited_rounds=sorted(uncredited), launch_qualification=selected,
                       findings=[dict(f, status='open') if f['reference'] in reopened else f
                                 for f in current.get('findings', [])])
        if changed != current:
            current = loops.append(directory, current['generation'], changed,
                                   dict(kind='credit-evidence-lost' if lost_reads or lost_verification or lost_probes or lost_snapshots or reopened else 'qualification-continuity-lost',
                                        previous=previous, selected=selected, reopened_findings=reopened))
    return current, selected


def author_reliances(current, job_dir):
    relied = list(current.get('author_qualifications', []))
    relied += [q for f in current.get('findings', [])
               if f.get('assessment_round') == os.path.basename(job_dir) and
               f.get('independent_assessment', {}).get('execution')
               for q in execution.reliances(f['independent_assessment']['execution'])]
    selected = records.selected(current, job_dir)
    if os.path.isfile(selected['probes']):
        relied += [q for a in loops._json(selected['probes'])['annotations']
                   if a.get('execution') for q in execution.reliances(a['execution'])]
    path = os.path.join(job_dir, 'verification.json')
    if os.path.isfile(path):
        used = loops._json(path)['verification_evidence'].get('execution')
        if used:
            relied.extend(execution.reliances(used))
    return relied


def reviewer_requirements(directory, current, target):
    return [(attempt['qualification'], qualification.lookup(os.path.dirname(directory), launch_key(
        target, 'resume' if attempt['kind'] == 'rebuttal' else 'cold-launch')))
        for attempt in current['attempts'] if attempt['round'] == current['current_round'] and
        attempt['outcome'] == 'judgment' and attempt.get('qualification') and not attempt.get('was_retired')]


def check_reviewer_qualifications(directory, current, target):
    required = reviewer_requirements(directory, current, target)
    if any(not qualification.continuous(os.path.dirname(directory), prior, latest) for prior, latest in required):
        updated = dict(current, counting=counting.reset(current['counting']),
                       uncredited_rounds=sorted(set(current.get('uncredited_rounds', []) + [current['current_round']])))
        if any(prior['key'] != latest['key'] for prior, latest in required):
            updated['pause'] = dict(kind='qualification-key-changed', required=[latest['key'] for _, latest in required])
        if updated != current:
            current = loops.append(directory, current['generation'], updated, dict(kind='reviewer-qualification-lost'))
    return current, [latest for _, latest in required]


def check_author_qualifications(directory, current):
    root = os.path.dirname(directory)
    job_dir = os.path.join(root, current['current_round'])
    relied = author_reliances(current, job_dir)
    target = context.after_apply(job_dir, loops._json(os.path.join(job_dir, 'target.json')))
    current_key = execution.key(target)
    key_changed = any(item['key'] != execution.rebind_key(target, item['key']) for item in relied)
    selected = {qualification.digest(item['key']): qualification.lookup(root, item['key']) for item in relied}
    lost = key_changed or any(not qualification.continuous(root, item, selected[qualification.digest(item['key'])]) for item in relied)
    if relied:
        selected[qualification.digest(current_key)] = qualification.lookup(root, current_key)
    updated = dict(current, author_qualifications=list(selected.values()))
    if lost:
        updated.update(counting=counting.reset(current['counting']),
                       uncredited_rounds=sorted(set(current.get('uncredited_rounds', []) + [current['current_round']])))
    if key_changed:
        updated['pause'] = dict(kind='qualification-key-changed', required=current_key)
    if updated != current:
        current = loops.append(directory, current['generation'], updated, dict(kind='check-author-qualification', continuity=not lost))
    return current, list(selected.values())


def check_exclusion_qualifications(directory, current, target):
    selected = context.exclusion_qualifications(target, os.path.dirname(directory))
    relied = [item['qualification'] for item in target.get('dependencies', []) if item['exclude']]
    if any(not qualification.continuous(os.path.dirname(directory), previous, latest)
           for previous, latest in zip(relied, selected)):
        updated = dict(current, counting=counting.reset(current['counting']),
                       uncredited_rounds=sorted(set(current.get('uncredited_rounds', []) + [current['current_round']])))
        if any(previous['key'] != latest['key'] for previous, latest in zip(relied, selected)):
            updated['pause'] = dict(kind='qualification-key-changed', required=[s['key'] for s in selected])
        if updated != current:
            current = loops.append(directory, current['generation'], updated, dict(kind='exclusion-qualification-lost'))
    return current, selected


def retain_freshness(current):
    attempt = current['attempts'][-1]
    if (attempt.get('kind') == 'cold' and attempt['outcome'] == 'judgment' and attempt.get('thread_id') and
            any(prior.get('thread_id') == attempt['thread_id'] for prior in current['attempts'][:-1])):
        return dict(current, counting=counting.reset(current['counting']),
                    disqualified_rounds=sorted(set(current.get('disqualified_rounds', []) + [attempt['round']])),
                    freshness_violations=current.get('freshness_violations', []) + [
                        dict(round=attempt['round'], attempt=attempt['id'], reason='reused-cold-thread')])
    return current


def run(directory, options, current):
    from codex_review import main
    response._require(not ({'model', 'effort'} & set(options)), 'option-not-applicable', 'loop effort/model are bound by its policy')
    response._require(options.get('round') == current['current_round'], 'round-required',
                      'run needs the current explicit round nonce')
    retry_after = availability(current)
    if retry_after:
        return handback('run', directory, 'availability', retry_after=retry_after)
    response._require(current['phase'] == 'prepared' and not current['pause'],
                      'wrong-stage', 'round is not ready to launch')
    round_dir = os.path.join(os.path.dirname(directory), current['current_round'])
    target = main.load_target(round_dir)
    current, selected = check_launch_qualification(directory, current, target)
    current, exclusions = check_exclusion_qualifications(directory, current, target)
    if not selected['valid'] or current['pause'] or not all(q['valid'] for q in exclusions):
        return handback('run', directory, 'qualification-required', qualifications=[selected] + exclusions)
    response._require(loops.snapshot_retained(os.path.dirname(directory), current, current['current_round']),
                      'capture-bytes-corrupt', 'launch needs the original retained snapshot')
    capacity = counting.budget([a['outcome'] for a in current['attempts'] if a.get('charged', True)], **current['limits'])
    if not capacity['may_launch']:
        return handback('run', directory, 'budget-exhausted', budget=capacity)
    executable = codex.resolve()
    if executable is None:
        return _envelope('run', directory, 7, error=dict(code='codex-unresolvable', category='preflight',
                                                       message='reviewer executable unavailable', details={}))
    attempt = dict(id=state.new_nonce(), round=current['current_round'], kind='cold', outcome='pending',
                   charged=True, reserved_at=time.time(), thread_id=None, qualification=selected,
                   effort=current['counting']['next_effort'])
    reserved = loops.append(directory, current['generation'],
                            dict(current, phase='calling', attempts=current['attempts'] + [attempt],
                                 launch_qualification=selected),
                            {'kind': 'reserve-call', 'attempt': attempt['id']})
    result = main.run_review(round_dir, executable, dict(_attempt_id=attempt['id'], effort=attempt['effort']))
    completed = dict(attempt)
    attempt_dir = os.path.join(round_dir, 'reviewer-attempts', attempt['id'])
    receipt_path = os.path.join(attempt_dir, 'completion.json')
    if os.path.exists(receipt_path):
        receipt = loops._json(receipt_path)
        completed.update(thread_id=receipt['thread_id'], timing=receipt['timing'], attempt_dir=attempt_dir)
        if receipt['launched']:
            completed['outcome'] = 'judgment' if os.path.exists(os.path.join(round_dir, 'report.json')) else 'no-judgment'
        else:
            completed.update(outcome='not-launched', charged=False)
    else:
        # A normal return before invoking transport is proof of a pre-launch refusal.
        completed.update(outcome='not-launched', charged=False)
    if completed['outcome'] == 'no-judgment':
        completed['failure_kind'] = 'unavailable' if result['exit'] == 7 else 'other'
    updated = loops.read(directory)
    response._require(updated['attempts'][-1]['id'] == attempt['id'], 'attempt-mismatch',
                      'another transition replaced the reserved call')
    if updated['attempts'][-1]['outcome'] != 'pending':
        return _envelope('run', directory, payload=status_payload(directory))
    phase = 'assessment' if completed['outcome'] == 'judgment' else 'prepared'
    if result['exit'] in (4, 8):
        phase = 'recapture'
    pause = None
    retry_after = result.get('error', {}).get('details', {}).get('retry_after')
    if retry_after:
        completed['retry_after'] = retry_after
        pause = dict(kind='availability', retry_after=retry_after)
    updated = loops.append(directory, updated['generation'],
                           retain_call_drift(retain_freshness(dict(updated, phase=phase, pause=pause,
                                                                 attempts=updated['attempts'][:-1] + [completed])), round_dir),
                           {'kind': 'complete-call', 'attempt': attempt['id'], 'exit': result['exit']})
    return _envelope('run', directory, result['exit'], error=result.get('error'),
                     payload=dict(status_payload(directory), review=result.get('payload')))


def retain_call_drift(current, job_dir):
    if not os.path.isfile(os.path.join(job_dir, 'invalidated.json')):
        return current
    report_path = os.path.join(job_dir, 'report.json')
    ledger = current.get('findings', [])
    if os.path.isfile(report_path):
        ledger = findings.retain_unassessed(ledger, current['current_round'], loops._json(report_path),
                                            images.sha256_file(report_path))
    return dict(current, phase='recapture', pause=dict(kind='candidate-drift'), findings=ledger,
                counting=counting.reset(current['counting']))


def round_operation(verb, directory, rest, options, current):
    from codex_review import main
    response._require(options.get('round') == current['current_round'], 'round-required',
                      'operation needs the explicit current round')
    expected = {'record-probes': 'reviewed', 'record-apply': 'probed', 'record-verification': 'applied'}
    if verb in expected and (current['phase'] != expected[verb] or current['pause']):
        return handback(verb, directory, 'read-assessment-required' if current['phase'] == 'assessment' else 'wrong-stage')
    contract = current['verification_contract']
    if verb == 'record-verification' and contract['intent'] == 'deferred' and contract['command'] is None:
        return handback(verb, directory, 'verification-selection-required')
    round_dir = os.path.join(os.path.dirname(directory), current['current_round'])
    if verb.startswith('record-') and lifecycle.pending(current, round_dir):
        return handback(verb, directory, 'record-recovery-required')
    arguments = [round_dir] + rest[1:]
    for name, value in options.items():
        if name not in ('round', 'expected_generation'):
            arguments += ['--' + name.replace('_', '-'), str(value)]
    handler = getattr(main, verb.replace('-', '_'))
    def operation(latest):
        response._require(latest['phase'] != 'closed', 'already-terminal', 'loop is closed')
        selected = records.selected(latest, round_dir)
        if verb == 'record-verification':
            result = handler(arguments, verification_contract=latest['verification_contract'], probes_path=selected['probes'])
        elif verb == 'seal':
            assessed = latest['assessments'].get(latest['current_round'], {}).get('disposition') in (
                'no-observed-breach', 'history-consumed', 'read-unverified') and not assessment_issue(directory, latest)
            result = handler(arguments, verification_contract=latest['verification_contract'],
                             execution_blocked=None if assessed else 'read-assessment-required')
        elif verb == 'record-probes':
            from codex_review import adjudication
            payload = loops._json(evidence.payload_path(round_dir, options['probes_file'], 'probes_file'))
            references = latest.get('carried_findings', {}).get(latest['current_round'], {})
            for annotation in payload.get('annotations', []) if isinstance(payload, dict) and isinstance(payload.get('annotations'), list) else []:
                if isinstance(annotation, dict) and annotation.get('finding_id') in references and (
                        annotation.get('disposition') == 'rejected' or annotation.get('read') == 'unsupported'):
                    adjudication.require_retained_reopening(latest, references[annotation['finding_id']], round_dir,
                                                           annotation.get('reopening'))
            unresolved = adjudication.unresolved_disputes(round_dir, latest, payload)
            if unresolved:
                updated = dict(latest, pause=dict(kind='stalled-dispute', findings=unresolved,
                                                  attempt=latest['rebuttals'][latest['current_round']]['attempt']))
                return updated, handback(verb, directory, 'stalled-dispute', findings=unresolved), dict(kind='stalled-dispute')
            result = handler(arguments, selected_records=selected)
        elif verb == 'record-apply':
            result = handler(arguments, path_amendments=latest.get('amendments', {}).get(latest['current_round'], []),
                             selected_records=selected)
        else:
            result = handler(arguments)
        publication = lifecycle.pending(latest, round_dir)
        updated = lifecycle.adopted(latest, round_dir, publication) if publication else latest
        return updated, result, dict(kind=verb, round=latest['current_round'], exit=result['exit'])
    result = loops.mutate(directory, current['generation'], operation)
    return _envelope(verb, directory, result['exit'], result.get('error'),
                     dict(status_payload(directory), **(result.get('payload') or {})))


def amend_paths(directory, options, current):
    from codex_review import main
    response._require(options.get('round') == current['current_round'], 'round-required',
                      'amendment requires the current round')
    response._require(current['phase'] == 'probed' and not current['pause'], 'wrong-stage',
                      'amend paths after probes and before recording apply')
    response._require(options.get('amendment_file'), 'no-input', 'amend-paths needs --amendment-file')
    round_id = current['current_round']
    job_dir = os.path.join(os.path.dirname(directory), round_id)
    submitted = loops._json(evidence.payload_path(job_dir, options['amendment_file'], 'amendment_file'))
    response._require(isinstance(submitted, dict), 'bad-amendment', 'amendment must be an object')
    def operation(latest):
        with jobs.mutex(job_dir):
            response._require(not os.path.exists(os.path.join(job_dir, 'apply.json')), 'already-applied',
                              'amendment must precede apply publication')
            _, checkpoint = loops._read(directory)
            selected = records.selected(latest, job_dir)
            for field, path in [('checkpoint_sha256', checkpoint), ('probes_sha256', selected['probes']),
                                ('report_sha256', selected['report'])]:
                response._require(submitted.get(field) == images.sha256_file(path), 'stale-amendment',
                                  'amendment must bind the current records', field=field)
            target = main.load_target(job_dir)
            conflict = main.apply_ownership('amend-paths', job_dir, target, 'require_owner')
            if conflict:
                return None, conflict, None
            record = loops._json(selected['probes'])
            prior = latest.get('amendments', {}).get(round_id, [])
            response._require(context.apply_applicable(target, apply_module.declared_paths(
                target, amendments.effective(record, prior))), 'reference-drift',
                'candidate references and undeclared external context must remain applicable')
            amendment = amendments.validate(target, record, prior, submitted)
            retained = prior + [amendment]
            effective = amendments.effective(record, retained)
            paths = {tuple(sorted(findings.reference_for(round_id, a['finding_id'],
                     latest.get('carried_findings', {}).get(round_id)).items())): a.get('fix_paths', [])
                     for a in effective['annotations']}
            ledger = [dict(item, fix_paths=paths[tuple(sorted(item['reference'].items()))])
                      if tuple(sorted(item['reference'].items())) in paths else item for item in latest.get('findings', [])]
            updated = dict(latest, findings=ledger, amendments=dict(latest.get('amendments', {}), **{round_id: retained}))
            return updated, None, dict(kind='amend-paths', round=round_id, amendment=amendment)
    refusal = loops.mutate(directory, current['generation'], operation)
    return (_envelope('amend-paths', directory, refusal['exit'], refusal.get('error')) if refusal
            else _envelope('amend-paths', directory, payload=status_payload(directory)))


def _log_assessment(attempt):
    path = os.path.join(attempt['attempt_dir'], 'stdout.jsonl')
    events, complete, malformed = [], False, False
    try:
        with open(path, 'rb') as handle:
            raw = handle.read()
    except OSError:
        return dict(log_sha256=None, event_lines=[], complete=False)
    for line_number, line in enumerate(raw.splitlines(), 1):
        try:
            event = json.loads(line)
            if event.get('type') == 'turn.completed':
                complete = True
            item = event.get('item', event)
            if item.get('type') == 'command_execution':
                events.append(line_number)
        except (ValueError, TypeError, AttributeError):
            malformed = True
    return dict(log_sha256=images.sha256_bytes(raw), event_lines=events, complete=complete and not malformed)


def assessment_attempts(current, round_id):
    return [a for a in current['attempts'] if a['round'] == round_id and
            a['outcome'] == 'judgment' and not a.get('was_retired')]


def assessment_for(current, attempt):
    return (current['assessments'].get(attempt['round']) if attempt['kind'] == 'cold' else
            current.get('rebuttal_assessments', {}).get(attempt['id']))


def read_bindings(current, round_id):
    return [dict(round=round_id, attempt=a['id'], assessment=assessment_for(current, a))
            for a in assessment_attempts(current, round_id)]


def assessment_field(attempt):
    return 'report_sha256' if attempt['kind'] == 'cold' else 'adjudication_sha256'


def assessment_path(directory, attempt):
    return (os.path.join(os.path.dirname(directory), attempt['round'], 'report.json') if attempt['kind'] == 'cold'
            else os.path.join(attempt['attempt_dir'], 'adjudication.json'))


def assessment_needs_complete_log(attempt, disposition):
    return disposition == 'no-observed-breach' or attempt['kind'] == 'rebuttal' and disposition == 'history-consumed'


def attempt_assessment_issue(directory, attempt, assessment):
    if not assessment:
        return 'missing-assessment'
    try:
        if assessment.get(assessment_field(attempt)) != images.sha256_file(assessment_path(directory, attempt)):
            return 'stale-report-digest'
        inspected = _log_assessment(attempt)
        if assessment.get('log_sha256') != inspected['log_sha256']:
            return 'stale-log-digest'
        if assessment_needs_complete_log(attempt, assessment['disposition']) and (
                not inspected['complete'] or assessment.get('event_lines') != inspected['event_lines']):
            return 'incomplete-read-assessment'
        if assessment['disposition'] == 'read-unresolved':
            return 'read-unresolved'
    except (OSError, ValueError, KeyError, TypeError):
        return 'assessment-evidence-unavailable'
    return None


def assessment_issue(directory, current, round_id=None, assessment=None, attempt_id=None):
    attempts = assessment_attempts(current, round_id or current['current_round'])
    if assessment is not None:
        attempt = next((a for a in attempts if a.get('id') == attempt_id), None) if attempt_id else next(
            (a for a in attempts if a['kind'] == 'cold'), None)
        return attempt_assessment_issue(directory, attempt, assessment) if attempt else 'assessment-attempt-missing'
    return next((issue for a in attempts
                 for issue in [attempt_assessment_issue(directory, a, assessment_for(current, a))] if issue), None)


def continue_loop(directory, options, current):
    if not options.get('decision_file'):
        retry_after = availability(current)
        if retry_after:
            return handback('continue', directory, 'availability', retry_after=retry_after)
        if late_evidence.pending(directory, current):
            return handback('continue', directory, 'late-evidence')
        if lifecycle.pending(current, os.path.join(os.path.dirname(directory), current['current_round'])):
            lifecycle.recover(directory, current)
            return _envelope('continue', directory, payload=status_payload(directory))
        if current['pause'] and current['pause']['kind'] == 'availability':
            deadline = datetime.fromisoformat(current['pause']['retry_after'])
            response._require(deadline.tzinfo is not None, 'bad-availability-deadline',
                              'provider deadline must include its timezone')
            if time.time() < deadline.timestamp():
                return handback('continue', directory, 'availability', retry_after=current['pause']['retry_after'])
            current = loops.append(directory, current['generation'], dict(current, pause=None),
                                   dict(kind='availability-elapsed', retry_after=current['pause']['retry_after']))
        if current['phase'] == 'capture-pending' or (current['phase'] != 'closed' and not os.path.isfile(os.path.join(os.path.dirname(directory), current['current_round'], 'target.json'))):
            recover_publication(directory, current)
            return _envelope('continue', directory, payload=status_payload(directory))
        if current['phase'] == 'round-complete':
            return finish_round(directory, current)
        if current['phase'] == 'assessment':
            return handback('continue', directory, 'read-assessment-required')
        return _envelope('continue', directory, payload=status_payload(directory))
    path = evidence.payload_path(directory, options['decision_file'], 'decision_file')
    decision = loops._json(path)
    response._require(isinstance(decision, dict), 'bad-decision', 'decision must be an object')
    _, checkpoint = loops._read(directory)
    response._require(decision.get('checkpoint_sha256') == images.sha256_file(checkpoint),
                      'stale-checkpoint', 'decision must bind the current checkpoint')
    response._require(isinstance(decision.get('rationale'), str) and decision['rationale'].strip(),
                      'missing-rationale', 'every decision requires a rationale')
    if decision.get('kind') == 'read-assessment':
        assess_reads(directory, current, decision)
    elif decision.get('kind') == 'select-verification':
        select_verification(directory, current, decision)
    elif decision.get('kind') == 'finding-resolution':
        issue = assessment_issue(directory, current)
        if issue:
            return handback('continue', directory, 'read-assessment-required', assessment_issue=issue)
        resolve_finding(directory, current, decision)
    elif decision.get('kind') in ('resolve-call', 'retire-attempt'):
        recover_call(directory, current, decision)
    elif decision.get('kind') == 'budget-extension':
        extend_budget(directory, current, decision)
    elif decision.get('kind') in ('authorized-commit', 'drift-resolution'):
        from codex_review import transitions
        transitions.candidate(directory, current, decision)
    elif decision.get('kind') == 'dispute-resolution':
        from codex_review import adjudication
        adjudication.resolve_dispute(directory, current, decision)
    else:
        raise response.ContractViolation('bad-decision-kind', 'unsupported decision kind',
                                         field='kind', expected=['read-assessment', 'select-verification', 'finding-resolution',
                                                                 'resolve-call', 'retire-attempt', 'budget-extension',
                                                                 'authorized-commit', 'drift-resolution', 'dispute-resolution'])
    return _envelope('continue', directory, payload=status_payload(directory))


def assess_reads(directory, current, decision):
    round_id = decision.get('round')
    response._require(round_id == current['current_round'], 'round-required', 'assessment must bind the current round')
    attempts = assessment_attempts(current, round_id)
    attempt = (next((a for a in attempts if a.get('id') == decision['attempt']), None) if 'attempt' in decision
               else attempts[-1] if attempts else None)
    response._require(attempt is not None, 'no-judgment', 'only judgment-bearing attempts need assessment')
    response._require(decision.get(assessment_field(attempt)) == images.sha256_file(assessment_path(directory, attempt)),
                      'stale-report-digest', 'assessment must bind this judgment')
    inspected = _log_assessment(attempt)
    response._require('log_sha256' in decision and decision['log_sha256'] == inspected['log_sha256'],
                      'stale-log-digest', 'assessment must bind the raw attempt log')
    disposition = decision.get('disposition')
    response._require(disposition in ('no-observed-breach', 'history-consumed', 'read-unresolved', 'read-unverified'),
                      'bad-read-assessment', 'unsupported read disposition')
    previous = assessment_for(current, attempt)
    response._require(decision.get('previous_assessment_sha256') == (qualification.digest(previous) if previous else None),
                      'stale-assessment', 'assessment resolution must cite its predecessor')
    if assessment_needs_complete_log(attempt, disposition):
        response._require(inspected['complete'] and decision.get('event_lines') == inspected['event_lines'],
                          'incomplete-read-assessment', 'clean assessment requires complete logs and every command event')
    disqualified = set(current.get('disqualified_rounds', []))
    if disposition == 'read-unverified' or disposition == 'history-consumed' and attempt['kind'] == 'cold':
        disqualified.add(round_id)
    field, key = ('assessments', round_id) if attempt['kind'] == 'cold' else ('rebuttal_assessments', attempt['id'])
    updated = dict(current, **{field: dict(current.get(field, {}), **{key: decision})})
    phase = 'reviewed' if current['phase'] == 'assessment' and not assessment_issue(directory, updated) else current['phase']
    updated = dict(updated, phase=phase,
                   pause=(current['pause'] if current['pause'] and current['pause']['kind'] != 'read-unresolved' else
                          {'kind': 'read-unresolved'} if disposition == 'read-unresolved' else None),
                   disqualified_rounds=sorted(disqualified),
                   counting=counting.reset(current['counting']) if round_id in disqualified else current['counting'])
    loops.append(directory, current['generation'], updated, decision)


def finish_round(directory, current):
    round_id = current['current_round']
    response._require(round_id not in current['processed_rounds'], 'round-already-counted', 'round already counted')
    issue = assessment_issue(directory, current)
    if issue:
        return handback('continue', directory, 'read-assessment-required', assessment_issue=issue)
    round_dir = os.path.join(os.path.dirname(directory), round_id)
    target = loops._json(os.path.join(round_dir, 'target.json'))
    current, selected = check_launch_qualification(directory, current, target)
    current, exclusions = check_exclusion_qualifications(directory, current, target)
    if not selected['valid'] or current['pause'] or not all(q['valid'] for q in exclusions):
        return handback('continue', directory, 'qualification-required', qualifications=[selected] + exclusions)
    current, reviewer_qualifications = check_reviewer_qualifications(directory, current, target)
    if current['pause'] or not all(q['valid'] for q in reviewer_qualifications):
        return handback('continue', directory, 'qualification-required', qualifications=reviewer_qualifications)
    current, author = check_author_qualifications(directory, current)
    if current['pause'] or not all(item['valid'] for item in author):
        return handback('continue', directory, 'qualification-required', qualifications=author)
    probe_binding = probe_integrity.binding(current, round_dir)
    response._require(probe_integrity.retained(os.path.dirname(directory), probe_binding),
                      'probe-evidence-lost', 'counting needs retained probe records, seals and execution bindings')
    annotations = loops._json(records.selected(current, round_dir)['probes'])['annotations']
    verified = loops._json(os.path.join(round_dir, 'verification.json'))
    verification.revalidate(round_dir, verified)
    response._require(loops._json(os.path.join(round_dir, 'apply.json'))['probes_sha256'] == probe_binding['sha256'],
                      'stale-probes-digest', 'counting must use the probes bound by apply')
    accepted = sum(a.get('disposition') == 'accepted' for a in annotations)
    finding_blockers = (bool(findings.blocking([f for f in current.get('findings', []) if f['reference']['round'] == round_id])) or
                        verified['probe_completion'] != 'complete' or
                        verified['application_result'] in ('apply-incomplete', 'scope-violation'))
    verification_blocked = verified['verification_result'] in ('failed', 'denied', 'not-reached')
    blockers = finding_blockers or verification_blocked
    if (apply_module.identity_sha(target) != verified['post_fix_sha'] or
            not context.unchanged(context.after_apply(round_dir, target))):
        return handback('continue', directory, 'candidate-drift')
    assessment = current['assessments'].get(round_id, {})
    eligible = (round_id not in current.get('uncredited_rounds', []) and
                not (current['verification_contract']['intent'] == 'deferred' and current['verification_contract']['command'] is None) and
                assessment.get('disposition') == 'no-observed-breach' and
                round_id not in current.get('disqualified_rounds', []))
    counted = counting.round_finished(current['counting'], effort=current['counting']['next_effort'],
                                     eligible=eligible, accepted=accepted,
                                     blockers=blockers or bool(findings.outstanding(current.get('findings', []))))
    if counted['streak']:
        relied = current['counting'].get('qualification_refs', []) + author_reliances(current, round_dir)
        relied += [a['qualification'] for a in current['attempts']
                   if a['round'] == round_id and a.get('qualification') and a['outcome'] == 'judgment']
        relied += [item['qualification'] for item in target.get('dependencies', []) if item['exclude']]
        counted['qualification_refs'] = list({item['digest']: item for item in relied}.values())
        counted['read_bindings'] = current['counting'].get('read_bindings', []) + read_bindings(current, round_id)
        counted['snapshot_rounds'] = current['counting'].get('snapshot_rounds', []) + [round_id]
        counted['probe_bindings'] = current['counting'].get('probe_bindings', []) + [probe_binding]
        counted['verification_bindings'] = current['counting'].get('verification_bindings', []) + [verification.binding(round_dir)]
    updated = dict(current, counting=counted, processed_rounds=current['processed_rounds'] + [round_id],
                   phase='closed' if counted['converged'] else 'recapture',
                   pause=({'kind': 'unresolved-findings'} if finding_blockers else
                          dict(kind='verification-incomplete', result=verified['verification_result']) if verification_blocked else None))
    loops.append(directory, current['generation'], updated,
                 dict(kind='count-round', round=round_id, accepted=accepted, eligible=eligible, blockers=blockers))
    return _envelope('continue', directory, payload=status_payload(directory))


def abandon(directory, options, current):
    from codex_review import main, stage
    response._require(bool(options.get('reason')), 'no-reason', 'abandon needs a reason')
    response._require(current['phase'] != 'calling', 'pending-call', 'reconcile a launched call before abandoning')
    response._require(current['phase'] != 'closed', 'already-terminal', 'loop is closed')
    round_dir = os.path.join(os.path.dirname(directory), current['current_round'])
    decision = dict(kind='abandon', reason=options['reason'], asserted_by='user')
    arguments = [round_dir, '--reason', options['reason']]
    if options.get('denied_interactively'):
        arguments += ['--denied-interactively']
        decision['denial_kind'] = 'interactive'
    def operation(latest):
        if os.path.isdir(round_dir) and not stage.is_terminal(stage.describe(round_dir)['stage']):
            selected = records.selected(latest, round_dir)
            result = main.abandon(arguments,
                                  probes_path=selected['probes'] if records.digest(selected['probes']) else selected['prior_probes'])
            if result['exit']:
                return None, result, None
        return dict(latest, phase='closed', pause=None), None, decision
    result = loops.mutate(directory, current['generation'], operation)
    return _envelope('abandon', directory, payload=status_payload(directory)) if result is None else result


def equivalence(target):
    return dict(mode=target['mode'], capture_mode=target.get('capture_mode'),
                expected_paths=(target.get('expected_paths') or {}).get('paths'),
                projections=[{k: item[k] for k in ('path', 'before', 'after', 'projection') if k in item}
                             for item in target['artifacts_detail']],
                companions=[dict(id=c['id'], image=c['image']) for c in target.get('companions', [])],
                dependencies=[dict(id=d['id'], manifest=d['manifest'], exclude=d['exclude'])
                              for d in target.get('dependencies', [])],
                context=target.get('context_manifest'), policy=target.get('policy_equivalence_sha256'),
                global_policy_sources=target.get('global_policy_sources'),
                note=target.get('note'), driver=target.get('driver_hash'), model=target.get('model'),
                verification=dict(intent=target.get('verification_intent'), command=target.get('verify_command'),
                                  cwd=target['exec_dir'], cache_policy=target.get('cache_policy')))


def recapture(directory, options, current):
    from codex_review import main, stage
    response._require(current['phase'] == 'recapture' and not current['pause'],
                      'wrong-stage', 'loop is not ready for recapture')
    old_dir = os.path.join(os.path.dirname(directory), current['current_round'])
    old = main.load_target(old_dir)
    authorization = current.get('recapture_authorization')
    author_key = execution.key(dict(old, **authorization['scope']['inputs']) if authorization else old)
    author_target = dict(old, **authorization['scope']['inputs']) if authorization else old
    author_changed = any(q['key'] != execution.rebind_key(author_target, q['key']) for q in author_reliances(current, old_dir))
    if author_changed:
        if not authorization or authorization.get('author_key') != author_key:
            return handback('recapture', directory, 'qualification-key-changed', required=author_key)
        author = qualification.lookup(os.path.dirname(directory), author_key)
        if not author['valid']:
            return handback('recapture', directory, 'qualification-required', qualifications=[author])
    if authorization:
        from codex_review import transitions
        response._require(authorization['round'] == current['current_round'], 'stale-transition',
                          'candidate authority belongs to another round')
        if not transitions.applicable(old, authorization):
            return handback('recapture', directory, 'candidate-drift')
    response._require(stage.is_terminal(stage.describe(old_dir)['stage']),
                      'round-not-closed', 'finish the current round before recapture')
    if old['mode'] == 'plan':
        response._require(not ({'base', 'head', 'expect_paths'} & set(options)), 'option-not-applicable',
                          'plan loops cannot honor code capture options')
        argv = ['plan', old['plan_identity']['plan_path'], '--loop']
    else:
        from codex_review import transitions
        head = options.get('head', current.get('authorized_head') or old['repo_identity']['head_oid'])
        response._require(head == current.get('authorized_head', old['repo_identity']['head_oid']),
                          'head-not-authorized', 'replacement head needs a recorded candidate transition')
        base = authorization['base'] if authorization else old['base_ref']
        if options.get('base'):
            response._require(transitions.resolve_base(old['repo_root'], options['base']) == base,
                              'base-not-authorized', 'a rebuilt base requires a recorded candidate transition')
        argv = ['code', '--loop', '--base', base, '--head', head]
    if old.get('context_repository'):
        argv += ['--repo', old['context_repository']]
    else:
        argv += ['--no-repo']
    if old['verification_intent'] == 'declined':
        argv += ['--no-verify', '--reason', old['verification_reason']]
    elif old['verification_intent'] == 'deferred':
        argv += ['--defer-verify', '--reason', old['verification_reason']]
    else:
        argv += ['--verify', old['verify_command']]
    for key in ('note', 'model'):
        if old.get(key):
            argv += ['--' + key, old[key]]
    argv += ['--effort', current['counting']['next_effort']]
    capture_limits = current['request'].get('capture_limits')
    if capture_limits is None:
        capture_limits = main.resolve_limits(main.parse_common(current['request']['argv'])[1])[0]
    for name, limit in capture_limits.items():
        if limit is not None:
            argv += ['--' + name.replace('_', '-'), str(limit)]
    if old.get('no_apply'):
        argv += ['--no-apply']
    staging = os.path.join(directory, 'staging')
    os.makedirs(staging, exist_ok=True)
    declared_scope = authorization['scope']['declarations'] if authorization else {}
    assertion = declared_scope.get('expected_paths', (old.get('expected_paths') or {}).get('paths'))
    if assertion is not None:
        assertion_path = os.path.join(staging, 'paths-' + state.new_nonce() + '.json')
        state.atomic_json(assertion_path, assertion)
        if options.get('expect_paths'):
            response._require(context.expected_paths(options['expect_paths'], assertion)[0]['paths'] == sorted(assertion),
                              'scope-not-authorized', 'path assertion replacement needs a scope decision')
        argv += ['--expect-paths', assertion_path]
    elif options.get('expect_paths'):
        raise response.ContractViolation('scope-not-authorized', 'new path assertion needs a scope decision')
    for name in ('companions', 'dependencies'):
        fields = ('id', 'path', 'exclude') if name == 'dependencies' else ('id', 'path')
        inherited = declared_scope.get(name, [{k: item[k] for k in fields if k in item} for item in old.get(name, [])])
        supplied = options.get(name + '_file')
        if supplied:
            repository = old.get('repo_root') or old.get('context_repository')
            normalize = context.companion_declarations if name == 'companions' else context.dependency_declarations
            declared = [{k: item[k] for k in fields if k in item} for item in normalize(supplied, repository)]
            response._require(declared == inherited, 'scope-not-authorized', 'context set replacement needs a scope decision')
        path = os.path.join(staging, name + '-' + state.new_nonce() + '.json')
        state.write_json(path, inherited)
        argv += ['--' + name + '-file', path]
    verification_path = os.path.join(old_dir, 'verification.json')
    if not authorization:
        unchanged = (apply_module.identity_sha(old) == loops._json(verification_path)['post_fix_sha']
                     if os.path.exists(verification_path) else context.applicable(old))
        if not unchanged:
            return handback('recapture', directory, 'candidate-drift')
    def publisher(root, round_id, build, target):
        selected_key = launch_key(target, captured=True)
        expected_key = authorization['launch_key'] if authorization else current.get('bound_launch_key')
        response._require(expected_key in (None, selected_key), 'qualification-key-changed',
                          'changed launch settings require authorized recapture')
        equivalent = (equivalence(old) == equivalence(target) and
                      current.get('bound_launch_key') in (None, selected_key) and not author_changed)
        if authorization:
            response._require(transitions.applicable(old, authorization), 'candidate-drift',
                              'candidate moved while recapture was frozen')
            if authorization['equivalence'] == 'matching' and not equivalent:
                loops.append(directory, current['generation'],
                             dict(current, counting=counting.reset(current['counting']),
                                  pause=dict(kind='equivalence-mismatch')),
                             dict(kind='equivalence-mismatch', round=current['current_round']))
                raise response.ContractViolation('equivalence-mismatch',
                                                 'rebuilt inputs do not match; record a reset decision')
            equivalent = equivalent and authorization['equivalence'] == 'matching'
        loops.publish_capture(directory, current['generation'], round_id, build, equivalent, selected_key)
    retained_paths = []
    if old['mode'] == 'code':
        retained = {a['path'] for a in old['artifacts_detail']}
        for finding in current.get('findings', []):
            if finding['status'] == 'open' and finding['disposition'] == 'accepted':
                retained.update(path for path in finding.get('fix_paths', []) if context.captured_bound_image(
                    author_target, os.path.join(old['repo_root'], path)) is None)
        retained_paths = assertion if assertion is not None else sorted(retained)
    result = main.prepare(argv, loop_id=current['loop_id'], verification_contract=current['verification_contract'],
                          publisher=publisher, retained_paths=retained_paths)
    if result['exit']:
        loops.append(directory, current['generation'],
                     dict(current, pause={'kind': 'capture-refused', 'error': result.get('error')}),
                     dict(kind='recapture-refused', error=result.get('error')))
    return _envelope('recapture', directory, result['exit'], result.get('error'),
                     dict(status_payload(directory), capture=result.get('payload')))


def select_verification(directory, current, decision):
    response._require(current['phase'] not in ('calling', 'closed'), 'wrong-stage',
                      'cannot select verification during a call or after loop closure')
    response._require(decision.get('round') == current['current_round'], 'round-required',
                      'selection must cite the current round')
    job_dir = os.path.join(os.path.dirname(directory), current['current_round'])
    target = loops._json(os.path.join(job_dir, 'target.json'))
    selected = verification.select(current['verification_contract'], target, job_dir, decision)
    loops.append(directory, current['generation'], dict(current, verification_contract=selected,
                 counting=counting.reset(current['counting']),
                 uncredited_rounds=current.get('uncredited_rounds', []) + [current['current_round']]), decision)


def resolve_finding(directory, current, decision):
    from codex_review import finding_resolution
    response._require(current['phase'] in ('reviewed', 'probed', 'applied', 'round-complete', 'recapture'),
                      'wrong-stage', 'finding closure follows read assessment and precedes clean credit')
    reference = decision.get('reference')
    matches = [item for item in current.get('findings', []) if item['reference'] == reference]
    response._require(len(matches) == 1, 'unknown-finding', 'closure must identify a retained round-local finding')
    if decision.get('disposition') == 'reassess':
        return finding_resolution.reassess(directory, current, decision, matches[0])
    if decision.get('disposition') == 'accepted':
        return finding_resolution.accept_retained(directory, current, decision, matches[0])
    if decision.get('disposition') == 'refuted' and 'assessment' in decision:
        response._require('related_reference' not in decision, 'conflicting-assessment',
                          'choose an independent assessment or a current reviewer finding')
        return finding_resolution.refute_retained(directory, current, decision, matches[0])
    if decision.get('disposition') in ('recurring', 'refuted'):
        return finding_resolution.relate(directory, current, decision, matches[0])
    if decision.get('disposition') == 'out-of-scope':
        return finding_resolution.exclude(directory, current, decision, matches[0])
    response._require(current['phase'] != 'recapture', 'wrong-stage', 'fixed closure needs a current re-examination')
    response._require(decision.get('disposition') == 'fixed', 'bad-finding-resolution',
                      'fixed closure must be explicit')
    reviewer_round = decision.get('reviewer_round')
    verified_round = decision.get('verification_round')
    response._require(reviewer_round == current['current_round'] and verified_round in current['rounds'] and
                      reference['round'] != reviewer_round, 'bad-reexamination',
                      'fixed closure needs a later current review and a retained verification round')
    fix_round = decision.get('fix_round', verified_round)
    response._require(fix_round in current['rounds'], 'bad-fix-round', 'fix evidence must belong to this loop')
    order = {round_id: index for index, round_id in enumerate(current['rounds'])}
    response._require(reference['round'] in order and
                      order[reference['round']] <= order[fix_round] <= order[verified_round] <= order[reviewer_round] and
                      order[fix_round] < order[reviewer_round], 'invalid-fix-order',
                      'fix must follow the finding, precede verification and precede cold re-examination')
    root = os.path.dirname(directory)
    review_dir, verified_dir = os.path.join(root, reviewer_round), os.path.join(root, verified_round)
    for name, path in [('report_sha256', os.path.join(review_dir, 'report.json')),
                       ('verification_sha256', os.path.join(verified_dir, 'verification.json'))]:
        response._require(decision.get(name) == images.sha256_file(path), 'stale-closure-evidence',
                          'closure must bind the exact review and verification records', field=name)
    reviewed = loops._json(os.path.join(review_dir, 'target.json'))
    verified = loops._json(os.path.join(verified_dir, 'target.json'))
    result = loops._json(os.path.join(verified_dir, 'verification.json'))
    verification.revalidate(verified_dir, result)
    proofs = execution.reliances(result.get('verification_evidence', {}).get('execution', {}))
    response._require(proofs and all(qualification.continuous(root, proof, qualification.lookup(root, proof['key'])) for proof in proofs),
                      'fix-evidence-unqualified', 'fix closure requires uninterrupted qualification for its verification evidence')
    response._require(context.applicable(reviewed) and apply_module.identity_sha(verified) == result['post_fix_sha'],
                      'stale-fix-verification', 'the verified candidate must be the currently captured candidate')
    response._require(result.get('verification_contract', verification.contract(verified)) == verification.contract(reviewed),
                      'verification-contract-mismatch', 'fix verification and re-examination need the same contract')
    fix_dir = os.path.join(root, fix_round)
    fix_target = loops._json(os.path.join(fix_dir, 'target.json'))
    fixed_paths = result['changed_paths']
    if fix_round != verified_round:
        fix_path = os.path.join(fix_dir, 'apply.json')
        response._require(decision.get('fix_apply_sha256') == images.sha256_file(fix_path), 'stale-closure-evidence',
                          'separate fix evidence must bind its exact apply record')
        fixed_paths = loops._json(fix_path)['changed_paths']
    fix_root = fix_target.get('repo_root') or os.path.dirname(fix_target['plan_identity']['plan_path'])
    result = dict(result, changed_paths=[os.path.relpath(path, fix_root) for path in fixed_paths])
    current, launch = check_launch_qualification(directory, current, reviewed)
    current, exclusions = check_exclusion_qualifications(directory, current, reviewed)
    current, reviewer_qualifications = check_reviewer_qualifications(directory, current, reviewed)
    eligible = (current['assessments'].get(reviewer_round, {}).get('disposition') == 'no-observed-breach' and
                reviewer_round not in current.get('disqualified_rounds', []) and
                reviewer_round not in current.get('uncredited_rounds', []) and not current['pause'] and
                launch['valid'] and all(q['valid'] for q in exclusions + reviewer_qualifications))
    response._require(all(loops.snapshot_retained(root, current, round_id)
                          for round_id in (reviewer_round, verified_round, fix_round)),
                      'capture-bytes-corrupt', 'fixed closure needs the original retained review, fix and verification snapshots')
    verified_context = context.after_apply(verified_dir, verified)
    reviewed_paths = [a['path'] for a in reviewed['artifacts_detail']]
    for path in fixed_paths:
        captured = context.captured_bound_image(reviewed, path)
        if captured is not None and captured == context.captured_bound_image(verified_context, path):
            reviewed_paths.append(os.path.relpath(path, fix_root))
    closed = findings.fixed(matches[0], result, reviewed_paths, eligible=eligible, decision=decision)
    closed.update(closure_read_bindings=read_bindings(current, reviewer_round),
                  closure_qualification_refs=exclusions + reviewer_qualifications)
    ledger = [closed if item['reference'] == reference else item for item in current['findings']]
    loops.append(directory, current['generation'], dict(current, findings=ledger), decision)


def recover_call(directory, current, decision):
    def operation(latest):
        updated = _recover_call(directory, latest, decision)
        return updated, None, decision
    loops.mutate(directory, current['generation'], operation)


def extend_budget(directory, current, decision):
    response._require(current['phase'] not in ('calling', 'closed'), 'wrong-stage',
                      'resolve pending calls before extending an open loop')
    response._require(decision.get('asserted_by') == 'user' and
                      isinstance(decision.get('authorization'), str) and decision['authorization'].strip(),
                      'user-decision-required', 'budget extension needs a cited explicit user decision')
    limits = decision.get('limits')
    response._require(isinstance(limits, dict) and set(limits) == set(counting.LIMITS) and
                      all(type(limits[k]) is int and limits[k] >= current['limits'][k] for k in counting.LIMITS) and
                      limits != current['limits'], 'bad-budget-extension',
                      'supply all ceilings; increase at least one and decrease none', field='limits',
                      expected=list(counting.LIMITS))
    loops.append(directory, current['generation'], dict(current, limits=limits), decision)


def _recover_call(directory, current, decision):
    from codex_review import main, stage, transport
    retired = next((a for a in current['attempts'] if a['id'] == decision.get('attempt') and
                    (a['outcome'] == 'retired-unknown' or a.get('was_retired'))), None)
    if retired and decision['kind'] == 'resolve-call':
        return late_evidence.reconcile(directory, current, decision)
    response._require(current['phase'] == 'calling' and current['attempts'], 'no-pending-call',
                      'recovery requires a pending call')
    attempt = current['attempts'][-1]
    response._require(attempt['outcome'] == 'pending' and decision.get('attempt') == attempt['id'],
                      'attempt-mismatch', 'recovery must name the pending attempt')
    job_dir = os.path.join(os.path.dirname(directory), attempt['round'])
    attempt_dir = os.path.join(job_dir, 'reviewer-attempts', attempt['id'])
    launch_path = os.path.join(attempt_dir, 'launch.json')
    launch = loops._json(launch_path) if os.path.isfile(launch_path) else None
    receipt_path = os.path.join(attempt_dir, 'completion.json')
    receipt = loops._json(receipt_path) if os.path.isfile(receipt_path) else None
    nonlaunch = transport.nonlaunch_evidence(attempt_dir, job_dir) if receipt is None else None
    if nonlaunch is not None:
        response._require(decision['kind'] == 'resolve-call', 'outcome-recoverable',
                          'durable nonlaunch evidence must be reconciled with resolve-call')
        completed = dict(attempt, attempt_dir=attempt_dir, outcome='not-launched', charged=False,
                         completion_evidence='nonlaunch-marker', nonlaunch_evidence=nonlaunch)
        return dict(current, phase=attempt['return_phase'] if attempt.get('kind') == 'rebuttal' else 'prepared',
                    pause=None, attempts=current['attempts'][:-1] + [completed])
    stopped = quiescence.require(launch, decision, job_dir, completion=receipt is not None)
    completed = dict(attempt, attempt_dir=attempt_dir)
    recovered_answer = reviewer.published_answer(job_dir, attempt_dir, main.load_target(job_dir), attempt, receipt)
    if recovered_answer is None and receipt is None:
        recovered_answer = reviewer.answer_without_receipt(attempt_dir, main.load_target(job_dir), attempt)
    pause = None
    if decision['kind'] == 'retire-attempt':
        response._require(decision.get('asserted_by') == 'user' and
                          isinstance(decision.get('authorization'), str) and decision['authorization'].strip(),
                          'user-decision-required', 'retirement requires a cited explicit user decision')
        response._require(receipt is None and recovered_answer is None, 'outcome-recoverable',
                          'a retained completion or valid answer must be reconciled with resolve-call')
        completed.update(outcome='retired-unknown', retired_evidence=late_evidence.snapshot(attempt_dir),
                         quiescence=stopped)
        if attempt.get('kind') == 'rebuttal':
            phase = attempt['return_phase']
            pause = dict(kind='stalled-dispute', reason='retired-rebuttal', attempt=attempt['id'])
        else:
            phase = 'recapture'
            if not stage.is_terminal(stage.describe(job_dir)['stage']):
                main.abandon([job_dir, '--reason', 'Explicit retirement: ' + decision['authorization']])
    else:
        response._require(receipt is not None or recovered_answer is not None, 'call-outcome-unknown',
                          'PID absence alone does not establish an outcome; explicit retirement remains available')
        outcome = recovered_answer if recovered_answer is not None else reviewer.collected(attempt_dir)
        if attempt.get('kind') == 'rebuttal':
            from codex_review import adjudication
            updated, _, _ = adjudication.completed_state(directory, current, attempt, outcome)
            return updated
        completed.update(thread_id=outcome['thread_id'], timing=outcome['timing'],
                         completion_evidence=outcome['completion_evidence'])
        if outcome.get('retry_after'):
            completed['retry_after'] = outcome['retry_after']
        report_path = os.path.join(job_dir, 'report.json')
        if outcome['launched']:
            if not os.path.isfile(report_path) and not stage.is_terminal(stage.describe(job_dir)['stage']):
                invocation = loops._json(os.path.join(attempt_dir, 'invocation.json'))
                target = main.load_target(job_dir)
                with jobs.mutex(job_dir):
                    main.publish_review(job_dir, invocation['codex_path'], target, invocation['model'],
                                        invocation['effort'], outcome)
            if os.path.isfile(report_path):
                report = loops._json(report_path)
                response._require(report.get('run_metadata', {}).get('attempt_dir') == attempt_dir,
                                  'attempt-mismatch', 'published judgment belongs to another attempt')
                response.validate(json.dumps({k: v for k, v in report.items() if k != 'run_metadata'}),
                                  main.load_target(job_dir))
                completed['outcome'] = 'judgment'
            else:
                completed['outcome'] = 'no-judgment'
                completed['failure_kind'] = 'unavailable' if outcome['failed'] else 'other'
                if outcome['retry_after']:
                    pause = dict(kind='availability', retry_after=outcome['retry_after'])
        else:
            response._require(os.path.isfile(os.path.join(attempt_dir, 'not-launched.json')),
                              'no-launch-unproven', 'canceling a charge requires no-launch evidence')
            completed.update(outcome='not-launched', charged=False)
        phase = 'assessment' if completed['outcome'] == 'judgment' else 'prepared'
        if stage.is_terminal(stage.describe(job_dir)['stage']):
            phase = 'recapture'
    updated = dict(current, phase=phase, pause=pause, attempts=current['attempts'][:-1] + [completed],
                   counting=counting.reset(current['counting']) if completed['outcome'] == 'retired-unknown' and attempt.get('kind') == 'cold'
                   else current['counting'])
    return retain_call_drift(retain_freshness(updated), job_dir)


def recover_publication(directory, current):
    def operation(latest):
        updated, published = _publish_pending(directory, latest)
        return updated, None, dict(kind='recover-publication', **published)
    loops.mutate(directory, current['generation'], operation)


def _publish_pending(directory, current):
    root = os.path.dirname(directory)
    pending = current.get('pending_capture')
    round_id = pending['round'] if pending else current['current_round']
    build = os.path.join(root, state.STAGING, round_id)
    published = os.path.join(root, round_id)
    candidate = published if os.path.isdir(published) else build
    expected = pending['target_sha256'] if pending else current['request'].get('staged_target_sha256')
    response._require(expected and os.path.isdir(candidate) and not os.path.islink(candidate),
                      'capture-unrecoverable', 'the retained round build is unavailable')
    response._require(images.sha256_file(os.path.join(candidate, 'target.json')) == expected and
                      loops._json(os.path.join(candidate, 'target.json')).get('loop_id') == current['loop_id'],
                      'capture-unrecoverable', 'the retained target does not match the loop publication record')
    context.verify_retained(candidate, loops._json(os.path.join(candidate, 'target.json')))
    if candidate == build:
        state.publish(root, round_id, build)
    return (loops.adopt_pending(current) if pending else current), dict(round=round_id, target_sha256=expected)
