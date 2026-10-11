"""Bindings for host-run author commands; this module never executes a command."""
import json
import os

from codex_review import apply, context, evidence, images, probes, qualification, response, state


def inherited_environment():
    names = ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'VIRTUAL_ENV')
    return {name: os.environ[name] for name in names if name in os.environ}


def command_argv(job_dir, command_id, environment, inherited):
    settings = dict(inherited, **environment)
    return ['/usr/bin/env', '-i'] + [name + '=' + value for name, value in sorted(settings.items())] + [
        '/bin/bash', '--noprofile', '--norc', os.path.join(job_dir, 'evidence', command_id, 'content')]


class Unqualified(Exception):
    def __init__(self, selected):
        super().__init__('author execution requires matching cache controls')
        self.selected = selected


def key(target, command_sha256=None):
    settings = dict(
        cache_policy=target['cache_policy'], cwd=target['exec_dir'],
        shell=dict(path='/bin/bash', image=images.image_of('/bin/bash')),
        inherited_environment=inherited_environment(),
        runtime=[dict(id=d['id'], path=d['path'], manifest=d['manifest'], exclude=d['exclude'])
                 for d in target.get('dependencies', [])],
        environment=dict(PYTHONPYCACHEPREFIX='<fresh-execution>/python', NUMBA_CACHE_DIR='<fresh-execution>/numba'))
    if command_sha256 is not None:
        settings['command_sha256'] = command_sha256
    return qualification.key('cache-execution', settings)


def rebind_key(target, bound):
    return key(target, bound['settings'].get('command_sha256'))


def reliances(manifest):
    return [manifest[name] for name in ('qualification', 'command_qualification') if name in manifest]


