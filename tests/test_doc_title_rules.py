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
    got = choose_name("05_RISE 단위과제 과제계획서.hwp",
                      "2026년 RISE 단위과제(1-2)\n지역 정주형 인재 양성 과제계획서\n(주관기관)")
    assert got["source"] == "body"
    assert got["name"] == "2026년 RISE 단위과제(1-2) 지역 정주형 인재 양성 과제계획서"
    # 본문 제목이 파일 이름의 기관 이름을 잃으면 파일 이름을 둔다
    got = choose_name("05_가나대학교 1-2 과제계획서(주관).hwp",
                      "2026년 RISE 단위과제(1-2)\n지역 정주형 인재 양성 과제계획서\n(주관기관)")
    assert got == {"name": "가나대학교 1-2 과제계획서(주관)", "title": None, "date": None, "source": "file"}


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


# ── 운영 미리보기에서 드러난 회귀: 본문 제목이 구별 정보를 잃으면 안 된다 ─────────────────────────
def test_body_title_must_keep_region_and_kind_word():
    # 지역이 빠지고 갈래가 바뀐(기본계획 → 계획서) 본문 제목은 쓰지 않는다
    got = choose_name("11. 충북 지역혁신 대학지원체계(ABC) 기본계획.pdf", "가나 지역혁신 대학지원체계(ABC)\n계획서\n" + BODY)
    assert got["name"] == "충북 지역혁신 대학지원체계(ABC) 기본계획" and got["source"] == "file"
    # 처음 보는 꼴 — 지역의 정식 이름이 본문에 있으면 지역을 지킨 것이다
    got = choose_name("2. 전남 가나사업 기본계획.pdf", "전라남도 가나사업 추진 기본계획\n" + BODY)
    assert got["source"] == "body"


def test_body_title_must_keep_year_round_acronym_program_and_org():
    from zzaimy.app.doc_naming import lost_tokens

    assert "2017" in lost_tokens("1차 사회맞춤형 가나 육성사업 사업계획서", "2017년 사회맞춤형 가나사업 보조금사업계획서")
    assert "XYZ" in lost_tokens("선정평가 항목 및 지표정의서", "XYZ선정평가 지표정의서 및 평가편람")
    assert "3차" in lost_tokens("가나사업 수정사업계획서", "3차년도 가나사업 수정사업계획서")
    assert "가나이공대학교" in lost_tokens("부트캠프 1차 연도 사업계획서", "부트캠프 사업계획서 가나이공대학교")
    assert "다라마 지원사업" in lost_tokens("2026년 지원사업 계획서", "2026년 다라마 지원사업 계획서", programs=("다라마 지원사업",))
    # 지키면 비어 있다 — 띄어쓰기·점이 다른 약어('ABC3.0'·'ABC 3.0')와 '2차' 차수는 같은 것으로 본다
    assert lost_tokens("3단계 가나 육성사업(ABC 3.0) 2차년도 수정사업계획서", "2차년도 ABC3.0 수정사업계획서") == []


def test_same_body_title_with_different_filenames_falls_back_to_filenames(tmp_path):
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    body = "1차 사회맞춤형 가나사업 육성 사업계획서\n" + BODY
    a = db.add_document(filename="가나사업 사업계획서 (갑).hwp", stored_path="", doc_type="grant")
    b = db.add_document(filename="가나사업 사업계획서 (을).hwp", stored_path="", doc_type="grant")
    db.rename_from_text(a, body)
    db.rename_from_text(b, body)
    assert db.get_document(a)["filename"] == "1차 사회맞춤형 가나사업 육성 사업계획서"
    assert db.get_document(b)["filename"] == "가나사업 사업계획서 (을)"      # 겹치는 대신 제 파일 이름
    # 같은 파일 이름(사본)끼리는 겹침이 아니다 — 구분 말이 붙는다
    c = db.add_document(filename="가나사업 사업계획서 (갑).hwp", stored_path="", doc_type="grant")
    db.rename_from_text(c, body)
    assert db.get_document(c)["filename"].startswith("1차 사회맞춤형 가나사업 육성 사업계획서 · ")


def test_slogan_and_decorated_cover_lines_are_rejected():
    assert title_problem("ㅣ - 지역과 대학의 동반성장을 위한 새로운 길 - 가나시 -ABC 5개년") is not None
    assert title_problem("- 함께 여는 내일 -") is not None
    assert title_problem("· 가나사업 기본계획") is not None
    # 그대로 두는 것 — 붙임표가 낀 보통 제목, 따옴표로 시작하는 제목
    assert title_problem("2025-2027 가나사업 기본계획") is None
    assert title_problem("'26년 2학기 가나 장학금 신청 매뉴얼") is None


