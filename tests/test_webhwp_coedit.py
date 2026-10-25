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
    assert kim.post(f"/hwp/{fid}/lease", json={"field": "칸:사업명", "client": "k1"}).json()["ok"]      # 처음 고칠 때 편집 화면이 잡는다
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


def test_presence_ping_returns_everyone_including_quiet_people(tmp_path, monkeypatch):
    """알림은 칸이 바뀔 때만 가서 가만히 있는 사람이 30초 뒤 남의 화면에서 사라졌다 — 몇 초마다 보내는 알림의 답으로 지금 있는 사람 모두를 준다."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    park.post(f"/hwp/{fid}/presence", json={"client": "p1", "field": ""})
    out = kim.post(f"/hwp/{fid}/presence", json={"client": "k1", "field": "칸:사업명"}).json()
    got = {p["user"]: p for p in out["people"]}
    assert got["park"]["name"] == "박다온" and got["park"]["client"] == "p1" and got["kim"]["field"] == "칸:사업명"


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


def test_one_lease_per_window_and_typing_takes_it(tmp_path, monkeypatch):
    """칸을 옮겨 다녀도 임대가 쌓이지 않는다 — 한 창에 한 칸. 변경만으로는 임대를 만들지 않는다(편집 화면이 처음 고칠 때 잡는다).
    같은 사람의 다른 창은 따로 센다(창 번호가 다른 임대는 놓지 않는다). 알림에는 창 번호가 있어 화면이 「(다른 창)」 을 가른다."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    for f in ("칸:사업명", "칸:담당자", "절:1. 변경 사유"):
        assert kim.post(f"/hwp/{fid}/lease", json={"field": f, "client": "k1"}).json()["ok"]
    assert {f: x["user"] for f, x in ce.locks(db, fid).items()} == {"절:1. 변경 사유": "kim"}
    assert kim.post(f"/hwp/{fid}/lease", json={"field": "칸:사업명", "client": "k2"}).json()["ok"]      # 같은 사람 다른 창
    lk = ce.locks(db, fid)
    assert lk["절:1. 변경 사유"]["client"] == "k1" and lk["칸:사업명"]["client"] == "k2"
    ev = [e["data"] for e in ce.events_since(db, fid, 0) if e["kind"] == "lock-changed"]
    assert all("client" in d for d in ev) and any(d["field"] == "칸:담당자" and d["user"] == "" for d in ev)
    # 변경만 보낸 칸에는 임대가 생기지 않는다(옛 칸 임대가 되살아나지 않게)
    r = park.post(f"/hwp/{fid}/ops", json={"client": "p1", "ops": [{"seq": 1, "field": "칸:담당자", "base": 0, "text": "박"}]}).json()["results"][0]
    assert r["ok"] and "until" not in r and "칸:담당자" not in ce.locks(db, fid)
    # 쥔 창의 변경은 임대를 늘린다
    r = kim.post(f"/hwp/{fid}/ops", json={"client": "k2", "ops": [{"seq": 1, "field": "칸:사업명", "base": 0, "text": "가"}]}).json()["results"][0]
    assert r["ok"] and r["until"] > time.time()
    # 활동 늘리기는 그 창 것만
    t = kim.post(f"/hwp/{fid}/lease", json={"action": "touch", "client": "k2"}).json()["until"]
    assert set(t) == {"칸:사업명"}
    # 창을 닫으면 그 창의 칸은 바로 풀린다(유예 없이) — 같은 사람 다른 창의 칸은 그대로
    assert kim.post(f"/hwp/{fid}/leave", json={"client": "k2"}).json()["ok"]
    assert {f: x["client"] for f, x in ce.locks(db, fid).items()} == {"절:1. 변경 사유": "k1"}


