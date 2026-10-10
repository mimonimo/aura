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
