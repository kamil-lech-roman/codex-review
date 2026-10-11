"""Observe and reconcile retired-call output without mutating its terminal job."""
import os

from codex_review import counting, images, loops, qualification, quiescence, response

FILES = ('stdout.jsonl', 'stderr.txt', 'last-message.txt', 'completion.json')
require = response._require


def snapshot(attempt_dir):
    return {name: images.sha256_file(os.path.join(attempt_dir, name))
            for name in FILES if os.path.isfile(os.path.join(attempt_dir, name))}


def pending(directory, current):
    result = []
    for attempt in current['attempts']:
        if attempt['outcome'] != 'retired-unknown' and not attempt.get('was_retired'):
            continue
        path = os.path.join(os.path.dirname(directory), attempt['round'], 'reviewer-attempts', attempt['id'])
        observed = snapshot(path)
        if observed != attempt.get('retired_evidence', {}):
            result.append(dict(attempt=attempt['id'], round=attempt['round'], files=observed,
                               sha256=qualification.digest(observed)))
    return result


def reconcile(directory, current, decision):
    notices = [n for n in pending(directory, current) if n['attempt'] == decision.get('attempt')]
    require(len(notices) == 1, 'no-late-evidence', 'no changed retired output needs reconciliation')
    notice = notices[0]
    require(decision.get('attempt_evidence_sha256') == notice['sha256'], 'stale-late-evidence',
            'resolution must bind the observed output files')
    attempt = next(a for a in current['attempts'] if a['id'] == notice['attempt'])
    job_dir = os.path.join(os.path.dirname(directory), attempt['round'])
    attempt_dir = os.path.join(job_dir, 'reviewer-attempts', attempt['id'])
    launch_path = os.path.join(attempt_dir, 'launch.json')
    launch = loops._json(launch_path) if os.path.isfile(launch_path) else None
    stopped = quiescence.require(launch, decision, os.path.join(os.path.dirname(directory), current['current_round']))
    path = os.path.join(attempt_dir, 'last-message.txt')
    raw = b''
    if os.path.isfile(path):
        with open(path, 'rb') as handle:
            raw = handle.read()
    target = loops._json(os.path.join(job_dir, 'target.json'))
    try:
        if attempt.get('kind') == 'rebuttal':
            from codex_review import adjudication
            parsed = adjudication.validate(raw, target, loops._json(os.path.join(job_dir, 'report.json')),
                                           [c['id'] for c in attempt['claims']])
            reported = parsed['new_findings']
        else:
            parsed = response.validate(raw, target)
            reported = parsed['findings']
        result = dict(report=parsed, raw_sha256=images.sha256_file(path), quiescence=stopped)
    except response.ContractViolation as error:
        require(decision.get('disposition') == 'unusable', 'late-result-unusable',
                'no valid report; explicitly acknowledge unusable late evidence', validation_code=error.code)
        result = dict(validation_code=error.code, quiescence=stopped)
        reported = []
    require(snapshot(attempt_dir) == notice['files'], 'late-evidence-race', 'retired output changed during reconciliation')
    updated_attempt = dict(attempt, was_retired=True, retired_evidence=notice['files'], late_result=result,
                           outcome='judgment' if 'report' in result else attempt['outcome'])
    ledger = list(current.get('findings', []))
    known = {tuple(sorted(f['reference'].items())) for f in ledger}
    for finding in reported:
        reference = dict(round=attempt['round'], local_id=finding['id'])
        if tuple(sorted(reference.items())) not in known:
            ledger.append(dict(reference=reference, status='open', disposition='unassessed',
                               finding=finding, late_attempt=attempt['id'], late_report_sha256=result['raw_sha256']))
    updated = dict(current, attempts=[updated_attempt if a['id'] == attempt['id'] else a for a in current['attempts']],
                   findings=ledger)
    if attempt.get('kind') == 'rebuttal' and 'report' in result:
        updated = adjudication.retain_result(updated, attempt, parsed, path)
    if attempt.get('kind') == 'cold':
        updated.update(counting=counting.reset(current['counting']),
                       disqualified_rounds=sorted(set(current.get('disqualified_rounds', [])) | {attempt['round']}))
    return updated
