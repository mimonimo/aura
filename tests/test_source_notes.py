"""근거 성격·적용 대상 지시(responder.source_notes) — 교내 규정을 물었는데 사업 지침뿐이거나, 근거 규정의 대상이 질문과 다를 때."""
from zzaimy.app.responder import audience_of, source_notes


def test_school_rule_question_with_only_grant_evidence_says_so():
    notes = source_notes("우리 대학 여비 규정에서 국내 출장 일비와 숙박비는 얼마야?", [], True)
    assert len(notes) == 1 and "교내 규정은 없습니다" in notes[0] and "어느 사업의 기준" in notes[0]


def test_grant_question_without_school_words_gets_no_note():
    assert source_notes("국고사업 예산으로 국내 출장을 가면 일비는 얼마까지 받을 수 있어?", [], True) == []


def test_audience_mismatch_only_other_audience_found():
    notes = source_notes("직원 육아휴직은 최대 몇 년까지 쓸 수 있어?",
                         ["영남이공대학교 산학협력단 직원 취업규칙 · 2020년"], False)
    assert len(notes) == 1 and "대학 직원에게 적용되는 규정은 찾은 자료에 없습니다" in notes[0] and "산학협력단 직원" in notes[0]


def test_audience_mixed_puts_asked_first():
    notes = source_notes("직원 육아휴직은 최대 몇 년?",
                         ["영남이공대학교 직원인사 규정", "영남이공대학교 산학협력단 직원 취업규칙"], False)
    assert len(notes) == 1 and "앞세워" in notes[0] and "산학협력단 직원의 경우" in notes[0]


def test_same_audience_no_note():
    assert source_notes("교수 정년은 몇 살이야?", ["영남이공대학교 교원인사 규정"], False) == []
    assert audience_of("학생은 휴학을 최대 몇 학기까지?") == "학생"
    assert audience_of("영남이공대학교 산학협력단 직원 취업규칙") == "산학협력단 직원"
