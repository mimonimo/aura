"""사업 그래프 화면 — 연차 열 × 갈래 행, 계획↔실적 대응과 근거가 보인다(ADR-0048 6항)."""

from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.graph import kg_store
from zzaimy.app.kg_view import program_view


def _scoped_graph(tmp_path):
    app = _app(tmp_path)
    db = app.state.db
    kg_store.ensure(db)
    docs = {}
    for name, owner, level in (("public", "other", "public"), ("mine", "zzaimy", "owner"),
                               ("private", "other", "owner"), ("hidden", "other", "owner")):
        did = db.add_document(name + ".txt", "unused", doc_type="regulation")
        docs[name] = did
        with db._conn() as conn:
            conn.execute("UPDATE documents SET owner=?,access_level=? WHERE id=?", (owner, level, did))
            pid = "hidden" if name == "hidden" else "shared"
            kg_store.put_node(conn, f"program:{pid}", "program", pid, {"names": [pid], "acronyms": []})
            kg_store.put_node(conn, f"year:{pid}:r1", "year", "1차년도", {"round": 1})
            kg_store.put_node(conn, f"doc:{did}", "doc", name + ".txt", {"kind": "plan"}, did)
            kg_store.put_node(conn, f"doc:{did}:sec:1", "section", name + " section", {}, did)
            for src, dst in ((f"program:{pid}", f"year:{pid}:r1"), (f"year:{pid}:r1", f"doc:{did}"),
                             (f"doc:{did}", f"doc:{did}:sec:1")):
                kg_store.put_edge(conn, src, dst, "contains", "분류", [name + " evidence"])
    with db._conn() as conn:
        for name in ("mine", "private"):
            kg_store.put_edge(conn, f"doc:{docs['public']}:sec:1", f"doc:{docs[name]}:sec:1",
                              "plans_reports", "식별자 일치", [name + " pair evidence"])
    return app, db, docs


def test_program_graph_hides_private_nodes_pairs_and_counts(tmp_path):
    app, db, docs = _scoped_graph(tmp_path)
    data = program_view(db, "program:shared", scope={"user": "zzaimy", "role": "staff", "dept": None})
    assert [p["id"] for p in data["programs"]] == ["program:shared"]
    assert data["n_sections"] == 2
    pairs = [p for pairs in data["plans_reports"].values() for p in pairs]
    assert len(pairs) == 1 and pairs[0]["why"] == "mine pair evidence"
    client = TestClient(app)
    assert _login(client, "zzaimy", "boot-pass-1")
    page = client.get("/graph/program?id=program:shared")
    assert page.status_code == 200
    assert "public.txt" in page.text and "mine.txt" in page.text
    assert "private.txt" not in page.text and "private pair evidence" not in page.text
    assert "program:hidden" not in page.text
    assert client.get("/graph/program?id=program:hidden").status_code == 404
    assert client.get("/graph/program?id=program:missing").status_code == 404


def test_program_graph_admin_keeps_all_visible_documents(tmp_path):
    app, db, docs = _scoped_graph(tmp_path)
    data = program_view(db, "program:shared", scope={"user": "zzdev", "role": "dev", "dept": None})
    assert len(data["programs"]) == 2 and data["n_sections"] == 3
    assert sum(len(p) for p in data["plans_reports"].values()) == 2


def test_program_graph_with_no_access_has_no_programs(tmp_path):
    app, db, docs = _scoped_graph(tmp_path)
    with db._conn() as conn:
        conn.execute("UPDATE documents SET access_level='owner',owner='other'")
    data = program_view(db, None, scope={"user": "zzaimy", "role": "staff", "dept": None})
    assert data == {"programs": [], "program": None}


def test_program_page_shows_years_docs_and_pairs(tmp_path):
    app = _app(tmp_path)
    db = app.state.db
    # Graph references must resolve to actual, visible documents.
    for i in range(8):
        db.add_document(f"fixture-{i}.txt", "unused", doc_type="regulation")
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
