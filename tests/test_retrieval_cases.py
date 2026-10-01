from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest
from zzaimy.dataset.retrieval_cases import validate_case, evaluation_row, digest


def sample():
    text = '합성 평가 원문'
    return {'id':'case-a', 'question':'그 평가 기준은?',
            'resolved_question':'합성 A 사업 3차년도 평가에 적용되는 기준은?',
            'program_id':'program:a', 'year_ids':['year:a:r3'], 'purpose':'평가 기준 확인',
            'decision':'answer',
            'candidates':[{'node_id':f'doc:{i}:sec:1.2','program_id':f'program:{p}',
                           'year_id':f'year:{p}:r3', 'document_kind':'평가편람',
                           'text':text, 'source_hash':hashlib.sha256(text.encode()).hexdigest()}
                          for i,p in ((1,'a'),(2,'b'))],
            'selected':[{'node_id':'doc:1:sec:1.2', 'use_as':'applicable_criterion', 'reason':'질문의 사업·연차 기준'}],
            'rejected':[{'node_id':'doc:2:sec:1.2', 'reason':'다른 사업의 평가 기준'}]}


def reviewed(case):
    case['review']={'decision':'accept','reviewer':'independent-reviewer',
                    'independent':True,'source_checked':True,'content_sha256':digest(case)}
    return case


def test_scope_selection_and_input_preservation():
    case=sample(); before=deepcopy(case)
    validate_case(case)
    assert case == before
    row=evaluation_row(reviewed(case))
    assert row['gold_nodes'] == ['doc:1:sec:1.2']
    assert row['question'] == case['resolved_question']
    assert 'candidates' not in row and 'rejected' not in row


def test_other_program_criteria_cannot_become_target_criteria():
    case=sample()
    case['selected'][0]['node_id']='doc:2:sec:1.2'
    case['rejected'][0]['node_id']='doc:1:sec:1.2'
    case['reference_program_ids']=['program:b']
    with pytest.raises(ValueError, match='cross_program_criterion'):
        validate_case(case)
    case['selected'][0]['use_as']='reference_example'
    validate_case(case)
    case['reference_program_ids']=[]
    with pytest.raises(ValueError, match='undeclared_cross_program'):
        validate_case(case)


@pytest.mark.parametrize('target,other', [('linc30', 'aid'), ('aid', 'linc30'),
                                        ('rise', 'regional_innovation')])
def test_no_program_is_a_universal_criterion(target, other):
    case = sample()
    case['program_id'] = f'program:{target}'
    case['year_ids'] = []
    case['candidates'][0]['program_id'] = f'program:{target}'
    case['candidates'][1]['program_id'] = f'program:{other}'
    validate_case(case)
    case['selected'][0]['node_id'] = 'doc:2:sec:1.2'
    case['rejected'][0]['node_id'] = 'doc:1:sec:1.2'
    with pytest.raises(ValueError, match='cross_program_criterion_or_target'):
        validate_case(case)


def test_wrong_year_and_missing_candidate_decisions_fail():
    case=sample();case['year_ids']=['year:a:r2']
    with pytest.raises(ValueError, match='year_scope'):validate_case(case)
    case=sample();case['rejected']=[]
    with pytest.raises(ValueError, match='unaccounted'):validate_case(case)


def test_missing_evidence_means_retrieve_more_not_answer():
    case=sample();case.update(candidates=[],selected=[],rejected=[])
    with pytest.raises(ValueError, match='answer_without'):validate_case(case)
    case.update(decision='retrieve_more',next_action='해당 사업 평가편람의 원문을 검색')
    validate_case(case)
    with pytest.raises(ValueError, match='non_answerable'):evaluation_row(reviewed(case))


def test_no_auto_gold_or_stale_review():
    case=sample()
    with pytest.raises(ValueError, match='independent_source_review'):evaluation_row(case)
    reviewed(case);case['resolved_question']+=' 수정'
    with pytest.raises(ValueError, match='independent_source_review'):evaluation_row(case)
    reviewed(case);case['review']['independent']=False
    with pytest.raises(ValueError, match='independent_source_review'):evaluation_row(case)


def test_changed_excerpt_requires_new_hash_and_new_review():
    case = reviewed(sample())
    case['candidates'][0]['text'] += ' 변경'
    with pytest.raises(ValueError, match='source_excerpt_hash_mismatch'):
        validate_case(case)
    case['candidates'][0]['source_hash'] = hashlib.sha256(
        case['candidates'][0]['text'].encode('utf-8')).hexdigest()
    validate_case(case)
    with pytest.raises(ValueError, match='independent_source_review'):
        evaluation_row(case)


@pytest.mark.parametrize('review', ['accept', ['accept'], True, 1])
def test_malformed_review_is_held(review):
    case = sample()
    case['review'] = review
    with pytest.raises(ValueError, match='independent_source_review'):
        evaluation_row(case)


def test_cli_never_partially_exports_or_overwrites(tmp_path):
    spec=importlib.util.spec_from_file_location('cases_cli',Path(__file__).parents[1]/'scripts/audit_retrieval_cases.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    source=tmp_path/'cases.jsonl';out=tmp_path/'gold.jsonl'
    good=reviewed(sample());bad=sample();bad['id']='unreviewed'
    source.write_text('\n'.join(json.dumps(c) for c in [good,bad]))
    assert cli.main([str(source),'--export-gold',str(out)]) == 2
    assert not out.exists()
    source.write_text(json.dumps(good))
    assert cli.main([str(source),'--export-gold',str(out)]) == 0
    before=out.read_bytes()
    with pytest.raises(FileExistsError):cli.main([str(source),'--export-gold',str(out)])
    assert out.read_bytes() == before
