import pytest
from zzaimy.dataset.authoring import prepare_tasks
from zzaimy.dataset.candidate_publish import prepare_publication


def sample():
    rows = [{'id':'one','kind':'확인','question':'시험사업의 지원 목적은 무엇인가요?',
             'answer':'현장 교육 지원입니다.','rationale':'사업 개요에 명시되어 있습니다.',
             'path':['개요'],'refs':[[1,2]]}]
    manifest = {'program_id':'test','program':'시험사업','document_ids':[1],'protect_sources':True}
    resolve = lambda did,cid: {'text':'시험사업은 현장 교육을 지원합니다.','location':'문서'}
    tasks, _ = prepare_tasks(rows, manifest, resolve, author='test')
    return {'job':'job','model':'test','status':'candidate','rows':rows,'manifest':manifest,'tasks':tasks}, resolve


def test_publication_is_pending_not_approved():
    candidate, resolve = sample()
    tasks = prepare_publication(candidate, resolve)
    assert tasks[0]['data']['관리_상태'] == '원문 대조 대기'
    assert tasks[0]['data']['관리_승인'] == 'SFT 승인 전'
    assert not tasks[0]['data']['_record']['reviewed']


def test_changed_source_is_not_published():
    candidate, _ = sample()
    with pytest.raises(ValueError, match='source_changed'):
        prepare_publication(candidate, lambda d,c:{'text':'시험사업 현장 교육 지원 내용 변경','location':'문서'})


def test_held_candidate_is_not_published():
    candidate, resolve = sample()
    candidate['status'] = 'held'
    with pytest.raises(ValueError, match='candidate_not_eligible'):
        prepare_publication(candidate, resolve)
