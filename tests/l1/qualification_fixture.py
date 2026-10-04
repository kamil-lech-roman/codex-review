"""Synthetic registry evidence for offline gates; never host qualification."""
import json

from codex_review import evidence, qualification


def qualify_fixture(root, job, key):
    prior = qualification.lookup(str(root), key)
    if prior['valid']:
        return prior['digest']
    controls = []
    for case in qualification.CASES[key['capability']]:
        item = dict(case=case, outcome='pass')
        for kind, data in [('command', 'echo fixture\n'), ('output', 'fixture observation\n'), ('rc', '0\n')]:
            slot = 'staging/' + case + '-' + kind
            (job / 'staging').mkdir(exist_ok=True)
            (job / slot).write_text(data)
            item[kind + '_evidence_id'] = evidence.seal(str(job), job.name, slot)['evidence_id']
        controls.append(item)
    return qualification.record(str(root), str(job), dict(schema_version=1, capability=key['capability'],
        key=key, controls=controls, rationale='Offline gate fixture; not live capability evidence.', supersedes=prior['digest']))


def qualify_command(job, command, target=None):
    from codex_review import execution, images
    target = target or json.loads((job / 'target.json').read_text())
    return qualify_fixture(job.parent, job, execution.key(target, images.sha256_bytes(command.encode('utf-8'))))


def qualify_author(job, commands=()):
    from codex_review import execution
    target = json.loads((job / 'target.json').read_text())
    result = qualify_fixture(job.parent, job, execution.key(target))
    for command in list(commands) + ([target['verify_command'] + '\n'] if target.get('verify_command') else []):
        qualify_command(job, command, target)
    return result
