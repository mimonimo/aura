"""기안기 칸 단위 공동 편집(ADR-0070) — 임대·판 번호·중복 막기·충돌 사본·알림 흐름(SSE)·한글 파일 써 넣기·권한·에이전트 참여."""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from tests.test_accounts import _login
from tests.test_webhwp import _setup, _staff, _up
from zzaimy.app import webhwp
from zzaimy.app import webhwp_coedit as ce
from zzaimy.app import webhwp_templates as wt
from zzaimy.app import webhwp_write as ww


def _doc(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    _staff(app, "park", "기획처")
    app.state.db.set_setting("user:kim:name", "김가람")
    app.state.db.set_setting("user:park:name", "박다온")
    kim, park = TestClient(app), TestClient(app)
    assert _login(kim, "kim", "pass-12345678") and _login(park, "park", "pass-12345678")
    fid = _up(kim, app, "기획처", "공동.hwpx", wt.build(wt.GRANT["budget_change"]))
    return app, kim, park, fid


def _vals(app, fid):
    return ww.field_values(Path(webhwp.get(app.state.db, fid)["path"]).read_bytes())


def test_lease_acquire_deny_wait_touch_grace_release(tmp_path, monkeypatch):
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    r = kim.post(f"/hwp/{fid}/lease", json={"field": "칸:사업명", "client": "k1"}).json()
    assert r["ok"] and r["until"] > time.time() + ce.conf(db, "lease") - 5
    j = park.post(f"/hwp/{fid}/lease", json={"field": "칸:사업명", "client": "p1"}).json()
    assert j["ok"] is False and j["by"] == "kim" and j["by_name"] == "김가람"
    # 기다리는 사람이 생기면 쥔 사람의 입력 없음 시간이 짧아지고(설정값), 쥔 사람에게 알림이 간다
    lk = ce.locks(db, fid)["칸:사업명"]
    assert lk["wanted"] == "park" and lk["until"] <= time.time() + ce.conf(db, "lease_contended") + 1
    assert any(e["kind"] == "lock-wanted" and e["data"]["holder"] == "kim" for e in ce.events_since(db, fid, 0))
    # 활동하면 늘어난다(기다리는 사람이 있으면 짧은 쪽으로)
    t = kim.post(f"/hwp/{fid}/lease", json={"action": "touch", "client": "k1"}).json()["until"]["칸:사업명"]
    assert t > time.time() + ce.conf(db, "lease_contended") - 5
    # 다른 칸으로 옮기면 유예 뒤 풀린다 — 유예 시간은 설정
    db.set_setting("coedit:grace", "0.2")
    assert kim.post(f"/hwp/{fid}/lease", json={"field": "칸:사업명", "action": "grace"}).json()["ok"]
    assert ce.holder(db, fid, "칸:사업명") == "kim"
    time.sleep(0.3)
    assert ce.holder(db, fid, "칸:사업명") == ""
    assert park.post(f"/hwp/{fid}/lease", json={"field": "칸:사업명", "client": "p1"}).json()["ok"]
    # 바로 놓기 — 알림 뒤 지운다, 남의 임대는 놓지 못한다
    assert kim.post(f"/hwp/{fid}/lease", json={"field": "칸:사업명", "action": "release"}).json()["ok"] is False
    assert park.post(f"/hwp/{fid}/lease", json={"field": "칸:사업명", "action": "release"}).json()["ok"]
    assert ce.locks(db, fid) == {}


def test_ops_idempotent_versions_and_conflict_copies(tmp_path, monkeypatch):
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    op = {"seq": 1, "field": "칸:사업명", "base": 0, "text": "AI 사업"}
    r1 = kim.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [op]}).json()["results"][0]
    r2 = kim.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [op]}).json()["results"][0]
    assert r1["ok"] and r1["version"] == 1 and r2 == r1                      # 다시 보내도 한 번만
    assert ce.state(db, fid)["칸:사업명"]["version"] == 1
    # 같은 창이 답을 받기 전에 이어 보낸 것(base 가 한 판 늦음)은 받는다
    r3 = kim.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [{"seq": 2, "field": "칸:사업명", "base": 0, "text": "AI 사업 2"},
                                                                    {"seq": 3, "field": "칸:사업명", "base": 0, "text": "AI 사업 2차"}]}).json()
    assert [x["version"] for x in r3["results"]] == [2, 3]
    # 남이 임대를 쥔 칸 — 반영하지 않고 충돌 사본으로 남긴다(버리지 않는다)
    r4 = park.post(f"/hwp/{fid}/ops", json={"client": "p1", "ops": [{"seq": 1, "field": "칸:사업명", "base": 3, "text": "박의 글"}]}).json()["results"][0]
    assert r4["ok"] is False and r4["reason"] == "locked" and r4["text"] == "AI 사업 2차" and r4["by_name"] == "김가람"
    assert ce.state(db, fid)["칸:사업명"]["text"] == "AI 사업 2차"
    # 임대가 풀린 뒤 옛 판 위에 쓴 글 — 판이 달라 충돌 사본
    kim.post(f"/hwp/{fid}/lease", json={"field": "칸:사업명", "action": "release"})
    r5 = park.post(f"/hwp/{fid}/ops", json={"client": "p1", "ops": [{"seq": 2, "field": "칸:사업명", "base": 1, "text": "옛 판 위의 글"}]}).json()["results"][0]
    assert r5["ok"] is False and r5["reason"] == "version" and r5["version"] == 3
    cs = park.get(f"/hwp/{fid}/conflicts").json()["conflicts"]
    assert {c["text"] for c in cs} == {"박의 글", "옛 판 위의 글"} and kim.get(f"/hwp/{fid}/conflicts").json()["conflicts"] == []
    # 지금 판 위에 쓰면 받는다
    r6 = park.post(f"/hwp/{fid}/ops", json={"client": "p1", "ops": [{"seq": 3, "field": "칸:사업명", "base": 3, "text": "박의 새 글"}]}).json()["results"][0]
    assert r6["ok"] and r6["version"] == 4
    hist = [e["data"]["text"] for e in ce.events_since(db, fid, 0) if e["kind"] == "field-updated"]
    assert hist == ["AI 사업", "AI 사업 2", "AI 사업 2차", "박의 새 글"]


