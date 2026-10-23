"""조직도(/team?tab=org, org_routes) — 권한별 고치기, 옮기기·이름 바꾸기 연쇄, 지우기 막힘, 총괄 책임자 지정, 순환·세 단계 막기."""

from __future__ import annotations

import json

import pytest

from tests.test_staff_grades import _add, _client, _dev
from zzaimy.app import dept_dictionary, grades, rag_spaces
from zzaimy.ingest import gdocs, gdrive


@pytest.fixture(autouse=True)
def _no_google(monkeypatch):
    monkeypatch.setattr(gdrive, "list_accounts", lambda: [])
    monkeypatch.setattr(gdocs, "has_docs_scope", lambda email: True)
    monkeypatch.setattr(gdrive, "configured", lambda: True)


@pytest.fixture
def org(tmp_path):
    """처·단 둘(기획처·학생복지처)과 팀 셋, 상위 없는 센터 하나. 기획처 총괄 책임자 boss, 일반 kim."""
    app, dev = _dev(tmp_path)
    cfg = rag_spaces.config(tmp_path)
    for d in ("기획처", "학생복지처", "기획팀", "예산팀", "학생지원팀", "천마빌리지센터"):
        cfg, err = rag_spaces.create_dept_rag(cfg, d)
        assert not err
    rag_spaces.save_config(cfg, tmp_path)
    for c, p in (("기획팀", "기획처"), ("예산팀", "기획처"), ("학생지원팀", "학생복지처")):
        assert not dept_dictionary.set_parent(tmp_path, c, p)
    _add(app, "boss", dept="기획처", grade="head")
    _add(app, "kim", dept="기획팀", grade="member")
    _add(app, "lee", dept="학생지원팀", grade="lead")
    return app, dev, tmp_path


def _tree(c):
    r = c.get("/api/org")
    assert r.status_code == 200
    return r.json()


def _names(t):
    return {x["name"]: [c["name"] for c in x["children"]] for x in t["tops"]}


def test_tree_shape_and_view_rights(org):
    app, dev, _ = org
    t = _tree(dev)
    assert _names(t) == {"기획처": ["기획팀", "예산팀"], "학생복지처": ["학생지원팀"]}
    assert [x["name"] for x in t["loose"]] == ["천마빌리지센터"]          # 팀·센터 급인데 위가 없는 것
    top = {x["name"]: x for x in t["tops"]}
    assert [h["user"] for h in top["기획처"]["heads"]] == ["boss"] and top["기획처"]["n"] == 1
    assert t["admin"] and all(x["can_edit"] for x in t["tops"])
    kim = _client(app, "kim")
    tk = _tree(kim)
    assert not tk["can_any"] and not any(x["can_edit"] or x["can_hold"] for x in tk["tops"])
    assert kim.get("/team?tab=org").status_code == 200 and kim.get("/team").status_code == 403   # 일반은 조직도 보기만
    boss = _client(app, "boss")
    tb = {x["name"]: x for x in _tree(boss)["tops"]}
    assert not tb["기획처"]["can_edit"] and tb["기획처"]["can_hold"]               # 자기 처·단 자체는 못 고치고 아래만
    assert all(c["can_edit"] for c in tb["기획처"]["children"]) and not tb["학생복지처"]["can_hold"]
    page = boss.get("/team?tab=org").text
    assert "조직도" in page and 'href="/team"' in page and "org-chart.js" in page


