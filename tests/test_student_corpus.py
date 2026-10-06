"""학생 계정은 학생 공개로 지정한 학사 규정만 — 검색·대화·화면·문서 경로 어디로도 교직원 자료가 새지 않는다(ADR-0052)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import access_guard, access_policy
from zzaimy.app.db import Database
from zzaimy.app.main import create_app


def _setup(tmp_path):
    (tmp_path / "accounts.json").write_text(json.dumps({
        "s1": {"pw": "stu-pass", "role": "student"},
        "kim": {"pw": "kim-pass", "role": "staff", "dept": "학생처"},
    }))
    db = Database(tmp_path / "t.db")
    src = tmp_path / "x.txt"
    src.write_text("본문")
    hak = db.add_document("학칙.hwp", str(src), doc_type="regulation", owner="kim")       # 학생 공개 지정할 학사 규정
    db.update_document(hak, status="reviewed", masked_text="휴학은 2학기까지")
    db.set_document_audience(hak, "student")
    staff_reg = db.add_document("교원 인사 규정.hwp", str(src), doc_type="regulation", owner="kim")   # 공개 등급이지만 교직원용
    db.update_document(staff_reg, status="reviewed", masked_text="교원 승진 심사")
    grant = db.add_document("LINC 계획서.hwp", str(src), doc_type="grant", owner="kim")
    db.update_document(grant, status="reviewed", masked_text="사업비")
    with db._conn() as conn:
        for did, text in ((hak, "휴학은 2학기까지 할 수 있다"), (staff_reg, "교원 승진 심사 기준")):
            conn.execute("INSERT INTO regulation_chunks (doc_id, reg_title, heading, content, sector, dept, access_level)"
                         " VALUES (?, '규정', '제1조', ?, 'common', '공통', 'public')", (did, text))
    return db, hak, staff_reg, grant


def _login(tmp_path, uid, pw) -> TestClient:
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), password="boot-pass-1")
    c = TestClient(app)
    assert c.post("/login", data={"username": uid, "pw": pw}, follow_redirects=False).status_code == 303
    return c


def test_student_search_scope_sees_only_student_regulations(tmp_path):
    db, hak, staff_reg, _ = _setup(tmp_path)
    scope = access_guard.search_scope(None, "student")
    chunks = db.list_regulation_chunks(levels=scope["levels"])
    assert {c["doc_id"] for c in chunks} == {hak}
    assert {c["doc_id"] for c in db.list_regulation_chunks(dept="학생처", user="kim")} >= {hak, staff_reg}   # 교직원은 둘 다
    assert access_policy.visible(db.get_document(hak), dept=None, user=None, role="student")
    assert not access_policy.visible(db.get_document(staff_reg), dept=None, user=None, role="student")


def test_student_answer_never_calls_grant_search(monkeypatch):
    from zzaimy.app import grant_search, regulations, responder
    from zzaimy.generate import client
    monkeypatch.setattr(regulations, "find_relevant", lambda db, q, **kw: [])
    monkeypatch.setattr(grant_search, "search", lambda *a, **k: pytest.fail("학생 질문이 사업 문서를 검색했다"))
    fake = SimpleNamespace(model="m", client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **k: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="답"))])))))
    monkeypatch.setattr(client, "VllmClient", lambda **kw: fake)
    responder.AgentResponder().answer(SimpleNamespace(all_settings=lambda: {}), "휴학 기간은?",
                                      scope=access_guard.search_scope(None, "student"))


def test_student_routes_are_allowlisted(tmp_path):
    db, hak, staff_reg, grant = _setup(tmp_path)
    s = _login(tmp_path, "s1", "stu-pass")
    assert s.get("/", follow_redirects=False).headers["location"] == "/chat"
    assert s.get("/chat").status_code == 200
    assert s.get(f"/doc/{hak}").status_code == 200                                  # 학생 공개 학사 규정
    for path in (f"/doc/{staff_reg}", f"/doc/{grant}", f"/doc/{grant}/original"):
        assert s.get(path).status_code in (403, 404), path
    for path in ("/archive", "/graph", "/projects/archived", "/criteria", "/connections", "/dev", "/dev/intake", "/settings",
                 "/?type=all", "/api/projects/browse"):
        assert s.get(path, follow_redirects=False).status_code in (303, 403), path
    assert s.post(f"/doc/{hak}/audience", data={"audience": "staff"}).status_code == 403
    assert s.post(f"/doc/{hak}/delete").status_code == 403
    assert db.get_document(hak)["audience"] == "student"


def test_only_regulations_can_be_student_audience(tmp_path):
    db, hak, staff_reg, grant = _setup(tmp_path)
    kim = _login(tmp_path, "kim", "kim-pass")
    assert kim.post(f"/doc/{grant}/audience", data={"audience": "student"}).status_code == 400
    assert kim.post(f"/doc/{staff_reg}/audience", data={"audience": "student"}, follow_redirects=False).status_code == 303
    assert db.get_document(staff_reg)["audience"] == "student"
