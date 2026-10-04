"""Author finding ledger; reviewer-local IDs never identify cross-round work."""
from codex_review.response import _require


def reference_for(round_id, local_id, references=None):
    return (references or {}).get(local_id, dict(round=round_id, local_id=local_id))


def has_conclusive_probe(annotation):
    return (annotation.get('probe_execution') == 'executed' and
            annotation.get('read') in ('unsupported', 'confirmed-pre-existing'))


def add(ledger, round_id, annotations, references=None):
    result = list(ledger)
    known = {(item['reference']['round'], item['reference']['local_id']): i for i, item in enumerate(result)}
    for annotation in annotations:
        disposition = annotation.get('disposition', 'scope-question' if annotation.get('scope_question') else None)
        conclusive = has_conclusive_probe(annotation)
        status = 'open'
        if disposition == 'rejected' and conclusive:
            status = 'refuted'
        elif disposition is None:
            disposition = 'pre-existing' if conclusive else 'inconclusive'
            if conclusive:
                status = 'refuted' if annotation.get('read') == 'unsupported' else 'reviewed'
        reference = reference_for(round_id, annotation['finding_id'], references)
        key = (reference['round'], reference['local_id'])
        if key in known:
            previous = result[known[key]]
            if (previous['status'] == 'open' and previous['disposition'] == 'scope-question' and
                    not (conclusive and annotation.get('read') == 'unsupported')):
                disposition, status = 'scope-question', 'open'
        item = dict(reference=dict(round=key[0], local_id=key[1]), status=status,
                    disposition=disposition, fix_paths=annotation.get('fix_paths', []), assessment=annotation)
        if key in known:
            if previous['status'] == 'out-of-scope':
                item.update(status='out-of-scope', disposition=previous['disposition'], resolution=previous['resolution'])
            for field in ('finding', 'late_attempt', 'late_report_sha256', 'unassessed_report_sha256'):
                if field in previous:
                    item[field] = previous[field]
            result[known[key]] = item
        else:
            known[key] = len(result)
            result.append(item)
    return result


def outstanding(ledger):
    return [item for item in ledger if item['status'] == 'open']


def maintained_disputes(ledger, round_id, adjudication):
    maintained = {d['id'] for d in adjudication['decisions'] if d['outcome'] == 'maintained'}
    return [dict(item, status='open', disposition='disputed')
            if item['reference']['round'] == round_id and item['reference']['local_id'] in maintained and
            item['status'] == 'refuted' else item for item in ledger]


def retain_unassessed(ledger, round_id, report, report_sha256, references=None):
    result = list(ledger)
    known = {(f['reference']['round'], f['reference']['local_id']) for f in ledger}
    for finding in report['findings']:
        reference = reference_for(round_id, finding['id'], references)
        if (reference['round'], reference['local_id']) not in known:
            result.append(dict(reference=reference, status='open',
                               disposition='unassessed', finding=finding,
                               unassessed_report_sha256=report_sha256))
    return result


def blocking(ledger):
    return [item for item in outstanding(ledger) if item['disposition'] != 'accepted']


def related(item, successor, decision, *, applicable, introduced=True):
    _require(item['status'] == 'open', 'finding-not-open', 'resolve an open finding')
    disposition = decision['disposition']
    if disposition == 'refuted':
        _require(has_conclusive_probe(successor['assessment']) and applicable, 'refutation-not-equivalent',
                 'refutation must apply to the unchanged original claim; corrected bytes need fixed closure')
        status = 'refuted'
    else:
        _require(disposition == 'recurring' and successor['status'] == 'open', 'bad-finding-relation',
                 'a recurring finding must remain open under its current reference')
        _require(item['disposition'] != 'accepted' or successor['disposition'] == 'accepted',
                 'accepted-finding-downgrade', 'recurrence cannot downgrade an accepted defect')
        status = 'open' if introduced or item['disposition'] == 'accepted' else 'superseded'
    return dict(item, status=status, resolution=decision)


def fixed(item, verification, reviewed_paths, *, eligible, decision):
    _require(item['status'] == 'open' and item['disposition'] == 'accepted',
             'finding-not-accepted', 'fixed closure needs an open accepted finding')
    _require(verification['verification_result'] == 'passed', 'fix-not-verified',
             'fixed closure requires successful sealed verification')
    relevant = set(item['fix_paths']) & set(verification['changed_paths']) & set(reviewed_paths)
    _require(bool(relevant), 'fix-not-reexamined', 'closure needs a relevant changed path in the new capture')
    _require(eligible, 'recheck-ineligible', 'closure needs a qualifying cold re-examination')
    _require(bool(decision.get('rationale', '').strip()), 'missing-rationale', 'closure needs its evidence assessment')
    return dict(item, status='fixed', resolution=decision, reexamined_paths=sorted(relevant))
