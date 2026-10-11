"""Flat loop namespace, immutable generations and stale mutation refusal."""
import json
from pathlib import Path

import pytest

from codex_review import response


def should_publish_flat_loop_and_refuse_stale_generation_without_rewriting(tmp_path):
    from codex_review import loops
    # given a loop with one immutable member round
    root = tmp_path / 'state'
    round_id = 'b' * 16
    (root / round_id).mkdir(parents=True)
    loop_id = 'a' * 16
    (root / round_id / 'target.json').write_text(json.dumps({'loop_id': loop_id}))
    directory = loops.create(str(root), loop_id, round_id, {'mode': 'plan'})
    original = (Path(directory) / 'loop.json').read_bytes()
    # when a checked transition advances the durable generation
    state = loops.read(directory)
    loops.append(directory, 0, dict(state, phase='assessment'), {'kind': 'judgment'})
    # then a stale writer cannot overwrite it or the initial record
    with pytest.raises(response.ContractViolation, match='generation'):
        loops.append(directory, 0, dict(state, phase='closed'), {'kind': 'stale'})
    assert loops.read(directory)['generation'] == 1
    assert loops.read(directory)['phase'] == 'assessment'
    assert (Path(directory) / 'loop.json').read_bytes() == original
    assert loops.resolve(str(root), loop_id, round_id)[1] == str(root / round_id)
    with pytest.raises(response.ContractViolation):
        loops.resolve(str(root), loop_id, 'c' * 16)


def should_reject_a_broken_generation_chain(tmp_path):
    from codex_review import loops
    # given a durable transition whose parent hash is corrupted
    directory = loops.create(str(tmp_path), 'a' * 16, 'b' * 16, {'mode': 'plan'})
    current = loops.read(directory)
    loops.append(directory, 0, dict(current, phase='assessment'), {'kind': 'judgment'})
    event = Path(directory) / 'transitions' / '00000001.json'
    stored = json.loads(event.read_text())
    stored['previous_sha256'] = '0' * 64
    event.write_text(json.dumps(stored))
    # when recovering, then corrupt history cannot manufacture current state
    with pytest.raises(response.ContractViolation, match='chain'):
        loops.read(directory)


@pytest.mark.parametrize('damage', ['none', 'artifact-missing', 'artifact-corrupted', 'target-missing', 'target-rewritten', 'unbound'])
def should_require_publication_bound_snapshot_bytes(tmp_path, damage):
    from codex_review import images, loops
    # given a target digest bound at publication, including its retained-file manifest
    round_id = 'b' * 16
    job = tmp_path / round_id
    job.mkdir()
    artifact = job / 'after'
    artifact.write_bytes(b'captured')
    target = job / 'target.json'
    target.write_text(json.dumps(dict(retained_files={'after': images.sha256_file(artifact)})))
    current = dict(round_targets={round_id: images.sha256_file(target)})
    # when retained bytes or their manifest disappear/change, then current bytes cannot rebind themselves
    if damage == 'artifact-missing':
        artifact.unlink()
    elif damage == 'artifact-corrupted':
        artifact.write_bytes(b'other')
    elif damage == 'target-missing':
        target.unlink()
    elif damage == 'target-rewritten':
        target.write_text('{"retained_files": {}}')
    elif damage == 'unbound':
        current = {}
    assert loops.snapshot_retained(str(tmp_path), current, round_id) is (damage == 'none')


def should_preserve_relative_record_names_through_a_state_alias(tmp_path):
    from codex_review import images, probe_integrity, records
    # given intact probe/report records reached through a symlinked state root
    root = tmp_path / 'real'
    job = root / ('b' * 16)
    job.mkdir(parents=True)
    (job / 'report.json').write_text('{}')
    (job / 'probes.json').write_text(json.dumps(dict(report_sha256=images.sha256_file(job / 'report.json'), annotations=[])))
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    aliased_job = str(alias / job.name)
    # when binding evidence and appending a revision, then both retain confined job-relative paths
    bound = probe_integrity.binding({}, aliased_job)
    assert bound['path'] == 'probes.json'
    assert bound['report_path'] == 'report.json'
    assert probe_integrity.retained(str(alias), bound)
    revised = records.next_probes({}, aliased_job, 'next')
    assert revised[job.name] == dict(report='report.json', prior_probes='probes.json', probes='probe-revisions/next.json')
    assert records.selected(dict(round_records=revised), aliased_job)['prior_probes'] == str(job / 'probes.json')