def test_locked_template_never_coedited_even_by_admin(tmp_path, monkeypatch):
    """공통 양식은 모두의 한글 완성본이 원본 서식으로 쓴다 — 칸 공동 편집으로는 관리자도 고치지 않는다(시험 글이 양식에 남지 않게)."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    tid = {x["spec"]: x["id"] for x in wt.ensure_all(app.state.db, Path(app.state.data_dir))}["plan"]
    before = Path(webhwp.get(app.state.db, tid)["path"]).read_bytes()
    dev = TestClient(app)
    app.state.accounts["boss"] = app.state.new_account("boss", "pass-12345678", "dev")
    assert _login(dev, "boss", "pass-12345678")
    assert dev.get(f"/hwp/{tid}/coedit").json()["fields_off"] is True
    assert dev.post(f"/hwp/{tid}/ops", json={"client": "d", "ops": [{"seq": 1, "field": "칸:사업명", "text": "시험"}]}).status_code == 403
    assert dev.post(f"/hwp/{tid}/lease", json={"field": "칸:사업명", "client": "d"}).status_code == 403
    assert Path(webhwp.get(app.state.db, tid)["path"]).read_bytes() == before and ce.state(app.state.db, tid) == {}
    # 사본은 칸 공동 편집이 된다
    new = kim.post(f"/hwp/{tid}/copy", data={"dept": "기획처"}, headers={"Accept": "application/json"}).json()["id"]
    assert kim.get(f"/hwp/{new}/coedit").json()["fields_off"] is False


def test_agent_flag_only_on_fields_it_wrote(tmp_path, monkeypatch):
    """사람이 쥔 칸이라 AURA 가 못 쓰면 「AURA 작성 중」 표시를 띄우지 않는다."""
    from zzaimy.app import webhwp_agent as wa
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    hw = wa.HwpTools(db, base, 78, "kim", "기획처", {"id": 1, "name": "AI 사업"}, lambda: [])
    tid = next(line.split()[0] for line in hw.forms({"query": "예산 변경"}).splitlines() if "예산 변경" in line)
    hw.start({"form": tid, "title": "표시 시험"})
    fid = wa.bound(db, 78)
    assert ce.acquire(db, fid, "칸:담당자", "lee", "l1")["ok"]
    out = hw.fill({"values": {"칸:담당자": "홍길동"}})
    assert "쓰는 칸이라" in out and wa.activity(db, fid) == {}
    assert not any(e["kind"] == "aura" for e in ce.events_since(db, fid, 0))
    hw.fill({"values": {"칸:사업명": "AI"}})
    assert wa.activity(db, fid)["field"] == "칸:사업명"


def test_overlay_geometry_from_editor_model_not_screen_caret():
    """남이 쥔 칸 자리는 기안기 내부 커서 모델(쪽 좌표)로 재고 화면 커서로 재지 않는다 — 화면 커서는 그리기 차례에 늦게 옮겨져
    가려진·초점 없는 창에서 엉뚱한 자리가 나왔다. 이름 깃발은 칸 끝 커서 위에, 내 창이 쥔 칸에는 표시하지 않고 이름에 「(다른 창)」 을 붙이지 않는다.
    재기는 커서를 칸에 넣었다 빼므로 빈 칸(안내 글이 지워진다)·누르거나 치는 중(막 누른 자리를 덮는다)에는 하지 않고, 남의 커서만 있는 칸은 그리지 않는다.
    머리줄 맨 끝 「나」 동그라미로 내가 누구인지 보인다."""
    root = Path(__file__).resolve().parents[1] / "src/zzaimy/app/static"
    w, c = (root / "webhwp-wrapper.js").read_text(), (root / "webhwp-collab.js").read_text()
    assert "hwpCaret.KNi()" in w and "CalcCaretPos" in w and "measureField" not in w + c
    ui = c[c.index("function ui("):]
    assert "caretRect" not in ui and "(다른 창)" not in ui
    assert "co-box hl" in ui and "co-caret" in ui and "co-people" in ui        # 구글 독스 꼴(옅은 바탕·커서·이름 깃발)·같이 있는 사람 목록
    assert "kind: 'here'" not in ui                                             # 보는 중인 칸은 문서 위에 그리지 않는다
    assert "x === ''" in ui and "< 1500" in ui                                  # 빈 칸·입력 직후에는 재지 않는다
    assert "'co-av me', '나'" in ui                                             # 나 동그라미
    assert "co-block" in ui and "emitLocal('blocked'" in ui                     # 남의 칸 누름은 막고 알린다(글이 앞 칸으로 새지 않게)
    assert "'co-box multi'" in ui and "'co-box bar'" not in ui                  # 머리줄 .bar 꾸밈과 겹치지 않게
    assert "auraLabel()" in ui and "heldBy(" in ui                              # AURA · 시킨 사람, 쓰는 중은 임대로
    assert "lastInput = Date.now(); fire('activity'" in w                       # 누름도 입력으로 셈(재기 미룸)
    assert "lk.client !== eng.client" in ui                                     # 같은 사람이라도 다른 창만
    assert "acquire(f)" in c and "letGo(prev, true)" in c                       # 처음 고칠 때 잡고, 옮기면 바로 놓는다
    assert "lease-lost" not in c                                                # 풀렸다는 안내가 남아 막히지 않게


def test_opening_old_file_turns_line_breaks_into_paragraphs_once(tmp_path, monkeypatch):
    """예전 판의 필드 안 줄 나눔은 파일을 열 때 한 번 문단 나눔으로 — 앞 판은 .판/ 에 남고, 열린 다른 화면은 「문서 저장」 으로 새 판을 연다."""
    import io as _io
    import zipfile as _zip
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    p = Path(webhwp.get(db, fid)["path"])
    data = ww.set_fields(p.read_bytes(), {"절:1. 변경 사유": "가"})[0]
    zin = _zip.ZipFile(_io.BytesIO(data))
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w") as z:
        for i in zin.infolist():
            b = zin.read(i.filename)
            if i.filename == "Contents/section0.xml":
                b = b.decode().replace("<hp:t>가</hp:t>", "<hp:t>가<hp:lineBreak/>나</hp:t>", 1).encode()
            z.writestr(i, b)
    p.write_bytes(buf.getvalue())
    got = kim.get(f"/hwp/{fid}/file").content
    assert b"lineBreak" not in _zip.ZipFile(_io.BytesIO(got)).read("Contents/section0.xml")
    assert ww.field_values(got)["절:1. 변경 사유"] == "가\n나"
    olds = list((p.parent / ".판").glob(f"{p.stem} *.hwpx"))
    assert any(b"lineBreak" in _zip.ZipFile(o).read("Contents/section0.xml") for o in olds)      # 바꾸기 전 판이 남는다
    assert any(e["kind"] == "doc-saved" and e["data"]["by"] == ce.AGENT for e in ce.events_since(db, fid, 0))
    n_versions = len(olds)
    kim.get(f"/hwp/{fid}/file")
    assert len(list((p.parent / ".판").glob(f"{p.stem} *.hwpx"))) == n_versions                    # 두 번째 열기는 그대로


def test_long_section_is_not_truncated_and_caps_reject_instead(tmp_path, monkeypatch):
    """50,000자 넘는 절도 끝까지 받고 파일에 들어간다(예전에는 잘려 칸 끝 글이 사라졌다). 상한을 넘으면 자르지 않고 거절한다."""
    from zzaimy.app import webhwp_fields as wf
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    assert ce.TEXT_MAX == wf.TEXT_MAX >= 1_000_000 and ce.DOC_TEXT_MAX == wf.DOC_TEXT_MAX
    long = "\n".join("가나다라마바사아" * 120 for _ in range(70))            # 약 67,000자
    r = ce.apply_ops(db, fid, "kim", "k1", [{"seq": 1, "field": "절:1. 변경 사유", "base": 0, "text": long + "끝"}])
    assert r[0]["ok"] and ce.state(db, fid)["절:1. 변경 사유"]["text"].endswith("끝")
    ce.commit_now(db, base, fid)
    assert _vals(app, fid)["절:1. 변경 사유"] == long + "끝"
    assert kim.get(f"/hwp/{fid}/coedit").json()["conf"]["field_max"] == ce.TEXT_MAX
    monkeypatch.setattr(ce, "TEXT_MAX", 100)
    r = ce.apply_ops(db, fid, "kim", "k1", [{"seq": 2, "field": "칸:사업명", "base": 0, "text": "가" * 101}])
    assert r[0]["reason"] == "too_long" and "칸:사업명" not in ce.state(db, fid)
    monkeypatch.setattr(ce, "TEXT_MAX", 1_000_000)
    monkeypatch.setattr(ce, "DOC_TEXT_MAX", len(long) + 10)
    r = ce.apply_ops(db, fid, "kim", "k1", [{"seq": 3, "field": "칸:사업명", "base": 0, "text": "가" * 20}])
    assert r[0]["reason"] == "too_big"
    r = ce.apply_ops(db, fid, "kim", "k1", [{"seq": 4, "field": "칸:사업명", "base": 0, "text": "가"}])
    assert r[0]["ok"]


def test_delta_ops_apply_on_server_text_and_fall_back_to_full_text(tmp_path, monkeypatch):
    """바뀐 부분 {at, del, ins, fp} — 서버가 지금 글에 넣어 보고 지문이 맞을 때만 받는다. 판이 어긋나거나 지문이 다르면 「delta」 로 돌려보내
    (기록하지 않아 같은 seq 로 다시 보낼 수 있다) 편집 화면이 전체 글로 다시 보낸다. 알림·고친 기록도 바뀐 부분만, 스냅숏으로 되살린다."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    F = "절:1. 변경 사유"
    base = "가" * 30000 + "\n둘째 문단"
    assert ce.apply_ops(db, fid, "kim", "k1", [{"seq": 1, "field": F, "base": 0, "text": base}])[0]["version"] == 1
    want = base[:100] + "새 글" + base[103:]
    op = {"seq": 2, "field": F, "base": 1, "at": 100, "del": 3, "ins": "새 글", "fp": ce.fingerprint(want)}
    r = ce.apply_ops(db, fid, "kim", "k1", [op])[0]
    assert r["ok"] and r["version"] == 2 and ce.state(db, fid)[F]["text"] == want
    ev = [e for e in ce.events_since(db, fid, 0, full=False) if e["kind"] == "field-updated"][-1]["data"]
    assert "text" not in ev and ev["ins"] == "새 글" and ev["base"] == 1 and ev["fp"] == ce.fingerprint(want)
    # 판이 어긋남 → delta(기록 안 함), 지문이 다름 → delta
    bad = dict(op, seq=3, base=1)
    assert ce.apply_ops(db, fid, "kim", "k1", [bad])[0]["reason"] == "delta"
    assert ce.apply_ops(db, fid, "kim", "k1", [dict(op, seq=3, base=2, fp="00000000")])[0]["reason"] == "delta"
    good = want + "끝"
    r = ce.apply_ops(db, fid, "kim", "k1", [{"seq": 3, "field": F, "base": 2, "at": len(want), "del": 0, "ins": "끝", "fp": ce.fingerprint(good)}])[0]
    assert r["ok"] and r["version"] == 3                                   # 같은 seq 로 다시 보내면 이번에는 받는다
    # 남이 쥔 칸에 바뀐 부분 → delta(전체 글로 다시 오면 그때 충돌 사본)
    ce.acquire(db, fid, F, "park", "p1")
    assert ce.apply_ops(db, fid, "kim", "k1", [{"seq": 4, "field": F, "base": 3, "at": 0, "del": 0, "ins": "x", "fp": ""}])[0]["reason"] == "delta"
    assert ce.apply_ops(db, fid, "kim", "k1", [{"seq": 5, "field": F, "base": 3, "text": "x" + good}])[0]["reason"] == "locked"
    # 고친 기록: 처음은 스냅숏, 그 뒤는 바뀐 부분만 — history 가 판마다 글을 되살린다
    with db._conn() as conn:
        rows = conn.execute("SELECT version, LENGTH(text), delta FROM webhwp_field_edits WHERE fid = ? AND field = ? ORDER BY id", (fid, F)).fetchall()
    assert rows[0][1] == len(base) and rows[1][1] == 0 and rows[1][2]
    assert [h["text"] for h in ce.history(db, fid, F)] == [base, want, good]
    # 칸 글 전체만 따로 받기(알림이 끊겼을 때)
    j = kim.get(f"/hwp/{fid}/coedit", params={"field": [F]}).json()
    assert list(j["fields"]) == [F] and j["fields"][F]["text"] == good and j["fields"][F]["version"] == 3


