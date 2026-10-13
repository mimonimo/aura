"""개발 현황 「답변 흐름」 탭 — 답이 근거를 찾는 순서(질문 읽기 → 그래프 → 문서 검색 → 답변)와 그 단계의 실제 수치.

수치는 모두 기계 산출물에서 읽는다 — 관계 수는 그래프(kg_edges), 관계 질문 측정은 scripts/184 결과 파일,
일반 질문 측정은 scripts/173 결과 파일. 파일이 없으면 「측정 전」 으로 둔다.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# 그래프 단계가 따라가는 관계 — (화면 이름, kg_edges.kind, 문서 수준만 셀지)
RELATION_ROWS = (
    ("계획서 → 그 연차 실적보고서", "plans_reports", True),
    ("평가 문서 → 평가받은 문서", "evaluates", True),
    ("앞 단계 사업 → 다음 단계 사업", "succeeded_by", False),
    ("사업 → 편입된 사업", "integrated_into", False),
    ("연관 사업(문서 근거)", "related", False),
)
_cache: dict = {"at": 0.0, "edges": None}


def _edge_counts(db) -> dict[str, int]:
    if _cache["edges"] is not None and time.time() - _cache["at"] < 600:
        return _cache["edges"]
    out: dict[str, int] = {}
    try:
        with db._conn() as conn:
            for _, kind, doc_only in RELATION_ROWS:
                sql = "SELECT COUNT(*) FROM kg_edges WHERE kind = ?" + (" AND src NOT LIKE '%:sec:%'" if doc_only else "")
                out[kind] = int(conn.execute(sql, (kind,)).fetchone()[0])
    except Exception:
        out = {}
    _cache.update(at=time.time(), edges=out)
    return out


def _latest(eval_dir: Path, pattern: str) -> tuple[dict | None, str]:
    files = sorted(eval_dir.glob(pattern), key=lambda p: p.stat().st_mtime) if eval_dir.is_dir() else []
    if not files:
        return None, ""
    try:
        return json.loads(files[-1].read_text(encoding="utf-8")), files[-1].name
    except (OSError, ValueError):
        return None, files[-1].name


def snapshot(db, eval_dir: Path) -> dict:
    edges = _edge_counts(db)
    rel, rel_file = _latest(eval_dir, "graph_relation_probe_*.json")
    rows = []
    if rel:
        name, model = (rel.get("name") or {}).get("summary") or {}, (rel.get("model") or {}).get("summary") or {}
        for key, label in (("plan_report", "계획 대비 실적 — 계획서·실적보고서 둘 다"), ("lineage", "앞·뒤 단계·편입 사업"),
                           ("evaluates", "평가 의견")):
            if key in name or key in model:
                rows.append({"label": label, "n": (model.get(key) or name.get(key) or {}).get("n"),
                             "name": (name.get(key) or {}).get("ok"), "model": (model.get(key) or {}).get("ok")})
        rows.append({"label": "전체", "n": model.get("n") or name.get("n"), "name": name.get("ok"), "model": model.get("ok")})
        timing = {"name": name.get("sec_median"), "model": model.get("sec_median")}
    else:
        timing = {}
    return {
        "relations": [{"label": lab, "kind": kind, "n": edges.get(kind)} for lab, kind, _ in RELATION_ROWS],
        "probe": rows, "probe_file": rel_file, "probe_at": (rel or {}).get("made", ""), "timing": timing,
        "model_read_on": os.environ.get("ZZAIMY_GRAPH_READ", "0") == "1",
        "grant_rerank_on": os.environ.get("ZZAIMY_GRANT_RERANK", "0") == "1",
    }