def test_table_header_word_at_line_start_is_dropped():
    from zzaimy.app.doc_naming import strip_header

    assert body_title("구분 전문대 첨단산업 인재양성 가나 1차 연도 사업계획서\n" + BODY)[0] \
        == "전문대 첨단산업 인재양성 가나 1차 연도 사업계획서"
    assert strip_header("항목: 가나사업 운영 계획") == "가나사업 운영 계획"
    # 머리 낱말로 시작하지 않는 제목은 그대로, 머리 낱말 하나뿐인 줄도 그대로
    assert strip_header("구분관리 운영 지침") == "구분관리 운영 지침"
    assert strip_header("구분") == "구분"


@pytest.mark.parametrize("raw, want", [
    ("2017년 가나사업 보조금사업계획서_170922_직인X.hwp", "2017년 가나사업 보조금사업계획서"),
    ("2018년 가나사업 보조금사업계획서_180808_직인없음.hwp", "2018년 가나사업 보조금사업계획서"),
    ("2018년 가나사업 보조금사업계획서_발송0808.hwp", "2018년 가나사업 보조금사업계획서"),
    ("가나학과 중점형 사업수행계획서(다라대학교)_최종(직임미포함).hwp", "가나학과 중점형 사업수행계획서(다라대학교)"),
    ("가나학과 중점형사업계획서_최종본03300921.hwp", "가나학과 중점형사업계획서"),
    ("2026-1학기 개설과목 관리(01.27).xlsx", "2026-1학기 개설과목 관리"),
    ("가나사업 계획서170922.hwp", "가나사업 계획서"),
    # 처음 보는 꼴
    ("가나사업 협약서_날인본.pdf", "가나사업 협약서"),
    ("가나사업 공문_송부1015.hwp", "가나사업 공문"),
])
def test_dispatch_and_seal_status_tails_are_removed(raw, want):
    assert clean_filename(raw) == want


@pytest.mark.parametrize("raw", [
    "가나사업 계획서201799.hwp",           # 날짜가 될 수 없는 여섯 자리(99일)가 붙은 것은 남는다
    "직인 관리 규정.hwp",                   # 상태 낱말이 이름의 머리에 있으면 이름이다
    "발송 업무 매뉴얼.hwp",
])
def test_status_like_words_inside_names_stay(raw):
    assert clean_filename(raw) == raw.rsplit(".", 1)[0]


@pytest.mark.parametrize("raw, want", [
    ("1.1차년도 ABC3.0 수정사업계획서.hwp", "1차년도 ABC3.0 수정사업계획서"),
    ("3. 2차년도 가나사업 실적보고서.hwp", "2차년도 가나사업 실적보고서"),
])
def test_list_number_before_round_is_removed(raw, want):
    assert clean_filename(raw) == want


def test_list_number_alone_keeps_outline_meaning():
    assert clean_filename("1.1 가나사업 개요.hwp") == "1.1 가나사업 개요"     # 차년도가 아니면 '1.1' 은 건드리지 않는다


def test_cover_slogan_that_keeps_only_the_region_does_not_replace_the_filename():
    got = choose_name("4. 인천 지역혁신 가나체계(ABC) 기본계획.pdf",
                      "ㅣ - 지역과 대학의 새로운 길 -\n인천광역시 -ABC 5개년(2025~2029) 기본계획\n" + BODY)
    assert got["name"] == "인천 지역혁신 가나체계(ABC) 기본계획"


def test_numbered_stage_line_is_a_title():
    assert body_title("3단계 가나 선도대학 육성사업(ABC 3.0) 2차년도 수정사업계획서\n" + BODY)[0] \
        == "3단계 가나 선도대학 육성사업(ABC 3.0) 2차년도 수정사업계획서"


# ── 두 번째 미리보기에서 드러난 것: 본문 제목 모양 관문, 구조로 찾는 사람 이름, 순번, 이은 줄의 되풀이 ──────
META = {"kind_label": "증빙·회계 서류", "program": "가나사업", "year": "2026"}


