import pytest
from zzaimy.dataset.batch_candidates import windows, select_documents, job_key, convert_response


def test_windows_preserve_chunks_and_skip_oversize():
    chunks = [{'id':1,'content':'abc'}, {'id':2,'content':'def'}, {'id':3,'content':'x'*10}]
    assert list(windows(chunks, 5)) == [[{'id':1,'text':'abc'}],[{'id':2,'text':'def'}]]


def test_program_round_robin_and_conflicting_assignments():
    def doc(i): return {'doc_id':i,'kind':'plan'}
    catalog = {'programs':[{'program':'a','docs':[doc(1),doc(2),doc(9)]},
                           {'program':'b','docs':[doc(3),doc(9)]}]}
    assert [d['doc_id'] for p,d in select_documents(catalog)] == [1,3,2]


def test_source_version_changes_job_key():
    assert job_key({'program':'a'},[{'text':'x'}]) != job_key({'program':'a'},[{'text':'y'}])


def response(parent=None):
    item = {'question':'q','answer':'a','rationale':'r','path':['topic'],'kind':'qa','chunk_ids':[1]}
    return {'scope_confirmed':True,'candidates':[item, {**item,'parent_index':parent}]}


def test_actual_parent_id_is_preserved():
    rows = convert_response(response(0), {'doc_id':2}, [{'id':1}], 'abc')
    assert rows[1]['parent'] == rows[0]['id']
    assert rows[1]['refs'] == [[2,1]]


def test_outside_reference_rejected():
    with pytest.raises(ValueError, match='reference_outside_window'):
        convert_response(response(), {'doc_id':2}, [{'id':3}], 'abc')


def test_future_parent_rejected():
    with pytest.raises(ValueError, match='invalid_parent_index'):
        convert_response(response(1), {'doc_id':2}, [{'id':1}], 'abc')


def test_unconfirmed_scope_rejected():
    with pytest.raises(ValueError, match='program_scope_unconfirmed'):
        convert_response({'scope_confirmed':False}, {}, [], 'abc')
