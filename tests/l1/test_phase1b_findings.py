"""A later empty report cannot silently close an earlier accepted defect."""
import pytest

from codex_review import counting, findings, response


@pytest.mark.parametrize('read,status', [('unsupported', 'refuted'), ('confirmed-pre-existing', 'reviewed'),
                                       ('inconclusive', 'open')])
def should_distinguish_a_refuted_preexisting_claim_from_confirmed_behavior(read, status):
    # given a pre-existing annotation, whose schema has no author disposition
    annotation = dict(finding_id='F1', read=read, probe_execution='executed')
    # when recording it, then only an unsupported claim supplies a refuted successor
    result = findings.add([], 'round', [annotation])[0]
    assert result['status'] == status


@pytest.mark.parametrize('disposition', [None, 'rejected'])
def should_withhold_clean_credit_for_inconclusive_reads(disposition):
    # given an executed probe with an unresolved read, regardless of author disposition
    annotation = dict(finding_id='F1', read='inconclusive', probe_execution='executed')
    if disposition:
        annotation['disposition'] = disposition
    # when projecting the annotations into the convergence ledger
    ledger = findings.add([], 'origin', [annotation])
    result = counting.round_finished(counting.initial(), effort='xhigh', eligible=True,
                                     accepted=0, blockers=bool(findings.outstanding(ledger)))
    # then the unresolved finding is retained and cannot earn credit
    assert len(findings.blocking(ledger)) == 1
    assert result['streak'] == 0


def should_keep_round_local_ids_distinct_until_explicit_fixed_closure():
    # given two rounds reporting the same local ID
    ledger = findings.add([], 'round-a', [dict(finding_id='F001', disposition='accepted', fix_paths=['p.py'])])
    ledger = findings.add(ledger, 'round-b', [dict(finding_id='F001', disposition='accepted', fix_paths=['q.py'])])
    # when inspecting unresolved work, then no ID collision drops either finding
    assert [f['reference'] for f in findings.outstanding(ledger)] == [
        dict(round='round-a', local_id='F001'), dict(round='round-b', local_id='F001')]


@pytest.mark.parametrize('verification,changed,reviewed,eligible', [
    ('not-run-explicitly', ['p.py'], ['p.py'], True),
    ('passed', ['unrelated.py'], ['p.py'], True),
    ('passed', ['p.py'], ['other.py'], True),
    ('passed', ['p.py'], ['p.py'], False),
])
def should_refuse_fixed_closure_without_the_complete_evidence_chain(verification, changed, reviewed, eligible):
    # given an accepted defect and one missing link in its proposed closure
    item = findings.add([], 'origin', [dict(finding_id='F001', disposition='accepted', fix_paths=['p.py'])])[0]
    # when deciding closure, then unrelated green tests or an ineligible review cannot suffice
    with pytest.raises(response.ContractViolation):
        findings.fixed(item, dict(verification_result=verification, changed_paths=changed), reviewed,
                       eligible=eligible, decision=dict(rationale='checked fix'))


def should_retain_the_closure_evidence_after_a_relevant_verified_cold_recheck():
    # given a relevant captured fix and passing bound verification
    item = findings.add([], 'origin', [dict(finding_id='F001', disposition='accepted', fix_paths=['p.py'])])[0]
    # when a qualified re-examination confirms it
    result = findings.fixed(item, dict(verification_result='passed', changed_paths=['p.py']), ['p.py'],
                            eligible=True, decision=dict(rationale='Relevant assertion now holds'))
    # then the immutable reference survives and its closure is explicit
    assert result['reference'] == item['reference']
    assert result['status'] == 'fixed'
    assert result['resolution']['rationale'] == 'Relevant assertion now holds'
    assert findings.outstanding([result]) == []


def should_keep_accepted_defects_open_when_linking_a_recurrence():
    # given two accepted reports of the same issue on different snapshots
    original = findings.add([], 'old', [dict(finding_id='F1', disposition='accepted', fix_paths=['p.py'])])[0]
    successor = findings.add([], 'new', [dict(finding_id='F2', disposition='accepted', fix_paths=['p.py'])])[0]
    decision = dict(disposition='recurring', related_reference=successor['reference'], rationale='Same failing path')
    # when linked, then the relation cannot replace the original verified-fix closure requirement
    related = findings.related(original, successor, decision, applicable=False)
    assert related['status'] == 'open'
    assert related['resolution'] == decision


@pytest.mark.parametrize('changed,successor_disposition', [(True, 'rejected'), (False, 'inconclusive')])
def should_not_turn_new_bytes_or_an_unresolved_probe_into_old_refutation(changed, successor_disposition):
    original = findings.add([], 'old', [dict(finding_id='F1', disposition='inconclusive')])[0]
    successor = findings.add([], 'new', [dict(finding_id='F2', disposition=successor_disposition)])[0]
    # given a non-equivalent target or inconclusive successor, when relating, then the old question stays open
    with pytest.raises(response.ContractViolation, match='unchanged original'):
        findings.related(original, successor, dict(disposition='refuted'), applicable=not changed)


