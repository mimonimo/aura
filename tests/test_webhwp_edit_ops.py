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
