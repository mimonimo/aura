from copy import deepcopy
import pytest
from zzaimy.dataset.authoring import prepare_tasks, validate_rows, validate_manifest, content_digest


def row(**kw):
    return dict(dict(id='one', kind='설명', question='목적은?', answer='교육 개선', rationale='원문 요약',
                     path=['개요'], refs=[[1, 2]]), **kw)


def manifest(**kw):
    return dict(dict(program_id='program-a-2026', program='가상 교육 사업', document_ids=[1, 3]), **kw)


def prepare(rows=None, scope=None, resolver=None, **kw):
    return prepare_tasks(rows or [row()], scope or manifest(),
                         resolver or (lambda *a: {'text':'교육 개선', 'location':'문서 · 조각'}),
                         author='test-author', privacy_check=kw.pop('privacy_check', lambda v:True),
                         number_check=kw.pop('number_check', lambda a,s:True), **kw)


def test_explicit_business_and_diagnostics_without_false_outline_coverage():
    rows, scope = [row()], manifest()
    before = deepcopy((rows, scope))
    tasks, report = prepare(rows, scope)
    assert (rows,scope) == before
    assert tasks[0]['data']['program_id'] == scope['program_id']
    assert tasks[0]['data']['_record']['author'] == 'test-author'
    assert tasks[0]['data']['_record']['reviewed'] is False
    assert report['documents_without_candidates'] == [3]
    assert report['nodes_without_candidates'] is None
    assert report['outline_match_checked'] is False
    assert report['semantic_review_required'] is True


@pytest.mark.parametrize('change', [
    {'program_id':''}, {'program':''}, {'document_ids':[]}, {'document_ids':[True]},
    {'document_ids':[1,1]}, {'document_ids':['1']}, {'node_paths':[]},
    {'node_paths':[['']]}, {'node_paths':[['개요'],['개요']]},
])
def test_invalid_manifest_is_rejected(change):
    with pytest.raises(ValueError): validate_manifest(manifest(**change))


@pytest.mark.parametrize('candidate,code', [
    (row(program_id='other'), 'cross_program_candidate'),
    (row(refs=[[999,2]]), 'source_outside_manifest'),
    (row(refs=[[1,2],[1,2]]), 'duplicate_source_reference'),
    (row(parent=[]), 'invalid_parent'),
    (row(revision_of='one'), 'invalid_revision'),
])
def test_cross_program_and_malformed_links_fail_closed(candidate,code):
    with pytest.raises(ValueError,match=code): prepare([candidate])


def test_missing_source_never_creates_a_task():
    for source in (None, {}, {'text':'', 'location':'x'}, {'text':'ok'}):
        with pytest.raises(ValueError,match='source_missing_or_empty'):
            prepare(resolver=lambda *a:source)


def test_outline_mismatch_and_uncovered_nodes_are_visible():
    scope = manifest(node_paths=[['개요'],['개요','목적']])
    assert prepare(scope=scope)[1]['nodes_without_candidates'] == [['개요','목적']]
    with pytest.raises(ValueError,match='node_path_outside_outline'):
        prepare([row(path=['만든 목차'])],scope)


def test_child_first_history_and_hold_propagation():
    parent = row(answer='unsupported')
    child = row(id='child',question='후속?',parent='one')
    grandchild = row(id='grandchild',question='그다음?',parent='child')
    tasks, report = prepare([grandchild,child,parent],number_check=lambda a,s:a!='unsupported')
    assert report['held'] == 3
    assert report['issues']['grandchild'] == ['ancestor_held']
    assert tasks[0]['data']['preflight']['issues'] == ['ancestor_held']
    assert tasks[0]['data']['_record']['history'][0]['answer'] == 'unsupported'


def test_privacy_failure_stops_followups_and_does_not_rewrite_source():
    tasks, report = prepare([row(),row(id='child',parent='one',question='후속?')],
                             privacy_check=lambda d:d['sample_id']!='one')
    assert report['held'] == 2
    assert tasks[0]['data']['evidence'] == '교육 개선'
    assert report['issues']['one'] == ['privacy_requires_revision']


def test_source_read_once_and_digest_changes_with_answer_or_history():
    calls = []
    def resolver(did,cid):
        calls.append((did,cid))
        return {'text':'교육 개선','location':'조각'}
    tasks,_ = prepare([row(),row(id='child',parent='one',question='후속?')],resolver=resolver)
    assert calls == [(1,2)]
    data = tasks[0]['data']
    digest = content_digest(data)
    for field in ('answer','question','evidence','history','path'):
        changed=dict(data); changed[field]+='changed'
        assert content_digest(changed) != digest


def test_normalized_duplicate_questions_are_rejected():
    with pytest.raises(ValueError,match='duplicate_question_in_context'):
        validate_rows([row(question='Ａ  질문'),row(id='two',question='A 질문')])


def test_same_names_different_program_ids_remain_distinct():
    first,_ = prepare(scope=manifest(program_id='school-a'))
    second,_ = prepare(scope=manifest(program_id='school-b'))
    assert first[0]['data']['program'] == second[0]['data']['program']
    assert first[0]['data']['program_id'] != second[0]['data']['program_id']


def test_followup_numbers_can_use_ancestor_evidence_but_not_sibling_evidence():
    parent = row(answer='참여자 7명', refs=[[1,2]])
    child = row(id='child',question='그 인원은?',parent='one',answer='7명',refs=[[1,4]])
    unrelated = row(id='other',question='별도 질문?',answer='7명',refs=[[1,4]])
    def source(d,c):
        return {'text':'참여자 7명' if c==2 else '교육 안내','location':'조각'}
    tasks, report = prepare([child,parent,unrelated], resolver=source,
                            number_check=lambda a,s:'7' not in a or any('7' in v for v in s))
    assert report['issues'] == {'other':['unsupported_number']}
