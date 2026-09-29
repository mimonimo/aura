from copy import deepcopy

from zzaimy.dataset.quality_gate import audit_dataset, audit_pair, REVIEW_CHECKS


def sample():
    return {'conversations': [{'from': 'human', 'value': '근거: 참여자는 9명이다. 인원은?'},
                              {'from': 'gpt', 'value': '참여자는 9명이다.'}],
            'meta': {'program_id': 'sample-a', 'node_path': ['overview', 'participants'],
                     'evidence_records': [{'doc_id': 'doc-a', 'chunk_id': 'c1', 'text': '참여자는 9명이다.',
                                           'program_id': 'sample-a', 'turn': 0}],
                     'review': {'decision': 'accept', 'reviewer': 'fixture-reviewer',
                                'checks': dict.fromkeys(REVIEW_CHECKS, True)}}}


def test_valid_candidate_and_no_mutation():
    p = sample()
    before = deepcopy(p)
    assert audit_pair(p) == []
    assert p == before


def test_unreviewed_is_not_sft_ready():
    p = sample()
    del p['meta']['review']
    assert 'review_incomplete' in audit_pair(p)


def test_small_numbers_checked():
    for n in (3, 27):
        p = sample()
        p['conversations'][1]['value'] = f'참여자는 {n}명이다.'
        assert 'unsupported_number' in audit_pair(p)


def test_other_program_and_invisible_evidence():
    p = sample()
    p['meta']['evidence_records'][0]['program_id'] = 'sample-b'
    assert 'cross_program_evidence' in audit_pair(p)
    p = sample()
    p['conversations'][0]['value'] = '인원은?'
    assert 'evidence_not_visible' in audit_pair(p)


def test_future_turn_does_not_ground_earlier_answer():
    p = sample()
    p['conversations'] += deepcopy(p['conversations'])
    p['meta']['evidence_records'][0]['turn'] = 2
    assert 'answer_without_evidence' in audit_pair(p)


def test_program_holdout_and_duplicate():
    a, b = sample(), sample()
    a['meta']['split'], b['meta']['split'] = 'train', 'test'
    report = audit_dataset([a, b])
    assert report['eligible'] == 0
    assert report['issues']['program_split_leakage'] == 2
    assert report['issues']['duplicate_conversation'] == 1


def test_legacy_pairs_are_held_not_guessed():
    p = sample()
    p['meta'] = {'program': '표시용 사업명', 'section': '1.1 개요'}
    assert {'missing_program_id', 'missing_node_path', 'missing_evidence_records', 'review_incomplete'} <= set(audit_pair(p))
