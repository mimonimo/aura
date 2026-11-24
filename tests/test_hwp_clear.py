"""작년 완성본에서 시작한 작업본 — 남은 글을 번호로 보이고(leftovers_text), 27B 가 고른 번호만 비운다(clear_items, 11-23).
요청 낱말(「틀」「뼈대」)로 나누지 않고, 무엇이 짜임이고 무엇이 지난 내용인지는 에이전트가 판단한다 — 코드는 목록과 비우기만."""
from __future__ import annotations

import io
import re
import zipfile

from zzaimy.app import webhwp_edit_ops as eo


def _hwpx(section: str) -> bytes:
    from hwpx import HwpxDocument
    base = HwpxDocument.new().to_bytes()
    zin = zipfile.ZipFile(io.BytesIO(base))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            body = zin.read(info.filename)
            if info.filename == "Contents/section0.xml":
                body = re.sub(r"(<hs:sec\b[^>]*>)", r"\1" + section, body.decode(), count=1).encode()
            z.writestr(info, body)
    return buf.getvalue()


def _p(text: str, pid: int) -> str:
    return f'<hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p>'


def _table(rows: list[list[str]], base: int) -> str:
    trs = "".join("<hp:tr>" + "".join(
        f'<hp:tc><hp:cellAddr colAddr="{j}" rowAddr="{i}"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList>{_p(c, base + i * 10 + j)}</hp:subList></hp:tc>'
        for j, c in enumerate(r)) + "</hp:tr>" for i, r in enumerate(rows))
    return (f'<hp:p id="{base}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0">'
            f'<hp:tbl id="{base + 1}" rowCnt="{len(rows)}" colCnt="{len(rows[0])}">{trs}</hp:tbl></hp:run></hp:p>')


def _texts(data: bytes) -> str:
    return " ".join(re.findall(r"<hp:t>([^<]*)</hp:t>", zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()))


DOC = (_p("「2025 가상 사업」 결과보고서", 1) + _p("1. 사업 개요", 2)
       + _table([["사업명", "2025 가상 사업"], ["주관 부서", "가상 산학협력단"]], 100)
       + _p("2025년에는 특강과 캠프를 운영하였다.", 3))


def test_leftovers_lists_cells_and_paragraphs_with_ids():
    lt = eo.leftovers_text(_hwpx(DOC))
    assert "표1:1,2 「2025 가상 사업」" in lt and "표1:1,1 「사업명」" in lt
    assert "글1 「「2025 가상 사업」 결과보고서」" in lt and "글2 「1. 사업 개요」" in lt and "글3 「2025년에는" in lt


def test_clear_items_blanks_only_chosen_and_keeps_structure():
    data = _hwpx(DOC)
    out, msg = eo.clear_items(data, {"ids": ["표1:1,2", "글3", "글1", "표9:1,1"]})
    t = _texts(out)
    assert "2025 가상 사업" not in t and "2025년에는" not in t
    assert "사업명" in t and "1. 사업 개요" in t and "가상 산학협력단" in t
    assert "3곳을 비웠다" in msg and "표9:1,1" in msg
    lt = eo.leftovers_text(out)
    assert "표1:1,2" not in lt and "표1:2,2 「가상 산학협력단」" in lt


# ── 본문 표를 같은 머리의 빈 서식 표 칸 자리에 맞추기(fit_form_table, 11-23 14차) ──

def test_fit_form_table_maps_columns_by_head_and_rows_by_label():
    doc = _hwpx(_p("2.2 분야별 추진 실적", 2) + _table(
        [["분야", "활동 수", "참여 인원(명)"], ["교육과정 개발", "", ""], ["산학협력", "", ""]], 100))
    fit = eo.fit_form_table(doc, 1, [["분야", "참여 인원", "활동 수"], ["산학협력", "183", "6"], ["교육과정 개발", "24", "4"], ["합계", "207", "10"]])
    assert fit["start"] == [2, 1]
    assert fit["values"] == [["교육과정 개발", "4", "24"], ["산학협력", "6", "183"], ["합계", "10", "207"]]


