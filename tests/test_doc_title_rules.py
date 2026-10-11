"""문서 이름 규칙(app/doc_naming) — 파일 이름 군더더기, 사람 이름, 본문 제목 검사, 여러 줄 제목, 대체 이름.

모든 예는 합성이다(사람 이름은 지어낸 것). 규칙마다 처음 보는 꼴 하나를 처리하고, 비슷하지만 건드리면 안 되는
꼴 하나를 그대로 두는지 함께 본다 — 예시에 맞춘 규칙이 아니라 형식에 대한 규칙인지 확인하려는 것이다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from zzaimy.app.doc_naming import (USER_SOURCE, body_title, choose_name, clean_filename, drop_person, meta_name,
                                   poor_name, title_problem)

# 본문 증거 — 파일 이름의 사람 이름은 마스킹된 본문에 나오지 않는다
BODY = ("본 과제는 지역 산업과 연계한 인재 양성을 목표로 한다. 추진 체계와 성과 지표를 아래와 같이 정한다.\n" * 6)


# ── 앞 군더더기: 번호·개요 번호·목록 기호·짧은 낱말+번호·상태 표시 ──────────────────────
@pytest.mark.parametrize("raw, want", [
    ("05_가나대학교 1-2 과제계획서(주관).hwp", "가나대학교 1-2 과제계획서(주관)"),
    ("4_가나대학교 1-2 과제계획서(주관)_1118.hwp", "가나대학교 1-2 과제계획서(주관)"),
    ("3.4.2. 성과관리 체계 개선안.hwp", "성과관리 체계 개선안"),
    ("1-30)2013년 기술인력 양성사업 세부운영지침(최종).hwp", "2013년 기술인력 양성사업 세부운영지침"),
    ("p)학점인정에 관한 내규.hwp", "학점인정에 관한 내규"),
    ("발표3. 혁신지원사업LINC3.0 추진 성과.pptx", "혁신지원사업LINC3.0 추진 성과"),
    ("최종! 가나대학교 적합성 검토 결과보고서 - 제출본(190424).hwp", "가나대학교 적합성 검토 결과보고서"),
    # 처음 보는 꼴 — 같은 형식의 다른 표기
    ("[2-7]산학협력 운영 현황.xlsx", "산학협력 운영 현황"),
    ("12.5. 예산 집행 기준.pdf", "예산 집행 기준"),
    ("나) 현장실습 운영 안내.hwp", "현장실습 운영 안내"),
    ("안건2) 교원 연구년 심의.hwp", "교원 연구년 심의"),
    ("[최종]_교육과정 개편 계획.hwp", "교육과정 개편 계획"),
    ("241106_2024년 단계평가 추진계획.hwpx", "2024년 단계평가 추진계획"),
    ("(공고문) 2026년 가나 지원사업 공고.hwpx", "2026년 가나 지원사업 공고"),
    ("[가나부 03-10(금) 보도자료] 2023년 가나사업 기본계획 발표.hwp", "2023년 가나사업 기본계획 발표"),
    ("2025 가나대학 지정계획(결재본).pdf", "2025 가나대학 지정계획"),
])
def test_leading_and_trailing_file_noise_is_removed(raw, want):
    assert clean_filename(raw) == want


@pytest.mark.parametrize("raw", [
    "2025년 인공지능 분야 사업계획서.hwp",          # 연도로 시작하는 제목
    "3단계 산학연협력 선도대학 육성사업 기본계획.pdf",   # 번호 낱말로 시작하는 제목
    "2025.10.08 교무위원회 회의록.hwp",             # 날짜로 시작하는 제목 — 개요 번호가 아니다
    "LINC3.0 사업 성과보고서.pdf",                  # 판 번호가 붙은 사업 이름
    "4차 산업혁명 대응 교육과정.hwp",
    "2-3 과제 운영계획.hwp",                         # 과제 번호(괄호·밑줄 없음)
    "최종 결과보고서.hwp",                          # 띄어쓰기만 있는 '최종' 은 제목의 낱말이다
    "적합성 검토.hwp",                              # 띄어쓰기 뒤 '검토' 는 상태 표시가 아니다
    "S.W. 교육 운영 계획.pdf",                       # 라틴 약어
    "(주)가나기술 협약서.hwp",                        # 법인 표시는 이름의 일부다
    "(안) 교원 연구년 운영규정.hwp",
    "2025 운영 계획.hwp",                            # 앞의 네 자리는 연도
])
def test_titles_that_start_with_numbers_or_end_with_status_words_stay(raw):
    assert clean_filename(raw) == raw.rsplit(".", 1)[0]


@pytest.mark.parametrize("raw, want", [
    ("가나 사업 계획서_ver.1.hwp", "가나 사업 계획서"),
    ("가나 사업 계획서_v2.hwp", "가나 사업 계획서"),
    ("가나 사업 계획서_0123_OK.hwp", "가나 사업 계획서"),
    ("평가 템플릿_2022.1123 - 복사본.pptx", "평가 템플릿"),
    ("운영 지침 (1).pdf", "운영 지침"),
    ("연차 보고서(검토완료).hwp", "연차 보고서"),
    ("연차 보고서_수정본.hwp", "연차 보고서"),
    ("연차 보고서 final.docx", "연차 보고서"),
    ("가신청서 양식_1.hwpx", "가신청서 양식"),
    ("사업 추진계획_F.pdf", "사업 추진계획"),
    ("정오표 (Ver.2024.04.11.).hwp", "정오표"),
])
def test_trailing_version_date_and_status_tails_are_removed(raw, want):
    assert clean_filename(raw) == want


@pytest.mark.parametrize("raw", [
    "사업계획서_2025.hwp",                # 연도 하나는 정보다
    "운영규정(2024).pdf",
    "공고 제2025-12호.pdf",
    "울산 RISE 사업계획서 서식1-11.pdf",      # 서식 번호
    "사업계획서 서식(자율협약형 1유형).hwp",
    "교육과정 운영지침(2).hwp",            # 띄어쓰기 없는 괄호 번호는 판 구분일 수 있다
    "가나부_혁신지원사업_기본계획_2025-2027.hwpx",   # 연도 범위
    "중기 발전계획(2019~2021).hwp",
    "국립대학 육성사업 기본계획(수정안).hwpx",  # '-안' 은 따로 공표되는 판이다
])
def test_years_and_document_numbers_in_the_tail_stay(raw):
    assert clean_filename(raw) == raw.rsplit(".", 1)[0].replace("_", " ")


def test_underscores_and_extensions_become_plain_names():
    assert clean_filename("장학금_지급_계획.hwpx") == "장학금 지급 계획"
    assert clean_filename("보고서.pdf.pdf") == "보고서"
    assert clean_filename("재정지원사업+매뉴얼(최종).pdf") == "재정지원사업 매뉴얼"


# ── 사람 이름: 개인정보 탐지기의 성명 모양 + 증거(직위·'수정' 표시, 또는 본문에 없는 낱말) ──────────
@pytest.mark.parametrize("raw, want", [
    ("2번(홍길동교수).pptx", "2번"),
    ("종합평가 템플릿 링크 최종(김가온 수정).pptx", "종합평가 템플릿 링크"),
    ("혁신지원사업 추진 성과 사업단장 박도윤.pptx", "혁신지원사업 추진 성과"),
    # 처음 보는 꼴
    ("연구년 계획서(이하람 작성).hwp", "연구년 계획서"),
    ("교원 연수 결과 최서진 교수.hwp", "교원 연수 결과"),
])
def test_person_name_with_a_role_marker_is_dropped_without_body(raw, want):
    assert clean_filename(raw) == want


def test_bare_person_name_needs_body_evidence():
    raw = "RISE_단위과제(1-2) 과제계획서(주관)_정민우_ver.1.hwp"
    # 본문이 없으면 맨 이름은 떼지 않는다(증거 부족)
    assert clean_filename(raw) == "RISE 단위과제(1-2) 과제계획서(주관) 정민우"
    # 본문에 그 낱말이 없으면 사람 이름으로 보고 뗀다
    assert clean_filename(raw, BODY) == "RISE 단위과제(1-2) 과제계획서(주관)"
    assert clean_filename("60윤하늘.pdf", BODY) == ""
    assert clean_filename("[4-4]한바다.pdf", BODY) == ""


@pytest.mark.parametrize("raw", [
    "2024 장학금 지급계획_장학금.hwp",            # 성씨 글자로 시작하는 보통 낱말(본문에 나온다)
    "업무분장_신청서.hwp",                       # 서류 갈래 꼬리
    "운영 계획_정보화.hwp",                       # '-화' 로 끝나는 낱말
    "교무 회의 자료 기획 처장.hwp",               # 두 글자 낱말 + 직위 — 이름이 아니다
])
def test_ordinary_words_that_look_like_names_stay(raw):
    body = BODY + " 장학금 신청서 정보화 기획 "
    assert clean_filename(raw, body) == raw.rsplit(".", 1)[0].replace("_", " ")


def test_drop_person_only_trusts_the_pii_name_shape():
    # 성씨로 시작하지 않는 세 글자는 탐지기가 이름으로 보지 않는다
    assert drop_person("운영 계획 뷁뷁뷁", BODY) == "운영 계획 뷁뷁뷁"


# ── 본문 제목 검사: 연락처·날짜·쪽 표시·작성 안내·목차·되풀이 머리글 ─────────────────────
@pytest.mark.parametrize("line", [
    "산학협력팀 /담당 홍길동 /이메일abc@example.ac.kr /전화 053-000-0000/FAX",
    "문의처: 교무팀 [KR_PHONE]",
    "20251216110841",
    "2017. 8. 10.",
    "2025년 10월 8일 (수)",
    "- 3 -",
    "※ 작성방법: 항목별로 빠짐없이 기재",
    "< 작성요령 >",
    "목 차",
    "Contents",
])
def test_non_title_lines_are_rejected(line):
    assert title_problem(line) is not None


@pytest.mark.parametrize("line", [
    "2025년 인공지능(AI) 분야 첨단산업 인재양성 부트캠프 사업계획서",
    "3단계 산학연협력 선도대학 육성사업 기본계획",
    "교원 연구년 운영 지침",
    "전화 응대 매뉴얼",                          # '전화' 가 들어간 제목 — 연락처 꼴이 아니다
])
def test_real_titles_pass_the_checks(line):
    assert title_problem(line) is None


def test_running_header_is_not_a_title():
    text = ("2025 RISE 사업 추진 실적\n본문 문장이 이렇게 이어진다.\n" * 5)
    assert body_title(text)[0] is None


def test_contact_line_is_skipped_and_the_real_title_is_used():
    text = "산학협력팀 /담당 홍길동 /이메일abc@example.ac.kr\n2017학년도 산학협력 운영 계획\n1. 목적"
    assert body_title(text)[0] == "2017학년도 산학협력 운영 계획"


def test_guide_box_and_timestamp_are_skipped():
    assert body_title("20251216110841\n2025. 12. 16.\n학생 상담 결과 보고서")[0] == "학생 상담 결과 보고서"
    assert body_title("※ 작성방법: 각 항목 기재\n< 작성요령 >\n지역혁신 중심 대학지원체계 과제계획서")[0] \
        == "지역혁신 중심 대학지원체계 과제계획서"


# ── 여러 줄 제목: 서류 갈래로 끝날 때까지 다음 줄(최대 두 줄)을 잇는다 ──────────────────────
def test_cover_title_split_over_two_lines_is_joined():
    text = "2025년 인공지능(AI) 분야\n첨단산업 인재양성 부트캠프 사업계획서\n2025. 3.\n가나대학교"
    assert body_title(text)[0] == "2025년 인공지능(AI) 분야 첨단산업 인재양성 부트캠프 사업계획서"


def test_cover_title_split_over_three_lines_is_joined():
    text = "2026년도 지역 정주형\n디지털 전환 선도 인재\n양성 사업 결과보고서\n1. 개요"
    assert body_title(text)[0] == "2026년도 지역 정주형 디지털 전환 선도 인재 양성 사업 결과보고서"


def test_complete_one_line_title_is_not_joined_with_the_next_line():
    text = "교원 연구년 운영 지침\n학과 교수 회의 자료 정리\n1. 목적"
    assert body_title(text)[0] == "교원 연구년 운영 지침"


# ── 고르는 순서: 본문 제목 → 걷어 낸 파일 이름 → 「갈래 · 사업 연도」 → 걷어 낸 파일 이름 ────────
def test_body_title_beats_a_noisy_filename():
    got = choose_name("05_가나대학교 1-2 과제계획서(주관).hwp",
                      "2026년 RISE 단위과제(1-2)\n지역 정주형 인재 양성 과제계획서\n(주관기관)")
    assert got["source"] == "body"
    assert got["name"] == "2026년 RISE 단위과제(1-2) 지역 정주형 인재 양성 과제계획서"


def test_partial_body_title_never_beats_a_fuller_filename():
    got = choose_name("대학재정지원사업_공동 운영관리 매뉴얼.pdf", "공동 운영관리 매뉴얼\n1. 개요")
    assert got["source"] == "file" and got["name"] == "대학재정지원사업 공동 운영관리 매뉴얼"


def test_timestamp_filename_takes_the_body_title():
    got = choose_name("20251216110841.pdf", "20251216110841\n학생 상담 결과 보고서\n상담 일시")
    assert got["name"] == "학생 상담 결과 보고서"


def test_both_poor_falls_back_to_kind_program_year():
    meta = {"kind_label": "결과보고서", "program": "가나 지원사업", "year": "2025"}
    got = choose_name("2번(홍길동교수).pptx", "목 차\n1. 개요", meta)
    assert got == {"name": "결과보고서 · 가나 지원사업 2025", "title": None, "date": None, "source": "meta"}
    assert meta_name({"kind_label": "회의 자료"}) == "회의 자료"
    assert meta_name({}) == ""


def test_nothing_available_keeps_the_cleaned_filename():
    got = choose_name("2번(홍길동교수).pptx", "", None)
    assert got["name"] == "2번" and got["source"] == "file"


def test_poor_name_examples():
    assert poor_name("") and poor_name("2번") and poor_name("20251216110841") and poor_name("2017. 8. 10.")
    assert not poor_name("성과관리 체계") and not poor_name("LINC report")


# ── 반입 경로: 같은 함수(db.rename_from_text)로 이름을 정하고 원래 파일 이름·접수번호를 지킨다 ─────────
def test_intake_renames_and_keeps_original_filename_and_receipt(tmp_path):
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    raw = "4_가나대학교 1-2 과제계획서(주관)_1118.hwp"
    d = db.add_document(filename=raw, stored_path="dgx://사업/2025/" + raw, doc_type="grant")
    receipt = db.get_document(d)["receipt_no"]
    db.rename_from_text(d, "목 차\n1. 개요\n" + BODY)
    doc = db.get_document(d)
    ident = db.get_doc_identity(d)
    assert doc["filename"] == "가나대학교 1-2 과제계획서(주관)"
    assert ident["original_filename"] == raw
    assert ident["name_source"] == "file"
    assert doc["receipt_no"] == receipt
    assert doc["stored_path"].endswith(raw)


def test_intake_uses_body_title_when_the_cover_has_one(tmp_path):
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    d = db.add_document(filename="60윤하늘.pdf", stored_path="", doc_type="grant")
    db.rename_from_text(d, "2025년 인공지능(AI) 분야\n첨단산업 인재양성 부트캠프 사업계획서\n" + BODY)
    assert db.get_document(d)["filename"] == "2025년 인공지능(AI) 분야 첨단산업 인재양성 부트캠프 사업계획서"
    assert db.get_doc_identity(d)["name_source"] == "body"


def test_user_named_document_is_not_renamed(tmp_path):
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    d = db.add_document(filename="담당자가 붙인 이름", stored_path="", doc_type="grant")
    db.set_doc_identity(d, {"original_filename": "05_원래.hwp", "name_source": USER_SOURCE})
    db.rename_from_text(d, "2025년 다른 사업 계획서\n" + BODY, overwrite=True)
    assert db.get_document(d)["filename"] == "담당자가 붙인 이름"


def test_display_name_follows_the_decided_name():
    from zzaimy.app.doc_title import display_name

    doc = {"filename": "가나대학교 1-2 과제계획서(주관)", "stored_path": "", "masked_text": "교원 연구년 운영 지침\n",
           "identity": json.dumps({"original_filename": "05_가나대학교 1-2 과제계획서(주관).hwp", "name_source": "file"})}
    assert display_name(doc) == "가나대학교 1-2 과제계획서(주관)"



# ── 반입 지점이 새로 생겨도 이름 규칙을 거치는지 ──────────────────────────────────────────
def test_every_intake_path_names_documents_through_the_shared_rule():
    """add_document 로 문서를 만드는 모든 코드는 같은 파일에서 파이프라인 처리(process) 또는 rename_from_text 를 부른다.
    새 반입 지점이 이 규칙을 건너뛰면 여기서 걸린다."""
    root = Path(__file__).resolve().parent.parent
    missing = []
    for p in list((root / "src").rglob("*.py")) + list((root / "scripts").glob("*.py")):
        src = p.read_text(encoding="utf-8", errors="replace")
        if ".add_document(" not in src or p.name == "db.py":
            continue
        if not any(k in src for k in ("rename_from_text", ".process(", "_run_one")):
            missing.append(str(p.relative_to(root)))
    assert missing == []


def test_parenthesised_subtitle_and_repeated_title_are_not_joined():
    assert body_title("교원 연구년 운영 절차\n( 2026학년도 2학기 )\n교무팀")[0] == "교원 연구년 운영 절차"
    assert body_title("## 교원 연구년 운영 절차\n**교원 연구년 운영 절차** (요약본)\n교무팀")[0] == "교원 연구년 운영 절차"


def test_body_title_is_polished():
    from zzaimy.app.doc_naming import polish_title

    assert polish_title("가나부 공고 제2026–126호 「가나 융합인재양성사업」 컨소시엄 선정 공고") == "가나 융합인재양성사업 컨소시엄 선정 공고"
    assert polish_title("사업계획서 표지 『가나 혁신 사업』 사업계획서") == "가나 혁신 사업 사업계획서"
    assert polish_title("\U000f02b1 부정·비리 사항 확인서") == "부정·비리 사항 확인서"
    assert polish_title("가나대학 기본계획(수정)") == "가나대학 기본계획"
    # 그대로 두는 것
    assert polish_title("2026학년도 공고 운영 지침") == "2026학년도 공고 운영 지침"


def test_doubled_text_layer_and_cover_label_lines_are_rejected():
    assert title_problem("2024 가나사업 협의회2024 가나사업 협의회 모집 공고모집 공고") == "글자층 겹침"
    assert title_problem("사업계획서 표지") == "표지 표시"
    assert title_problem("가나 혁신 사업 사업계획서") is None


def test_kind_word_overlap_alone_does_not_replace_a_good_filename():
    # 서식 여러 개가 묶인 파일 — 첫 서식 이름은 파일 전체의 이름이 아니다
    got = choose_name("가나사업 신청을 위한 확인서 및 확약서 양식.hwp", "부정·비리 사항 확인서\n1. 대학명")
    assert got["source"] == "file"
    # 고유한 낱말(기관 이름)이 겹치면 본문 제목이 더 온전한 이름이다
    got = choose_name("1. 가나대학교.pdf", "가나대학교 혁신기획서\n목차")
    assert got["name"] == "가나대학교 혁신기획서"


def test_same_name_suffix_skips_guide_lines_and_sentences(tmp_path):
    """같은 이름의 문서를 가르는 말은 부제·항목 값이어야 한다 — 작성 안내·본문 문장·자리 표시는 쓰지 않고 접수번호로 간다."""
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    names = []
    for extra in ("* 적요에는 본문의 작성요령에 제시된 유형을 기재", "전공 불일치 문제 등 현장 수요 변화에 비해 혁신 속도는 미흡",
                  "OO대학교 OO학과 (인문계열)"):
        d = db.add_document(filename="가나사업 신청서 양식.hwp", stored_path="", doc_type="grant")
        db.rename_from_text(d, f"가나사업 신청서 양식\n{extra}\n" + BODY)
        names.append(db.get_document(d)["filename"])
    assert names[0] == "가나사업 신청서 양식"
    for n in names[1:]:
        assert re.fullmatch(r"가나사업 신청서 양식 · \d{4}-국고-\d{4}", n), n
    # 부제·항목 줄('학과(계열) 기계계열')은 여전히 쓴다
    d = db.add_document(filename="가나사업 신청서 양식.hwp", stored_path="", doc_type="grant")
    db.rename_from_text(d, "가나사업 신청서 양식\n학과(계열) 스마트기계계열\n" + BODY)
    assert db.get_document(d)["filename"] == "가나사업 신청서 양식 · 학과(계열) 스마트기계계열"
