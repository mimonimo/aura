"""표 갈래 가리기·갈래별 디자인 입히기·담당자 지정 — export/table_kinds, app/table_styles, webhwp_style.style_table(kind)."""
from __future__ import annotations

import io
import re
import zipfile

import pytest

from zzaimy.app import table_styles as ts
from zzaimy.app import webhwp_style as ws
from zzaimy.export import table_kinds as tk

SCHEDULE = [["구분", "추진 내용", "기간"], ["1단계", "수요 조사", "2026.3.~2026.5."], ["2단계", "시범 운영", "6~8월"]]
GANTT = [["추진 과제", "3월", "4월", "5월", "6월"], ["교육과정 개발", "●", "●", "", ""], ["현장실습", "", "●", "●", "●"]]
BUDGET = [["비목", "금액(천원)", "산출 근거"], ["인건비", "24,000", "2명"], ["운영비", "8,500", "회의"], ["합계", "32,500", ""]]
STATS = [["학과", "재학생(명)", "졸업생(명)", "취업률"], ["기계과", "320", "98", "77.6%"], ["전기과", "280", "85", "81.2%"]]
ROLES = [["성명", "소속", "역할"], ["홍길동", "기획처", "사업 총괄"], ["김영희", "산학협력단", "예산 집행"]]
COMPARE = [["구분", "현행", "개선"], ["운영 주체", "학과별 분산 운영", "사업단 통합 운영"], ["평가 주기", "연 1회", "학기별 2회"]]
SUMMARY = [["사업명", "지역 산업 연계 실무 인재 양성"], ["사업 기간", "2026.3.1.~2027.2.28."], ["주관 부서", "산학협력단"]]
LISTED = [["번호", "프로그램", "내용"], ["1", "현장실습", "지역 협약 기업과 연계해 학기 중 4주간 현장실습을 운영함"],
          ["2", "멘토링", "기업 실무자를 멘토로 위촉해 학생 프로젝트를 월 1회 점검함"]]


@pytest.mark.parametrize("rows,kind", [(SCHEDULE, "schedule"), (GANTT, "schedule"), (BUDGET, "budget"), (STATS, "stats"),
                                       (ROLES, "roles"), (COMPARE, "compare"), (SUMMARY, "summary"), (LISTED, "list")])
def test_classify(rows, kind):
    assert tk.classify(rows)[0] == kind


def test_value_types():
    assert [tk.value_type(v) for v in ("3.5", "2025.3.", "3~6월", "1,200", "80%", "○", "12명", "홍길동")] == \
        ["number", "date", "date", "money", "percent", "symbol", "number", "label"]
    # 2열 표 첫 행이 「라벨 | 긴 값」 이면 머리줄이 아니다(요약표), 「구분 | 내용」 이면 머리줄
    assert tk.head_rows_of(SUMMARY) == 0 and tk.head_rows_of([["구분", "내용"], ["a", "b"]]) == 1


def _prof(kind: str, n: int = 40, docs: int = 10, **vals) -> dict:
    v = {k: {"mode": m, "share": 0.8, "n": n} for k, m in vals.items()}
    return {"kinds": {kind: {"n": n, "docs": docs, "values": v}}, "all": {"n": n, "docs": docs, "values": {}}}


def test_mined_design_needs_enough_samples():
    prof = _prof("budget", **{"head_fill:family": "#F2F2F2", "align:money": "RIGHT", "outer_lr": "none"})
    d = ts.design("budget", prof)
    assert d["head_fill"] == "#F2F2F2" and d["align"]["money"] == "RIGHT" and d["outer_lr"] == "none"
    # 표본이 적거나 한두 문서에서만 왔으면 기본값
    assert ts.design("budget", _prof("budget", n=5, **{"head_fill:family": "#F2F2F2"}))["head_fill"] == tk.BASE["head_fill"]
    assert ts.design("budget", _prof("budget", docs=1, **{"head_fill:family": "#F2F2F2"}))["head_fill"] == tk.BASE["head_fill"]
    # 정렬의 양쪽·배분은 왼쪽으로(좁은 칸에서 글자 사이가 벌어지지 않게)
    assert ts.design("list", _prof("list", **{"align:long": "JUSTIFY"}))["align"]["long"] == "LEFT"


