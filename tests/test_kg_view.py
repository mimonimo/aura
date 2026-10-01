"""사업 그래프 화면 — 연차 열 × 갈래 행, 계획↔실적 대응과 근거가 보인다(ADR-0048 6항)."""

from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.graph import kg_store


def test_program_page_shows_years_docs_and_pairs(tmp_path):
    app = _app(tmp_path)
    db = app.state.db
    kg_store.ensure(db)
    with db._conn() as c:
        kg_store.put_node(c, "program:linc30", "program", "3단계 산학연협력 선도전문대학 육성사업", {"names": ["3단계 산학연협력 선도전문대학 육성사업"], "acronyms": ["LINC3.0"]})
        kg_store.put_node(c, "year:linc30:r1", "year", "3단계 산학연협력 선도전문대학 육성사업 1차년도", {"round": 1})
        kg_store.put_node(c, "doc:7", "doc", "1차년도 수정사업계획서.hwp", {"kind": "plan", "kind_label": "계획서"}, 7)
        kg_store.put_node(c, "doc:8", "doc", "1차년도 사업실적보고서.hwp", {"kind": "report", "kind_label": "실적보고서"}, 8)
        kg_store.put_node(c, "doc:7:sec:1", "section", "5-2. 산학연연계 교육방법 모형", {}, 7)
        kg_store.put_node(c, "doc:8:sec:2", "section", "6-2. 산학연연계 교육방법 모형", {}, 8)
        kg_store.put_edge(c, "program:linc30", "year:linc30:r1", "contains", "분류", ["문서 분류"])
        kg_store.put_edge(c, "year:linc30:r1", "doc:7", "contains", "분류", ["제목에 LINC30"])
        kg_store.put_edge(c, "year:linc30:r1", "doc:8", "contains", "분류", ["제목에 LINC30"])
        kg_store.put_edge(c, "doc:7:sec:1", "doc:8:sec:2", "plans_reports", "식별자 일치", ["계획", "실적", "제목 같음·맥락 점수 0.61"])
    c = TestClient(app)
    assert _login(c, "zzaimy", "boot-pass-1")
    page = c.get("/graph/program").text
    assert "3단계 산학연협력 선도전문대학 육성사업" in page and "1차년도 수정사업계획서.hwp" in page and "1차년도 사업실적보고서.hwp" in page
    assert "5-2. 산학연연계 교육방법 모형" in page and "맥락 점수 0.61" in page and "LINC3.0" in page


def test_program_page_without_graph(tmp_path):
    c = TestClient(_app(tmp_path))
    assert _login(c, "zzaimy", "boot-pass-1")
    assert "아직 그래프가 없습니다" in c.get("/graph/program").text
