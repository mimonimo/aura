"""조각 품질 필터·표제·청킹 — 운영 화면에서 나온 실제 증상으로 회귀를 막는다.

실물 사례(통영시 대학생 학자금 대출이자 지원 공고, 검색 단위 24건)에서 확인된
증상을 그대로 재현한다: 이메일 주소가 표제, 낱말 중간에서 잘린 표제, 33~82자
파편, 개인정보 동의 정형 문구, FAQ 질문 한 줄, OCR 손상(날짜 붕괴·띄어쓰기 소실).
"""

from __future__ import annotations

from zzaimy.app import chunk_quality as cq
from zzaimy.app.regulations import (
    RegulationChunk,
    _finalize,
    _heading_of,
    chunk_document,
    looks_like_heading,
    split_prose,
)


# --- 단일 조각 규칙 ---


def test_substantive_len_ignores_symbols_and_spaces():
    assert cq.substantive_len("◦  ·  |  -  \n ") == 0
    assert cq.substantive_len("제1조(목적) 이 규정은") == 9   # 한글·숫자만


def test_too_short_fragment_is_dropped_at_search_level():
    ok, why = cq.is_useful("심층")
    assert ok is False and "실질 내용 부족" in why


def test_thin_chunk_is_flagged_but_kept_for_search():
    text = "관계, 주소, 연락처를 수집하며 보유 기간은 지원 종료 시까지입니다."
    v = cq.assess(text)
    assert "thin" in v.reasons or v.reasons == ()
    assert v.keep(cq.Strictness.SEARCH) is True     # 표시만, 검색에서는 살린다


def test_page_number_only_and_rule_only_are_dropped():
    for junk in ("- 13 -", "12 / 34", "───────────", "| | | |"):
        ok, why = cq.is_useful(junk)
        assert ok is False, junk
        assert why


def test_table_of_contents_lines_are_dropped():
    toc = (
        "Ⅰ. 추진배경 · · · · · · · · · · · · · · 1\n"
        "Ⅱ. 평가체제 및 기본원칙 · · · · · · · · 2\n"
        "Ⅲ. 단계평가 추진계획 · · · · · · · · · 5"
    )
    ok, why = cq.is_useful(toc)
    assert ok is False and "목차" in why


def test_long_body_with_one_dotted_line_is_not_a_toc():
    """본문 안에 점선이 한 번 나온 것으로 목차 판정을 하지 않는다(실측 오탐)."""
    body = ("지원 대상은 관내에 주소를 둔 대학생으로 한다. " * 12
            + "\n참고 · · · · · 12\n"
            + "신청은 방문 또는 우편으로 접수하며 마감일 소인까지 유효하다. " * 10)
    v = cq.assess(body)
    assert "toc_line" not in v.reasons


def test_question_only_chunk_is_dropped_but_question_with_answer_is_kept():
    q_only = "이자 지원은 언제, 어떻게 이루어지나요?"
    ok, why = cq.is_useful(q_only)
    assert ok is False and "질문" in why

    q_and_a = (
        "이자 지원은 언제, 어떻게 이루어지나요?\n"
        "학자금 대출 이자는 매 학기 종료 후 한국장학재단을 통해 정산하여 지급합니다."
    )
    assert cq.is_useful(q_and_a)[0] is True


def test_ocr_damage_needs_two_signals_and_is_rate_based():
    damaged = ("청방법 가신청기간: 202692(수) ~ 92 "
               "①통영시소재고등학교졸업생이면서관내에주민등록을둔대학생"
               "②학자금대출을받은사람으로서직전학기성적이기준에미달하지않은사람")
    assert len(cq.ocr_damage_signals(damaged)) >= 2
    clean = "신청 기간은 2026. 9. 2.(수)부터 9. 12.(금)까지이며 방문 접수만 받습니다."
    assert cq.ocr_damage_signals(clean) == []


def test_digits_only_table_shell_is_flagged_but_english_body_is_not():
    digits = " ".join(str(i) for i in range(1, 40))      # 항목명이 사라진 표
    assert "low_text" in cq.assess(digits).reasons
    english = ("Listen to concerns while respecting individual values and choices and "
               "promote the meaning and value of higher education for every student.")
    assert "low_text" not in cq.assess(english).reasons


