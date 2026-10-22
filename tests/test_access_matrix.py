"""열람 규칙 한 곳(access_policy) × 조회 경로 — 관리자·기획처 교직원·산학협력단 교직원·학생·추가 권한 계정이 각 경로에서
보는 문서가 같은 규칙을 따르는지, 권한 밖 문서가 이름·건수·존재로도 새지 않는지(docs/notes/2026-10-15-access-control.md)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import access_guard, access_policy, grant_search, library_folders, rag_spaces
from zzaimy.app.main import QUALITY_KIND_LABELS, create_app
from zzaimy.graph import kg_store

ACCOUNTS = {
    "plan": {"pw": "plan-pass", "role": "staff", "dept": "기획처"},
    "sanhak": {"pw": "san-pass", "role": "staff", "dept": "산학협력단"},
    "stu": {"pw": "stu-pass", "role": "student"},
    "granted": {"pw": "gr-pass", "role": "staff", "dept": "산학협력단"},     # 기획처 부서 RAG 를 추가 권한으로 받은 계정
}
PW = {"zzdev": "devpass", **{k: v["pw"] for k, v in ACCOUNTS.items()}}

# 문서 이름 — 화면 글에서 찾을 수 있게 서로 겹치지 않는 낱말
DOCS = {
    "g_pub": ("공통사업계획 누리.hwp", "grant", "공통", None, "zzdev"),
    "g_plan": ("기획처내부계획 누리.hwp", "grant", "기획처", None, "zzdev"),
    "g_san": ("산학내부계획 누리.hwp", "grant", "산학협력단", None, "zzdev"),
    "o_san": ("산학담당메모 누리.hwp", "grant", "공통", "owner", "sanhak"),
    "r_stu": ("학칙누리 규정.hwp", "regulation", "공통", None, "zzdev"),
    "r_staff": ("교원인사누리 규정.hwp", "regulation", "공통", None, "zzdev"),
    "r_plan": ("기획처예산누리 규정.hwp", "regulation", "기획처", None, "zzdev"),
}
SEES = {
    "zzdev": set(DOCS),
    "plan": {"g_pub", "g_plan", "r_stu", "r_staff", "r_plan"},
    "sanhak": {"g_pub", "g_san", "o_san", "r_stu", "r_staff"},
    "granted": {"g_pub", "g_san", "g_plan", "r_stu", "r_staff", "r_plan"},
    "stu": {"r_stu"},
}


def _stem(key: str) -> str:
    return DOCS[key][0].rsplit(".", 1)[0]


@pytest.fixture()
def world(tmp_path):
    (tmp_path / "accounts.json").write_text(json.dumps({
        "zzdev": {"pw": "devpass", "role": "dev"}, "zzaimy": {"pw": "boot-pass-1", "role": "staff"}, **ACCOUNTS}))
    (tmp_path / "rag_spaces.json").write_text(json.dumps({
        "dept_rags": [{"dept": "기획처"}, {"dept": "산학협력단"}], "grants": {"granted": ["dept:기획처"]}}))
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                     password="boot-pass-1")
    db = app.state.db
    src = tmp_path / "x.txt"
    src.write_text("본문")
    ids = {}
    for key, (name, typ, dept, level, owner) in DOCS.items():
        ids[key] = db.add_document(name, str(src), doc_type=typ, owner=owner, dept=dept, access_level=level)
        db.update_document(ids[key], status="reviewed", masked_text="누리 본문")
    db.set_document_audience(ids["r_stu"], "student")
    with db._conn() as conn:
        for key in ("r_stu", "r_staff", "r_plan"):
            d = db.get_document(ids[key])
            conn.execute("INSERT INTO regulation_chunks (doc_id, reg_title, heading, content, sector, dept, access_level)"
                         " VALUES (?, ?, '제1조', '누리 조항', 'common', ?, ?)", (ids[key], _stem(key), d["dept"], d["access_level"]))
    # 지식그래프 — 한 사업·한 연차 아래 사업 문서 넷, 기획처 문서에서만 나온 단위과제 하나
    kg_store.ensure(db)
    with db._conn() as conn:
        grants = [k for k in ids if k.startswith(("g_", "o_"))]
        kg_store.put_nodes(conn, [("program:P", "program", "누리 사업", {"n_docs": 4}),
                                  ("year:P:1", "year", "누리 사업 1차년도", {"year": 2025}),
                                  ("unit:secret", "unit", "기획처숨은과제", {"n_docs": 1})]
                           + [(f"doc:{ids[k]}", "doc", _stem(k), {}, ids[k]) for k in grants])
        kg_store.put_edges(conn, [("program:P", "year:P:1", "contains", "분류", [f"문서 #{ids['g_plan']} 분류: {_stem('g_plan')}"]),
                                  (f"doc:{ids['g_plan']}", "unit:secret", "instance_of", "식별자 일치", ["과제 코드"]),
                                  ("program:P", "unit:secret", "contains", "식별자 일치", ["문서 1건에 같은 제목의 절"])]
                           + [("year:P:1", f"doc:{ids[k]}", "contains", "분류", ["경로"]) for k in grants])
    # 라이브러리 폴더 — 기획처 범위 폴더 하나
    library_folders.create(db, tmp_path, "기획처전용폴더", "기획처", by="zzdev", allowed=["기획처", "산학협력단"])
    return app, ids, tmp_path


def _client(app, uid) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": uid, "pw": PW[uid]}, follow_redirects=False)
    assert r.status_code == 303, uid
    return c


def _viewer(app, uid):
    a = app.state.accounts[uid]
    return access_policy.viewer(a.get("role"), a.get("dept"), uid, app.state.data_dir)


@pytest.mark.parametrize("uid", list(SEES))
def test_visible_and_sql_agree(world, uid):
    """규칙의 두 꼴(파이썬 visible·SQL doc_sql)이 다섯 계정 모두에서 같은 문서를 고른다 — 그리고 기대한 표와 같다."""
    app, ids, _ = world
    db, v = app.state.db, _viewer(app, uid)
    by_py = {k for k, i in ids.items() if access_policy.visible(db.get_document(i), **v)}
    sql, args = access_policy.doc_sql(v)
    with db._conn() as conn:
        got = {int(r[0]) for r in conn.execute(f"SELECT d.id FROM documents d WHERE 1 = 1{sql}", args)}
    by_sql = {k for k, i in ids.items() if i in got}
    assert by_py == by_sql == SEES[uid]


@pytest.mark.parametrize("uid", ["zzdev", "plan", "sanhak", "granted"])
def test_library_lists_only_visible(world, uid):
    app, ids, _ = world
    c = _client(app, uid)
    page = c.get("/", params={"type": "all"}).text
    for k in DOCS:
        assert (_stem(k) in page) == (k in SEES[uid]), (uid, k)
    # 범위 밖 부서를 골라 걸러도 아무것도 나오지 않는다
    other = c.get("/", params={"type": "all", "scope": "기획처"}).text
    assert (_stem("g_plan") in other) == ("g_plan" in SEES[uid])
    # 범위 밖 부서의 폴더는 이름도 보이지 않는다
    assert ("기획처전용폴더" in page) == (uid in ("zzdev", "plan", "granted"))


@pytest.mark.parametrize("uid", ["zzdev", "plan", "sanhak", "granted", "stu"])
def test_document_paths_404_outside_scope(world, uid):
    app, ids, _ = world
    c = _client(app, uid)
    for k, i in ids.items():
        for path in (f"/doc/{i}", f"/doc/{i}/original", f"/doc/{i}/original/download", f"/api/doc/{i}/google",
                     f"/chat/peek/{i}", f"/doc/{i}/export.md"):
            code = c.get(path).status_code
            if k not in SEES[uid]:
                assert code in (403, 404), (uid, k, path, code)


@pytest.mark.parametrize("uid", ["zzdev", "plan", "sanhak", "granted"])
def test_search_page_follows_rule(world, uid):
    app, ids, _ = world
    page = _client(app, uid).get("/search", params={"q": "누리"}).text
    for k in DOCS:
        if k not in SEES[uid]:
            assert _stem(k) not in page, (uid, k)


def test_student_cannot_open_browse_screens(world):
    app, ids, _ = world
    s = _client(app, "stu")
    assert s.get("/", params={"type": "all"}, follow_redirects=False).status_code in (303, 403)
    for path in ("/search?q=누리", "/graph/explore", "/graph/explore/overview", "/graph/explore/search?q=누리",
                 f"/graph/explore/neighbors?id=year:P:1", "/graph/program", "/archive", "/dev/rag", "/dev/export",
                 "/api/projects/browse"):
        assert s.get(path, follow_redirects=False).status_code == 403, path
    assert s.get(f"/doc/{ids['r_stu']}").status_code == 200                    # 학생 공개 규정은 읽는다
    assert 'action="/search"' not in s.get("/chat").text                       # 상단 검색창도 숨긴다


@pytest.mark.parametrize("uid", ["plan", "sanhak"])
def test_staff_cannot_open_admin_screens(world, uid):
    app, _, _ = world
    c = _client(app, uid)
    for path in ("/dev/rag", "/dev/export", "/dev/export/regulations"):
        assert c.get(path, follow_redirects=False).status_code == 403, path


@pytest.mark.parametrize("uid", ["zzdev", "plan", "sanhak", "granted"])
def test_graph_hides_outside_docs_names_and_counts(world, uid):
    app, ids, _ = world
    c = _client(app, uid)
    want = {ids[k] for k in SEES[uid] if k.startswith(("g_", "o_"))}
    nb = c.get("/graph/explore/neighbors", params={"id": "year:P:1"}).json()
    got = {n["doc_id"] for n in nb["nodes"] if n["type"] == "doc"}
    assert got == want
    assert {g["key"]: g["total"] for g in nb["groups"]}["contains:out:doc"] == len(want)
    panel = c.get("/graph/explore/docs", params={"id": "year:P:1"}).json()
    assert panel["total"] == len(want)
    ov = c.get("/graph/explore/overview").json()
    assert ov["totals"]["docs"] == len(want)
    # 권한 밖 문서의 이름은 노드 검색에도, 사업 노드의 관계 근거 문구에도 나오지 않는다
    hits = c.get("/graph/explore/search", params={"q": "누리"}).json()
    shown = json.dumps(hits, ensure_ascii=False) + json.dumps(c.get("/graph/explore/neighbors", params={"id": "program:P"}).json(),
                                                              ensure_ascii=False)
    for k in DOCS:
        if k not in SEES[uid]:
            assert _stem(k) not in shown, (uid, k)
    # 기획처 문서에서만 나온 단위과제 — 기획처 범위 밖이면 이름도 열리지 않는다
    unit = c.get("/graph/explore/neighbors", params={"id": "unit:secret"})
    assert (unit.status_code == 200) == ("g_plan" in SEES[uid])
    if uid != "zzdev":
        assert all("n_docs" not in n["info"] for n in hits)                 # 그래프 전체 문서 수(권한 밖 포함)는 싣지 않는다


@pytest.mark.parametrize("uid", ["zzdev", "plan", "sanhak", "granted", "stu"])
def test_chat_evidence_stays_in_scope(world, uid):
    """채팅 근거 — 사업 문서 검색 조건(grant_search._access)과 규정 조각 조건(list_regulation_chunks)이 계정 범위 밖 문서를 내지 않는다."""
    app, ids, data_dir = world
    db = app.state.db
    a = app.state.accounts[uid]
    sc = access_guard.search_scope(a.get("dept") or None, a["role"], uid, data_dir)
    allowed = {k for k in SEES[uid]}
    if sc.get("grant"):
        acc, args = grant_search._access(sc.get("user"), sc.get("grant_depts"))
        with db._conn() as conn:
            got = {int(r[0]) for r in conn.execute(
                "SELECT d.id FROM documents d WHERE d.doc_type IN ('grant', 'auto') AND d.status = 'reviewed'" + acc, args)}
        assert {k for k, i in ids.items() if i in got} <= allowed
    else:
        assert uid == "stu"
    regs = {int(r["doc_id"]) for r in db.list_regulation_chunks(**access_guard.reg_kwargs(sc))}
    assert {k for k, i in ids.items() if i in regs} == {k for k in allowed if k.startswith("r_")}


def test_scope_note_for_other_department_requests(world):
    app, ids, data_dir = world
    depts = ["공통", "기획처", "산학협력단"]
    note = access_guard.scope_note("기획처 RAG 에서 예산 자료 찾아 줘", "산학협력단", "staff", depts)
    assert note and "기획처 자료는 열람 권한이 없습니다" in note and _stem("g_plan") not in note
    assert access_guard.scope_note("기획처 RAG 에서 예산 자료 찾아 줘", "기획처", "staff", depts) is None
    assert access_guard.scope_note("기획처 자료 찾아 줘", "산학협력단", "staff", depts,
                                   access_policy.extra_depts("granted", data_dir)) is None          # 추가 권한 계정
    assert access_guard.scope_note("기획처 자료 찾아 줘", None, "staff", depts)                      # 부서 없는 교직원도
    assert access_guard.scope_note("기획처 자료 찾아 줘", None, "dev", depts) is None


def test_rag_space_grants_reach_regulations(world):
    """추가 권한 부서는 사업 문서뿐 아니라 그 부서 규정도 채팅 근거 범위에 든다(reg_depts)."""
    _, ids, data_dir = world
    sc = rag_spaces.search_scope("staff", "산학협력단", "granted", data_dir)
    assert sc["reg_depts"] == ["산학협력단", "기획처"] and "기획처" in sc["grant_depts"]


def test_legacy_professor_head_roles_count_as_staff():
    """역할은 교직원·학생·관리자 셋(10-21). 예전 교수·부서장으로 저장된 계정은 교직원과 같은 범위로 본다."""
    from zzaimy.app import access_policy as ap
    from zzaimy.app.access_guard import ROLES
    assert set(ROLES) == {"staff", "student", "dev"}
    docs = [{"access_level": "public", "dept": "공통"},
            {"access_level": "dept", "dept": "기획처"},
            {"access_level": "dept", "dept": "산학협력단"}]
    for legacy in ("professor", "head"):
        for d in docs:
            assert ap.visible(d, dept="기획처", user="p", role=legacy) == ap.visible(d, dept="기획처", user="s", role="staff")
        assert ap.role_class(legacy) == "staff"


def test_staff_can_work_on_docs_of_assigned_depts():
    """교직원은 소속 부서와 추가로 받은 부서(여럿)의 문서를 작업(고치기)한다 — 공통 문서·다른 부서·담당자 한정 문서는 못 고친다."""
    from zzaimy.app import access_policy as ap
    me = {"role": "staff", "dept": "기획처", "user": "s", "also": ("산학협력단", "교무처")}
    assert ap.can_edit({"dept": "기획처", "access_level": "dept", "owner": "x"}, **me)
    assert ap.can_edit({"dept": "산학협력단", "access_level": "dept", "owner": "x"}, **me)
    assert ap.can_edit({"dept": "교무처", "access_level": "dept", "owner": "x"}, **me)
    assert not ap.can_edit({"dept": "학생처", "access_level": "dept", "owner": "x"}, **me)
    assert not ap.can_edit({"dept": "공통", "access_level": "public", "owner": "x"}, **me)
    assert ap.can_edit({"dept": "공통", "access_level": "public", "owner": "s"}, **me)          # 자기가 올린 문서
    assert not ap.can_edit({"dept": "기획처", "access_level": "owner", "owner": "x"}, **me)   # 담당자 한정
    assert not ap.can_edit({"dept": "기획처", "access_level": "dept", "owner": "x"}, role="student", dept="기획처", user="t")
    assert ap.can_edit({"dept": "학생처", "access_level": "dept", "owner": "x"}, role="dev", dept="", user="d")


def test_student_chat_is_academic_qa():
    """학생 대화 화면은 학사 질문용 — 예시 질문, 첨부·문서 패널·답변 방식 숨김, 답 지시는 학생 안내(교직원 업무 관점 아님)."""
    from zzaimy.app.responder import compose_system
    assert "학사 질문" in compose_system({}, "student") and "교직원" not in compose_system({}, "student").split("\n")[0]
    assert compose_system({}).startswith("당신은 영남이공대학교 행정 담당자")


def test_student_chat_page_shows_academic_examples(world):
    app = world["app"] if isinstance(world, dict) and "app" in world else world[0]
    page = _client(app, "stu").get("/chat").text
    assert "학사 관련해 무엇이든" in page and 'data-example="휴학은' in page
    assert 'id="attachButton"' in page and 'id="attachButton" title="파일 또는 사진 첨부" aria-label="파일 또는 사진 첨부" hidden' in page


def test_dev_view_as_narrows_to_chosen_account(world):
    """개발자 「테스트 보기」 — 역할·부서를 고르면 그 계정이 보는 범위로(개발자가 올린 문서도 자기 문서로 치지 않는다), 원래대로 돌아온다.
    개발자 아닌 계정은 켤 수 없다."""
    app = world["app"] if isinstance(world, dict) and "app" in world else world[0]
    dev = _client(app, "zzdev")
    r = dev.post("/view-as", data={"role": "staff", "dept": "기획처", "back": "/"}, follow_redirects=False)
    assert r.status_code == 303
    page = dev.get("/?type=all").text
    assert "테스트 보기 중" in page and "기획처내부계획" in page and "산학내부계획" not in page
    assert dev.get("/dev").status_code == 403                     # 고른 계정 그대로 — 개발자 화면도 막힌다
    assert dev.post("/view-as/clear", data={"back": "/"}, follow_redirects=False).status_code == 303
    assert "산학내부계획" in dev.get("/?type=all").text and dev.get("/dev").status_code == 200
    dev.post("/view-as", data={"role": "student", "back": "/"})
    assert dev.get("/?type=all").status_code in (403, 303) or "학생" in dev.get("/chat").text
    dev.post("/view-as/clear", data={"back": "/"})
    assert _client(app, "plan").post("/view-as", data={"role": "student"}).status_code == 403
    assert dev.post("/view-as", data={"role": "dev"}).status_code == 400


def test_view_as_back_is_local_only(world):
    """되돌아갈 주소는 서비스 안 경로만 — 「/\\evil.com」 처럼 브라우저가 다른 사이트로 읽는 꼴은 기본 화면으로."""
    app = world["app"] if isinstance(world, dict) and "app" in world else world[0]
    dev = _client(app, "zzdev")
    for bad in ("//evil.com", "/\\evil.com", "https://evil.com", "/\\\\evil.com"):
        r = dev.post("/view-as/clear", data={"back": bad}, follow_redirects=False)
        assert r.headers["location"] == "/", bad
    assert dev.post("/view-as/clear", data={"back": "/chat"}, follow_redirects=False).headers["location"] == "/chat"


def test_only_owner_or_admin_changes_a_document(world):
    """볼 수 있다고 바꿀 수 있지 않다 — 지우기·판정·초안·다시 읽기·갈래·접수번호·검토 의견은 올린 사람과 관리자만(10-16).
    품질 신고만 볼 수 있는 누구나. 기관 정보는 관리자만."""
    app, ids, _ = world
    db = app.state.db
    plan = _client(app, "plan")
    writes = [("delete", {}), ("decision", {"decision": "approved"}), ("draft", {}), ("revise", {"want": "고쳐"}),
              ("identity", {}), ("route", {}), ("analyze", {}), ("receipt", {"receipt_no": "2026-국고-0099"}),
              ("review", {"opinion": "x"}), ("read-pages", {})]
    for path, data in writes:
        r = plan.post(f"/doc/{ids['g_pub']}/{path}", data=data, follow_redirects=False)
        assert r.status_code == 403, path
    assert db.get_document(ids["g_pub"]) is not None                    # 공통 문서가 지워지지 않았다
    r = plan.post(f"/doc/{ids['g_pub']}/quality-report", data={"kind": next(iter(QUALITY_KIND_LABELS))}, follow_redirects=False)
    assert r.status_code == 303
    san = _client(app, "sanhak")                                          # 자기가 올린 문서는 바꾼다
    assert san.post(f"/doc/{ids['o_san']}/review", data={"opinion": "보완"}, follow_redirects=False).status_code == 303
    assert plan.post("/institution", data={"대학명": "x"}, follow_redirects=False).status_code == 403
    assert _client(app, "zzdev").post("/institution", data={"대학명": ""}, follow_redirects=False).status_code == 303


def test_helper_question_never_changes_a_guessed_document(world):
    """떠 있는 창 질문의 낱말로 짐작한 문서에는 아무 일도 저절로 하지 않는다 — 지금 보는 문서이고 바꿀 수 있을 때만."""
    app, ids, _ = world
    plan = _client(app, "plan")
    before = app.state.db.get_document(ids["g_pub"])
    body = plan.post("/chat/ask", data={"question": f"{_stem('g_pub')} 사업 정체 파악해서 초안 작성해 줘", "page": "/dev"}).json()
    assert body["done"] == [] and not any(a.get("changes") for a in body["actions"])
    after = app.state.db.get_document(ids["g_pub"])
    assert (after.get("identity"), after.get("draft")) == (before.get("identity"), before.get("draft"))


def test_other_peoples_projects_stay_closed(world):
    """진행 중인 남의 프로젝트는 보지도 바꾸지도 못한다(이름·대화 제목·메모·지침이 화면에 있다). 보관된 사업 묶음은 볼 수 있는
    문서가 있을 때만 읽기와 「불러오기」 — 불러오기로 진행 중인 남의 프로젝트를 가져갈 수 없다(10-16)."""
    app, ids, _ = world
    db = app.state.db
    live = db.create_project("grant", "남의진행프로젝트", owner="zzdev")
    db.update_project_meta(live, instructions="비밀지침")
    plan = _client(app, "plan")
    assert plan.get(f"/project/{live}").status_code == 404
    for path in ("unarchive", "archive", "meta", "notes", "criteria"):
        assert plan.post(f"/project/{live}/{path}", data={"content": "x"}, follow_redirects=False).status_code == 404, path
    assert plan.post(f"/projects/{live}/delete", follow_redirects=False).status_code == 404
    assert db.get_project(live)["owner"] == "zzdev" and not db.get_project(live).get("archived")
    # 보관 묶음 — 기획처 문서가 든 묶음은 기획처 직원이 보고 불러오고, 산학협력단 직원은 못 본다
    bundle = db.create_project("grant", "지난사업묶음", owner="zzdev")
    db.set_document_project(ids["g_plan"], bundle)
    db.set_project_archived(bundle, True)
    assert _client(app, "sanhak").get(f"/project/{bundle}").status_code == 404
    assert plan.get(f"/project/{bundle}").status_code == 200
    assert plan.post(f"/project/{bundle}/meta", data={"memo": "x"}, follow_redirects=False).status_code == 404
    assert plan.post(f"/project/{bundle}/unarchive", follow_redirects=False).status_code == 303
    assert db.get_project(bundle)["owner"] == "plan"


def test_google_account_of_another_user_cannot_be_used(world, monkeypatch):
    """구글 계정은 본인이 연결한 것과 부서 공용만 — 남이 연결한 계정으로 드라이브를 둘러보거나 문서를 잇지 못한다."""
    from zzaimy.ingest import gdrive
    app, ids, _ = world
    db = app.state.db
    monkeypatch.setattr(gdrive, "list_accounts", lambda: [{"email": "a@ync.ac.kr"}, {"email": "b@ync.ac.kr"}, {"email": "d@ync.ac.kr"}])
    db.set_setting("google_account:plan", "a@ync.ac.kr")
    db.set_setting("google_account:sanhak", "b@ync.ac.kr")
    db.set_setting("google_account_dept:기획처", "d@ync.ac.kr")
    plan = _client(app, "plan")
    got = {a["email"] for a in plan.get("/api/chat-documents/accounts").json()["accounts"]}
    assert got == {"a@ync.ac.kr", "d@ync.ac.kr"}
    assert plan.get("/api/chat-documents/browse", params={"account": "b@ync.ac.kr"}).status_code == 400
    assert plan.post("/api/chat-documents/connect", data={"doc": "x", "account": "b@ync.ac.kr"}).status_code == 400
    assert plan.post("/gdocs/replace", data={"doc": "x", "account": "b@ync.ac.kr", "old": "a", "new": "b"},
                     follow_redirects=False).status_code == 403
    assert plan.get("/gdocs/work", params={"doc": "x", "account": "b@ync.ac.kr"}).status_code == 403


def test_same_department_colleague_reads_project_and_shared_chats_read_only(world):
    """같은 부서 동료의 진행 중 프로젝트는 읽기 전용 — 화면·지침·기준 문서와 공개된 대화를 읽는다. 대화를 이어 가거나 고치는 것,
    대화 시작·관리 단추는 담당자만. 만든 사람이 비공개로 고른 대화는 본인만. 다른 부서는 보이지 않는다(10-16)."""
    app, ids, tmp = world
    db = app.state.db
    accounts = json.loads((tmp / "accounts.json").read_text())
    accounts["plan2"] = {"pw": "plan2-pass", "role": "staff", "dept": "기획처"}
    (tmp / "accounts.json").write_text(json.dumps(accounts))
    PW["plan2"] = "plan2-pass"
    app2 = create_app(db_path=tmp / "t.db", inbox_dir=tmp / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(), password="boot-pass-1")
    pid = db.create_project("grant", "동료의기획프로젝트", owner="plan2")
    shared = db.create_chat_session("동료공개대화", owner="plan2", project_id=pid)
    db.add_chat(shared, "user", "동료질문")
    db.add_chat(shared, "assistant", "동료답")
    secret = db.create_chat_session("동료비밀대화", owner="plan2", project_id=pid)
    db.add_chat(secret, "user", "비밀질문")
    owner = _client(app2, "plan2")
    assert owner.post(f"/chat/{secret}/private", data={"private": "1"}, follow_redirects=False).status_code == 303
    plan = _client(app2, "plan")
    page = plan.get(f"/project/{pid}")
    assert page.status_code == 200 and "읽기 전용" in page.text and "동료공개대화" in page.text and "동료비밀대화" not in page.text
    assert 'action="/chat/send"' not in page.text and 'data-modal-open="#manageModal"' not in page.text
    r = plan.get(f"/chat/{shared}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"/chat/{shared}/read")
    read = plan.get(f"/chat/{shared}/read")
    assert read.status_code == 200 and "동료질문" in read.text and "동료답" in read.text and 'id="chatForm"' not in read.text
    assert plan.get(f"/chat/{secret}/read").status_code == 404
    assert plan.get(f"/chat/{shared}/messages").status_code == 404                   # 이어 쓰기용 경로는 만든 사람만
    assert plan.post("/chat/send", data={"question": "끼어들기", "session_id": str(shared)}, follow_redirects=False).status_code == 404
    assert plan.post(f"/chat/{shared}/private", data={"private": "1"}, follow_redirects=False).status_code == 404
    assert plan.post(f"/project/{pid}/notes", data={"content": "x"}, follow_redirects=False).status_code == 404
    found = plan.get("/api/projects/browse", params={"status": "active"}).json()["items"]
    assert any(it["id"] == pid and it["readonly"] for it in found)
    san = _client(app2, "sanhak")
    assert san.get(f"/project/{pid}").status_code == 404 and san.get(f"/chat/{shared}/read").status_code == 404
    assert not any(it["id"] == pid for it in san.get("/api/projects/browse", params={"status": "active"}).json()["items"])
    mine = owner.get(f"/project/{pid}").text
    assert "동료비밀대화" in mine and "공개로" in mine and "비공개로" in mine          # 본인은 둘 다 보고 공개·비공개를 바꾼다


def test_project_search_popup_lists_colleague_projects_read_only(world):
    app, ids, tmp = world
    db = app.state.db
    accounts = json.loads((tmp / "accounts.json").read_text())
    accounts["plan3"] = {"pw": "plan3-pass", "role": "staff", "dept": "기획처"}
    (tmp / "accounts.json").write_text(json.dumps(accounts))
    app2 = create_app(db_path=tmp / "t.db", inbox_dir=tmp / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(), password="boot-pass-1")
    db.create_project("grant", "누리동료사업", owner="plan3")
    db.create_project("grant", "누리남부서사업", owner="sanhak")
    got = _client(app2, "plan").get("/api/projects/search", params={"q": "누리"}).json()["projects"]
    assert [(p["name"], p["readonly"]) for p in got] == [("누리동료사업", True)]


def test_colleague_sharing_never_reveals_what_the_reader_cannot_see(world):
    """보안 검토(10-16) — 동료 공유가 읽는 사람의 범위를 넘지 않는다.
    1) 대화가 근거로 쓴 문서·첨부 가운데 내가 못 보는 것이 있으면 그 대화는 읽을 수 없다.
    2) 관리자나 다른 부서 RAG 추가 권한을 받은 계정은 동료로 보지 않는다(그 사람 답에는 내가 못 보는 자료가 섞인다).
    3) 프로젝트 찾기는 동료 프로젝트를 이름으로만 — 문서 제목·비공개 대화 제목으로 떠보지 못한다."""
    app, ids, tmp = world
    db = app.state.db
    accounts = json.loads((tmp / "accounts.json").read_text())
    accounts["plan4"] = {"pw": "p4", "role": "staff", "dept": "기획처"}
    accounts["planadmin"] = {"pw": "pa", "role": "dev", "dept": "기획처"}
    accounts["sanplus"] = {"pw": "sp", "role": "staff", "dept": "산학협력단"}      # granted 와 달리 추가 권한 없음
    (tmp / "accounts.json").write_text(json.dumps(accounts))
    PW.update({"plan4": "p4", "planadmin": "pa", "sanplus": "sp"})
    app2 = create_app(db_path=tmp / "t.db", inbox_dir=tmp / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(), password="boot-pass-1")
    from zzaimy.app import chat_topics
    pid = db.create_project("grant", "누리공유사업", owner="plan4")
    ok = db.create_chat_session("공개대화", owner="plan4", project_id=pid)
    db.add_chat(ok, "user", "질문")
    hidden_doc = db.add_document("plan4만보는메모.hwp", str(tmp / "x.txt"), doc_type="grant", owner="plan4", dept="기획처",
                                 access_level="owner")
    leaky = db.create_chat_session("못보는근거대화", owner="plan4", project_id=pid)
    db.add_chat(leaky, "user", f"[첨부#{hidden_doc}] 메모\n정리해 줘")
    cited = db.create_chat_session("못보는인용대화", owner="plan4", project_id=pid)
    db.add_chat(cited, "user", "질문")
    chat_topics.ChatTopics(tmp / "t.db").record(cited, [{"doc_id": hidden_doc, "title": "메모"}])
    plan = _client(app2, "plan")
    assert plan.get(f"/chat/{ok}/read").status_code == 200
    assert plan.get(f"/chat/{leaky}/read").status_code == 404
    assert plan.get(f"/chat/{cited}/read").status_code == 404
    page = plan.get(f"/project/{pid}").text
    assert "공개대화" in page and "못보는근거대화" not in page
    # 관리자 계정의 프로젝트는 같은 부서여도 동료 공유가 아니다
    admin_pid = db.create_project("grant", "관리자누리사업", owner="planadmin")
    assert plan.get(f"/project/{admin_pid}").status_code == 404
    # 추가 권한(기획처 RAG)을 받은 granted 의 프로젝트는 그 권한이 없는 sanplus 에게 열리지 않는다
    g_pid = db.create_project("grant", "추가권한누리사업", owner="granted")
    assert _client(app2, "sanplus").get(f"/project/{g_pid}").status_code == 404
    assert _client(app2, "granted").get("/api/projects/search", params={"q": "누리"}).status_code == 200
    # 동료 프로젝트를 문서 제목·대화 제목으로 떠보지 못한다
    db.set_document_project(hidden_doc, pid)
    db.set_chat_private(ok, True)
    for q in ("plan4만보는메모", "공개대화"):
        assert all(p["id"] != pid for p in plan.get("/api/projects/search", params={"q": q}).json()["projects"]), q
    got = {p["id"]: p for p in plan.get("/api/projects/search", params={"q": "누리공유"}).json()["projects"]}
    assert got[pid]["n_chats"] == 2                                                   # 비공개 대화는 세지 않는다(공개 2: 근거·인용 대화)


def test_private_chat_is_owner_only_even_for_admin(world):
    app, ids, _ = world
    db = app.state.db
    pid = db.create_project("grant", "본인사업", owner="plan")
    sid = db.create_chat_session("본인비밀대화", owner="plan", project_id=pid)
    db.add_chat(sid, "user", "x")
    db.set_chat_private(sid, True)
    admin = _client(app, "zzdev")
    assert admin.get(f"/chat/{sid}/read").status_code == 404
    db.set_chat_private(sid, False)
    assert admin.get(f"/chat/{sid}/read").status_code == 200


def test_drive_share_follows_chat_read_rules_and_is_owner_only(world, monkeypatch):
    """작업 문서 공유는 대화 읽기와 같은 규칙 — 비공개 대화·못 보는 근거를 쓴 대화의 문서는 동료에게 주지 않는다.
    비공개로 돌리거나 공유를 끄면 바로 거둔다. 켜고 끄는 것은 담당자만, 첫 비밀번호를 안 바꾼 계정은 받지 않는다(보안 검토 10-17)."""
    from zzaimy.ingest import drive_share
    from zzaimy.app import chat_topics
    app, ids, tmp = world
    db = app.state.db
    accounts = json.loads((tmp / "accounts.json").read_text())
    accounts["plan5"] = {"pw": "p5", "role": "staff", "dept": "기획처"}
    accounts["plan6"] = {"pw": "p6", "role": "staff", "dept": "기획처", "must_change": True}
    (tmp / "accounts.json").write_text(json.dumps(accounts))
    PW["plan5"] = "p5"
    app2 = create_app(db_path=tmp / "t.db", inbox_dir=tmp / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(), password="boot-pass-1")
    calls = []
    def fake_sync(db_, pid_, e, want, http=None, missing=0):
        want = want()[0] if callable(want) else want
        calls.append({k: sorted(v) for k, v in want.items()})
        return {"added": [], "removed": [], "shared": 0, "error": ""}
    monkeypatch.setattr(drive_share, "sync", fake_sync)
    pid = db.create_project("grant", "공유토글사업", owner="plan5")
    pub = db.create_chat_session("공개작업", owner="plan5", project_id=pid)
    db.set_setting(f"chat_google_doc:{pub}", json.dumps({"doc": "DOCPUB0001", "account": "plan5@ync.ac.kr"}))
    priv = db.create_chat_session("비공개작업", owner="plan5", project_id=pid)
    db.set_setting(f"chat_google_doc:{priv}", json.dumps({"doc": "DOCPRIV001", "account": "plan5@ync.ac.kr"}))
    db.set_chat_private(priv, True)
    secret = db.add_document("plan5만메모.hwp", str(tmp / "x.txt"), doc_type="grant", owner="plan5", dept="기획처", access_level="owner")
    leak = db.create_chat_session("못보는근거작업", owner="plan5", project_id=pid)
    db.set_setting(f"chat_google_doc:{leak}", json.dumps({"doc": "DOCLEAK001", "account": "plan5@ync.ac.kr"}))
    chat_topics.ChatTopics(tmp / "t.db").record(leak, [{"doc_id": secret, "title": "메모"}])
    copyc = db.create_chat_session("못보는원본복제작업", owner="plan5", project_id=pid)
    db.add_file("google", "https://docs.google.com/document/d/DOCCOPY0001/edit", name="복제", session_id=copyc, doc_id=secret)
    db.set_setting("google_account:plan", "plan@ync.ac.kr")
    db.set_setting("google_account:plan6", "plan6@ync.ac.kr")
    plan = _client(app2, "plan")
    assert plan.post(f"/project/{pid}/shares", data={"mode": "private"}, follow_redirects=False).status_code == 404   # 동료는 못 바꾼다
    page = plan.get(f"/project/{pid}").text
    assert "공개작업의 작업 문서" in page and "비공개작업" not in page and "못보는근거작업" not in page and "못보는원본복제작업" not in page
    assert plan.get(f"/chat/{copyc}/read").status_code == 404                # 못 보는 문서의 복제본이 든 대화는 읽지도 못한다
    owner = _client(app2, "plan5")
    owner.get(f"/project/{pid}")
    from zzaimy.app import project_access
    owner.post(f"/project/{pid}/shares", data={"mode": "shared", "to": "dept:기획처", "level": "view"}, follow_redirects=False)
    assert calls[-1] == {"DOCPUB0001": ["plan@ync.ac.kr"]}                 # 공개·근거 열람 가능한 대화의 문서만, 첫 비번 계정 제외
    owner.post(f"/chat/{pub}/private", data={"private": "1"}, follow_redirects=False)
    assert calls[-1] == {}                                                # 비공개로 돌리면 바로 다시 맞춘다(목표가 비어 거둔다)
    owner.post(f"/chat/{pub}/private", data={"private": "0"}, follow_redirects=False)
    r = owner.post(f"/project/{pid}/shares", data={"mode": "private"}, follow_redirects=False)
    assert r.status_code == 303 and calls[-1] == {}                        # 나만으로 돌리면 준 권한을 거둔다
    assert "공유 안 함</button>" in owner.get(f"/project/{pid}").text
    assert plan.get(f"/project/{pid}").status_code == 404                  # 같은 부서여도 이제 안 보인다


def test_dept_staff_cannot_publish_dept_regulation_to_students(world):
    """부서 교직원은 그 부서 문서를 작업(공개 범위 등)할 수 있어도 학생 공개는 못 한다 — 올린 사람·관리자만(10-21 보안 검토)."""
    app, ids, tmp = world
    db = app.state.db
    did = db.add_document("기획처 내부 규정.pdf", "x", doc_type="regulation")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET owner='someone', dept='기획처', access_level='dept' WHERE id=?", (did,))
    plan = _client(app, "plan")
    r = plan.post(f"/doc/{did}/audience", data={"audience": "student"}, follow_redirects=False)
    assert r.status_code == 403
    assert (db.get_document(did).get("audience") or "staff") != "student"
    r = plan.post(f"/doc/{did}/scope", data={"dept": "기획처", "access_level": "dept"}, follow_redirects=False)
    assert r.status_code == 303                                  # 같은 부서 문서의 범위 작업은 된다


def test_project_share_to_user_with_edit_lets_them_open_their_own_chat(world, monkeypatch):
    """지정 사용자에게 「함께 작업」으로 공유하면 다른 부서여도 프로젝트를 보고 그 안에 자기 대화를 연다.
    담당자 대화·작업 문서는 담당자 검색 범위를 덮는 사람만(같은 부서 등) — 그때 작업 문서는 편집 권한.
    「보기」 공유는 대화를 열 수 없다. 공유 설정·프로젝트 관리는 담당자만(10-21)."""
    from zzaimy.ingest import drive_share
    app, ids, tmp = world
    db = app.state.db
    calls = []
    monkeypatch.setattr(drive_share, "sync", lambda db_, pid_, e, want, http=None, missing=0:
                        calls.append(want()[0] if callable(want) else want) or {"added": [], "removed": [], "shared": 0, "error": ""})
    pid = db.create_project("grant", "함께작업사업", owner="plan")
    cs = db.create_chat_session("담당자작업", owner="plan", project_id=pid)
    db.set_setting(f"chat_google_doc:{cs}", json.dumps({"doc": "DOCTOGETHER1", "account": "plan@ync.ac.kr"}))
    db.set_setting("google_account:sanhak", "sanhak@ync.ac.kr")
    owner = _client(app, "plan")
    other = _client(app, "sanhak")
    assert other.get(f"/project/{pid}").status_code == 404                 # 다른 부서 — 공유 전에는 안 보인다
    owner.post(f"/project/{pid}/shares", data={"mode": "shared", "to": "user:sanhak", "level": "edit"}, follow_redirects=False)
    # 담당자(기획처) 범위를 덮지 못하는 다른 부서 사람에게는 담당자 대화의 작업 문서를 주지 않는다 — 근거 기록 밖 자료가 섞일 수 있다
    assert calls[-1] == {}
    page = other.get(f"/project/{pid}")
    assert page.status_code == 200 and "함께 작업하도록 공유한" in page.text and 'name="project_id"' in page.text
    assert "공유받은 프로젝트" in page.text                                 # 사이드바에 따로
    assert other.get(f"/chat?project={pid}").status_code == 200
    assert other.post(f"/project/{pid}/shares", data={"mode": "private"}, follow_redirects=False).status_code == 404
    owner.post(f"/project/{pid}/shares", data={"mode": "shared", "to": "user:sanhak", "level": "view"}, follow_redirects=False)
    assert other.get(f"/chat?project={pid}").status_code == 404           # 보기만 — 새 대화는 못 연다


def test_collaborator_cannot_continue_project_chat_after_share_is_revoked(world, monkeypatch):
    """함께 작업 공유가 풀리면 그 프로젝트 안에서 연 내 대화도 이어 쓰지 못한다 — 읽기는 된다(10-21 보안 검토)."""
    from zzaimy.ingest import drive_share
    app, ids, tmp = world
    db = app.state.db
    monkeypatch.setattr(drive_share, "sync", lambda *a, **k: {"added": [], "removed": [], "shared": 0, "error": ""})
    pid = db.create_project("grant", "거둘사업", owner="plan")
    owner, other = _client(app, "plan"), _client(app, "sanhak")
    owner.post(f"/project/{pid}/shares", data={"mode": "shared", "to": "user:sanhak", "level": "edit"}, follow_redirects=False)
    mine = db.create_chat_session("내작업", owner="sanhak", project_id=pid)
    owner.post(f"/project/{pid}/shares", data={"mode": "private"}, follow_redirects=False)
    r = other.post("/chat/send", data={"question": "계속 써 줘", "session_id": mine}, follow_redirects=False)
    assert r.status_code == 403
    assert other.get(f"/chat/{mine}").status_code == 200


def test_writer_grant_turns_off_resharing():
    """함께 작업(편집) 권한을 줄 때 편집자가 다시 공유하지 못하게 한다 — 기록 밖 권한이 생기면 거둘 수 없다."""
    import httpx
    from zzaimy.ingest import drive_share, gdrive, gdrive_files

    class FakeDB:
        def __init__(self):
            self.s = {}
        def get_setting(self, k, d=""):
            return self.s.get(k, d)
        def set_setting(self, k, v):
            self.s[k] = v
    sent = []

    def handler(req):
        sent.append((req.method, str(req.url.path), json.loads(req.content or b"{}")))
        return httpx.Response(200, json={"id": "perm1"})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    orig = (gdrive_files.has_file_scope, gdrive_files._headers)
    gdrive_files.has_file_scope = lambda e: True
    gdrive_files._headers = lambda e, h: {"Authorization": "Bearer x"}
    try:
        drive_share.sync(FakeDB(), 1, "owner@ync.ac.kr", {"F1": {"col@ync.ac.kr": "writer"}}, http=http)
    finally:
        gdrive_files.has_file_scope, gdrive_files._headers = orig
    assert any(m == "POST" and b.get("role") == "writer" for m, _p, b in sent)
    assert any(m == "PATCH" and b == {"writersCanShare": False} for m, _p, b in sent)


def test_same_dept_collaborator_gets_writer_on_owner_work_docs(world, monkeypatch):
    """같은 부서(범위를 덮는) 사람에게 함께 작업으로 공유하면 담당자 작업 문서에 편집 권한."""
    from zzaimy.ingest import drive_share
    app, ids, tmp = world
    db = app.state.db
    accounts = json.loads((tmp / "accounts.json").read_text())
    accounts["plan7"] = {"pw": "p7", "role": "staff", "dept": "기획처"}
    (tmp / "accounts.json").write_text(json.dumps(accounts))
    PW["plan7"] = "p7"
    app2 = create_app(db_path=tmp / "t.db", inbox_dir=tmp / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(), password="boot-pass-1")
    calls = []
    monkeypatch.setattr(drive_share, "sync", lambda db_, pid_, e, want, http=None, missing=0:
                        calls.append(want()[0] if callable(want) else want) or {"added": [], "removed": [], "shared": 0, "error": ""})
    pid = db.create_project("grant", "같은부서함께", owner="plan")
    cs = db.create_chat_session("담당자작업", owner="plan", project_id=pid)
    db.set_setting(f"chat_google_doc:{cs}", json.dumps({"doc": "DOCSAME0001", "account": "plan@ync.ac.kr"}))
    db.set_setting("google_account:plan7", "plan7@ync.ac.kr")
    _client(app2, "plan").post(f"/project/{pid}/shares", data={"mode": "shared", "to": "user:plan7", "level": "edit"}, follow_redirects=False)
    assert calls[-1] == {"DOCSAME0001": {"plan7@ync.ac.kr": "writer"}}
