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
    assert g.too_wordy_for_value("미래모빌리티·로봇 산업 중심 구조 개편 추진") and not g.too_wordy_for_value("(확인 필요)")
    assert g.too_wordy_for_value("2022년 지역 인력 수요 증가세 지속")
    assert g.column_year(rows, 2, 2) == "" and g.other_year("81.9% (2023)", "2022") and not g.other_year("81.9%", "2022")


def test_same_table_in_section_detects_form_swot(tmp_path):
    from zzaimy.ingest import gdocs as g

    def para(s, t, style="NORMAL_TEXT"):
        return {"startIndex": s, "endIndex": s + len(t) + 1, "paragraph": {"paragraphStyle": {"namedStyleType": style},
                "elements": [{"textRun": {"content": t + "\n"}}]}}

    def cell(t):
        return {"content": [{"paragraph": {"elements": [{"textRun": {"content": t + "\n"}}]}}]}
    swot = {"startIndex": 30, "endIndex": 90, "table": {"tableRows": [
        {"tableCells": [cell("강점 (S)"), cell(""), cell("약점 (W)"), cell("")]},
        {"tableCells": [cell("기회 (O)"), cell(""), cell("위협 (T)"), cell("")]},
        {"tableCells": [cell("사업 시사점"), cell(""), cell(""), cell("")]}]}}
    body = [para(1, "3. 대학 여건", "HEADING_2"), para(15, "본문"), swot, para(90, " "), para(92, "4. 다음", "HEADING_2")]
    doc = {"body": {"content": body}}
    info = g.outline(doc)
    idx = next(s["index"] for s in info["sections"] if s["heading"] == "3. 대학 여건")
    new = [["강점 (S)", "산업 기반", "약점 (W)", "인프라 부족"], ["기회 (O)", "정책", "위협 (T)", "경쟁"], ["사업 시사점", "융합 인재", "", ""]]
    assert g.same_table_in_section(body, info, idx, new) == 1
    assert g.same_table_in_section(body, info, idx, [["구분", "값"], ["가", "1"], ["나", "2"]]) == 0


def test_record_fills_round_trip(tmp_path):
    import json
    from zzaimy.ingest import gdocs as g
    g.record_fills(tmp_path, "DOC1", "3. 대학 여건", 1, [(2, 2), (2, 3)])
    g.record_fills(tmp_path, "DOC1", "3. 대학 여건", 1, [(3, 2)])
    data = json.loads((tmp_path / "gdocs_fills.json").read_text())
    assert sorted(map(tuple, next(iter(data.values())))) == [(2, 2), (2, 3), (3, 2)]


def test_outline_spec_from_conversation_toc():
    from zzaimy.ingest import gdocs_templates as T
    ol = ("#### Ⅰ. 사업 개요 및 추진 배경\n\n1. 사업 목적 및 비전\n    *   지역 AI-X 선도 인재 양성\n2. 추진 배경 및 필요성\n    *   인력 부족\n\n"
          "#### Ⅱ. 대학 역량 및 추진 체계\n\n1. 대학 기본 교육 현황\n2. 사업 추진 체계\n    *   추진 조직도\n")
    sp = T.outline_spec(T.SPECS["plan"], ol)
    heads = [(b["h"], b["text"]) for b in sp["blocks"] if "h" in b]
    assert heads == [(1, "Ⅰ. 사업 개요 및 추진 배경"), (2, "1. 사업 목적 및 비전"), (2, "2. 추진 배경 및 필요성"),
                     (1, "Ⅱ. 대학 역량 및 추진 체계"), (2, "1. 대학 기본 교육 현황"), (2, "2. 사업 추진 체계")]
    guides = [b["guide"] for b in sp["blocks"] if "guide" in b]
    assert any("지역 AI-X 선도 인재 양성" in g for g in guides) and sp["intro"].startswith("대화에서 정한 목차")
    assert T.outline_spec(T.SPECS["plan"], "그냥 답변입니다.\n1. 하나\n2. 둘") is None
    bold = "#### **Ⅰ. 대학의 역량**\n1. **대학 기본 교육 현황**\n   - 학과 현황\n2. **실습 여건**\n#### **Ⅱ. 사업 추진내용**\n1. **추진체계**\n2. **교육과정**\n"
    assert [b["text"] for b in T.outline_spec(T.SPECS["plan"], bold)["blocks"] if b.get("h") == 2][:2] == ["1. 대학 기본 교육 현황", "2. 실습 여건"]