# --- 코퍼스 규칙 ---


def _chunk(doc_id: int, content: str) -> dict:
    return {"doc_id": doc_id, "content": content}


CONSENT = (
    "본인은 위와 같이 개인정보를 수집·이용하는 데 동의합니다. 수집 항목은 성명, "
    "생년월일, 관계, 주소, 연락처이며 보유 기간은 지원 종료 시까지입니다. "
    "관련 규정에 의하여 기록·보존되고, 기간이 지나면 지체 없이 파기합니다."
)


def test_boilerplate_is_found_across_documents_without_naming_it():
    """같은 정형 문구가 여러 문서 묶음에 반복되면 자동으로 걸러진다."""
    bodies = [
        "가. 지원 대상은 관내에 주민등록을 둔 대학생으로 한다. 선정은 소득 분위 순으로 한다.",
        "나. 신청 서류는 성적증명서와 재학증명서이며 학과 사무실에 제출한다.",
        "다. 평가는 정량 지표 60점과 정성 지표 40점으로 구성하여 산정한다.",
        "라. 사업비는 인건비와 운영비로 구분하며 집행 잔액은 반납한다.",
        "마. 성과 지표는 취업률과 자격증 취득률을 핵심으로 삼는다.",
        "바. 협약 기간은 2년이며 연차 점검 결과에 따라 연장할 수 있다.",
        "사. 컨소시엄 구성은 주관 대학과 참여 대학으로 나누어 운영한다.",
        "아. 변경 승인은 사업관리 부서의 사전 검토를 거쳐 처리한다.",
    ]
    chunks = []
    for doc, body in enumerate(bodies, start=1):
        chunks.append(_chunk(doc, CONSENT))
        chunks.append(_chunk(doc, (body + " ") * 4))
    reasons = cq.corpus_reasons(chunks)
    consent_idx = [i for i, c in enumerate(chunks) if c["content"] == CONSENT]
    assert all("boilerplate" in reasons.get(i, []) for i in consent_idx)
    other = [i for i in range(len(chunks)) if i not in consent_idx]
    assert not any("boilerplate" in reasons.get(i, []) for i in other)


def test_same_document_loaded_twice_does_not_turn_its_body_into_boilerplate():
    """같은 파일이 여러 번 적재돼도 본문이 '반복 문구'로 사라지지 않는다."""
    body = ["공고문 본문 문단 %d — 지원 대상과 절차를 설명한다. " % i * 4 for i in range(6)]
    chunks = [_chunk(doc, t) for doc in (1, 2, 3) for t in body]
    reasons = cq.corpus_reasons(chunks)
    assert not any("boilerplate" in v for v in reasons.values())
    # 대신 중복으로 잡혀 한 벌만 남는다
    kept, dropped = cq.filter_chunks(chunks)
    assert len(kept) == len(body)
    assert all("duplicate" in d["quality_reasons"] for d in dropped)


def test_header_footer_repeated_in_one_document_is_dropped():
    chunks = [_chunk(1, "영남이공대학교 학자금 지원 공고") for _ in range(4)]
    chunks.append(_chunk(1, "지원 금액은 학기당 등록금 범위에서 이자 전액으로 한다."))
    reasons = cq.corpus_reasons(chunks)
    assert "header_footer" in reasons.get(0, [])
    assert "header_footer" not in reasons.get(len(chunks) - 1, [])


def test_strictness_levels_select_how_much_is_dropped():
    chunks = [_chunk(1, "심층"),
              _chunk(1, "관계, 주소, 연락처를 수집하고 지원 종료 시 파기한다."),
              _chunk(1, "지원 대상은 관내에 주민등록을 둔 대학생으로 한다. " * 5)]
    for level, expect in ((cq.Strictness.FLAG, 3), (cq.Strictness.SEARCH, 2),
                          (cq.Strictness.INDEX, 1)):
        kept, _ = cq.filter_chunks(chunks, level)
        assert len(kept) == expect, level


# --- 표제 ---


