"""코드로 짠 공통 양식 15종의 생성 규칙(10-25 점검 뒤) — 잎 절 필드, 열 폭 비율, 작성방법, 제출 문구, 머리줄 반복, 빈 양식 표 지우기."""
import io
import re
import zipfile

import pytest

from zzaimy.app import webhwp_style as ws
from zzaimy.app import webhwp_templates as wt
from zzaimy.app import webhwp_write as ww
from zzaimy.ingest import gdocs_templates as gt

ALL = {**gt.SPECS, **wt.OFFICIAL, **wt.GRANT}
_BUILT: dict[str, bytes] = {}


def _built(sid: str) -> bytes:
    if sid not in _BUILT:
        _BUILT[sid] = wt.build(ALL[sid])
    return _BUILT[sid]


def _sec(data: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()


def _tables(data: bytes) -> list[str]:
    return re.findall(r"<hp:tbl\b(?:(?!<hp:tbl\b).)*?</hp:tbl>", _sec(data), re.S)


def _col_widths(tbl: str) -> list[int]:
    w = {}
    for c in re.findall(r"<hp:tc\b.*?</hp:tc>", tbl, re.S):
        if re.search(r'colSpan="1"', c):
            w[int(re.search(r'colAddr="(\d+)"', c).group(1))] = int(re.search(r'<hp:cellSz width="(\d+)"', c).group(1))
    return [w[i] for i in range(max(w) + 1)]


def _spec_table(sid: str, first_col: str) -> str:
    """정의 표(작성방법 상자 뺀 차례) 가운데 첫 머리 글이 first_col 인 표의 xml."""
    tbls = [t for t in _tables(_built(sid)) if "【작성방법】" not in t]
    defs = [b["table"] for b in ALL[sid]["blocks"] if "table" in b]
    return next(t for t, d in zip(tbls, defs) if (d.get("head") or [d["columns"]])[0][0] == first_col)


def test_all_fifteen_forms_pass_check_spec():
    assert len(ALL) == 15
    for sid, spec in ALL.items():
        assert gt.check_spec(spec) == [], sid
    for sid in [*wt.OFFICIAL, *wt.GRANT]:                     # 공문·국고 양식도 절마다 작성방법(10-25 전에는 하나도 없었다)
        data = _built(sid)
        sec_fields = [f["name"] for f in ww.fields(data) if f["kind"] == "절"]
        assert sec_fields and all("【작성방법】" in ww.section_context(data, n) for n in sec_fields), sid


def test_section_fields_only_on_leaf_sections():
    """절 필드는 build 가 아는 잎 절에만 — 번호 없는 절·로마 숫자 잎 장·붙임에도 박히고 위 제목에는 없다."""
    for sid, spec in ALL.items():
        names = [f["name"] for f in ww.fields(_built(sid)) if f["kind"] == "절"]
        want = [f"절:{t}"[:40] for t, leaf in wt.slots_of(spec["blocks"]) if leaf]
        assert names == want, sid
    plan = [f["name"] for f in ww.fields(_built("plan"))]
    report = [f["name"] for f in ww.fields(_built("report"))]
    minutes = [f["name"] for f in ww.fields(_built("minutes"))]
    assert "절:사업 요약" in plan and "절:붙임. 증빙 자료 목록" in plan
    assert "절:1. (추진 과제 1) ○○○○" not in plan and "절:Ⅲ. 사업 추진 계획" not in plan
    assert "절:Ⅶ. 차년도 추진 계획 및 지속가능성" in report and "절:Ⅵ. 환경 변화 대응 및 평가·컨설팅 결과 반영" in report
    assert "절:첨부. 우수사례" not in report
    assert "절:3. 안건별 논의 및 의결" not in minutes and "절:1. (안건 1) ○○○○" not in minutes


def test_school_forms_still_guess_headings():
    """학교 실물 서식(slots 없음)은 지금처럼 글 꼴로 제목을 짐작한다."""
    from hwpx import HwpxDocument

    from zzaimy.app import webhwp_fields as wf
    doc = HwpxDocument.new()
    for t in ("1. 추진 배경", "", "2. 추진 내용", ""):
        doc.add_paragraph(t)
    names = [f["name"] for f in wf.fieldize(doc.to_bytes())[1]]
    assert names == ["절:1. 추진 배경", "절:2. 추진 내용"]


def test_column_widths_follow_defined_ratio():
    """정의 비율(widths)이 살아 있다 — 안내 글을 세지 않고, 합친 칸 글은 덮은 열 수로 나눠 센다."""
    w = _col_widths(_spec_table("budget_change", "비목"))
    ratio = [1, 1, 1, 1, 1, 2]
    assert w[5] == max(w)                                     # 「사유」(비율 2)가 가장 넓다(전에는 가장 좁았다)
    assert max(abs(a / sum(w) - b / sum(ratio)) / (b / sum(ratio)) for a, b in zip(w, ratio)) < 0.15
    # 간트표 — 3.4 : 0.62×12 : 1.6. 머리 「추진 일정 (월)」이 첫 달 칸에 몰려 1월만 두 배이던 것
    g = _col_widths(_spec_table("plan", "세부 추진 내용"))
    months = g[1:13]
    assert 0.24 < g[0] / sum(g) < 0.30 and max(months) - min(months) <= 0.15 * max(months)
    assert g[13] >= 2800                                      # 산출물 칸에 필드가 박힐 폭(MIN_SLOT_W)
    worst = []
    for sid, spec in ALL.items():                            # 15종 전체 — 하한(머리 낱말 한 줄) 때문에 벗어나도 절반은 넘지 않는다
        tbls = [t for t in _tables(_built(sid)) if "【작성방법】" not in t]
        for t, d in zip(tbls, [b["table"] for b in spec["blocks"] if "table" in b]):
            cw = _col_widths(t)
            if d.get("widths") and len(d["widths"]) == len(cw):
                s, sw = sum(cw), sum(d["widths"])
                worst.append(max(abs(a / s - b / sw) / (b / sw) for a, b in zip(cw, d["widths"])))
    assert max(worst) < 0.5


def test_fit_widths_divides_merged_text_by_span():
    from zzaimy.export import house_style as hs
    cols = [["긴 머리 글자 열여섯 글자짜리", "", ""], ["", "가", "나"], ["", "다", "라"]]
    a = hs.fit_widths(cols, 42520, [1, 1, 1])
    b = hs.fit_widths(cols, 42520, [1, 1, 1], spans=[[3, 1, 1], [1, 1, 1], [1, 1, 1]])
    assert a[0] > a[1] and max(b) - min(b) <= 2                # 셋을 덮은 머리 글은 셋으로 나눠 세면 비율(1:1:1) 그대로


def test_tables_with_head_rows_repeat_header():
    """머리줄 있는 표는 「제목 줄 반복」이 켜진다(학교 실물 표 거의 모두 켜 있음). 작성방법 상자·머리줄 없는 격자는 그대로."""
    on = 0
    for sid in ALL:
        for t in _tables(_built(sid)):
            head = re.search(r"<hp:tbl\b[^>]*>", t).group(0)
            has_head = "head" in ws.roles(t).values()
            assert ('repeatHeader="1"' in head) == has_head, sid
            on += has_head
    assert on >= 70                                          # 15종 머리줄 표 76개(10-25)
    budget = _spec_table("budget_change", "비목")
    assert 'repeatHeader="1"' in re.search(r"<hp:tbl\b[^>]*>", budget).group(0)


def test_sign_block_is_built_and_unknown_blocks_fail():
    x = _sec(_built("plan"))
    texts = [re.sub(r"<[^>]+>", "", p) for p in re.findall(r"<hp:p\b[^>]*>(?:(?!<hp:p\b).)*?</hp:p>", x, re.S)]
    sign = ["위와 같이 「○○○○ 사업」 사업계획서를 제출합니다.", "20○○. ○. ○.", "○○대학교 총장  ○ ○ ○  (직인)"]
    at = [texts.index(s) for s in sign]
    assert at == sorted(at) and at[0] > texts.index("붙임. 증빙 자료 목록")
    assert "제출합니다" in _sec(_built("report")) and "신청합니다" in _sec(_built("partner_apply"))
    with pytest.raises(ValueError, match="모른다"):
        wt.build({"id": "x", "title": "x", "blocks": [{"h": 1, "text": "1. 가"}, {"intro": "버려지던 블록"}]})


def test_body_table_drops_empty_form_frame_only():
    """본문에 표를 쓰면 같은 절의 빈 양식 표가 지워진다 — 라벨·2단 머리줄이 있어도 사용자 글이 없으면 빈 틀.
    열 수가 같은 틀만 지우고(SWOT 격자는 남음), 사용자 글이 든 표·결재란·확인표는 남긴다."""
    def heads(d):
        return [t["head"] for t in ws.tables(d) if not t["head"].startswith("【")]
    plan = _built("plan")
    rows = [["구분", "지표", "2023", "2024", "2025", "출처"], ["교육", "취업률(%)", "70", "71", "72", "공시"]]
    out, _ = ww.write_section(plan, "절:3. 대학 여건 및 현황 분석", [("table", rows)])
    gone = [h for h in heads(plan) if h not in heads(out)]
    assert len(gone) == 1 and gone[0].startswith("구분 | 지표 (단위)") and any(h.startswith("강점") for h in heads(out))
    budget = _built("budget_change")
    item = [["비목", "금액(천원)"], ["인건비", "1,000"]]
    out, _ = ww.write_section(budget, "절:2. 변경 내역", [("table", item)])
    assert not any(h.startswith("비목 | 세목") for h in heads(out)) and any(h.startswith("기안자") for h in heads(out))
    filled, _ = ww.set_fields(budget, {"표4:비목": "인건비"})       # 사용자 글이 든 표는 둔다
    out, _ = ww.write_section(filled, "절:2. 변경 내역", [("table", item)])
    assert any(h.startswith("비목 | 세목") for h in heads(out))
    out, _ = ww.write_section(budget, "절:3. 성과지표·사업 추진에 미치는 영향", [("table", [["가", "나", "다"], ["1", "2", "3"]])])
    assert any(h.startswith("기안자") for h in heads(out))      # 결재란은 열 수가 같아도 남는다
    minutes = _built("minutes")
    out, _ = ww.write_section(minutes, "절:7. 확인", [("table", [["가", "나", "다", "라"], ["1", "2", "3", "4"]])])
    assert any(h.startswith("구분 | 소속·직위") for h in heads(out))


def test_person_cells_and_grid_only_sections():
    """사람 칸은 「입력:」, 라벨·값 격자만 있는 잎 절(표만 채우는 절)은 절 필드 없이 칸 필드로만."""
    budget = {f["name"]: f["kind"] for f in ww.fields(_built("budget_change"))}
    assert budget["입력:신청 일자"] == "입력" and {"입력:기안자", "입력:검토자", "입력:결재권자"} <= set(budget)
    assert budget["칸:사업명"] == budget["칸:담당자"] == "칸"            # 기존 이름은 그대로
    assert "입력:달성률(%)" in {f["name"] for f in ww.fields(_built("kpi_evidence"))}
    assert "입력:대표자(서명)" in {f["name"] for f in ww.fields(_built("partner_apply"))}
    plan = {f["name"] for f in ww.fields(_built("plan"))}
    assert "절:가. 과제 개요" not in plan and "절:나. 세부 추진 내용" in plan and "칸:과제명" in plan
    assert "절:실적 요약" not in {f["name"] for f in ww.fields(_built("report"))}


def test_page_margins_from_one_place_and_page_numbers():
    """여백은 house_style.PAGE 한 곳(좌우 20mm, 독스 판과 같음) — 표 폭이 본문 폭과 같고, 여러 쪽 문서에만 쪽 번호."""
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.export import house_style as hs
    assert wt.TEXT_W == hs.text_width() and abs(gt.MARGIN - hs.PAGE["margin_mm"]["left"] * 72 / 25.4) < 0.1
    for sid in ("plan", "budget_change"):
        x = _sec(_built(sid))
        assert eo._text_width(x) == wt.TEXT_W                     # 여백과 표 폭이 어긋나지 않는다
        assert all(int(re.search(r'<hp:sz width="(\d+)"', t).group(1)) == wt.TEXT_W for t in _tables(_built(sid)))
    assert _sec(_built("plan")).count("<hp:pageNum ") == 1 and 'pos="BOTTOM_CENTER"' in _sec(_built("plan"))
    assert "<hp:pageNum " not in _sec(_built("budget_change"))   # 한두 쪽 서식은 쪽 번호 없이