def should_preserve_retained_source_provenance_when_current_probes_accept_it():
    # given a late finding whose origin has no ordinary report or probes record
    source = dict(finding=dict(id='F1', classification='introduced'), late_attempt='attempt',
                  late_report_sha256='late-digest', unassessed_report_sha256='original-digest')
    item = dict(reference=dict(round='origin', local_id='F1'), status='open', disposition='unassessed', **source)
    annotation = dict(finding_id='retained-alias', disposition='accepted', read='confirmed-introduced',
                      probe_execution='executed', fix_paths=['p.py'])
    # when current-round probes adopt its independent acceptance, then its immutable origin survives
    result = findings.add([item], 'current', [annotation], {'retained-alias': item['reference']})[0]
    assert {key: result.get(key) for key in source} == source
    assert result['assessment'] == annotation
    assert result['disposition'] == 'accepted'


@pytest.mark.parametrize('read', ['confirmed-pre-existing', 'inconclusive', 'unsupported'])
def should_preserve_user_scope_resolution_when_probe_annotations_are_revised(read):
    # given a user-settled scope question in the durable ledger
    annotation = dict(finding_id='F1', probe_execution='executed', read='confirmed-pre-existing', scope_question=True)
    item = findings.add([], 'round', [annotation])[0]
    item.update(status='out-of-scope', resolution=dict(disposition='out-of-scope', asserted_by='user',
                                                     authorization='User excluded this existing issue'))
    # when a revised probe record includes that finding again, then the authority survives
    revised = dict(annotation, read=read, rationale='Latest observation')
    if read != 'confirmed-pre-existing':
        revised.pop('scope_question')
    result = findings.add([item], 'round', [revised])[0]
    assert result['status'] == 'out-of-scope'
    assert result['resolution'] == item['resolution']
    assert result['assessment'] == revised
    assert not findings.blocking([result])


@pytest.mark.parametrize('scope_status', ['reviewed', 'scope-question', 'out-of-scope'])
def should_allow_a_conclusive_preexisting_successor_to_resolve_an_introduced_classification(scope_status):
    # given a conclusive later pre-existing assessment with applicability checked by the caller
    original = findings.add([], 'old', [dict(finding_id='F1', disposition='inconclusive')])[0]
    successor = findings.add([], 'new', [dict(finding_id='F2', read='confirmed-pre-existing', probe_execution='executed')])[0]
    assert successor['status'] == 'reviewed'
    if scope_status != 'reviewed':
        successor.update(status='open' if scope_status == 'scope-question' else 'out-of-scope', disposition='scope-question')
    # when relating that classification counterexample, then the old claim is refuted
    resolved = findings.related(original, successor, dict(disposition='refuted'), applicable=True)
    assert resolved['status'] == 'refuted'
@pytest.mark.parametrize('status,outcome,expected', [('refuted', 'maintained', 'open'),
    ('refuted', 'withdrawn', 'refuted'), ('out-of-scope', 'maintained', 'out-of-scope'),
    ('open', 'maintained', 'open')])
def should_reopen_a_refuted_claim_when_its_rebuttal_is_maintained(status, outcome, expected):
    import copy
    # given an earlier author assessment and a later original-thread adjudication
    item = dict(reference=dict(round='r1', local_id='F-1'), status=status,
                disposition='rejected', assessment=dict(read='unsupported'))
    before = copy.deepcopy(item)
    # when adopting the adjudication, then only a maintained refutation becomes unresolved
    result = findings.maintained_disputes([item], 'r1', dict(decisions=[dict(id='F-1', outcome=outcome)]))
    assert result[0]['status'] == expected
    assert item == before
    assert result[0]['assessment'] == before['assessment']


@pytest.mark.parametrize('disposition', ['defer', 'inconclusive', 'disputed', 'unassessed'])
def should_keep_introduced_predecessors_open_across_recurrence(disposition):
    # given an unresolved introduced claim and a new pre-existing scope question
    original = dict(reference=dict(round='old', local_id='F1'), status='open', disposition=disposition)
    successor = dict(reference=dict(round='new', local_id='F2'), status='open', disposition='scope-question')
    # when linking recurrence, then excluding the successor cannot resolve the original claim
    linked = findings.related(original, successor, dict(disposition='recurring'), applicable=False, introduced=True)
    assert linked['status'] == 'open'
    assert findings.outstanding([linked, dict(successor, status='out-of-scope')]) == [linked]
