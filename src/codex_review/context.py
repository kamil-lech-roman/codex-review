"""Declared governing inputs: retain bytes and separate identity from locators."""
import json
import os

from codex_review import images, probes, projections


def _valid_path(path):
    try:
        return b'\x00' not in os.fsencode(path)
    except UnicodeError:
        return False


def companions(declaration_file, repository):
    return capture_companions(companion_declarations(declaration_file, repository), repository)


def companion_declarations(declaration_file, repository):
    if declaration_file is None:
        return []
    try:
        with open(declaration_file, encoding='utf-8') as handle:
            entries = json.load(handle)
    except (OSError, ValueError) as error:
        raise projections.CaptureRefused('bad-companions', 'cannot read companion declarations', 2,
                                         field='companions_file', expected='JSON array of {id, path}',
                                         detail=str(error))
    if not isinstance(entries, list):
        raise projections.CaptureRefused('bad-companions', 'companions must be an array', 2,
                                         field='companions', expected='JSON array of {id, path}')
    ids = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'id', 'path'} or
                not all(isinstance(entry.get(key), str) and entry[key].strip() for key in ('id', 'path')) or
                not _valid_path(entry['path']) or entry['id'] in ids or
                (not repository and not os.path.isabs(entry['path']))):
            raise projections.CaptureRefused('bad-companions', 'invalid companion declaration', 2,
                                             field='companions', expected='unique IDs and resolvable file paths')
        ids.add(entry['id'])
    return [dict(id=entry['id'], path=os.path.realpath(os.path.join(repository or '', entry['path'])))
            for entry in sorted(entries, key=lambda item: item['id'])]


def capture_companions(entries, repository):
    captured, blobs = [], {}
    for entry in sorted(entries, key=lambda item: item['id']):
        path = os.path.realpath(os.path.join(repository or '', entry['path']))
        try:
            before = images.image_of(path)
            with images.regular_reader(path) as handle:
                data = handle.read()
            after = images.image_of(path)
        except OSError as error:
            raise projections.CaptureRefused('companion-unreadable', 'cannot read companion', 3,
                                             id=entry['id'], path=path, detail=str(error))
        digest = images.sha256_bytes(data)
        if before != after or after['content_identity'] != 'sha256:' + digest:
            raise projections.CaptureRefused('capture-race', 'companion moved during capture', 4,
                                             id=entry['id'], path=path)
        relative = None
        if repository and os.path.commonpath((path, repository)) == repository:
            relative = os.path.relpath(path, repository)
        content_path = 'inputs/' + digest
        captured.append(dict(id=entry['id'], path=path, repo_relative_path=relative,
                             image=after, bytes=len(data), content_path=content_path))
        blobs[content_path] = data
    return captured, blobs


def write_blobs(build, blobs):
    from codex_review import state
    directory = os.path.join(os.path.dirname(os.path.dirname(build)), 'blobs')
    os.makedirs(directory, exist_ok=True)
    for relative, data in blobs.items():
        digest = images.sha256_bytes(data)
        shared = os.path.join(directory, digest)
        if not os.path.exists(shared):
            temporary = shared + '.' + state.new_nonce()
            try:
                with open(temporary, 'xb') as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o444)
                try:
                    os.link(temporary, shared)
                except FileExistsError:
                    pass
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        if os.path.islink(shared) or images.sha256_file(shared) != digest:
            raise projections.CaptureRefused('input-store-corrupt', 'retained input digest mismatch', 8,
                                             digest=digest)
        destination = os.path.join(build, relative)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        if os.path.lexists(destination):
            if os.path.islink(destination) or images.sha256_file(destination) != digest:
                raise projections.CaptureRefused('input-store-corrupt', 'retained input digest mismatch', 8,
                                                 digest=digest)
        else:
            os.link(shared, destination)
    state._fsync_dir(directory)


def retained_manifest(policy, artifacts, blobs):
    from codex_review import qualification
    manifest = {path: images.sha256_bytes(data) for path, data in blobs.items()}
    manifest['AGENTS.md'] = images.sha256_bytes(policy)
    for item, sides in artifacts:
        prefix = 'artifacts/' + item['id'] + '/'
        manifest[prefix + 'metadata.json'] = qualification.digest(item)
        for side, data in zip(('before', 'after'), sides):
            manifest[prefix + side] = images.sha256_bytes(data)
    return manifest


def verify_retained(directory, target):
    root = os.path.realpath(directory)
    for relative, digest in target.get('retained_files', {}).items():
        path = os.path.join(root, relative)
        try:
            valid = (not os.path.isabs(relative) and '..' not in relative.split('/') and
                     os.path.commonpath((root, os.path.realpath(path))) == root and
                     not os.path.islink(path) and images.sha256_file(path) == digest)
        except OSError:
            valid = False
        if not valid:
            raise projections.CaptureRefused('capture-bytes-corrupt', 'frozen capture bytes are missing or changed', 8,
                                             path=relative, expected=digest)