def _json(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def revalidate_retained(job_dir, command_id, manifest):
    response._require(isinstance(manifest, dict) and isinstance(manifest.get('execution_id'), str) and
                      state.is_job_id(manifest['execution_id']),
                      'missing-execution-binding', 'retained execution needs its original manifest')
    execution_id = manifest['execution_id']
    pointer = _json(os.path.join(job_dir, 'evidence', command_id, 'execution.json'))
    stored = _json(os.path.join(job_dir, 'executions', execution_id, 'manifest.json'))
    response._require(pointer == dict(execution_id=execution_id) and stored == manifest and
                      manifest.get('command_evidence_id') == command_id and
                      manifest.get('command_sha256') == images.sha256_bytes(apply.sealed_bytes(job_dir, command_id)),
                      'execution-binding-mismatch', 'retained execution no longer matches its recorded command and settings')


def prepare(job_dir, target, command_id):
    target = command_target(job_dir, target, command_id)
    for selected in context.exclusion_qualifications(target, os.path.dirname(job_dir)):
        if not selected['valid']:
            return dict(ready=False, reason='qualification-required', qualification=selected)
    metadata = _json(os.path.join(job_dir, 'evidence', command_id, 'metadata.json'))
    response._require(evidence.COMMAND_SCRIPT.fullmatch(metadata['source_path']),
                      'not-author-command', 'execution needs a sealed probe or verification script')
    probes._rehash(job_dir, os.path.basename(job_dir), command_id, metadata['source_path'])
    if metadata['source_path'] == 'staging/verify.sh':
        response._require(target.get('verify_command') is not None, 'verification-selection-required',
                          'verification execution needs a configured or selected command')
        apply.check_verification_command(job_dir, os.path.basename(job_dir), target,
                                         dict(command_evidence_id=command_id))
        applied_path = os.path.join(job_dir, 'apply.json')
        response._require(os.path.isfile(applied_path), 'apply-record-required',
                          'verification executes only after apply-scope validation')
        response._require(_json(applied_path)['post_fix_sha'] == apply.identity_sha(target),
                          'context-drift', 'the applied candidate changed before verification')
    else:
        response._require(context.applicable(target), 'context-drift', 'captured inputs changed before probing')
    selected = qualification.lookup(os.path.dirname(job_dir), key(target))
    if not selected['valid']:
        return dict(ready=False, reason='qualification-required', qualification=selected)
    command_selected = qualification.lookup(os.path.dirname(job_dir), key(target, metadata['sha256']))
    if not command_selected['valid']:
        return dict(ready=False, reason='qualification-required', qualification=command_selected)
    response._require(context.unchanged(target), 'context-drift', 'declared external context changed before execution')
    execution_id = state.new_nonce()
    directory = os.path.join(job_dir, 'executions', execution_id)
    for dependency in target.get('dependencies', []):
        root = os.path.realpath(dependency['path'])
        response._require(not os.path.isdir(root) or os.path.commonpath((root, directory)) != root,
                          'cache-inside-bound-root', 'cache allocation must be outside declared roots')
    environment = dict(PYTHONPYCACHEPREFIX=os.path.join(directory, 'python'),
                       NUMBA_CACHE_DIR=os.path.join(directory, 'numba'))
    for path in environment.values():
        os.makedirs(path, exist_ok=False)
    manifest = dict(execution_id=execution_id, ready=True, command_evidence_id=command_id,
                    command_sha256=metadata['sha256'], cwd=target['exec_dir'],
                    candidate_sha256=apply.identity_sha(target), cache_policy=target['cache_policy'],
                    environment=environment, qualification=selected, command_qualification=command_selected,
                    inherited_environment=selected['key']['settings']['inherited_environment'])
    manifest['argv'] = command_argv(job_dir, command_id, environment, manifest['inherited_environment'])
    state.atomic_json(os.path.join(directory, 'manifest.json'), manifest)
    return manifest


def validate(job_dir, target, command_id, execution_id):
    target = command_target(job_dir, target, command_id)
    for selected in context.exclusion_qualifications(target, os.path.dirname(job_dir)):
        if not selected['valid']:
            raise Unqualified(selected)
    selected = qualification.lookup(os.path.dirname(job_dir), key(target))
    if not selected['valid']:
        raise Unqualified(selected)
    response._require(isinstance(execution_id, str) and state.is_job_id(execution_id),
                      'missing-execution-binding', 'executed evidence must cite its prepared launch settings',
                      field='execution_id', expected='execution nonce from seal')
    manifest = _json(os.path.join(job_dir, 'executions', execution_id, 'manifest.json'))
    response._require(manifest.get('execution_id') == execution_id and
                      manifest.get('command_evidence_id') == command_id and
                      manifest.get('cache_policy') == target['cache_policy'] and
                      manifest.get('cwd') == target['exec_dir'] and
                      manifest.get('candidate_sha256') == apply.identity_sha(target),
                      'execution-binding-mismatch', 'execution does not bind this command, candidate and policy')
    response._require(manifest.get('qualification', {}).get('key') == selected['key'] and
                      manifest['qualification']['digest'] == selected['digest'],
                      'execution-qualification-changed', 'execution qualification was superseded')
    expected = dict(PYTHONPYCACHEPREFIX=os.path.join(job_dir, 'executions', execution_id, 'python'),
                    NUMBA_CACHE_DIR=os.path.join(job_dir, 'executions', execution_id, 'numba'))
    response._require(manifest.get('environment') == expected and
                      all(os.path.isdir(path) and not os.path.islink(path) for path in expected.values()),
                      'execution-environment-mismatch', 'only driver-allocated cache locators may be normalized')
    inherited = selected['key']['settings']['inherited_environment']
    response._require(manifest.get('inherited_environment') == inherited and
                      manifest.get('argv') == command_argv(job_dir, command_id, expected, inherited),
                      'execution-environment-mismatch', 'execution must use the qualified explicit environment')
    metadata = _json(os.path.join(job_dir, 'evidence', command_id, 'metadata.json'))
    probes._rehash(job_dir, os.path.basename(job_dir), command_id, metadata['source_path'])
    command_selected = qualification.lookup(os.path.dirname(job_dir), key(target, metadata['sha256']))
    if not command_selected['valid']:
        raise Unqualified(command_selected)
    response._require(manifest.get('command_qualification') == command_selected,
                      'execution-qualification-changed', 'sealed-command qualification changed; reseal after qualification')
    response._require(metadata['sha256'] == manifest['command_sha256'],
                      'execution-command-mismatch', 'the execution command changed')
    return manifest


def command_target(job_dir, target, command_id):
    metadata = _json(os.path.join(job_dir, 'evidence', command_id, 'metadata.json'))
    return context.after_apply(job_dir, target) if metadata['source_path'] == 'staging/verify.sh' else target


def from_command(job_dir, target, command_id):
    target = command_target(job_dir, target, command_id)
    selected = qualification.lookup(os.path.dirname(job_dir), key(target))
    if not selected['valid']:
        raise Unqualified(selected)
    path = os.path.join(job_dir, 'evidence', command_id, 'execution.json')
    response._require(os.path.isfile(path), 'missing-execution-binding',
                      'reseal the command after qualification to allocate fresh execution settings',
                      field='command_evidence_id', expected='command sealed with execution.ready true')
    return validate(job_dir, target, command_id, _json(path)['execution_id'])