def test_fit_form_table_fills_empty_rows_in_order_without_labels():
    doc = _hwpx(_p("2.1 월별", 2) + _table([["월", "참여 인원(명)"], ["", ""], ["", ""]], 100))
    fit = eo.fit_form_table(doc, 1, [["월", "참여 인원(명)"], ["3월", "24"], ["4월", "42"], ["5월", "55"]])
    assert fit == {"start": [2, 1], "values": [["3월", "24"], ["4월", "42"], ["5월", "55"]]}


def test_fit_form_table_transposes_horizontal_summary_into_label_column():
    doc = _hwpx(_p("1. 사업 개요", 2) + _table([["사업명", ""], ["사업 기간", ""], ["주관 부서", ""]], 100))
    fit = eo.fit_form_table(doc, 1, [["사업명", "사업 기간"], ["2026 가상 사업", "2026. 3. ~ 12."]])
    assert fit["start"] == [1, 1]
    assert fit["values"][:2] == [["사업명", "2026 가상 사업"], ["사업 기간", "2026. 3. ~ 12."]]
    fit2 = eo.fit_form_table(doc, 1, [["구분", "내용"], ["주관 부서", "산학협력단"], ["사업명", "2026 가상 사업"]])
    assert fit2["start"] == [1, 1] and fit2["values"][0] == ["사업명", "2026 가상 사업"] and fit2["values"][2] == ["주관 부서", "산학협력단"]


def test_clear_result_points_to_next_step():
    _, msg = eo.clear_items(_hwpx(DOC), {"ids": ["표1:1,2"]})
    assert "비우기는 끝났다" in msg and "남은 글" in msg


def test_review_points_out_repeated_row_names():
    """첫 열 이름이 여러 행에 되풀이된 표를 끝 점검 후보로 알린다 — 합칠지는 27B 가 요청을 보고 판단(11-23 16차)."""
    from zzaimy.app import doc_review
    doc = _hwpx(_p("2.1 월별 추진 실적", 2) + _table(
        [["월", "주요 활동", "참여 인원(명)"], ["4월", "협약", "-"], ["4월", "캠프", "42"], ["5월", "특강", "55"], ["5월", "방문", "2"]], 100))
    out = doc_review.review(doc, "월마다 한 행으로 합계")
    assert "같은 이름이 여러 행" in out and "4월 2행" in out


def test_review_attaches_ledger_grouping_for_blank_columns():
    """빈·「확인 필요」 칸이 남은 열은 장부로 묶어 본 결과를 후보에 붙인다(11-23 17차: 성과지표 실적 칸을 빈 채로 finish)."""
    from zzaimy.app import doc_review
    doc = _hwpx(_p("3. 성과지표 달성 실적", 2) + _table(
        [["성과지표", "목표", "실적"], ["교육 이수 인원(명)", "300", ""], ["취업 연계(명)", "40", "확인 필요"]], 100))
    seen = []
    out = doc_review.review(doc, "", ledger=lambda labels, heads, lh: seen.append((labels, heads, lh)) or " 묶어 보았다: 209명")
    assert seen == [(["교육 이수 인원(명)", "취업 연계(명)"], ["실적"], "성과지표")]
    assert "「실적」 열에 빈·「확인 필요」 칸" in out and "209명" in out


def test_gap_tables_lists_number_columns_left_blank():
    from zzaimy.app import doc_review
    doc = _hwpx(_p("3. 성과지표 달성 실적", 2) + _table(
        [["성과지표", "목표", "실적", "비고"], ["교육 이수 인원(명)", "300", "", "캠프 운영"], ["취업 연계(명)", "40", "확인 필요", "특강"]], 100))
    assert doc_review.gap_tables(doc) == [(1, ["교육 이수 인원(명)", "취업 연계(명)"], ["실적"], "성과지표")]


def test_review_flags_names_not_in_ledger():
    """대부분 행이 모은 활동 이름인 열에서 홀로 없는 이름(지어낸 활동)을 짚는다(11-23 18차)."""
    from zzaimy.app import doc_review
    rows = [["월", "주요 활동", "참여 인원(명)"], ["4월", "파이썬 기초 집중 캠프(1기)", "42"], ["5월", "AI 자격증 대비 특강", "55"],
            ["7월", "하계 AI 프로젝트 부트캠프", "38"], ["8월", "교과목 개발 중간 점검", "확인 필요"]]
    known = {"파이썬 기초 집중 캠프(1기)", "AI 자격증 대비 특강", "하계 AI 프로젝트 부트캠프", "《AI 경진대회 결과 보고》"}
    assert doc_review.unknown_names(rows, known) == ["교과목 개발 중간 점검"]
    doc = _hwpx(_p("2.1 월별 추진 실적", 2) + _table(rows, 100))
    assert "교과목 개발 중간 점검" in doc_review.review(doc, "", known=known)


