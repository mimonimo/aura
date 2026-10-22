"""교직원 등급(ADR-0066) — 계정 화면의 등급 저장, 관리자 2명 상한, RAG 최소 등급, 사업 관리 권한, 기본 설정 무변화."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.app import access_guard, access_policy, grades, program_admin, rag_spaces
from zzaimy.ingest import gdocs, gdrive


@pytest.fixture(autouse=True)
def _no_google(monkeypatch):
    monkeypatch.setattr(gdrive, "list_accounts", lambda: [])
    monkeypatch.setattr(gdocs, "has_docs_scope", lambda email: True)
    monkeypatch.setattr(gdrive, "configured", lambda: True)


def _add(app, uid, role="staff", dept="", grade=None, pw="pass-word-1"):
    a = app.state.new_account(uid, pw, role, uid.upper())
    a["dept"] = dept
    if grade:
        a["grade"] = grade
    app.state.accounts[uid] = a
    app.state.save_accounts()


def _client(app, uid, pw="pass-word-1"):
    c = TestClient(app)
    assert _login(c, uid, pw)
    return c


def _dev(tmp_path):
    app = _app(tmp_path)
    return app, _client(app, "zzdev", "devpass")


# ---------------------------------------------------------------- grades 기본

def test_grade_defaults_and_ranks():
    assert grades.grade_of({"role": "staff"}) == "lead"                 # 기존 계정은 사업 담당자
    assert grades.grade_of({"role": "staff", "grade": "member"}) == "member"
    assert grades.grade_of({"role": "professor", "grade": "head"}) == "head"
    assert grades.grade_of({"role": "dev"}) == "admin" and grades.grade_of({"role": "student"}) == ""
    assert not grades.can_create_project({"role": "staff", "grade": "member"})
    assert grades.can_create_project({"role": "staff"})
    assert grades.user_grade({"role": "dev"}, {"role": "staff", "grade": "member"}) == "member"
    assert grades.user_grade({"role": "dev"}, {"role": "student"}) == ""


def test_view_activity_follows_program_designation():
    proj = {"owner": "x", "program": "program:abc|2026"}
    lead = {"role": "staff", "grade": "lead", "dept": "기획처"}
    head = {"role": "staff", "grade": "head", "dept": "교무처"}
    member = {"role": "staff", "grade": "member", "dept": "기획처"}
    roles = {"heads": ["h1"], "leads": ["l1"]}
    assert grades.can_view_activity(proj, user="l1", account=lead, program_roles=roles)
    assert not grades.can_view_activity(proj, user="l2", account=lead, program_roles=roles)
    assert grades.can_view_activity(proj, user="h1", account=head, program_roles=roles)
    assert not grades.can_view_activity(proj, user="l1", account=member, program_roles=roles)
    assert not grades.can_view_activity(proj, user="h1", account=lead, program_roles=roles)   # 총괄 책임자 지정은 총괄 책임자 등급만
    # 예전 호출 모양 그대로
    assert grades.can_view_activity(proj, user="x", account=lead)
    assert grades.can_view_activity(proj, user="h9", account=head, project_dept="교무처")


# ---------------------------------------------------------------- 계정 화면

def test_grade_saved_only_for_staff_and_shown(tmp_path):
    app, c = _dev(tmp_path)
    page = c.get("/dev/users").text
    assert 'name="grade"' in page and "data-grade-field" in page
    r = c.post("/dev/users/save", data={"uid": "kimhead", "is_new": "1", "role": "staff", "grade": "head",
                                        "new_pw": "init-pass-1"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    data = json.loads((tmp_path / "accounts.json").read_text())
    assert data["kimhead"]["grade"] == "head"
    assert "교직원 · 총괄 책임자" in c.get("/dev/users").text
    # 등급을 보내지 않으면 지금 값 유지
    c.post("/dev/users/save", data={"uid": "kimhead", "role": "staff", "active": "1"})
    assert json.loads((tmp_path / "accounts.json").read_text())["kimhead"]["grade"] == "head"
    r = c.post("/dev/users/save", data={"uid": "kimhead", "role": "staff", "grade": "boss", "active": "1"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    # 새 계정 기본은 사업 담당자
    c.post("/dev/users/save", data={"uid": "newbie", "is_new": "1", "role": "staff", "new_pw": "init-pass-1"})
    assert json.loads((tmp_path / "accounts.json").read_text())["newbie"]["grade"] == "lead"


def test_admin_limit_two(tmp_path):
    app, c = _dev(tmp_path)
    r = c.post("/dev/users/save", data={"uid": "sysadm", "is_new": "1", "role": "dev", "new_pw": "init-pass-1"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    data = json.loads((tmp_path / "accounts.json").read_text())
    assert data["sysadm"]["role"] == "dev" and "grade" not in data["sysadm"]
    r = c.post("/dev/users/save", data={"uid": "third", "is_new": "1", "role": "dev", "new_pw": "init-pass-1"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and "third" not in json.loads((tmp_path / "accounts.json").read_text())
    r = c.post("/dev/users/save", data={"uid": "zzaimy", "role": "dev", "active": "1"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    # 이미 관리자인 계정은 그대로 저장된다
    r = c.post("/dev/users/save", data={"uid": "sysadm", "role": "dev", "active": "1", "name": "관리"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]


def test_view_as_can_pick_grade(tmp_path):
    app, c = _dev(tmp_path)
    r = c.post("/view-as", data={"role": "staff", "grade": "member", "back": "/"}, follow_redirects=False)
    assert r.status_code == 303
    assert json.loads(app.state.db.get_setting("view_as:zzdev"))["grade"] == "member"
    assert c.get("/programs/admin").status_code == 403                  # 일반으로 보면 사업 관리 못 엶
    assert c.post("/view-as", data={"role": "staff", "grade": "boss"}, follow_redirects=False).status_code == 400


# ---------------------------------------------------------------- RAG 최소 등급

def _cfg(tmp_path, **more):
    cfg = rag_spaces.config(tmp_path)
    cfg["dept_rags"] = [{"dept": "기획처"}, {"dept": "산학협력단"}]
    cfg["grants"] = {"kim": ["dept:산학협력단"]}
    cfg.update(more)
    rag_spaces.save_config(cfg, tmp_path)


def test_default_config_changes_nothing(tmp_path):
    _cfg(tmp_path)
    for g in ("", "member", "lead", "head"):
        sc = rag_spaces.search_scope("staff", "기획처", "kim", tmp_path, grade=g)
        assert sc == rag_spaces.search_scope("staff", "기획처", "kim", tmp_path)
        assert access_policy.viewer("staff", "기획처", "kim", tmp_path, grade=g) == access_policy.viewer("staff", "기획처", "kim", tmp_path)
    assert access_guard.search_scope("기획처", "staff", "kim", tmp_path) == rag_spaces.search_scope("staff", "기획처", "kim", tmp_path)


def test_min_grade_narrows_scope_and_view(tmp_path):
    _cfg(tmp_path, min_grade={"common": "lead", "dept:산학협력단": "head"})
    full = rag_spaces.search_scope("staff", "기획처", "kim", tmp_path)
    assert set(full["grant_depts"]) == {"기획처", "공통", "산학협력단"}
    assert rag_spaces.search_scope("staff", "기획처", "kim", tmp_path, grade="member")["grant_depts"] == ["기획처"]
    assert set(rag_spaces.search_scope("staff", "기획처", "kim", tmp_path, grade="lead")["grant_depts"]) == {"기획처", "공통"}
    assert set(rag_spaces.search_scope("staff", "기획처", "kim", tmp_path, grade="head")["grant_depts"]) == {"기획처", "공통", "산학협력단"}
    assert rag_spaces.search_scope("dev", None, "zzdev", tmp_path, grade="admin")["grant_depts"] is None
    # 열람: 부서 RAG 만 빠진다(전체 공개 문서는 그대로)
    v = access_policy.viewer("staff", "산학협력단", "lee", tmp_path, grade="lead")
    assert v["dept"] is None
    doc = {"access_level": "dept", "dept": "산학협력단", "owner": "x"}
    assert not access_policy.visible(doc, **v)
    assert access_policy.visible(doc, **access_policy.viewer("staff", "산학협력단", "lee", tmp_path, grade="head"))
    assert access_policy.visible({"access_level": "public", "dept": "공통"}, **access_policy.viewer("staff", None, "lee", tmp_path, grade="member"))
    assert access_policy.viewer("staff", "기획처", "kim", tmp_path, grade="lead")["also"] == ()


def test_rag_screen_sets_min_grade(tmp_path):
    app, c = _dev(tmp_path)
    c.post("/dev/rag/dept", data={"action": "create", "name": "기획처"})
    page = c.get("/dev/rag").text
    assert 'action="/dev/rag/min-grade"' in page and "걸지 않음" in page
    r = c.post("/dev/rag/min-grade", data={"space": "dept:기획처", "grade": "head"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    assert rag_spaces.config(tmp_path)["min_grade"] == {"dept:기획처": "head"}
    assert "err=" in c.post("/dev/rag/min-grade", data={"space": "dept:없는부서", "grade": "head"}, follow_redirects=False).headers["location"]
    c.post("/dev/rag/min-grade", data={"space": "dept:기획처", "grade": ""})
    assert "min_grade" not in rag_spaces.config(tmp_path)
    _add(app, "plain", dept="기획처", grade="member")
    assert _client(app, "plain").post("/dev/rag/min-grade", data={"space": "common", "grade": "lead"}).status_code == 403


# ---------------------------------------------------------------- 사업 관리

LEDGER = {"programs": [
    {"name": "가나다 혁신사업", "terms": ["가나다"], "ministry": "교육부", "category": "일반재정지원",
     "school": {"years": [2024, 2026], "depts": ["기획처"]}},
    {"name": "라마바 지원사업", "terms": [], "ministry": "대구광역시", "category": "교내 장부",
     "school": {"years": [2025], "depts": ["산학협력단"]}},
    {"name": "사업 아닌 것", "category": "사업 아님"},
]}


@pytest.fixture()
def world(tmp_path):
    app = _app(tmp_path)
    (tmp_path / "kg_external.json").write_text(json.dumps(LEDGER, ensure_ascii=False), encoding="utf-8")
    _add(app, "boss", dept="기획처", grade="head")
    _add(app, "lead1", dept="기획처", grade="lead")
    _add(app, "lead2", dept="산학협력단", grade="lead")
    _add(app, "plain", dept="기획처", grade="member")
    db = app.state.db
    pid = db.create_project("grant", "2026 가나다", owner="lead2", program="program:가나다혁신사업|2026")
    db.create_project("grant", "옛 묶음", owner="zzdev", archived=True, program="program:가나다혁신사업|2024")
    return app, tmp_path, pid


def test_program_admin_permissions(world):
    app, tmp_path, pid = world
    dev = _client(app, "zzdev", "devpass")
    page = dev.get("/programs/admin").text
    assert "가나다 혁신사업" in page and "라마바 지원사업" in page and "사업 아닌 것" not in page
    assert "data-pa-new>" in page and "교육부 재정지원" in page
    assert _client(app, "plain").get("/programs/admin").status_code == 403
    assert _client(app, "plain").post("/programs/admin/save", data={"key": "x"}).status_code == 403
    # 총괄 책임자은 자기 부서 사업만 읽기
    boss = _client(app, "boss")
    page = boss.get("/programs/admin").text
    assert "가나다 혁신사업" in page and "라마바 지원사업" not in page and "data-pa-new>" not in page
    # 사업 담당자은 지정된 사업만 — 아직 없음
    assert "가나다 혁신사업" not in _client(app, "lead1").get("/programs/admin").text
    # 총괄 책임자이 같은 부서 사람을 사업 담당자로
    key = "program:가나다혁신사업"
    r = boss.post("/programs/admin/save", data={"key": key, "leads": ["lead1"]}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    assert "err=" in boss.post("/programs/admin/save", data={"key": key, "leads": ["lead2"]}, follow_redirects=False).headers["location"]
    assert boss.post("/programs/admin/save", data={"key": "program:라마바지원사업", "leads": ["lead1"]}).status_code == 404
    assert boss.post("/programs/admin/hide", data={"key": key}).status_code == 403
    assert "가나다 혁신사업" in _client(app, "lead1").get("/programs/admin").text
    assert _client(app, "lead1").post("/programs/admin/save", data={"key": key, "leads": ["lead1"]}).status_code == 403


def test_program_admin_edit_add_hide_csv(world):
    app, tmp_path, pid = world
    dev = _client(app, "zzdev", "devpass")
    key = "program:가나다혁신사업"
    r = dev.post("/programs/admin/save", data={"key": key, "name": "가나다 혁신사업", "l1": "교육부 재정지원", "l2": "일반재정지원",
                                                "l3": "혁신", "dept": "기획처", "period": "2024~2026", "budget": "10억 원",
                                                "heads": ["boss"], "leads": ["lead1", "lead2"]}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    store = json.loads((tmp_path / "program_admin.json").read_text())
    assert store["rows"][key]["budget"] == "10억 원" and store["rows"][key]["heads"] == ["boss"]
    assert "l1" not in store["rows"][key]                              # 장부와 같은 값은 따로 적지 않는다
    assert "err=" in dev.post("/programs/admin/save", data={"key": key, "name": "가나다 혁신사업", "heads": ["plain"]},
                              follow_redirects=False).headers["location"]
    dev.post("/programs/admin/save", data={"key": "", "name": "새 교내 사업", "dept": "교무처"})
    rows = {r["key"]: r for r in program_admin.merged_rows(tmp_path)}
    assert rows["program:새교내사업"]["source"] == "추가"
    program_admin.link_projects(app.state.db, list(rows.values()))
    assert rows[key]["projects"] == 1 and rows[key]["archived"] == 1
    csv_text = dev.get("/programs/admin.csv").text
    assert csv_text.startswith("﻿대분류") and "10억 원" in csv_text and "BOSS(boss)" in csv_text
    assert "가나다" not in dev.get("/programs/admin.csv", params={"dept": "교무처"}).text
    dev.post("/programs/admin/hide", data={"key": "program:새교내사업", "hidden": "1"})
    assert "새 교내 사업" not in dev.get("/programs/admin").text and "새 교내 사업" in dev.get("/programs/admin?hidden=1").text
    # 지정은 진행 현황 권한으로 이어진다
    proj = app.state.db.get_project(pid)
    lead1 = app.state.accounts["lead1"]
    assert grades.can_view_activity(proj, user="lead1", account=lead1, data_dir=tmp_path)
    assert grades.can_view_activity(proj, user="boss", account={"role": "staff", "grade": "head", "dept": "교무처"}, data_dir=tmp_path)
    assert not grades.can_view_activity(proj, user="plain", account=app.state.accounts["plain"], data_dir=tmp_path)
    assert program_admin.roles_for_project({"program": ""}, tmp_path) == {}
