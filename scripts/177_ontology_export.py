"""온톨로지 표준 내보내기·검사(ADR-0056) — OWL 설계도·RDF 인스턴스를 쓰고 SHACL 규칙으로 검사한다.

쓰는 곳: data/platform/ontology/
  ontology.ttl   설계도(클래스·하위 클래스·관계 정의역·치역·속성) — Protégé·WebVOWL 로 연다
  shapes.ttl     검사 규칙(src/zzaimy/graph/shapes.ttl 사본)
  instances.ttl  사업 묶음·사업·기관·연차·문서·성과지표와 관계(+ 관계 진술)
  shacl_report.json  규칙별 위반 수·예시 — 작업 현황·온톨로지 보기에 나온다
실행: env PYTHONPATH=src .venv/bin/python scripts/177_ontology_export.py [--no-validate]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import kg_explore  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402
from zzaimy.graph import ontology_rdf  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data/platform/ontology"))
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    t0 = time.time()
    kinds = kg_explore._KIND_KO
    schema = ontology_rdf.schema_graph(kg_explore.schema(db), kinds)
    schema.serialize(out / "ontology.ttl", format="turtle")
    shutil.copy(ontology_rdf.SHAPES, out / "shapes.ttl")
    inst = ontology_rdf.instance_graph(db, kinds)
    inst.serialize(out / "instances.ttl", format="turtle")
    print(f"설계도 {len(schema)}개 세 쌍 · 인스턴스 {len(inst)}개 세 쌍 ({time.time() - t0:.0f}초)")
    if args.no_validate:
        return
    t1 = time.time()
    rep = ontology_rdf.validate(inst, schema)
    rep.update({"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "seconds": round(time.time() - t1), "triples": len(inst)})
    (out / "shacl_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"ONTO_SHACL conforms={rep['conforms']} violations={rep['n_violations']} rules={len(rep['rules'])} "
          + " | ".join(f"{v['message']} {v['n']}" for v in rep["violations"][:6]))


if __name__ == "__main__":
    main()
