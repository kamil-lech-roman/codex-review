"""Cited reassessment and cross-round finding relations without rewriting old jobs."""
import os

from codex_review import adjudication, context, execution, findings, images, loops, probe_integrity, probes, records, response, state

require = response._require


def original_report(current, origin_dir, item):
    return (os.path.join(origin_dir, 'reviewer-attempts', item['late_attempt'], 'last-message.txt')
            if item.get('late_attempt') else records.selected(current, origin_dir)['report'])


def original_finding(source, item):
    return item.get('finding') or next(f for f in loops._json(source)['findings']
                                       if f['id'] == item['reference']['local_id'])


def refuting_read(reported, annotation):
    return (annotation.get('read') == 'unsupported' or
            reported['classification'] == 'introduced' and annotation.get('read') == 'confirmed-pre-existing')


def citations(job_dir, decision):
    supplied = decision.get('evidence_ids')
    require(isinstance(supplied, list) and supplied, 'missing-resolution-evidence',
            'finding resolution requires cited sealed evidence')
    for citation in supplied:
        require(isinstance(citation, str) and state.is_job_id(citation), 'bad-evidence-id', 'cite sealed evidence nonces')
        metadata = loops._json(os.path.join(job_dir, 'evidence', citation, 'metadata.json'))
        probes._rehash(job_dir, os.path.basename(job_dir), citation, metadata['source_path'])


def assessment_applicable(job_dir, original, target, reported, decision):
    from codex_review.loop_driver import equivalence
    before, after = equivalence(original), equivalence(target)
    if before == after:
        return True
    artifacts = [a for a in original['artifacts_detail'] if a['id'] == reported['artifact']]
    require(len(artifacts) == 1, 'refutation-not-equivalent',
            'a finding without a captured artifact requires equivalent original inputs')
    claimed = artifacts[0]['path']
    old = {(p['path'], p.get('projection')): p for p in before['projections']}
    new = {(p['path'], p.get('projection')): p for p in after['projections']}
    require(old.keys() == new.keys(), 'refutation-not-equivalent', 'assessment cannot change the captured projection scope')
    changed = {path for path, projection in old if old[path, projection] != new[path, projection]}
    old_context, new_context = before['context'] or {}, after['context'] or {}
    changed.update(path for path in old_context.keys() | new_context.keys()
                   if old_context.get(path) != new_context.get(path))
    require(claimed not in changed, 'refutation-not-equivalent',
            'the finding artifact changed; corrected bytes need fixed closure')
    for value in (before, after):
        value['projections'] = [p for p in value['projections'] if p['path'] not in changed]
        value['context'] = {p: image for p, image in (value['context'] or {}).items() if p not in changed}
    require(before == after, 'refutation-not-equivalent',
            'finding applicability cannot waive changed policy, runtime, companions or verification')
    assessment = decision.get('applicability')
    require(isinstance(assessment, dict) and assessment.get('changed_paths') == sorted(changed) and
            isinstance(assessment.get('rationale'), str) and assessment['rationale'].strip(),
            'finding-applicability-required',
            'cite why every changed path is irrelevant to this unchanged original claim', changed_paths=sorted(changed))
    citations(job_dir, assessment)
    return True


def reassess(directory, current, decision, item):
    completed = current['phase'] == 'recapture' and current['pause'] == {'kind': 'unresolved-findings'}
    belongs = (item['reference']['round'] == current['current_round'] or
               item['reference'] in current.get('carried_findings', {}).get(current['current_round'], {}).values())
    require((current['phase'] == 'probed' and not current['pause'] or completed) and
            belongs, 'wrong-stage',
            'reassess current probes before apply or carry unresolved work into a fresh round')
    job_dir = os.path.join(os.path.dirname(directory), current['current_round'])
    selected = records.selected(current, job_dir)
    require(decision.get('probes_sha256') == records.digest(selected['probes']),
            'stale-prior-probes', 'reassessment must bind the current probes')
    if not completed:
        require(context.applicable(loops._json(os.path.join(job_dir, 'target.json'))),
                'context-drift', 'reassessment cannot adopt changed candidate bytes')
    citations(job_dir, decision)
    updated = dict(current, pause=None)
    if not completed:
        updated.update(phase='reviewed', round_records=records.next_probes(current, job_dir, state.new_nonce()))
    loops.append(directory, current['generation'], updated, decision)


