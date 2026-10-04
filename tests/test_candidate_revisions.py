import pytest
from zzaimy.dataset.revisions import revise_task, revise_batch


def sample():
    return {'id': 1, 'annotations': [], 'data': {'sample_id': 's', 'program': '사업A',
        'question': '목적은?', 'answer': '원문', 'rationale': '근거', 'path': '개요',
        'history': '단독 질문', 'evidence': '원문', 'ai_review': {'decision': 'accept'},
        '_record': {'refs': [[1, 2]], 'source_texts': ['원문']}}}


def test_revision_invalidates_prior_review():
    t = sample()
    d = revise_task(t, {'question': '사업A 공고의 목적은?', 'answer': '원문', 'rationale': '공고 근거'}, [t], lambda *args: '원문')
    assert 'ai_review' not in d and 'ai_review' in t['data']
    assert d['_record']['revision'] == 2
    assert d['question'] == d['_record']['question']
    assert d['preflight']['semantic_reviewed'] is False


def test_rejects_changed_source_annotations_and_dependent_turns():
    t = sample(); p = {'question': '사업A 목적은?', 'answer': '답', 'rationale': '근거'}
    with pytest.raises(ValueError, match='source_changed'):
        revise_task(t, p, [t], lambda *args: '변경')
    with pytest.raises(ValueError, match='dependent_turns'):
        revise_task(t, p, [t, {'data': {'_record': {'parent': 's'}}}], lambda *args: '원문')
    t['annotations'] = [{'id': 1}]
    with pytest.raises(ValueError, match='annotated_task'):
        revise_task(t, p, [t], lambda *args: '원문')


def test_joint_revision_rebuilds_history_even_when_child_first():
    root, child = sample(), sample()
    child['data']['sample_id'] = 'child'
    child['data']['_record']['parent'] = 's'
    p = {'question': '사업A 목적은?', 'answer': '새 답', 'rationale': '근거'}
    proposals = {'child': {**p, 'question': '그 근거는?'}, 's': p}
    result = revise_batch([root, child], proposals, lambda *args: '원문')
    assert result['child']['_record']['history'] == [{'question': p['question'], 'answer': p['answer']}]
    assert '새 답' in result['child']['history']
    assert 'ai_review' not in result['child']
    with pytest.raises(ValueError, match='dependent_turns'):
        revise_batch([root, child], {'s': p}, lambda *args: '원문')
    with pytest.raises(ValueError, match='ancestor_requires'):
        revise_batch([root, child], {'child': p}, lambda *args: '원문')
    child['data']['program'] = '사업B'
    with pytest.raises(ValueError, match='cross_program'):
        revise_batch([root, child], proposals, lambda *args: '원문')


def test_joint_revision_fails_closed_for_cycles_and_changed_child_source():
    root, child = sample(), sample()
    child['data']['sample_id'] = 'child'
    child['data']['_record']['parent'] = 's'
    p = {'question': '사업A 목적은?', 'answer': '새 답', 'rationale': '근거'}
    proposals = {'s': p, 'child': p}
    child['data']['_record']['source_texts'] = ['이전 본문']
    with pytest.raises(ValueError, match='source_changed'):
        revise_batch([root, child], proposals, lambda *args: '원문')
    assert root['data']['answer'] == '원문'  # A failed batch has no mutation.
    root['data']['_record']['parent'] = 'child'
    with pytest.raises(ValueError, match='cyclic_parent'):
        revise_batch([root, child], proposals, lambda *args: '원문')
