"""조직 관리 한 화면(/team, ADR-0073) — 트리 자료(여러 단계), 부서 지우기의 아래 부서 처리, 구성원 여러 명 바꾸기 권한,
전체 사용자 여러 명 바꾸기, 옛 화면 정리."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_staff_grades import _add, _client, _dev
from zzaimy.app import dept_dictionary, rag_spaces
from zzaimy.ingest import gdocs, gdrive

STATIC = Path(__file__).resolve().parents[1] / "src" / "zzaimy" / "app" / "static"
TEMPLATES = STATIC.parent / "templates"


@pytest.fixture(autouse=True)
def _no_google(monkeypatch):
    monkeypatch.setattr(gdrive, "list_accounts", lambda: [])
    monkeypatch.setattr(gdocs, "has_docs_scope", lambda email: True)
    monkeypatch.setattr(gdrive, "configured", lambda: True)


@pytest.fixture
def org(tmp_path):
    """기획처 › 기획팀 › 기획1파트 › 기획2파트(네 단계), 기획처 › 예산팀, 학생복지처 › 학생지원팀. 기획처 총괄 책임자 boss."""
    app, dev = _dev(tmp_path)
    cfg = rag_spaces.config(tmp_path)
    for d in ("기획처", "학생복지처", "기획팀", "예산팀", "기획1파트", "기획2파트", "학생지원팀"):
        cfg, err = rag_spaces.create_dept_rag(cfg, d)
        assert not err
    rag_spaces.save_config(cfg, tmp_path)
    for c, p in (("기획팀", "기획처"), ("예산팀", "기획처"), ("기획1파트", "기획팀"), ("기획2파트", "기획1파트"), ("학생지원팀", "학생복지처")):
        assert not dept_dictionary.set_parent(tmp_path, c, p)
    _add(app, "boss", dept="기획처", grade="head")
    _add(app, "kim", dept="기획팀", grade="member")
    _add(app, "park", dept="예산팀", grade="member")
    _add(app, "lee", dept="학생지원팀", grade="lead")
    return app, dev, tmp_path


def _nodes(c):
    r = c.get("/api/org")
    assert r.status_code == 200
    return {n["name"]: n for n in r.json()["nodes"]}


def test_tree_nodes_many_levels(org):
    app, dev, tmp = org
    n = _nodes(dev)
    assert n["기획2파트"]["parent"] == "기획1파트" and n["기획1파트"]["parent"] == "기획팀" and n["기획처"]["parent"] == ""
    assert all(x["team"] for x in n.values())
    # 단계 제한 없이 옮긴다 — 순환만 막는다
    assert dev.post("/org/move", json={"dept": "예산팀", "parent": "기획2파트"}).json()["ok"]
    assert dept_dictionary.parents_override(tmp)["예산팀"] == "기획2파트"
    r = dev.post("/org/move", json={"dept": "기획팀", "parent": "기획2파트"})
    assert r.status_code == 400 and "아래 부서" in r.json()["error"]
    boss = _nodes(_client(app, "boss"))
    assert boss["기획2파트"]["can_edit"] and not boss["기획처"]["can_edit"] and not boss["학생지원팀"]["team"]
    kim = _nodes(_client(app, "kim"))
    assert not any(x["team"] or x["can_edit"] for x in kim.values())


def test_delete_lifts_or_takes_children(org):
    app, dev, tmp = org
    r = dev.post("/org/delete", json={"dept": "기획1파트"})                 # 기본은 위로 올리기
    assert r.json()["ok"] and r.json()["lifted"] == ["기획2파트"]
    assert dept_dictionary.parents_override(tmp)["기획2파트"] == "기획팀"
    r = dev.post("/org/delete", json={"dept": "기획팀", "kids": "with"})    # 기획팀에 kim(소속)이 있어 막힌다
    assert r.status_code == 409 and "기획팀" in r.json()["error"]
    assert "기획2파트" in _nodes(dev)                                         # 하나도 지우지 않았다
    app.state.accounts["kim"]["dept"] = "예산팀"
    app.state.save_accounts()
    r = dev.post("/org/delete", json={"dept": "기획팀", "kids": "with"})
    assert r.json()["ok"] and set(r.json()["deleted"]) == {"기획팀", "기획2파트"}
    names = _nodes(dev)
    assert "기획팀" not in names and "기획2파트" not in names and "기획2파트" not in dept_dictionary.parents_override(tmp)
    r = dev.post("/org/delete", json={"dept": "학생복지처"})
    assert r.json()["ok"] and r.json()["deleted"] == ["학생복지처"]
    assert dept_dictionary.parents_override(tmp)["학생지원팀"] == ""          # 맨 위 부서의 아래는 맨 위로 올라간다


def test_members_ops_follow_grade_rules(org):
    app, dev, tmp = org
    boss = _client(app, "boss")
    m = boss.get("/api/org/members", params={"dept": "기획팀"}).json()
    assert m["can_place"] and not m["can_primary"] and [r["uid"] for r in m["rows"]] == ["kim"]
    r = boss.post("/api/org/members", json={"dept": "기획팀", "op": "grade", "uids": ["kim"], "value": "lead"})
    assert r.json()["ok"] and app.state.accounts["kim"]["grade"] == "lead"
    r = boss.post("/api/org/members", json={"dept": "기획팀", "op": "move", "uids": ["kim"], "value": "학생지원팀"})
    assert r.status_code == 403                                               # 맡지 않은 부서로는 못 옮긴다
    r = boss.post("/api/org/members", json={"dept": "기획팀", "op": "move", "uids": ["kim"], "value": "예산팀"})
    assert r.json()["ok"] and app.state.accounts["kim"]["dept"] == "예산팀"
    r = boss.post("/api/org/members", json={"dept": "기획팀", "op": "add", "uids": ["lee"], "value": "extra"})
    assert r.json()["ok"] and app.state.accounts["lee"]["dept_grades"].get("기획팀") == "member"    # 겸직(일반)
    r = boss.post("/api/org/members", json={"dept": "기획팀", "op": "add", "uids": ["lee"], "value": "primary"})
    assert r.status_code == 403                                               # 소속째 옮겨 오기는 관리자만
    r = boss.post("/api/org/members", json={"dept": "기획팀", "op": "remove", "uids": ["lee"]})
    assert r.json()["ok"] and "기획팀" not in (app.state.accounts["lee"].get("dept_grades") or {})
    r = boss.post("/api/org/members", json={"dept": "예산팀", "op": "remove", "uids": ["park"]})
    assert r.status_code == 400 and "관리자" in r.json()["msg"]               # 소속 비우기는 관리자만(걸린 사람 까닭)
    r = dev.post("/api/org/members", json={"dept": "예산팀", "op": "remove", "uids": ["park"]})
    assert r.json()["ok"] and app.state.accounts["park"]["dept"] == ""
    lee = _client(app, "lee")
    assert lee.get("/api/org/members", params={"dept": "예산팀"}).status_code == 403
    r = lee.post("/api/org/members", json={"dept": "학생지원팀", "op": "move", "uids": ["lee"], "value": "학생복지처"})
    assert r.status_code == 403                                               # 사업 책임자는 옮기지 못한다


def test_accounts_bulk_and_page(org):
    app, dev, _ = org
    assert dev.get("/api/org/accounts").json()["users"]
    assert _client(app, "boss").get("/api/org/accounts").status_code == 403
    r = dev.post("/team/accounts/bulk", data={"op": "dept", "value": "학생복지처", "uids": ["kim", "park"]})
    assert r.json()["ok"] and {app.state.accounts[u]["dept"] for u in ("kim", "park")} == {"학생복지처"}
    r = dev.post("/team/accounts/bulk", data={"op": "suspend", "uids": ["kim", "zzdev"]})
    j = r.json()
    assert j["done"] == ["kim"] and j["failed"][0]["uid"] == "zzdev" and app.state.accounts["kim"]["active"] is False
    assert dev.post("/team/accounts/bulk", data={"op": "activate", "uids": ["kim"]}).json()["ok"]
    assert dev.post("/team/accounts/bulk", data={"op": "role", "value": "student", "uids": ["kim"]}).status_code == 400
    page = dev.get("/team").text
    assert 'id="orgAdmin"' in page and 'data-accounts="1"' in page and 'id="userEdit"' in page and "data-org-add" in page
    boss_page = _client(app, "boss").get("/team").text
    assert 'data-accounts=""' in boss_page and 'id="userEdit"' not in boss_page
    assert _client(app, "kim").get("/team?tab=org").status_code == 200       # 옛 주소도 같은 화면
    assert dev.get("/dev/users", follow_redirects=False).status_code == 404      # 구글 연결도 조직 관리로(10-27), 옛 주소는 지웠다
    assert 'id="google-links"' in page and 'id="google-links"' not in boss_page      # 학교 계정 연결 현황은 관리자에게만


def test_screen_code_uses_common_parts():
    src = (STATIC / "org-chart.js").read_text(encoding="utf-8")
    assert "innerHTML" not in src and "insertAdjacentHTML" not in src and "location.reload" not in src
    for path in ("/api/org", "/api/org/members", "/api/org/accounts", "/org/move", "/org/rename", "/org/delete", "/org/create", "/org/head",
                 "/team/accounts/bulk", "/dev/rag/min-grade"):
        assert path in src
    assert "platformConfirm" in src and "bulk-bar" in src and "row-menu" in src and "confirm-dialog" in src
    assert "resize" not in (TEMPLATES / "team.html").read_text(encoding="utf-8")
    nav = (STATIC / "workspace-navigation.js").read_text(encoding="utf-8")
    assert "choices" in nav and "(danger ? cancel : accept).focus()" in nav


def test_reconcile_cleans_traces_of_hand_removed_dept(org):
    """설정 파일에서 부서를 손으로 지워도(부서 RAG 줄만 빠짐) 흔적 맞추기가 나머지를 모두 정리한다(10-27 「어디는 되어 있고 어디는 안 된 건 안 됨」)."""
    import json

    from zzaimy.app import org_routes
    app, dev, tmp = org
    st, db = app.state, app.state.db
    cfg = rag_spaces.config(tmp)
    cfg["dept_grants"] = {"기획처": ["예산팀"], "예산팀": ["기획처"]}
    cfg["dept_rags"] = [r for r in cfg["dept_rags"] if r.get("dept") != "예산팀"]       # 손으로 지운 것처럼
    rag_spaces.save_config(cfg, tmp)
    st.accounts["park"]["dept"] = "기획처"
    db.set_setting("google_root:예산팀", "folder-x")
    db.set_setting("google_root_name:학생복지처", "")                                  # 있는 부서의 빈 열쇠는 그대로
    db.set_setting("project_shares:1", json.dumps({"mode": "shared", "items": [{"to": "dept:예산팀", "level": "view"}, {"to": "dept:기획처", "level": "edit"}]}))
    (tmp / "documents" / "기안기" / "예산팀").mkdir(parents=True, exist_ok=True)
    got = org_routes.reconcile(st)
    assert "예산팀" in got["orphans"]
    assert rag_spaces.config(tmp)["dept_grants"] == {}                                # 주는 쪽·받는 쪽 모두
    assert not db.get_setting("google_root:예산팀", "")
    assert [i["to"] for i in json.loads(db.get_setting("project_shares:1", "{}"))["items"]] == ["dept:기획처"]
    assert "예산팀" not in dept_dictionary.parents_override(tmp)
    assert not (tmp / "documents" / "기안기" / "예산팀").exists()                        # 빈 폴더는 지운다
    assert org_routes.reconcile(st)["orphans"] == []                                   # 한 번 더 돌려도 그대로
    # 보안: 설정 파일이 깨지면 아무것도 하지 않는다(모두 「없는 부서」 로 보여 권한을 지우지 않게)
    (tmp / "rag_spaces.json").write_text("{깨짐", encoding="utf-8")
    assert org_routes.reconcile(st).get("skipped")


def test_rag_lend_is_dept_based_and_only_admin_or_owner_head(org):
    """부서 RAG 다른 부서에 열기(/org/rag-lend) — 관리자와 그 부서를 맡은 총괄 책임자만, 받는 쪽은 늘 부서."""
    app, dev, tmp = org
    _add(app, "stu1", role="student", dept="학생지원팀")
    boss, kim, lee = _client(app, "boss"), _client(app, "kim"), _client(app, "lee")
    # 받기 전에는 학생지원팀 사람이 예산팀 RAG 를 찾지 못한다
    assert "예산팀" not in rag_spaces.search_scope("staff", "학생지원팀", "lee", tmp)["grant_depts"]
    # 권한 없는 사람: 그 부서 일반·다른 처·단 사업 책임자 — 거절, 설정 그대로
    assert kim.post("/org/rag-lend", json={"dept": "예산팀", "to": ["학생지원팀"]}).status_code == 403
    assert lee.post("/org/rag-lend", json={"dept": "예산팀", "to": ["학생지원팀"]}).status_code == 403
    assert lee.get("/org/rag-lend/targets/예산팀").status_code == 403
    assert rag_spaces.config(tmp)["dept_grants"] == {}
    # 기획처 총괄 책임자는 맡은 하위 부서(예산팀)를 연다 — 남의 처·단(학생복지처)은 못 연다
    assert boss.post("/org/rag-lend", json={"dept": "학생복지처", "to": ["기획처"]}).status_code == 403
    names = [p["user"] for p in boss.get("/org/rag-lend/targets/예산팀", params={"q": "학생"}).json()["people"]]
    assert "학생지원팀" in names and "예산팀" not in names
    r = boss.post("/org/rag-lend", json={"dept": "예산팀", "to": ["학생지원팀"]}).json()
    assert r["ok"] and r["to"] == ["학생지원팀"] and rag_spaces.config(tmp)["dept_grants"] == {"예산팀": ["학생지원팀"]}
    assert "예산팀" in rag_spaces.search_scope("staff", "학생지원팀", "lee", tmp)["grant_depts"]
    assert not rag_spaces.search_scope("student", "학생지원팀", "stu1", tmp)["grant"]       # 학생은 받지 않는다
    assert _client(app, "stu1").post("/org/rag-lend", json={"dept": "학생지원팀", "to": ["기획처"]}).status_code == 403
    # 트리 자료 — 열 수 있는 사람에게만 받는 부서를 보인다
    assert _nodes(boss)["예산팀"]["lend_to"] == ["학생지원팀"] and "lend_to" not in _nodes(kim)["예산팀"]
    # 모르는 부서·자기 자신은 거절
    assert dev.post("/org/rag-lend", json={"dept": "예산팀", "to": ["없는부서"]}).status_code == 400
    assert dev.post("/org/rag-lend", json={"dept": "예산팀", "to": ["예산팀"]}).status_code == 400
    # 관리자가 거두면 바로 빠진다
    assert dev.post("/org/rag-lend", json={"dept": "예산팀", "to": []}).json()["to"] == []
    assert rag_spaces.config(tmp)["dept_grants"] == {}
    assert "예산팀" not in rag_spaces.search_scope("staff", "학생지원팀", "lee", tmp)["grant_depts"]
