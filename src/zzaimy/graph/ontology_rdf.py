"""온톨로지를 표준 형식으로 — OWL 설계도(스키마)·RDF 인스턴스 내보내기와 SHACL 검사(ADR-0056).

정본은 PostgreSQL(kg_nodes·kg_edges)이다. 여기서는 그것을 W3C 표준(OWL·RDF·SHACL)으로 옮겨
1) 설계도(ontology.ttl)를 Protégé·WebVOWL 같은 공개 도구로 열 수 있게 하고,
2) 검사 규칙(shapes.ttl)을 코드 밖 문서로 두어 pySHACL 이 그래프를 검사하게 한다.
인스턴스는 사업 묶음·사업·연차·기관·문서·성과지표 수준만 낸다(절·단위과제 수십만 개는 규모 때문에 뺀다).
관계는 근거·기준을 검사할 수 있게 진술(z:Statement)로도 낸다 — RDF 세 쌍에는 근거를 달 수 없기 때문이다.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

BASE = "https://aura.ync.ac.kr/ontology/zzaimy#"
SHAPES = Path(__file__).with_name("shapes.ttl")

CLASS = {"program_group": "ProgramGroup", "program": "Program", "org": "Org", "year": "Year", "doc": "Doc",
         "section": "Section", "unit": "Unit", "indicator": "Indicator"}
PROP = {"contains": "contains", "instance_of": "instanceOf", "continues": "continues", "plans_reports": "plansReports",
        "evaluates": "evaluates", "has_indicator": "hasIndicator", "measures": "measures", "related": "relatedProgram",
        "integrated_into": "integratedInto", "succeeded_by": "succeededBy", "supervised_by": "supervisedBy",
        "managed_by": "managedBy"}
LEDGER_KINDS = {"integrated_into", "succeeded_by", "supervised_by", "managed_by"}     # 외부 확인 장부에서 온 관계
EXPORT_TYPES = ("program_group", "program", "org", "year", "doc", "indicator")
DATA_PROPS = {"year": ("연도", "integer"), "round": ("차년도", "integer"), "kind": ("갈래", "string"),
              "kindLabel": ("갈래 이름", "string"), "period": ("사업 기간", "string")}


def _rdf():
    from rdflib import OWL, RDF, RDFS, XSD, BNode, Graph, Literal, Namespace
    from rdflib.collection import Collection
    return Graph, Namespace(BASE), RDF, RDFS, OWL, XSD, Literal, BNode, Collection


def schema_graph(schema: dict, kinds: dict[str, str]):
    """설계도 — 클래스(노드 종류)·하위 클래스(문서 갈래)·관계(정의역·치역은 실제 그래프에서 나온 조합)·속성."""
    from zzaimy.app.kg_explore import KIND_KO, TYPE_DEF, TYPE_KO
    Graph, Z, RDF, RDFS, OWL, XSD, Literal, BNode, Collection = _rdf()
    g = Graph()
    g.bind("z", Z)
    g.bind("owl", OWL)
    onto = Z[""]
    g.add((onto, RDF.type, OWL.Ontology))
    g.add((onto, RDFS.label, Literal("ZZAIMY 대학 재정지원 사업 온톨로지", lang="ko")))
    g.add((onto, RDFS.comment, Literal("정본은 플랫폼 DB(kg_nodes·kg_edges). scripts/177 이 내보낸다(ADR-0056).", lang="ko")))
    for t, name in CLASS.items():
        c = Z[name]
        g.add((c, RDF.type, OWL.Class))
        g.add((c, RDFS.label, Literal(TYPE_KO.get(t, t), lang="ko")))
        if TYPE_DEF.get(t):
            g.add((c, RDFS.comment, Literal(TYPE_DEF[t], lang="ko")))
    for k, label in kinds.items():
        c = Z["Doc_" + k]
        g.add((c, RDF.type, OWL.Class))
        g.add((c, RDFS.subClassOf, Z.Doc))
        g.add((c, RDFS.label, Literal(label, lang="ko")))
    for c in ("Statement", "LedgerStatement"):
        g.add((Z[c], RDF.type, OWL.Class))
    g.add((Z.LedgerStatement, RDFS.subClassOf, Z.Statement))
    g.add((Z.Statement, RDFS.label, Literal("관계 진술(출발·도착·기준·근거)", lang="ko")))
    g.add((Z.LedgerStatement, RDFS.label, Literal("외부 확인 장부에서 온 관계 진술", lang="ko")))

    def union(types: set[str]):
        names = [Z[CLASS[t]] for t in sorted(types) if t in CLASS]
        if len(names) == 1:
            return names[0]
        node, lst = BNode(), BNode()
        Collection(g, lst, names)
        g.add((node, RDF.type, OWL.Class))
        g.add((node, OWL.unionOf, lst))
        return node
    dom: dict[str, set] = defaultdict(set)
    rng: dict[str, set] = defaultdict(set)
    for r in schema.get("rels", []):
        dom[r["kind"]].add(r["src"])
        rng[r["kind"]].add(r["dst"])
    for kind, pname in PROP.items():
        p = Z[pname]
        g.add((p, RDF.type, OWL.ObjectProperty))
        g.add((p, RDFS.label, Literal(KIND_KO.get(kind, (kind,))[0], lang="ko")))
        if dom.get(kind):
            g.add((p, RDFS.domain, union(dom[kind])))
            g.add((p, RDFS.range, union(rng[kind])))
    for pname, (label, dt) in DATA_PROPS.items():
        p = Z[pname]
        g.add((p, RDF.type, OWL.DatatypeProperty))
        g.add((p, RDFS.label, Literal(label, lang="ko")))
        g.add((p, RDFS.range, XSD[dt]))
    for pname, label in (("source", "출발"), ("target", "도착")):
        g.add((Z[pname], RDF.type, OWL.ObjectProperty))
        g.add((Z[pname], RDFS.label, Literal(label, lang="ko")))
        g.add((Z[pname], RDFS.domain, Z.Statement))
    for pname, label in (("basis", "기준"), ("evidence", "근거"), ("relation", "관계 종류")):
        g.add((Z[pname], RDF.type, OWL.DatatypeProperty))
        g.add((Z[pname], RDFS.label, Literal(label, lang="ko")))
        g.add((Z[pname], RDFS.domain, Z.Statement))
        g.add((Z[pname], RDFS.range, XSD.string))
    return g


def node_iri(Z, node_id: str):
    return Z["n/" + quote(node_id, safe="")]


def instance_graph(db, kinds: dict[str, str]):
    """사업 묶음·사업·기관·연차·문서·성과지표 노드와 그 사이 관계(+ 관계 진술)."""
    Graph, Z, RDF, RDFS, OWL, XSD, Literal, BNode, Collection = _rdf()
    g = Graph()
    g.bind("z", Z)
    keep: set[str] = set()
    ph = ",".join("?" * len(EXPORT_TYPES))
    with db._conn() as conn:
        for r in conn.execute(f"SELECT id, type, label, props FROM kg_nodes WHERE type IN ({ph})", EXPORT_TYPES):
            nid, typ, label, props = r[0], r[1], r[2], json.loads(r[3] or "{}") if isinstance(r[3], str) else (r[3] or {})
            s = node_iri(Z, nid)
            keep.add(nid)
            g.add((s, RDF.type, Z[CLASS[typ]]))
            g.add((s, RDFS.label, Literal(label)))
            if typ == "doc" and props.get("kind") in kinds:
                g.add((s, RDF.type, Z["Doc_" + props["kind"]]))
            for key, pname in (("year", "year"), ("round", "round")):
                v = props.get(key)
                if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
                    g.add((s, Z[pname], Literal(int(v), datatype=XSD.integer)))
            if typ == "doc" and props.get("kind"):
                g.add((s, Z.kind, Literal(str(props["kind"]))))
            if typ == "program" and (props.get("ledger") or {}).get("period"):
                g.add((s, Z.period, Literal(str(props["ledger"]["period"]))))
        n = 0
        for r in conn.execute("SELECT src, dst, kind, basis, evidence FROM kg_edges"):
            src, dst, kind, basis, ev = r[0], r[1], r[2], r[3], r[4]
            if src not in keep or dst not in keep or kind not in PROP:
                continue
            a, b = node_iri(Z, src), node_iri(Z, dst)
            g.add((a, Z[PROP[kind]], b))
            st = Z["s/" + str(n)]
            n += 1
            g.add((st, RDF.type, Z.LedgerStatement if kind in LEDGER_KINDS else Z.Statement))
            g.add((st, Z.source, a))
            g.add((st, Z.target, b))
            g.add((st, Z.relation, Literal(kind)))
            g.add((st, Z.basis, Literal(basis)))
            for e in (json.loads(ev or "[]") if isinstance(ev, str) else (ev or [])):
                if str(e).strip():
                    g.add((st, Z.evidence, Literal(str(e))))
    return g


def validate(data_graph, schema=None) -> dict:
    """pySHACL 검사 — 규칙(sh:message)별 위반 수와 예시 노드."""
    from pyshacl import validate as _validate
    from rdflib import RDF, RDFS, Graph, Namespace
    SH = Namespace("http://www.w3.org/ns/shacl#")
    shapes = Graph().parse(SHAPES, format="turtle")
    conforms, report, _text = _validate(data_graph, shacl_graph=shapes, ont_graph=schema, inference="none",
                                        allow_warnings=True, advanced=True)
    by: dict[str, dict] = {}
    for res in report.subjects(RDF.type, SH.ValidationResult):
        msg = str(report.value(res, SH.resultMessage) or "")
        focus = str(report.value(res, SH.focusNode) or "")
        shape = str(report.value(res, SH.sourceShape) or "")
        sev = "경고" if report.value(res, SH.resultSeverity) == SH.Warning else "위반"
        row = by.setdefault(msg, {"message": msg, "n": 0, "examples": [], "shape": shape, "severity": sev})
        row["n"] += 1
        if len(row["examples"]) < 5:
            row["examples"].append(focus.replace(BASE + "n/", "").replace(BASE, ""))
    rules = []
    for s in shapes.subjects(RDF.type, SH.NodeShape):
        rules.append({"shape": str(s).replace(BASE, ""), "comment": str(shapes.value(s, RDFS.comment) or "")})
    rows = sorted(by.values(), key=lambda r: (r["severity"] != "위반", -r["n"]))
    return {"conforms": bool(conforms), "violations": rows,
            "n_violations": sum(r["n"] for r in rows if r["severity"] == "위반"),
            "n_warnings": sum(r["n"] for r in rows if r["severity"] == "경고"),
            "rules": sorted(rules, key=lambda r: r["shape"])}
