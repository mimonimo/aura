"""채팅 에이전트의 한글 기안기 도구 — 양식 찾기·작업본 시작(대화에 잇기)·칸 채우기·절 쓰기·사람이 편집 중이면 쓰지 않기·편집 화면 상태."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_webhwp import _login, _setup
from zzaimy.app import webhwp, webhwp_agent as wa
from zzaimy.app import webhwp_templates as wt
from zzaimy.app import webhwp_write as ww


def test_agent_tools_start_fill_write(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    hw = wa.HwpTools(db, base, 77, "kim", "기획처", {"id": 1, "name": "AI 사업"}, lambda: [])
    assert "hwp_forms" in hw.state_lines()[0]
    out = hw.forms({"query": "예산 변경"})
    tid = next(line.split()[0] for line in out.splitlines() if "예산 변경" in line)
    assert tid.startswith("tpl:")
    msg = hw.start({"form": tid, "title": "2차년도 예산 변경 신청서"})
    fid = wa.bound(db, 77)
    assert fid and "만들고 이 대화에 이었다" in msg
    f = webhwp.get(db, fid)
    assert Path(f["path"]).parts[-3:-1] == ("작업", "AI 사업") and Path(f["path"]).parent.parent.parent.name == "기획처"
    assert "칸:사업명 — (비어 있음)" in hw.fields({})
    assert "채웠다: 칸:사업명" in hw.fill({"values": {"칸:사업명": "AI 융합 인재 양성"}})
    assert "썼다" in hw.write({"field": "1. 변경 사유", "text": "1) 단가 상승\n| 비목 | 금액 |\n|---|---|\n| 기자재 | 120 |"})
    vals = {x["name"]: x["text"] for x in ww.fields(Path(webhwp.get(db, fid)["path"]).read_bytes())}
    assert vals["칸:사업명"] == "AI 융합 인재 양성" and vals["절:1. 변경 사유"] == "1) 단가 상승"
    assert webhwp.meta(db, fid)["saved_by"] == "AURA" and wa.activity(db, fid)["field"] == "절:1. 변경 사유"
    # 다시 쓰면 앞 내용을 바꾼다
    hw.write({"field": "절:1. 변경 사유", "text": "1) 일정 조정"})
    x = ww.fields(Path(webhwp.get(db, fid)["path"]).read_bytes())
    assert {f_["name"]: f_["text"] for f_ in x}["절:1. 변경 사유"] == "1) 일정 조정"
    # 사람이 편집 중이어도 쓰고, 칸 글은 실시간 공유로 그 화면에 넘긴다
    webhwp.take_lock(db, fid, "lee")
    assert "채웠다: 칸:담당자" in hw.fill({"values": {"칸:담당자": "홍길동"}})
    v, ch = webhwp.live_since(db, fid, 0, "lee")
    assert any(c["field"] == "칸:담당자" and c["by"] == "AURA" for c in ch)
    assert any("lee 님도" in line for line in hw.state_lines())


def test_editor_state_and_presence(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    from tests.test_webhwp import _hwpx, _up
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    fid = _up(kim, app, "기획처", "같이.hwpx", _hwpx())
    assert kim.post(f"/hwp/{fid}/presence", json={"field": "칸:사업명"}).json()["ok"]
    st = kim.get(f"/hwp/{fid}/state").json()
    assert st["people"] == [] and st["aura"] == {}                     # 나는 이름표에 안 나온다
    webhwp.take_lock(app.state.db, fid, "kim")
    import json, time
    app.state.db.set_setting(f"webhwp_view:{fid}", json.dumps({"lee": {"at": time.time(), "field": "절:1. 개요"}}))
    app.state.db.set_setting(f"webhwp_activity:{fid}", json.dumps({"by": "AURA", "field": "절:2. 현황", "at": time.time()}))
    st = kim.get(f"/hwp/{fid}/state").json()
    assert st["people"][0]["user"] == "lee" and st["people"][0]["field"] == "절:1. 개요"
    assert st["aura"]["field"] == "절:2. 현황" and st["lock"]["user"] == "kim"


def test_chat_bind_from_department_folder_and_template(tmp_path, monkeypatch):
    """문서 패널 — 부서 폴더 파일을 이 대화 작업 문서로 잇기, 잠긴 양식은 복사해 잇기, 남의 부서 파일은 못 잇기."""
    app = _setup(tmp_path, monkeypatch)
    from tests.test_webhwp import _hwpx, _up
    kim, lee = TestClient(app), TestClient(app)
    assert _login(kim, "kim", "pass-12345678") and _login(lee, "lee", "pass-12345678")
    db = app.state.db
    sid = db.create_chat_session("시험", owner="kim")
    fid = _up(kim, app, "기획처", "계획.hwpx", _hwpx())
    j = kim.get("/api/hwp/browse?dept=기획처").json()
    assert j["here"]["dept"] == "기획처" and any(f["id"] == fid for f in j["here"]["files"])
    r = kim.post(f"/api/chat/{sid}/hwp/bind", json={"id": fid}).json()
    assert r["file"]["id"] == fid and wa.bound(db, sid) == fid
    assert kim.get(f"/api/chat/{sid}/hwp").json()["file"]["id"] == fid
    tid = next(x["id"] for g in kim.get("/api/hwp/templates").json()["groups"] for x in g["items"] if "기안문" in x["name"])
    r = kim.post(f"/api/chat/{sid}/hwp/bind", json={"id": tid}).json()
    assert r["file"]["id"] != tid and not webhwp.locked(db, r["file"]["id"])        # 양식은 복사본으로
    sid2 = db.create_chat_session("시험2", owner="lee")
    assert lee.post(f"/api/chat/{sid2}/hwp/bind", json={"id": fid}).status_code == 404   # 남의 부서 파일
    assert kim.post(f"/api/chat/{sid2}/hwp/bind", json={"id": fid}).status_code == 404   # 남의 대화


def test_fill_rejects_long_text_and_placeholders():
    from zzaimy.app import webhwp_agent as wa
    assert wa._too_long("| 구분 | 내용 |\n|---|---|\n| 가 | 나 |")
    assert wa._too_long("1) 첫째\n2) 둘째\n3) 셋째")
    assert not wa._too_long("2026. 3. 1. ~ 2027. 2. 28.")
    assert wa._placeholder("○○대학교 총장") and wa._placeholder("000명")
    assert not wa._placeholder("120,000천원") and not wa._placeholder("홍길동")