def test_snapshots_every_n_versions(tmp_path, monkeypatch):
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    monkeypatch.setattr(ce, "SNAP_EVERY", 3)
    t = ""
    for i in range(1, 8):
        nt = t + str(i)
        ce.apply_ops(db, fid, "kim", "k1", [{"seq": i, "field": "칸:사업명", "base": i - 1, "at": len(t), "del": 0, "ins": str(i), "fp": ce.fingerprint(nt)}])
        t = nt
    with db._conn() as conn:
        snaps = [v for v, d in conn.execute("SELECT version, delta FROM webhwp_field_edits WHERE fid = ? ORDER BY id", (fid,)).fetchall() if not d]
    assert snaps == [1, 3, 6] and ce.history(db, fid, "칸:사업명")[-1]["text"] == "1234567"


def test_first_delta_uses_file_value_as_base(tmp_path, monkeypatch):
    """표에 아직 없는 칸의 첫 고침도 바뀐 부분으로 — 서버가 파일의 지금 값을 바탕으로 맞춰 본다(받는 화면이 칸 전체를 다시 그리지 않게)."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    p = Path(webhwp.get(db, fid)["path"])
    p.write_bytes(ww.set_fields(p.read_bytes(), {"절:1. 변경 사유": "파일 글\n둘째"})[0])
    want = "파일 글\n둘째 더함"
    r = ce.apply_ops(db, fid, "kim", "k1", [{"seq": 1, "field": "절:1. 변경 사유", "base": 0, "at": 7, "del": 0, "ins": " 더함", "fp": ce.fingerprint(want)}])[0]
    assert r["ok"] and ce.state(db, fid)["절:1. 변경 사유"]["text"] == want


def test_editor_sends_and_applies_deltas_and_keeps_cursor_by_field():
    """편집 화면: 보내기는 바뀐 부분(서버가 안 받으면 전체 글로 다시), 받기는 그 자리만 고치기(applyDelta, 안 맞으면 통째 쓰기),
    판이 끊기면 그 칸만 다시 받기. 커서는 든 칸 기준으로 되돌리고, 남의 고침을 넣은 뒤에는 되돌리기를 잠근다."""
    root = Path(__file__).resolve().parents[1] / "src/zzaimy/app/static"
    w, c = (root / "webhwp-wrapper.js").read_text(), (root / "webhwp-collab.js").read_text()
    assert "function diffText(" in c and "fp: fp(op.text)" in c and "r.reason === 'delta'" in c and "full: true" in c
    assert "hwp.applyDelta(" in c and "fetchFields([f])" in c and "'/coedit?'" in c
    assert "slice(0, 50000)" not in c and "field_max" in c
    assert "function applyDelta(" in w and "SelectText(" in w and "InsertText" in w and "setEditable(name, true, true)" in w
    assert "c.rel = {field: f" in w and "LockCommand('Undo', on)" in w
    assert "hwp.lockUndo(true)" in c and "hwp.lockUndo(false)" in c


def test_release_does_not_wait_for_write_back_and_cache_follows_file(tmp_path, monkeypatch):
    """임대를 놓을 때 써 넣기는 뒤에서(놓기 요청을 붙잡지 않는다). 써 넣기는 파일 값 캐시를 이어 쓰고, 파일이 밖에서 바뀌면 다시 푼다."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    db.set_setting("coedit:commit_delay", "60")
    calls = []
    real = ce.commit_now
    monkeypatch.setattr(ce, "commit_now", lambda *a, **k: (calls.append(time.time()), time.sleep(0.5), real(*a, **k))[-1])
    ce.acquire(db, fid, "칸:사업명", "kim", "k1")
    ce.apply_ops(db, fid, "kim", "k1", [{"seq": 1, "field": "칸:사업명", "base": 0, "text": "첫 값"}])
    t = time.time()
    assert ce.release(db, fid, "칸:사업명", "kim", base=base)
    assert time.time() - t < 0.3                                         # 놓기는 바로 돌아온다
    deadline = time.time() + 5
    while time.time() < deadline and ce.dirty(db, fid):
        time.sleep(0.05)
    assert _vals(app, fid)["칸:사업명"] == "첫 값"
    monkeypatch.setattr(ce, "commit_now", real)
    ce.apply_ops(db, fid, "kim", "k1", [{"seq": 2, "field": "칸:사업명", "base": 1, "text": "둘째 값"}])
    ce.commit_now(db, base, fid)
    assert _vals(app, fid)["칸:사업명"] == "둘째 값" and ce._file_values(db, fid)["칸:사업명"] == "둘째 값"
    p = Path(webhwp.get(db, fid)["path"])                                # 밖에서 파일이 바뀌면(문서 전체 저장) 캐시는 다시 푼다
    p.write_bytes(ww.set_fields(p.read_bytes(), {"칸:사업명": "밖에서"})[0])
    assert ce._file_values(db, fid)["칸:사업명"] == "밖에서"