def test_heading_is_empty_when_no_structural_title_exists():
    for junk in ("us9510@korea.kr", "6지원방법: 이자 지", "본 기관은 학자금 대출이자 지",
                 "관계, 주소", "규정에 의하여 기록·보존되고, 기간"):
        assert _heading_of(junk) == "", junk
        assert looks_like_heading(junk) is False


def test_heading_keeps_real_structural_titles():
    assert _heading_of("제7조(지원 대상) 지원 대상은 다음 각 호와 같다.") == "제7조(지원 대상)"
    assert _heading_of("Ⅱ. 평가체제 및 기본원칙\n평가는 정량·정성으로 나눈다.").startswith("Ⅱ.")
    assert _heading_of("□ 신청 방법\n방문 또는 우편으로 접수한다.") == "□ 신청 방법"


def test_heading_never_comes_from_a_word_cut_in_half():
    assert _heading_of("학자금 대출이자 지\n원 사업의 절차는 다음과 같다.") == ""


# --- 청킹 ---


def test_fragments_are_merged_until_they_can_stand_alone():
    frags = [RegulationChunk(heading="", content=t) for t in
             ("마", "감일 소인까지 유효함", "◦", "신청은 방문 또는 우편으로 접수한다.")]
    out = _finalize(frags)
    assert len(out) < len(frags)
    assert all(cq.substantive_len(c.content) >= cq.MIN_SUBSTANTIVE_DROP for c in out)


def test_article_boundaries_survive_the_merge():
    """조문 단위 분할은 이 플랫폼의 핵심 — 짧아도 합치지 않는다."""
    text = (
        "제1조(목적) 이 규정은 합성사업의 운영 기준을 정함을 목적으로 한다.\n"
        "제2조(정의) 이 규정에서 평가위원이란 선정평가를 수행하는 자를 말한다.\n"
        "제3조(회피) 평가위원은 이해관계가 있는 대학의 평가를 회피하여야 한다."
    )
    chunks = chunk_document(text)
    assert len(chunks) == 3
    assert [c.heading for c in chunks] == ["제1조(목적)", "제2조(정의)", "제3조(회피)"]


def test_question_and_answer_stay_in_one_chunk():
    text = (
        "□ 자주 묻는 질문\n"
        "학자금 대출 이자 지원 제외 대상은 무엇인가요?\n"
        "학자금 대출을 받지 않은 학생과 타 지자체 지원을 중복으로 받는 학생은 제외합니다.\n"
        "이자 지원은 언제, 어떻게 이루어지나요?\n"
        "매 학기 종료 후 한국장학재단을 통하여 계좌로 지급합니다.\n"
    )
    chunks = split_prose(text)
    for c in chunks:
        assert cq.assess(c.content).keep(cq.Strictness.SEARCH), c.content
        if "?" in c.content:
            assert "제외합니다" in c.content or "지급합니다" in c.content


def test_bulleted_pdf_lines_do_not_become_one_chunk_each():
    """PDF 추출문은 줄마다 불릿이 붙는다 — 줄 단위로 쪼개지면 안 된다."""
    text = "\n".join(
        f"◦ 항목 {i}" for i in range(12)
    ) + "\n◦ 지원 대상은 관내에 주민등록을 둔 대학생으로 하며 소득 기준은 적용하지 않는다."
    chunks = split_prose(text)
    assert len(chunks) <= 3
    assert all(cq.substantive_len(c.content) >= cq.MIN_SUBSTANTIVE_DROP for c in chunks)


def test_garbled_script_is_a_damage_signal():
    """스캔 PDF 에 박힌 엉터리 글자층 — 한자 잡음이 글자의 15% 를 넘으면 손상이다."""
    from zzaimy.app.chunk_quality import garbled_ratio, ocr_damage_signals

    junk = "01 02 03 [ 04 OL-----(IYI0影)I0号是收百亡C 05 LL-----(晶否)庫I号是收百是亡C 吾(3p卫) 吾号 三今 吾号斗章吞 " * 2
    assert garbled_ratio(junk) >= 0.15
    assert "다른 문자 체계로 깨짐" in ocr_damage_signals(junk)
    fine = "제1조(목적) 이 규정은 산학협력단의 운영에 관한 사항을 정함을 목적으로 한다. " * 3
    assert garbled_ratio(fine) == 0.0


