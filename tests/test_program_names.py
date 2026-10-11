"""사업 이름 정리 — 처음 보는(합성) 이름으로 규칙이 일반적으로 맞는지 본다. 실제 사업 이름을 정답으로 적지 않는다."""

from zzaimy.app import program_names as pn


def test_display_name_tidies_spacing_brackets_ministry_and_time_tags():
    assert pn.display_name("가나다라  융합  지원사업") == "가나다라 융합 지원사업"
    assert pn.display_name("미래가치창출 지원( ZETA )사업") == "미래가치창출 지원(ZETA)사업"
    assert pn.display_name("미래가치창출（ZETA）사업") == "미래가치창출(ZETA)사업"              # 전각 괄호
    assert pn.display_name("해양미래부 별빛 인재육성 사업") == "별빛 인재육성 사업"              # 앞 부처(…부)
    assert pn.display_name("우주탐사청 은하수 개척 사업") == "은하수 개척 사업"                  # 앞 기관(…청)
    assert pn.display_name("2031년 은하수 개척 사업(3차년도)") == "은하수 개척 사업"
    assert pn.display_name("4차년도(2034) 은하수 개척 사업") == "은하수 개척 사업"
    assert pn.display_name("· 은하수 개척 사업") == "은하수 개척 사업"


def test_display_name_leaves_name_parts_alone():
    assert pn.display_name("학부 별빛 교육 사업") == "학부 별빛 교육 사업"                       # 두 글자 「…부」는 이름일 수 있다
    assert pn.display_name("4단계 은하수 개척 사업") == "4단계 은하수 개척 사업"                 # 단계는 이름의 일부
    assert pn.display_name("ZETA 2.0") == "ZETA 2.0"
    assert pn.display_name("해양미래부 사업") == "해양미래부 사업"                              # 떼면 남는 것이 너무 짧다


def test_split_year_round():
    assert pn.split_year_round("2031학년도 은하수 개척 사업 (2차년도)") == ("은하수 개척 사업", 2031, 2)
    assert pn.split_year_round("은하수 개척 사업(2030)") == ("은하수 개척 사업", 2030, None)
    assert pn.split_year_round("2차년도") == ("2차년도", None, None)                          # 꼬리표뿐이면 그대로


def test_is_program_name_accepts_unseen_programs():
    for name in ("은하수 개척 지원사업", "별빛융합사업단", "ZETA", "QX2.0", "OmegA 사업", "해양미래부 별빛 인재육성 사업",
                 "도심 정원 가꾸기 프로젝트", "지방전문대학 별빛 활성화사업"):
        assert pn.is_program_name(name), name


def test_is_program_name_rejects_non_programs():
    for name in ("Setup2.1", "QuickInstall4.0", "수익 비수익 사업", "통합공고 사업", "과제 2차년도 이월사업",
                 "대학 수행 사업", "관련 사업", "지원사업", "2029년 사업", "컨소시엄 사업", "회의 자료 모음", ""):
        assert not pn.is_program_name(name), name


def test_group_key_ignores_spacing_dots_brackets_and_tail_word():
    assert pn.group_key("ZETA 3.0 사업") == pn.group_key("ZETA3.0사업") == pn.group_key("ZETA3.0")
    assert pn.group_key("은하수 개척 사업") == pn.group_key("은하수개척사업")
    assert pn.group_key("별빛 대학(ZETA) 사업") != pn.group_key("별빛 대학(ZETA+) 사업")


