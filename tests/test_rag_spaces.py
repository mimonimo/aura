"""RAG 공간(ADR-0053) — 역할·부서 → 공간, 부서 RAG 열어 주기(부서 단위), 부서 다시 붙이기, 관리 화면."""
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


def test_dept_grant_opens_scope_only_after_grant_and_never_to_students(tmp_path):
    """부서 RAG 는 부서 기반 — 앵커사업단이 LINC사업단에 열어 줘야 LINC사업단 교직원이 앵커사업단 사업 문서·규정까지 찾는다."""
    before = rag_spaces.search_scope("staff", "LINC사업단", "kim", tmp_path)
    assert before["grant_depts"] == ["LINC사업단", "공통"] and "reg_depts" not in before
    rag_spaces.save_config({"dept_grants": {"앵커사업단": ["LINC사업단"]}}, tmp_path)
    sc = rag_spaces.search_scope("staff", "LINC사업단", "kim", tmp_path)
    assert sc["grant_depts"] == ["LINC사업단", "공통", "앵커사업단"] and sc["space"] == "dept:LINC사업단+dept:앵커사업단"
    assert sc["reg_depts"] == ["LINC사업단", "앵커사업단"]                       # 규정도 같은 범위
    # 받는 쪽만 — 앵커사업단 사람은 LINC사업단 RAG 를 받지 않고, 다른 부서·부서 없는 교직원도 그대로
    assert rag_spaces.search_scope("staff", "앵커사업단", "lee", tmp_path)["grant_depts"] == ["앵커사업단", "공통"]
    assert rag_spaces.search_scope("staff", "기획처", "park", tmp_path)["grant_depts"] == ["기획처", "공통"]
    assert rag_spaces.search_scope("staff", "", "kim", tmp_path)["grant_depts"] == ["공통"]
    # 학생은 부서가 같아도 받지 않는다
    st = rag_spaces.search_scope("student", "LINC사업단", "stu", tmp_path)
    assert st["grant"] is False and st["levels"] == ("student",)
    # 거두면 바로 빠진다
    rag_spaces.save_config(rag_spaces.set_receivers(rag_spaces.config(tmp_path), "앵커사업단", []), tmp_path)
    assert rag_spaces.config(tmp_path)["dept_grants"] == {}
    assert rag_spaces.search_scope("staff", "LINC사업단", "kim", tmp_path)["grant_depts"] == ["LINC사업단", "공통"]


def test_dept_grant_respects_min_grade(tmp_path):
    """열어 받은 부서 RAG 에도 그 RAG 의 최소 등급이 그대로 걸린다(ADR-0066)."""
    rag_spaces.save_config({"dept_grants": {"앵커사업단": ["LINC사업단"]}, "min_grade": {"dept:앵커사업단": "head"}}, tmp_path)
    low = rag_spaces.search_scope("staff", "LINC사업단", "kim", tmp_path, grade="member")
    assert low["grant_depts"] == ["LINC사업단", "공통"] and "reg_depts" not in low
    high = rag_spaces.search_scope("staff", "LINC사업단", "kim", tmp_path, grade="head")
    assert "앵커사업단" in high["grant_depts"]


def test_old_account_grants_key_is_ignored(tmp_path):
    """예전 계정별 추가 권한(grants)은 읽을 때 버리고 저장할 때도 남지 않는다 — 검색 범위를 넓히지 않는다."""
    (tmp_path / "rag_spaces.json").write_text(json.dumps({"grants": {"kim": ["dept:앵커사업단"]}}), encoding="utf-8")
    assert "grants" not in rag_spaces.config(tmp_path)
    assert rag_spaces.search_scope("staff", "LINC사업단", "kim", tmp_path)["grant_depts"] == ["LINC사업단", "공통"]
    rag_spaces.save_config({**rag_spaces.config(tmp_path), "grants": {"kim": ["dept:x"]}}, tmp_path)
    assert "grants" not in json.loads((tmp_path / "rag_spaces.json").read_text(encoding="utf-8"))


def test_dept_grants_cleaned_on_delete_and_rename(tmp_path):
    db = Database(tmp_path / "t.db")
    cfg = {**rag_spaces.config(tmp_path), "dept_rags": [{"dept": "가"}, {"dept": "나"}, {"dept": "다"}],
           "dept_grants": {"가": ["나", "다"], "나": ["가"]}}
    new, _ = rag_spaces.rename_dept_rag(db, cfg, "나", "나2")              # 주는 쪽·받는 쪽 이름 모두 따라간다
    assert new["dept_grants"] == {"가": ["나2", "다"], "나2": ["가"]}
    gone, _ = rag_spaces.delete_dept_rag(db, new, "가")                     # 주는 쪽 열쇠도, 받는 쪽 목록에서도 빠진다
    assert gone["dept_grants"] == {}
    gone2, _ = rag_spaces.delete_dept_rag(db, new, "다")
    assert gone2["dept_grants"] == {"가": ["나2"], "나2": ["가"]}


def test_save_config_keeps_backup(tmp_path):
    rag_spaces.save_config({"dept_grants": {}}, tmp_path)
    rag_spaces.save_config({"dept_grants": {"a": ["b"]}}, tmp_path)
    assert list(tmp_path.glob("rag_spaces.json.bak-*"))
    assert rag_spaces.config(tmp_path)["dept_grants"] == {"a": ["b"]}


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


