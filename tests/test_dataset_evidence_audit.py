"""C-134 독립 품질 계약(아스트라 작성). 합성 자료만 사용. K-74 에서 생성기를 고쳐 네 계약이 통과한다 — xfail 표시는 그때 뗐다."""
from zzaimy.dataset import real_pairs as rp
from zzaimy.dataset import tree_cot as tc


def test_single_digit_fact_requires_evidence():
    rec = {'human': '근거: 교육 프로그램을 운영한다.', 'gpt': '[답] 참여자는 9명이다.', 'step': 1}
    assert '9' in tc.missing_numbers(rec)


def test_two_digit_fact_is_not_a_section_number():
    rec = {'human': '근거: 교육 프로그램을 운영한다.', 'gpt': '[답] 참여자는 27명이다.', 'step': 3}
    assert '27' in tc.missing_numbers(rec)


def test_nonnumeric_fact_is_available_in_input():
    sec = rp.FormSection(1, '1. 운영 계획', 1, instructions='실제 운영 내용을 적는다.')
    fact = '산학협력처는 새싹멘토링을 운영한다.'
    pair = rp.build_section_pair(sec, [sec], [('text', fact)], [])
    assert fact in pair['human']


def test_required_item_has_visible_evidence():
    required = '야간 실습실 안전 담당 체계를 작성한다'
    node = tc.Node('1.1 운영', 2, part='Ⅰ. 계획', instructions='【작성방법】 1) ' + required)
    rec = tc.step3('합성 사업', '사업 개요', [], node, ['운영 계획을 평가한다.'])
    assert required in rec['human']