@pytest.mark.parametrize("line, why", [
    ("가나대학교산학협력단(홍길동)님", "호칭·수신 표시"),
    ("가나재단 귀하", "호칭·수신 표시"),
    ("문서번호 기획조정 2026-0001 기안 팀장 처장", "결재란"),
    ("생년월일/Date of birth 주민등록번호/Personal No", "항목 이름 나열"),
    ("성명 연락처 주소", "항목 이름 나열"),
    ("의3서식]", "괄호 조각"),
    ("ABC3.0 [] 가나 보고서", "괄호 조각"),
    ("“기대보다 더 큰 만족을 주는 대학", "따옴표로 여는 구호"),
    ("대주주 주식수(천 주) 지분율", "서류 낱말 없음"),
    ("타대학과의 교류", "서류 낱말 없음"),
])
def test_shape_gate_rejects_non_title_lines(line, why):
    from zzaimy.app.doc_naming import shape_problem

    assert shape_problem(line) == why


@pytest.mark.parametrize("line", [
    "가구원 정보제공 동의 절차",
    "'26년 2학기 가나장학금(Ⅰ·Ⅱ유형) 및 다자녀 가나장학금 신청 매뉴얼",
    "2025년-2027년 가나사업 기본계획",
    "개인정보 제공·활용 동의서",
    "2023학년도 가나계열 교육과정표 다라전공 (주간)",
    "주소 변경 신청서",                     # 항목 이름 하나는 서류 이름의 일부일 수 있다
])
def test_shape_gate_keeps_noun_phrase_titles(line):
    from zzaimy.app.doc_naming import shape_problem

    assert shape_problem(line) is None


@pytest.mark.parametrize("raw, body, want, source", [
    ("공문.pdf", "문서번호 기획조정 2026-0001 기안 팀장 처장\n", "증빙·회계 서류 · 가나사업 2026", "meta"),   # 파일 이름 빈약 → 대체 이름
    ("4.통장사본.pdf", "가나대학교산학협력단(홍길동)님\n", "통장사본", "file"),                         # '통장사본' 은 서류 이름
    ("여권사본.pdf", "생년월일/Date of birth 주민등록번호/Personal No\n", "여권사본", "file"),
    ("[별지 4의3] 가나수익명세서.hwp", "의3서식]\n", "가나수익명세서", "file"),
    ("[별첨] CQI보고서 및 가나평가서.hwp", "ABC3.0 [] CQI 보고서\n", "CQI보고서 및 가나평가서", "file"),
    ("가나건설.pdf", "대주주 주식수(천 주) 지분율\n", "가나건설", "file"),
    ("00-전단지.ppt", "“기대보다 더 큰 만족을 주는 대학\n", "전단지", "file"),
])
def test_failed_body_titles_fall_back(raw, body, want, source):
    got = choose_name(raw, body + BODY, META)
    assert (got["name"], got["source"]) == (want, source)


@pytest.mark.parametrize("raw, want", [
    ("건축과 교수학습지침서 결과보고서(배하늘).pdf", "건축과 교수학습지침서 결과보고서"),     # 끝 괄호 속 세 글자 이름
    ("58.조하린_교육과정표.pdf", "교육과정표"),                                          # 순번 + 이름_ + 서류 이름
    ("가나치료과-강하준.pdf", "가나치료과"),                                             # 소속-이름
    ("017 (가나자동화과_김하늘).hwp", "017 (가나자동화과)"),                              # 괄호 속 소속_이름
    ("AI 물류 기업(손하람).xlsx", "AI 물류 기업"),
])
def test_person_names_found_by_position_are_removed(raw, want):
    assert clean_filename(raw) == want


@pytest.mark.parametrize("raw", [
    "가나건설.pdf",                      # 첫 글자가 직위 꼬리('장')여도 띄어 쓰지 않았으면 직위가 아니다
    "한국어_교육과정표.hwp",              # 이름에 드문 끝 글자(어)
    "교육과정 운영(주간).hwp",            # 두 글자 괄호
    "기업소개 자료(가나기업).hwpx",        # 네 글자 괄호
    "졸업식(입학식).hwp",
    "통장사본.pdf",
])
def test_names_that_only_look_like_people_stay(raw):
    assert clean_filename(raw) == raw.rsplit(".", 1)[0].replace("_", " ")


def test_body_title_never_carries_a_person_name():
    got = choose_name("242Lk13.pdf", "가나계열 교육과정 운영계획서(홍길동)\n" + BODY)
    assert got["name"] == "가나계열 교육과정 운영계획서"


@pytest.mark.parametrize("raw, want", [
    ("117.가나중앙병원.pdf", "가나중앙병원"),
    ("18-가나자동차과.hwp", "가나자동차과"),
])
def test_leading_sequence_number_before_an_organisation_name(raw, want):
    assert clean_filename(raw) == want


