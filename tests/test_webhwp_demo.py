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


def _new(dev, kind: str = "") -> int:
    r = dev.post("/dev/coedit-demo/new", data={"kind": kind}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].split("id=")[-1])


def H(slot):
    return {"X-Aura-Demo": slot}


def test_page_admin_only_and_three_panes(tmp_path, monkeypatch):
    app, dev, kim = _admin(tmp_path, monkeypatch)
    assert kim.get("/dev/coedit-demo").status_code == 403
    assert kim.post("/dev/coedit-demo/new", follow_redirects=False).status_code == 403
    # 열자마자 빈 시연 문서가 생겨 세 칸으로 — 한 갈래(세 사람 + AURA 4 절), 단추는 「AURA 정리 쓰기」·「새 빈 문서」
    r = dev.get("/dev/coedit-demo", follow_redirects=False)
    assert r.status_code == 303
    fid = int(r.headers["location"].split("id=")[-1])
    page = dev.get(f"/dev/coedit-demo?id={fid}").text
    for s in "abc":
        assert f"/webhwpctrl/aura-edit?id={fid}&amp;demo={s}" in page
    assert page.count("<iframe") == 3 and "김가람" in page and "시연 계정" in page and "새 빈 문서" in page
    assert "AURA 양식 만들기" in page and "AURA 정리 쓰기" not in page and "어디를 고쳐도" not in page
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
    # 「새 시연 문서」 는 빈 문서 — 제목 말고 미리 든 글 없음, 시연 폴더에(양식은 AURA 가 불러온다)
    f = webhwp.get(app.state.db, fid)
    assert webhwp.dept_of_path(Path(app.state.data_dir), f["path"]) == demo.ROOT and not webhwp.locked(app.state.db, fid)
    fs = ww.fields(Path(f["path"]).read_bytes())
    assert not any(x["text"] for x in fs) and not demo.form_loaded_id(app, fid)
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
                           ("lock", None), ("save", None)):
            assert dev.post(f"/hwp/{target}/{path}", headers=H("a"), json=body).status_code == 404, path
        for path in ("presence", "leave"):            # 볼 수 없는 문서는 없는 문서와 같은 답 — 아무것도 하지 않고 조용히(지운 문서로 온 옛 창과 같은 길)
            assert dev.post(f"/hwp/{target}/{path}", headers=H("a"), json={"client": "x"}).json() == {"ok": False, "gone": True}, path
        assert not ce.people(app.state.db, target)
    # 새 문서 화면도 시연 폴더 밖 부서로는 열지 못한다
    assert dev.get(f"/webhwpctrl/aura-edit?dept=기획처&demo=a").status_code == 403
    assert dev.get(f"/hwp/{fid}/coedit", headers=H("a")).status_code == 200


def test_three_slots_are_three_users(tmp_path, monkeypatch):
    app, dev, _ = _admin(tmp_path, monkeypatch)
    fid = _new(dev, "long")        # 칸이 있는 문서(양식 사본)로 — 빈 문서는 제목뿐이다
    db = app.state.db
    for s in "abc":
        assert dev.post(f"/hwp/{fid}/presence", headers=H(s), json={"client": f"w{s}", "field": ""}).json()["ok"]
    out = dev.post(f"/hwp/{fid}/presence", headers=H("a"), json={"client": "wa", "field": ""}).json()["people"]
    assert {p["user"]: p["name"] for p in out} == {"demo_a": "김가람", "demo_b": "이나래", "demo_c": "박다온"}
    dev.get(f"/hwp/{fid}/file", headers=H("a"))                     # 열면 칸 밖 문단(제목·빈 문단)을 칸으로 감싼다
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
    fid = _new(dev, "long")        # 칸이 있는 문서(양식 사본)로 — 빈 문서는 제목뿐이다
    db = app.state.db
    dev.get(f"/hwp/{fid}/file", headers=H("a"))
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
    # 시연 화면은 한 갈래(빈 문서) — 양식 사본은 화면을 열면 휴지통으로 가고 빈 문서가 새로 생긴다(10-26)
    from zzaimy.app import webhwp_write as _ww
    body = "".join(__import__("zipfile").ZipFile(__import__("io").BytesIO(Path(webhwp.get(db, fid)["path"]).read_bytes())).read(n).decode()
                   for n in ["Contents/section0.xml"])
    assert "○○○○ 사업" not in body and demo.scenario()["사업명"] in body       # 표지 자리표시도 시연용 값
    again = int(dev.post("/dev/coedit-demo/new", data={"kind": "long"}, follow_redirects=False).headers["location"].split("id=")[-1])
    assert again != fid and webhwp.get(db, fid) is None
    r = dev.get(f"/dev/coedit-demo?id={again}", follow_redirects=False)
    assert r.status_code == 303 and webhwp.get(db, again) is None and demo.is_blank_id(app, int(r.headers["location"].split("id=")[-1]))


