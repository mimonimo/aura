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


def test_default_program_is_the_one_with_most_docs():
    progs = [{"id": "program_group:G", "type": "program_group", "n_docs": 90},
             {"id": "program:A", "type": "program", "n_docs": 3}, {"id": "program:B", "type": "program", "n_docs": 12}]
    assert kg_explore.default_program(progs) == "program:B"                  # 묶음이 더 커도 사업 하나
    assert kg_explore.default_program(progs[:1]) == "program_group:G"
    assert kg_explore.default_program([]) == ""


def test_explore_opens_default_program_and_keeps_dev_sidebar(tmp_path):
    from tests.test_accounts import _app, _login

    app = _app(tmp_path)
    kg_explore._cache.clear()
    pub, _sec = _graph(app.state.db)
    c = TestClient(app)
    assert _login(c, "zzdev", "devpass")
    page = c.get("/graph/explore").text
    assert 'data-focus="program:P"' in page                                 # 처음 열면 문서가 가장 많은 사업
    assert "업무 화면으로" in page and "진행 중 프로젝트 찾기" not in page     # 개발 메뉴의 화면은 개발 사이드바
    assert 'data-focus="doc:' + str(pub) + '"' in c.get("/graph/explore", params={"id": f"doc:{pub}"}).text
    staff = TestClient(app)
    assert _login(staff, "zzaimy", "boot-pass-1")
    assert "진행 중 프로젝트 찾기" in staff.get("/graph/explore").text          # 개발자가 아니면 업무 사이드바
