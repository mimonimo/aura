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


def test_mismatch_names_record_group_that_makes_the_difference():
    recs = [{"과제": "지역 연계 강좌", "활동": "디지털 기초", "참석자(명)": "190명", "_doc": "디지털 기초 결과서"},
            {"과제": "평생교육 네트워크", "활동": "정례 모임", "참석자(명)": "110명", "_doc": "정례 모임 결과서"}]
    recs += [{"과제": "2026 가상 사업", "활동": f"운영위원회 {i}차", "참석자(명)": "운영위원 5명", "_doc": f"운영위원회 {i}차 회의 결과"}
             for i in (1, 2, 3)]
    a = [["추진 과제", "참석자(명)"], ["지역 연계 강좌", "190"], ["평생교육 네트워크", "110"]]
    b = [["분기", "참석자(명)"], ["2분기", "200"], ["3분기", "115"]]
    out = d.total_mismatches([("과제별", 2, a), ("분기별", 3, b)], recs)
    assert "차이 15" in out[0] and "「과제」 가 「2026 가상 사업」 인 기록 3건" in out[0] and "《운영위원회 1차 회의 결과》" in out[0]


def test_ledger_label_rules_from_round40():
    from zzaimy.app import fact_ledger as fl
    items = ["날짜", "과제", "활동", "수료자", "상담 건수", "연계 기관", "참석자"]
    doc = ("참석자는 27명이었고, 사후 설문에서 다수가 계속 참여 의사를 밝혔다. 기간 중 학습 상담 27건을 진행하였다. "
           "이 결과는 핵심지표 「학습 상담」 달성치에 27건으로 반영한다.")
    kept, _ = fl.validate([{"활동": "진로 탐색 워크숍", "수료자": "27명", "참석자": "27명", "상담 건수": "27건",
                            "quote": "참석자는 27명이었고"}], items, doc)
    assert kept[0]["수료자"] == "" and kept[0]["참석자"] == "27명" and kept[0]["상담 건수"] == "27건"
    doc2 = "지역 기관 2곳과 업무협약을 맺었다. 이 결과는 핵심지표 「지역 기관 연계」 달성치에 2곳으로 반영한다."
    kept2, _ = fl.validate([{"연계 기관": "지역 기관 2곳", "quote": "지역 기관 2곳과 업무협약을 맺었다."}], items, doc2)
    assert kept2[0]["연계 기관"] == "지역 기관 2곳"



def test_unbacked_fixes_reads_only_document_part():
    from zzaimy.app.doc_agent import unbacked_fixes
    fake = "후보\n1. 「취업 연계」 문서 합 31\n\n지금 문서 전체:\n\n| 취업 연계 | 52 |"
    assert unbacked_fixes("취업 연계를 31명으로 바로잡았습니다", fake) == ["31명"]


def test_period_sum_shows_scope_by_category_and_filter_args():
    """41차 B 한번에: 운영위원회 기록(5명 ×3)이 분기 합에 섞였다 — 같은 합을 과제로 나눠 보이고 범위 맞추는 거르기 인자를 보인다."""
    from zzaimy.app import fact_ledger as fl
    from tests.test_fact_ledger import FakeDB
    items = ["날짜", "분야", "활동", "참석 인원"]
    docs, i = {}, 0
    for d, f, a, n in [("2026. 5. 1.", "지역 연계 강좌", "강좌1", "40명"), ("2026. 8. 1.", "지역 연계 강좌", "강좌2", "50명"),
                       ("2026. 6. 1.", "평생교육 네트워크", "모임", "19명"), ("2026. 11. 1.", "평생교육 네트워크", "한마당", "64명"),
                       ("2026. 6. 2.", "", "운영위원회 1차", "5명"), ("2026. 8. 6.", "2026 가상 사업", "운영위원회 2차", "5명")]:
        i += 1
        docs[f"k{i}"] = {"doc_id": i, "title": f"{a} 결과", "items": items,
                         "records": [{"날짜": d, "분야": f, "활동": a, "참석 인원": n, "quote": "q"}]}
    db = FakeDB([])
    fl.save(db, 5, {"items": items, "live": list(docs), "docs": docs})
    obs, _ = fl.aggregate(db, 5, ["날짜:분기"], ["참석 인원"])
    assert "「분야」 로 나누면" in obs and "(빈 값) 5" in obs and "2026 가상 사업 5" in obs and "!" not in obs.split("로 나누면")[1][:200]
    obs2, _ = fl.aggregate(db, 5, ["분야"], ["참석 인원"])
    assert "로 나누면" not in obs2                                  # 갈래로 묶었으면 이미 보인다


def test_alias_fixes_category_that_disagrees_with_document_cell():
    """41차 B 한번에: 「추진 과제 | 지역 연계 강좌」 문서의 분야를 활동 이름으로 뽑아 과제별 합이 33 모자랐다."""
    from zzaimy.app import fact_ledger as fl
    pairs = []
    for t, f in [("지역 연계 강좌", "지역 연계 강좌"), ("평생교육 네트워크", "평생교육 네트워크"),
                 ("성인 학습자 지원", "성인 학습자 지원"), ("지역 연계 강좌", "가을 공예 체험 강좌")]:
        pairs.append(({"records": [{"분야": f, "참석 인원": "33명"}]}, f"사업 | 가상 사업\n추진 과제 | {t}\n운영일 | 2026년 9월 1일"))
    notes = fl.fill_aliases(pairs, ["분야", "참석 인원"])
    assert pairs[3][0]["records"][0]["분야"] == "지역 연계 강좌" and "바로잡았다" in " ".join(notes)
    many = ({"records": [{"분야": "강사료"}, {"분야": "운영비"}]}, "추진 과제 | 지역 연계 강좌")
    fl.fill_aliases(pairs[:3] + [many], ["분야"])
    assert [r["분야"] for r in many[0]["records"]] == ["강사료", "운영비"]   # 값이 여럿인 문서는 그대로