def test_demo_values_fill_header_cells_only():
    vals = demo.demo_values([{"kind": "칸", "name": "칸:보고 부서", "text": ""}, {"kind": "칸", "name": "칸:보고 일자", "text": "2026. 1. 1."},
                             {"kind": "절", "name": "절:1. 개요", "text": ""}, {"kind": "칸", "name": "칸:사업명", "text": ""}])
    assert vals == {"칸:보고 부서": "기획팀", "칸:사업명": demo.scenario()["사업명"]}


def test_scenario_is_virtual_and_current():
    """시연 시나리오 — 사업명에 시연용 가상 사업임이 드러나고 연도·날짜는 오늘에서. 양식 요청은 칸 이름을 박지 않고 설정만 준다(짜임은 27B)."""
    from datetime import date
    d = date(2031, 5, 4)
    sc = demo.scenario(d)
    assert "2031" in sc["사업명"] and "시연" in sc["사업명"] and "가상" in sc["사업명"] and sc["작성일"] == "2031. 05. 04."
    fr = demo.form_request(d)
    for k, v in sc.items():
        assert f"- {k}: {v}" in fr                                   # 수치 관문의 근거(요청 글)에 설정이 그대로
    assert "칸:" not in fr and "절:" not in fr
    assert all("예시" in v for k, v in sc.items() if k in ("총사업비", "참여 대상"))   # 꾸민 수치에는 「예시」


DESIGN = {"title": "가상 사업계획서", "cover": False, "summary": ["사업명", "사업 기간", "총사업비", "주관 부서"],
          "sections": [{"level": 1, "title": t, "guide": g, "length": "500자 내외"} for t, g in
                       (("추진 배경", "사업이 필요한 까닭을 쓴다."), ("사업 목표", "목표와 성과 지표를 쓴다."),
                        ("추진 전략", "전략과 과제를 쓴다."), ("추진 체계", "조직과 역할을 쓴다."), ("기대 효과", "효과를 쓴다."))],
          "why": "짧은 계획서"}
BODY = ("1) 필요성\n가) 지역 기업의 생성형 AI 활용 수요가 늘어 실무 인재 양성이 필요함.\n나) 재학생 120명(예시)을 대상으로 실습 중심 교과를 운영함.\n"
        "2) 방향\n가) AI 기초·응용 교과와 현장 실습 과제를 잇는 과정을 마련함.")


