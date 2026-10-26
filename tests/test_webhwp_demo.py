"""공동 편집 시연(ADR-0071) — 관리자 세션 하나로 칸마다 다른 시연 계정, 기안기 편집 경로에서만, 시연 폴더 밖은 못 연다."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_accounts import _login
from tests.test_webhwp import _setup, _up
from zzaimy.app import webhwp
from zzaimy.app import webhwp_coedit as ce
from zzaimy.app import webhwp_demo as demo
from zzaimy.app import webhwp_templates as wt
from zzaimy.app import webhwp_write as ww


def _admin(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    dev, kim = TestClient(app), TestClient(app)
    assert _login(dev, "zzdev", "devpass") and _login(kim, "kim", "pass-12345678")
    return app, dev, kim


def _new(dev) -> int:
    r = dev.post("/dev/coedit-demo/new", follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].split("id=")[-1])


def H(slot):
    return {"X-Aura-Demo": slot}


def test_page_admin_only_and_three_panes(tmp_path, monkeypatch):
    app, dev, kim = _admin(tmp_path, monkeypatch)
    assert kim.get("/dev/coedit-demo").status_code == 403
    assert kim.post("/dev/coedit-demo/new", follow_redirects=False).status_code == 403
    # 열자마자 빈 시연 문서가 생겨 세 칸으로 — 단추는 「처음부터 다시」 하나(세 사람 동시 작성 갈래)
    r = dev.get("/dev/coedit-demo", follow_redirects=False)
    assert r.status_code == 303
    fid = int(r.headers["location"].split("id=")[-1])
    page = dev.get(f"/dev/coedit-demo?id={fid}").text
    for s in "abc":
        assert f"/webhwpctrl/aura-edit?id={fid}&amp;demo={s}" in page
    assert page.count("<iframe") == 3 and "김가람" in page and "시연 계정" in page and "처음부터 다시" in page
    assert "AURA로 절 쓰기" not in page and 'aria-current="page">세 사람 동시 작성' in page
    assert dev.get("/dev/coedit-demo", follow_redirects=False).status_code == 200          # 있으면 다시 만들지 않는다
    # 시연 계정 — 장부에 demo 표시·비활성·비밀번호 없음(로그인 길 없음), 다시 눌러도 그대로
    acc = json.loads((tmp_path / "accounts.json").read_text())
    for uid, name in demo.SLOTS.values():
        assert acc[uid]["demo"] is True and acc[uid]["active"] is False and "pw_hash" not in acc[uid] and acc[uid]["name"] == name
    assert not _login(TestClient(app), "demo_a", "")
    fid2 = _new(dev)
    assert json.loads((tmp_path / "accounts.json").read_text())["demo_a"] == acc["demo_a"]
    assert webhwp.get(app.state.db, fid) is None                     # 같은 양식으로 다시 만들면 앞 시연 문서는 휴지통으로
    fid = fid2
    # 「새 시연 문서」 는 빈 문서 — 시연 계정마다 번호 붙은 빈 절 하나, 미리 든 글 없음, 시연 폴더에
    f = webhwp.get(app.state.db, fid)
    assert webhwp.dept_of_path(Path(app.state.data_dir), f["path"]) == demo.ROOT and not webhwp.locked(app.state.db, fid)
    fs = ww.fields(Path(f["path"]).read_bytes())
    assert [x["name"] for x in fs] == [f"절:{i}. {n}" for i, (_u, n) in enumerate(demo.SLOTS.values(), 1)] and not any(x["text"] for x in fs)
    # 바로가기는 개발 사이드바 「문서 작성」 한 곳(10-26 — 업무 화면 사이드바의 링크는 뺐다), 교직원 화면에는 없다
    assert 'href="/dev/coedit-demo"' in dev.get("/dev").text and 'href="/dev/coedit-demo"' not in dev.get("/hwp").text
    assert 'href="/dev/coedit-demo"' not in kim.get("/hwp").text


def test_slot_acts_as_demo_user_on_editor_routes_only(tmp_path, monkeypatch):
    app, dev, kim = _admin(tmp_path, monkeypatch)
    fid = _new(dev)
    # 편집 화면 — 칸 표시가 있으면 그 시연 계정으로 그리고(나 = demo_a), 저장소를 나누는 앞 스크립트가 붙는다
    pg = dev.get(f"/webhwpctrl/aura-edit?id={fid}&demo=a").text
    assert 'me = "demo_a"' in pg and "X-Aura-Demo" in pg and json.dumps("김가람") in pg
    plain = dev.get(f"/webhwpctrl/aura-edit?id={fid}").text
    assert 'me = "zzdev"' in plain and "X-Aura-Demo" not in plain              # 표시가 없으면 예전 그대로
    assert dev.get(f"/hwp/{fid}/coedit", headers=H("b")).json()["me"] == "demo_b"
    assert dev.get(f"/hwp/{fid}/coedit?demo=c").json()["me"] == "demo_c"         # 알림 흐름·sendBeacon 처럼 머리를 못 붙이는 요청
    assert dev.get(f"/hwp/{fid}/coedit").json()["me"] == "zzdev"
    assert dev.get(f"/hwp/{fid}/coedit", headers=H("z")).json()["me"] == "zzdev"   # 없는 칸은 무시
    # 편집 경로 밖은 표시를 무시한다 — 문서함·개발 화면은 관리자 그대로
    assert dev.get("/hwp", headers=H("a")).status_code == 200 and 'me = "demo_a"' not in dev.get("/hwp", headers=H("a")).text
    assert dev.get("/dev/coedit-demo", headers=H("a")).status_code == 200
    # 관리자가 아닌 사람의 표시는 무시 — 자기 권한 그대로(시연 폴더는 못 연다)
    kfid = _up(kim, app, "기획처", "내 문서.hwpx", wt.build(wt.GRANT["budget_change"]))
    assert kim.get(f"/hwp/{kfid}/coedit", headers=H("a")).json()["me"] == "kim"
    assert kim.get(f"/hwp/{fid}/coedit", headers=H("a")).status_code == 404
    assert kim.get(f"/webhwpctrl/aura-edit?id={fid}&demo=a").status_code == 404


def test_demo_user_cannot_open_non_demo_documents(tmp_path, monkeypatch):
    app, dev, kim = _admin(tmp_path, monkeypatch)
    fid = _new(dev)
    kfid = _up(kim, app, "기획처", "부서 문서.hwpx", wt.build(wt.GRANT["budget_change"]))
    tid = {x["spec"]: x["id"] for x in wt.ensure_all(app.state.db, Path(app.state.data_dir))}["plan"]   # 공통 양식
    assert dev.get(f"/hwp/{kfid}/coedit").status_code == 200                    # 관리자 본인은 연다
    for target in (kfid, tid):
        assert dev.get(f"/webhwpctrl/aura-edit?id={target}&demo=a").status_code == 404
        for path in ("coedit", "file", "state", "conflicts", "events"):
            assert dev.get(f"/hwp/{target}/{path}", headers=H("a")).status_code == 404, path
        for path, body in (("ops", {"client": "x", "ops": []}), ("lease", {"field": "칸:사업명", "client": "x"}),
                           ("presence", {"client": "x"}), ("leave", {"client": "x"}), ("lock", None), ("save", None)):
            assert dev.post(f"/hwp/{target}/{path}", headers=H("a"), json=body).status_code == 404, path
    # 새 문서 화면도 시연 폴더 밖 부서로는 열지 못한다
    assert dev.get(f"/webhwpctrl/aura-edit?dept=기획처&demo=a").status_code == 403
    assert dev.get(f"/hwp/{fid}/coedit", headers=H("a")).status_code == 200


def test_three_slots_are_three_users(tmp_path, monkeypatch):
    app, dev, _ = _admin(tmp_path, monkeypatch)
    fid = _new(dev)
    db = app.state.db
    for s in "abc":
        assert dev.post(f"/hwp/{fid}/presence", headers=H(s), json={"client": f"w{s}", "field": ""}).json()["ok"]
    out = dev.post(f"/hwp/{fid}/presence", headers=H("a"), json={"client": "wa", "field": ""}).json()["people"]
    assert {p["user"]: p["name"] for p in out} == {"demo_a": "김가람", "demo_b": "이나래", "demo_c": "박다온"}
    names = [x["name"] for x in ww.fields(Path(webhwp.get(db, fid)["path"]).read_bytes())]
    f1, f2 = names[0], names[1]
    # a 가 칸을 쥐면 b 는 못 쥐고(쥔 사람 이름은 시연 이름), 다른 칸은 b 가 쓴다
    assert dev.post(f"/hwp/{fid}/lease", headers=H("a"), json={"field": f1, "client": "wa"}).json()["ok"]
    j = dev.post(f"/hwp/{fid}/lease", headers=H("b"), json={"field": f1, "client": "wb"}).json()
    assert j["ok"] is False and j["by"] == "demo_a" and j["by_name"] == "김가람"
    r = dev.post(f"/hwp/{fid}/ops", headers=H("b"), json={"client": "wb", "ops": [{"seq": 1, "field": f2, "base": 0, "text": "이나래 글"}]}).json()
    assert r["results"][0]["ok"]
    assert ce.state(db, fid)[f2]["by"] == "demo_b" and ce.locks(db, fid)[f1]["user"] == "demo_a"
    # 창을 닫으면(sendBeacon — 주소 demo=) 그 시연 계정의 그 창만 빠진다
    assert dev.post(f"/hwp/{fid}/leave?client=wa&demo=a", content=json.dumps({"client": "wa"}),
                    headers={"Content-Type": "application/json"}).json()["ok"]
    assert ce.holder(db, fid, f1) == "" and {p["user"] for p in ce.people(db, fid)} == {"demo_b", "demo_c"}


def test_reset_makes_fresh_copy_and_trashes_old(tmp_path, monkeypatch):
    app, dev, _ = _admin(tmp_path, monkeypatch)
    fid = _new(dev)
    db = app.state.db
    f1 = ww.fields(Path(webhwp.get(db, fid)["path"]).read_bytes())[0]["name"]
    dev.post(f"/hwp/{fid}/ops", headers=H("a"), json={"client": "wa", "ops": [{"seq": 1, "field": f1, "base": 0, "text": "남은 글"}]})
    src = webhwp.meta(db, fid)["source"]
    r = dev.post("/dev/coedit-demo/reset", data={"id": fid}, follow_redirects=False)
    new = int(r.headers["location"].split("id=")[-1])
    assert new != fid and webhwp.get(db, fid) is None and webhwp.meta(db, new)["source"] == src
    assert ce.snapshot(db, new)["fields"] == {}
    assert dev.post("/dev/coedit-demo/reset", data={"id": 999999}, follow_redirects=False).status_code == 404


def test_long_demo_uses_business_plan_form(tmp_path, monkeypatch):
    """「장문 시연 문서」 — 코드로 짠 공통 양식 사업계획서로. 이름은 「시연 · 사업계획서」, 머리 칸에 시연용 값,
    같은 양식으로 다시 만들면 앞 시연 문서는 휴지통으로(목록에 같은 이름이 쌓이지 않게)."""
    app, dev, _ = _admin(tmp_path, monkeypatch)
    db = app.state.db
    r = dev.post("/dev/coedit-demo/new", data={"kind": "long"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    fid = int(r.headers["location"].split("id=")[-1])
    assert webhwp.get(db, fid)["name"].startswith("시연 · 사업계획서")
    assert str(webhwp.meta(db, fid)["source"]).startswith(demo.SOURCE)
    page = dev.get(f"/dev/coedit-demo?id={fid}").text
    assert "AURA로 절 쓰기" in page and 'aria-current="page">AURA 함께 쓰기' in page    # 사업계획서 문서는 AURA 갈래로 열린다
    assert dev.get("/dev/coedit-demo?mode=aura", follow_redirects=False).status_code == 200   # 이미 있으면 그 문서
    from zzaimy.app import webhwp_write as _ww
    body = "".join(__import__("zipfile").ZipFile(__import__("io").BytesIO(Path(webhwp.get(db, fid)["path"]).read_bytes())).read(n).decode()
                   for n in ["Contents/section0.xml"])
    assert "○○○○ 사업" not in body and "2026 AI 업무 도구 교육 운영" in body       # 표지 자리표시도 시연용 값
    again = int(dev.post("/dev/coedit-demo/new", data={"kind": "long"}, follow_redirects=False).headers["location"].split("id=")[-1])
    assert again != fid and webhwp.get(db, fid) is None


def test_demo_values_fill_header_cells_only():
    vals = demo.demo_values([{"kind": "칸", "name": "칸:보고 부서", "text": ""}, {"kind": "칸", "name": "칸:보고 일자", "text": "2026. 1. 1."},
                             {"kind": "절", "name": "절:1. 개요", "text": ""}, {"kind": "칸", "name": "칸:사업명", "text": ""}])
    assert vals == {"칸:보고 부서": "기획팀", "칸:사업명": "2026 AI 업무 도구 교육 운영"}


def test_aura_button_runs_chat_agent_path(tmp_path, monkeypatch):
    """「AURA로 쓰기」 — 시연 계정 김가람의 대화를 문서에 잇고 채팅과 같은 답 길(schedule_answer)로 요청을 보낸다. 쓰는 중이면 다시 보내지 않는다."""
    app, dev, _ = _admin(tmp_path, monkeypatch)
    fid = _new(dev)
    sent = []
    monkeypatch.setattr(app.state, "schedule_answer", lambda bg, sid, q, stored, crit, *a, **k: sent.append((sid, q)), raising=False)
    r = dev.post("/dev/coedit-demo/aura", data={"id": fid}, follow_redirects=False)
    assert r.status_code == 303 and "aura=1" in r.headers["location"]
    sid, q = sent[-1]
    db = app.state.db
    from zzaimy.app import webhwp_agent as wa
    assert wa.bound(db, sid) == fid and db.get_chat_session(sid)["owner"] == demo.SLOTS["a"][0] and "비어 있는 절" in q
    assert "AURA(27B)에게 빈 절 하나를 맡겼습니다" in dev.get(r.headers["location"]).text
    assert dev.post("/dev/coedit-demo/aura", data={"id": 999999}, follow_redirects=False).status_code == 404