def test_unit_column_fix_names_thousand_won_value():
    """41차 B 짧게: 집행액(천원) 열 합계 행에 51,600,000 을 다섯 번 넣다 막힘 — 그 열 단위로 고친 값을 보인다."""
    from types import SimpleNamespace
    from zzaimy.app import webhwp_edit_ops as eo
    cls = next(c for c in vars(eo).values() if isinstance(c, type) and hasattr(c, "_unit_column_fix"))
    o = cls.__new__(cls)
    o.hw = SimpleNamespace(_gate_evidence=lambda: ["◇ 집계 합계 · 지출액: 11,400,000+40,200,000=51,600,000"])
    out = o._unit_column_fix(["세목", "집행액(천원)", "구성비(%)"], [["합계", "51,600,000", "100.0%"]], ["51,600,000"])
    assert "「51,600」" in out and "천원" in out
    assert o._unit_column_fix(["세목", "집행액(원)"], [["합계", "51,600,000"]], ["51,600,000"]) == ""


def test_fill_into_thousand_won_column_offers_fix(tmp_path, monkeypatch):
    """41차 B 짧게: hwp_fill 칸:강사료 11,400,000 이 집행액(천원) 열에 들어가고 알림이 없었다."""
    from pathlib import Path
    from tests.test_webhwp_agent import _setup, wa, wt
    from zzaimy.app import webhwp_edit_ops as eo
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    hw = wa.HwpTools(db, base, 77, "kim", "기획처", {"id": 1, "name": "AI 사업"}, lambda: [])
    tid = next(line.split()[0] for line in hw.forms({"query": "예산 변경"}).splitlines() if "예산 변경" in line)
    hw.start({"form": tid, "title": "예산 변경 신청서"})
    monkeypatch.setattr(hw, "numbers_gate", lambda md: None)
    monkeypatch.setattr(hw, "_gate_evidence", lambda: ["◇ 집계 강사료 · 지출액: 11,400,000"])
    monkeypatch.setattr(eo, "field_cell_where", lambda raw, k: "표5 2행 「집행액(천원)」 열 · 지금 행: 강사료 | 11,400,000")
    out = hw.fill({"values": {"칸:사업명": "11,400,000"}})
    assert "[단위 셈 확인]" in out and '"11,400"' in out


def test_scope_call_uses_row_labels_of_the_other_table():
    """42차 B 한번에: 운영위원회 기록이 과제 「사업 이름」 2건 + 빈 칸 1건으로 갈려 한 묶음 합이 차이와 같지 않았다 —
    과제별 표의 행 이름 밖 기록 합이 차이와 같으면 그 행 이름만 남기는 인자를 보인다."""
    recs = [{"분야": "지역 연계 강좌", "참석 인원": "190명", "_doc": "a"}, {"분야": "성인 학습자 지원", "참석 인원": "27명", "_doc": "b"},
            {"분야": "평생교육 네트워크", "참석 인원": "83명", "_doc": "c"},
            {"분야": "2026 가상 사업", "참석 인원": "5명", "_doc": "운영위원회 1차"}, {"분야": "2026 가상 사업", "참석 인원": "5명", "_doc": "운영위원회 2차"},
            {"분야": "", "참석 인원": "5명", "_doc": "운영위원회 3차"}]
    a = [["추진 과제", "참석자(명)"], ["지역 연계 강좌", "190"], ["성인 학습자 지원", "27"], ["평생교육 네트워크", "83"]]
    b = [["분기", "참석자(명)"], ["2분기", "88"], ["3분기", "90"], ["4분기", "137"]]
    out = d.total_mismatches([("과제별", 2, a), ("분기별", 3, b)], recs)
    assert "밖인 기록 3건" in out[0] and '"filt": {"분야": ["지역 연계 강좌", "성인 학습자 지원", "평생교육 네트워크"]}' in out[0]
    assert "「분기」 표 기준" in out[0]


def test_mismatch_notes_equal_totals_in_past_document():
    """43차 B 짧게: 차이 15 를 「다른 대상이라 둘 다 맞다」 로 뒀다 — 작업본을 만든 작년 보고서에서 두 표 합이 같았으면 그것을 알린다."""
    a = [["추진 과제", "참석자(명)"], ["지역 연계 강좌", "190"], ["성인 학습자 지원", "27"], ["평생교육 네트워크", "83"]]
    b = [["분기", "참석자(명)"], ["2분기", "88"], ["3분기", "90"], ["4분기", "137"]]
    start = {"지역연계강좌|참석자(명)": "137\x00표2:2,3", "성인학습자지원|참석자(명)": "18\x00표2:3,3",
             "평생교육네트워크|참석자(명)": "46\x00표2:4,3", "2분기|참석자(명)": "71\x00표3:2,3",
             "3분기|참석자(명)": "84\x00표3:3,3", "4분기|참석자(명)": "46\x00표3:4,3", "강사료|집행액(천원)": "23,450\x00표5:2,2"}
    out = d.total_mismatches([("과제별", 2, a), ("분기별", 3, b)], None, start)
    assert "지난 문서에서는 이 머리의 두 표 합이 201 로 같았다" in out[0]
    start["4분기|참석자(명)"] = "40\x00표3:4,3"
    assert "지난 문서" not in d.total_mismatches([("과제별", 2, a), ("분기별", 3, b)], None, start)[0]