def test_permissions_by_grade(org):
    app, dev, tmp = org
    boss, kim, lee = _client(app, "boss"), _client(app, "kim"), _client(app, "lee")
    # 일반·사업 책임자: 무엇이든 403
    for c in (kim, lee):
        assert c.post("/org/move", json={"dept": "기획팀", "parent": "학생복지처"}).status_code == 403
        assert c.post("/org/rename", json={"dept": "기획팀", "name": "기획조정팀"}).status_code == 403
        assert c.post("/org/delete", json={"dept": "천마빌리지센터"}).status_code == 403
        assert c.post("/org/create", json={"name": "새팀", "parent": "기획처"}).status_code == 403
        assert c.post("/org/head", json={"dept": "기획처", "user": "kim"}).status_code == 403
    # 총괄 책임자: 남의 처·단 아래는 403, 자기 처·단 자체도 403
    assert boss.post("/org/rename", json={"dept": "학생지원팀", "name": "학생팀"}).status_code == 403
    assert boss.post("/org/move", json={"dept": "학생지원팀", "parent": "기획처"}).status_code == 403
    assert boss.post("/org/move", json={"dept": "예산팀", "parent": "학생복지처"}).status_code == 403   # 남의 처·단으로 내보내기
    assert boss.post("/org/move", json={"dept": "예산팀", "parent": ""}).status_code == 403             # 맨 위로는 관리자만
    assert boss.post("/org/rename", json={"dept": "기획처", "name": "기획조정처"}).status_code == 403
    assert boss.post("/org/create", json={"name": "새팀", "parent": "학생복지처"}).status_code == 403
    assert boss.post("/org/head", json={"dept": "기획처", "user": "kim"}).status_code == 403
    # 총괄 책임자: 자기 아래는 고친다
    r = boss.post("/org/create", json={"name": "평가팀", "parent": "기획처"})
    assert r.status_code == 200 and dept_dictionary.parents_override(tmp)["평가팀"] == "기획처"
    assert boss.post("/org/rename", json={"dept": "평가팀", "name": "성과평가팀"}).json()["ok"]
    assert boss.post("/org/delete", json={"dept": "성과평가팀"}).json()["ok"]
    assert "성과평가팀" not in dept_dictionary.parents_override(tmp)


def test_move_two_levels_cycle_and_undo(org):
    app, dev, tmp = org
    r = dev.post("/org/move", json={"dept": "천마빌리지센터", "parent": "학생복지처"}).json()
    assert r["ok"] and r["undo"] == {"dept": "천마빌리지센터", "parent": "", "unset": True, "expect": {"parent": "학생복지처", "unset": False}}
    assert _names(_tree(dev))["학생복지처"] == ["천마빌리지센터", "학생지원팀"]
    assert dev.post("/org/move", json={**r["undo"]}).json()["ok"]                       # 되돌리기 — 정하기 전으로
    assert "천마빌리지센터" not in dept_dictionary.parents_override(tmp)
    # 팀 아래로는 못 놓는다(두 단계), 아래가 있는 처·단은 다른 처·단 아래로 못 간다, 순환도 막힌다
    assert "처·단이 아니라서" in dev.post("/org/move", json={"dept": "예산팀", "parent": "기획팀"}).json()["error"]
    assert "아래에 부서가 있어" in dev.post("/org/move", json={"dept": "기획처", "parent": "학생복지처"}).json()["error"]
    assert dept_dictionary.set_parent(tmp, "기획처", "기획팀")                         # 순환은 위계 함수가 거절
    # 맨 위로 올리면 팀도 처·단 카드가 된다
    assert dev.post("/org/move", json={"dept": "예산팀", "parent": ""}).json()["ok"]
    t = _tree(dev)
    assert "예산팀" in _names(t) and _names(t)["기획처"] == ["기획팀"]


def test_move_follows_head_scope(org):
    app, dev, tmp = org
    dev.post("/org/move", json={"dept": "학생지원팀", "parent": "기획처"})
    assert "학생지원팀" in grades.covered_depts("boss", app.state.accounts["boss"], tmp)   # 옮기면 맡는 범위도 따라온다