def test_server_commits_fields_into_hwpx_and_keeps_versions(tmp_path, monkeypatch):
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    db.set_setting("coedit:commit_delay", "0.1")
    kim.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [{"seq": 1, "field": "칸:사업명", "base": 0, "text": "AI 융합"}]})
    park.post(f"/hwp/{fid}/ops", json={"client": "p1", "ops": [{"seq": 1, "field": "절:1. 변경 사유", "base": 0, "text": "첫 줄\r\n둘째 줄"}]})
    deadline = time.time() + 5
    while time.time() < deadline and ce.dirty(db, fid):
        time.sleep(0.05)
    vals = _vals(app, fid)
    assert vals["칸:사업명"] == "AI 융합" and vals["절:1. 변경 사유"] == "첫 줄\n둘째 줄"
    assert ce.dirty(db, fid) == []
    p = Path(webhwp.get(db, fid)["path"])
    assert list((p.parent / ".판").glob(f"{p.stem} *.hwpx"))                # 앞 판이 남는다
    # 파일을 여는 쪽(/file)·내려받기는 아직 안 들어간 칸 글을 먼저 써 넣는다
    db.set_setting("coedit:commit_delay", "60")
    kim.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [{"seq": 2, "field": "칸:사업명", "base": 1, "text": "AI 융합 2"}]})
    assert ww.field_values(kim.get(f"/hwp/{fid}/file").content)["칸:사업명"] == "AI 융합 2"
    # 써 넣기는 「문서 저장」 알림을 남기지 않는다(열린 화면이 다시 열지 않게) — 사람·AURA 의 문서 전체 저장만
    assert not any(e["kind"] == "doc-saved" for e in ce.events_since(db, fid, 0))


