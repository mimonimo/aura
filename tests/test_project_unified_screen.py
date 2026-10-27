"""프로젝트 화면 한 장(대화·문서) — 옛 「작성 원칙·기준 문서」 탭을 합친 화면, 끌어 놓기 자리, 문서함·드라이브에서 가져오기,
아이콘 색·사업 책임자, 참조(읽기 전용) 공유, 등급별 만들기·진행 현황."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from tests.test_webhwp import _hwpx, _login, _setup

from zzaimy.app import project_access as pa
from zzaimy.app import project_routes, webhwp


def _world(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)                  # kim(기획처) · lee(산학협력단)
    db, base = app.state.db, Path(app.state.data_dir)
    pid = db.create_project("grant", "가상 실증 사업", owner="kim")
    mine = webhwp.add(db, base, "기획처", "사업 계획 초안.hwpx", _hwpx("우리 부서"), owner="kim")
    other = webhwp.add(db, base, "산학협력단", "남의 부서 문서.hwpx", _hwpx("남의 것"), owner="lee")
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    return app, pid, kim, {"mine": mine, "other": other}


def _lee(app):
    lee = TestClient(app)
    assert _login(lee, "lee", "pass-12345678")
    return lee


def test_one_screen_with_documents_principles_and_drop_zone(tmp_path, monkeypatch):
    app, pid, kim, _ = _world(tmp_path, monkeypatch)
    page = kim.get(f"/project/{pid}").text
    tabs = page.split('class="project-tabs"', 1)[1].split("</div>", 1)[0]
    assert ">대화·문서</button>" in tabs and "작성 원칙·기준 문서" not in page
    assert f'href="/project/{pid}/activity"' in tabs and "진행 현황" in tabs      # 진행 현황은 다른 화면으로 가는 탭
    assert 'id="projectTabContext"' not in page
    # 기준 문서는 프로젝트 문서의 기준 묶음, 작성 원칙·지난 사업은 같은 오른쪽 열 아래
    side = page.split('class="project-side"', 1)[1].split('id="projectChatList"', 1)[0]
    assert 'data-doc-group="rules"' in side and 'id="projectContext"' in side and 'id="paneRefs"' in side
    assert 'aria-label="원칙 추가"' in side and "지난 사업 연결" in side
    # 끌어 놓기 자리와 「문서 추가」 메뉴 세 갈래, 공용 디렉터리 창
    assert '<section id="projectDocuments" aria-label="프로젝트 문서" data-drop-zone>' in page
    assert "여기에 놓으면 프로젝트 문서로 올립니다" in page
    menu = page.split('id="docAddMenu"', 1)[1].split("</div>", 1)[0]
    assert "내 컴퓨터에서 올리기" in menu and "문서함·구글 드라이브에서 가져오기" in menu and "문서함 문서를 기준으로 연결" in menu
    assert "data-finder-pick hidden" in menu                                      # 창 모듈이 없으면 숨긴 채로
    assert "/static/aura-finder.js" in page and "/static/aura-finder.css" in page
    assert 'data-modal-open="#linkModal"' in menu


def test_import_from_department_folder_goes_through_intake(tmp_path, monkeypatch):
    app, pid, kim, f = _world(tmp_path, monkeypatch)
    db = app.state.db
    r = kim.post(f"/project/{pid}/documents/import", json={"items": [{"source": "dept", "id": f["mine"], "name": "사업 계획 초안.hwpx"}]})
    assert r.status_code == 200, r.text
    assert r.json()["intake"] + r.json()["criteria"] == 1 and r.json()["rejected"] == []
    docs = db.list_documents(project_id=pid)
    assert len(docs) == 1 and docs[0]["filename"] == "사업 계획 초안.hwpx"
    stored = Path(docs[0]["stored_path"])
    assert stored.is_file() and stored != Path(webhwp.get(db, f["mine"])["path"])   # 원본 보관(adopt_original) — 문서함 파일은 그대로
    assert Path(webhwp.get(db, f["mine"])["path"]).is_file()
    assert db.list_files(kind="original", doc_id=docs[0]["id"]) or "documents" in str(stored)


def test_import_rejects_files_the_user_cannot_open_and_caps_the_batch(tmp_path, monkeypatch):
    app, pid, kim, f = _world(tmp_path, monkeypatch)
    db = app.state.db
    r = kim.post(f"/project/{pid}/documents/import", json={"items": [{"source": "dept", "id": f["other"], "name": "남의 부서 문서.hwpx"},
                                                                     {"source": "dept", "id": 99999, "name": "없는 문서"}]})
    assert r.status_code == 403 and len(r.json()["rejected"]) == 2
    assert db.list_documents(project_id=pid) == []
    r = kim.post(f"/project/{pid}/documents/import", json={"items": [{"source": "dept", "id": f["mine"]}, {"source": "dept", "id": f["other"]}]})
    assert r.status_code == 200 and len(r.json()["rejected"]) == 1 and len(db.list_documents(project_id=pid)) == 1
    many = [{"source": "dept", "id": f["mine"]}] * (project_routes.IMPORT_LIMIT + 1)
    assert kim.post(f"/project/{pid}/documents/import", json={"items": many}).status_code == 400
    assert kim.post(f"/project/{pid}/documents/import", json={"items": []}).status_code == 400
    bad = kim.post(f"/project/{pid}/documents/import", json={"items": [{"source": "drive", "drive_id": "../../etc"}]})
    assert bad.status_code == 403 and "올바르지" in bad.json()["rejected"][0]["why"]


def test_import_from_drive_uses_the_users_account(tmp_path, monkeypatch):
    app, pid, kim, _ = _world(tmp_path, monkeypatch)
    from zzaimy.ingest import gdrive_files
    monkeypatch.setattr(gdrive_files, "account_for", lambda db, user, dept=None: "kim@ync.ac.kr" if user == "kim" else "")
    seen = []
    def fetch(email, drive_id, http=None):
        seen.append((email, drive_id))
        return b"%PDF-1.4 drive", "드라이브 공고문.pdf"
    monkeypatch.setattr(project_routes, "_drive_fetch", fetch)
    r = kim.post(f"/project/{pid}/documents/import", json={"items": [{"source": "drive", "drive_id": "AbCdEf123456", "name": "드라이브 공고문"}]})
    assert r.status_code == 200 and seen == [("kim@ync.ac.kr", "AbCdEf123456")]
    assert [d["filename"] for d in app.state.db.list_documents(project_id=pid)] == ["드라이브 공고문.pdf"]


def test_bundle_upload_answers_json_for_the_progress_rows(tmp_path, monkeypatch):
    app, pid, kim, _ = _world(tmp_path, monkeypatch)
    r = kim.post(f"/project/{pid}/bundle", headers={"Accept": "application/json"},
                 files=[("file", ("모집 공고.pdf", b"%PDF", "application/pdf")), ("file", ("메모.exe", b"x", "application/octet-stream"))])
    assert r.status_code == 200 and r.json()["criteria"] + r.json()["intake"] == 1 and r.json()["skipped"] == ["메모.exe"]


def test_row_action_links_a_document_as_criterion(tmp_path, monkeypatch):
    app, pid, kim, _ = _world(tmp_path, monkeypatch)
    db = app.state.db
    doc = db.add_document(filename="참고 지침.pdf", stored_path=str(tmp_path / "x.pdf"), doc_type="grant", project_id=pid, owner="kim")
    page = kim.get(f"/project/{pid}").text
    attached = page.split('data-doc-group="attached"', 1)[1].split("</section>", 1)[0]
    assert 'action="/project/%d/criteria/link"' % pid in attached and "기준으로 쓰기" in attached
    assert kim.post(f"/project/{pid}/criteria/link", data={"criteria_doc_id": doc}, follow_redirects=False).status_code == 303
    rules = kim.get(f"/project/{pid}").text.split('id="paneCriteria"', 1)[1].split("</section>", 1)[0]
    assert f'href="/doc/{doc}"' in rules and "기준에서 빼기" in rules


def test_reference_share_is_read_only(tmp_path, monkeypatch):
    app, pid, kim, f = _world(tmp_path, monkeypatch)
    db = app.state.db
    pa.set_shares(db, pid, "shared", [{"to": "user:lee", "level": "view"}])
    lee = _lee(app)
    page = lee.get(f"/project/{pid}")
    assert page.status_code == 200 and 'class="pill project-share-chip"' in page.text and ">읽기만</span>" in page.text
    assert 'aria-label="프로젝트 문서" data-drop-zone' not in page.text and 'name="question"' not in page.text and 'id="noteInput"' not in page.text
    # 쓰기는 서버에서 거절 — 원칙·문서 추가·가져오기·대화 시작
    assert lee.post(f"/project/{pid}/notes", data={"content": "몰래"}, follow_redirects=False).status_code in (403, 404)
    assert lee.post(f"/project/{pid}/bundle", files={"file": ("a.pdf", b"%PDF", "application/pdf")}, follow_redirects=False).status_code in (403, 404)
    assert lee.post(f"/project/{pid}/documents/import", json={"items": [{"source": "dept", "id": f["other"]}]}).status_code in (403, 404)
    assert lee.post("/chat/send", data={"question": "질문", "project_id": str(pid)}, follow_redirects=False).status_code in (403, 404)
    assert db.list_project_notes(pid) == [] and db.list_documents(project_id=pid) == []
    # 권한 판단 — 읽기만은 쓰기 못 함, 함께 작업·소유는 쓴다
    assert pa.can_write("view") is False and pa.can_write("edit") is True
    assert pa.LEVEL_LABEL == {"view": "읽기만", "edit": "함께 작업"} and pa.DEFAULT_LEVEL == "view"


def test_write_guard_answers_403_for_reference_users(tmp_path, monkeypatch):
    """프로젝트 경로 앞단 검사를 지나 들어온 읽기만 사용자(라우트 안 검사)는 403."""
    app, pid, kim, _ = _world(tmp_path, monkeypatch)
    from fastapi import HTTPException
    from types import SimpleNamespace
    proj = app.state.db.get_project(pid)
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(project_access=lambda r, p: "view")))
    try:
        project_routes._require_write(req, proj)
        raise AssertionError("참조 사용자의 쓰기가 통과했다")
    except HTTPException as e:
        assert e.status_code == 403
    req.app.state.project_access = lambda r, p: "edit"
    project_routes._require_write(req, proj)


def test_icon_color_and_project_lead(tmp_path, monkeypatch):
    app, pid, kim, _ = _world(tmp_path, monkeypatch)
    db = app.state.db
    r = kim.post(f"/project/{pid}/color", data={"color": "teal"}, headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.json() == {"color": "teal", "hex": "#0F766E", "bg": "#E3F4F1"}
    assert db.get_setting(f"project_color:{pid}") == "teal" and pa.color(db, pid) == "#0F766E"
    assert kim.post(f"/project/{pid}/color", data={"color": "#123456"}).status_code == 400
    page = kim.get(f"/project/{pid}").text
    assert "--pc:#0F766E" in page and 'value="teal" class="project-swatch" style="--sw:#0F766E;"\n        aria-label="청록" title="청록" aria-pressed="true"' in page
    assert app.state.templates.env.globals["project_color"](pid) == "#0F766E"   # 목록·사이드바가 같은 색으로
    # 사업 책임자 — 기본은 만든 사람, 「함께 작업」으로 받는 사람 가운데서 고른다
    assert "책임 kim" in page
    assert kim.post(f"/project/{pid}/lead", data={"user": "lee"}, follow_redirects=False).status_code == 400
    pa.set_shares(db, pid, "shared", [{"to": "user:lee", "level": "edit"}])
    assert kim.post(f"/project/{pid}/lead", data={"user": "lee"}, follow_redirects=False).status_code == 303
    assert db.get_setting(f"project_lead:{pid}") == "lee" and pa.lead(db, db.get_project(pid)) == "lee"
    assert "책임 lee" in kim.get(f"/project/{pid}").text
    lee = _lee(app)
    assert lee.post(f"/project/{pid}/color", data={"color": "red"}, follow_redirects=False).status_code in (403, 404)


def test_grades_gate_project_creation_and_activity_tab(tmp_path, monkeypatch):
    app, pid, kim, _ = _world(tmp_path, monkeypatch)
    assert 'id="projectTabActivity"' in kim.get(f"/project/{pid}").text            # 등급 없는 교직원 = 사업 책임자, 만든 사람
    app.state.accounts["kim"]["grade"] = "member"
    r = kim.post("/projects", data={"sector": "grant", "name": "일반 등급 프로젝트"}, follow_redirects=False)
    assert r.status_code == 403
    assert kim.post("/projects/bundle", data={"sector": "grant", "name": "묶음"}, follow_redirects=False).status_code == 403
    assert 'id="projectTabActivity"' not in kim.get(f"/project/{pid}").text
    app.state.accounts["kim"]["grade"] = "lead"
    assert kim.post("/projects", data={"sector": "grant", "name": "책임 등급 프로젝트"}, follow_redirects=False).status_code == 303
