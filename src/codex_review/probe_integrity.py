"""Retain probe judgments only while their original records and sealed execution survive."""
import os

from codex_review import execution, images, loops, probes, qualification, records, response


def revalidate(job_dir, annotation):
    local_id = annotation['finding_id']
    job_id = os.path.basename(job_dir)
    command = annotation['command_evidence_id']
    probes._rehash(job_dir, job_id, command, 'staging/probe-' + local_id + '.sh')
    if annotation['probe_execution'] == 'executed':
        for field, suffix in (('output', 'out'), ('rc', 'rc')):
            probes._rehash(job_dir, job_id, annotation[field + '_evidence_id'], 'probes/' + local_id + '.' + suffix)
        with open(os.path.join(job_dir, 'evidence', annotation['rc_evidence_id'], 'content'), 'rb') as handle:
            response._require(probes._canonical_rc(handle.read()), 'bad-return-code', 'retained probe needs a canonical shell status')
        execution.revalidate_retained(job_dir, command, annotation.get('execution'))


def binding(current, job_dir, local_id=None):
    selected = records.selected(current, job_dir)
    return dict(round=os.path.basename(job_dir), path=records.relative(selected['probes'], job_dir),
                sha256=images.sha256_file(selected['probes']),
                report_path=records.relative(selected['report'], job_dir),
                report_sha256=images.sha256_file(selected['report']), finding_id=local_id)


def retained(root, bound):
    if not bound:
        return False
    try:
        job_dir = os.path.join(root, bound['round'])
        if 'annotation' in bound:
            annotations = [bound['annotation']]
        else:
            selected = records.selected(dict(round_records={bound['round']: dict(
                probes=bound['path'], report=bound['report_path'])}), job_dir)
            response._require(images.sha256_file(selected['probes']) == bound['sha256'] and
                              images.sha256_file(selected['report']) == bound['report_sha256'],
                              'stale-probe-evidence', 'retained probe and report bytes must match their binding')
            record = loops._json(selected['probes'])
            response._require(record['report_sha256'] == bound['report_sha256'],
                              'stale-report-digest', 'retained probes must bind their original report')
            annotations = record['annotations']
            if bound.get('finding_id') is not None:
                annotations = [a for a in annotations if a['finding_id'] == bound['finding_id']]
                response._require(len(annotations) == 1, 'missing-annotation', 'retained finding needs its original assessment')
        for annotation in annotations:
            revalidate(job_dir, annotation)
            if not all(qualification.continuous(root, proof, qualification.lookup(root, proof['key']))
                       for proof in execution.reliances(annotation.get('execution', {}))):
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError, response.ContractViolation):
        return False
