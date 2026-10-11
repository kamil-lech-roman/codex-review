"""Append-only fix-path declarations over the original pre-apply baseline."""
from codex_review import apply, git, probes, response

require = response._require


def effective(record, amendments):
    annotations = {a['finding_id']: dict(a, fix_paths=list(a.get('fix_paths', [])))
                   for a in record['annotations']}
    for amendment in amendments:
        for item in amendment['additions']:
            paths = annotations[item['finding_id']]['fix_paths']
            paths.extend(path for path in item['paths'] if path not in paths)
    return dict(record, annotations=list(annotations.values()))


def validate(target, record, prior, submitted):
    require(isinstance(submitted, dict), 'bad-amendment', 'amendment must be an object')
    additions = submitted.get('additions')
    require(isinstance(additions, list) and additions, 'bad-amendment',
            'amendment needs nonempty additions', field='additions', expected='array of finding/path declarations')
    declared = {a['finding_id']: a for a in effective(record, prior)['annotations']}
    moved = set(apply.changed_paths(target, record['pre_apply_baseline']))
    seen, normalized = set(), []
    for item in additions:
        require(isinstance(item, dict) and isinstance(item.get('finding_id'), str),
                'bad-amendment', 'addition must name a finding', field='additions[].finding_id')
        finding_id = item['finding_id']
        require(finding_id in declared and declared[finding_id].get('disposition') == 'accepted',
                'finding-not-accepted', 'path additions require an accepted finding')
        require(finding_id not in seen, 'duplicate-finding', 'combine paths into one addition per finding')
        seen.add(finding_id)
        for field in ('rationale', 'authority'):
            require(isinstance(item.get(field), str) and item[field].strip(), 'missing-' + field,
                    'each addition needs its rationale and existing scope authority', field=field)
        paths = probes.canonicalise_fix_paths(target, item.get('paths'), finding_id)
        if target['mode'] == 'code':
            for path in paths:
                ignored = git.run(('check-ignore', '--quiet', '--', path), cwd=target['repo_root'], check=False)
                absolute = apply._declared(target, dict(fix_paths=[path]))[0]
                bound = absolute in record['pre_apply_baseline'].get('external_images', {})
                require(ignored.returncode == 1 or ignored.returncode == 0 and bound,
                        'amendment-unbound-path',
                        'added paths must remain observable by the original baseline', path=path)
        require(not set(paths) & set(declared[finding_id]['fix_paths']), 'already-declared-path',
                'amendments only add paths not previously declared for this finding')
        require(not moved & set(apply._declared(target, dict(fix_paths=paths))), 'amendment-after-edit',
                'added paths must still match the original pre-apply baseline')
        normalized.append(dict(item, paths=paths))
    return dict(submitted, additions=normalized)