def test_rename_cascades(org):
    app, dev, tmp = org
    app.state.accounts["lee"]["dept_grades"] = {"기획팀": "lead"}
    app.state.save_accounts()
    cfg = rag_spaces.config(tmp)
    cfg["grants"] = {"lee": ["dept:기획팀"]}
    cfg["min_grade"] = {"dept:기획팀": "lead"}
    cfg["dept_of_area"] = {"plan": "기획팀"}
    rag_spaces.save_config(cfg, tmp)
    db = app.state.db
    with db._conn() as c:
        c.execute("INSERT INTO documents (filename, stored_path, created_at, doc_type, status, dept, access_level)"
                  " VALUES ('a.pdf', 'x/a.pdf', '2026-10-23', 'grant', 'done', '기획팀', 'dept')")
        c.execute("INSERT INTO library_folders (name, dept) VALUES ('기획 자료', '기획팀')")
    db.set_setting("google_account_dept:기획팀", "plan@ync.ac.kr")
    r = dev.post("/org/rename", json={"dept": "기획팀", "name": "기획조정팀"}).json()
    assert r["ok"] and r["undo"] == {"dept": "기획조정팀", "name": "기획팀"}
    acc = json.loads((tmp / "accounts.json").read_text())
    assert acc["kim"]["dept"] == "기획조정팀" and acc["lee"]["dept_grades"] == {"기획조정팀": "lead"}
    cfg = rag_spaces.config(tmp)
    assert {"dept": "기획조정팀"}.items() <= next(x for x in cfg["dept_rags"] if x["dept"] == "기획조정팀").items()
    assert not any(x["dept"] == "기획팀" for x in cfg["dept_rags"])
    assert cfg["grants"]["lee"] == ["dept:기획조정팀"] and cfg["min_grade"] == {"dept:기획조정팀": "lead"}
    assert cfg["dept_of_area"] == {"plan": "기획조정팀"}
    assert dept_dictionary.parents_override(tmp)["기획조정팀"] == "기획처" and "기획팀" not in dept_dictionary.parents_override(tmp)
    with db._conn() as c:
        assert c.execute("SELECT dept, access_level FROM documents WHERE filename = 'a.pdf'").fetchone()[:] == ("기획조정팀", "dept")
    with db._conn() as c:
        assert c.execute("SELECT dept FROM library_folders WHERE name = '기획 자료'").fetchone()[0] == "기획조정팀"
    assert db.get_setting("google_account_dept:기획조정팀", "") == "plan@ync.ac.kr" and not db.get_setting("google_account_dept:기획팀", "")
    assert list((tmp / "history").glob("accounts.*.json"))                               # 바꾸기 전 계정 판
    # 이미 있는 이름·빈 이름은 거절
    assert dev.post("/org/rename", json={"dept": "예산팀", "name": "기획처"}).status_code == 400
    assert dev.post("/org/rename", json={"dept": "예산팀", "name": " "}).status_code == 400


def test_delete_blocked_with_members_and_cleans_up(org):
    app, dev, tmp = org
    r = dev.post("/org/delete", json={"dept": "기획팀"})
    assert r.status_code == 409 and "먼저 사람을" in r.json()["error"]
    assert "기획팀" in rag_spaces.known_depts(rag_spaces.config(tmp), app.state.accounts)
    app.state.accounts["lee"]["dept_grades"] = {"예산팀": "head"}
    app.state.save_accounts()
    assert dev.post("/org/delete", json={"dept": "예산팀"}).json()["ok"]
    assert "dept_grades" not in app.state.accounts["lee"] and "예산팀" not in dept_dictionary.parents_override(tmp)


def test_head_designation(org):
    app, dev, tmp = org
    # 소속이 다른 사람 — 겸직 등급과 겸직 부서(추가 권한)
    assert dev.post("/org/head", json={"dept": "학생복지처", "user": "kim"}).json()["ok"]
    kim = app.state.accounts["kim"]
    assert kim["dept_grades"] == {"학생복지처": "head"} and kim["grade"] == "member"
    assert "dept:학생복지처" in rag_spaces.config(tmp)["grants"]["kim"]
    assert "학생지원팀" in grades.covered_depts("kim", kim, tmp)
    assert [h["user"] for h in {x["name"]: x for x in _tree(dev)["tops"]}["학생복지처"]["heads"]] == ["kim"]
    assert dev.post("/org/head", json={"dept": "학생복지처", "user": "kim", "on": False}).json()["ok"]
    assert "dept_grades" not in app.state.accounts["kim"] and "kim" not in rag_spaces.config(tmp)["grants"]
    # 소속인 사람 — 계정 등급
    _add(app, "park", dept="학생복지처", grade="lead")
    assert dev.post("/org/head", json={"dept": "학생복지처", "user": "park"}).json()["ok"]
    assert app.state.accounts["park"]["grade"] == "head"
    dev.post("/org/head", json={"dept": "학생복지처", "user": "park", "on": False})
    assert app.state.accounts["park"]["grade"] == grades.DEFAULT_GRADE
    assert dev.post("/org/head", json={"dept": "학생복지처", "user": "nobody"}).status_code == 400