def test_agent_writes_as_deltas_and_streams_sections_in_parallel(tmp_path, monkeypatch):
    """AURA 쓰기도 바뀐 부분으로 — 알림에 글 전체가 실리지 않는다. 여러 절을 절마다 따로 임대(AURA 창 번호)로 나란히 흘려 쓰고,
    사람이 쥔 절은 건너뛴다. 다 쓰면 놓는다."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    ce.put(db, fid, ce.AGENT, {"절:1. 변경 사유": "가" * 5000})
    ce.put(db, fid, ce.AGENT, {"절:1. 변경 사유": "가" * 5000 + "\n덧붙임"})
    ev = [e for e in ce.events_since(db, fid, 0, full=False) if e["kind"] == "field-updated"][-1]["data"]
    assert "text" not in ev and ev["ins"] == "\n덧붙임"
    ce.acquire(db, fid, "절:2. 변경 내역", "kim", "k1")
    seen = []
    real = ce.acquire
    monkeypatch.setattr(ce, "acquire", lambda *a, **k: (seen.append(a[4] if len(a) > 4 else k.get("client")), real(*a, **k))[1])
    done, held = ce.put_sections(db, fid, {"절:1. 변경 사유": "첫 문단\n둘째 문단", "절:3. 성과지표·사업 추진에 미치는 영향": "가\n나\n다",
                                           "절:2. 변경 내역": "못 씀"}, base=base)
    assert sorted(done) == ["절:1. 변경 사유", "절:3. 성과지표·사업 추진에 미치는 영향"] and held == {"절:2. 변경 내역": "kim"}
    assert len(set(seen)) == 3 and all(c.startswith("aura:") for c in seen)          # 절마다 따로 창 번호
    st = ce.state(db, fid)
    assert st["절:3. 성과지표·사업 추진에 미치는 영향"]["text"] == "가\n나\n다" and st["절:3. 성과지표·사업 추진에 미치는 영향"]["version"] == 3
    assert set(ce.locks(db, fid)) == {"절:2. 변경 내역"}                                # AURA 는 다 쓰고 놓았다


def test_presence_carries_page_and_own_live_windows(tmp_path, monkeypatch):
    """같이 있는 사람에 쪽 번호(「이름 · N쪽 · 절」), presence 답에 내 살아 있는 창 — 닫힌 내 옛 창이 쥔 칸은 새 창이 이어받는다."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    kim.post(f"/hwp/{fid}/presence", json={"client": "k-old", "field": "칸:사업명", "page": 12})
    j = park.post(f"/hwp/{fid}/presence", json={"client": "p1", "field": "", "page": 3}).json()
    me = [p for p in j["people"] if p["user"] == "kim"][0]
    assert me["page"] == 12 and j["windows"] == [{"user": "park", "client": "p1"}]       # 창 목록은 내 것만
    j2 = kim.post(f"/hwp/{fid}/presence", json={"client": "k-new", "field": ""}).json()
    assert {w["client"] for w in j2["windows"]} == {"k-old", "k-new"}
    snap = kim.get(f"/hwp/{fid}/coedit").json()
    assert {"user": "kim", "client": "k-old"} in snap["windows"]