def test_aura_makes_form_then_writes_sections_in_order(tmp_path, monkeypatch):
    """「AURA 양식 만들기」 — 27B 설계(가짜 연결)로 짧은 사업계획서를 빈 문서에 넣고 요약 칸을 채운다. 「이어 쓰기」 — 빈 본문 절을 1절부터
    차례로 세 개, 같은 관문·흘려 쓰기 길로. 단계·걸린 초가 남고, 결과 글은 실제로 쓴 절 수 그대로다(근거 없는 수치 글은 고쳐 쓴 뒤에만)."""
    from tests.test_delegate import FakeClient
    from zzaimy.app import webhwp_agent as wa
    app, dev, _ = _admin(tmp_path, monkeypatch)
    db = app.state.db
    fid = _new(dev)
    monkeypatch.setattr(wa.HwpTools, "STREAM_PACE", 0.001)
    sc = demo.scenario()
    answers = [json.dumps(DESIGN, ensure_ascii=False),
               json.dumps({"사업명": sc["사업명"], "칸:사업 기간": sc["사업 기간"], "총 사업비": sc["총사업비"], "없는 칸": "x"}, ensure_ascii=False),   # 27B 는 앞머리 없이·띄어 쓰기 달리 답하기도 한다(10-27 운영)
               BODY.replace("120명", "300명"),          # 1절 첫 초안 — 설정에 없는 수치(300) → 관문이 막는다
               BODY, BODY, BODY]                        # 2절 초안(미리), 1절 고쳐 쓰기, 3절
    client = FakeClient(answers)
    monkeypatch.setattr(demo, "make_client", lambda: client)
    r = dev.post("/dev/coedit-demo/aura", data={"id": fid}, follow_redirects=False)
    assert r.status_code == 303
    st = dev.get(f"/dev/coedit-demo/state?id={fid}").json()
    assert st["loaded"] and not st["busy"] and "만들어" in st["step"]
    names = [x["name"] for x in ww.fields(Path(webhwp.get(db, fid)["path"]).read_bytes())]
    secs = [n for n in names if n.startswith("절:")]
    assert secs[:3] == ["절:1. 추진 배경", "절:2. 사업 목표", "절:3. 추진 전략"]
    vals = ww.field_values(Path(webhwp.get(db, fid)["path"]).read_bytes())
    assert vals["칸:사업명"] == sc["사업명"] and vals["칸:총사업비"] == sc["총사업비"]
    assert "AURA 이어 쓰기" in dev.get(f"/dev/coedit-demo?id={fid}").text
    run = demo.run_state(db, fid)
    assert [x["step"].split(" —")[0] for x in run["log"]][:2] == ["양식 설계 중", "양식 넣는 중"] and all("sec" in x for x in run["log"])
    # 이어 쓰기 — 1·2·3 절 차례로
    dev.post("/dev/coedit-demo/aura", data={"id": fid}, follow_redirects=False)
    run = demo.run_state(db, fid)
    steps = [x["step"] for x in run["log"]]
    assert steps[1].startswith("1. 추진 배경 쓰는 중 (1/3)") and any("고쳐 쓰는 중" in x for x in steps) and steps[-1].startswith("3. 추진 전략 쓰는 중")
    vals = ww.field_values(Path(webhwp.get(db, fid)["path"]).read_bytes())
    for n in secs[:3]:
        assert "1) 필요성" in vals[n] and "300명" not in vals[n]
    assert not vals[secs[3]]
    last = [m for m in db.list_chats(int(webhwp.meta(db, fid)["demo_session"]), limit=50) if m["role"] == "assistant"][-1]["content"]
    assert last.startswith("3개 절을 썼습니다") and run["step"].startswith("3개 절을 썼습니다")


