"""칸 안 문단 단위 공동 편집(ADR-0070 덧붙임 10-27) — 한 칸을 두 사람이 서로 다른 문단에서 동시에 고치기.
문단 임대(커서 문단), 서버의 문단 세 갈래 합치기(보낸 쪽이 본 판 바탕), 같은 문단 겹침은 나중 것 + 충돌 사본, 개체가 든 칸은 칸째 임대."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_accounts import _login
from tests.test_webhwp import _setup, _staff, _up
from zzaimy.app import webhwp_coedit as ce
from zzaimy.app import webhwp_paras as wp
from zzaimy.app import webhwp_templates as wt
from zzaimy.app import webhwp_write as ww

F = "절:1. 변경 사유"
BASE = "가) 첫째 항목\n나) 둘째 항목\n다) 셋째 항목\n라) 넷째 항목"


def _doc(tmp_path, monkeypatch, data: bytes | None = None):
    app = _setup(tmp_path, monkeypatch)
    _staff(app, "park", "기획처")
    app.state.db.set_setting("user:kim:name", "김가람")
    app.state.db.set_setting("user:park:name", "박다온")
    kim, park = TestClient(app), TestClient(app)
    assert _login(kim, "kim", "pass-12345678") and _login(park, "park", "pass-12345678")
    fid = _up(kim, app, "기획처", "문단.hwpx", data or wt.build(wt.GRANT["budget_change"]))
    return app, kim, park, fid


def _seed(app, fid):
    """칸에 네 문단을 넣어 판 1 로 — 두 사람 모두 이 판을 보고 고치기 시작한다."""
    r = ce.apply_ops(app.state.db, fid, "kim", "k0", [{"seq": 1, "field": F, "base": 0, "text": BASE}])[0]
    assert r["ok"] and r["version"] == 1


def _edit(text: str, i: int, new: str) -> str:
    p = text.split("\n")
    p[i] = new
    return "\n".join(p)


def _delta_op(seq: int, base_v: int, old: str, new: str) -> dict:
    at, dl, ins = ce._diff(old, new)
    return {"seq": seq, "field": F, "base": base_v, "at": at, "del": dl, "ins": ins, "fp": ce.fingerprint(new)}


def test_merge3_rules():
    b = "a\nb\nc\nd"
    assert wp.merge3(b, "a\nB\nc\nd", "a\nb\nc\nD") == ("a\nB\nc\nD", [])              # 다른 문단 — 둘 다
    assert wp.merge3(b, "a\nb\nc\nD", "a\nNEW\nb\nc\nd") == ("a\nNEW\nb\nc\nD", [])    # 위에 문단 넣기 + 아래 고침
    assert wp.merge3(b, "a\nX\nc\nd", "a\nY\nc\nd") == ("a\nX\nc\nd", [1])            # 같은 문단 — 나중 것(mine), 겹친 자리
    assert wp.merge3(b, "a\nb\nm\nc\nd", "a\nb\nt\nc\nd") == ("a\nb\nt\nm\nc\nd", [])  # 같은 자리 새 문단 둘 — 둘 다
    assert wp.merge3(b, "a\nB\nc\nd", "a\nB\nc\nd") == ("a\nB\nc\nd", [])              # 같은 고침 — 한 번
    assert wp.merge3(b, "a\nc\nd", "a\nb\nc\nD") == ("a\nc\nD", [])                    # 지우기 + 다른 문단 고침
    assert wp.para_map(b, "a\nNEW\nb\nc\nd") == [0, 2, 3, 4]
    assert wp.map_delta(b, 0, 0, "x\n", 2) == 3 and wp.map_delta(b, 4, 0, "!", 2) == 2


def test_two_people_edit_different_paragraphs_concurrently(tmp_path, monkeypatch):
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    _seed(app, fid)
    # 문단 임대 — 김가람 둘째 문단, 박다온 넷째 문단: 둘 다 잡힌다(칸째였다면 둘째 사람은 기다려야 했다)
    a = kim.post(f"/hwp/{fid}/lease", json={"field": F, "client": "k1", "para": 1, "action": "acquire"}).json()
    b = park.post(f"/hwp/{fid}/lease", json={"field": F, "client": "p1", "para": 3, "action": "acquire"}).json()
    assert a["ok"] and b["ok"] and a["para"] == 1 and b["para"] == 3 and not a.get("whole")
    assert {(x["user"], x["para"]) for x in ce.para_locks(db, fid)} == {("kim", 1), ("park", 3)}
    assert ce.locks(db, fid) == {}                                          # 칸째 임대는 없다
    # 둘 다 판 1 을 보고 고친다 — 김가람이 먼저 닿고, 박다온은 옛 판(1)으로 닿아 합쳐진다
    mine_k = _edit(BASE, 1, "나) 둘째 항목 — 김가람 고침")
    mine_p = _edit(BASE, 3, "라) 넷째 항목 — 박다온 고침")
    rk = kim.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [_delta_op(1, 1, BASE, mine_k)]}).json()["results"][0]
    rp = park.post(f"/hwp/{fid}/ops", json={"client": "p1", "ops": [_delta_op(1, 1, BASE, mine_p)]}).json()["results"][0]
    want = _edit(mine_k, 3, "라) 넷째 항목 — 박다온 고침")
    assert rk["ok"] and rk["version"] == 2
    assert rp["ok"] and rp["version"] == 3 and rp["merged"] and rp["text"] == want and rp["clash"] == 0
    assert ce.state(db, fid)[F]["text"] == want
    assert ce.conflicts(db, fid) == []
    # 받는 화면(김가람)에는 지금 판(2)에서 바뀐 부분만 — 넣어 보면 합친 글
    ev = [e for e in ce.events_since(db, fid, 0, full=False) if e["kind"] == "field-updated"][-1]["data"]
    assert ev["merged"] and ev["base"] == 2 and mine_k[:ev["at"]] + ev["ins"] + mine_k[ev["at"] + ev["del"]:] == want
    # 같은 창이 다시 옛 판(2)으로 보내도 합치기를 탄다(합친 글을 아직 못 본 창이 남의 고침을 덮지 않게)
    more = _edit(mine_k, 1, "나) 둘째 항목 — 김가람 고침 더")
    r2 = kim.post(f"/hwp/{fid}/ops", json={"client": "k1", "ops": [{"seq": 2, "field": F, "base": 2, "text": more}]}).json()["results"][0]
    assert r2["ok"] and r2["merged"] and ce.state(db, fid)[F]["text"] == _edit(want, 1, "나) 둘째 항목 — 김가람 고침 더")


def test_insert_paragraph_above_keeps_other_edit_in_place(tmp_path, monkeypatch):
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    _seed(app, fid)
    assert park.post(f"/hwp/{fid}/lease", json={"field": F, "client": "p1", "para": 2, "action": "acquire"}).json()["ok"]
    # 김가람이 맨 위에 문단 둘을 넣는다 — 박다온의 문단 임대는 같은 문단(다)을 따라 2 → 4
    ins = "머리 문단 하나\n머리 문단 둘\n" + BASE
    assert ce.apply_ops(db, fid, "kim", "k1", [_delta_op(1, 1, BASE, ins)])[0]["ok"]
    assert [x["para"] for x in ce.para_locks(db, fid) if x["user"] == "park"] == [4]
    # 박다온은 판 1 을 보고 셋째 문단을 고쳤다 — 합치면 넣은 문단 아래 제자리(다)에
    mine = _edit(BASE, 2, "다) 셋째 항목 — 박다온")
    r = ce.apply_ops(db, fid, "park", "p1", [{"seq": 1, "field": F, "base": 1, "text": mine}])[0]
    assert r["ok"] and r["merged"]
    assert ce.state(db, fid)[F]["text"] == "머리 문단 하나\n머리 문단 둘\n" + mine
    # 남이 문단을 지워도 제자리 — 김가람이 첫 문단(가)을 지움, 박다온은 판 3 을 보고 넷째(라)를 고침
    cur = ce.state(db, fid)[F]["text"]
    gone = cur.replace("가) 첫째 항목\n", "")
    assert ce.apply_ops(db, fid, "kim", "k1", [_delta_op(2, 3, cur, gone)])[0]["ok"]
    mine2 = cur.replace("라) 넷째 항목", "라) 넷째 — 박다온")
    assert ce.apply_ops(db, fid, "park", "p1", [{"seq": 2, "field": F, "base": 3, "text": mine2}])[0]["merged"]
    assert ce.state(db, fid)[F]["text"] == gone.replace("라) 넷째 항목", "라) 넷째 — 박다온")


def test_same_paragraph_later_wins_and_conflict_is_kept(tmp_path, monkeypatch):
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    _seed(app, fid)
    # 같은 문단은 임대로 막힌다 — 쥔 사람에게 기다림 알림(문단 번호와 함께)
    assert kim.post(f"/hwp/{fid}/lease", json={"field": F, "client": "k1", "para": 1, "action": "acquire"}).json()["ok"]
    j = park.post(f"/hwp/{fid}/lease", json={"field": F, "client": "p1", "para": 1, "action": "acquire"}).json()
    assert j["ok"] is False and j["by"] == "kim" and j["by_name"] == "김가람" and j["para"] == 1
    w = [e["data"] for e in ce.events_since(db, fid, 0, full=False) if e["kind"] == "lock-wanted"][-1]
    assert w == {"field": F, "para": 1, "by": "park", "holder": "kim", "holder_client": "k1"}
    # 그래도 경합으로 둘 다 같은 문단을 고쳐 보냈다 — 나중에 온 박다온 것이 남고, 덮인 김가람 글은 충돌 사본(김가람 몫)
    k_txt, p_txt = _edit(BASE, 1, "나) 김가람 판"), _edit(BASE, 1, "나) 박다온 판")
    assert ce.apply_ops(db, fid, "kim", "k1", [{"seq": 1, "field": F, "base": 1, "text": k_txt}])[0]["ok"]
    r = ce.apply_ops(db, fid, "park", "p1", [{"seq": 1, "field": F, "base": 1, "text": p_txt}])[0]
    assert r["ok"] and r["merged"] and r["clash"] == 1 and ce.state(db, fid)[F]["text"] == p_txt
    c = ce.conflicts(db, fid, "kim")
    assert len(c) == 1 and c[0]["text"] == k_txt and c[0]["reason"] == "para"
    ev = [e for e in ce.events_since(db, fid, 0, full=False) if e["kind"] == "field-updated"][-1]["data"]
    assert ev["conflict"]["user"] == "kim" and ev["conflict"]["client"] == "k1" and ev["conflict"]["paras"] == [1]


def test_rich_field_and_aura_stay_whole_field(tmp_path, monkeypatch):
    # 표가 든 칸 — 문단 임대를 청해도 칸째로 잡히고(whole), 남은 그 칸을 통째로 기다린다
    data = wt.build(wt.GRANT["budget_change"])
    z, secs = ww._open(data)
    name = next(n for n, x in secs.items() if F in x)
    x = secs[name]
    m = next(m for m in ww._BEGIN.finditer(x) if ww._unq(m.group(2)) == F)
    sp = ww._span(x, m.group(1), m.end())
    data = ww._pack(z, {name: x[:sp[0]] + '<hp:tbl id="7" rowCnt="1" colCnt="1"/>' + x[sp[0]:]})
    assert F in ww.rich_fields(data)
    app, kim, park, fid = _doc(tmp_path, monkeypatch, data)
    db = app.state.db
    a = kim.post(f"/hwp/{fid}/lease", json={"field": F, "client": "k1", "para": 0, "action": "acquire"}).json()
    assert a["ok"] and a["whole"] and ce.locks(db, fid)[F]["user"] == "kim" and ce.para_locks(db, fid) == []
    b = park.post(f"/hwp/{fid}/lease", json={"field": F, "client": "p1", "para": 3, "action": "acquire"}).json()
    assert b["ok"] is False and b["by"] == "kim" and b["whole"]
    # 칸째 임대 중에는 남의 고침이 예전처럼 충돌(locked) — 합치지 않는다
    r = ce.apply_ops(db, fid, "park", "p1", [{"seq": 1, "field": F, "base": 0, "text": "x"}])[0]
    assert r["ok"] is False and r["reason"] == "locked"


def test_aura_whole_field_waits_for_paragraph_holder_and_setting_off(tmp_path, monkeypatch):
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    _seed(app, fid)
    assert park.post(f"/hwp/{fid}/lease", json={"field": F, "client": "p1", "para": 2, "action": "acquire"}).json()["ok"]
    # AURA 절 쓰기는 칸째 — 사람이 그 칸 문단을 쥐었으면 쓰지 않는다
    s = ce.SectionStream(db, fid, F)
    assert s.ok is False and s.held == "park"
    # 칸째 임대 청도 막힌다(누가 어느 문단인지와 함께)
    j = ce.acquire(db, fid, F, "kim", "k1")
    assert j["ok"] is False and j["by"] == "park" and j["para"] == 2
    # 창을 닫으면 문단 임대가 풀린다
    assert park.post(f"/hwp/{fid}/leave", json={"client": "p1"}).json()["ok"]
    assert ce.para_locks(db, fid) == [] and ce.SectionStream(db, fid, F).ok
    # 설정 coedit:para_lock=0 — 예전처럼 칸째(문단 청도 칸째로 잡힌다)
    db.set_setting("coedit:para_lock", "0")
    ce.release(db, fid, F, ce.AGENT)
    a = kim.post(f"/hwp/{fid}/lease", json={"field": F, "client": "k9", "para": 1, "action": "acquire"}).json()
    assert a["ok"] and a["whole"]


def test_editor_paragraph_merge_js():
    """편집 화면 쪽 같은 규칙(webhwp-collab.js paraHunks·mapPara·merge3)과 두 창 동시 편집 흐름 — node 시험(tests/coedit_para.test.cjs)."""
    import shutil
    import subprocess
    from pathlib import Path
    c = (Path(__file__).resolve().parents[1] / "src/zzaimy/app/static/webhwp-collab.js").read_text()
    assert "function merge3(" in c and "'para-lock'" in c and "acquirePara(f, curPara)" in c
    node = shutil.which("node")
    if node:
        r = subprocess.run([node, "--test", str(Path(__file__).resolve().parent / "coedit_para.test.cjs")], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr


def test_agent_value_write_skips_field_with_paragraph_holder(tmp_path, monkeypatch):
    """AURA 칸 값 쓰기(put, force)는 칸째 — 사람이 그 칸의 문단을 쥐고 있으면 쓰지 않고 쥔 사람을 돌려준다."""
    app, kim, park, fid = _doc(tmp_path, monkeypatch)
    db = app.state.db
    _seed(app, fid)
    assert park.post(f"/hwp/{fid}/lease", json={"field": F, "client": "p1", "para": 0, "action": "acquire"}).json()["ok"]
    done, held = ce.put(db, fid, ce.AGENT, {F: "AURA 글"})
    assert done == [] and held == {F: "park"} and ce.state(db, fid)[F]["text"] == BASE