def test_editor_slices_inbox_measures_one_field_and_takes_over_closed_window():
    root = Path(__file__).resolve().parents[1] / "src/zzaimy/app/static"
    w, c = (root / "webhwp-wrapper.js").read_text(), (root / "webhwp-collab.js").read_text()
    assert "SLICE_MS" in c and "prioritize" in c and "function stepsFor(" in c                 # 나눠 넣기·보이는 칸 먼저·전체 글도 바뀐 부분으로
    assert "hwp.fieldGeom([pick])" in c and "invalidateFrom(" in c and "nearView(" in c          # 한 번에 한 칸, 화면 근처만
    assert "otherWindowGone(" in c and "learnWindows(" in c                                       # 닫힌 내 옛 창 칸 이어받기
    assert "p.page + '쪽'" in c and "KeyIndicator()" in w and "page: page" in c                  # 쪽 번호


def test_light_snapshot_has_versions_and_fingerprints_only(tmp_path, monkeypatch):
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    ce.apply_ops(app.state.db, fid, "kim", "k1", [{"seq": 1, "field": "칸:사업명", "base": 0, "text": "값"}])
    full = kim.get(f"/hwp/{fid}/coedit").json()["fields"]["칸:사업명"]
    light = kim.get(f"/hwp/{fid}/coedit", params={"light": 1}).json()["fields"]["칸:사업명"]
    assert full["text"] == "값" and "text" not in light and light["fp"] == full["fp"] == ce.fingerprint("값") and light["version"] == 1
    c = (Path(__file__).resolve().parents[1] / "src/zzaimy/app/static/webhwp-collab.js").read_text()
    assert "resync(false, true)" in c and "'/coedit?light=1'" in c and "function checkSet(" in c