def test_rag_page_sections(tmp_path):
    c = _client(tmp_path)
    r = c.get("/dev/rag")
    assert r.status_code == 200
    for text in ("RAG 목록", "공통 RAG", "학생 RAG", "기준 문서·조각", "검색 범위 시험", "dept:가부서"):
        assert text in r.text, text
    # 부서 RAG 는 문서 있는 것만 기본으로 보이고, 빈 부서는 한 줄 요약에서 펼친다 — 줄마다 같은 단추 없음
    assert "문서 없는 부서" in r.text and 'data-comp=' not in r.text and "구성 보기" not in r.text
    assert "합계 사업 문서" not in r.text                                      # 표 아래 합계 문장 없음
    # 학생 공간이 비면 그 줄에 안내와 지정 링크(학생 공개 지정은 라이브러리 「기준」 칩)
    assert "학생 공개 규정 없음 — 학생 계정은 찾을 문서가 없습니다" in r.text and 'href="/?type=all&amp;use=criteria"' in r.text
    # 계정별 추가 권한(겸직 부서)은 사용자·계정 편집 창 한 곳 — 여기는 한 줄 안내(10-26)
    assert 'id="rgGrantTable"' not in r.text and 'href="/team"' in r.text
    assert 'id="student-regs"' not in r.text and "/dev/rag/regs" not in r.text     # 규정 관리는 라이브러리로 옮겼다(10-27)

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
    """폴더별 담당 부서는 라이브러리 폴더의 「공개 범위 바꾸기」 한 곳 — 옛 문서 반입 탭 넘기기는 10-25 에 지웠다."""
    c = _client(tmp_path)
    assert c.get("/dev/intake?tab=areas", follow_redirects=False).status_code == 404   # 옛 주소는 없앴다(10-27)
    assert "폴더별 담당 부서" not in c.get("/dev/flow").text


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
    rag_spaces.save_config({"dept_grants": {"나부서": ["가부서"]}}, tmp_path)
    assert docs("user:u-ga") == {ga, na}                   # 나부서가 가부서에 열어 주면 실제 검색과 같이 넓어진다
    assert docs("user:u-na") == {na}                       # 주는 쪽은 그대로
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
    assert "data-stu-empty hidden" in c.get("/dev/rag").text           # 학생 공개 규정이 있으면 빈 안내는 숨김(규정을 바꾸면 화면이 다시 센다)


def test_student_audience_set_and_unset_from_library_criteria(tmp_path, monkeypatch):
    c = _client(tmp_path)
    db, ga, na, reg_s, reg_t = _search_fixture(tmp_path, monkeypatch)
    page = c.get("/?type=all&use=criteria").text
    assert 'data-crit="student"' in page and "이 쪽 모두" in page            # 라이브러리 기준 칩 — 체크 뒤 작업 줄
    regs = {d["id"]: d for d in c.get("/criteria/regs").json()["registered"]}
    assert reg_t in regs and ga not in regs
    assert c.post("/criteria/regs/audience", json={"ids": [reg_t, ga], "audience": "student"}).json()["changed"] == 1
    assert db.get_document(reg_t)["audience"] == "student" and db.get_document(ga).get("audience") != "student"
    c.post("/criteria/regs/audience", json={"ids": [reg_t], "audience": "staff"})
    back = "/?type=all&use=criteria"
    r = c.post(f"/doc/{reg_t}/audience", data={"audience": "student", "back": back}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == back
    assert db.get_document(reg_t)["audience"] == "student"
    c.post(f"/doc/{reg_s}/audience", data={"audience": "staff", "back": back})
    assert db.get_document(reg_s)["audience"] == "staff"
    # 사업 문서는 규정 갈래여도 학생 공개가 되지 않는다(기존 규칙 그대로)
    assert c.post(f"/doc/{ga}/audience", data={"audience": "student", "back": "/dev/rag"}).status_code == 400
    # 바깥 주소로는 돌려보내지 않는다
    r = c.post(f"/doc/{reg_t}/audience", data={"audience": "staff", "back": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == f"/doc/{reg_t}"




def test_rag_page_counts_are_live_and_detail_endpoints(tmp_path, monkeypatch):
    """화면 수치는 요청 때 센다 — 문서를 더하면 다시 열 때 바로 바뀐다. 규정 상세·최소 등급 제자리 저장(JSON)."""
    c = _client(tmp_path)
    db, ga, na, reg_s, reg_t = _search_fixture(tmp_path, monkeypatch)
    rows = lambda: json.loads(c.get("/dev/rag").text.split('id="rgRows">', 1)[1].split("</script>", 1)[0])  # noqa: E731
    first = {r["sid"]: r for r in rows()}
    assert first["dept:가부서"]["docs"] == 1 and first["student"]["reg_docs"] == 1
    extra = db.add_document("가부서 결과.hwp", "x", doc_type="grant", dept="가부서", access_level="public")
    db.update_document(extra, status="reviewed")
    assert {r["sid"]: r for r in rows()}["dept:가부서"]["docs"] == 2
    c.post("/criteria/regs/audience", json={"ids": [reg_t], "audience": "student"})
    assert {r["sid"]: r for r in rows()}["student"]["reg_docs"] == 2
    d = c.get(f"/criteria/regs/{reg_t}").json()
    assert d["registered"] is True and d["chunks"] == 2 and d["audience"] == "student"
    assert c.get("/criteria/regs/999999").status_code == 404
    got = c.post("/dev/rag/min-grade", data={"space": "dept:가부서", "grade": "lead"}, headers={"Accept": "application/json"}).json()
    assert got["grade"] == "lead" and got["label"].endswith("이상")
    assert c.post("/dev/rag/min-grade", data={"space": "dept:없음", "grade": "lead"}, headers={"Accept": "application/json"}).status_code == 400
    assert c.get("/dev/rag/try", params={"target": "space:dept:가부서", "q": "장비"}).json()["label"] == "가부서 부서 RAG"