def exclude(directory, current, decision, item):
    require(item['status'] == 'open' and item['disposition'] == 'scope-question', 'not-scope-question',
            'scope resolution cannot waive an introduced accepted or deferred finding')
    require(not current['pause'] or current['pause']['kind'] == 'unresolved-findings',
            'wrong-stage', 'resolve other pauses before deciding finding scope')
    require(decision.get('asserted_by') == 'user' and isinstance(decision.get('authorization'), str) and
            decision['authorization'].strip(), 'user-decision-required', 'an open scope question requires explicit user direction')
    root = os.path.dirname(directory)
    origin_dir = os.path.join(root, item['reference']['round'])
    selected = records.selected(current, origin_dir)
    require(decision.get('probes_sha256') == records.digest(selected['probes']),
            'stale-prior-probes', 'scope resolution must bind the original probe record')
    report = loops._json(selected['report'])
    original = next(f for f in report['findings'] if f['id'] == item['reference']['local_id'])
    require(original['classification'] == 'pre-existing', 'not-scope-question', 'only pre-existing scope questions can be excluded')
    citations(os.path.join(root, current['current_round']), decision)
    resolved = dict(item, status='out-of-scope', resolution=decision)
    ledger = [resolved if f['reference'] == item['reference'] else f for f in current['findings']]
    pause = current['pause']
    if pause and not findings.blocking(ledger):
        pause = None
    loops.append(directory, current['generation'], dict(current, findings=ledger, pause=pause), decision)


def relate(directory, current, decision, item):
    require(not current['pause'] and current['phase'] in ('probed', 'applied', 'round-complete'),
            'wrong-stage', 'finding relations require current validated probes')
    reference = decision.get('related_reference')
    matches = [f for f in current.get('findings', []) if f['reference'] == reference]
    require(len(matches) == 1 and reference['round'] == current['current_round'] and
            item['reference']['round'] != current['current_round'], 'bad-finding-relation',
            'link an earlier finding to a current probed finding')
    related = matches[0]
    root = os.path.dirname(directory)
    origin_dir = os.path.join(root, item['reference']['round'])
    job_dir = os.path.join(root, current['current_round'])
    if item.get('late_attempt'):
        origin_path = original_report(current, origin_dir, item)
        require(records.digest(origin_path) == item['late_report_sha256'], 'stale-relation-evidence',
                'the independently assessed late report bytes must remain retained')
        origin_binding = ('origin_report_sha256', origin_path)
    elif item.get('unassessed_report_sha256'):
        origin_path = original_report(current, origin_dir, item)
        require(records.digest(origin_path) == item['unassessed_report_sha256'], 'stale-relation-evidence',
                'the original unassessed report must remain retained')
        origin_binding = ('origin_report_sha256', origin_path)
    else:
        origin_binding = ('origin_probes_sha256', records.selected(current, origin_dir)['probes'])
    for field, path in [origin_binding, ('probes_sha256', records.selected(current, job_dir)['probes'])]:
        require(decision.get(field) == records.digest(path) and records.digest(path) is not None,
                'stale-relation-evidence', 'finding relation must bind both probe records', field=field)
    target = loops._json(os.path.join(job_dir, 'target.json'))
    original = loops._json(os.path.join(origin_dir, 'target.json'))
    citations(job_dir, decision)
    adjudication.require_retained_reopening(current, item['reference'], job_dir, decision.get('reopening'))
    require(context.applicable(target), 'context-drift', 'finding relation needs the current captured candidate')
    applicable = False
    reported = original_finding(original_report(current, origin_dir, item), item)
    if decision['disposition'] == 'refuted':
        annotation = related['assessment']
        require(annotation.get('probe_execution') == 'executed' and refuting_read(reported, annotation),
                'refutation-not-probed', 'refutation requires an executed counterexample, not a denial or inconclusive read')
        probe_integrity.revalidate(job_dir, annotation)
        execution.from_command(job_dir, target, annotation.get('command_evidence_id'))
        applicable = assessment_applicable(job_dir, original, target, reported, decision)
    resolved = findings.related(item, related, decision, applicable=applicable,
                                introduced=reported['classification'] == 'introduced')
    if decision['disposition'] == 'refuted':
        resolved['probe_binding'] = related['probe_binding']
    ledger = [resolved if f['reference'] == item['reference'] else f for f in current['findings']]
    loops.append(directory, current['generation'], dict(current, findings=ledger), decision)


