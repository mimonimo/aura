"""기안기 도구 튼튼함 — 무작위 시험(시드 고정)·입력 방어·멱등성, 표 갈래 빌더·디자인 값(house_style 한 곳 → 한글·구글).

무작위 시험: 실제 양식(공통 양식 전부 + 저장소 시험용 hwpx)에 도구 연산을 무작위 차례·인자로 적용하고, 매번
(a) 결과가 열리는 꼴(ZIP·웰폼드·참조 id·itemCnt·표 격자·누름틀 쌍 — webhwp_edit_ops.check_bytes)인지,
(b) 실패한 연산은 까닭 있는 오류(EditError)로 끝나고 들어온 파일을 그대로 두는지(ArgError = 인자를 못 읽은 고장으로 셈)를 본다.
"""
from __future__ import annotations

import io
import random
import re
import zipfile

import pytest

from zzaimy.app import webhwp_edit_ops as eo
from zzaimy.app import webhwp_fields as wf
from zzaimy.app import webhwp_style as ws
from zzaimy.app import webhwp_templates as wt
from zzaimy.app import webhwp_write as ww
from zzaimy.export import house_style as hs

WEIRD = ["", " ", "a&b<c>\"'", "\x01\x02제어\x0b", "😀이모지👍", "가" * 3000, "줄\n바꿈\r\n둘", "]]>", "&amp;", "￾끝",
         "1. 항목\n가. 하위", "| a | b |\n| 1 | 2 |", "```도식\nA → B\nC\n```", "보통 글"]


def _corpus() -> dict[str, bytes]:
    from tests import test_hwpx_fill as thf
    from zzaimy.ingest.gdocs_templates import SPECS
    out = {sid: wt.build(spec) for sid, spec in [*SPECS.items(), *wt.OFFICIAL.items(), *wt.GRANT.items()]}
    import pathlib
    import tempfile
    d = pathlib.Path(tempfile.mkdtemp())
    out["fixture_fill"] = thf._hwpx(d).read_bytes()
    out["fixture_fill_fields"] = wf.fieldize(thf._hwpx(d, two_sections=True).read_bytes())[0]
    return out