def test_server_participant_sends_hwpx_fragment_as_rich(tmp_path, monkeypatch):
    """AURA·논문 작성기가 칸을 서식째(hwpx 조각) — 칸 글과 조각이 표·알림에 실리고, 편집 화면은 SetTextFile(HWPX, insertfile)로 넣는다."""
    import base64
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    frag = _fragment()
    r = ce.put_rich(db, fid, "절:1. 변경 사유", "조각 글", frag)
    assert r["ok"]
    rich = json.loads(ce.state(db, fid)["절:1. 변경 사유"]["rich"])
    sent = base64.b64decode(rich["b64"])                          # 검사한 나무를 다시 써서 보낸다 — 다시 검사해도 그대로 지나고, 글은 같다
    assert rich["fmt"] == "hwpx" and ce.sanitize_fragment(sent)[1] == ""
    import io as _io, zipfile as _zip
    assert "조각 문단" in _zip.ZipFile(_io.BytesIO(sent)).read("Contents/section0.xml").decode()
    ev = [e for e in ce.events_since(db, fid, 0, full=False) if e["kind"] == "field-updated"][-1]["data"]
    assert json.loads(ev["rich"])["fmt"] == "hwpx" and ev["text"] == "조각 글" and ev["trusted"] is True
    w = (Path(__file__).resolve().parents[1] / "src/zzaimy/app/static/webhwp-wrapper.js").read_text()
    c = (Path(__file__).resolve().parents[1] / "src/zzaimy/app/static/webhwp-collab.js").read_text()
    assert "fmt = 'HWPX'" in w and "isHwpxRich" in c and "richField[f] !== x.version" in c


def _fragment(extra_xml: str = "", extra_entry: str | None = None) -> bytes:
    import io as _io
    import zipfile as _zip
    from hwpx import HwpxDocument
    doc = HwpxDocument.new()
    doc.add_paragraph("조각 문단")
    t = doc.add_table(1, 2)
    t.set_cell_text(0, 0, "표")
    data = doc.to_bytes()
    if not extra_xml and not extra_entry:
        return data
    zin, buf = _zip.ZipFile(_io.BytesIO(data)), _io.BytesIO()
    with _zip.ZipFile(buf, "w") as z:
        for i in zin.infolist():
            b = zin.read(i.filename)
            if i.filename == "Contents/section0.xml" and extra_xml:
                b = b.decode().replace("조각 문단", "조각 문단" + extra_xml, 1).encode()
            z.writestr(i, b)
        if extra_entry:
            z.writestr(extra_entry, b"x")
    return buf.getvalue()


def test_rich_fragments_only_from_server_participants_and_checked(tmp_path, monkeypatch):
    """보안 점검: 사람 화면이 /ops 로 보낸 hwpx 조각은 받지 않는다(남의 화면에 아무 조각이나 넣게 된다). 서버 참여자의 조각도 필드·OLE·링크·
    외부 주소·스크립트 같은 부분이 있으면 받지 않는다. 기안기 JSON 블록(시험 기능)은 설정이 켜져 있고 그 칸 임대를 쥔 창이 보낸 것만 — 아니면 글만."""
    import base64
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    F = "절:1. 변경 사유"
    hx = json.dumps({"fmt": "hwpx", "b64": base64.b64encode(_fragment()).decode()})
    r = kim.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [{"seq": 1, "field": F, "base": 0, "text": "글", "rich": hx}]}).json()["results"][0]
    assert r["reason"] == "rich_denied" and F not in ce.state(db, fid)
    # 조각 안 누름틀은 떼어 내고 받는다(칸 안에 칸이 생기지 않게)
    withf = _fragment('</hp:t></hp:run><hp:run charPrIDRef="0"><hp:ctrl><hp:fieldBegin id="1" type="CLICK_HERE" name="칸:사업명"/></hp:ctrl><hp:t>')
    assert ce.put_rich(db, fid, "칸:담당자", "글", withf)["ok"]
    sent = base64.b64decode(json.loads(ce.state(db, fid)["칸:담당자"]["rich"])["b64"])
    assert b"fieldBegin" not in __import__("zipfile").ZipFile(__import__("io").BytesIO(sent)).read("Contents/section0.xml")
    for bad in (_fragment('</hp:t></hp:run><hp:run charPrIDRef="0"><hp:ctrl><hp:fieldBegin id="2" type="HYPERLINK" name="x"/></hp:ctrl><hp:t>'),
                _fragment(extra_xml="", extra_entry="Scripts/headerScripts"),
                _fragment('</hp:t></hp:run><hp:run charPrIDRef="0"><hp:pic href="http://example.invalid/x.png"/><hp:t>'),
                b"not a zip"):
        assert ce.put_rich(db, fid, F, "글", bad)["reason"] == "rich_bad"
    assert F not in ce.state(db, fid)
    # JSON 블록: 설정이 꺼져 있으면 글만, 켜져 있어도 임대를 쥔 창이 아니면 글만
    kim.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [{"seq": 2, "field": F, "base": 0, "text": "글", "rich": "{\"x\":1}"}]})
    assert ce.state(db, fid)[F]["text"] == "글" and "rich" not in ce.state(db, fid)[F]
    db.set_setting("coedit:rich", "1")
    kim.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [{"seq": 3, "field": F, "base": 1, "text": "글2", "rich": "{\"x\":2}"}]})
    assert "rich" not in ce.state(db, fid)[F]
    ce.acquire(db, fid, F, "kim", "k1")
    kim.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [{"seq": 4, "field": F, "base": 2, "text": "글3", "rich": "{\"x\":3}"}]})
    assert ce.state(db, fid)[F]["rich"] == "{\"x\":3}"
    c = (Path(__file__).resolve().parents[1] / "src/zzaimy/app/static/webhwp-collab.js").read_text()
    assert "d.trusted === true || d.fromState === true" in c and "d.at + d.del > cpLen(s)" in c