def test_create_at_top_and_suggestions(org, monkeypatch):
    app, dev, tmp = org
    assert dev.post("/org/create", json={"name": "AI-X추진단", "parent": ""}).json()["ok"]
    assert dev.post("/org/create", json={"name": "홍보팀", "parent": ""}).json()["ok"]
    t = _tree(dev)
    assert {"AI-X추진단", "홍보팀"} <= set(_names(t))                                     # 맨 위에 둔 팀도 처·단 카드
    assert dev.post("/org/create", json={"name": "기획팀", "parent": "기획처"}).status_code == 400   # 이미 있음
    monkeypatch.setattr(dept_dictionary, "suggested_parents", lambda data_dir=None: {"천마빌리지센터": "학생복지처", "기획팀": "기획처"})
    s = _tree(dev)["suggestions"]
    assert s == [{"dept": "천마빌리지센터", "parent": "학생복지처", "can": True}]            # 지금과 다른 곳만


# ---------------------------------------------------------------- 보안 점검(권한 넓히기·정보 노출·옛 권한 되살아남)

def test_head_cannot_pull_outside_depts_in(org):
    app, dev, tmp = org
    boss = _client(app, "boss")
    assert boss.post("/org/move", json={"dept": "천마빌리지센터", "parent": "기획처"}).status_code == 403   # 상위 없는 부서 끌어오기
    assert boss.post("/org/move", json={"dept": "학생지원팀", "parent": "기획처"}).status_code == 403      # 남의 처·단 부서 끌어오기
    assert boss.post("/org/move", json={"dept": "천마빌리지센터", "unset": True}).status_code == 403
    assert boss.post("/org/move", json={"dept": "예산팀", "unset": True}).status_code == 403              # 정하기 전으로는 관리자만
    assert "천마빌리지센터" not in grades.covered_depts("boss", app.state.accounts["boss"], tmp)
    assert boss.post("/org/create", json={"name": "새팀", "parent": ""}).status_code == 403


def test_api_hides_ids_from_viewers(org):
    app, dev, tmp = org
    app.state.accounts["kim"]["dept_grades"] = {"학생복지처": "head"}
    app.state.save_accounts()
    lee = _client(app, "lee")                                                    # 사업 책임자 — 보기만
    raw = lee.get("/api/org").text
    for secret in ('"boss"', '"kim"', '"user"', "n_extra", '"extra"', '"docs"', '"me"', '"roots"'):
        assert secret not in raw, secret
    t = lee.get("/api/org").json()
    top = {x["name"]: x for x in t["tops"]}
    assert top["기획처"]["heads"] == [{"name": "BOSS"}] and top["기획처"]["n"] == 1
    # 관리자와 그 처·단을 맡은 총괄 책임자는 자세히
    assert {x["name"]: x for x in _tree(dev)["tops"]}["기획처"]["heads"][0]["user"] == "boss"
    tb = {x["name"]: x for x in _tree(_client(app, "boss"))["tops"]}
    assert tb["기획처"]["heads"][0]["user"] == "boss" and "docs" in tb["기획처"]
    assert "user" not in tb["학생복지처"]["heads"][0] and "docs" not in tb["학생복지처"]          # 남의 처·단은 이름만


def _leave_traces(app, tmp, name):
    """지운 부서가 남긴 것처럼 흔적을 심는다 — 겸직 등급·최소 등급·위계·사업 관리 지정."""
    from zzaimy.app import program_admin
    app.state.accounts["kim"]["dept_grades"] = {name: "head"}
    app.state.save_accounts()
    cfg = rag_spaces.config(tmp)
    cfg["min_grade"] = {f"dept:{name}": "member"}         # 추가 권한(grants)이 남으면 부서 목록에 그대로 보여 「이미 있는 부서」로 막힌다
    rag_spaces.save_config(cfg, tmp)
    over = dept_dictionary.parents_override(tmp)
    over["예산팀"] = name                                                        # 남의 부서를 옛 이름 아래로 가리키는 관계
    (tmp / dept_dictionary.PARENTS_FILE).write_text(json.dumps(over, ensure_ascii=False), encoding="utf-8")
    store = program_admin.load_store(tmp)
    store["added"]["x1"] = {"name": "가상 사업", "heads": [f"dept:{name}"]}
    program_admin.save_store(store, tmp)


