"""대화 첨부는 문서가 된다 — 대화에서 바로 열리고 프로젝트 문서 목록에 선다."""

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app.main import create_app


def test_chat_attachment_becomes_a_document_linked_from_the_turn(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    c = TestClient(app)
    db = app.state.db
    pid = db.create_project("grant", "RISE 2027", owner="zzaimy")
    r = c.post("/chat/send", data={"question": "이 계획서 검토해 줘", "project_id": str(pid)},
               files={"attachment": ("사업계획서.pdf", b"%PDF-1.4 plan", "application/pdf")}, follow_redirects=False)
    assert r.status_code == 303
    docs = db.list_documents("grant", project_id=pid)
    assert len(docs) == 1 and docs[0]["filename"] == "사업계획서.pdf" and "첨부" in docs[0]["stored_path"]
    page = c.get(r.headers["location"]).text
    assert f'href="/doc/{docs[0]["id"]}"' in page and "첨부 · 사업계획서.pdf" in page
    assert c.get(f"/doc/{docs[0]['id']}").status_code == 200
    ledger = db.list_files(kind="attachment")
    assert ledger[0]["doc_id"] == docs[0]["id"]
    # 재생성·수정은 첨부 표시줄을 떼고 질문만 다시 쓴다
    msgs = db.list_chats(int(r.headers["location"].rsplit("/", 1)[-1]))
    assert msgs[0]["content"].startswith(f"[첨부#{docs[0]['id']}] 사업계획서.pdf\n")