def test_review_flags_same_number_in_two_columns_of_a_row():
    from zzaimy.app import doc_review
    doc = _hwpx(_p("3. 성과지표 달성 실적", 2) + _table(
        [["성과지표", "목표", "실적", "달성률(%)"], ["교육 이수 인원(명)", "300", "264", "88.0"], ["산업체 협약(건)", "10", "10", "100.0"]], 100))
    out = doc_review.review(doc, "")
    assert "다른 열에 같은 수" in out and "산업체 협약" in out


def test_repeated_review_on_unchanged_doc_is_short(tmp_path, monkeypatch):
    """문서가 그대로인데 다시 읽기를 또 부르면 전체를 다시 보이지 않고 고치거나 끝내라고 한다(11-23: 5~9번 되풀이로 걸음 한도)."""
    from types import SimpleNamespace
    from zzaimy.app import main, webhwp
    p = tmp_path / "w.hwpx"
    p.write_bytes(_hwpx(_p("1. 개요", 2) + _table([["항목", "값"], ["사업명", "가"]], 100)))
    monkeypatch.setattr(webhwp, "get", lambda db, fid: {"path": str(p)})
    hw = SimpleNamespace(fid=lambda: 1, db=None, sid=None)
    first = main._doc_review_text(hw, "")
    assert "다시 읽는다" in first
    again = main._doc_review_text(hw, "")
    assert "바뀌지 않았다" in again and len(again) < 300
    p.write_bytes(_hwpx(_p("1. 개요", 2) + _table([["항목", "값"], ["사업명", "나"]], 100)))
    assert "다시 읽는다" in main._doc_review_text(hw, "")


def test_delete_table_removes_stray_table_but_not_form_table():
    """잘못 만든 표(문장 하나가 든 1칸 표)를 통째로 지운다 — 「모든 행을 지울 수 없다」 를 되풀이하던 것(11-23)."""
    doc = _hwpx(_p("2.2 분야별", 2) + _table([["교육 인프라 분야 참여 인원은 결과 보고에 명시된 수치가 없다"]], 100)
                + _table([["분야", "인원"], ["산학협력", "183"]], 200))
    out, msg = eo.table_edit(doc, {"table": 1, "op": "delete_table"})
    assert "통째로 지웠다" in msg
    left = [t["head"] for t in eo.list_tables(eo._Doc(out))]
    assert len(left) == 1 and left[0].startswith("분야")


def test_ragged_longer_row_gets_a_ready_call_not_silent_move():
    """비율만 넣는 줄 사이 한 줄만 [집행액, 비율] — 코드가 자리를 옮겨 넣지 않고, 그 행 빈 칸부터 채우는 인자를 보여 준다(11-23 사용자:
    상황에 맞춰 판별). 그 인자로 다시 부르면 맞게 들어간다."""
    import json as _j
    doc = _hwpx(_p("4. 예산", 2) + _table([["비목", "집행액(원)", "비율(%)"], ["인건비", "28,200,000원", ""],
                                          ["운영비", "", ""], ["기자재", "39,200,000원", ""]], 100))
    try:
        eo.table_edit(doc, {"table": 1, "op": "set_cells", "start": [2, 3], "values": [["21.3"], ["27,400,000원", "20.6"], ["29.5"]]})
        assert False, "제안 없이 넣으면 안 된다"
    except eo.EditError as e:
        m = re.search(r"start: (\[\d+, \d+\]), values: (\[\[.*?\]\]) 로 다시", str(e))
        assert m
    out, _ = eo.table_edit(doc, {"table": 1, "op": "set_cells", "start": _j.loads(m.group(1)), "values": _j.loads(m.group(2))})
    from zzaimy.app import doc_review
    d = eo._Doc(out)
    rows = doc_review._rows(re.search(r"<hp:tbl\b.*?</hp:tbl>", d.secs[next(iter(d.secs))], re.S).group(0))
    assert rows[1] == ["인건비", "28,200,000원", "21.3"] and rows[2] == ["운영비", "27,400,000원", "20.6"] and rows[3][2] == "29.5"