def _raw_fragment(mutate=None, extra: list | None = None) -> bytes:
    """조각을 바이트 수준에서 고쳐 만든다(같은 이름 두 번·이상한 이름 같은 것도) — 검사 우회 시험용."""
    import io as _io
    import zipfile as _zip
    base = _fragment()
    zin, buf = _zip.ZipFile(_io.BytesIO(base)), _io.BytesIO()
    with _zip.ZipFile(buf, "w") as z:
        for i in zin.infolist():
            b = zin.read(i.filename)
            if i.filename == "Contents/section0.xml" and mutate:
                b = mutate(b.decode()).encode()
            z.writestr(i.filename, b)
        for n, b in extra or []:
            z.writestr(n, b)
    return buf.getvalue()


def test_fragment_sanitizer_parses_xml_and_rejects_crafted_fragments():
    """보안 점검 3: 조각 검사는 진짜 XML 해석기로 — 접두어 바꾸기·글자 참조·주석·CDATA·대소문자·같은 이름 두 번·경로 이상·DTD 개체로 빠져나가지 못한다."""
    P = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    ins = lambda frag: (lambda x: x.replace("조각 문단", "조각 문단</hp:t></hp:run><hp:run charPrIDRef=\"0\">" + frag + "<hp:t>", 1))
    bad = {
        "다른 접두어의 필드": _raw_fragment(ins(f'<zz:ctrl xmlns:zz="{P}"><zz:fieldBegin type="HYPERLINK" name="x"/></zz:ctrl>')),
        "글자 참조로 숨긴 주소": _raw_fragment(ins('<hp:pic href="&#104;ttp://evil.invalid/a.png"/>')),
        "글자 참조로 숨긴 javascript": _raw_fragment(ins('<hp:pic alt="&#106;avascript:alert(1)"/>')),
        "OLE(대소문자)": _raw_fragment(ins('<hp:OLE/>')),
        "주석으로 가른 태그(잘못된 XML)": _raw_fragment(ins('<hp:fiel<!-- -->dBegin/>')),
        "DTD 개체": _raw_fragment(lambda x: x.replace("?>", '?><!DOCTYPE r [<!ENTITY e "x">]>', 1)),
        "같은 이름 두 번": _raw_fragment(extra=[("Contents/section0.xml", b"<x/>")]),
        "대소문자만 다른 이름": _raw_fragment(extra=[("contents/section0.xml", b"<x/>")]),
        "경로 넘기": _raw_fragment(extra=[("../evil.xml", b"<x/>")]),
        "모르는 부분": _raw_fragment(extra=[("Scripts/sourceScripts", b"x")]),
        "그림 이름과 다른 내용": _raw_fragment(extra=[("BinData/BIN0001.png", b"MZ\x90\x00")]),
    }
    for why, data in bad.items():
        out, reason = ce.sanitize_fragment(data)
        assert reason and out == b"", why
    # CDATA 속 태그는 글일 뿐 — 다시 쓰면 이스케이프된 글로 나가고 요소가 되지 않는다
    out, reason = ce.sanitize_fragment(_raw_fragment(lambda x: x.replace("조각 문단", "조각<![CDATA[<hp:fieldBegin/>]]>", 1)))
    assert reason == ""
    import io as _io, zipfile as _zip
    sec = _zip.ZipFile(_io.BytesIO(out)).read("Contents/section0.xml").decode()
    assert "<hp:fieldBegin" not in sec and "&lt;hp:fieldBegin" in sec