def test_number_only_lines_never_survive_as_chunks():
    """크기 분할이 만든 "3-2." 같은 번호 줄 조각도 이웃과 합친다 — 표제만 있는 조각은 검색 단위가 아니다."""
    from zzaimy.app.regulations import RegulationChunk, _finalize, index_ready

    long_body = "\n".join(f"{i}. 컨소시엄 참여대학은 지역 산업 수요에 맞는 교육과정을 운영한다." for i in range(40))
    out = _finalize([RegulationChunk(heading="3-1.", content="3-1.\n" + long_body + "\n3-2.")])
    assert out and all(cq.substantive_len(c.content) >= cq.MIN_SUBSTANTIVE_DROP for c in out)
    kept, dropped = index_ready(1, [RegulationChunk(heading="Ⅴ.", content="Ⅴ."),
                                    RegulationChunk(heading="제1조(목적)", content="제1조(목적) 이 규정은 산학협력단의 운영에 필요한 사항을 정한다.")])
    assert [c.heading for c in kept] == ["제1조(목적)"] and dropped == 1
    kept, dropped = index_ready(1, [RegulationChunk(heading="Ⅴ.", content="Ⅴ.")])
    assert len(kept) == 1 and dropped == 0        # 전부 걸러지면 원본을 둔다


def test_table_block_is_packed_by_rows_not_sentences():
    """표 블록은 행 단위로 묶인다 — 행 중간에서 잘려 머리글과 값이 떨어지지 않는다."""
    from zzaimy.app.regulations import split_prose

    rows = [f"{i}학과 | 산업디자인학과 | {i}학년 | 공간디자인기초 {i} | 컴퓨터그래픽 {i}" for i in range(1, 60)]
    text = "< 연계교육과정 편성표 >\n" + "\n".join(rows)
    chunks = split_prose(text, target=400, hard_max=600)
    assert len(chunks) >= 3
    for c in chunks:
        for ln in c.content.splitlines():
            if " | " in ln:
                assert ln.count("|") == 4, ln          # 행이 온전하다


def test_prose_blocks_inherit_the_previous_structural_heading():
    """표제 없는 블록은 직전 절 표제를 물려받는다 — 화면에서 어느 절의 조각인지 보이게."""
    from zzaimy.app.regulations import split_prose

    text = ("Ⅱ. 사업 개요\n" + "사업의 목적은 지역 산업과 연계한 교육과정을 운영하는 것이다. " * 6 + "\n"
            + "예산은 연 10억 원 규모이며 3년간 지원한다. 대학은 자체 부담금을 확보해야 한다. " * 12 + "\n"
            + "Ⅲ. 신청 자격\n" + "전문대학 및 일반대학이 신청할 수 있다. 컨소시엄 구성은 필수다. " * 8)
    chunks = split_prose(text, target=300, hard_max=450)
    heads = [c.heading for c in chunks]
    assert heads[0] == "Ⅱ. 사업 개요" and all(h for h in heads)
    assert "Ⅲ. 신청 자격" in heads and heads.index("Ⅲ. 신청 자격") > 0


def test_headed_table_skeleton_is_not_protected_by_its_heading():
    """표제가 있어도 기호가 글자보다 많은 표 껍데기는 적재 관문을 못 넘는다."""
    from zzaimy.app.regulations import RegulationChunk, index_ready

    skeleton = " | ".join(["-----"] * 12) + "\n" + " | ".join(["|"] * 12) + "\n합계 1 2 3 4 5 6 7 8 9 10 11 12 13"
    kept, dropped = index_ready(1, [
        RegulationChunk(heading="< 예산 집행 기준 >", content=skeleton),
        RegulationChunk(heading="제2조(정의)", content="제2조(정의) 이 규정에서 쓰는 용어의 뜻은 다음과 같다. 1. 사업단이란 대학이 설치한 조직을 말한다."),
    ])
    assert [c.heading for c in kept] == ["제2조(정의)"] and dropped == 1
