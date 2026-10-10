"""프로젝트·대화 문서의 세 갈래 — 대화 문서함, 프로젝트 화면, 지침·기준 탭이 같은 목록을 본다."""
import re

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import project_documents
from zzaimy.app.chat_topics import ChatTopics
from zzaimy.app.db import Database
from zzaimy.app.main import create_app


class SourcingResponder:
    """답할 때 문서함의 다른 문서를 근거로 불러온 것처럼 last_sources 를 남긴다."""

    def __init__(self):
        self.doc_ids: list[int] = []
        self.last_sources: list[dict] = []

    def answer(self, db, question, attachment_text=None, criteria_ids=None, session_id=None, project=None):
        self.last_sources = [{"title": "지난 계획서", "heading": "", "snippet": "", "doc_id": d,
                              "origin": "사업 문서", "weak": False} for d in self.doc_ids]
        return "합성 답변"


def _app(tmp_path, responder=None):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), responder=responder or SourcingResponder())
    return app, TestClient(app)


def _bundle(c):
    files = [("file", ("2026학년도 ○○ 지원사업 기본계획.hwp", b"hwp-bytes", "application/octet-stream")),
             ("file", ("[붙임2] 사업계획서 양식.hwp", b"hwp-form", "application/octet-stream"))]
    r = c.post("/projects/bundle", data={"sector": "grant"}, files=files, follow_redirects=False)
    return int(r.headers["location"].split("/project/")[1].split("?")[0])


def _ids(groups):
    return {key: [d["id"] for d in groups[key]] for key, _ in project_documents.GROUPS}


def test_chat_panel_and_project_page_show_the_same_three_groups(tmp_path):
    responder = SourcingResponder()
    app, c = _app(tmp_path, responder)
    db = app.state.db
    pid = _bundle(c)
    library = db.add_document(filename="2025 지난 계획서.pdf", stored_path=str(tmp_path / "x.pdf"), doc_type="grant")
    responder.doc_ids = [library]
    r = c.post("/chat/send", data={"question": "지난해 계획서와 비교해 줘", "project_id": str(pid)},
               files={"attachment": ("회의 메모.pdf", b"%PDF-1.4 memo", "application/pdf")}, follow_redirects=False)
    sid = int(r.headers["location"].rsplit("/", 1)[-1])

    data = c.get(f"/api/chat/{sid}/documents").json()
    panel = {g["key"]: {d["name"]: d for d in g["documents"]} for g in data["groups"]}
    assert set(panel["attached"]) == {"회의 메모", "[붙임2] 사업계획서 양식"}
    assert set(panel["rules"]) == {"2026학년도 ○○ 지원사업 기본계획"}
    # 에이전트가 근거로 불러온 문서는 참고 문서에, 어디서 왔는지와 함께
    assert set(panel["reference"]) == {"2025 지난 계획서"}
    assert panel["reference"]["2025 지난 계획서"]["via"].startswith(project_documents.AGENT_NOTE)

    page = c.get(f"/project/{pid}").text
    for key, label in project_documents.GROUPS:
        block = page.split(f'data-doc-group="{key}"')[1].split("</section>")[0]
        assert re.search(rf"{label} <span class=\"project-doc-count\">{len(panel[key])}</span>", block)
        for d in panel[key].values():
            assert f'href="/doc/{d["id"]}"' in block
    # 지침·기준 탭의 규정·지침도 같은 목록(검토 전이라도 연결된 기준은 보인다)
    criteria = page.split('id="paneCriteria"')[1].split("</section>")[0]
    for d in panel["rules"].values():
        assert f'href="/doc/{d["id"]}"' in criteria


def test_agent_sources_from_another_project_chat_show_on_every_view(tmp_path):
    app, c = _app(tmp_path)
    db = app.state.db
    pid = _bundle(c)
    library = db.add_document(filename="참고 규정.pdf", stored_path=str(tmp_path / "r.pdf"), doc_type="grant")
    weak = db.add_document(filename="우연히 걸린 문서.pdf", stored_path=str(tmp_path / "w.pdf"), doc_type="grant")
    s1 = db.create_chat_session("첫 대화", project_id=pid, owner="zzaimy")
    s2 = db.create_chat_session("둘째 대화", project_id=pid, owner="zzaimy")
    ChatTopics(tmp_path / "t.db").record(s1, [{"title": "참고 규정", "doc_id": library, "origin": "문서함 검색"},
                                             {"title": "약한 근거", "doc_id": weak, "weak": True}])
    project = db.get_project(pid)
    a = project_documents.collect(db, project, s1, role="dev")
    b = project_documents.collect(db, project, s2, role="dev")
    p = project_documents.collect(db, project, role="dev")
    assert _ids(a) == _ids(b) == _ids(p)
    assert _ids(p)["reference"] == [library]                      # 약한 근거는 쌓지 않는다


def test_hidden_documents_stay_hidden(tmp_path):
    db = Database(tmp_path / "t.db")
    ChatTopics(tmp_path / "t.db")
    pid = db.create_project("grant", "사업", owner="kim")
    mine = db.add_document(filename="내 첨부.pdf", stored_path="a", doc_type="grant", project_id=pid, owner="kim",
                           access_level="owner")
    other = db.add_document(filename="남의 비공개.pdf", stored_path="b", doc_type="grant", owner="lee",
                            access_level="owner")
    sid = db.create_chat_session("대화", project_id=pid, owner="kim")
    ChatTopics(tmp_path / "t.db").record(sid, [{"title": "남의 비공개", "doc_id": other}])
    groups = project_documents.collect(db, db.get_project(pid), sid, user="kim", role="staff")
    assert _ids(groups) == {"attached": [mine], "reference": [], "rules": []}
    assert _ids(project_documents.collect(db, db.get_project(pid), sid, role="dev"))["reference"] == [other]


def test_criteria_picker_keeps_links_it_does_not_list(tmp_path):
    app, c = _app(tmp_path)
    pid = _bundle(c)
    db = app.state.db
    # 다른 업무 영역의 기준 — 선택지(이 영역·공통의 검토 완료 기준)에는 없지만 규정·지침에는 보이고 저장해도 풀리지 않는다
    other = db.add_document(filename="채용 규정.pdf", stored_path=str(tmp_path / "c.pdf"), doc_type="regulation", sector="recruit")
    db.set_project_criteria(pid, [*db.get_project_criteria_ids(pid), other])
    page = c.get(f"/project/{pid}").text
    assert f'<input type="hidden" name="criteria" value="{other}">' in page
    assert f'href="/doc/{other}"' in page.split('id="paneCriteria"')[1].split("</section>")[0]
