"""Adopt immutable author records after a crash between job and loop publication."""
import os

from codex_review import amendments, counting, findings, images, jobs, loops, probe_integrity, records, response


PHASES = {'reviewed': ('probes', 'probed'), 'probed': ('apply', 'applied'),
          'applied': ('verification', 'round-complete')}


def pending(current, job_dir):
    if current['phase'] not in PHASES:
        return None
    for record, reason in (('apply-error.json', 'scope-violation'), ('invalidated.json', 'candidate-drift')):
        path = os.path.join(job_dir, record)
        if os.path.isfile(path):
            return dict(name=record, path=path, phase='recapture', failure=reason)
    name, phase = PHASES[current['phase']]
    selected = records.selected(current, job_dir)
    path = selected['probes'] if name == 'probes' else os.path.join(job_dir, name + '.json')
    return dict(name=name, path=path, phase=phase) if os.path.isfile(path) else None


def adopted(current, job_dir, publication):
    if publication.get('failure'):
        return dict(current, phase='recapture', pause=dict(kind=publication['failure']),
                    counting=counting.reset(current['counting']))
    selected = records.selected(current, job_dir)
    record = loops._json(publication['path'])
    name = publication['name']
    bindings = [('report_sha256', selected['report'])] if name == 'probes' else (
        [('report_sha256', selected['report']), ('probes_sha256', selected['probes'])] if name == 'apply' else
        [('apply_sha256', os.path.join(job_dir, 'apply.json'))])
    for field, path in bindings:
        response._require(record.get(field) == images.sha256_file(path), 'record-binding-mismatch',
                          'published record does not bind its immutable predecessor', field=field)
    ledger, credit = current.get('findings', []), current['counting']
    if name == 'probes':
        record = amendments.effective(record, current.get('amendments', {}).get(current['current_round'], []))
        ledger = findings.add(ledger, current['current_round'], record['annotations'],
                              current.get('carried_findings', {}).get(current['current_round']))
        bindings = [(findings.reference_for(current['current_round'], a['finding_id'],
                                            current.get('carried_findings', {}).get(current['current_round'])),
                     probe_integrity.binding(current, job_dir, a['finding_id'])) for a in record['annotations']]
        ledger = [dict(item, probe_binding=next(bound for reference, bound in bindings if reference == item['reference']))
                  if any(reference == item['reference'] for reference, _ in bindings) else item for item in ledger]
        if any(a.get('disposition') == 'accepted' for a in record['annotations']):
            credit = counting.reset(credit)
    return dict(current, phase=publication['phase'], findings=ledger, counting=credit)


def recover(directory, current):
    from codex_review import main
    job_dir = os.path.join(os.path.dirname(directory), current['current_round'])
    def operation(latest):
        with jobs.mutex(job_dir):
            publication = pending(latest, job_dir)
            response._require(publication is not None, 'no-pending-record', 'no author publication needs recovery')
            updated = adopted(latest, job_dir, publication)
            if publication['name'] == 'verification':
                main.release_apply(job_dir, main.load_target(job_dir), records.selected(latest, job_dir)['probes'])
            return updated, None, dict(kind='recover-record', round=latest['current_round'],
                                       record=publication['name'], sha256=images.sha256_file(publication['path']))
    loops.mutate(directory, current['generation'], operation)
