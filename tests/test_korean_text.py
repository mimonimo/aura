"""화면 문구 다듬기 — 조사·자르기. 실제 화면에서 본 적 없는 낱말로도 받침 규칙이 맞는지 본다."""
from zzaimy.app.korean_text import attach_particles, clip, josa, quote


def test_josa_by_final_syllable():
    assert josa("사업계획서", "을") == "를"
    assert josa("공통 양식", "를") == "을"
    assert josa("교육과정 편성표", "은") == "는"
    assert josa("재단", "와") == "과"
    assert josa("바다", "과") == "와"
    # ㄹ 받침은 '로', 그 밖의 받침은 '으로'
    assert josa("서울", "으로") == "로"
    assert josa("한글 파일", "으로") == "로"
    assert josa("작업본", "로") == "으로"
    assert josa("보고서", "으로") == "로"


def test_josa_digits_latin_and_unknown():
    assert josa("2025", "을") == "를"        # 오
    assert josa("제3", "을") == "을"         # 삼
    assert josa("ver7", "으로") == "로"      # 칠(ㄹ)
    assert josa("HTML", "을") == "을"        # 엘
    assert josa("PDF", "을") == "를"
    assert josa("「안내」", "을") == "를"    # 닫는 괄호는 건너뛰고 마지막 글자 '내'로 본다
    assert josa("계획(안)", "을") == "을"
    assert josa("★", "을") == "을(를)"       # 판단할 수 없으면 둘 다
    assert josa("문서", "에서") == "에서"    # 받침과 무관한 조사는 그대로


def test_quote_attaches_without_space():
    assert quote("사업계획서", "을") == "「사업계획서」를"
    assert quote("사업계획서 공통 양식", "을") == "「사업계획서 공통 양식」을"
    assert quote("제목") == "「제목」"


def test_attach_particles_fixes_spaced_and_wrong_forms():
    assert attach_particles("「…사업계획서」 을 만들어") == "「…사업계획서」를 만들어"
    assert attach_particles("「사업계획서 공통 양식」 을 깔아 두었습니다.") == "「사업계획서 공통 양식」을 깔아 두었습니다."
    assert attach_particles("프로젝트 「신규 사업단」 로 옮기기") == "프로젝트 「신규 사업단」으로 옮기기"
    assert attach_particles("「원본」 의 복제본 「작업본」 을 만들었습니다") == "「원본」의 복제본 「작업본」을 만들었습니다"
    # 관형사 '이'·낱말 속 글자는 건드리지 않는다
    assert attach_particles("「안내」 이 문서는") == "「안내」 이 문서는"
    assert attach_particles("「안내」 은행") == "「안내」 은행"
    assert attach_particles("조사 없는 글") == "조사 없는 글"


def test_clip_keeps_words_and_sentences():
    long = "학과명과 교육과정 편성표의 학과명이 일치해야 합니다. 다른 값은 담당자가 확인합니다."
    out = clip(long, 40)
    assert out == "학과명과 교육과정 편성표의 학과명이 일치해야 합니다."
    out = clip("가나다라 마바사아 자차카타 파하가나 다라마바 사아자차", 20)
    assert out.endswith("…") and len(out) <= 20
    assert not out[:-1].endswith(" ")
    assert all(w in "가나다라 마바사아 자차카타 파하가나 다라마바 사아자차".split() for w in out[:-1].split())
    assert clip("짧은 글", 20) == "짧은 글"
    assert clip("  공백이   여러   칸  ", 20) == "공백이 여러 칸"
    # 경계가 없는 아주 긴 낱말만 글자 수로 자른다
    assert clip("가" * 50, 10) == "가" * 9 + "…"
    # 목록 번호 '1.' 은 문장 끝으로 보지 않는다
    assert clip("단계는 다음과 같다 1. 준비 2. 실행 3. 점검 그리고 마무리 보고", 24).endswith("…")
