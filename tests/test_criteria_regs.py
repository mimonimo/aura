"""기준 문서 관리 — 라이브러리 「기준」 칩에서 관리자만 학생 공개·검색에서 빼기·새 판 등록(/criteria/regs*). 교직원 화면은 그대로."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app


def _app(tmp_path):
    (tmp_path / "accounts.json").write_text(json.dumps({
        "zzaimy": {"pw": "staffpass", "role": "staff"},
        "zzdev": {"pw": "devpass", "role": "dev"},
    }))
    return create_app(db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                      password="boot-pass-1")


def _login(app, uid, pw):
    c = TestClient(app)
    assert c.post("/login", data={"username": uid, "pw": pw}, follow_redirects=False).status_code == 303
    return c


def _seed(db):
    stu = db.add_document("학사 안내 규정.hwp", "x", doc_type="regulation")
    staff = db.add_document("내부 심사 규정.hwp", "x", doc_type="regulation")
    cand = db.add_document("연구비 관리 규정.hwp", "x", doc_type="grant")
    for d in (stu, staff, cand):
        db.update_document(d, status="reviewed")
    db.set_document_audience(stu, "student")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET masked_text = ? WHERE id = ?",
                     ("제1조(목적) 이 규정은 연구비 관리에 필요한 사항을 정한다. 제2조(적용) 이 규정은 교내 연구 과제에 적용한다. " * 3, cand))
    return stu, staff, cand


def test_admin_manages_criteria_in_library(tmp_path):
    app = _app(tmp_path)
    db = app.state.db
    stu, staff, cand = _seed(db)
    c = _login(app, "zzdev", "devpass")
    page = c.get("/?type=all&use=criteria").text
    for mark in ('data-crit="student"', 'data-crit="staff"', 'data-crit="unregister"', 'data-crit="register"', 'id="critCandModal"',
                 'name="aud"', "data-crit-menu"):
        assert mark in page, mark
    assert "학생 공개 1" in page and "교직원만 1" in page                    # 공개 대상 거르기의 수는 지금 값
    only = c.get("/?type=all&use=criteria&aud=student").text
    assert "학사 안내 규정" in only and "내부 심사 규정" not in only
    got = c.get("/criteria/regs").json()
    assert {d["id"] for d in got["registered"]} == {stu, staff} and "items" in got
    assert c.post("/criteria/regs/audience", json={"ids": [staff, cand], "audience": "student"}).json()["changed"] == 1
    assert db.get_document(staff)["audience"] == "student" and db.get_document(cand).get("audience") != "student"
    assert c.get(f"/criteria/regs/{staff}").json()["registered"] is True
    assert c.get("/criteria/regs/999999").status_code == 404
    r = c.post("/criteria/regs/unregister", json={"ids": [staff]}).json()
    assert r.get("unregistered", 0) + r.get("kept", 0) == 1
    # 옛 RAG 공간 경로·자리는 없다
    assert c.get("/dev/rag/regs").status_code == 404
    rag = c.get("/dev/rag").text
    assert 'id="student-regs"' not in rag and "/dev/rag/regs" not in rag and "규정·지침</h2>" not in rag


def test_staff_sees_criteria_but_cannot_manage(tmp_path):
    app = _app(tmp_path)
    db = app.state.db
    stu, staff, cand = _seed(db)
    c = _login(app, "zzaimy", "staffpass")
    page = c.get("/?type=all&use=criteria").text
    assert "학사 안내 규정" in page and "내부 심사 규정" in page                 # 기준 문서는 지금처럼 보인다
    assert 'data-crit="student"' not in page and 'data-crit="register"' not in page and 'id="critCandModal"' not in page and 'name="aud"' not in page
    assert c.get("/criteria/regs").status_code == 403
    assert c.get(f"/criteria/regs/{stu}").status_code == 403
    for path, body in (("/criteria/regs/audience", {"ids": [staff], "audience": "student"}),
                       ("/criteria/regs/unregister", {"ids": [stu]}), ("/criteria/regs/register", {"ids": [cand]})):
        assert c.post(path, json=body).status_code == 403, path
    assert db.get_document(staff).get("audience") != "student" and db.get_document(stu)["doc_type"] == "regulation"
    assert db.get_document(cand)["doc_type"] == "grant"
