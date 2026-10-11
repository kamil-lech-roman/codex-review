"""Immutable, evidence-backed capability records and compare-and-swap supersession."""
import json
import os
import platform
import socket

from codex_review import images, jobs, probes, response, state

CASES = {
    'cold-launch': ('authentication', 'cwd', 'repository-write-denial', 'scoped-persistence',
                    'host-instruction-injection', 'ignore-user-config', 'ignore-rules'),
    'resume': ('authentication', 'same-thread', 'cwd', 'repository-write-denial',
               'scoped-persistence', 'host-instruction-injection', 'ignore-user-config', 'ignore-rules'),
    'cache-execution': ('python-stale-cache', 'python-redirect', 'numba-redirect',
                        'descendant-redirect', 'override-refusal'),
    'cache-exclusion': ('reviewer-excluded-read', 'author-excluded-read', 'bound-regeneration'),
}
require = response._require


def digest(value):
    return images.sha256_bytes((json.dumps(value, sort_keys=True, indent=2) + '\n').encode('utf-8'))


def key(capability, settings):
    from codex_review.main import driver_hash
    require(capability in CASES, 'bad-capability', 'unknown qualification capability',
            field='capability', expected=sorted(CASES))
    return dict(capability=capability, host=socket.gethostname(), os=platform.system(),
                os_release=platform.release(), architecture=platform.machine(),
                driver_hash=driver_hash(), suite_sha256=digest(CASES[capability]),
                settings=settings, settings_sha256=digest(settings))


def _read(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def _validate(record, job_dir):
    require(isinstance(record, dict) and record.get('schema_version') == 1,
            'bad-qualification', 'qualification needs schema_version 1',
            field='schema_version', expected=1)
    capability = record.get('capability')
    require(isinstance(capability, str) and capability in CASES, 'bad-capability',
            'unknown qualification capability', field='capability', expected=sorted(CASES))
    bound = record.get('key')
    require(isinstance(bound, dict) and isinstance(bound.get('settings'), dict),
            'bad-qualification-key', 'qualification key needs effective settings', field='key', expected='object')
    require(bound == key(capability, bound['settings']), 'qualification-key-mismatch',
            'qualification key does not match this host, driver and suite', field='key',
            expected=key(capability, bound['settings']))
    require(isinstance(record.get('rationale'), str) and record['rationale'].strip(),
            'missing-rationale', 'qualification needs a cited assessment rationale', field='rationale', expected='text')
    controls = record.get('controls')
    require(isinstance(controls, list) and all(isinstance(c, dict) for c in controls),
            'bad-controls', 'controls must be objects', field='controls', expected='array')
    names = [c.get('case') for c in controls]
    require(all(isinstance(name, str) for name in names) and
            len(set(names)) == len(names) and set(names) == set(CASES[capability]),
            'incomplete-controls', 'required case set is incomplete or duplicated',
            field='controls[].case', expected=list(CASES[capability]))
    for control in controls:
        require(control.get('outcome') in ('pass', 'fail', 'unavailable'), 'bad-control-outcome',
                'control outcome is invalid', field='outcome', expected=['pass', 'fail', 'unavailable'])
        for kind in ('command', 'output', 'rc'):
            evidence_id = control.get(kind + '_evidence_id')
            require(isinstance(evidence_id, str) and state.is_job_id(evidence_id),
                    'bad-evidence-id', 'control citation must name sealed evidence', field=kind + '_evidence_id',
                    expected='evidence nonce')
            meta = _read(os.path.join(job_dir, 'evidence', evidence_id, 'metadata.json'))
            probes._rehash(job_dir, os.path.basename(job_dir), evidence_id, meta['source_path'])
        with open(os.path.join(job_dir, 'evidence', control['rc_evidence_id'], 'content'), 'rb') as handle:
            raw = handle.read()
        require(probes._canonical_rc(raw), 'bad-return-code', 'control return code is not canonical',
                field='rc_evidence_id', expected='sealed shell status')
        require(control['outcome'] != 'pass' or raw == b'0\n', 'control-contradicted',
                'passing control carries a nonzero return code')


def record(root, job_dir, submitted):
    _validate(submitted, job_dir)
    directory = os.path.join(root, 'qualifications')
    os.makedirs(directory, exist_ok=True)
    identity = digest(submitted['key'])
    with jobs.mutex(directory):
        index_path = os.path.join(directory, 'index.json')
        index = _read(index_path) if os.path.exists(index_path) else {}
        prior = index.get(identity)
        require('supersedes' in submitted and submitted['supersedes'] == prior,
                'stale-qualification', 'supersession must cite the current qualification',
                field='supersedes', expected=prior)
        stored = dict(submitted, evidence_job=os.path.basename(job_dir))
        sha = digest(stored)
        path = os.path.join(directory, sha + '.json')
        if not os.path.exists(path):
            state.atomic_json(path, stored)
        index[identity] = sha
        state.atomic_json(index_path, index)
    return sha


def lookup(root, expected_key):
    result = dict(key=expected_key, digest=None, valid=False, reason='missing')
    try:
        directory = os.path.join(root, 'qualifications')
        index = _read(os.path.join(directory, 'index.json'))
        sha = index.get(digest(expected_key))
        if sha is None:
            return result
        result['digest'] = sha
        require(isinstance(sha, str) and len(sha) == 64 and all(c in '0123456789abcdef' for c in sha),
                'qualification-corrupt', 'invalid qualification digest')
        stored = _read(os.path.join(directory, sha + '.json'))
        require(digest(stored) == sha and stored['key'] == expected_key,
                'qualification-corrupt', 'qualification bytes or key changed')
        require(state.is_job_id(stored['evidence_job']), 'qualification-corrupt', 'invalid evidence job')
        job_dir = jobs.resolve(root, stored['evidence_job'])
        require(job_dir is not None, 'qualification-corrupt', 'evidence job is unavailable')
        _validate(stored, job_dir)
        result['valid'] = all(c['outcome'] == 'pass' for c in stored['controls'])
        result['reason'] = None if result['valid'] else 'controls-not-passing'
    except (OSError, ValueError, TypeError, KeyError, response.ContractViolation) as error:
        result['reason'] = getattr(error, 'code', 'evidence-unavailable')
    return result


def continuous(root, previous, current):
    """A passing head cannot erase failed or broken supersessions since prior use."""
    if not current['valid'] or not previous['valid'] or previous['key'] != current['key']:
        return False
    cursor, seen = current['digest'], set()
    try:
        while True:
            require(isinstance(cursor, str) and len(cursor) == 64 and
                    all(c in '0123456789abcdef' for c in cursor) and cursor not in seen,
                    'broken-qualification-chain', 'qualification supersession chain is invalid')
            seen.add(cursor)
            record = _read(os.path.join(root, 'qualifications', cursor + '.json'))
            require(digest(record) == cursor and record['key'] == current['key'],
                    'broken-qualification-chain', 'qualification supersession bytes or key changed')
            job = jobs.resolve(root, record['evidence_job'])
            require(job is not None, 'broken-qualification-chain', 'qualification evidence is unavailable')
            _validate(record, job)
            if not all(c['outcome'] == 'pass' for c in record['controls']):
                return False
            if cursor == previous['digest']:
                return True
            cursor = record['supersedes']
    except (OSError, ValueError, TypeError, KeyError, response.ContractViolation):
        return False
