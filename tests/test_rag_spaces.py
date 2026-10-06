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
    # 부서 없는 교직원(전부)은 추가 권한으로 좁아지지 않는다
    assert rag_spaces.search_scope("staff", "", "kim", tmp_path)["grant_depts"] is None


def test_save_config_keeps_backup(tmp_path):
    rag_spaces.save_config({"grants": {}}, tmp_path)
    rag_spaces.save_config({"grants": {"a": ["dept:x"]}}, tmp_path)
    assert list(tmp_path.glob("rag_spaces.json.bak-*"))
    assert rag_spaces.config(tmp_path)["grants"] == {"a": ["dept:x"]}


def test_dept_of_rel(tmp_path):
    assert rag_spaces.dept_of_rel("링크/2024/계획.hwp", tmp_path) == "산학협력단"
    assert rag_spaces.dept_of_rel("기타/a.pdf", tmp_path) == "공통"


def test_access_filter_by_dept():
    sql, args = grant_search._access("u", ["LINC사업단", "공통"])
    assert "COALESCE(d.dept, '공통') IN (?,?)" in sql and args[-3:] == ["u", "LINC사업단", "공통"]
    assert grant_search._access(None, [])[0] == " AND 1 = 0"
    assert "d.dept" not in grant_search._access(None, None)[0]          # 부서로 자르지 않음(범위 밖 갈래 조건만)


def test_backfill_depts(tmp_path):
    db = Database(tmp_path / "t.db")
    a = db.add_document("a.hwp", "dgx://링크/2024/a.hwp", doc_type="grant", dept="공통", access_level="dept")
    b = db.add_document("b.hwp", "dgx://기타/b.hwp", doc_type="grant", dept="공통")
    got = rag_spaces.backfill_depts(db, tmp_path)
    assert got["산학협력단"] == 1
    da, dbb = db.get_document(a), db.get_document(b)
    assert da["dept"] == "산학협력단" and da["access_level"] == "public"
    assert dbb["dept"] == "공통"


def _client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                     password="boot-pass-1")
    c = TestClient(app)
    assert c.post("/login", data={"username": "zzdev", "pw": "devpass"}, follow_redirects=False).status_code == 303
    return c


def test_rag_page_and_edits(tmp_path):
    c = _client(tmp_path)
    r = c.get("/dev/rag")
    assert r.status_code == 200
    for text in ("공간 목록", "student", "staff", "dept:산학협력단", "원본 폴더 → 부서", "학생 공개 규정"):
        assert text in r.text, text
    r = c.post("/dev/rag/grant", data={"user": "zzaimy", "spaces": ["dept:앵커사업단", "bad"]}, follow_redirects=False)
    assert r.status_code == 303
    assert json.loads((tmp_path / "rag_spaces.json").read_text(encoding="utf-8"))["grants"] == {"zzaimy": ["dept:앵커사업단"]}
    assert c.post("/dev/rag/grant", data={"user": "ghost"}, follow_redirects=False).status_code == 404
    c.post("/dev/rag/area", data={"mapping": "링크=산학협력단\n새폴더 = 새부서\n잘못된줄"}, follow_redirects=False)
    assert rag_spaces.config(tmp_path)["dept_of_area"] == {"링크": "산학협력단", "새폴더": "새부서"}
    assert "dept:새부서" in c.get("/dev/rag").text
    assert c.post("/dev/rag/backfill", follow_redirects=False).status_code == 303
