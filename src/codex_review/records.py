"""Loop-selected immutable report/probe revisions; one-pass paths stay unchanged."""
import os

from codex_review import images, response


def selected(current, job_dir):
    relative = current.get('round_records', {}).get(os.path.basename(job_dir), {}) if current else {}
    result = {}
    for kind, default in (('report', 'report.json'), ('probes', 'probes.json'), ('prior_probes', None)):
        name = relative.get(kind, default)
        if name is None:
            result[kind] = None
            continue
        path = os.path.realpath(os.path.join(job_dir, name))
        response._require(not os.path.isabs(name) and '..' not in name.split('/') and
                          os.path.commonpath((os.path.realpath(job_dir), path)) == os.path.realpath(job_dir),
                          'bad-record-path', 'round record must remain in its job')
        result[kind] = path
    return result


def digest(path):
    return images.sha256_file(path) if path and os.path.isfile(path) else None


def relative(path, job_dir):
    return os.path.relpath(os.path.realpath(path), os.path.realpath(job_dir))


def next_probes(current, job_dir, revision):
    selected_records = selected(current, job_dir)
    paths = dict(report=relative(selected_records['report'], job_dir),
                 prior_probes=relative(selected_records['probes'], job_dir),
                 probes='probe-revisions/' + revision + '.json')
    return dict(current.get('round_records', {}), **{os.path.basename(job_dir): paths})
