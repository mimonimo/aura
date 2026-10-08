"""온톨로지 표준 내보내기·SHACL 검사 — 근거 없는 관계·두 사업에 속한 연차·범위 밖 연도를 잡는다."""
from __future__ import annotations

from zzaimy.app import kg_explore
from zzaimy.app.db import Database
from zzaimy.graph import kg_store, ontology_rdf


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    kg_store.ensure(db)
    d = db.add_document("계획서.hwp", "x", doc_type="grant")
    with db._conn() as c:
        kg_store.put_nodes(c, [("program:P", "program", "가 사업"), ("program:Q", "program", "나 사업"), ("org:교육부", "org", "교육부"),
                               ("year:P:r1", "year", "1차년도", {"round": 1, "year": 2024}),
                               ("year:X:r1", "year", "떠돌이 연차", {"round": 1, "year": 1850}),
                               (f"doc:{d}", "doc", "계획서.hwp", {"kind": "plan", "year": 2024}, d)])
        kg_store.put_edges(c, [("program:P", "year:P:r1", "contains", "분류", ["연차"]),
                               ("program:Q", "year:P:r1", "contains", "분류", ["잘못 붙은 연차"]),
                               ("year:P:r1", f"doc:{d}", "contains", "분류", ["경로"]),
                               ("program:P", "org:교육부", "supervised_by", "분류", ["주관 기관", "https://www.moe.go.kr/x"]),
                               ("program:P", "program:Q", "succeeded_by", "분류", ["출처 없는 장부 관계"])])
    return db


def test_schema_and_shacl_find_violations(tmp_path):
    db = _db(tmp_path)
    kinds = kg_explore._KIND_KO
    schema = ontology_rdf.schema_graph(kg_explore.schema(db), kinds)
    ttl = schema.serialize(format="turtle")
    assert "z:Program" in ttl and "z:Doc_plan" in ttl and "rdfs:subClassOf z:Doc" in ttl and "z:supervisedBy" in ttl
    inst = ontology_rdf.instance_graph(db, kinds)
    rep = ontology_rdf.validate(inst, schema)
    msgs = {v["message"]: v["n"] for v in rep["violations"]}
    assert not rep["conforms"]
    assert msgs.get("연차가 사업 하나에 속하지 않는다(없거나 둘 이상)") == 2         # 두 사업에 속한 연차 + 떠돌이 연차
    assert msgs.get("연차의 연도가 범위 밖이거나 여럿이다") == 1
    assert msgs.get("장부 관계에 출처 URL 이 없다") == 1
    assert "근거 없는 관계" not in msgs and "문서가 어떤 사업·연차에도 속하지 않는다(미분류)" not in msgs
    assert rep["n_warnings"] == 0 and rep["n_violations"] == 4
    assert {r["shape"] for r in rep["rules"]} >= {"ProgramShape", "YearShape", "StatementShape"}


def test_institutional_docs_are_not_unassigned_warnings(tmp_path):
    """사업 없이 검토가 「사업 아님」(status agent)으로 정한 문서는 기관 업무 문서 — 미분류 경고는 정말 모르는 문서만."""
    db = Database(tmp_path / "t.db")
    kg_store.ensure(db)
    a = db.add_document("업무분장표.hwp", "x", doc_type="grant")
    b = db.add_document("무엇인지 모름.hwp", "y", doc_type="grant")
    p_ = db.add_document("에이전트가 사업을 정한 계획서.hwp", "z", doc_type="grant")
    with db._conn() as c:
        kg_store.put_nodes(c, [(f"doc:{a}", "doc", "업무분장표.hwp", {"kind": "table", "status": "agent"}, a),
                               (f"doc:{b}", "doc", "무엇인지 모름.hwp", {"kind": "table", "status": "review"}, b),
                               ("program:P", "program", "가 사업"), (f"doc:{p_}", "doc", "계획서.hwp", {"kind": "plan", "status": "agent"}, p_)])
        kg_store.put_edges(c, [("program:P", f"doc:{p_}", "contains", "분류", ["검토 에이전트"])])
    kinds = kg_explore._KIND_KO
    schema = ontology_rdf.schema_graph(kg_explore.schema(db), kinds)
    assert "z:InstitutionalDoc" in schema.serialize(format="turtle")
    inst = ontology_rdf.instance_graph(db, kinds)
    rep = ontology_rdf.validate(inst, schema)
    assert rep["n_warnings"] == 1 and rep["n_violations"] == 0
    inst_ttl = inst.serialize(format="turtle")
    assert inst_ttl.count("z:InstitutionalDoc") == 1                     # 사업에 이어진 에이전트 문서는 기관 업무 문서가 아니다
