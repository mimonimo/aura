"""등급·공유 빈틈 — 일반 등급은 대화에 문서 세트를 던져도 프로젝트를 만들지 않는다(첨부로만), 에이전트 동작도 같은 판정,
「함께 작업」 공유 대상의 내 컴퓨터 파일 올리기, 대화 지우기 때 딸린 설정 키, 공용 파일 창의 저장 모드."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_accounts import _login
from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app import chat_routes, platform_ops
from zzaimy.app import project_access as pa
from zzaimy.app.main import create_app

APP = Path(__file__).resolve().parents[1] / "src/zzaimy/app"


def _app(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), responder=FakeResponder(), password="boot-pass-1")
    for uid, dept, grade in (("kim", "기획처", None), ("lee", "산학협력단", "member"), ("park", "산학협력단", None)):
        a = app.state.new_account(uid, "pass-word-1", "staff", uid.upper())
        a["dept"] = dept
        if grade:
            a["grade"] = grade
        app.state.accounts[uid] = a
    app.state.save_accounts()
    return app


def _client(app, uid):
    c = TestClient(app)
    assert _login(c, uid, "pass-word-1")
    return c


def _two():
    return [("attachment", ("2026 지원사업 기본계획.pdf", b"%PDF-1.4 a", "application/pdf")),
            ("attachment", ("사업계획서 양식.pdf", b"%PDF-1.4 b", "application/pdf"))]


def test_member_bundle_in_chat_stays_attachment(tmp_path):
    app = _app(tmp_path)
    db = app.state.db
    lee = _client(app, "lee")
    r = lee.post("/chat/send", data={"question": "검토해 줘"}, files=_two(), follow_redirects=False)
    assert r.status_code == 303
    sid = int(r.headers["location"].rsplit("/", 1)[-1])
    assert db.get_chat_session(sid)["project_id"] is None
    assert not [p for p in db.list_all_projects() if p.get("owner") == "lee"]
    msgs = db.list_chats(sid)
    assert msgs[0]["role"] == "assistant" and msgs[0]["content"].startswith(chat_routes.PROJECT_GRADE_NOTE)
    user = [m for m in msgs if m["role"] == "user"][0]
    assert user["content"].count("[첨부#") == 2 and user["content"].endswith("검토해 줘")
    # 사업 담당자(등급 없는 기존 교직원)은 그대로 프로젝트가 생긴다
    kim = _client(app, "kim")
    r2 = kim.post("/chat/send", data={"question": "검토해 줘"}, files=_two(), follow_redirects=False)
    assert db.get_chat_session(int(r2.headers["location"].rsplit("/", 1)[-1]))["project_id"] is not None


def test_agent_project_ops_follow_grade(tmp_path):
    app = _app(tmp_path)
    db = app.state.db
    h = app.state.chat
    assert h._may_create_project("kim", "staff") and not h._may_create_project("lee", "staff")
    assert h._may_create_project("zzdev", "dev")
    ctx = h._ops_ctx("lee", "staff")
    assert ctx.may_create_project is False
    with pytest.raises(platform_ops.OpRefused, match="이상이 만듭니다"):
        platform_ops._project_check(ctx, {"sector": "grant", "name": "새 사업", "first_request": "시작하자"})
    assert h._ops_ctx("kim", "staff").may_create_project is True
    # 「이 공고로 사업 준비 시작」 자동 동작도 같은 판정 — 일반 등급이면 만들지 않고 안내 한 줄
    doc = db.add_document(filename="공고.pdf", stored_path=str(tmp_path / "x.pdf"), doc_type="grant", owner="lee")
    before = len(db.list_all_projects())
    assert h._run_action("plan.start", {"doc_id": doc, "owner": "lee", "may_create_project": False}) == chat_routes.PROJECT_GRADE_NOTE
    assert len(db.list_all_projects()) == before


def test_shared_edit_can_upload_bundle_view_cannot(tmp_path):
    app = _app(tmp_path)
    db = app.state.db
    pid = db.create_project("grant", "가상 실증 사업", owner="kim")
    files = {"file": ("붙임 자료.pdf", b"%PDF-1.4 x", "application/pdf")}
    park = _client(app, "park")
    assert park.post(f"/project/{pid}/bundle", files=files, follow_redirects=False).status_code == 404   # 공유 안 받음
    pa.set_shares(db, pid, "shared", [{"to": "user:park", "level": "view"}])
    assert park.post(f"/project/{pid}/bundle", files=files, follow_redirects=False).status_code in (403, 404)   # 참조(앞단 검사가 404)
    assert db.list_documents(project_id=pid) == []
    pa.set_shares(db, pid, "shared", [{"to": "user:park", "level": "edit"}])
    r = park.post(f"/project/{pid}/bundle", files=files, headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.json()["criteria"] + r.json()["intake"] == 1
    docs = db.list_documents(project_id=pid)
    assert len(docs) == 1 and docs[0]["owner"] == "park"          # 문서함·드라이브 가져오기(/documents/import)와 같이 올린 사람
    kim = _client(app, "kim")
    assert kim.post(f"/project/{pid}/bundle", files=files, follow_redirects=False).status_code == 303


def test_delete_chat_session_removes_its_settings(tmp_path):
    app = _app(tmp_path)
    db = app.state.db
    sid = db.create_chat_session(title="t", owner="kim")
    other = db.create_chat_session(title="u", owner="kim")
    db.add_chat(sid, "user", "질문")
    mid = db.add_chat(sid, "assistant", "답")
    omid = db.add_chat(other, "assistant", "다른 답")
    for k in (f"chat_ctx_msg:{mid}", f"chat_cot:{mid}", f"chat_ctx:{sid}", f"chat_summary:{sid}",
              f"chat_ctx_msg:{omid}", f"chat_cot:{omid}", f"chat_ctx:{other}", f"chat_summary:{other}"):
        db.set_setting(k, "{}")
    assert db.delete_chat_session(sid)
    left = db.all_settings()
    assert not any(k in left for k in (f"chat_ctx_msg:{mid}", f"chat_cot:{mid}", f"chat_ctx:{sid}", f"chat_summary:{sid}"))
    assert all(k in left for k in (f"chat_ctx_msg:{omid}", f"chat_cot:{omid}", f"chat_ctx:{other}", f"chat_summary:{other}"))
    db.delete_setting(f"chat_cot:{omid}")
    assert db.get_setting(f"chat_cot:{omid}", "없음") == "없음"


def test_finder_save_mode_is_the_only_save_dialog():
    js = (APP / "static/aura-finder.js").read_text(encoding="utf-8")
    assert "window.AuraFinder={open,save}" in js and "'/hwp/folder'" in js and "새 폴더" in js
    assert "학교 구글 계정을 연결하면 드라이브에 저장할 수 있습니다" in js
    edit = (APP / "templates/hwp_edit.html").read_text(encoding="utf-8")
    assert "AuraFinder.save(" in edit and '<dialog class="sa"' not in edit and "data-sa-" not in edit
    assert "/hwp/save-as?dept=" in edit and "/hwp/save-as/drive?name=" in edit and "&keep=1" in edit
    assert "새 문서 만들기" in edit and "드라이브에 만들면 작업본은 " in edit and "/static/aura-finder.js" in edit
    box = (APP / "static/hwp-box.js").read_text(encoding="utf-8")
    assert "AuraFinder.open(" in box and "드라이브에서 가져오기" in box and "start: 'drive'" in box
    node = shutil.which("node")
    if node:
        for f in ("static/aura-finder.js", "static/hwp-box.js"):
            subprocess.run([node, "--check", str(APP / f)], check=True)
