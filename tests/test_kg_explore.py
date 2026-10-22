"""그래프 탐색(/graph/explore) — 이웃만 잘라 보내기, 열람 권한 거르기, 검색, 온톨로지 구조도."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import kg_explore
from zzaimy.app.db import Database
from zzaimy.app.main import create_app
from zzaimy.graph import kg_store


def _graph(db):
    kg_store.ensure(db)
    pub = db.add_document("공개 계획서.hwp", "dgx://링크/a.hwp", doc_type="grant", access_level="public")
    sec = db.add_document("남의 비공개.hwp", "x/b.hwp", doc_type="grant", owner="other", access_level="owner")
    with db._conn() as conn:
        kg_store.put_nodes(conn, [("program:P", "program", "가람 사업"), ("year:P:r1", "year", "가람 사업 1차년도", {"year": 2024}),
                                  (f"doc:{pub}", "doc", "공개 계획서", {"kind_label": "계획서"}, pub),
                                  (f"doc:{sec}", "doc", "남의 비공개", {}, sec),
                                  ("program:Q", "program", "나래 사업")])
        kg_store.put_edges(conn, [("program:P", "year:P:r1", "contains", "분류", ["연차"]),
                                  ("year:P:r1", f"doc:{pub}", "contains", "분류", ["경로"]),
                                  ("year:P:r1", f"doc:{sec}", "contains", "분류", ["경로"]),
                                  ("program:P", "program:Q", "succeeded_by", "분류", ["장부"])])
    return pub, sec


def test_neighbors_filters_hidden_docs_and_pages(tmp_path):
    db = Database(tmp_path / "t.db")
    pub, sec = _graph(db)
    staff = {"role": "staff", "user": "me", "dept": None}
    d = kg_explore.neighbors(db, "year:P:r1", staff)
    ids = {n["id"] for n in d["nodes"]}
    assert f"doc:{pub}" in ids and f"doc:{sec}" not in ids and "program:P" in ids
    assert {g["key"]: g["total"] for g in d["groups"]}["contains:out:doc"] == 1
    assert kg_explore.neighbors(db, "year:P:r1", {"role": "dev"})["groups"][-1]["total"] >= 1
    # 볼 수 없는 문서 노드는 중심으로도 열리지 않는다
    try:
        kg_explore.neighbors(db, f"doc:{sec}", staff)
        raise AssertionError("열리면 안 된다")
    except Exception as e:
        assert getattr(e, "status_code", None) == 404
    one = kg_explore.neighbors(db, "program:P", staff, per_group=1)
    assert all(g["shown"] <= 1 for g in one["groups"])
    assert any(g["label"] == "후속 사업" for g in one["groups"])


def test_search_programs_schema(tmp_path):
    db = Database(tmp_path / "t.db")
    pub, sec = _graph(db)
    staff = {"role": "staff", "user": "me", "dept": None}
    hits = kg_explore.search(db, "비공개", staff)
    assert hits == []
    assert [h["id"] for h in kg_explore.search(db, "사업", staff)][:2] == ["program:P", "program:Q"]
    kg_explore._cache.clear()
    progs = {p["id"]: p["n_docs"] for p in kg_explore.programs(db)}
    assert progs["program:P"] == 2
    sc = kg_explore.schema(db)
    assert {t["type"] for t in sc["types"]} == {"program", "year", "doc"}
    assert any(r["src"] == "program" and r["dst"] == "program" and r["kind"] == "succeeded_by" for r in sc["rels"])


def test_explore_routes(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter())
    c = TestClient(app)
    kg_explore._cache.clear()
    _graph(app.state.db)
    r = c.get("/graph/explore")
    assert r.status_code == 200 and "cytoscape.min.js" in r.text and "가람 사업" in r.text
    assert c.get("/graph/explore/neighbors", params={"id": "program:P"}).json()["center"]["label"] == "가람 사업"
    assert c.get("/graph/explore/neighbors", params={"id": "program:P", "more": "contains:out:year@0"}).status_code == 200
    assert c.get("/graph/explore/neighbors", params={"id": "nope"}).status_code == 404
    assert c.get("/graph/explore/schema").json()["types"]
    assert c.get("/static/cytoscape-fcose.js").status_code == 200


def test_ontology_downloads(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter())
    c = TestClient(app)
    kg_explore._cache.clear()
    _graph(app.state.db)
    owl = c.get("/graph/explore/ontology.ttl")
    assert owl.status_code == 200 and "owl:Ontology" in owl.text and "z:Program" in owl.text
    assert "sh:NodeShape" in c.get("/graph/explore/shapes.ttl").text
    assert c.get("/graph/explore/schema").json()["shacl"] is None          # 아직 검사 전


def _program_graph(db):
    """묶음 하나(사업 둘)·묶음 밖 사업 하나, 사업마다 문서 — 개요에 문서 노드가 끼지 않는지 본다."""
    kg_store.ensure(db)
    d1 = db.add_document("가 계획서.hwp", "x/1.hwp", doc_type="grant", access_level="public")
    d2 = db.add_document("나 계획서.hwp", "x/2.hwp", doc_type="grant", access_level="public")
    d3 = db.add_document("다 계획서.hwp", "x/3.hwp", doc_type="grant", access_level="public")
    with db._conn() as conn:
        kg_store.put_nodes(conn, [("group:G", "program_group", "갈래 묶음"), ("program:A", "program", "가온 사업"),
                                  ("program:B", "program", "누리 사업"), ("program:C", "program", "다솜 사업"),
                                  ("year:A:r1", "year", "가온 사업 1차년도", {"round": 1}),
                                  ("org:M", "org", "어느 부처"),
                                  *[(f"doc:{d}", "doc", f"문서 {d}", {}, d) for d in (d1, d2, d3)]])
        kg_store.put_edges(conn, [("group:G", "program:A", "contains", "분류", ["분류"]), ("group:G", "program:B", "contains", "분류", ["분류"]),
                                  ("program:A", "year:A:r1", "contains", "분류", ["분류"]), ("year:A:r1", f"doc:{d1}", "contains", "분류", ["분류"]),
                                  ("program:A", f"doc:{d2}", "contains", "분류", ["분류"]), ("program:B", f"doc:{d2}", "contains", "분류", ["분류"]),
                                  ("program:C", f"doc:{d3}", "contains", "분류", ["분류"]),
                                  ("program:A", "program:C", "related", "유사도", ["근거"]), ("program:A", "org:M", "supervised_by", "분류", ["장부"])])
    return d1, d2, d3


def test_overview_has_programs_and_groups_only(tmp_path):
    db = Database(tmp_path / "t.db")
    _program_graph(db)
    kg_explore._cache.clear()
    ov = kg_explore.overview(db)
    types = {n["id"]: n["type"] for n in ov["nodes"]}
    assert types == {"group:G": "program_group", "program:A": "program", "program:B": "program", "program:C": "program"}
    assert {(e["src"], e["kind"], e["dst"]) for e in ov["edges"]} == {
        ("group:G", "contains", "program:A"), ("group:G", "contains", "program:B"), ("program:A", "related", "program:C")}
    by = {n["id"]: n for n in ov["nodes"]}
    assert by["program:A"]["n_docs"] == 2 and by["group:G"]["n_docs"] == 2            # 크기 = 문서 수(묶음은 합집합)
    assert by["program:A"]["groups"] == ["group:G"] and by["program:C"]["groups"] == []
    assert ov["totals"] == {"programs": 3, "groups": 1, "docs": 3}                   # 두 사업에 걸린 문서는 한 번


def test_explore_default_overview_and_selection_query(tmp_path):
    from tests.test_accounts import _app, _login

    app = _app(tmp_path)
    kg_explore._cache.clear()
    d1, _d2, _d3 = _program_graph(app.state.db)
    c = TestClient(app)
    assert _login(c, "zzdev", "devpass")
    ov = c.get("/graph/explore/overview").json()
    assert ov["nodes"] and not any(n["type"] in ("doc", "section", "year", "org") for n in ov["nodes"])
    page = c.get("/graph/explore").text
    assert '"sel": []' in page and 'data-focus=' not in page                       # 처음 열면 고른 것 없이 전체 개요
    assert 'data-id="group:G"' in page and "data-l1" in page and "전체 보기" in page
    assert '"sel": ["program:A", "program:C"]' in c.get("/graph/explore", params={"sel": "program:A,program:C"}).text
    assert '"sel": ["doc:' + str(d1) + '"]' in c.get("/graph/explore", params={"id": f"doc:{d1}"}).text   # 옛 id= 링크는 하나 고르기
    assert 'data-tab="onto"' in c.get("/graph/explore", params={"tab": "onto"}).text
    assert "업무 화면으로" in page and "한글 문서함" not in page     # 개발 메뉴의 화면은 개발 사이드바
    staff = TestClient(app)
    assert _login(staff, "zzaimy", "boot-pass-1")
    sp = staff.get("/graph/explore", params={"tab": "onto"}).text
    assert "한글 문서함" in sp and 'data-tab="kg"' in sp               # 온톨로지 탭은 개발자만


def test_ontology_default_schema_has_classes_and_attributes(tmp_path):
    db = Database(tmp_path / "t.db")
    _program_graph(db)
    kg_explore._cache.clear()
    sc = kg_explore.schema(db)
    by = {t["type"]: t for t in sc["types"]}
    assert {"program_group", "program", "year", "org", "doc"} <= set(by)
    assert by["year"]["attrs"].get("차년도") == 100                                   # 속성(채워진 비율)도 함께 그린다
    assert any(r["src"] == "program" and r["dst"] == "org" and r["kind"] == "supervised_by" for r in sc["rels"])


def test_program_docs_panel_lists_visible_docs_by_year(tmp_path):
    """오른쪽 패널의 「문서」 — 사업 노드의 연차별 문서(볼 수 있는 것만)와 라이브러리 바로가기."""
    from fastapi.testclient import TestClient
    from tests.test_accounts import _app, _login
    from zzaimy.graph import kg_store

    app = _app(tmp_path)
    c = TestClient(app)
    assert _login(c, "zzaimy", "boot-pass-1")
    db = app.state.db
    kg_store.ensure(db)
    a = db.add_document("가 계획서.pdf", "x", doc_type="grant", access_level="public")
    b = db.add_document("가 보고서.pdf", "x", doc_type="grant", access_level="public")
    with db._conn() as conn:
        kg_store.put_node(conn, "program:ga", "program", "가 사업", {})
        kg_store.put_node(conn, "year:ga:y2024", "year", "가 사업 1차년도 (2024)", {"year": 2024})
        kg_store.put_edge(conn, "program:ga", "year:ga:y2024", "contains", "분류", ["연도"])
        for did, src in ((a, "year:ga:y2024"), (b, "program:ga")):
            kg_store.put_node(conn, f"doc:{did}", "doc", f"문서{did}", {}, did)
            kg_store.put_edge(conn, src, f"doc:{did}", "contains", "분류", ["분류"])
    got = c.get("/graph/explore/docs?id=program:ga").json()
    assert got["total"] == 2 and got["years"][0]["label"].endswith("(2024)") and got["years"][-1]["label"] == "연차 미상"
    assert {d["doc_id"] for y in got["years"] for d in y["docs"]} == {a, b}
    assert c.get("/graph/explore/docs?id=doc:1").json()["total"] == 0
    page = c.get("/graph/explore").text
    assert "docsBox(id, body)" in page and "fixedNodeConstraint" in page