def test_fill_family():
    assert [ts.fill_family(c) for c in ("#F2F2F2", "#D8D8D8", "#E0E5FA", "#FFFFCC", "none")] == ["gray", "gray", "blue", "yellow", "none"]


# ── 입히기 ──

def _doc(rows):
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.app import webhwp_templates as wt
    base = wt.build(wt.GRANT["budget_change"])
    data, _ = eo.table_insert(base, {"values": rows, "at": {"end": True}})
    return data


def _last_table(data: bytes) -> tuple[str, str]:
    z = zipfile.ZipFile(io.BytesIO(data))
    sec = z.read("Contents/section0.xml").decode()
    return list(ws._TBL.finditer(sec))[-1].group(0), z.read("Contents/header.xml").decode()


def _align_of(tbl: str, hdr: str, text: str) -> str:
    tc = next(m.group(0) for m in ws._TC.finditer(tbl) if ws._text(m.group(0)) == text)
    pp = re.search(r'paraPrIDRef="(\d+)"', tc).group(1)
    body = re.search(rf'<hh:paraPr\b[^>]*\bid="{pp}"[^>]*>.*?</hh:paraPr>', hdr, re.S).group(0)
    return re.search(r'<hh:align\b[^>]*horizontal="(\w+)"', body).group(1)


def _fill_of(tbl: str, hdr: str, text: str) -> str:
    tc = next(m.group(0) for m in ws._TC.finditer(tbl) if ws._text(m.group(0)) == text)
    bf = re.search(r'borderFillIDRef="(\d+)"', tc).group(1)
    body = re.search(rf'<hh:borderFill\b[^>]*\bid="{bf}"[^>]*>.*?</hh:borderFill>', hdr, re.S).group(0)
    m = re.search(r'faceColor="(#[0-9A-Fa-f]{6})"', body)
    return m.group(1).upper() if m else "none"


def test_insert_applies_kind_design(monkeypatch):
    prof = _prof("budget", **{"head_fill:family": "#F2F2F2", "align:money": "RIGHT", "total_fill:family": "none",
                              "outer_lr": "none", "outer_tb": "0.4 mm"})
    monkeypatch.setattr(ts, "current", lambda: prof)
    tbl, hdr = _last_table(_doc(BUDGET))
    assert _fill_of(tbl, hdr, "비목") == "#F2F2F2"                 # 캔 머리줄 음영
    assert _align_of(tbl, hdr, "24,000") == "RIGHT"                  # 금액 오른쪽
    assert _fill_of(tbl, hdr, "합계") == "none"                      # 합계 행 음영 없음(캔 값), 굵게는 기본
    # 좌우 열린 표 — 첫 열 칸의 왼쪽 선이 없다
    tc = next(m.group(0) for m in ws._TC.finditer(tbl) if ws._text(m.group(0)) == "인건비")
    bf = re.search(r'borderFillIDRef="(\d+)"', tc).group(1)
    body = re.search(rf'<hh:borderFill\b[^>]*\bid="{bf}"[^>]*>.*?</hh:borderFill>', hdr, re.S).group(0)
    assert 'leftBorder type="NONE"' in body


def test_summary_table_labels_and_no_head(monkeypatch):
    monkeypatch.setattr(ts, "current", lambda: {})
    tbl, hdr = _last_table(_doc(SUMMARY))
    # 기본값: 머리줄 없음, 라벨 열 음영(요약표), 첫 행 값 칸은 음영 없음
    assert _fill_of(tbl, hdr, "사업명") == tk.default_design("summary")["label_fill"].upper()
    assert _fill_of(tbl, hdr, "지역 산업 연계 실무 인재 양성") == "none"