def test_client_and_field_names_have_one_canonical_parse(tmp_path, monkeypatch):
    """보안 점검 3: 창 번호·칸 이름은 webhwp_coedit 한 곳에서 가르고 자르지 않는다 — 표 안 열쇠의 구분자(\\x1f)·제어 문자·공백·너무 긴 값은 400/bad."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    for c in ("k\x1fpark", "k 1", "k\n1", "x" * 81, ""):
        r = kim.post(f"/hwp/{fid}/ops", json={"client": c, "ops": [{"seq": 1, "field": "칸:사업명", "base": 0, "text": "x"}]})
        assert r.status_code == 400, repr(c)
        assert kim.post(f"/hwp/{fid}/lease", json={"field": "칸:사업명", "client": c}).status_code == 400
        assert kim.post(f"/hwp/{fid}/presence", json={"client": c}).status_code == (400 if c else 200)
    r = ce.apply_ops(app.state.db, fid, "kim", "k1", [{"seq": 1, "field": "칸:" + "가" * 300, "text": "x"}, {"seq": 2, "field": "칸:\x1f사업명", "text": "x"}])
    assert [x["reason"] for x in r] == ["bad", "bad"]
    assert kim.post(f"/hwp/{fid}/lease", json={"field": "칸:" + "가" * 300, "client": "k1"}).status_code == 400
    import pytest as _pt
    with _pt.raises(ValueError):
        ce.ckey("kim\x1fpark", "k1")
    # put_rich 는 서버 안에서만 — 이 길로 들어오는 경로가 없다
    from zzaimy.app import main as _m
    assert not any("rich" in getattr(r_, "path", "") for r_ in app.routes)


def test_sse_sends_named_beat_so_client_can_spot_dead_stream(tmp_path, monkeypatch):
    """조용한 동안 이름 붙은 beat 알림을 보낸다(주석 줄은 EventSource 스크립트가 못 본다) — 화면은 STALE_MS 동안 아무 알림이 없으면 새로 붙는다."""
    import uvicorn
    from zzaimy.app import webhwp_coedit_routes as cr
    monkeypatch.setattr(cr, "POLL", 0.1)
    monkeypatch.setattr(cr, "BEAT", 0.3)
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
        got: list = []

        def listen():
            with httpx.Client(cookies={"zz_session": park.cookies.get("zz_session")}, timeout=10) as c:
                with c.stream("GET", f"http://127.0.0.1:{port}/hwp/{fid}/events") as r:
                    for line in r.iter_lines():
                        if line == "event: beat":
                            got.append(line)
                            return
        t = threading.Thread(target=listen, daemon=True)
        t.start()
        t.join(5)
        assert got, "조용한 동안 beat 알림이 오지 않았다"
    finally:
        server.should_exit = True
        th.join(5)
    js = (Path(__file__).resolve().parents[1] / "src/zzaimy/app/static/webhwp-collab.js").read_text(encoding="utf-8")
    assert "addEventListener('beat'" in js and "STALE_MS" in js and "connect(true)" in js


def test_prune_drops_old_rows_and_rows_of_deleted_documents(tmp_path, monkeypatch):
    """칸 글 표는 문서가 살아 있는 동안 두고, 쌓이는 표는 기간이 지나면, 지운 문서의 줄은 모든 표에서 지운다."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    r = kim.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [{"seq": 1, "field": "칸:사업명", "base": 0, "text": "남김"}]})
    assert r.status_code == 200
    with db._conn() as c:
        c.execute("INSERT INTO webhwp_field_edits (fid, field, version, text, author, at) VALUES (?, ?, ?, ?, ?, ?)",
                  (fid, "칸:사업명", 0, "아주 옛 글", "kim", time.time() - 40 * 86400))
        c.execute("INSERT INTO webhwp_field_state (fid, field, version, text, author, at) VALUES (?, ?, ?, ?, ?, ?)",
                  (999999, "칸:사업명", 1, "지운 문서", "kim", time.time()))
    ce.prune(db)
    with db._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM webhwp_field_state WHERE fid = ?", (fid,)).fetchone()[0] == 1   # 살아 있는 문서 칸 글은 그대로
        assert c.execute("SELECT COUNT(*) FROM webhwp_field_edits WHERE text = '아주 옛 글'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM webhwp_field_state WHERE fid = 999999").fetchone()[0] == 0
    webhwp.trash(db, Path(app.state.data_dir), fid, user="kim")
    ce.prune(db)
    with db._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM webhwp_field_state WHERE fid = ?", (fid,)).fetchone()[0] == 0     # 휴지통으로 보낸 문서도


def test_korean_typing_sends_at_each_commit_and_empty_field_counts():
    """한글은 쉬지 않고 치면 조합이 끝없이 이어진다 — 글자 확정(조합 끝, 거품 단계)마다 그 칸을 읽어 보내고,
    조합 중에도 담아 둔 글은 기다리지 않고 보낸다. 칸을 다 지워 커서 칸 이름이 비어도 방금 고치던 칸의 고침이다(영상 10-25)."""
    root = Path(__file__).resolve().parents[1] / "src/zzaimy/app/static"
    w = (root / "webhwp-wrapper.js").read_text(encoding="utf-8")
    c = (root / "webhwp-collab.js").read_text(encoding="utf-8")
    assert "d.addEventListener('compositionend', function () { fire('commit', {}); }, false);" in w and "onCommit:" in w
    assert "hwp.onCommit(function" in c and "collect(); send();" in c
    assert "if (hwp.isComposing()) { send(); flushTimer = setTimeout(flush, 60); return; }" in c
    assert "if (!f && lastEdit.f && now() - lastEdit.at < 5000) f = lastEdit.f;" in c