def changed_image_paths(before, after):
    changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
    result = set()
    for path in changed:
        values = [side.get(path) or {} for side in (before, after)]
        kinds = {value.get('image', value).get('kind') for value in values}
        scaffolding = ('directory' in kinds and kinds <= {'directory', images.KIND_ABSENT, None} and
                       any(other.startswith(path + os.sep) for other in changed))
        if not scaffolding:
            result.add(path)
    return result


def unchanged(target, allowed=()):
    expected = dict(
        companions=[dict(id=item['id'], image=item['image']) for item in target.get('companions', [])],
        dependencies=[dict(id=item['id'], manifest=item['manifest']) for item in target.get('dependencies', [])])
    actual = external_identity(target)
    if not allowed:
        return actual == expected
    for old, new, declaration in zip(expected['companions'], actual['companions'], target.get('companions', [])):
        if declaration['path'] not in allowed and old != new:
            return False
    for old, new, declaration in zip(expected['dependencies'], actual['dependencies'], target.get('dependencies', [])):
        before, after = old['manifest'], new['manifest']
        if 'unreadable' in after:
            return False
        changed = changed_image_paths(before, after)
        for relative in changed:
            path = os.path.normpath(os.path.join(declaration['path'], relative))
            if path not in allowed:
                return False
    return True


def retain_applied_context(job_dir, target):
    if unchanged(target):
        return None
    captured, blobs = capture_companions(target.get('companions', []), target.get('context_repository'))
    dependencies = []
    for item in target.get('dependencies', []):
        manifest, data = dependency_manifest(item, target['dependencies'])
        dependencies.append(dict(item, manifest=manifest))
        blobs.update(data)
    snapshot = dict(companions=captured, dependencies=dependencies,
                    retained_files={path: images.sha256_bytes(data) for path, data in blobs.items()})
    if not unchanged(dict(target, **snapshot)):
        raise projections.CaptureRefused('capture-race', 'external context moved during apply capture', 4)
    write_blobs(os.path.join(job_dir, 'post-apply'), blobs)
    return snapshot


def after_apply(job_dir, target):
    path = os.path.join(job_dir, 'apply.json')
    if not os.path.isfile(path):
        return target
    with open(path, encoding='utf-8') as handle:
        snapshot = json.load(handle).get('post_apply_context')
    if snapshot is None:
        return target
    verify_retained(os.path.join(job_dir, 'post-apply'), snapshot)
    return dict(target, companions=snapshot['companions'], dependencies=snapshot['dependencies'])


def external_identity(target):
    dependencies = []
    for item in target.get('dependencies', []):
        try:
            manifest, _ = dependency_manifest(item, target['dependencies'])
        except (OSError, projections.CaptureRefused) as error:
            manifest = dict(unreadable=str(error))
        dependencies.append(dict(id=item['id'], manifest=manifest))
    return dict(companions=[dict(id=item['id'], image=images.image_of(item['path']))
                            for item in target.get('companions', [])], dependencies=dependencies)


def captured_bound_image(target, path):
    """Return a declared frozen image, or proven absence inside an enumerated root."""
    path = os.path.normpath(path)
    for item in target.get('companions', []):
        if path == item['path']:
            return item['image']
    for item in target.get('dependencies', []):
        root, manifest = item['path'], item['manifest']
        if os.path.commonpath((root, path)) != root:
            continue
        relative = os.path.relpath(path, root)
        if any(relative == e or relative.startswith(e + '/') for e in item['exclude']):
            continue
        if relative in manifest:
            return manifest[relative]['image']
        if relative == '.' or any((name == '.' or relative.startswith(name + '/')) and
                                  entry['image']['kind'] != 'directory' for name, entry in manifest.items()):
            continue
        return dict(kind=images.KIND_ABSENT, git_mode=None, content_identity=None)
    return None


def bound_images(target):
    manifest = {item['path']: images.image_of(item['path']) for item in target.get('companions', [])}
    for item in target.get('dependencies', []):
        entries, _ = dependency_manifest(item, target['dependencies'])
        manifest.update({os.path.normpath(os.path.join(item['path'], path)): value['image']
                         for path, value in entries.items()})
    return manifest


def retained_images(target, *, captured=False):
    return {probes.resolve_keeping_link(os.path.join(target['repo_root'], item['path'])):
            item['after'] if captured else images.image_of(os.path.join(target['repo_root'], item['path']))
            for item in target.get('artifacts_detail', []) if item.get('projection') == 'retained'}


