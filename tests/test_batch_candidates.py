import pytest
from zzaimy.dataset.batch_candidates import windows, select_documents, job_key, convert_response, generate_checked


def test_windows_preserve_chunks_and_skip_oversize():
    chunks = [{'id':1,'content':'abc'}, {'id':2,'content':'def'}, {'id':3,'content':'x'*10}]
    assert list(windows(chunks, 5)) == [[{'id':1,'text':'abc'}],[{'id':2,'text':'def'}]]


def test_table_plain_text_does_not_crash_document_iteration():
    chunks = [{'id':1,'kind':'table','content':'2026년 성과 | 목표 12건'},
              {'id':2,'kind':'table','content':'{"text":"결과 13건"}'},
              {'id':3,'kind':'table','content':'{"text":null}'}]
    assert list(windows(chunks)) == [[{'id':1,'text':'2026년 성과 | 목표 12건'},
                                     {'id':2,'text':'결과 13건'}]]


def test_program_round_robin_and_conflicting_assignments():
    def doc(i): return {'doc_id':i,'kind':'plan'}
    catalog = {'programs':[{'program':'a','docs':[doc(1),doc(2),doc(9)]},
                           {'program':'b','docs':[doc(3),doc(9)]}]}
    assert [d['doc_id'] for p,d in select_documents(catalog)] == [1,3,2]


def test_source_version_changes_job_key():
    assert job_key({'program':'a'},[{'text':'x'}]) != job_key({'program':'a'},[{'text':'y'}])


def response(parent=None):
    item = {'question':'q','answer':'a','rationale':'r','path':['topic'],'kind':'qa','chunk_ids':[1]}
    return {'scope_confirmed':True,'candidates':[item, {**item,'parent_index':parent}, {**item,'parent_index':1}]}


def test_actual_parent_id_is_preserved():
    rows = convert_response(response(0), {'doc_id':2}, [{'id':1}], 'abc')
    assert rows[1]['parent'] == rows[0]['id']
    assert rows[1]['refs'] == [[2,1]]
    assert rows[2]['parent'] == rows[1]['id']
    assert {r['conversation_id'] for r in rows} == {'dialogue-abc'}
    assert {r['split_group'] for r in rows} == {'document:2'}


def test_outside_reference_rejected():
    with pytest.raises(ValueError, match='reference_outside_window'):
        convert_response(response(), {'doc_id':2}, [{'id':3}], 'abc')


def test_future_parent_rejected():
    with pytest.raises(ValueError, match='disconnected_conversation'):
        convert_response(response(1), {'doc_id':2}, [{'id':1}], 'abc')


def test_independent_questions_are_not_a_conversation():
    with pytest.raises(ValueError, match='disconnected_conversation'):
        convert_response(response(), {'doc_id':2}, [{'id':1}], 'abc')


def test_unconfirmed_scope_rejected():
    with pytest.raises(ValueError, match='program_scope_unconfirmed'):
        convert_response({'scope_confirmed':False}, {}, [], 'abc')


def test_two_linked_turns_are_valid_but_one_is_not():
    value = response(0)
    value['candidates'] = value['candidates'][:2]
    assert len(convert_response(value, {'doc_id':2}, [{'id':1}], 'abc')) == 2
    value['candidates'] = value['candidates'][:1]
    with pytest.raises(ValueError, match='invalid_candidate_count'):
        convert_response(value, {'doc_id':2}, [{'id':1}], 'abc')


def test_correction_uses_feedback_and_rechecks():
    feedbacks = []
    def generate(feedback):
        feedbacks.append(feedback)
        return '{"scope_confirmed":true}'
    def check(parsed):
        return {'preflight': {'held': int(len(feedbacks) == 1), 'issues': {'x':['unsupported_number']}},
                'number_details': {'x':['2028']}}
    result = generate_checked(generate, check)
    assert result['status'] == 'candidate'
    assert feedbacks[1]['number_details'] == {'x':['2028']}
    assert len(result['attempts']) == 2


def test_failed_correction_stays_held_and_is_bounded():
    result = generate_checked(lambda feedback:'{}', lambda parsed: {'preflight':{'held':1,'issues':{'x':['privacy_requires_revision']}}})
    assert result['status'] == 'held'
    assert len(result['attempts']) == 2


def test_scope_rejection_is_not_retried_or_forced():
    result = generate_checked(lambda feedback:'{"scope_confirmed":false}', lambda parsed: pytest.fail('no check'))
    assert result['hold_reason'] == 'program_scope_unconfirmed'
    assert len(result['attempts']) == 1


def test_changed_source_stops_without_retry():
    def check(parsed):
        raise ValueError('source_missing_or_empty')
    result = generate_checked(lambda feedback:'{}', check)
    assert result['hold_reason'] == 'source_changed'
    assert len(result['attempts']) == 1


def test_malformed_json_can_be_corrected():
    values = iter(['not json', '{}'])
    result = generate_checked(lambda feedback:next(values),
                              lambda parsed:{'preflight':{'held':0,'issues':{}}})
    assert result['status'] == 'candidate'
    assert result['attempts'][0]['feedback'] == {'format_error':'invalid_response'}


def test_semantic_gate_requires_all_checks_and_no_concerns():
    import json
    from zzaimy.dataset.batch_candidates import semantic_result, REVIEW_CHECKS
    value = {'checks':dict.fromkeys(REVIEW_CHECKS, True),'issues':[]}
    assert semantic_result(json.dumps(value))['passed']
    assert not semantic_result(json.dumps(value))['human_approved']
    value['checks']['source_readable'] = False
    assert not semantic_result(json.dumps(value))['passed']
    value['checks']['source_readable'] = 'true'
    with pytest.raises(ValueError): semantic_result(json.dumps(value))
    value['checks'] = {}
    with pytest.raises(ValueError): semantic_result(json.dumps(value))