def test_structure_save_overlays_others_fields(tmp_path, monkeypatch):
    """칸 밖 고치기(구조 잠금)를 쥔 사람이 문서 전체를 저장해도 다른 사람이 쓴 칸 글이 남는다."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    form = Path(webhwp.get(app.state.db, fid)["path"]).read_bytes()
    park.post(f"/hwp/{fid}/ops", json={"client": "p1", "ops": [{"seq": 1, "field": "칸:담당자", "base": 0, "text": "박다온"}]})
    assert kim.post(f"/hwp/{fid}/lock").json()["ok"]
    j = park.post(f"/hwp/{fid}/lock").json()
    assert j["ok"] is False and j["by_name"] == "김가람"                       # 화면에는 아이디 대신 이름
    assert park.post(f"/hwp/{fid}/save", content=form).status_code == 409
    mine, _ = ww.set_fields(form, {"칸:사업명": "김의 사업"})
    out = kim.post(f"/hwp/{fid}/save", content=mine).json()
    assert out["merged"] == ["칸:담당자"]
    vals = _vals(app, fid)
    assert vals["칸:사업명"] == "김의 사업" and vals["칸:담당자"] == "박다온"
    assert any(e["kind"] == "doc-saved" and e["data"]["by"] == "kim" for e in ce.events_since(app.state.db, fid, 0))
    assert "김가람 편집 중" in park.get("/hwp?dept=기획처").text


def test_permissions_student_other_dept_and_locked_template(tmp_path, monkeypatch):
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    app.state.accounts["stu"] = app.state.new_account("stu", "pass-12345678", "student")
    stu, lee = TestClient(app), TestClient(app)
    assert _login(stu, "stu", "pass-12345678") and _login(lee, "lee", "pass-12345678")
    assert stu.get(f"/hwp/{fid}/coedit").status_code in (401, 403)
    assert stu.post(f"/hwp/{fid}/ops", json={"client": "s", "ops": []}).status_code in (401, 403)
    assert lee.get(f"/hwp/{fid}/coedit").status_code == 404                  # 다른 부서
    assert lee.post(f"/hwp/{fid}/lease", json={"field": "칸:사업명"}).status_code == 404
    tid = {x["spec"]: x["id"] for x in wt.ensure_all(app.state.db, Path(app.state.data_dir))}["plan"]
    assert kim.get(f"/hwp/{tid}/coedit").json()["readonly"] is True
    assert kim.post(f"/hwp/{tid}/ops", json={"client": "k", "ops": [{"seq": 1, "field": "x", "text": "y"}]}).status_code == 403
    assert kim.post(f"/hwp/{tid}/lease", json={"field": "x"}).status_code == 403


def test_presence_from_everyone_and_snapshot_names(tmp_path, monkeypatch):
    """기다리는 사람(구조 잠금 없음)도 같이 보는 사람에 나오고, 이름은 계정 아이디가 아닌 표시 이름."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    assert kim.post(f"/hwp/{fid}/lock").json()["ok"]
    assert park.post(f"/hwp/{fid}/presence", json={"client": "p1", "field": "칸:사업명"}).json()["ok"]
    st = kim.get(f"/hwp/{fid}/state").json()
    assert st["people"] == [{"user": "park", "name": "박다온", "field": "칸:사업명"}]
    snap = kim.get(f"/hwp/{fid}/coedit").json()
    assert snap["names"]["park"] == "박다온" and snap["conf"]["throttle_ms"] == 300 and snap["locks"][""]["user"] == "kim"
    assert kim.post(f"/hwp/{fid}/leave?client=p1").json()["ok"]                # 남의 창 id 로는 남의 임대를 건드리지 않는다
    assert park.post(f"/hwp/{fid}/leave", json={"client": "p1"}).json()["ok"]
    assert kim.get(f"/hwp/{fid}/state").json()["people"] == []