def retained_unchanged(target, allowed=()):
    before, after = retained_images(target, captured=True), retained_images(target)
    return all(path in allowed or after[path] == image for path, image in before.items())


def repository_snapshot(repository):
    if not repository:
        return {}, {}
    manifest = projections.worktree_manifest(repository)
    blobs = {}
    for name, image in manifest.items():
        path = os.path.join(repository, name)
        if image['kind'] == images.KIND_REGULAR:
            with images.regular_reader(path) as handle:
                data = handle.read()
        elif image['kind'] == images.KIND_SYMLINK:
            data = os.fsencode(os.readlink(path))
        else:
            continue
        digest = images.sha256_bytes(data)
        if image['content_identity'] != 'sha256:' + digest:
            raise projections.CaptureRefused('capture-race', 'repository input changed', 4, path=name)
        blobs['inputs/' + digest] = data
    if projections.worktree_manifest(repository) != manifest:
        raise projections.CaptureRefused('capture-race', 'repository context changed', 4)
    return manifest, blobs


def applicable(target):
    if not unchanged(target) or not retained_unchanged(target):
        return False
    repository = target.get('context_repository')
    if repository and 'context_manifest' in target:
        if projections.worktree_manifest(repository) != target['context_manifest']:
            return False
    if target['mode'] == 'plan':
        identity = target['plan_identity']
        return (images.image_of(identity['plan_path']) == identity['plan_image'] and
                (not target.get('context_repo_identity') or
                 projections.repo_identity(repository) == target['context_repo_identity']))
    return projections.repo_identity(target['repo_root']) == target['repo_identity']


def apply_applicable(target, allowed=()):
    if not unchanged(target, allowed) or not retained_unchanged(target, allowed):
        return False
    original = target.get('repo_identity') or target.get('context_repo_identity')
    if not original:
        return True
    current = projections.repo_identity(target.get('repo_root') or target['context_repository'])
    return all(current[key] == original[key] for key in ('head_oid', 'ref_digest'))


CACHE_POLICY = 'fresh-python-numba-per-execution-v1'


def exclusion_key(root, runtime, launch_profile):
    from codex_review import codex, qualification, reviewer
    return qualification.key('cache-exclusion', dict(
        launch_profile=launch_profile, provider='codex',
        authentication=codex.authentication_settings(),
        global_policy_sources=reviewer.global_policy_sources(),
        execution_home=reviewer.execution_home(),
        cache_policy=CACHE_POLICY, root={k: root[k] for k in ('id', 'path', 'exclude', 'manifest')},
        runtime=[{k: item[k] for k in ('id', 'path', 'exclude', 'manifest')} for item in runtime],
        shell=dict(path='/bin/bash', image=images.image_of('/bin/bash')),
        sandbox='read-only', ignore_user_config=True, ignore_rules=True))


def dependency_declarations(declaration_file, repository):
    if declaration_file is None:
        return []
    try:
        with open(declaration_file, encoding='utf-8') as handle:
            entries = json.load(handle)
    except (OSError, ValueError) as error:
        raise projections.CaptureRefused('bad-dependencies', str(error), 2,
                                         field='dependencies_file', expected='array of {id, path, exclude?}')
    if not isinstance(entries, list):
        raise projections.CaptureRefused('bad-dependencies', 'dependencies must be an array', 2)
    ids, roots = set(), []
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) - {'id', 'path', 'exclude'} or
                not all(isinstance(entry.get(k), str) and entry[k].strip() for k in ('id', 'path')) or
                not _valid_path(entry['path']) or entry['id'] in ids or
                (not repository and not os.path.isabs(entry['path']))):
            raise projections.CaptureRefused('bad-dependencies', 'invalid dependency declaration', 2,
                                             field='dependencies', expected='unique IDs and file/directory paths')
        ids.add(entry['id'])
        path = os.path.abspath(os.path.join(repository or '', entry['path']))
        path = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
        exclude = entry.get('exclude', [])
        if (not isinstance(exclude, list) or not all(isinstance(e, str) and e and
                not os.path.isabs(e) and not set(e) & set('*?[]') and
                not any(p in ('', '.', '..') for p in e.split('/')) for e in exclude) or
                len(set(exclude)) != len(exclude) or (exclude and not os.path.isdir(path))):
            raise projections.CaptureRefused('bad-dependencies', 'invalid exclusion set', 2,
                                             field='exclude', expected='exact root-relative files or subtrees')
        roots.append(dict(id=entry['id'], path=path, exclude=sorted(exclude),
                          repo_relative_path=os.path.relpath(path, repository) if repository and
                          os.path.commonpath((path, repository)) == repository else None))
    return sorted(roots, key=lambda entry: entry['id'])