def test_leading_number_rules_leave_names_with_numbers():
    assert clean_filename("가나30_다라 운영 계획.xlsx") == "가나30 다라 운영 계획"     # 밑줄 앞 '낱말+번호' 는 이름
    assert clean_filename("2025.10.08 회의록.hwp") == "2025.10.08 회의록"


def test_join_that_repeats_a_year_or_phrase_is_rejected():
    from zzaimy.app.doc_naming import repeats

    assert body_title("2025 가나수익 집행 현황\n(2) 2025년 가나수익 현황\n" + BODY)[0] == "2025 가나수익 집행 현황"
    assert body_title("2022학년도ABC참여학생\n2022년 ABC 3.0 참여학생, 협약산업체 현황\n" + BODY)[0] != \
        "2022학년도ABC참여학생 2022년 ABC 3.0 참여학생, 협약산업체 현황"
    assert repeats("미팅위크(총괄) 2018년 총장특강(미팅위크) 개최 현황")
    assert not repeats("2025-2027 가나사업 기본계획")                # 연도 범위는 되풀이가 아니다


def test_number_plus_lone_name_is_a_person_even_without_body():
    assert clean_filename("60윤하늘.pdf") == "" and clean_filename("[4-4]한바다.pdf") == ""
    assert choose_name("60윤하늘.pdf", "", META)["name"] == "증빙·회계 서류 · 가나사업 2026"


# ── 세 번째 미리보기: 앞 떼기가 낱말 안을 자르지 않는다, 목록 번호 + 연도, 서류 낱말 뒤의 이름 ───────────
REAL_WORD_TITLES = [
    "제안요청서", "제출서류 목록", "제목 없는 회의 자료", "제도 개선 계획", "붙박이장 구매 계획", "별도 지원 계획", "별표 운영 기준",
    "첨단분야 혁신융합대학 기본계획", "첨부파일 목록", "서식관리 지침", "양식장 관리 계획", "발표지원 사업 안내", "표지 디자인 공모 안내",
]


@pytest.mark.parametrize("title", REAL_WORD_TITLES)
def test_prefix_strips_never_cut_inside_a_word(title):
    """앞 표시 떼기는 구분자·낱말 경계에서만 끝난다 — 남은 이름은 원래 이름의 낱말 경계에서 시작해야 한다."""
    from zzaimy.app.doc_naming import polish_title, strip_header

    for raw in (title, f"붙임 {title}", f"[붙임1] {title}", f"별첨 {title}"):
        for got in (clean_filename(raw + ".hwp"), polish_title(raw), strip_header(raw)):
            assert got.endswith(title) or got == raw, (raw, got)
            assert title.startswith(got.split()[0]) or got.split()[0] in raw.split(), (raw, got)


def test_title_label_is_stripped_only_as_a_label():
    from zzaimy.app.doc_naming import strip_header

    assert strip_header("제 목 2018학년도 가나 특강 신청 안내") == "2018학년도 가나 특강 신청 안내"
    assert strip_header("제목: 가나 특강 신청 안내") == "가나 특강 신청 안내"
    assert strip_header("제목없음 처리 지침") == "제목없음 처리 지침"


@pytest.mark.parametrize("raw, want", [
    ("3.2020_가나스페이스 구축·운영사업_최종보고서.hwp", "2020 가나스페이스 구축·운영사업 최종보고서"),
    ("2.2018 가나프로젝트 경진대회 계획.hwp", "2018 가나프로젝트 경진대회 계획"),
    ("11.2025년 9월 가나 회의록.hwp", "2025년 9월 가나 회의록"),
])
def test_list_number_before_a_year_is_removed(raw, want):
    assert clean_filename(raw) == want


def test_year_like_numbers_that_are_not_list_numbers_stay():
    assert clean_filename("2025.10.08 회의록.hwp") == "2025.10.08 회의록"
    assert clean_filename("1.1 가나사업 개요.hwp") == "1.1 가나사업 개요"


@pytest.mark.parametrize("raw, want", [
    ("※ 가나디자인 산업체 인사 명단_김하준.hwpx", "가나디자인 산업체 인사 명단"),
    ("가나사업 참여 학생 현황 박서윤.xlsx", "가나사업 참여 학생 현황"),
])
def test_person_name_after_a_document_word_is_removed(raw, want):
    assert clean_filename(raw) == want


@pytest.mark.parametrize("raw", [
    "가나사업 운영 현황 한국어.xlsx",        # 이름에 드문 끝 글자
    "가나사업 신청서 장학금.hwp",
])
def test_words_after_a_document_word_that_are_not_names_stay(raw):
    assert clean_filename(raw) == raw.rsplit(".", 1)[0]