def test_rename_refuses_name_with_old_traces(org):
    app, dev, tmp = org
    _leave_traces(app, tmp, "옛사업단")
    boss = _client(app, "boss")
    r = boss.post("/org/rename", json={"dept": "기획팀", "name": "옛사업단"})
    assert r.status_code == 409 and "권한 기록" in r.json()["error"]
    assert dev.post("/org/rename", json={"dept": "기획팀", "name": "옛사업단"}).status_code == 409   # 관리자도 이름 바꾸기로는 못 차지
    assert app.state.accounts["kim"]["dept"] == "기획팀"
    assert dev.post("/org/rename", json={"dept": "기획팀", "name": "기획처"}).status_code == 400     # 지금 있는 부서 이름


def test_create_purges_old_traces_first(org):
    app, dev, tmp = org
    from zzaimy.app import program_admin
    _leave_traces(app, tmp, "옛사업단")
    r = dev.post("/org/create", json={"name": "옛사업단", "parent": ""}).json()
    assert r["ok"] and r["cleaned"]
    assert "dept_grades" not in app.state.accounts["kim"]
    cfg = rag_spaces.config(tmp)
    assert "dept:옛사업단" not in cfg.get("min_grade", {})
    assert dept_dictionary.parents_override(tmp).get("예산팀") != "옛사업단"
    assert "dept:옛사업단" not in program_admin.load_store(tmp)["added"]["x1"]["heads"]
    assert "옛사업단" not in grades.covered_depts("kim", app.state.accounts["kim"], tmp)


def test_delete_clears_all_traces(org):
    app, dev, tmp = org
    from zzaimy.app import program_admin, user_admin
    cfg = rag_spaces.config(tmp)
    cfg["min_grade"] = {"dept:천마빌리지센터": "lead"}
    rag_spaces.save_config(cfg, tmp)
    store = program_admin.load_store(tmp)
    store["added"]["x2"] = {"name": "가상", "heads": ["dept:천마빌리지센터"]}
    program_admin.save_store(store, tmp)
    assert dev.post("/org/delete", json={"dept": "천마빌리지센터"}).json()["ok"]
    from zzaimy.app import org_routes
    assert org_routes.traces(app.state, "천마빌리지센터") == []


def test_undo_refused_after_later_change(org):
    app, dev, tmp = org
    r = dev.post("/org/move", json={"dept": "예산팀", "parent": "학생복지처"}).json()
    dev.post("/org/move", json={"dept": "예산팀", "parent": "기획처"})                  # 그 사이 다시 옮김
    assert dev.post("/org/move", json=r["undo"]).status_code == 409
    r = dev.post("/org/move", json={"dept": "예산팀", "parent": "학생복지처"}).json()
    dev.post("/org/rename", json={"dept": "예산팀", "name": "예산관리팀"})             # 그 사이 이름 바뀜
    assert dev.post("/org/move", json=r["undo"]).status_code == 400
    # 되돌리기도 지금 권한으로 다시 본다 — 총괄 책임자가 받은 되돌리기를 그 사이 권한이 빠진 뒤 쓰면 403
    boss = _client(app, "boss")
    dev.post("/org/move", json={"dept": "예산관리팀", "parent": "기획처"})
    dev.post("/org/move", json={"dept": "기획팀", "parent": "학생복지처"})
    r = boss.post("/org/move", json={"dept": "예산관리팀", "parent": "기획처"})
    app.state.accounts["boss"]["grade"] = "lead"
    app.state.save_accounts()
    u = dev.post("/org/move", json={"dept": "학생지원팀", "parent": "기획처"}).json()["undo"]
    assert boss.post("/org/move", json=u).status_code == 403