def test_live_put_concurrent_writes_are_not_lost(tmp_path, monkeypatch):
    """예전 칸 공유는 설정 JSON 을 읽고 고쳐 써 동시에 쓰면 칸 글이 사라졌다 — 이제 칸마다 줄이라 다 남는다."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    errs = []

    def put(i):
        try:
            webhwp.live_put(db, fid, f"u{i}", f"칸:{i}", f"글 {i}")
        except Exception as e:
            errs.append(e)
    ts = [threading.Thread(target=put, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and {k: v["text"] for k, v in ce.state(db, fid).items()} == {f"칸:{i}": f"글 {i}" for i in range(12)}
    last, ch = webhwp.live_since(db, fid, 0, "u0")
    assert len(ch) == 11 and last > 0


def test_agent_is_a_participant_and_respects_leases(tmp_path, monkeypatch):
    from zzaimy.app import webhwp_agent as wa
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    hw = wa.HwpTools(db, base, 77, "kim", "기획처", {"id": 1, "name": "AI 사업"}, lambda: [])
    tid = next(line.split()[0] for line in hw.forms({"query": "예산 변경"}).splitlines() if "예산 변경" in line)
    hw.start({"form": tid, "title": "공동 시험"})
    fid = wa.bound(db, 77)
    assert ce.acquire(db, fid, "칸:담당자", "lee")["ok"]                     # 사람이 그 칸을 쓰는 중
    out = hw.fill({"values": {"칸:사업명": "AI 융합", "칸:담당자": "홍길동"}})
    assert "채웠다: 칸:사업명" in out and "칸:담당자" in out and "쓰는 칸이라" in out
    vals = ww.field_values(Path(webhwp.get(db, fid)["path"]).read_bytes())
    assert vals["칸:사업명"] == "AI 융합" and vals["칸:담당자"] == ""
    assert ce.holder(db, fid, "칸:사업명") == ""                              # 에이전트는 쓰고 바로 놓는다
    ev = [e for e in ce.events_since(db, fid, 0) if e["kind"] == "field-updated"]
    assert ev and ev[-1]["data"]["by"] == "AURA"
    assert ce.acquire(db, fid, "절:1. 변경 사유", "lee")["ok"]
    assert "쓰는 중" in hw.write({"field": "절:1. 변경 사유", "text": "1) 단가 상승"})
    ce.release(db, fid, "절:1. 변경 사유", "lee")
    assert "썼다" in hw.write({"field": "절:1. 변경 사유", "text": "1) 단가 상승"})
    assert ce.state(db, fid)["절:1. 변경 사유"]["text"] == "1) 단가 상승"
    # AURA 작성 중 표시는 마지막 쓰기 뒤 aura_ttl 초만
    assert wa.activity(db, fid)["field"] == "절:1. 변경 사유"
    db.set_setting("coedit:aura_ttl", "0.05")
    time.sleep(0.1)
    assert wa.activity(db, fid) == {}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_sse_pushes_field_updates_to_other_user(tmp_path, monkeypatch):
    """실제 서버(uvicorn)에 SSE 로 붙어 다른 사람의 칸 변경이 곧바로 오는지 — 같은 프로세스는 깨워서 보낸다."""
    import uvicorn
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        url = f"http://127.0.0.1:{port}"
        jar = {"zz_session": park.cookies.get("zz_session")}
        got: list = []
        sent_at: dict = {}

        def listen():
            with httpx.Client(cookies=jar, timeout=10) as c:
                with c.stream("GET", f"{url}/hwp/{fid}/events") as r:
                    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
                    kind = None
                    for line in r.iter_lines():
                        if line.startswith("event: "):
                            kind = line[7:]
                        elif line.startswith("data: ") and kind == "field-updated":
                            got.append((json.loads(line[6:]), time.time()))
                            return
        t = threading.Thread(target=listen, daemon=True)
        t.start()
        time.sleep(0.5)
        with httpx.Client(cookies={"zz_session": kim.cookies.get("zz_session")}, timeout=10) as c:
            sent_at["t"] = time.time()
            r = c.post(f"{url}/hwp/{fid}/ops", json={"client": "k1", "ops": [{"seq": 1, "field": "칸:사업명", "base": 0, "text": "실시간"}]})
            assert r.status_code == 200
        t.join(5)
        assert got, "SSE 로 칸 변경을 받지 못했다"
        d, at = got[0]
        assert d["field"] == "칸:사업명" and d["text"] == "실시간" and d["by"] == "kim" and d["by_name"] == "김가람"
        assert at - sent_at["t"] < 0.8                                          # 같은 프로세스는 기다리지 않고 바로
    finally:
        server.should_exit = True
        th.join(5)


def test_editor_page_uses_wrapper_and_has_no_duplicate_dialogs(tmp_path, monkeypatch):
    """편집 화면은 어댑터·공동 편집 층을 싣고, 기안기에 이미 있는 찾기·편집 용지 창을 따로 만들지 않는다.
    기다리는 사람의 편집기는 읽기 전용(setReadOnly)으로 둔다 — 예전에는 보기 전용 안내만 띄우고 입력이 들어가 사라졌다."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    html = kim.get(f"/webhwpctrl/aura-edit?id={fid}").text
    assert "/static/webhwp-wrapper.js" in html and "/static/webhwp-collab.js" in html
    assert "data-ps" not in html and "data-find" not in html and "data-page-setup" not in html and "data-replace" not in html
    assert "hwp.run(act)" in html and "F3" not in html.split("function findKey")[1].split("}")[0]   # 찾기는 기안기 창으로, F3 은 건드리지 않는다
    assert "/static/webhwp-agent-runner.js" in html and "runner=2" in html
    assert "setReadOnly(true)" in html and "data-struct" in html
    root = Path(__file__).resolve().parents[1] / "src/zzaimy/app"
    page_js = html.split("<script>")[-1]
    for api in ("PutFieldText", "GetFieldText", "ModifyFieldProperties", "GetCurFieldName", "MoveToField("):
        assert api not in page_js, api                                          # 기안기 API 는 어댑터만 부른다
        assert api not in (root / "static/webhwp-collab.js").read_text()