def test_group_names_merges_only_with_evidence():
    counts = {"은하수 개척 사업": 50, "은하수개척 사업": 3, "해양미래부 은하수 개척 사업": 2,       # 같은 열쇠
              "첨성대 별빛 부트랩 사업": 40, "부트랩 사업": 4,                                  # 앞 수식어만 뗀 이름(하나뿐)
              "인재육성사업": 5, "별빛 인재육성 사업": 9,                                        # 일반 이름 — 묶지 않음
              "다온 정원 사업": 6, "가람 다온 정원 사업": 7, "누리 다온 정원 사업": 8,             # 긴 이름이 둘 — 모름
              "별빛 대학(ZETA) 육성사업": 30, "새물결 별빛 대학(ZETA+) 육성사업": 20,             # 약칭이 다름
              "통합공고 사업": 1, "Setup2.1": 1, "": 3}
    g = pn.group_names(counts)
    assert g["은하수개척 사업"] == g["해양미래부 은하수 개척 사업"] == "은하수 개척 사업"
    assert g["부트랩 사업"] == "첨성대 별빛 부트랩 사업"
    assert g["인재육성사업"] == "인재육성사업" and g["별빛 인재육성 사업"] == "별빛 인재육성 사업"
    assert g["다온 정원 사업"] == "다온 정원 사업"
    assert g["별빛 대학(ZETA) 육성사업"] != g["새물결 별빛 대학(ZETA+) 육성사업"]
    assert g["통합공고 사업"] == g["Setup2.1"] == g[""] == ""


def test_group_names_uses_ledger_identity_and_difference():
    counts = {"하늘길 사업": 30, "별누리 사업": 10, "가람 별누리 사업": 50}
    assert pn.group_names(counts)["하늘길 사업"] == "하늘길 사업"
    ledger = {"facts": [{"relation": "renamed", "terms": ["하늘길", "별누리"], "sources": ["https://example.org/a"]}]}
    g = pn.group_names(counts, ledger)
    assert g["하늘길 사업"] == g["별누리 사업"] == g["가람 별누리 사업"] == "가람 별누리 사업"
    no_source = {"facts": [{"relation": "renamed", "terms": ["하늘길", "별누리"], "sources": []}]}
    assert pn.group_names(counts, no_source)["하늘길 사업"] == "하늘길 사업"            # 출처 없는 사실은 근거가 아니다
    apart = {"programs": [{"terms": ["별누리 사업"], "sources": ["s"]}, {"terms": ["가람 별누리 사업"], "sources": ["s"]}]}
    g = pn.group_names(counts, apart)
    assert g["별누리 사업"] == "별누리 사업"                                            # 장부가 다른 사업이라 함


def test_spelling_variant_of_acronym():
    assert pn.spelling_variant("ZOTA3.0", ["ZETA 3.0", "QX"]) == "ZETA 3.0"
    assert pn.spelling_variant("ZOTA3.0", ["ZETA 2.0"]) is None                       # 판 번호가 다르면 다른 단계
    assert pn.spelling_variant("WXC", ["WCC"]) is None                                # 세 글자 약칭은 보지 않음
    assert pn.spelling_variant("ZOTA", ["ZETA", "ZOTB"]) is None                      # 후보가 둘이면 모름
    assert pn.spelling_variant("은하수 사업", ["ZETA"]) is None


def test_group_names_merges_rare_acronym_typo_into_frequent_program():
    counts = {"별빛 대학(ZETA3.0) 육성사업": 400, "ZOTA3.0": 9, "QUUX": 5}
    g = pn.group_names(counts)
    assert g["ZOTA3.0"] == "별빛 대학(ZETA3.0) 육성사업" and g["QUUX"] == "QUUX"
    # 약칭이 이름에 없으면 aliases(그래프 노드의 약칭)로
    counts = {"4단계 별빛 육성사업": 400, "ZOTA3.0": 9}
    assert pn.group_names(counts)["ZOTA3.0"] == "ZOTA3.0"
    assert pn.group_names(counts, aliases={"4단계 별빛 육성사업": ["ZETA3.0"]})["ZOTA3.0"] == "4단계 별빛 육성사업"
    assert pn.group_names({"4단계 별빛 육성사업": 50, "ZOTA3.0": 9},                  # 열 배 미만이면 근거 부족
                          aliases={"4단계 별빛 육성사업": ["ZETA3.0"]})["ZOTA3.0"] == "ZOTA3.0"
