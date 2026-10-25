"""기안기 기본 기능(webhwp_native) — 허용 목록·인자 검사·단위 변환·자리·단계 관문, 명령 줄의 실행기 2(가져가기·주인 우선·읽기 결과·그림),
에이전트 도구 hwp_action·hwp_inspect·hwp_styles, 공동 편집 임대 지키기, 기안기가 열려 있을 때 파일 도구를 기안기 동작으로."""
from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_webhwp import _login, _setup
from zzaimy.app import webhwp, webhwp_agent as wa
from zzaimy.app import webhwp_edit_ops as eo
from zzaimy.app import webhwp_native as nat
from zzaimy.app import webhwp_templates as wt

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x02\x00\x00\x00\x01\x08\x02\x00\x00\x00\x00\x00\x00\x00"
       b"\x00\x00\x00\x00IEND\xaeB`\x82")


# ── 허용 목록·인자 ──
def test_char_shape_units_and_selection():
    steps, field = nat.steps_for("CharShape", {"Bold": True, "Height": 12, "TextColor": "빨강", "FaceNameHangul": "휴먼명조"},
                                 {"field": "제목"})
    assert field == "제목" and steps[0] == ["field", "제목", True] and steps[-1] == ["run", "Cancel"]
    items = steps[1][2]
    assert items == {"Bold": 1, "Height": 1200, "TextColor": 0x0000FF, "FaceNameHangul": "휴먼명조", "FontTypeHangul": 1}
    assert nat.steps_ok(steps)
    steps, _ = nat.steps_for("CharShape", {"ShadeColor": "#FFFF00"}, {"find": "목표", "nth": 2})
    assert steps[0] == ["find", "목표", 2] and steps[1][2] == {"ShadeColor": 0x00FFFF}


@pytest.mark.parametrize("action,params,at,why", [
    ("FileSaveAs", {}, None, "허용 목록에 없다"),
    ("Print", {}, None, "허용 목록에 없다"),
    ("CharShape", {"Bold": True, "Script": "alert(1)"}, {"field": "x"}, "쓸 수 없는 항목"),
    ("CharShape", {"Height": 9999}, {"field": "x"}, "1~4096pt"),
    ("CharShape", {"TextColor": "javascript:"}, {"field": "x"}, "색은"),
    ("CharShape", {"FaceNameHangul": "가" * 41}, {"field": "x"}, "40자"),
    ("CharShape", {}, {"field": "x"}, "하나 이상"),
    ("CharShape", {"Bold": True}, None, "자리(at)"),
    ("ParagraphShape", {"AlignType": "세로"}, {"field": "x"}, "양쪽|왼쪽"),
    ("ParagraphShape", {"LineSpacing": 160.5}, {"field": "x"}, "정수"),
    ("ParagraphShape", {"LineSpacing": "많이"}, {"field": "x"}, "숫자"),
    ("ParagraphShape", {"LineSpacing": True}, {"field": "x"}, "숫자"),
    ("TableCreate", {"Rows": 3}, {"move": "문서 끝"}, "Rows, Cols"),
    ("TableCreate", {"Rows": 301, "Cols": 2}, {"move": "문서 끝"}, "1~300"),
    ("TableCreate", {"Rows": 2, "Cols": 31}, {"move": "문서 끝"}, "1~30"),
    ("TableInsertRowColumn", {"Side": "대각선"}, {"cell": "칸"}, "왼쪽|오른쪽"),
    ("BreakPage", {"x": 1}, {"move": "문서 끝"}, "인자가 없다"),
    ("InsertFootnote", {}, {"field": "x"}, "params.text"),
    ("InsertText", {"Text": "가" * (nat.MAX_TEXT + 1)}, {"field": "x"}, "자까지"),
    ("PageSetup", {"TopMargin": 101}, None, "0~100mm"),
    ("PageSetup", {"paper": "A9"}, None, "paper"),
    ("CharShape", {"Bold": True}, {"where": "아무 데"}, "at 은"),
    ("CharShape", {"Bold": True}, {"find": "가" * 101}, "100자"),
    ("TableMergeCell", {}, {"cell": "칸", "right": 31}, "0~30"),
])
def test_rejects_bad_input(action, params, at, why):
    with pytest.raises(nat.NativeError, match=re.escape(why)):
        nat.steps_for(action, params, at)