def _sec(data: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode("utf-8")


def _hdr(data: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read("Contents/header.xml").decode("utf-8")


def _op(rng: random.Random, data: bytes):
    """연산 하나 — 반은 맞는 인자, 반은 이상한 인자."""
    doc = eo._Doc(data)
    fields = eo.field_names(doc) or ["없음"]
    tl = eo.list_tables(doc)
    f = rng.choice(fields + ["없는 필드", ""])
    txt = lambda: rng.choice(WEIRD)
    bad = lambda: rng.choice([0, -1, 999, "x", None, 1.5, [], {}])
    k = rng.choice(["ti", "te", "te", "te", "ps", "pn", "hf", "pb", "ts", "fn", "rt", "sf", "ws", "st"])
    good = rng.random() < 0.6
    if k == "te" and tl and good:
        t = rng.choice(tl)
        op = rng.choice(list(eo.TABLE_OPS))
        r, c = rng.randint(1, t["rows"]), rng.randint(1, t["cols"])
        a = {"op": op, "table": t["no"], "row": r, "col": c, "count": rng.randint(1, 3), "where": rng.choice(["above", "below", "left", "right"])}
        a.update({"set_cells": {"start": [r, c], "values": [[txt()]]},
                  "merge": {"cells": [r, c, min(t["rows"], r + rng.randint(0, 2)), min(t["cols"], c + rng.randint(0, 2))]},
                  "split": {"cells": [r, c], "rows": rng.choice([None, 2]), "cols": rng.choice([None, 2])},
                  "widths": {"widths": [rng.randint(1, 5) for _ in range(t["cols"])]},
                  "head": {"rows": rng.randint(0, max(0, min(2, t["rows"] - 1)))},
                  "align": {"align": "오른쪽", "cells": [r, c, t["rows"], t["cols"]]}, "shade": {"fill": "#DDEEFF"},
                  "valign": {"valign": "가운데"}}.get(op, {}))
        return k, a, lambda d: eo.table_edit(d, a)
    if k == "te":
        a = {"op": rng.choice(list(eo.TABLE_OPS) + ["bad"]), "table": rng.choice([1, 0, 99, {"head": "구분"}, {"field": f}, None]),
             "row": bad(), "col": bad(), "count": bad(), "values": rng.choice([[[txt()]], "x", [[]]]), "start": rng.choice([[1], "x", [0, 0]]),
             "cells": rng.choice([[1, 1, 99, 99], [0, 0, 1, 1], "x", [1]]), "rows": bad(), "cols": bad(), "widths": rng.choice([[-1, 2], "x", ["a"]]),
             "align": "bad", "valign": "x", "fill": "bad"}
        return k, a, lambda d: eo.table_edit(d, a)
    if k == "ti":
        a = ({"at": rng.choice([{"field": f}, {"field": f, "where": "end"}, {}]), "values": [[txt() for _ in range(3)] for _ in range(rng.randint(1, 4))],
              "head": rng.choice([None, False, ["가", "나", "다"]])} if good else
             {"at": rng.choice([{"after_text": txt()}, None, "x"]), "rows": bad(), "cols": rng.choice([31, "a", 0]), "values": rng.choice(["x", [[None, 1]]]),
              "head": ["h"] * rng.choice([1, 40]), "widths": rng.choice([[0, 1], "x", [float("nan")]])})
        return k, a, lambda d: eo.table_insert(d, a)
    if k == "ps":
        a = {"paper": rng.choice(["A4", "B5", "Z9", None]), "orientation": rng.choice(["가로", "세로", "x", None]),
             "margin": rng.choice(["보통", "x", None]), "margins_mm": rng.choice([None, {"left": 10}, {"top": -5}, {"left": "x"}, "x"])}
        return k, a, lambda d: eo.page_setup(d, a)
    if k == "pn":
        a = {"position": rng.choice(["아래 가운데", "없음", "x"]), "format": rng.choice(["1", "가", "x"]), "start": rng.choice([None, 3, "x"])}
        return k, a, lambda d: eo.page_number(d, a)
    if k == "hf":
        a = {"header": rng.choice([None, "", txt()]), "footer": rng.choice([None, txt()])}
        return k, a, lambda d: eo.header_footer(d, a)
    if k == "pb":
        a = {"field": rng.choice([f, None]), "text": rng.choice([None, txt(), "1."]), "on": rng.choice([True, False])}
        return k, a, lambda d: eo.page_break(d, a)
    if k == "ts":
        a = {"scope": rng.choice(["all", {"field": f}, "x"]), "pt": rng.choice([None, 12, 0, 100, "x"]), "bold": rng.choice([None, True]),
             "font": rng.choice([None, "바탕", "<&\">"]), "line_spacing": rng.choice([None, 160, 9999]), "align": rng.choice([None, "가운데", "x"]),
             "tables": rng.choice([True, False])}
        return k, a, lambda d: eo.text_style(d, a)
    if k == "fn":
        a = {"field": rng.choice([f, None]), "after_text": rng.choice([None, "1.", txt()]), "text": txt()}
        return k, a, lambda d: eo.footnote(d, a)
    if k == "rt":
        a = {"find": rng.choice(["", "1.", "사업", "&", "<", txt()]), "replace": txt(), "scope": rng.choice(["all", None, {"field": f}])}
        return k, a, lambda d: eo.replace_text(d, a)
    if k == "sf":
        vals = {rng.choice(fields): txt() for _ in range(rng.randint(0, 3))}
        return k, vals, lambda d: ww.set_fields(d, vals)
    if k == "ws":
        name, body = rng.choice([x for x in fields if x.startswith("절:")] or fields), txt()
        return k, (name, body), lambda d: ww.write_section(d, name, ww.parse_body(body), {})
    a = {"which": rng.choice([None, [1], [99]]), "opts": rng.choice([None, {"head_fill": "파랑"}, {"outer": "x"}, {"head_bottom": "DOUBLE"}, {"zebra": True}])}
    return k, a, lambda d: (ws.apply(d, a["which"], a["opts"]), "")


def test_random_ops_keep_files_openable():
    """공통 양식 전부 + 시험용 hwpx 에 연산 25번씩(시드 고정) — 결과는 늘 열리는 꼴, 실패는 까닭 있는 오류뿐."""
    rng = random.Random(20261023)
    random.seed(20261023)
    applied, refused = 0, 0
    for name, data in _corpus().items():
        assert eo.check_bytes(data) == [], name
        for i in range(25):
            kind, a, fn = _op(rng, data)
            before = data
            try:
                out = fn(data)
            except eo.ArgError as e:                          # 인자를 못 읽어 생각지 못한 예외가 났다 = 고칠 고장
                pytest.fail(f"{name} {i} {kind} {a!r:.200}: {e}")
            except (eo.EditError, KeyError):
                refused += 1
                assert data is before
                continue
            new = out[0] if isinstance(out, tuple) else out
            probs = eo.check_bytes(new)
            assert probs == [], f"{name} {i} {kind} {a!r:.200}: {probs[:3]}"
            data = new
            applied += 1
    assert applied > 200 and refused > 50                   # 성공·거절이 고루 나왔다


def test_check_bytes_catches_broken_fields_and_grid():
    data = wt.build(wt.GRANT["budget_change"])
    x = _sec(data)
    end = re.search(r'<hp:ctrl><hp:fieldEnd\b[^>]*/></hp:ctrl>', x)
    broken = x[:end.start()] + x[end.end():]
    probs = eo.structure_problems(_repack(data, broken))
    assert any("누름틀" in p for p in probs)
    grid = re.sub(r'<hp:cellAddr colAddr="1" rowAddr="0"/>', '<hp:cellAddr colAddr="0" rowAddr="0"/>', x, count=1)
    assert any("격자" in p for p in eo.structure_problems(_repack(data, grid)))


def _repack(data: bytes, sec: str) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, sec.encode() if info.filename == "Contents/section0.xml" else zin.read(info.filename),
                       compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    return buf.getvalue()


@pytest.mark.parametrize("text", ["\x01\x02제어\x0b", "￾끝", "😀이모지", "a & b < c > \"d\"", "]]>", "줄\r\n바꿈"])
def test_weird_text_is_cleaned_not_breaking(text):
    """XML 이 받지 않는 글자는 빼고, 이스케이프가 필요한 글자·이모지는 그대로 들어간다 — 칸·절·찾아 바꾸기·각주·머리말."""
    d = wt.build(wt.GRANT["trip_report"])
    d1, done = ww.set_fields(d, {"칸:장소": text})
    d2, _ = ww.write_section(d1, "절:1. 주요 내용", ww.parse_body(text + "\n둘째 줄"), {})
    d3, _ = eo.header_footer(d2, {"header": text})
    d4, _ = eo.footnote(d3, {"field": "칸:장소", "text": text + " 각주"})
    for x in (d1, d2, d3, d4):
        assert eo.check_bytes(x) == []
    got = [f["text"] for f in ww.fields(d1) if f["name"] == "칸:장소"][0]
    assert got.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"') \
        .replace(" ", "") == wf.clean_text(text).replace("\n", "").replace(" ", "")
    assert "\x01" not in _sec(d4) and "￾" not in _sec(d4)


def test_bad_args_give_reasons_not_crashes():
    d = wt.build(wt.GRANT["budget_change"])
    for fn, a in [(eo.table_edit, {"table": 1, "op": "add_rows", "count": "x"}),
                  (eo.table_edit, {"table": 2, "op": "set_cells", "values": [["a"]], "start": [1]}),
                  (eo.table_edit, {"table": 2, "op": "split", "cells": [1, 1], "rows": "x"}),
                  (eo.table_edit, {"table": 2, "op": "widths", "widths": ["a", 1]}),
                  (eo.table_edit, {"table": 99, "op": "head"}),
                  (eo.table_insert, {"values": [["a", "b"]], "widths": ["x", 1]}),
                  (eo.table_insert, {"rows": 2, "head": ["h"] * 40}),
                  (eo.table_insert, {"rows": 500, "cols": 3}),
                  (eo.page_setup, {"margins_mm": "x"}),
                  (eo.text_style, {"pt": 0}),
                  (eo.replace_text, {"find": ""}),
                  (eo.table_edit, None)]:
        with pytest.raises(eo.EditError) as e:
            fn(d, a)
        assert str(e.value)
    with pytest.raises(eo.EditError):
        eo.page_setup(b"not a zip", {"paper": "A4"})


def test_same_op_twice_is_stable():
    """같은 서식을 두 번 입혀도 결과가 같다(header 가 늘지 않는다) — 같은 모양은 이미 있는 id 를 다시 쓴다."""
    d = wt.build(wt.GRANT["budget_change"])
    a = ws.apply(d)
    assert ws.apply(a) == a and len(_hdr(a)) == len(_hdr(d))
    x, _ = eo.table_edit(d, {"table": 2, "op": "head", "rows": 1})
    y, _ = eo.table_edit(x, {"table": 2, "op": "head", "rows": 1})
    assert _hdr(x) == _hdr(y)
    x, _ = eo.text_style(d, {"pt": 12, "bold": True})
    y, msg = eo.text_style(x, {"pt": 12, "bold": True})
    assert y == x and "이미" in msg
    x, _ = eo.page_number(d, {})
    assert eo.page_number(x, {})[0] == x
    h1, _ = eo.header_footer(d, {"header": "머리"})
    h2, _ = eo.header_footer(h1, {"header": "머리"})
    assert _sec(h2).count("<hp:header ") == 1


# ── 표 갈래 빌더 ──
def _tables(data: bytes) -> list[str]:
    return [m.group(0) for m in ws._TBL.finditer(_sec(data))]


def _cell(tbl: str, r: int, c: int) -> str:
    return next(m.group(0) for m in ws._TC.finditer(tbl) if f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/>' in m.group(0))


def _align(data: bytes, cell: str) -> str:
    pp = re.search(r'<hp:p\b[^>]*paraPrIDRef="(\d+)"', cell).group(1)
    return re.search(rf'<hh:paraPr\b[^>]*\bid="{pp}".*?<hh:align horizontal="(\w+)"', _hdr(data), re.S).group(1)


def _fill(data: bytes, cell: str) -> str:
    bf = re.search(r'borderFillIDRef="(\d+)"', cell).group(1)
    body = re.search(rf'<hh:borderFill\b[^>]*\bid="{bf}".*?</hh:borderFill>', _hdr(data), re.S).group(0)
    m = re.search(r'faceColor="([^"]+)"', body)
    return m.group(1) if m else "none"


def _bottom(data: bytes, cell: str) -> tuple[str, str]:
    bf = re.search(r'borderFillIDRef="(\d+)"', cell).group(1)
    body = re.search(rf'<hh:borderFill\b[^>]*\bid="{bf}".*?</hh:borderFill>', _hdr(data), re.S).group(0)
    m = re.search(r'<hh:bottomBorder type="(\w+)" width="([^"]+)"', body)
    return m.group(1), m.group(2)


def test_table_kind_builders_xml():
    spec = {"id": "t", "title": "t", "cover": ["시험"], "blocks": [
        wt.summary_table([["사업명", "○○"], ["기간", "2027. 3."], ["대상", "재학생"]], cols=4),
        wt.schedule_table(["설계", "운영"], months=["3월", "4월", "5월"], marks={"설계": ["3월"], "운영": [2, 3]}),
        wt.budget_table([["인건비", "4500000", "강사료"], ["여비", "1500", ""]], unit="천원", total="4501500"),
        {"table": {"columns": ["구분", "금액(원)", "비고"], "rows": [["가", "1200", ""]]}}]}
    d = wt.build(spec)
    assert eo.check_bytes(d) == []
    summ, sched, budget, plain = _tables(d)
    for t in (summ, sched, budget, plain):                    # 열 폭 합 = 표 폭(본문 폭)
        first = [int(w) for w in re.findall(r'<hp:cellSz width="(\d+)"', re.search(r"<hp:tr>.*?</hp:tr>", t, re.S).group(0))]
        spans = [int(s) for s in re.findall(r'colSpan="(\d+)"', re.search(r"<hp:tr>.*?</hp:tr>", t, re.S).group(0))]
        assert sum(first) == int(re.search(r'<hp:sz width="(\d+)"', t).group(1)) == wt.TEXT_W, spans
    # 요약 표 — 머리줄 없음, 라벨 열(0·2)만 음영, 홀수 마지막 값은 끝까지 합침
    assert _fill(d, _cell(summ, 0, 0)) == hs.HOUSE["label_fill"] and _fill(d, _cell(summ, 0, 2)) == hs.HOUSE["label_fill"]
    assert _fill(d, _cell(summ, 0, 1)) == "none" and 'colSpan="3"' in _cell(summ, 1, 1)
    # 일정표 — 추진 달 칸만 연남색
    assert _fill(d, _cell(sched, 1, 1)) == hs.TABLE["month_fill"] and _fill(d, _cell(sched, 1, 2)) == "none"
    assert _fill(d, _cell(sched, 2, 2)) == hs.TABLE["month_fill"] == _fill(d, _cell(sched, 2, 3))
    # 예산표 — 머리줄 음영·가운데·아래 선 굵게, 금액 오른쪽·천 단위, 합계 줄 연회색·굵게(계산하지 않고 받은 값)
    assert _fill(d, _cell(budget, 0, 1)) == hs.HOUSE["head_fill"] and _align(d, _cell(budget, 0, 1)) == "CENTER"
    assert _bottom(d, _cell(budget, 0, 1)) == ("SOLID", hs.TABLE["head_rule_width"])
    assert "4,500,000" in _cell(budget, 1, 1) and _align(d, _cell(budget, 1, 1)) == "RIGHT"
    assert "4,501,500" in _cell(budget, 3, 1) and _fill(d, _cell(budget, 3, 0)) == hs.TABLE["total_fill"]
    cp = re.search(r'charPrIDRef="(\d+)"', _cell(budget, 3, 0)).group(1)
    assert re.search(rf'<hh:charPr\b[^>]*\bid="{cp}".*?</hh:charPr>', _hdr(d), re.S).group(0).count("<hh:bold/>") == 1
    # 갈래 없는 표도 같은 규칙 — 머리가 금액(원)인 열의 수는 오른쪽, 빈 비고 옆 수 칸은 라벨로 보지 않는다
    assert _align(d, _cell(plain, 1, 1)) == "RIGHT" and _fill(d, _cell(plain, 1, 1)) == "none"
    with pytest.raises(ValueError):
        hs.budget_grid([])


def test_heading_types_and_cover():
    spec = {"id": "c", "title": "c", "cover": {"title": "운영 계획서", "subtitle": "(부제)", "date": "2027. 3.", "org": "영남이공대학교"},
            "blocks": [{"toc": True}, {"h": 1, "text": "Ⅰ. 개요"}, {"h": 2, "text": "1. 배경"}, {"h": 3, "text": "가. 현황"}]}
    d = wt.build(spec)
    assert eo.check_bytes(d) == []
    x, h = _sec(d), _hdr(d)

    def size(text):
        p = re.search(rf'<hp:p\b[^>]*>(?:(?!<hp:p\b).)*?<hp:t>{re.escape(text)}</hp:t>', x, re.S).group(0)
        cp = re.findall(r'charPrIDRef="(\d+)"', p)[-1]
        return int(re.search(rf'<hh:charPr\b[^>]*\bid="{cp}"[^>]*\bheight="(\d+)"', h).group(1)) / 100
    assert size("운영 계획서") == hs.TYPE["cover_title"]["pt"] and size("Ⅰ. 개요") == hs.TYPE["chapter"]["pt"]
    assert size("1. 배경") == hs.TYPE["h1"]["pt"] and size("가. 현황") == hs.TYPE["h2"]["pt"]
    # 목차 줄은 절 필드가 되지 않고(점선), 첫 장은 새 쪽에서
    names = [f["name"] for f in ww.fields(d)]
    assert "절:가. 현황" in names and not any("·····" in n for n in names)
    assert re.search(r'<hp:p\b[^>]*pageBreak="1"[^>]*>(?:(?!<hp:p\b).)*?목 차', x, re.S)
    # 바탕글(본문) 글자 크기도 TYPE
    st = re.search(r'<hh:style\b[^>]*\bid="0"[^>]*charPrIDRef="(\d+)"', h).group(1)
    assert re.search(rf'<hh:charPr\b[^>]*\bid="{st}"[^>]*\bheight="(\d+)"', h).group(1) == str(hs.TYPE["body"]["pt"] * 100)


def test_design_values_shared_by_hwp_and_docs(monkeypatch):
    """디자인 값을 house_style 에서 바꾸면 한글 표와 독스 표가 함께 따라온다(머리줄 아래 선·합계 음영·금액 정렬)."""
    from zzaimy.ingest import gdocs
    monkeypatch.setitem(hs.TABLE, "head_rule_width", "0.5 mm")
    monkeypatch.setitem(hs.TABLE, "total_fill", "#DDDDDD")
    d = wt.build({"id": "s", "title": "s", "blocks": [wt.budget_table([["인건비", "1000"]], total="1000")]})
    t = _tables(d)[0]
    assert _bottom(d, _cell(t, 0, 0))[1] == "0.5 mm" and _fill(d, _cell(t, 2, 0)) == "#DDDDDD"
    sec = {"index": 2, "heading": "절", "start": 10, "end": 30}
    reqs = gdocs._section_append_requests(sec, 100, [("table", [["비목", "금액(천원)"], ["인건비", "1,000"], ["합계", "1,000"]])])
    cs = [r["updateTableCellStyle"] for r in reqs if "updateTableCellStyle" in r]
    rule = [c for c in cs if set(c["tableCellStyle"]) == {"borderBottom"} and c["tableRange"]["tableCellLocation"]["rowIndex"] == 0]
    assert rule[0]["tableCellStyle"]["borderBottom"]["width"]["magnitude"] == hs.mm_to_pt("0.5 mm")
    total = [c for c in cs if "backgroundColor" in c["tableCellStyle"] and c["tableRange"]["tableCellLocation"]["rowIndex"] == 2]
    assert total and total[0]["tableCellStyle"]["backgroundColor"]["color"]["rgbColor"] == hs.rgb("#DDDDDD")
    ends = [r for r in reqs if r.get("updateParagraphStyle", {}).get("paragraphStyle", {}).get("alignment") == "END"]
    assert len(ends) == 2                                                   # 금액 두 칸
    assert hs.docs_type("h1")["pt"] == hs.TYPE["h1"]["pt"] and hs.docs_house()["head_rule_pt"] == hs.mm_to_pt("0.5 mm")


def test_fit_widths_bounds():
    w = hs.fit_widths([["항목", "가"], ["내용", ""]], 42520, [1, 4])
    assert sum(w) == 42520 and w[1] > w[0] * 3                              # 2열은 값 칸이 넓다
    w = hs.fit_widths([["구분"], ["아주 긴 설명이 들어가는 칸입니다 " * 3], ["비고"]], 42520)
    assert sum(w) == 42520 and max(w) <= int(42520 * hs.TABLE["col_max_share"]) + 3
    w = hs.fit_widths([[f"머리{i}"] for i in range(30)], 42520)             # 하한 합이 표 폭을 넘으면 비율로
    assert sum(w) == 42520 and len(w) == 30
