"""Durable editing ownership; locks cover only record updates, never host editing."""
import json
import os

from codex_review import images, jobs, stage, state


class Conflict(Exception):
    pass


def scope(target):
    if target.get('apply_scope'):
        return target['apply_scope']
    if target['mode'] == 'plan':
        return os.path.dirname(target['plan_identity']['plan_path'])
    return target['repo_root']


def _record(path):
    try:
        with open(path, encoding='utf-8') as handle:
            record = json.load(handle)
        checkout, holder = record['checkout'], record['job_id']
        if (not isinstance(checkout, str) or not os.path.isabs(checkout) or
                os.path.realpath(checkout) != checkout or not state.is_job_id(holder) or
                os.path.basename(path) != images.sha256_bytes(checkout.encode('utf-8')) + '.json'):
            raise ValueError('unknown ownership identity')
        return record
    except (OSError, ValueError, KeyError, TypeError):
        raise Conflict('apply ownership holder is unknown')


def _terminal(root, holder):
    holder_dir = jobs.resolve(root, holder)
    return holder_dir and stage.is_terminal(stage.describe(holder_dir)['stage'])


def _update(root, checkout, job_id, action):
    checkout = os.path.realpath(checkout)
    directory = os.path.join(root, 'owners')
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, images.sha256_bytes(checkout.encode('utf-8')) + '.json')
    with jobs.mutex(directory):
        holder = None
        if os.path.exists(path):
            holder = _record(path)['job_id']
        if action != 'release':
            for name in sorted(os.listdir(directory)):
                other_path = os.path.join(directory, name)
                if not name.endswith('.json') or other_path == path:
                    continue
                other = _record(other_path)
                overlaps = os.path.commonpath((checkout, other['checkout'])) in (checkout, other['checkout'])
                if overlaps and other['job_id'] != job_id and not _terminal(root, other['job_id']):
                    raise Conflict('apply ownership held by ' + other['job_id'])
        if action == 'claim' and holder is not None:
            if _terminal(root, holder):
                holder = None
        if action == 'release' and holder != job_id:
            job_dir = jobs.resolve(root, job_id)
            if job_dir and stage.is_terminal(stage.describe(job_dir)['stage']):
                return
        if holder not in (None, job_id):
            raise Conflict('apply ownership held by ' + holder)
        if action in ('require', 'release') and holder != job_id:
            raise Conflict('apply ownership is absent')
        if action == 'claim' and holder is None:
            state.atomic_json(path, {'checkout': checkout, 'job_id': job_id})
        elif action == 'release':
            os.unlink(path)
            state._fsync_dir(directory)


def claim(root, checkout, job_id):
    _update(root, checkout, job_id, 'claim')


def require_owner(root, checkout, job_id):
    _update(root, checkout, job_id, 'require')


def release(root, checkout, job_id):
    _update(root, checkout, job_id, 'release')