def test_blank_value_keeps_real_amount_but_clears_sample():
    """빈 값 「」 은 견본 글(0000·OOO)만 비우고 실제 금액(28,200,000원)은 둔다(11-23: 000 을 견본으로 보아 지웠다)."""
    from zzaimy.app import doc_review
    doc = _hwpx(_p("4", 2) + _table([["비목", "집행액", "비율"], ["인건비", "28,200,000원", ""], ["운영비", "0000", ""]], 100))
    out, _ = eo.table_edit(doc, {"table": 1, "op": "set_cells", "start": [2, 2], "values": [["", "21.3"], ["", "9.0"]]})
    d = eo._Doc(out)
    rows = doc_review._rows(re.search(r"<hp:tbl\b.*?</hp:tbl>", d.secs[next(iter(d.secs))], re.S).group(0))
    assert rows[1] == ["인건비", "28,200,000원", "21.3"] and rows[2] == ["운영비", "", "9.0"]


def test_leftovers_mark_text_written_after_start():
    """작업본을 만든 뒤 새로 쓴 글은 비울 글 목록에 표시한다 — 다 채운 올해 보고서를 작년 글로 알고 비운 일(11-23 시연)."""
    start_doc = _hwpx(_p("1. 개요", 2) + _table([["사업명", "2025 가상 사업"], ["총 사업비", "250,000,000원"]], 100))
    st = eo.unit_texts(start_doc)
    now = _hwpx(_p("1. 개요", 2) + _table([["사업명", "2025 가상 사업"], ["총 사업비", "120,000,000원"]], 100))
    lt = eo.leftovers_text(now, start=st)
    assert "120,000,000원」  ← 이 작업본에 새로 쓴 글" in lt and "2025 가상 사업」\n" in lt + "\n"
    assert eo.new_text_count(now, st) == 1 and eo.new_text_count(start_doc, st) == 0


def test_label_column_mismatch_is_not_written_and_points_to_row():
    """첫 열 값이 다른 행 라벨이면 라벨 칸을 덮지 않고 맞는 행을 알린다(11-23 20차: 「사업 기간」 이 「주관 부서」 로 덮임)."""
    doc = _hwpx(_p("Ⅰ. 개요", 2) + _table([["사업명", ""], ["사업 기간", ""], ["총 사업비", ""], ["주관 부서", ""]], 100))
    try:
        eo.table_edit(doc, {"table": 1, "op": "set_cells", "start": [1, 1], "values": [["사업명", "가"], ["주관 부서", "나"]]})
        assert False
    except eo.EditError as e:
        assert "「주관 부서」 은 4행 라벨" in str(e) and "start: [4, 1]" in str(e)
    out, _ = eo.table_edit(doc, {"table": 1, "op": "set_cells", "start": [1, 1], "values": [["사업명", "가"], ["사업 기간", "2026"]]})
    assert out


def test_same_filled_table_in_another_section_is_found(monkeypatch):
    # 11-23 재녹화 09: 5절에 3절과 같은 성과지표 표를 다시 붙였다
    kpi = [["성과지표", "목표", "실적", "달성률(%)"], ["교육 이수 인원(명)", "300", "264", "88.0"], ["산업체 협약(건)", "10", "9", "90.0"]]
    doc = _hwpx(_p("3. 성과지표 달성 실적", 2) + _table(kpi, 100) + _p("5. 성과 및 개선 사항", 3) + _p("본문", 4))
    from zzaimy.app import webhwp_write as ww
    monkeypatch.setattr(ww, "section_before", lambda data, sec, off: "절:3. 성과지표 달성 실적")   # 절 필드 없는 시험 문서
    sec5 = "절:5. 성과 및 개선 사항"
    hit = eo.same_table_elsewhere(doc, sec5, kpi)
    assert hit and hit["no"] == 1 and "3. 성과지표" in hit["section"]
    assert eo.same_table_elsewhere(doc, sec5, [["구분", "내용"], ["개선", "저녁 강좌"]]) is None


