"""글자 분해 복원 — OCR 이 한 칸씩 갈라 놓은 낱말을 붙인다.

글자를 새로 만들지 않고 잘못 끼어든 빈칸만 지운다. 멀쩡한 띄어쓰기는 건드리지 않는다.
경계값 근거는 운영 자료 4,977조각 실측이다(2026-09-19).
"""

from zzaimy.app import text_repair as tr


def test_joins_long_broken_word():
    out = tr.repair("영 남 이 공 대 학 교 총 장 이 이를 정한다.")
    assert out == "영남이공대학교총장이 이를 정한다."


def test_joins_three_characters_without_vocabulary():
    assert tr.repair("사 업 명: 학자금 지원") == "사업명: 학자금 지원"


def test_keeps_correct_spacing_of_dependent_nouns():
    # "할 수"·"둘 수"는 올바른 띄어쓰기다. 손상 신호가 없으면 건드리지 않는다.
    text = "학생은 휴학할 수 있다. 민원은 둘 수 있다."
    assert tr.repair(text) == text


def test_keeps_enumeration_markers():
    text = "지원 대상은 가, 나 중 어느 하나에 해당하는 사람"
    assert tr.repair(text) == text


def test_two_character_join_needs_damage_and_vocabulary():
    clean = "통영시에 거주하는 학생. 통영시에 사는 사람."
    damaged = "통 영 시 장 이 공고한다. 통 영 시에 사는 학생."
    fixed, stats = tr.repair_all([clean, damaged])
    assert fixed[0] == clean                      # 멀쩡한 글은 그대로 둔다
    assert "통영시장" in fixed[1]
    assert "통영시에 사는" in fixed[1]            # 뒤 낱말까지 합쳐 알아본다
    assert stats["changed"] == 1


def test_vocabulary_ignores_damaged_text():
    # 손상된 글에서 어휘를 모으면 "할수" 같은 것이 섞여 멀쩡한 글을 망친다
    damaged = "할 수 있 다 고 본 다"
    vocab = tr.build_vocab([damaged, "학생은 휴학할 수 있다."])
    assert "할수" not in vocab


def test_has_damage_needs_three_in_a_row():
    assert tr.has_damage("통 영 시 장 이 공고한다") is True
    assert tr.has_damage("학생 및 교직원 중 해당자") is False


def test_repair_document_uses_clean_lines_as_evidence():
    text = ("제1조(목적) 이 규정은 학사 운영에 관한 사항을 정한다.\n"
            "영 남 이 공 대 학 교 총 장 이 이를 시행한다.\n"
            "학생은 휴학할 수 있다.")
    out, stats = tr.repair_document(text)
    assert "영남이공대학교총장이" in out
    assert "휴학할 수 있다" in out                 # 올바른 띄어쓰기는 유지
    assert stats["joined"] == 1


def test_repair_leaves_clean_text_untouched():
    text = "제19조(휴학) 학생은 질병으로 수학할 수 없을 때 휴학할 수 있다."
    assert tr.repair(text) == text
    assert tr.repair_document(text)[0] == text


def test_empty_input_is_safe():
    assert tr.repair("") == ""
    assert tr.repair_document("")[0] == ""