def test_table_and_page_actions():
    steps, field = nat.steps_for("TableMergeCell", None, {"cell": "표1:칸", "right": 2, "down": 1})
    assert field == "표1:칸" and ["assert", "table"] in steps
    assert steps.count(["run", "TableRightCell"]) == 2 and steps.count(["run", "TableLowerCell"]) == 1 and ["run", "TableMergeCell"] in steps
    steps, _ = nat.steps_for("TableInsertRowColumn", {"Side": "아래", "Count": 2}, {"cell": "칸"})
    assert ["exec", "TableInsertRowColumn", {"Side": 3, "Count": 2}] in steps         # 기안기에서 확인한 값(왼0 오1 위2 아래3)
    steps, _ = nat.steps_for("TableDeleteRowColumn", {"Type": "행"}, {"cell": "칸"})
    assert ["exec", "TableDeleteRowColumn", {"Type": 1}] in steps                      # 칸(열) 0 · 줄(행) 1
    steps, _ = nat.steps_for("CellBorderFill", {"FillColor": "연노랑", "BorderColorLeft": "검정", "BorderWidthTop": 3}, {"cell": "칸"})
    items = steps[2][2]
    assert items["FillAttr.Type"] == 1 and items["FillAttr.WinBrushFaceColor"] == nat.bgr("#FFF2CC") and items["BorderCorlorLeft"] == 0
    steps, field = nat.steps_for("PageSetup", {"paper": "A4", "Landscape": True, "TopMargin": 20})
    assert field == "" and steps[0] == ["run", "MoveDocBegin"]
    assert steps[1][2]["PageDef.Landscape"] == 1 and steps[1][2]["PageDef.TopMargin"] == round(20 * nat.MM)
    steps, _ = nat.steps_for("HeaderFooter", {"HeaderFooterCtrlType": "꼬리말", "text": "영남이공대학교"})
    assert steps[1] == ["exec", "HeaderFooter", {"HeaderFooterCtrlType": 1}] and steps[2] == ["text", "영남이공대학교"] and steps[3] == ["run", "CloseEx"]
    steps, _ = nat.steps_for("AllReplace", {"FindString": "갑", "ReplaceString": "을"})
    assert steps == [["exec", "AllReplace", {"FindString": "갑", "ReplaceString": "을", "IgnoreMessage": 1, "Direction": 2, "ReplaceMode": 1}]]
    steps, _ = nat.steps_for("PageNumPos", {"DrawPos": "아래 가운데", "NumberFormat": "1", "SideChar": True})
    assert steps[1][2] == {"DrawPos": 5, "NumberFormat": 0, "SideChar": 0x2D}
    steps, _ = nat.steps_for("StyleShortcut3", None, {"field": "절:1. 개요"})
    assert steps == [["field", "절:1. 개요", False], ["run", "StyleShortcut3"]]
    steps, _ = nat.steps_for("InsertPicture", {}, {"move": "문서 끝"}, (40, 20))
    assert steps[-1] == ["picture", 40, 20] and nat.steps_ok(steps)
    for name in nat.NATIVE:
        assert name in nat.describe() or name.startswith("StyleShortcut")


def test_step_gate_rejects_anything_outside_the_list():
    ok = [["field", "x", True], ["exec", "CharShape", {"Bold": 1}], ["run", "Cancel"]]
    assert nat.steps_ok(ok)
    bad = [
        [["run", "FileSaveAs"]], [["run", "Print"]], [["run", "FindDlg"]], [["exec", "PageSetup", {"PageDef.Evil": 1}]],
        [["exec", "CharShape", {"Bold": True}]], [["exec", "CharShape", {"Bold": [1]}]], [["eval", "alert(1)"]],
        [["text", "가" * (nat.MAX_TEXT + 1)]], [["field", "x"]], [["find", "x", 99]], [["assert", "picture"]],
        [["read", ["cookies"]]], [["picture", 0, 10]], [["picture", "a", 10]], [["run", "Cancel"]] * (nat.MAX_STEPS + 1), "run",
    ]
    for steps in bad:
        assert not nat.steps_ok(steps), steps
        assert not eo.steps_allowed(steps, 2), steps
    assert not eo.steps_allowed([["find", "x", 1]], 1)                 # 예전 실행기는 새 단계를 모른다