def test_room_guard_blocks_long_text_in_narrow_cells_and_guide_boxes(tmp_path, monkeypatch):
    """짧은 값 칸(좁은 표 칸)에 여러 문장, 작성방법 안내 상자 안 필드에 글 — 쓰기·칸 채우기 모두 막고 까닭(폭·글자 예산)을 알린다."""
    from zzaimy.app import webhwp_agent as wa
    app, dev, _ = _admin(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    hw = wa.HwpTools(db, base, 601, "kim", demo.ROOT, None, lambda: [])
    fid = _new(dev, "long")
    wa.attach(db, 601, fid)
    rooms = hw.rooms()
    narrow = next((k for k, v in rooms.items() if v["budget"] is not None), None)
    if narrow:
        long_text = "지역 구분은 대학 소재지의 행정구역 기준을 따라 기재함. " * 3
        assert "짧은 값 칸" in hw.write({"field": narrow, "text": long_text})
        assert "짧은 값 칸" in hw.fill({"values": {narrow: long_text[:140]}})
        assert any("짧은 값 칸" in ln for ln in hw.state_lines())
    rooms[next(iter(rooms))]["guide"] = True
    assert "안내 상자" in hw.room_problem(next(iter(rooms)), "글", rooms)


def test_editor_reports_unopenable_file(tmp_path, monkeypatch):
    """편집기가 칸을 하나도 못 잡으면(칸 많은 파일) 「기안기에서 안 열림」 표시 — 다시 잘 열리면 지운다."""
    app, dev, _ = _admin(tmp_path, monkeypatch)
    fid = _new(dev, "long")
    db = app.state.db
    assert dev.post(f"/hwp/{fid}/opened", json={"fields": 0}).json()["broken"] is True
    assert db.get_setting(f"webhwp_broken:{fid}", "")
    assert dev.post(f"/hwp/{fid}/opened", json={"fields": 12}).json()["broken"] is False
    assert not db.get_setting(f"webhwp_broken:{fid}", "")


def test_load_into_blank_reports_cover_and_replace_keeps_coedit_state(tmp_path, monkeypatch):
    """빈 시연 문서에 양식을 불러오면 도구 답에 문서 앞부분 글(표지·머리)이 함께 간다 — 27B 가 옛 사업명·연도를 보고 고칠지 정한다.
    칸 밖 글을 찾아 바꾸면(hwp_replace) 공동 편집 칸 글 표도 파일에 맞춘다(다음 문서 저장이 옛 글로 되돌리지 않게)."""
    from zzaimy.app import webhwp_agent as wa
    from zzaimy.app import webhwp_edit_ops as eo
    app, dev, _ = _admin(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    fid = _new(dev)
    wt.ensure_all(db, base)
    hw = wa.HwpTools(db, base, 501, demo.SLOTS["a"][0], demo.ROOT, None, lambda: [])
    wa.attach(db, 501, fid)
    tid = next(line.split()[0] for line in hw.forms({"query": "사업계획서"}).splitlines() if line.startswith("tpl:"))
    msg = hw.start({"form": tid})
    assert "이어진 빈 문서에 양식" in msg and "문서 앞부분 글" in msg and demo.form_loaded_id(app, fid)
    lines = wa.lead_lines(Path(webhwp.get(db, fid)["path"]).read_bytes())
    assert lines and all(f"「{x}」" in msg for x in lines[:3])
    dev.get(f"/hwp/{fid}/file", headers=H("a"))                     # 열면 칸 밖 문단을 「문단:n」 칸으로 감싼다
    vals = ww.field_values(Path(webhwp.get(db, fid)["path"]).read_bytes())
    para = next(k for k, v in vals.items() if k.startswith("문단:") and len(v.strip()) >= 2)
    word = vals[para].strip()[:2]
    ce.put(db, fid, "demo_b", {para: vals[para]})                    # 사람이 쓴 적 있는 칸 — 표에 글이 있다
    out = eo.EditTools(hw).run("hwp_replace", {"find": word, "replace": "시연"})
    assert "곳을 바꿨다" in out, out
    now = ww.field_values(Path(webhwp.get(db, fid)["path"]).read_bytes())[para]
    assert "시연" in now and ce.norm(ce.state(db, fid)[para]["text"]) == ce.norm(now)


def test_replace_reports_split_runs():
    """찾는 글이 글자 모양 경계로 쪼개져 있으면 「찾지 못했다」 에 까닭과 다른 길을 붙인다."""
    from zzaimy.app import webhwp_edit_ops as eo
    import io as _io
    import zipfile as _zf
    data = wt.build(wt.GRANT["budget_change"])
    z = _zf.ZipFile(_io.BytesIO(data))
    sec = z.read("Contents/section0.xml").decode()
    m = __import__("re").search(r"<hp:t>([^<]{4,})</hp:t>", sec)
    t = m.group(1)
    split = sec.replace(m.group(0), f"<hp:t>{t[:2]}</hp:t></hp:run><hp:run charPrIDRef=\"0\"><hp:t>{t[2:]}</hp:t>", 1)
    out = _io.BytesIO()
    with _zf.ZipFile(out, "w") as w:
        for info in z.infolist():
            w.writestr(info, split.encode() if info.filename == "Contents/section0.xml" else z.read(info.filename))
    try:
        eo.replace_text(out.getvalue(), {"find": t[:4], "replace": "x"})
        raise AssertionError("바꾸면 안 된다")
    except eo.EditError as e:
        assert "쪼개져" in str(e)
