"""Immutable verification intent and a single append-only deferred selection."""
import json
import os

from codex_review import apply, execution, images, probes, qualification, response


def revalidate(job_dir, record):
    block = record['verification_evidence']
    result = record['verification_result']
    apply.check_verification_evidence(result, {k: v for k, v in block.items() if k != 'execution'})
    response._require(images.sha256_file(os.path.join(job_dir, 'apply.json')) == record['apply_sha256'],
                      'stale-apply-digest', 'retained verification must bind its original apply record')
    if result in ('passed', 'failed', 'denied'):
        apply.check_verification_command(job_dir, os.path.basename(job_dir),
                                        dict(verify_command=record['verification_contract']['command']), block)
    if result in ('passed', 'failed'):
        response._require(apply.derive_verification(job_dir, os.path.basename(job_dir), block) == result,
                          'verification-contradicted', 'retained return code contradicts the verification record')
        execution.revalidate_retained(job_dir, block['command_evidence_id'], block.get('execution'))


def binding(job_dir):
    return dict(round=os.path.basename(job_dir), sha256=images.sha256_file(os.path.join(job_dir, 'verification.json')))


def retained(root, bound):
    job_dir = os.path.join(root, bound['round'])
    path = os.path.join(job_dir, 'verification.json')
    try:
        response._require(images.sha256_file(path) == bound['sha256'], 'stale-verification-digest',
                          'retained verification record changed')
        with open(path, encoding='utf-8') as handle:
            record = json.load(handle)
        revalidate(job_dir, record)
        return all(qualification.continuous(root, proof, qualification.lookup(root, proof['key']))
                   for proof in execution.reliances(record['verification_evidence'].get('execution', {})))
    except (OSError, ValueError, KeyError, TypeError, response.ContractViolation):
        return False


def contract(target):
    return target.get('verification_contract') or dict(
        intent=target['verification_intent'], command=target.get('verify_command'),
        cwd=target['exec_dir'], cache_policy=target['cache_policy'])


def select(current, target, job_dir, decision):
    response._require(current['intent'] == 'deferred', 'verification-not-deferred',
                      'only deferred intent permits command selection')
    response._require(current['command'] is None, 'verification-already-selected',
                      'the loop verification command cannot be replaced')
    evidence_id = decision.get('command_evidence_id')
    probes._rehash(job_dir, os.path.basename(job_dir), evidence_id, 'staging/verify.sh')
    raw = apply.sealed_bytes(job_dir, evidence_id)
    response._require(raw.endswith(b'\n') and raw[:-1].strip(), 'bad-verification-command',
                      'sealed command must be nonempty UTF-8 followed by a newline')
    command = raw[:-1].decode('utf-8')
    response._require(current['cwd'] == target['exec_dir'], 'verification-cwd-mismatch',
                      'selection cannot relocate the execution directory')
    return dict(current, command=command, selection=dict(round=os.path.basename(job_dir),
                                                        command_evidence_id=evidence_id))


def effective_target(target, selected):
    response._require(target['verification_intent'] == selected['intent'] and
                      target['exec_dir'] == selected['cwd'] and
                      target['cache_policy'] == selected['cache_policy'],
                      'verification-contract-mismatch', 'verification contract does not match the round')
    return dict(target, verify_command=selected['command'], verification_contract=selected)
