"""RAG 공간(ADR-0053) — 역할·부서 → 공간, 계정별 추가 권한, 부서 다시 붙이기, 관리 화면."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import grant_search, rag_spaces
from zzaimy.app.db import Database
from zzaimy.app.main import create_app


def test_resolve_spaces(tmp_path):
    assert rag_spaces.resolve("student", "", tmp_path)["id"] == "student"
    assert rag_spaces.resolve("staff", "", tmp_path)["id"] == "staff"
    sp = rag_spaces.resolve("staff", "LINC사업단", tmp_path)
    assert sp["id"] == "dept:LINC사업단" and sp["grant_depts"] == ["LINC사업단", "공통"]
    assert rag_spaces.resolve("dev", "", tmp_path)["grant_depts"] is None
    assert rag_spaces.resolve("nobody", "", tmp_path)["grant"] is False     # 모르는 역할은 가장 좁게


def test_search_scope_grants_add_depts_but_never_to_students(tmp_path):
    rag_spaces.save_config({"grants": {"kim": ["dept:앵커사업단"], "stu": ["dept:앵커사업단"]}}, tmp_path)
    sc = rag_spaces.search_scope("staff", "LINC사업단", "kim", tmp_path)
    assert sc["grant_depts"] == ["LINC사업단", "공통", "앵커사업단"] and sc["space"].startswith("dept:LINC사업단+")
    st = rag_spaces.search_scope("student", "", "stu", tmp_path)
    assert st["grant"] is False and st["levels"] == ("student",)
    # 부서 없는 교직원은 공통 RAG — 추가 권한으로 부서 RAG 를 더 받는다
    assert rag_spaces.search_scope("staff", "", "kim", tmp_path)["grant_depts"] == ["공통", "앵커사업단"]


def test_save_config_keeps_backup(tmp_path):
    rag_spaces.save_config({"grants": {}}, tmp_path)
    rag_spaces.save_config({"grants": {"a": ["dept:x"]}}, tmp_path)
    assert list(tmp_path.glob("rag_spaces.json.bak-*"))
    assert rag_spaces.config(tmp_path)["grants"] == {"a": ["dept:x"]}


def test_dept_of_rel(tmp_path):
    assert rag_spaces.dept_of_rel("링크/2024/계획.hwp", tmp_path) == "공통"              # 기본은 공통 RAG
    rag_spaces.save_config({"dept_of_area": {"링크": "산학협력단"}}, tmp_path)          # 폴더를 부서 RAG 에 짝지을 때만
    assert rag_spaces.dept_of_rel("링크/2024/계획.hwp", tmp_path) == "산학협력단"
    assert rag_spaces.dept_of_rel("기타/a.pdf", tmp_path) == "공통"


def test_access_filter_by_dept():
    sql, args = grant_search._access("u", ["LINC사업단", "공통"])
    assert "COALESCE(d.dept, '공통') IN (?,?)" in sql and args[-2:] == ["LINC사업단", "공통"] and "u" in args
    assert "d.access_level = 'dept'" in sql                              # 부서 RAG 문서는 그 부서 범위의 계정이 찾는다
    assert grant_search._access(None, [])[0] == " AND 1 = 0"
    assert "d.dept" not in grant_search._access(None, None)[0]          # 부서로 자르지 않음(범위 밖 갈래 조건만)


def test_backfill_depts(tmp_path):
    db = Database(tmp_path / "t.db")
    a = db.add_document("a.hwp", "dgx://링크/2024/a.hwp", doc_type="grant", dept="공통", access_level="dept")
    b = db.add_document("b.hwp", "dgx://기타/b.hwp", doc_type="grant", dept="공통")
    rag_spaces.save_config({"dept_of_area": {"링크": "산학협력단"}}, tmp_path)
    got = rag_spaces.backfill_depts(db, tmp_path)
    assert got["산학협력단"] == 1
    da, dbb = db.get_document(a), db.get_document(b)
    assert da["dept"] == "산학협력단" and da["access_level"] == "dept"                 # 부서 RAG 문서는 그 부서만
    assert dbb["dept"] == "공통"


def _client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                     password="boot-pass-1")
    c = TestClient(app)
    assert c.post("/login", data={"username": "zzdev", "pw": "devpass"}, follow_redirects=False).status_code == 303
    # 합성 계정 — 부서 셋(가·나·다부서), 학생 하나
    app.state.accounts.update({
        "u-ga": {"role": "staff", "dept": "가부서"}, "u-na": {"role": "staff", "dept": "나부서"},
        "u-da": {"role": "head", "dept": "다부서"}, "u-stu": {"role": "student"},
    })
    return c


def _grants(tmp_path):
    return json.loads((tmp_path / "rag_spaces.json").read_text(encoding="utf-8"))["grants"]


def test_rag_page_sections(tmp_path):
    c = _client(tmp_path)
    r = c.get("/dev/rag")
    assert r.status_code == 200
    for text in ("RAG 목록", "공통 RAG", "기준 문서(문서·조각)", "검색 범위 시험", "계정별 추가 권한", "추가 부서",
                 "규정·지침", "dept:가부서", "학생 공개 규정만", "관리자, 전부"):
        assert text in r.text, text
    # 학생 공간이 비면 그 줄에 안내와 지정 링크
    assert "학생 공개 규정 없음 — 학생 계정은 찾을 문서가 없습니다" in r.text and 'href="#student-regs"' in r.text
    # 범위가 정해진 계정(학생·관리자)은 표에 줄을 두지 않고 한 줄 안내로
    assert '<tr data-find="u-stu' not in r.text and "더할 것이 없어 표에서 뺐습니다" in r.text
    # 순서: 검색 공간 → 규정·기준 문서 → 계정별 추가 권한 → 검색 범위 시험(접힘)
    assert r.text.index("규정·지침</h2>") < r.text.index("계정별 추가 권한</h2>") < r.text.index('id="rgTryToggle"')
    # 계정마다 한 줄 + 「설정」 창 — 부서는 열이 아니라 창의 체크 목록(부서가 늘어도 표가 넓어지지 않음)
    assert 'data-grant-edit="u-ga"' in r.text and "나부서 더하기" not in r.text
    assert 'data-grant-edit="u-stu"' not in r.text                     # 학생 줄은 없다


def test_grant_table_saves_only_changed_cells(tmp_path):
    c = _client(tmp_path)
    rag_spaces.save_config({"grants": {"u-na": ["dept:가부서"]}}, tmp_path)
    # 화면을 연 뒤 다른 관리자가 u-da 에 권한을 줬다 — 이번 저장에서 건드리지 않은 칸이라 그대로 남아야 한다
    rag_spaces.save_config({"grants": {"u-na": ["dept:가부서"], "u-da": ["dept:가부서"]}}, tmp_path)
    cells = ["u-ga\tdept:나부서", "u-na\tdept:가부서", "u-na\tdept:다부서", "u-da\tdept:나부서"]
    r = c.post("/dev/rag/grant", data={"cell": cells, "was": ["u-na\tdept:가부서"],
                                       "on": ["u-ga\tdept:나부서", "u-na\tdept:가부서"]}, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    assert _grants(tmp_path) == {"u-ga": ["dept:나부서"], "u-na": ["dept:가부서"], "u-da": ["dept:가부서"]}
    # 끄기도 바뀐 칸만
    c.post("/dev/rag/grant", data={"cell": ["u-na\tdept:가부서"], "was": ["u-na\tdept:가부서"]}, follow_redirects=False)
    assert _grants(tmp_path) == {"u-ga": ["dept:나부서"], "u-da": ["dept:가부서"]}
    # 바뀐 칸이 없으면 저장하지 않는다
    before = sorted(tmp_path.glob("rag_spaces.json.bak-*"))
    r = c.post("/dev/rag/grant", data={"cell": ["u-ga\tdept:나부서"], "was": ["u-ga\tdept:나부서"], "on": ["u-ga\tdept:나부서"]},
               follow_redirects=False)
    assert "ok=" in r.headers["location"] and sorted(tmp_path.glob("rag_spaces.json.bak-*")) == before


def test_grant_table_rejects_students_unknown_and_all_access(tmp_path):
    c = _client(tmp_path)
    rag_spaces.save_config({"grants": {}}, tmp_path)
    for cell in ("u-stu\tdept:가부서", "u-ga\tdept:없는부서", "ghost\tdept:가부서", "zzdev\tdept:가부서"):
        r = c.post("/dev/rag/grant", data={"cell": [cell], "on": [cell]}, follow_redirects=False)
        assert "err=" in r.headers["location"], cell
    assert _grants(tmp_path) == {}
    # 한 칸이라도 거절되면 같이 보낸 칸도 저장하지 않는다
    c.post("/dev/rag/grant", data={"cell": ["u-ga\tdept:나부서", "u-stu\tdept:가부서"], "on": ["u-ga\tdept:나부서", "u-stu\tdept:가부서"]})
    assert _grants(tmp_path) == {}


def _archive(db, rels):
    from zzaimy.app import archive
    archive.ensure(db)
    with db._conn() as conn:
        for rel, area in rels:
            conn.execute("INSERT INTO archive_files (rel, area) VALUES (?, ?)", (rel, area))


def test_area_folders_from_ledger(tmp_path):
    """원본 장부의 최상위 폴더 목록 — 라이브러리 폴더 목록(원본 폴더)과 같은 집합. 플랫폼 업로드는 빠진다."""
    _client(tmp_path)
    db = Database(tmp_path / "test.db")
    _archive(db, [("가폴더/2024/a.hwp", "가폴더"), ("가폴더/b.pdf", "가폴더"), ("나폴더/c.hwp", "나폴더"),
                  ("_플랫폼업로드/d.hwp", "플랫폼 업로드")])
    assert rag_spaces.area_folders(db) == [{"folder": "가폴더", "files": 2}, {"folder": "나폴더", "files": 1}]


def test_old_area_tab_points_to_rag_space(tmp_path):
    """폴더별 담당 부서는 라이브러리 폴더의 「공개 범위 바꾸기」 한 곳 — 옛 문서 반입 탭 주소는 RAG 공간으로."""
    c = _client(tmp_path)
    r = c.get("/dev/intake?tab=areas", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dev/rag"
    assert "폴더별 담당 부서" not in c.get("/dev/intake").text


def _search_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(grant_search, "INDEX", tmp_path / "none.npz")
    db = Database(tmp_path / "test.db")
    ga = db.add_document("가부서 장비 실적.hwp", "x", doc_type="grant", sector="grant", dept="가부서", access_level="public")
    na = db.add_document("나부서 장비 실적.hwp", "x", doc_type="grant", sector="grant", dept="나부서", access_level="public")
    reg_s = db.add_document("학사 안내 규정.hwp", "x", doc_type="regulation")
    reg_t = db.add_document("내부 심사 규정.hwp", "x", doc_type="regulation")
    for did in (ga, na, reg_s, reg_t):
        db.update_document(did, status="reviewed")
    db.set_document_kind(ga, "regulation")
    db.replace_doc_chunks(ga, [{"kind": "text", "content": "공동 연구장비 활용 실적과 장비 운영 성과를 정리하였다."}])
    db.replace_doc_chunks(na, [{"kind": "text", "content": "공동 연구장비 활용 실적과 장비 운영 계획을 정리하였다."}])
    db.set_document_audience(reg_s, "student")
    with db._conn() as conn:
        for did, text in (
                (reg_s, "장학금은 직전 학기 성적과 이수 학점을 기준으로 지급하며, 장학금 신청 절차는 학기마다 따로 공고한다."),
                (reg_s, "기숙사 입사는 학기 시작 전에 신청하고, 생활 수칙을 어기면 퇴사 조치를 할 수 있다."),
                (reg_t, "장학금 지급 기준의 내부 심사는 위원회가 맡으며, 심사 결과는 장학금 담당 부서가 보관한다."),
                (reg_t, "교원 연구년은 근속 연수와 연구 실적을 따져 정하고, 신청 서류는 교무 부서에 낸다.")):
            conn.execute("INSERT INTO regulation_chunks (doc_id, reg_title, heading, content, sector, dept, access_level)"
                         " VALUES (?, '규정', '제1조', ?, 'common', '공통', 'public')", (did, text))
    return db, ga, na, reg_s, reg_t


def test_search_preview_keeps_account_scope(tmp_path, monkeypatch):
    c = _client(tmp_path)
    db, ga, na, reg_s, reg_t = _search_fixture(tmp_path, monkeypatch)
    q = "연구장비 활용 실적"
    docs = lambda target, term=q: {h["doc_id"] for h in c.get("/dev/rag/try", params={"target": target, "q": term}).json()["grant"]}  # noqa: E731
    assert docs("user:u-ga") == {ga}                       # 가부서 계정은 나부서 문서를 못 본다
    assert docs("user:u-na") == {na}
    assert docs("space:dev") == {ga, na}
    rag_spaces.save_config({"grants": {"u-ga": ["dept:나부서"]}}, tmp_path)
    assert docs("user:u-ga") == {ga, na}                   # 추가 권한을 주면 실제 검색과 같이 넓어진다
    st = c.get("/dev/rag/try", params={"target": "user:u-stu", "q": "장학금 지급 기준"}).json()
    assert st["grant"] == [] and st["student_only"] is True
    assert {h["doc_id"] for h in st["regulation"]} == {reg_s}          # 학생은 학생 공개 규정만
    staff = c.get("/dev/rag/try", params={"target": "space:staff", "q": "장학금 지급 기준"}).json()
    assert {h["doc_id"] for h in staff["regulation"]} == {reg_s, reg_t}
    assert c.get("/dev/rag/try", params={"target": "user:ghost", "q": q}).status_code == 400
    assert c.get("/dev/rag/try", params={"target": "space:staff", "q": " "}).status_code == 400


def test_search_target_picker_lists_spaces_and_people(tmp_path):
    """검색 범위 시험의 대상 찾기 — 공간과 계정을 사람 고르기 꼴로(값은 시험이 받는 그대로, 이름·역할은 우리말)."""
    c = _client(tmp_path)
    c.app.state.accounts["u-ga"]["name"] = "가사람"
    got = c.get("/dev/rag/targets").json()["people"]
    by = {p["user"]: p for p in got}
    assert "space:staff" in by and "space:dev" in by and "space:dept:가부서" in by
    assert by["user:u-ga"]["name"] == "가사람" and by["user:u-ga"]["dept"] == "가부서"
    assert by["user:u-stu"]["grade_label"] == "학생" and "staff" not in by["user:u-ga"]["grade_label"]
    only = c.get("/dev/rag/targets", params={"q": "가사람"}).json()["people"]
    assert [p["user"] for p in only] == ["user:u-ga"]


def test_space_composition_and_regulation_kind_column(tmp_path, monkeypatch):
    c = _client(tmp_path)
    db, ga, na, reg_s, reg_t = _search_fixture(tmp_path, monkeypatch)
    got = c.get("/dev/rag/space", params={"target": "space:dept:가부서"}).json()
    assert got["grant_docs"] == 1 and got["kinds"] == [{"name": "규정", "n": 1}]
    assert got["programs"] == [{"name": "사업 미분류", "n": 1}]
    # 화면은 상위 몇 줄만 보이므로 합계는 자르기 전 값으로 따로 준다
    assert got["uses_grant"] is True and got["program_count"] == 1 and got["kind_count"] == 1
    assert got["reg_docs"] == 2 and got["reg_chunks"] == 4
    stu = c.get("/dev/rag/space", params={"target": "space:student"}).json()
    assert stu["uses_grant"] is False and stu["programs"] == [] and stu["kinds"] == []      # 빈 칸은 화면에서 숨긴다
    assert [r["doc_id"] for r in stu["regulations"]] == [reg_s] and stu["reg_docs"] == 1 and stu["reg_chunks"] == 2
    rows = {s["id"]: s for s in rag_spaces.stats(db, tmp_path, {"u-ga": {"role": "staff", "dept": "가부서"}})}
    assert rows["staff"]["grant_docs"] == 0                    # 부서 없는 교직원은 공통 RAG 만 — 부서 문서는 보이지 않는다
    assert rows["student"]["reg_docs"] == 1 and rows["student"]["grant"] is False
    assert "학생 공개 규정 없음" not in c.get("/dev/rag").text


def test_student_audience_set_and_unset_from_rag_page(tmp_path, monkeypatch):
    c = _client(tmp_path)
    db, ga, na, reg_s, reg_t = _search_fixture(tmp_path, monkeypatch)
    page = c.get("/dev/rag").text
    assert 'id="rrList"' in page and 'data-rr="student"' in page        # 목록은 /dev/rag/regs 에서 불러와 골라 바꾼다
    assert 'id="rrPager"' in page and "이 쪽 모두" in page                # 20건씩 쪽으로 — 한 번에 다 펼치지 않는다
    regs = {d["id"]: d for d in c.get("/dev/rag/regs").json()["registered"]}
    assert reg_t in regs and ga not in regs
    assert c.post("/dev/rag/regs/audience", json={"ids": [reg_t, ga], "audience": "student"}).json()["changed"] == 1
    assert db.get_document(reg_t)["audience"] == "student" and db.get_document(ga).get("audience") != "student"
    c.post("/dev/rag/regs/audience", json={"ids": [reg_t], "audience": "staff"})
    r = c.post(f"/doc/{reg_t}/audience", data={"audience": "student", "back": "/dev/rag#student-regs"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dev/rag#student-regs"
    assert db.get_document(reg_t)["audience"] == "student"
    c.post(f"/doc/{reg_s}/audience", data={"audience": "staff", "back": "/dev/rag#student-regs"})
    assert db.get_document(reg_s)["audience"] == "staff"
    # 사업 문서는 규정 갈래여도 학생 공개가 되지 않는다(기존 규칙 그대로)
    assert c.post(f"/doc/{ga}/audience", data={"audience": "student", "back": "/dev/rag"}).status_code == 400
    # 바깥 주소로는 돌려보내지 않는다
    r = c.post(f"/doc/{reg_t}/audience", data={"audience": "staff", "back": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == f"/doc/{reg_t}"


def test_grants_without_eligible_accounts_shows_one_line(tmp_path):
    from tests.test_accounts import _app, _login
    from fastapi.testclient import TestClient
    c = TestClient(_app(tmp_path))
    assert _login(c, "zzdev", "devpass")
    t = c.get("/dev/rag").text
    assert 'id="rgGrantTable"' not in t and "추가 권한을 줄 계정이 없습니다" in t
