"""기안기 편집 도구 — 파일 직접 고치기(페이지 설정·표 만들기·표 편집·쪽 번호·머리말·쪽 나누기·글자·각주·그림·찾아 바꾸기)와
열린 기안기 명령 줄(넣기·가져가기·결과·허용 목록 밖 거절), 판 남기기·실패 시 원본 유지, 도구 정의."""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_webhwp import _login, _setup
from zzaimy.app import webhwp, webhwp_agent as wa
from zzaimy.app import webhwp_edit_ops as eo
from zzaimy.app import webhwp_templates as wt


def _doc() -> bytes:
    return wt.build(wt.GRANT["budget_change"])


def _sec(data: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode("utf-8")


def _ok(data: bytes) -> bytes:
    assert eo.check_bytes(data) == []
    return data


def test_page_setup_landscape_margins():
    d, msg = eo.page_setup(_doc(), {"paper": "B5", "orientation": "가로", "margin": "좁게", "margins_mm": {"left": 12.5}})
    _ok(d)
    pp = re.search(r"<hp:pagePr\b.*?</hp:pagePr>", _sec(d), re.S).group(0)
    assert 'landscape="NARROWLY"' in pp and f'width="{eo.hu(182)}"' in pp and f'height="{eo.hu(257)}"' in pp
    assert f'left="{eo.hu(12.5)}"' in pp and f'top="{eo.hu(10)}"' in pp and f'header="{eo.hu(10)}"' in pp
    assert "가로" in msg and eo.current_page(d)["paper"] == "B5"
    with pytest.raises(eo.EditError):
        eo.page_setup(_doc(), {"paper": "A9"})
    with pytest.raises(eo.EditError):
        eo.page_setup(_doc(), {})


def test_table_insert_and_edit():
    d, msg = eo.table_insert(_doc(), {"at": {"field": "1. 변경 사유"}, "values": [["구분", "금액"], ["가", "1"]], "widths": [3, 1]})
    _ok(d)
    no = int(re.search(r"표(\d+)", msg).group(1))
    t = eo.list_tables(eo._Doc(d))[no - 1]
    assert (t["rows"], t["cols"], t["head"]) == (2, 2, "구분 | 금액")
    # 절 필드 바로 뒤에 들어갔다(다음 절 제목보다 앞)
    x = _sec(d)
    assert x.find('name="절:1. 변경 사유"') < x.find(">구분<") < x.find(">2. 변경 내역<")
    tb = {"head": "구분"}
    d, _ = eo.table_edit(d, {"table": tb, "op": "add_rows", "row": 2, "count": 2})
    d, _ = eo.table_edit(_ok(d), {"table": tb, "op": "add_cols", "col": 1, "where": "left"})
    d, msg = eo.table_edit(_ok(d), {"table": tb, "op": "merge", "cells": [2, 2, 3, 3]})
    _ok(d)
    assert "4행×3열" in msg
    tbl = eo._Table(_sec(d)[eo.list_tables(eo._Doc(d))[no - 1]["s"]:eo.list_tables(eo._Doc(d))[no - 1]["e"]])
    big = tbl.covering(1, 1)
    assert (big["r"], big["c"], big["rs"], big["cs"]) == (1, 1, 2, 2) and len(tbl.cells) == 12 - 3
    assert 'rowCnt="4"' in tbl.to_xml() and 'colCnt="3"' in tbl.to_xml()
    # 합친 칸에 걸친 범위는 거절, 나누면 다시 4칸
    with pytest.raises(eo.EditError):
        eo.table_edit(d, {"table": tb, "op": "merge", "cells": [1, 2, 2, 2]})
    d, _ = eo.table_edit(d, {"table": tb, "op": "split", "cells": [2, 2]})
    d, _ = eo.table_edit(_ok(d), {"table": tb, "op": "split", "cells": [1, 1], "cols": 2})
    d, _ = eo.table_edit(_ok(d), {"table": tb, "op": "set_cells", "start": [2, 1], "values": [["가", "나"]]})
    d, _ = eo.table_edit(_ok(d), {"table": tb, "op": "delete_rows", "row": 4})
    d, msg = eo.table_edit(_ok(d), {"table": tb, "op": "delete_cols", "col": 2})
    _ok(d)
    assert "3행×3열" in msg
    d, _ = eo.table_edit(d, {"table": tb, "op": "widths", "widths": [1, 2, 1]})
    d, _ = eo.table_edit(_ok(d), {"table": tb, "op": "shade", "row": 2, "fill": "노랑"})
    d, _ = eo.table_edit(_ok(d), {"table": tb, "op": "head", "rows": 1})
    d, _ = eo.table_edit(_ok(d), {"table": tb, "op": "align", "col": 3, "align": "오른쪽"})
    _ok(d)
    assert "#FFF2CC" in zipfile.ZipFile(io.BytesIO(d)).read("Contents/header.xml").decode()
    view = eo.table_view(d, {"table": tb})
    assert view.startswith(f"표{no} — 3행×3열") and "가1" in view   # 합친 칸의 글은 이어 붙고 풀어도 첫 칸에 남는다
    with pytest.raises(eo.EditError):
        eo.table_edit(d, {"table": tb, "op": "delete_rows", "row": 1, "count": 9})


def test_page_number_header_footer_break_footnote():
    d, _ = eo.page_number(_doc(), {"position": "아래 가운데", "dash": True, "start": 3})
    d, _ = eo.page_number(_ok(d), {"position": "위 오른쪽", "format": "I"})
    x = _sec(_ok(d))
    assert x.count("<hp:pageNum ") == 1 and 'pos="TOP_RIGHT" formatType="ROMAN_CAPITAL"' in x and 'page="3"' in x
    d, _ = eo.header_footer(d, {"header": "영남이공대학교", "footer": "대외비"})
    d, _ = eo.header_footer(_ok(d), {"header": "새 머리말"})
    x = _sec(_ok(d))
    assert x.count("<hp:header ") == 1 and "새 머리말" in x and "대외비" in x
    d, _ = eo.page_break(d, {"field": "절:2. 변경 내역"})
    x = _sec(_ok(d))
    assert re.search(r'<hp:p\b[^>]*pageBreak="1"[^>]*>(?:(?!</hp:p>).)*2\. 변경 내역', x, re.S)
    d, msg = eo.footnote(d, {"field": "칸:사업명", "text": "근거: 협약서"})
    x = _sec(_ok(d))
    assert "<hp:footNote " in x and "근거: 협약서" in x and "각주 1)" in msg


def test_text_style_replace_picture():
    d, msg = eo.text_style(_doc(), {"scope": "all", "font": "맑은 고딕", "pt": 11, "line_spacing": 170})
    _ok(d)
    hdr = zipfile.ZipFile(io.BytesIO(d)).read("Contents/header.xml").decode()
    assert hdr.count('face="맑은 고딕"') == len(re.findall(r"<hh:fontface\b", hdr)) and 'height="1100"' in hdr
    assert 'value="170"' in hdr and "문단" in msg
    d, msg = eo.text_style(d, {"scope": {"field": "절:1. 변경 사유"}, "align": "가운데", "indent_mm": -3})
    _ok(d)
    d, msg = eo.replace_text(d, {"find": "변경", "replace": "조정"})
    _ok(d)
    assert "곳을 바꿨다" in msg and "조정 사유" in _sec(d) and 'name="절:1. 변경 사유"' in _sec(d)
    with pytest.raises(eo.EditError):
        eo.replace_text(d, {"find": "없는 낱말"})
    png = io.BytesIO()
    from PIL import Image
    Image.new("RGB", (200, 100), (10, 20, 30)).save(png, "PNG")
    d2, msg = eo.picture(d, png.getvalue(), {"width_mm": 60, "at": {"after_text": "조정 사유"}})
    _ok(d2)
    z = zipfile.ZipFile(io.BytesIO(d2))
    assert any(n.startswith("BinData/image") for n in z.namelist()) and "<hp:pic " in _sec(d2)
    with pytest.raises(eo.EditError):
        eo.picture(d, b"not an image", {})


def test_apply_saves_version_and_keeps_original_on_failure(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    fid = webhwp.add(db, base, "기획처", "편집.hwpx", _doc(), owner="kim")
    p = Path(webhwp.get(db, fid)["path"])
    before = p.read_bytes()
    out = eo.apply(db, base, fid, lambda d: eo.page_setup(d, {"orientation": "가로"}))
    assert "가로" in out and p.read_bytes() != before
    vers = webhwp._versions(p.parent / ".판", p)
    assert vers and vers[-1].read_bytes() == before                       # 고치기 전 판
    assert webhwp.meta(db, fid)["saved_by"] == "AURA"
    # 실패(없는 필드) — 파일 그대로
    now = p.read_bytes()
    out = eo.apply(db, base, fid, lambda d: eo.table_insert(d, {"at": {"field": "없는 절"}, "rows": 2, "cols": 2}))
    assert out.startswith("바꾸지 않았다") and p.read_bytes() == now
    # 점검을 통과하지 못하는 결과 — 파일 그대로
    out = eo.apply(db, base, fid, lambda d: (d.replace(b"PK", b"PK", 1)[:-40], "망가짐"))
    assert out.startswith("바꾸지 않았다") and p.read_bytes() == now
    # 되돌리기 — AURA 가 고치기 전으로
    assert "되돌렸다" in eo.undo(db, base, fid) and p.read_bytes() == before


def test_apply_holds_file_lock_from_read_to_save(tmp_path, monkeypatch):
    """AURA 의 읽고-고쳐-저장 사이에 다른 저장(칸 밖 글 살리기·칸 글 써 넣기)이 끼면 옛 판으로 덮는다 — 같은 파일 잠금 안에서 한다(11-13)."""
    from zzaimy.app import webhwp_coedit as ce
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    fid = webhwp.add(db, base, "기획처", "편집.hwpx", _doc(), owner="kim")
    seen = []

    def fn(d):
        seen.append(ce.file_lock(fid).locked())
        return eo.page_setup(d, {"orientation": "가로"})
    assert "가로" in eo.apply(db, base, fid, fn)
    assert seen == [True] and not ce.file_lock(fid).locked()


def test_live_queue_take_report_and_reject(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    from tests.test_webhwp import _hwpx, _up
    fid = _up(kim, app, "기획처", "명령.hwpx", _hwpx())
    with pytest.raises(eo.EditError):
        eo.push(db, fid, "file_open", {})
    with pytest.raises(eo.EditError):
        eo.push(db, fid, "table_row", {"where": "아래"})                   # 표 동작은 field 가 있어야
    assert not eo.steps_allowed([["run", "FileSave"]]) and not eo.steps_allowed([["exec", "PrintDocument", {}]])
    c = eo.push(db, fid, "page_setup", {"orientation": "가로", "margin": "좁게"})
    assert c["steps"][0][:2] == ["exec", "PageSetup"] and c["steps"][0][2]["PageDef.Landscape"] == 1
    assert not eo.editor_open(db, fid)
    # 명령 줄에 직접 허용 밖 단계를 써 넣어도 편집 화면에 넘기지 않는다
    import json
    v = json.loads(db.get_setting(f"webhwp_cmd:{fid}"))
    v["items"].append({"id": 99, "action": "x", "label": "x", "steps": [["run", "FileSaveAs"]], "by": "AURA", "at": v["items"][0]["at"], "status": "new"})
    db.set_setting(f"webhwp_cmd:{fid}", json.dumps(v))
    got = kim.get(f"/hwp/{fid}/cmd").json()["cmds"]
    assert [g["id"] for g in got] == [c["id"]] and eo.editor_open(db, fid)
    assert kim.get(f"/hwp/{fid}/cmd").json()["cmds"] == []                # 가져간 것은 다시 오지 않는다
    assert webhwp.meta(db, fid).get("aura_undo")                         # 넘기기 전 판
    assert kim.post(f"/hwp/{fid}/cmd/{c['id']}", json={"ok": True}).json()["ok"]
    assert eo.status(db, fid, c["id"])["status"] == "done"
    ps = kim.get(f"/hwp/{fid}/page-setup").json()
    assert "A4" in ps["papers"] and "좁게" in ps["presets"]


def test_edit_tools_and_live_tool(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    hw = wa.HwpTools(db, base, 55, "kim", "기획처", {"id": 1, "name": "시험 사업"}, lambda: [])
    et = eo.EditTools(hw)
    assert "작업본이 없다" in et.run("hwp_page_setup", {"margin": "좁게"})
    tid = next(line.split()[0] for line in hw.forms({"query": "예산 변경"}).splitlines() if "예산 변경" in line)
    hw.start({"form": tid, "title": "편집 도구 시험"})
    assert "페이지 설정" in et.run("hwp_page_setup", {"orientation": "가로", "margin": "좁게"})
    out = et.run("hwp_table_insert", {"at": {"field": "1. 변경 사유", "where": "end"}, "rows": 3, "cols": 2})
    assert "을 만들었다" in out
    no = re.search(r"표(\d+)", out).group(1)
    assert "4행" in et.run("hwp_table_edit", {"table": no, "op": "add_rows", "row": 1})
    assert "합쳤다" in et.run("hwp_table_edit", {"table": no, "op": "merge", "cells": [1, 1, 1, 2]})
    assert "바꾸지 않았다" in et.run("hwp_table_edit", {"table": no, "op": "nope"})
    assert "곳" in et.run("hwp_replace", {"find": "변경"})
    assert "되돌렸다" in et.run("hwp_undo", {})
    # 기안기가 닫혀 있으면 두었다가 열면 이어서
    out = et.run("hwp_live", {"action": "page_number", "params": {"position": "아래 가운데"}})
    assert out.startswith("기안기를 열면 이어서 합니다")
    assert "보내지 않았다" in et.run("hwp_live", {"action": "print", "params": {}})
    # 도구 정의 — 이름·설명·인자 꼴, doc_agent.Tool 로 잇기
    from zzaimy.app import doc_agent
    tools = eo.doc_tools(hw, doc_agent.Tool)
    assert [t.name for t in tools] == [t["name"] for t in eo.TOOLS]
    for t in eo.TOOLS:
        assert t["name"].startswith("hwp_") and "args" in t["desc"] and isinstance(t["args"], dict) and t["label"]
    assert set(eo.LIVE_ACTIONS) <= set(re.findall(r"([a-z_]+)\(", next(t["desc"] for t in eo.TOOLS if t["name"] == "hwp_live")))


def test_landscape_refits_full_width_tables():
    """가로로 바꾸면 본문 폭에 맞춘 표도 새 본문 폭으로(열 비율 유지)."""
    from zzaimy.app import webhwp_edit_ops as eo
    sec = ('<hp:pagePr landscape="WIDELY" width="59528" height="84186"><hp:margin left="8504" right="8504" gutter="0"/></hp:pagePr>'
           '<hp:tbl><hp:sz width="42520" height="100"/><hp:tc><hp:cellSz width="21260" height="10"/></hp:tc>'
           '<hp:tc><hp:cellSz width="21260" height="10"/></hp:tc></hp:tbl>'
           '<hp:tbl><hp:sz width="10000" height="100"/><hp:tc><hp:cellSz width="10000" height="10"/></hp:tc></hp:tbl>')
    old = eo._text_width(sec)
    land = sec.replace('landscape="WIDELY"', 'landscape="NARROWLY"')
    new = eo._text_width(land)
    assert old == 42520 and new == 84186 - 17008
    out, n = eo._refit_tables(land, old, new)
    assert n == 1 and f'<hp:sz width="{new}"' in out and '<hp:sz width="10000"' in out


def test_stale_table_number_after_table_count_changes(tmp_path, monkeypatch):
    """표를 넣거나 빼 번호가 밀린 뒤 옛 번호로 부르면 막고 지금 번호를 알린다(11-13 8차: 표4 를 그림으로 바꾼 뒤 옛 표5 를 부른다는 것이 다른 표를 뺌)."""
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    hw = wa.HwpTools(db, base, 56, "kim", "기획처", {"id": 1, "name": "시험 사업"}, lambda: [])
    et = eo.EditTools(hw)
    tid = next(line.split()[0] for line in hw.forms({"query": "예산 변경"}).splitlines() if "예산 변경" in line)
    hw.start({"form": tid, "title": "번호 밀림 시험"})
    out = et.run("hwp_table_insert", {"at": {"field": "1. 변경 사유", "where": "end"}, "values": [["가", "나"], ["1", "2"]]})
    a_no = int(re.search(r"표(\d+)", out).group(1))
    out = et.run("hwp_table_insert", {"at": {"field": "1. 변경 사유", "where": "end"}, "values": [["마", "바"], ["5", "6"]]})
    c_no = int(re.search(r"표(\d+)", out).group(1))
    assert c_no == a_no + 1
    out = et.run("hwp_table_insert", {"at": {"field": "1. 변경 사유", "where": "start"}, "values": [["다", "라"], ["3", "4"]]})
    b_no = int(re.search(r"표(\d+)", out).group(1))
    assert b_no <= a_no and "번호가 밀렸다" in out
    # 새로 만든 표의 번호는 결과가 알려 준 지금 번호라 그대로 쓴다
    assert "3행" in et.run("hwp_table_edit", {"table": b_no, "op": "add_rows", "row": 1})
    # 옛 번호(마|바 표)로 부르면 막고 지금 번호를 알린다
    out = et.run("hwp_table_edit", {"table": c_no, "op": "add_rows", "row": 1})
    assert out.startswith("넣지 않았다") and f"지금 표{c_no + 1}" in out
    # 그 번호를 다시 본 뒤에는 지금 번호로 받아들인다
    et.run("hwp_table_view", {"table": c_no})
    assert "넣었다" in et.run("hwp_table_edit", {"table": c_no, "op": "add_rows", "row": 1})


def test_set_cells_keeps_header_row_when_start_left_at_top():
    # 11-15 실사용: 출장 보고 경비 표 「항목 | 금액(원) | 비고」 머리줄에 첫 값 행이 덮였다(시작 칸 [1, 1])
    d, msg = eo.table_insert(_doc(), {"at": {"field": "1. 변경 사유"}, "values": [["항목", "금액(원)", "비고"], ["", "", ""], ["", "", ""]]})
    tb = {"head": "항목"}
    d, msg = eo.table_edit(_ok(d), {"table": tb, "op": "set_cells", "start": [1, 1], "values": [["교통비", "확인 필요", ""], ["숙박비", "확인 필요", ""]]})
    _ok(d)
    assert "머리줄" in msg
    t = eo.list_tables(eo._Doc(d))[int(re.search(r"표(\d+)", msg).group(1)) - 1] if re.search(r"표(\d+)", msg) else None
    x = _sec(d)
    assert x.find(">항목<") < x.find(">교통비<") < x.find(">숙박비<") and ">금액(원)<" in x
    # 머리줄을 다시 적어 보낸 값 줄은 버린다
    d2, msg2 = eo.table_insert(_doc(), {"at": {"field": "1. 변경 사유"}, "values": [["항목", "금액"], ["", ""]]})
    d2, msg2 = eo.table_edit(_ok(d2), {"table": {"head": "항목"}, "op": "set_cells", "start": [1, 1], "values": [["항목", "금액"], ["교통비", "1"]]})
    assert "머리줄과 같아" in msg2 and _sec(d2).count(">항목<") == 1
    # overwrite 면 그대로(머리줄을 일부러 고치는 경우)
    d3, _ = eo.table_edit(_ok(d), {"table": tb, "op": "set_cells", "start": [1, 1], "values": [["구분", "금액", "비고"]], "overwrite": True})
    assert ">구분<" in _sec(d3)


def test_set_cells_marker_cell_filled_with_item_name():
    # 11-19 특성화 4.2 사업비 표: 번호만 박힌 칸 「①」「2.」 에 「① 규정 및 …」 을 넣는 것을 라벨 덮기로 보고 통째로 거절했다
    d, _ = eo.table_insert(_doc(), {"at": {"field": "1. 변경 사유"},
                                    "values": [["사업 과제명", "예산", "프로그램", "예산"], ["1.", "", "①", ""], ["2.", "", "②", ""]]})
    tb = {"head": "사업 과제명"}
    d, msg = eo.table_edit(_ok(d), {"table": tb, "op": "set_cells", "start": [2, 1],
                                    "values": [["1. 정원감축 체계 구축", "20", "① 규정 정비", "4"], ["2. 산학협력 체계", "276", "② 현장직무 교육", "220"]]})
    _ok(d)
    x = _sec(d)
    assert ">1. 정원감축 체계 구축<" in x and ">② 현장직무 교육<" in x and ">276<" in x
    # 번호로 시작하지 않는 값은 여전히 막는다(라벨 「계열 수」 를 값으로 덮던 것)
    d2, _ = eo.table_insert(_doc(), {"at": {"field": "1. 변경 사유"}, "values": [["대학 현황 구분", "값"], ["계열 수", ""]]})
    with pytest.raises(eo.EditError):
        eo.table_edit(_ok(d2), {"table": {"head": "대학 현황 구분"}, "op": "set_cells", "start": [2, 1], "values": [["9", ""]]})


def test_set_cells_one_column_count_must_match_filled_rows():
    """채워진 행에 한 열만 넣는데 값 수가 다르면 막는다 — 한 칸씩 밀린 월별 표(11-21 결과보고서 3차)."""
    rows = [["월", "활동", "인원"]] + [[f"{m}월", f"활동{m}", ""] for m in range(3, 8)]
    d, msg = eo.table_insert(_doc(), {"at": {"field": "1. 변경 사유"}, "values": rows})
    tb = {"head": "월"}
    with pytest.raises(eo.EditError, match="어긋난다"):
        eo.table_edit(d, {"table": tb, "op": "set_cells", "start": [2, 3], "values": [["1"], ["2"], ["3"], ["4"], ["5"], ["6"]]})
    d2, _ = eo.table_edit(d, {"table": tb, "op": "set_cells", "start": [2, 3], "values": [["1"], ["2"], ["3"], ["4"], ["5"]]})
    assert "6행: 7월 | 활동7 | 5" in eo.table_view(d2, {"table": tb})


def test_set_cells_refuses_unit_mismatch_and_does_not_shift_onto_filled_row():
    # 34차 A 한번에(11-24): 월별 집행액을 「참여 인원(명)」 열에 넣었고, 머리줄을 「집행액(원)」 으로 바꾸려던 쓰기가 아래로 밀려 3월 값을 덮었다
    d, _ = eo.table_insert(_doc(), {"at": {"field": "1. 변경 사유"}, "values": [["월", "참여 인원(명)", "집행액(원)"], ["", "", ""], ["", "", ""]]})
    tb = {"head": "월"}
    with pytest.raises(eo.EditError, match="사람·건 수 열"):
        eo.table_edit(_ok(d), {"table": tb, "op": "set_cells", "start": [2, 1], "values": [["3월", "8,000,000원 (6.0%)", ""]]})
    with pytest.raises(eo.EditError, match="금액 열"):
        eo.table_edit(_ok(d), {"table": tb, "op": "set_cells", "start": [2, 3], "values": [["24명"]]})
    d, _ = eo.table_edit(_ok(d), {"table": tb, "op": "set_cells", "start": [2, 1], "values": [["3월", "24", "8,000,000"], ["4월", "42명", "19,500,000원"]]})
    d, msg = eo.table_edit(_ok(d), {"table": tb, "op": "set_cells", "start": [1, 2], "values": [["참여자(명)"]]})
    x = _sec(d)
    assert "아래 행부터" not in msg and ">참여자(명)<" in x and ">24<" in x


def test_live_steps_accept_section_names_without_prefix(tmp_path, monkeypatch):
    """「2. 코드 설명」 처럼 접두사(절:) 없이 쓴 이름도 편집 화면 단계에서는 실제 필드 이름으로 — 화면이 「필드를 찾지 못했습니다」 로
    실패해 같은 글을 여러 번 다시 보냈다(11-28 구구단 문서)."""
    from tests.test_webhwp import _up
    from tests.test_webhwp_form_create import DESIGN
    from zzaimy.app import webhwp_form_create as fc
    app = _setup(tmp_path, monkeypatch)
    db = app.state.db
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    clean, _ = fc.validate(DESIGN)
    fid = _up(kim, app, "기획처", "양식.hwpx", fc.build(clean))
    full = next(n for n in eo.field_names(eo._Doc(Path(webhwp.get(db, fid)["path"]).read_bytes())) if n.startswith("절:"))
    c = eo.push(db, fid, "insert_text", {"field": full[2:], "text": "가) 글"})
    assert ["field", full, False] in c["steps"] or any(st[:2] == ["field", full] for st in c["steps"])