def test_user_values_override_kind(monkeypatch):
    prof = _prof("budget", **{"head_fill:family": "#F2F2F2", "align:money": "CENTER"})
    monkeypatch.setattr(ts, "current", lambda: prof)
    data = _doc(BUDGET)
    no = ws.tables(data)[-1]["no"]
    out = ws.apply(data, [no], {"kind": "auto", "head_fill": ws.color_value("파란색"), "num_align": "RIGHT", "font_pt": 9.0,
                                "table_align": "CENTER"})
    tbl, hdr = _last_table(out)
    assert _fill_of(tbl, hdr, "비목") == ws.NAMED_FILLS["파란색"]
    assert _align_of(tbl, hdr, "8,500") == "RIGHT"                   # 담당자 지정이 캔 값(가운데)에 앞선다
    assert 'horzAlign="CENTER"' in tbl
    assert 'height="900"' in hdr                                     # 9pt
    # kind=none 이면 학교 표 공통 기본(머리 #E5E5E5)
    tbl2, hdr2 = _last_table(ws.apply(data, [no], {"kind": "none"}))
    assert _fill_of(tbl2, hdr2, "비목") == "#E5E5E5"


def test_color_value_unknown_is_none():
    assert ws.color_value("연두색") == "#E2EFDA" and ws.color_value("abc") is None and ws.color_value("없음") == "none"


def test_column_weights_follow_types():
    d = tk.default_design("list")
    w = tk.column_weights(LISTED, d)
    assert w[0] < w[1] < w[2]                                        # 번호 < 짧은 이름 < 긴 내용


def test_text_style_role_caption_only():
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.app import webhwp_write as ww
    from zzaimy.app import webhwp_templates as wt
    data = wt.build(wt.GRANT["budget_change"])
    data, _ = ww.write_section(data, "절:1. 변경 사유", [("text", "<표 1> 비목별 예산\n1) 본문 줄입니다 길게 씁니다")])
    out, msg = eo.text_style(data, {"role": "표 제목", "align": "가운데"})
    assert "문단 1개" in msg
    with pytest.raises(eo.EditError):
        eo.text_style(data, {"role": "엉뚱", "align": "가운데"})


def test_write_section_markdown_table_gets_kind(monkeypatch):
    """에이전트 본문 쓰기(마크다운 표 → hwpx)도 갈래 디자인 — 예산표 금액 오른쪽·열 폭은 값 꼴 비율."""
    from zzaimy.app import webhwp_templates as wt
    from zzaimy.app import webhwp_write as ww
    monkeypatch.setattr(ts, "current", lambda: _prof("budget", **{"align:money": "RIGHT", "head_fill:family": "#D8D8D8"}))
    data = wt.build(wt.GRANT["budget_change"])
    body = "1) 소요 예산\n| 비목 | 금액(천원) | 산출 근거 |\n|---|---|---|\n| 인건비 | 24000 | 연구원 2명 × 12개월 인건비 |\n| 합계 | 24000 |  |"
    out, _ = ww.write_section(data, "절:1. 변경 사유", ww.parse_body(body))
    z = zipfile.ZipFile(io.BytesIO(out))
    sec, hdr = z.read("Contents/section0.xml").decode(), z.read("Contents/header.xml").decode()
    tbl = next(m.group(0) for m in ws._TBL.finditer(sec) if "인건비" in m.group(0))
    # 양식에 음영 있는 서식 표가 있으면(공통 양식은 #E5E5E5 머리) 그 모양이 캔 값(#D8D8D8)에 앞선다 — 정렬·열 폭은 갈래 값
    assert _fill_of(tbl, hdr, "비목") == "#E5E5E5"
    money = next(t for t in ("24000", "24,000") if any(ws._text(m.group(0)) == t for m in ws._TC.finditer(tbl)))
    assert _align_of(tbl, hdr, money) == "RIGHT"
    widths = [int(w) for w in re.findall(r'<hp:cellSz width="(\d+)"', tbl)[:3]]
    assert widths[2] > widths[1]                                     # 긴 근거 열이 금액 열보다 넓다


def test_pt_delta_against_all_tables():
    prof = _prof("stats", body_pt=9.0)
    prof["all"]["values"]["body_pt"] = {"mode": 10.0, "share": 0.4, "n": 100}
    assert ts.pt_delta("stats", "body", prof) == -1.0 and ts.pt_delta("roles", "body", prof) == 0.0
