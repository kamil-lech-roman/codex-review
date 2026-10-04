"""Cited candidate transitions authorize recapture, never a moving checkout."""
import os

from codex_review import apply, context, counting, execution, findings, git, images, loops, probes, records, response, stage, state

require = response._require


def scope(directory, target, decision):
    supplied = decision.get('scope', {})
    require(isinstance(supplied, dict) and not set(supplied) - {'companions', 'dependencies', 'expected_paths'},
            'bad-transition-scope', 'scope may declare companions, dependencies and expected_paths')
    if supplied:
        response._text(decision.get('scope_authority'), 'scope_authority')
    repository = target.get('repo_root') or target.get('context_repository')
    captured, declarations = {}, {}
    for name in ('companions', 'dependencies'):
        fields = ('id', 'path', 'exclude') if name == 'dependencies' else ('id', 'path')
        entries = supplied.get(name, [{k: item[k] for k in fields if k in item} for item in target.get(name, [])])
        path = os.path.join(directory, 'staging', name + '-' + state.new_nonce() + '.json')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        state.atomic_json(path, entries)
        captured[name], _ = (context.companions(path, repository) if name == 'companions' else
                             context.dependencies(path, repository, os.path.dirname(directory), 'persistent'))
        declarations[name] = [{k: item[k] for k in fields if k in item} for item in captured[name]]
    assertion = (target.get('expected_paths') or {}).get('paths')
    if 'expected_paths' in supplied:
        require(target['mode'] == 'code', 'option-not-applicable', 'plan captures have no path assertion')
        assertion = supplied['expected_paths']
        if assertion is not None:
            path = os.path.join(directory, 'staging', 'paths-' + state.new_nonce() + '.json')
            state.atomic_json(path, assertion)
            context.expected_paths(path, assertion)
    declarations['expected_paths'] = assertion
    return dict(declarations=declarations, inputs=captured)


def applicable(target, authorization):
    return (apply.identity_sha(target) == authorization['candidate_sha256'] and
            context.unchanged(dict(target, **authorization['scope']['inputs'])) and
            execution.key(dict(target, **authorization['scope']['inputs'])) == authorization.get('author_key'))


def resolve_base(repository, value):
    response._text(value, 'base')
    resolved = git.run(('rev-parse', '--verify', '--end-of-options', value), cwd=repository, check=False)
    require(resolved.returncode == 0, 'unresolvable-base', 'replacement base does not resolve')
    oid = resolved.stdout.strip()
    kind = git.run(('cat-file', '-t', oid), cwd=repository, check=False)
    require(kind.returncode == 0 and kind.stdout.strip() in ('commit', 'tree'),
            'unresolvable-base', 'replacement base must identify a commit or tree')
    return oid


def candidate(directory, current, decision):
    from codex_review import loop_driver, main
    require(current['phase'] not in ('calling', 'closed', 'capture-pending'), 'wrong-stage',
            'reconcile pending effects before changing an open candidate')
    require(decision.get('round') == current['current_round'], 'round-required', 'transition must name the current round')
    job_dir = os.path.join(os.path.dirname(directory), current['current_round'])
    target_path = os.path.join(job_dir, 'target.json')
    require(decision.get('target_sha256') == images.sha256_file(target_path), 'stale-transition-target',
            'transition must bind the frozen target record')
    target = main.load_target(job_dir)
    require(decision.get('candidate_sha256') == apply.identity_sha(target), 'stale-candidate',
            'transition must bind the observed candidate, including references and external inputs')
    response._text(decision.get('authority'), 'authority')
    citations = decision.get('evidence_ids')
    require(isinstance(citations, list) and citations, 'missing-transition-evidence', 'cite the scope/attribution evidence')
    for citation in citations:
        require(isinstance(citation, str) and probes.EVIDENCE_ID.fullmatch(citation), 'bad-evidence-id', 'cite sealed transition evidence')
        metadata = loops._json(os.path.join(job_dir, 'evidence', citation, 'metadata.json'))
        probes._rehash(job_dir, os.path.basename(job_dir), citation, metadata['source_path'])
    repository = target.get('repo_root') or target.get('context_repository')
    old_head = (target.get('repo_identity') or target.get('context_repo_identity') or {}).get('head_oid')
    new_head = git.head_oid(repository) if repository else None
    require(decision.get('old_head') == old_head and decision.get('new_head') == new_head,
            'transition-head-mismatch', 'transition must bind both the original and current HEAD')
    if decision['kind'] == 'authorized-commit':
        require(repository and new_head != old_head, 'not-a-commit-transition', 'authorized-commit requires a changed HEAD')
    requested = decision.get('equivalence', 'reset')
    require(requested in ('matching', 'reset', 'unresolved'), 'bad-equivalence',
            'equivalence must be matching, reset or unresolved')
    if requested == 'unresolved':
        return loops.append(directory, current['generation'],
                            dict(current, pause=dict(kind='equivalence-unresolved'), decisions=current['decisions'] + [decision]), decision)
    base = target.get('base_ref')
    if 'new_base' in decision:
        require(target['mode'] == 'code', 'option-not-applicable', 'plan capture has no comparison base')
        base = resolve_base(repository, decision['new_base'])
    selected_scope = scope(directory, target, decision)
    selected_key = loop_driver.launch_key(dict(target, **selected_scope['inputs']))
    if current.get('bound_launch_key') not in (None, selected_key):
        require(decision.get('launch_key') == selected_key, 'launch-key-required',
                'authorize the exact changed launch settings through the qualification key', expected=selected_key)
    authorization = dict(round=current['current_round'], candidate_sha256=decision['candidate_sha256'],
                         old_head=old_head, new_head=new_head, base=base, equivalence=requested,
                         target_sha256=decision['target_sha256'], scope=selected_scope, launch_key=selected_key,
                         author_key=execution.key(dict(target, **selected_scope['inputs'])))
    def operation(latest):
        require(applicable(target, authorization), 'stale-candidate',
                'candidate moved while its transition was being recorded')
        selected = records.selected(latest, job_dir)
        ledger = latest.get('findings', [])
        has_judgment = os.path.isfile(selected['report'])
        discarded = has_judgment and latest['current_round'] not in latest.get('processed_rounds', [])
        if has_judgment:
            ledger = findings.retain_unassessed(ledger, latest['current_round'], loops._json(selected['report']),
                                                images.sha256_file(selected['report']),
                                                latest.get('carried_findings', {}).get(latest['current_round']))
        if not stage.is_terminal(stage.describe(job_dir)['stage']):
            result = main.abandon([job_dir, '--reason', 'Superseded by cited candidate transition: ' + decision['authority']],
                                  probes_path=selected['probes'] if records.digest(selected['probes']) else selected['prior_probes'])
            require(result['exit'] == 0, 'transition-close-refused', 'current round could not be closed', result=result)
        updated = dict(latest, phase='recapture', pause=None, authorized_head=new_head,
                       recapture_authorization=authorization, findings=ledger,
                       decisions=latest['decisions'] + [decision],
                       counting=counting.reset(latest['counting']) if requested == 'reset' or discarded else latest['counting'])
        return updated, None, decision
    loops.mutate(directory, current['generation'], operation)
