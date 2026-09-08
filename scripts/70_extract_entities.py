"""개체 추출 배치 (지식 그래프 2단계, ADR-0010).

전체 문서에서 사업·기관·연도 개체를 결정론 규칙으로 추출해 DB에 저장한다.
재실행하면 교체된다(멱등). LLM·GPU 불필요, 수 초면 끝난다.

실행: env PYTHONPATH=src .venv/bin/python scripts/70_extract_entities.py
"""

from __future__ import annotations

from pathlib import Path

from zzaimy.app.db import Database
from zzaimy.graph.entities import extract_doc_entities


def main() -> None:
    db = Database(Path("data/platform/platform.db"))
    summary = extract_doc_entities(db)
    graph = db.graph_entities(min_docs=2)
    print(
        f"문서 {summary['docs']}건에서 개체 언급 {summary['links']}건 추출, "
        f"그래프 노드 승격(2개 문서 이상) {len(graph['entities'])}개"
    )
    for e in sorted(graph["entities"], key=lambda x: -x["n_docs"])[:15]:
        print(f"  [{e['kind']}] {e['name']} — 문서 {e['n_docs']}건")


if __name__ == "__main__":
    main()