def test_read_steps():
    steps, field = nat.read_steps(None, None)
    assert steps[0][0] == "read" and "char" in steps[0][1] and field == ""
    steps, field = nat.read_steps({"field": "칸"}, ["char", "cookies"])
    assert steps == [["field", "칸", False], ["read", ["char"]]] and field == "칸" and nat.read_only(steps)
    with pytest.raises(nat.NativeError):
        nat.read_steps(None, ["cookies"])
    assert not nat.read_only(nat.steps_for("BreakPage", None, {"move": "문서 끝"})[0])


# ── 명령 줄: 실행기 2 ──
def test_runner2_queue_owner_and_read_data(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    from tests.test_webhwp import _hwpx, _up
    fid = _up(kim, app, "기획처", "기능.hwpx", _hwpx())
    steps, _ = nat.steps_for("BreakPage", None, {"move": "문서 끝"})
    c = eo.push(db, fid, "BreakPage", {}, steps=steps, label="쪽 나누기", runner=2, owner="kim")
    with pytest.raises(eo.EditError):
        eo.push(db, fid, "x", {}, steps=[["run", "FileSaveAs"]], runner=2)
    # 예전 실행기(편집 화면에 든 것)는 새 명령을 받지 않는다
    assert kim.get(f"/hwp/{fid}/cmd").json()["cmds"] == []
    # 다른 사람의 실행기는 주인(kim)의 실행기가 살아 있으면 가져가지 않는다
    eo._seen_runners  # noqa: B018 — 존재
    db.set_setting(f"webhwp_cmd_r2:{fid}", json.dumps({"kim": __import__("time").time()}))
    assert eo.take(db, fid, "lee", runner=2) == []
    got = kim.get(f"/hwp/{fid}/cmd?runner=2").json()["cmds"]
    assert [g["id"] for g in got] == [c["id"]] and "readonly" not in got[0]
    assert eo.editor_open(db, fid, 2)
    assert kim.get(f"/hwp/{fid}/cmd").json()["cmds"] == []            # 새 실행기가 살아 있으면 예전 실행기는 쉰다
    # 읽기 명령 — 결과 data 를 받고, 큰 결과는 자른다
    rs, _ = nat.read_steps(None, ["char"])
    r = eo.push(db, fid, "read", {}, steps=rs, label="기안기 보기", runner=2)
    got = kim.get(f"/hwp/{fid}/cmd?runner=2").json()["cmds"]
    assert got[0]["id"] == r["id"] and got[0]["readonly"] is True
    assert kim.post(f"/hwp/{fid}/cmd/{r['id']}", json={"ok": True, "data": {"char": {"pt": 10}}}).json()["ok"]
    assert eo.status(db, fid, r["id"])["data"] == {"char": {"pt": 10}}
    r2 = eo.push(db, fid, "read", {}, steps=rs, label="기안기 보기", runner=2)
    kim.get(f"/hwp/{fid}/cmd?runner=2")
    kim.post(f"/hwp/{fid}/cmd/{r2['id']}", json={"ok": True, "data": {"text": "가" * (eo.MAX_DATA + 10)}})
    assert "_cut" in eo.status(db, fid, r2["id"])["data"]


def test_picture_asset_is_served_only_while_running(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    from tests.test_webhwp import _hwpx, _up
    fid = _up(kim, app, "기획처", "그림.hwpx", _hwpx())
    steps, _ = nat.steps_for("InsertPicture", {}, {"move": "문서 끝"}, (40, 20))
    with pytest.raises(eo.EditError):
        eo.push(db, fid, "InsertPicture", {}, steps=steps, runner=2, asset=b"x" * (eo.MAX_IMAGE + 1), base=base)
    c = eo.push(db, fid, "InsertPicture", {}, steps=steps, runner=2, asset=PNG, base=base)
    assert kim.get(f"/hwp/{fid}/cmd/{c['id']}/asset").content == PNG       # 실행기가 받아 갈 수 있다(아직 new)
    got = kim.get(f"/hwp/{fid}/cmd?runner=2").json()["cmds"]
    assert got[0]["asset"] is True
    r = kim.get(f"/hwp/{fid}/cmd/{c['id']}/asset")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    kim.post(f"/hwp/{fid}/cmd/{c['id']}", json={"ok": True})
    assert kim.get(f"/hwp/{fid}/cmd/{c['id']}/asset").status_code == 404   # 끝난 명령의 그림은 내주지 않는다
    assert kim.get(f"/hwp/{fid}/cmd/999/asset").status_code == 404


# ── 에이전트 도구 ──
def _tools(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    hw = wa.HwpTools(db, base, 77, "kim", "기획처", {"id": 1, "name": "시험 사업"}, lambda: [])
    et = eo.EditTools(hw)
    et.LIVE_WAIT = 0.3
    tid = next(line.split()[0] for line in hw.forms({"query": "예산 변경"}).splitlines() if "예산 변경" in line)
    hw.start({"form": tid, "title": "기본 기능 시험"})
    return app, db, base, hw, et, hw.fid()


def test_action_tool_queues_validates_and_reports(tmp_path, monkeypatch):
    app, db, base, hw, et, fid = _tools(tmp_path, monkeypatch)
    out = et.run("hwp_action", {"action": "CharShape", "params": {"Bold": True}, "at": {"field": "1. 변경 사유", "select": True}})
    assert out.startswith("기안기를 열면 이어서 합니다")
    c = eo._cmds(db, fid)["items"][-1]
    assert c["runner"] == 2 and c["for"] == "kim" and c["steps"][1][:2] == ["exec", "CharShape"]
    assert "보내지 않았다" in et.run("hwp_action", {"action": "FileSaveAs"})
    assert "보내지 않았다" in et.run("hwp_action", {"action": "CharShape", "params": {"Bold": True}})      # 자리 없음
    assert "보내지 않았다" in et.run("hwp_action", {"action": "CharShape", "params": "굵게", "at": {"field": "x"}})
    # 그림은 이 대화에서 볼 수 있는 문서·첨부만
    assert "보내지 않았다" in et.run("hwp_action", {"action": "InsertPicture", "params": {"image": "/etc/passwd"}, "at": {"move": "문서 끝"}})
    assert "보내지 않았다" in et.run("hwp_action", {"action": "InsertPicture", "params": {"image": "doc:999"}, "at": {"move": "문서 끝"}})
    n = len(eo._cmds(db, fid)["items"])
    # 열린 실행기가 있으면 결과를 기다려 돌려준다
    db.set_setting(f"webhwp_cmd_r2:{fid}", json.dumps({"kim": __import__("time").time()}))
    import threading

    def answer():                                   # 편집 화면 흉내 — 받은 명령마다 「했다」 를 보고, 표 만들기를 받으면 끝
        import time
        for _ in range(400):                         # 병렬 시험으로 바쁠 때도 넉넉히(예전 3초는 가끔 모자랐다)
            for g in eo.take(db, fid, "kim", runner=2):
                eo.report(db, fid, g["id"], True, "")
                if g["action"] == "TableCreate":
                    return
            time.sleep(0.05)
    th = threading.Thread(target=answer)
    th.start()
    et.LIVE_WAIT = 15
    out = et.run("hwp_action", {"action": "TableCreate", "params": {"Rows": 2, "Cols": 3}, "at": {"move": "문서 끝"}})
    th.join()
    assert out.startswith("기안기에서 「표 만들기」 을 했다") and len(eo._cmds(db, fid)["items"]) == n + 1


def test_inspect_needs_open_editor_and_styles_from_file(tmp_path, monkeypatch):
    app, db, base, hw, et, fid = _tools(tmp_path, monkeypatch)
    assert "열려 있지 않아" in et.run("hwp_inspect", {"what": ["char"]})
    assert all(c["status"] != "new" for c in eo._cmds(db, fid)["items"])     # 닫혀 있으면 읽기 명령은 남기지 않는다
    assert "읽지 않았다" in et.run("hwp_inspect", {"what": ["cookies"]})
    out = et.run("hwp_styles", {})
    assert out.startswith("1. ") and "StyleShortcut1" in out
    st = eo.styles(Path(webhwp.get(db, fid)["path"]).read_bytes())
    assert st and st[0]["name"]


def test_lease_is_respected(tmp_path, monkeypatch):
    app, db, base, hw, et, fid = _tools(tmp_path, monkeypatch)
    held = {"절:1. 변경 사유": "lee", "": "park"}
    stub = types.SimpleNamespace(STRUCT="", AGENT="AURA", holder=lambda db_, fid_, field: held.get(field, ""),
                                 emit=lambda *a, **k: None, conf=lambda *a: 60.0)
    monkeypatch.setitem(sys.modules, "zzaimy.app.webhwp_coedit", stub)
    monkeypatch.setattr(sys.modules["zzaimy.app"], "webhwp_coedit", stub, raising=False)
    out = et.run("hwp_action", {"action": "CharShape", "params": {"Bold": True}, "at": {"field": "절:1. 변경 사유"}})
    assert "lee 님이" in out and "칸을 편집 중" in out
    out = et.run("hwp_action", {"action": "PageSetup", "params": {"Landscape": True}})
    assert "park 님이" in out and "문서 구조" in out
    held.update({"": "kim"})                                               # 담당자 본인이 쥔 것은 막지 않는다
    assert "기안기를 열면" in et.run("hwp_action", {"action": "PageSetup", "params": {"Landscape": True}})
    held.update({"": "AURA"})
    assert eo.lease_block(db, fid, "", "kim") == ""


def test_file_tools_go_native_when_editor_is_open(tmp_path, monkeypatch):
    app, db, base, hw, et, fid = _tools(tmp_path, monkeypatch)
    before = Path(webhwp.get(db, fid)["path"]).read_bytes()
    db.set_setting(f"webhwp_cmd_r2:{fid}", json.dumps({"kim": __import__("time").time()}))
    out = et.run("hwp_page_setup", {"orientation": "가로", "margin": "좁게"})
    c = eo._cmds(db, fid)["items"][-1]
    assert c["action"] == "PageSetup" and c["steps"][1][2]["PageDef.Landscape"] == 1 and c["steps"][1][2]["PageDef.TopMargin"] == round(10 * nat.MM)
    assert "편집 용지" in out and Path(webhwp.get(db, fid)["path"]).read_bytes() == before     # 파일은 그대로, 기안기가 한다
    et.run("hwp_page_number", {"position": "아래 가운데", "dash": True})
    assert eo._cmds(db, fid)["items"][-1]["steps"][1][2] == {"DrawPos": 5, "NumberFormat": 0, "SideChar": 0x2D}
    et.run("hwp_header_footer", {"footer": "영남이공대학교"})
    assert eo._cmds(db, fid)["items"][-1]["action"] == "HeaderFooter"
    # 기안기 동작으로 못 옮기는 인자(시작 번호)는 파일 도구로
    n = len(eo._cmds(db, fid)["items"])
    assert "쪽 번호" in et.run("hwp_page_number", {"position": "아래 가운데", "start": 3})
    assert len(eo._cmds(db, fid)["items"]) == n


def test_tool_definitions():
    names = [t["name"] for t in eo.TOOLS]
    assert {"hwp_action", "hwp_inspect", "hwp_styles"} <= set(names)
    desc = next(t["desc"] for t in eo.TOOLS if t["name"] == "hwp_action")
    assert "CharShape(" in desc and "TableInsertRowColumn(" in desc and "StyleShortcut1~10" in desc
    assert not next(t for t in eo.TOOLS if t["name"] == "hwp_inspect")["writes"]
