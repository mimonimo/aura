"""문서 검토 화면의 길 안내 — 돌아갈 곳(프로젝트·들어온 곳)과 학교 원본 짧은 위치. 합성 사례로만."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import doc_origin
from zzaimy.app.main import create_app


def test_back_link_prefers_active_project_then_origin():
    doc = {"doc_type": "grant", "stored_path": "/x/a.pdf"}
    live = {"id": 7, "name": "가상 진행 사업", "archived": 0}
    assert doc_origin.back_link(doc, live, "archive")["href"] == "/project/7"
    got = doc_origin.back_link(doc, None, "", "http://h/dev/archive?program=p%3Ax&q=a", "h")
    assert got == {"label": "원본 관리로", "href": "/dev/archive?program=p%3Ax&q=a", "title": ""}
    assert doc_origin.back_link(doc, None, "search")["label"] == "검색으로"
    assert doc_origin.back_link(doc, None, "", "http://h/?type=grant&q=a", "h")["href"] == "/?type=grant&q=a"
    assert doc_origin.back_link(doc, None, "", "http://evil/archive", "h")["label"] == "라이브러리로"   # 다른 사이트
    assert doc_origin.back_link(doc, None, "", "http://h/doc/3", "h")["label"] == "라이브러리로"        # 모르는 화면
    archived = {"id": 9, "name": "보관 묶음", "archived": 1}
    assert doc_origin.back_link(doc, archived)["label"] == "지난 사업으로"
    assert doc_origin.back_link(doc, archived, "graph")["label"] == "그래프 탐색으로"
    assert doc_origin.back_link({"doc_type": "regulation"}, None)["href"] == "/?type=all&use=criteria"
    assert doc_origin.back_link({"doc_type": "grant", "stored_path": "dgx://가/나.pdf"}, None)["href"] == "/?type=all"   # 학교 원본 문서도 라이브러리로


def test_archive_location_is_short_and_links_to_archive():
    loc = doc_origin.archive_location("갑/을/병/2024 결과보고서.hwp",
                                      {"program": "program:x", "program_name": "가상 해양 사업", "year": 2024})
    assert loc["label"] == "학교 원본 · 가상 해양 사업 › 2024년"
    assert loc["href"].startswith("/dev/archive?program=program%3Ax&year=2024&q=") and loc["path"].startswith("갑/을/")
    bare = doc_origin.archive_location("갑/을/병/파일.pdf", {"program": "", "program_name": "", "year": 1968})
    assert bare["label"] == "학교 원본 · 을 › 병" and "program=" not in bare["href"]


def test_doc_page_shows_short_origin_and_back_link(tmp_path):
    from zzaimy.app import archive

    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter())
    c = TestClient(app)
    db = app.state.db
    rel = "가상영역/가상 사업/2025/가상 결과 보고서.hwp"
    did = db.add_document("가상 결과 보고서.hwp", "dgx://" + rel, doc_type="grant")
    archive.load(db, [{"rel": rel, "size": 4, "mtime": 1, "program": "program:v", "program_name": "가상 사업", "year": 2025}])
    page = c.get(f"/doc/{did}", headers={"referer": "http://testserver/dev/archive?q=x"}).text
    assert "학교 원본 · 가상 사업 › 2025년" in page and "원본 관리에서 보기" in page and "경로 복사" in page and 'title="' + rel + '"' in page
    assert "원본 위치:" not in page
    assert '<a href="/dev/archive?q=x" class="btn-ghost">← 원본 관리로</a>' in page
    assert "← 검색으로" in c.get(f"/doc/{did}?from=search").text
    assert 'class="doc-actions control-row"' in page


def test_staff_sees_origin_place_without_admin_link(tmp_path):
    """교직원은 학교 원본 위치(사업 › 연도·경로 복사)만 — 원본 관리로 가는 단추는 관리자에게만, 원본은 「원본 열기」로."""
    from fastapi.testclient import TestClient
    from tests.test_accounts import _app, _login
    from zzaimy.app import archive
    app = _app(tmp_path)
    db = app.state.db
    rel = "가상영역/가상 사업/2025/가상 결과 보고서.hwp"
    did = db.add_document("가상 결과 보고서.hwp", "dgx://" + rel, doc_type="grant")
    archive.load(db, [{"rel": rel, "size": 4, "mtime": 1, "program": "program:v", "program_name": "가상 사업", "year": 2025}])
    c = TestClient(app)
    assert _login(c, "zzaimy", "boot-pass-1")
    page = c.get(f"/doc/{did}").text
    assert "학교 원본 · 가상 사업 › 2025년" in page and "원본 관리에서 보기" not in page and "/dev/archive" not in page
    assert f'href="/doc/{did}/original"' in page
