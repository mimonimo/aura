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


def test_professor_sees_like_staff_of_same_dept():
    """교수 계정은 교직원과 같은 범위(공통 + 자기 부서 + 자기 문서) — 같은 부서 안의 등급은 나중에(2026-10-15)."""
    from zzaimy.app import access_policy as ap
    docs = [{"access_level": "public", "dept": "공통"},
            {"access_level": "dept", "dept": "기획처"},
            {"access_level": "dept", "dept": "산학협력단"},
            {"access_level": "public", "doc_type": "regulation", "audience": "student"}]
    for d in docs:
        assert ap.visible(d, dept="기획처", user="p", role="professor") == ap.visible(d, dept="기획처", user="s", role="staff")
    assert not ap.visible(docs[2], dept="기획처", user="p", role="professor")
    assert ap.viewer("professor", "기획처", "p")["role"] == "professor" and ap.can_browse("professor")
    assert ap.role_class("professor") == "staff" and ap.role_class("student") == "student"


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