def test_field_cell_where_names_the_column_of_a_filled_cell():
    # 11-23 3차 녹화 11: 칸 「교육 이수 인원(명)」 이 목표 열인데 실적을 넣었다 — 채운 자리의 열 머리를 보인다
    import re as _re
    xml = _table([["성과지표", "목표", "실적"], ["교육 이수 인원(명)", "300", "264"]], 100)
    i = xml.index("<hp:t>300</hp:t>")
    fld = ('<hp:ctrl><hp:fieldBegin id="9" type="CLICK_HERE" name="칸:교육 이수 인원(명)" editable="1" dirty="0" zorder="-1" fieldid="9"/></hp:ctrl>')
    xml = xml[:i] + fld + xml[i:]
    doc = _hwpx(_p("3. 성과지표", 2) + xml)
    w = eo.field_cell_where(doc, "칸:교육 이수 인원(명)")
    assert w.startswith("표1 2행") and "「목표」 열" in w and "300 | 264" in w


def test_numbers_left_from_last_year_are_found_by_row_and_column_even_if_tables_shift():
    # 11-23 4차 녹화: 성과지표 표의 취업 연계 실적 칸에 작년 값 21 이 남았는데 끝 점검이 못 찾음 — 표 번호가 밀려도 행·열로 찾는다
    kpi_old = [["성과지표", "목표", "실적"], ["취업 연계(명)", "30", "21"], ["교과목 개발(개)", "4", "13"]]
    kpi_new = [["성과지표", "목표", "실적"], ["취업 연계(명)", "40", "21"], ["교과목 개발(개)", "6", "15"]]
    old = _hwpx(_p("3. 성과지표", 2) + _table(kpi_old, 100))
    new = _hwpx(_p("2. 추가 표", 1) + _table([["구분", "내용"], ["가", "나"]], 50) + _p("3. 성과지표", 2) + _table(kpi_new, 100))
    hits = eo.numbers_from_start(new, eo.number_cells(old))
    assert hits == ["표2:2,3 「21」"]


def test_clear_items_tells_when_row_name_cells_were_blanked():
    # 27차 B 짧게: 세목 이름 칸(표 첫 열)까지 비우고 값만 채워 이름 없는 행으로 끝남 — 비운 행 이름 칸을 알린다
    doc = _table([["세목", "집행액(천원)"], ["강사료", "9,000"], ["운영비", "20,000"]], 300)
    _, msg = eo.clear_items(_hwpx(doc), {"ids": ["표1:2,1", "표1:2,2", "표1:3,2"]})
    assert "행 이름 칸 1곳" in msg and "강사료" in msg
    _, msg2 = eo.clear_items(_hwpx(doc), {"ids": ["표1:2,2", "표1:3,2"]})
    assert "행 이름 칸" not in msg2


def test_empty_form_table_with_same_first_head_elsewhere_is_found(monkeypatch):
    # 32차 A 짧게: 「2. 추진 실적」 에 머리가 다른 월별 표를 새로 붙여 하위 절 2.1 의 빈 서식 표(월 | … | 참여 인원)가 남음
    form = [["월", "주요 활동", "참여 인원(명)", "근거 문서"], ["", "", "", ""], ["", "", "", ""]]
    doc = _hwpx(_p("2.1 월별 추진 실적", 2) + _table(form, 100))
    from zzaimy.app import webhwp_write as ww
    monkeypatch.setattr(ww, "section_before", lambda data, sec, off: "절:2.1 월별 추진 실적")
    new = [["월", "주요 활동", "활동 건수", "집행 금액(원)"], ["3월", "워크숍", "2", "8,000,000"]]
    hit = eo.empty_form_elsewhere(doc, "절:2. 추진 실적", new)
    assert hit and hit["no"] == 1 and "2.1 월별" in hit["section"] and "참여 인원" in hit["head"]
    assert eo.empty_form_elsewhere(doc, "절:2. 추진 실적", [["구분", "내용"], ["가", "나"]]) is None
    filled = _hwpx(_p("2.1 월별 추진 실적", 2) + _table([form[0], ["3월", "워크숍", "24", "보고"], ["4월", "캠프", "42", "보고"]], 100))
    assert eo.empty_form_elsewhere(filled, "절:2. 추진 실적", new) is None
