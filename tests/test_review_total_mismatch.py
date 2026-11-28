"""표끼리 합 대조 — 같은 대상을 다른 묶음으로 나눈 두 표의 합이 다르면 다시 읽기 후보로 보인다(40차 B 분기별 315 ≠ 과제별 300)."""
from zzaimy.app import doc_review as d


def test_column_totals_skip_total_row_and_ratio():
    rows = [["분기", "주요 활동", "참석자(명)", "구성비(%)"],
            ["2분기", "6개 활동", "83", "27.7"],
            ["3분기", "5개 활동", "85", "28.3"],
            ["합계", "", "168", "56.0"]]
    assert d.column_totals(rows) == {"참석자(명)": ("참석자(명)", 168)}


def test_mismatch_named_with_both_tables():
    a = [["추진 과제", "운영 횟수", "참석자(명)"], ["강좌", "5회", "190"], ["네트워크", "4회", "83"], ["지원", "3회", "27"]]
    b = [["분기", "주요 활동", "참석자(명)"], ["2분기", "x", "88"], ["3분기", "y", "90"], ["4분기", "z", "137"]]
    out = d.total_mismatches([("Ⅱ. 과제별", 2, a), ("Ⅲ. 분기별", 3, b)])
    assert len(out) == 1 and "300" in out[0] and "315" in out[0] and "표2" in out[0] and "표3" in out[0]


def test_same_totals_or_text_columns_quiet():
    a = [["과제", "참석자(명)"], ["가", "10"], ["나", "20"]]
    b = [["분기", "참석자(명)"], ["1분기", "15"], ["2분기", "15"]]
    c = [["분기", "참석자(명)"], ["1분기", "확인 필요"], ["2분기", "15"]]
    assert d.total_mismatches([("A", 1, a), ("B", 2, b), ("C", 3, c)]) == []


def test_sub_evidence_shows_library_note():
    """40차 B 한번에: 다른 사업의 교내 계획서 조각이 서브 근거에 표시 없이 들어가 「향후 계획」 에 그대로 옮겨졌다 — 문서함 표시를 머리에 단다."""
    from zzaimy.app.delegate import Delegator
    hits = [{"title": "차년도 수정과제계획서", "text": "팻테크 융합 교육과정", "note": "문서함 자료(이 프로젝트에 올린 문서 아님): 짜임새만 참고"},
            {"title": "운영위원회 결과", "text": "저녁 강좌 확대"}]
    d = Delegator.__new__(Delegator)
    d.search, d.quality = (lambda q: hits), False
    out = d._gather(["향후 계획"])
    assert "(문서함 자료(이 프로젝트에 올린 문서 아님)" in out[0] and "(" not in out[1].split("\n")[0]