def test_client_id_is_bound_to_signed_in_user(tmp_path, monkeypatch):
    """창 번호(client)는 화면이 지어 보내는 값 — 남의 창 번호·seq 를 흉내 내어도 그 사람의 변경을 막거나 판 검사를 건너뛰지 못한다."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    # 박이 김의 창 번호와 seq 를 먼저 차지해 둔다
    park.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [{"seq": 1, "field": "칸:사업명", "base": 0, "text": "박의 글"}]})
    kim.post(f"/hwp/{fid}/lease", json={"field": "칸:사업명", "action": "acquire", "client": "k1"})
    park.post(f"/hwp/{fid}/lease", json={"field": "칸:사업명", "action": "release"})
    r = kim.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [{"seq": 1, "field": "칸:사업명", "base": 1, "text": "김의 글"}]}).json()["results"][0]
    assert r["ok"] and r["version"] == 2                       # 박의 결과가 되돌아오지 않고 김의 글이 실제로 들어간다
    assert ce.state(db, fid)["칸:사업명"]["text"] == "김의 글"
    kim.post(f"/hwp/{fid}/lease", json={"field": "칸:사업명", "action": "release"})
    # 김의 창 번호를 흉내 내어도 판 번호 검사(옛 판 위에 쓰기)를 건너뛰지 못한다
    r = park.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [{"seq": 9, "field": "칸:사업명", "base": 0, "text": "덮어쓰기"}]}).json()["results"][0]
    assert r["ok"] is False and r["reason"] == "version"
    assert ce.state(db, fid)["칸:사업명"]["text"] == "김의 글"


def test_presence_and_leave_only_touch_own_window(tmp_path, monkeypatch):
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    kim.post(f"/hwp/{fid}/presence", json={"client": "k1", "field": "칸:사업명"})
    park.post(f"/hwp/{fid}/presence", json={"client": "k1", "field": "칸:예산"})     # 같은 창 번호를 써도 김 줄을 바꾸지 못한다
    park.post(f"/hwp/{fid}/leave", json={"client": "k1"})                           # 박이 김의 창 번호로 나가도 김은 남는다
    ppl = {p["user"]: p for p in ce.people(db, fid)}
    assert ppl["kim"]["field"] == "칸:사업명" and ppl["kim"]["client"] == "k1" and "park" not in ppl


def test_coedit_resource_caps(tmp_path, monkeypatch):
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    from zzaimy.app import webhwp_coedit_routes as cr
    monkeypatch.setattr(cr, "MAX_BODY", 1000)
    r = kim.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [{"seq": 1, "field": "칸:사업명", "base": 0, "text": "가" * 2000}]})
    assert r.status_code == 413
    # 문서에 새로 만들 수 있는 칸 줄 수에 상한
    monkeypatch.setattr(ce, "FIELDS_MAX", 2)
    res = ce.apply_ops(db, fid, "kim", "k1", [{"seq": i, "field": f"f{i}", "text": "x"} for i in range(1, 5)])
    assert [x["ok"] for x in res] == [True, True, False, False] and res[2]["reason"] == "too_many"
    # 충돌 사본은 사람·문서마다 상한까지만 남는다
    monkeypatch.setattr(ce, "CONFLICTS_KEEP", 3)
    ce.acquire(db, fid, "f1", "kim", "k1")
    ce.apply_ops(db, fid, "park", "p1", [{"seq": 100 + i, "field": "f1", "text": f"t{i}"} for i in range(6)])
    assert len(ce.conflicts(db, fid, "park")) == 3
    # 한 사람이 한 문서에 여는 알림 흐름 수 상한
    monkeypatch.setattr(ce.HUB, "count", lambda f, u: cr.STREAMS_PER_USER)
    assert kim.get(f"/hwp/{fid}/events").status_code == 429
