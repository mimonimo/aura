"""교직원 등급(ADR-0066) — 계정 화면의 등급 저장, 관리자 2명 상한, RAG 최소 등급, 사업 관리 권한, 기본 설정 무변화."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.app import access_guard, access_policy, grades, program_admin, rag_spaces
from zzaimy.ingest import gdocs, gdrive


def _team(c, uid, dept="기획처", grade=None, perm=None):
    """구성원 등급·추가 권한 바꾸기 — 조직 관리 구성원 탭과 같은 POST /api/org/members(옛 /team/save 는 10-27 에 없앴다)."""
    r = None
    if grade is not None:
        r = c.post("/api/org/members", json={"dept": dept, "op": "grade", "uids": [uid], "value": grade})
    if perm is not None and (r is None or r.status_code == 200):
        r = c.post("/api/org/members", json={"dept": dept, "op": "perms", "uids": [uid], "value": perm})
    return r


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
    assert grades.grade_of({"role": "staff"}) == "lead"                 # 기존 계정은 사업 책임자
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
    page = c.get("/team").text                                                # 계정 편집 창은 조직 관리(ADR-0073)
    assert 'name="grade"' in page and "data-grade-field" in page
    r = c.post("/team/accounts/save", data={"uid": "kimhead", "is_new": "1", "role": "staff", "grade": "head",
                                        "new_pw": "init-pass-1"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    data = json.loads((tmp_path / "accounts.json").read_text())
    assert data["kimhead"]["grade"] == "head"
    me = next(u for u in c.get("/api/org/accounts").json()["users"] if u["user"] == "kimhead")
    assert (me["role_label"], me["grade_label"]) == ("교직원", "총괄 책임자")
    # 등급을 보내지 않으면 지금 값 유지
    c.post("/team/accounts/save", data={"uid": "kimhead", "role": "staff", "active": "1"})
    assert json.loads((tmp_path / "accounts.json").read_text())["kimhead"]["grade"] == "head"
    r = c.post("/team/accounts/save", data={"uid": "kimhead", "role": "staff", "grade": "boss", "active": "1"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    # 새 계정 기본은 사업 책임자
    c.post("/team/accounts/save", data={"uid": "newbie", "is_new": "1", "role": "staff", "new_pw": "init-pass-1"})
    assert json.loads((tmp_path / "accounts.json").read_text())["newbie"]["grade"] == "lead"


def test_admin_limit_two(tmp_path):
    app, c = _dev(tmp_path)
    r = c.post("/team/accounts/save", data={"uid": "sysadm", "is_new": "1", "role": "dev", "new_pw": "init-pass-1"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    data = json.loads((tmp_path / "accounts.json").read_text())
    assert data["sysadm"]["role"] == "dev" and "grade" not in data["sysadm"]
    r = c.post("/team/accounts/save", data={"uid": "third", "is_new": "1", "role": "dev", "new_pw": "init-pass-1"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and "third" not in json.loads((tmp_path / "accounts.json").read_text())
    r = c.post("/team/accounts/save", data={"uid": "zzaimy", "role": "dev", "active": "1"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    # 이미 관리자인 계정은 그대로 저장된다
    r = c.post("/team/accounts/save", data={"uid": "sysadm", "role": "dev", "active": "1", "name": "관리"}, follow_redirects=False)
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
    cfg["dept_grants"] = {"산학협력단": ["기획처"]}          # 산학협력단이 기획처에 부서 RAG 를 열어 줌
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
    c.post("/org/create", json={"name": "기획처"})
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
    assert "가나다 혁신사업" in page and "라마바 지원사업" not in page and "사업 아닌 것" not in page   # 현행(올해 진행)
    past = dev.get("/programs/admin?tab=past").text
    assert "라마바 지원사업" in past and "data-pa-new>" not in past and "끝난 사업(읽기만)" in past             # 지난 사업은 읽기만
    assert "data-pa-new>" in page and "교육부 재정지원" in page
    assert _client(app, "plain").get("/programs/admin").status_code == 403
    assert _client(app, "plain").post("/programs/admin/save", data={"key": "x"}).status_code == 403
    # 총괄 책임자는 자기 부서 사업만 읽기
    boss = _client(app, "boss")
    page = boss.get("/programs/admin").text
    assert "가나다 혁신사업" in page and "라마바 지원사업" not in page and "data-pa-new>" not in page
    # 사업 책임자는 지정된 사업만 — 아직 없음
    assert "가나다 혁신사업" not in _client(app, "lead1").get("/programs/admin").text
    # 총괄 책임자가 같은 부서 사람을 사업 책임자로
    key = "program:가나다혁신사업"
    r = boss.post("/programs/admin/save", data={"key": key, "leads": ["lead1"]}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    assert "err=" in boss.post("/programs/admin/save", data={"key": key, "leads": ["lead2"]}, follow_redirects=False).headers["location"]
    assert boss.post("/programs/admin/save", data={"key": "program:라마바지원사업", "leads": ["lead1"]}).status_code == 403   # 다른 부서 사업은 신청으로
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


def test_extra_perms_open_create_and_activity_for_member():
    """일반 직원도 계정 화면의 추가 권한으로 프로젝트 만들기·부서 진행 현황 보기를 받는다."""
    from zzaimy.app import grades
    m = {"role": "staff", "grade": "member", "dept": "기획처"}
    assert not grades.can_create_project(m)
    m["perms"] = ["create_project"]
    assert grades.can_create_project(m)
    proj = {"id": 1, "owner": "kim"}
    assert not grades.can_view_activity(proj, user="lee", account=m, project_dept="기획처", program_roles={})
    m["perms"].append("view_activity")
    assert grades.can_view_activity(proj, user="lee", account=m, project_dept="기획처", program_roles={})
    assert not grades.can_view_activity(proj, user="lee", account=m, project_dept="교무처", program_roles={})
    assert not grades.has_perm({"role": "student", "perms": ["create_project"]}, "create_project")


def test_team_page_head_grants_same_dept_only(tmp_path):
    """권한은 본인이 아니라 같은 부서 총괄 책임자(또는 관리자)가 준다 — 다른 부서·총괄 책임자는 못 바꾼다."""
    app, dev = _dev(tmp_path)
    _add(app, "boss", dept="기획처", grade="head")
    _add(app, "kim", dept="기획처", grade="member")
    _add(app, "park", dept="교무처", grade="member")
    _add(app, "boss2", dept="기획처", grade="head")
    boss = _client(app, "boss")
    uids = [r["uid"] for r in boss.get("/api/org/members", params={"dept": "기획처"}).json()["rows"]]
    assert "kim" in uids and "park" not in uids
    assert boss.get("/api/org/members", params={"dept": "교무처"}).status_code == 403
    r = _team(boss, "kim", grade="member", perm=["create_project"])
    assert r.status_code == 200 and app.state.accounts["kim"]["perms"] == ["create_project"]
    assert app.state.accounts["kim"]["perms_by"] == "boss" and grades.can_create_project(app.state.accounts["kim"])
    assert _team(boss, "park", dept="교무처", perm=["create_project"]).status_code == 403
    _team(boss, "boss2", grade="member")
    assert app.state.accounts["boss2"]["grade"] == "head"                 # 다른 총괄 책임자는 못 바꿈
    _team(boss, "kim", grade="head")
    assert app.state.accounts["kim"]["grade"] == "member"                 # 총괄 책임자 지정은 관리자만
    kim = _client(app, "kim")
    assert kim.get("/api/org/members", params={"dept": "기획처"}).status_code == 403   # 본인이 권한을 고르지 못한다
    assert kim.post("/api/org/members", json={"dept": "기획처", "op": "perms", "uids": ["kim"], "value": ["create_project"]}).status_code == 403
    assert 'href="/team"' in boss.get("/chat").text and 'href="/team"' in kim.get("/chat").text   # 조직 관리는 교직원 누구나(보기)
    assert dev.get("/team?dept=교무처").status_code == 200


def test_program_span_and_stage():
    """사업 관리는 연도 기준 — 끝 연도가 올해보다 앞이면 지난 사업(확인용), 진행 중·끝 열림은 현행."""
    assert program_admin.span("2022~2027") == (2022, 2027, False)
    assert program_admin.span("2025~") == (2025, None, True)
    assert program_admin.span("2026.5~2028.2")[:2] == (2026, 2028)
    assert program_admin.span("") == (None, None, False)
    assert program_admin.stage({"period": "2014~2018"}, 2026) == "past"
    assert program_admin.stage({"period": "2025~"}, 2026) == "current"
    assert program_admin.stage({"period": ""}, 2026) == "unknown"
    assert program_admin.runs_in({"period": "2017~2022"}, 2019) and not program_admin.runs_in({"period": "2017~2022"}, 2023)


def test_head_sees_all_past_programs(world):
    """총괄 책임자는 지난 사업 전체를 확인용으로 본다(다른 부서 사업 포함, 읽기만)."""
    app, tmp_path, pid = world
    boss = _client(app, "boss")
    assert "라마바 지원사업" not in boss.get("/programs/admin").text          # 현행은 자기 부서
    past = boss.get("/programs/admin?tab=past").text
    assert "라마바 지원사업" in past and "편집" not in past.split("<tbody>")[1].split("</tbody>")[0]


def test_lead_grants_perms_to_members_only_and_head_sets_grades(tmp_path):
    """사업 책임자도 같은 부서 일반 직원에게 추가 권한을 준다(등급은 못 바꿈). 사업 책임자·행정 담당자 지정은 총괄 책임자가."""
    app, _ = _dev(tmp_path)
    _add(app, "boss", dept="기획처", grade="head")
    _add(app, "lead1", dept="기획처", grade="lead")
    _add(app, "kim", dept="기획처", grade="member")
    _add(app, "lead2", dept="기획처", grade="lead")
    lead = _client(app, "lead1")
    assert lead.get("/team").status_code == 200
    _team(lead, "kim", perm=["create_project"])
    assert app.state.accounts["kim"]["perms"] == ["create_project"] and app.state.accounts["kim"]["perms_by"] == "lead1"
    _team(lead, "kim", grade="lead", perm=["create_project"])
    assert app.state.accounts["kim"]["grade"] == "member"                  # 등급은 총괄 책임자가
    _team(lead, "lead2", perm=["view_activity"])
    assert "perms" not in app.state.accounts["lead2"]                      # 다른 사업 책임자는 못 바꿈
    boss = _client(app, "boss")
    _team(boss, "kim", grade="clerk")
    assert app.state.accounts["kim"]["grade"] == "clerk" and grades.label("clerk") == "행정 담당자"
    assert grades.can_create_project(app.state.accounts["kim"])


def test_lead_cannot_grant_what_it_lacks(tmp_path):
    """보안: 사업 책임자는 부서 전체 진행 현황 보기 같은 자기에게 없는 권한을 줄 수도, 총괄 책임자가 준 것을 뺄 수도 없다."""
    app, _ = _dev(tmp_path)
    _add(app, "boss", dept="기획처", grade="head")
    _add(app, "lead1", dept="기획처", grade="lead")
    _add(app, "kim", dept="기획처", grade="member")
    lead = _client(app, "lead1")
    _team(lead, "kim", perm=["create_project", "view_activity"])
    assert app.state.accounts["kim"]["perms"] == ["create_project"]
    _team(_client(app, "boss"), "kim", perm=["create_project", "view_activity"])
    _team(lead, "kim", perm=[])
    assert app.state.accounts["kim"]["perms"] == ["view_activity"]           # 총괄 책임자가 준 권한은 남는다


def test_multi_dept_grades_and_hierarchy(monkeypatch):
    """겸직: 기획처 일반이면서 ○○사업단 총괄 책임자. 위계: 산학협력단 총괄 책임자는 하위 ○○사업단도 맡는다. 사업의 총괄을 부서로 지정."""
    monkeypatch.setattr(grades, "_children", lambda data_dir=None: {"산학협력단": ["가나사업단"]})
    kim = {"role": "staff", "grade": "member", "dept": "기획처", "dept_grades": {"가나사업단": "head"}}
    assert grades.grade_in("kim", kim, "기획처") == "member" and grades.grade_in("kim", kim, "가나사업단") == "head"
    assert grades.covered_depts("kim", kim) == {"가나사업단"} and grades.top_grade("kim", kim) == "head"
    assert grades.program_scope(kim) == "own" and grades.can_create_project(kim)          # 보안: 겸직 등급으로 소속 부서 범위가 넓어지지 않음
    proj = {"id": 1, "owner": "x", "program": ""}
    assert grades.can_view_activity(proj, user="kim", account=kim, project_dept="가나사업단", program_roles={})
    assert not grades.can_view_activity(proj, user="kim", account=kim, project_dept="교무처", program_roles={})
    boss = {"role": "staff", "grade": "head", "dept": "산학협력단"}
    assert grades.covered_depts("boss", boss) == {"산학협력단", "가나사업단"}
    assert grades.can_view_activity(proj, user="boss", account=boss, project_dept="가나사업단", program_roles={})
    other = {"role": "staff", "grade": "head", "dept": "교무처"}
    assert not grades.can_view_activity(proj, user="o", account=other, project_dept="다라사업단", program_roles={"heads": []})
    boss2 = {"role": "staff", "grade": "head", "dept": "산학협력단"}
    assert grades.can_view_activity(proj, user="b2", account=boss2, project_dept="다라사업단", program_roles={"heads": ["dept:산학협력단"]})


def test_team_page_multi_dept_head(tmp_path):
    """겸직 총괄 책임자: 기획처 일반이지만 가나사업단 총괄 책임자면 가나사업단 사람의 등급을 그 사업단에서만 정한다."""
    app, dev = _dev(tmp_path)
    for n in ("기획처", "교무처", "가나사업단"):
        assert dev.post("/org/create", json={"name": n}).json()["ok"]
    _add(app, "kim", dept="기획처", grade="member")
    app.state.accounts["kim"]["dept_grades"] = {"가나사업단": "head"}
    _add(app, "park", dept="교무처", grade="member")
    app.state.accounts["park"]["dept_grades"] = {"가나사업단": "member"}
    _add(app, "lee", dept="기획처", grade="member")
    app.state.save_accounts()
    kim = _client(app, "kim")
    rows = kim.get("/api/org/members", params={"dept": "가나사업단"}).json()["rows"]
    assert [(r["uid"], r["home"]) for r in rows if r["uid"] == "park"] == [("park", "교무처")]   # 겸직(소속 교무처)
    assert kim.get("/api/org/members", params={"dept": "기획처"}).status_code == 403          # 맡은 부서는 가나사업단뿐
    _team(kim, "park", dept="가나사업단", grade="lead")
    assert app.state.accounts["park"]["dept_grades"]["가나사업단"] == "lead" and app.state.accounts["park"]["grade"] == "member"
    assert _team(kim, "lee", dept="기획처", grade="lead").status_code == 403
