"""독스 작업본 품질 점검 — 넘친 표 폭·연속 반복 줄."""
from zzaimy.ingest import doc_quality as Q


def _doc(body):
    return {"tabs": [{"tabProperties": {"tabId": "t.0"}, "documentTab": {
        "documentStyle": {"pageSize": {"width": {"magnitude": 595.3}}, "marginLeft": {"magnitude": 72}, "marginRight": {"magnitude": 72}},
        "body": {"content": body}}}]}


def _p(s, e, text):
    return {"startIndex": s, "endIndex": e, "paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]}}


def _t(s, widths):
    return {"startIndex": s, "endIndex": s + 10, "table": {"tableStyle": {"tableColumnProperties": [
        {"widthType": "FIXED_WIDTH", "width": {"magnitude": w, "unit": "PT"}} for w in widths]}}}


def test_audit_finds_wide_tables_and_repeats():
    body = [_p(1, 5, "가"), _t(5, [240, 240]), _t(15, [200, 200]),
            _p(25, 30, "◦ 계획"), _p(30, 35, "◦ 계획"), _p(35, 40, "◦ 계획"), _p(40, 45, "나"), _t(45, [300, 10, 180])]
    a = Q.audit(_doc(body))
    assert a["tables"] == 3 and [w["start"] for w in a["wide"]] == [5, 45]
    assert a["repeats"] == [{"text": "◦ 계획", "count": 3, "ranges": [(30, 35), (35, 40)]}]


def test_fix_keeps_ratio_fits_width_and_shifts_after_cuts():
    body = [_p(1, 5, "◦ 계획"), _p(5, 9, "◦ 계획"), _p(9, 13, "◦ 계획"), _t(13, [300, 10, 180])]
    reqs = Q.fix_requests(_doc(body))
    dels = [r for r in reqs if "deleteContentRange" in r]
    assert [d["deleteContentRange"]["range"]["startIndex"] for d in dels] == [9, 5]      # 뒤에서부터
    cols = [r["updateTableColumnProperties"] for r in reqs if "updateTableColumnProperties" in r]
    assert {c["tableStartLocation"]["index"] for c in cols} == {13 - 8}                  # 지운 8 만큼 당김
    ws = [c["tableColumnProperties"]["width"]["magnitude"] for c in cols]
    assert abs(sum(ws) - 451.3) < 0.5 and min(ws) >= Q.MIN_COL_PT - 0.01 and ws[0] > ws[2] > ws[1]


def test_two_repeats_are_kept():
    a = Q.audit(_doc([_p(1, 5, "◦ 계획"), _p(5, 9, "◦ 계획"), _p(9, 13, "다")]))
    assert a["repeats"] == []                                                            # 두 번은 서식에도 흔하다


def test_toc_lines_get_indent_by_level():
    def para(s, text, style="NORMAL_TEXT", indent=0):
        ps = {"namedStyleType": style}
        if indent:
            ps["indentStart"] = {"magnitude": indent, "unit": "PT"}
        return {"startIndex": s, "endIndex": s + len(text) + 1, "paragraph": {"paragraphStyle": ps, "elements": [{"textRun": {"content": text + "\n"}}]}}
    body = [para(1, "목 차"), para(10, "Ⅰ. 사업 개요", indent=10), para(30, "1. 추진 배경 및 필요성"), para(60, "1.1. 정책 동향", indent=16),
            para(90, "Ⅰ. 사업 개요", "HEADING_1"), para(110, "1. 추진 배경 및 필요성", "HEADING_2"), para(140, "1.1. 정책 동향", "HEADING_3")]
    a = Q.audit(_doc(body))
    assert a["toc"] == 3 and {t["start"]: t["want"] for t in a["toc_uneven"]} == {10: 0.0, 30: 16.0, 60: 32.0}
    reqs = [r["updateParagraphStyle"] for r in Q.fix_requests(_doc(body)) if "updateParagraphStyle" in r]
    assert [r["paragraphStyle"]["indentStart"]["magnitude"] for r in reqs] == [0.0, 16.0, 32.0]


def test_year_value_cells_reject_prose():
    from zzaimy.ingest import gdocs as g

    def cell(t):
        return {"content": [{"paragraph": {"elements": [{"textRun": {"content": t + "\n"}}]}}]}
    rows = [{"tableCells": [cell("구분"), cell("지표"), cell("최근 3년 값"), cell("출처")]},
            {"tableCells": [cell(""), cell(""), cell("20○○"), cell("")]},
            {"tableCells": [cell("교육"), cell("취업률"), cell(""), cell("")]}]
    assert g.value_column(rows, 2, 2) and not g.value_column(rows, 2, 3)
    assert g.too_wordy_for_value("2025년 기준 1,390억 원을 미래모빌리티 융합산업 육성에 투입")
    assert not g.too_wordy_for_value("67.3%") and not g.too_wordy_for_value("1,390억 원(2025년)")
