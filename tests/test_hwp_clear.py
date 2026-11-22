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