def dependencies(declaration_file, repository, state_root, launch_profile='one-pass'):
    from codex_review import qualification
    roots = dependency_declarations(declaration_file, repository)
    captured, blobs = [], {}
    for root in roots:
        manifest, data = dependency_manifest(root, roots)
        captured.append(dict(root, manifest=manifest))
        blobs.update(data)
    for item in captured:
        if item['exclude']:
            required = exclusion_key(item, captured, launch_profile)
            status = qualification.lookup(state_root, required)
            if not status['valid']:
                raise projections.CaptureRefused('bad-dependencies', 'exclusions require matching qualification', 2,
                                                 field='exclude', expected='qualified read exclusion', qualification=status)
            item['qualification_sha256'] = status['digest']
            item['qualification'] = status
    return captured, blobs


def exclusion_qualifications(target, state_root):
    from codex_review import qualification
    runtime = target.get('dependencies', [])
    profile = 'persistent' if target.get('loop_id') else 'one-pass'
    return [qualification.lookup(state_root, exclusion_key(item, runtime, profile)) for item in runtime if item['exclude']]


def dependency_image(path):
    if os.path.isdir(path) and not os.path.islink(path):
        return dict(kind='directory', git_mode='040000', content_identity=None)
    return images.image_of(path)


def dependency_manifest(root, declared):
    path, exclusions = root['path'], root['exclude']
    def excluded(name):
        return any(name == e or name.startswith(e + '/') for e in exclusions)
    paths = []
    def fail(error):
        raise error
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            paths.append(('.', path))
            for directory, dirs, files in os.walk(path, followlinks=False, onerror=fail):
                dirs.sort()
                for name in dirs[:]:
                    absolute = os.path.join(directory, name)
                    relative = os.path.relpath(absolute, path)
                    if excluded(relative):
                        dirs.remove(name)
                    else:
                        paths.append((relative, absolute))
                        if os.path.islink(absolute):
                            dirs.remove(name)
                for name in sorted(files):
                    absolute = os.path.join(directory, name)
                    relative = os.path.relpath(absolute, path)
                    if not excluded(relative):
                        paths.append((relative, absolute))
        else:
            paths.append(('.', path))
        manifest, blobs = {}, {}
        for relative, absolute in paths:
            before = dependency_image(absolute)
            if before['kind'] == 'directory':
                manifest[relative] = dict(image=before, bytes=0, content_path=None)
                continue
            if before['kind'] == images.KIND_SYMLINK:
                referent = os.path.realpath(absolute)
                covered = any((referent == r['path'] or
                               (os.path.isdir(r['path']) and not os.path.islink(r['path']) and
                                os.path.commonpath((referent, r['path'])) == r['path']))
                              and not any(os.path.relpath(referent, r['path']) == e or
                                          os.path.relpath(referent, r['path']).startswith(e + '/')
                                          for e in r['exclude']) for r in declared)
                if not covered:
                    raise OSError('symlink referent must be declared: ' + referent)
                data = os.fsencode(os.readlink(absolute))
            elif before['kind'] == images.KIND_REGULAR:
                with images.regular_reader(absolute) as handle:
                    data = handle.read()
            else:
                raise OSError('dependency is not a readable file: ' + absolute)
            digest = images.sha256_bytes(data)
            if images.image_of(absolute) != before or before['content_identity'] != 'sha256:' + digest:
                raise projections.CaptureRefused('capture-race', 'dependency moved during capture', 4, path=absolute)
            content_path = 'inputs/' + digest
            manifest[relative] = dict(image=before, bytes=len(data), content_path=content_path)
            blobs[content_path] = data
        return manifest, blobs
    except OSError as error:
        raise projections.CaptureRefused('dependency-unreadable', str(error), 3,
                                         field='dependencies', path=path)


def expected_paths(path, actual):
    if path is None:
        return None, None
    try:
        with images.regular_reader(path) as handle:
            raw = handle.read()
        declared = json.loads(raw)
        if (not isinstance(declared, list) or not all(isinstance(p, str) and p and
                not os.path.isabs(p) and not any(part in ('', '.', '..') for part in p.split('/'))
                for p in declared) or len(set(declared)) != len(declared)):
            raise ValueError('expected unique repository-relative file paths')
    except (OSError, ValueError) as error:
        raise projections.CaptureRefused('bad-expect-paths', str(error), 2,
                                         field='expect_paths', expected='JSON string array of repository-relative paths')
    if set(declared) != set(actual):
        raise projections.CaptureRefused('paths-mismatch', 'captured paths differ from the assertion', 3,
                                         missing=sorted(set(declared) - set(actual)), extra=sorted(set(actual) - set(declared)))
    return dict(paths=sorted(declared), sha256=images.sha256_bytes(raw)), raw