def retained_assessment(directory, current, decision, item):
    require(not current['pause'] and current['phase'] in ('reviewed', 'probed', 'applied', 'round-complete'),
            'wrong-stage', 'retained assessment follows current read assessment')
    reference = item['reference']
    require(item['status'] == 'open' and reference['round'] != current['current_round'],
            'finding-not-open', 'independently assess an open earlier finding')
    root = os.path.dirname(directory)
    origin_dir, job_dir = os.path.join(root, reference['round']), os.path.join(root, current['current_round'])
    source = original_report(current, origin_dir, item)
    digest = records.digest(source)
    require(digest is not None and decision.get('origin_report_sha256') == digest and
            item.get('late_report_sha256', item.get('unassessed_report_sha256', digest)) == digest,
            'stale-relation-evidence', 'assessment must bind the retained original report')
    reported = original_finding(source, item)
    old_probes = records.digest(records.selected(current, origin_dir)['probes'])
    require(old_probes is None or decision.get('origin_probes_sha256') == old_probes,
            'stale-relation-evidence', 'also bind the earlier probe assessment when present')
    target = loops._json(os.path.join(job_dir, 'target.json'))
    original = loops._json(os.path.join(origin_dir, 'target.json'))
    require(context.applicable(target), 'context-drift', 'assessment needs the current captured candidate')
    assessment_applicable(job_dir, original, target, reported, decision)
    annotation = decision.get('assessment')
    alias = 'retained-' + reference['round'] + '-' + reference['local_id']
    probes.validate(job_dir, os.path.basename(job_dir), target, dict(findings=[dict(reported, id=alias)]),
                    dict(report_sha256=digest, annotations=[annotation]), digest)
    require(annotation.get('probe_execution') == 'executed', 'assessment-not-executed',
            'independent assessment needs an executed qualified probe')
    annotation['execution'] = execution.from_command(job_dir, target, annotation['command_evidence_id'])
    citations(job_dir, decision)
    return job_dir, reported, annotation


def refute_retained(directory, current, decision, item):
    job_dir, reported, annotation = retained_assessment(directory, current, decision, item)
    adjudication.require_retained_reopening(current, item['reference'], job_dir, decision.get('reopening'))
    require(
            refuting_read(reported, annotation) and
            (reported['classification'] == 'pre-existing' or annotation.get('disposition') == 'rejected'),
            'refutation-not-probed', 'refutation needs an executed conclusive counterexample')
    resolved = dict(item, status='refuted', resolution=decision, independent_assessment=annotation,
                    assessment_round=current['current_round'],
                    probe_binding=dict(round=current['current_round'], annotation=annotation))
    ledger = [resolved if f['reference'] == item['reference'] else f for f in current['findings']]
    loops.append(directory, current['generation'], dict(current, findings=ledger), decision)


def accept_retained(directory, current, decision, item):
    require(current['phase'] == 'probed', 'wrong-stage', 'assess current findings before adding retained accepted work')
    job_dir, reported, annotation = retained_assessment(directory, current, decision, item)
    require(reported['classification'] == 'introduced' and annotation.get('disposition') == 'accepted',
            'finding-not-accepted', 'independent acceptance needs a confirmed introduced finding')
    selected = records.selected(current, job_dir)
    require(not os.path.exists(os.path.join(job_dir, 'apply.json')), 'already-applied', 'accept retained work before apply')
    previous = loops._json(selected['probes'])
    report = loops._json(selected['report'])
    alias = annotation['finding_id']
    require(alias not in {f['id'] for f in report['findings']}, 'duplicate-finding', 'retained finding is already in current work')
    revision = state.new_nonce()
    relative_report = 'retained-findings/' + revision + '/report.json'
    report_path = os.path.join(job_dir, relative_report)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    carried = dict(report.get('carried_findings', {}), **{alias: dict(reference=item['reference'],
                   origin_report_sha256=decision['origin_report_sha256'])})
    state.atomic_json(report_path, dict(report, findings=report['findings'] + [dict(reported, id=alias)],
                                       verdict='changes-required', carried_findings=carried))
    updated_records = records.next_probes(current, job_dir, revision)
    round_id = current['current_round']
    submission = 'staging/retained-' + revision + '-probes.json'
    updated_records[round_id].update(report=relative_report, pending_submission=submission)
    payload = dict(report_sha256=images.sha256_file(report_path), previous_probes_sha256=images.sha256_file(selected['probes']),
                   annotations=[{key: value for key, value in item.items() if key != 'execution'}
                                for item in previous['annotations'] + [annotation]])
    if current.get('rebuttals', {}).get(round_id):
        payload['adjudication_sha256'] = current['rebuttals'][round_id]['sha256']
    state.atomic_json(os.path.join(job_dir, submission), payload)
    references = dict(current.get('carried_findings', {}))
    references[round_id] = dict(references.get(round_id, {}), **{alias: item['reference']})
    loops.append(directory, current['generation'], dict(current, phase='reviewed', round_records=updated_records,
                                                       carried_findings=references), decision)
