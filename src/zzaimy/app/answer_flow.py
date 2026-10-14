"""사업 문서 검색 측정 — 품질·성능 「사업 문서 검색(GraphRAG)」 카드: 관계 질문 측정과 풀텍스트 RAG 비교.

수치는 모두 기계 산출물에서 읽는다 — 관계 수는 그래프(kg_edges), 관계 질문 측정은 scripts/184 결과 파일,
일반 질문 측정은 scripts/173 결과 파일, GraphRAG 대 풀텍스트 RAG 비교는 scripts/188 결과 파일. 파일이 없으면 「측정 전」 으로 둔다.
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
        "compare": compare_rows(eval_dir),
        "relations": [{"label": lab, "kind": kind, "n": edges.get(kind)} for lab, kind, _ in RELATION_ROWS],
        "probe": rows, "probe_file": rel_file, "probe_at": (rel or {}).get("made", ""), "timing": timing,
        "model_read_on": os.environ.get("ZZAIMY_GRAPH_READ", "0") == "1",
        "grant_rerank_on": os.environ.get("ZZAIMY_GRANT_RERANK", "0") == "1",
    }


COMPARE_MODES = (("fulltext", "풀텍스트 RAG"), ("graph", "온톨로지+RAG"), ("graph_read", "+27B 읽기"))


def _cell(v, kind: str) -> str:
    if v is None:
        return "—"
    if kind == "cnt":                                   # {"k","n","rate","ci95"} → 「62% (41/66, 50~73%)」
        if not v.get("n"):
            return "—"
        ci = v.get("ci95") or [None, None]
        band = f", {ci[0] * 100:.0f}~{ci[1] * 100:.0f}%" if ci[0] is not None else ""
        return f"{v['rate'] * 100:.0f}% ({v['k']}/{v['n']}{band})"
    if kind == "sec":
        return f"{v}초"
    if kind == "ratio":
        return f"{v * 100:.1f}%"
    return str(v)


def _latest_complete(eval_dir: Path) -> tuple[dict | None, str]:
    """가장 최근의 「온전한」 비교 측정 — 세 방식 모두 절·관계 질문 검색과 답변까지 잰 파일.
    한 갈래만 다시 잰 측정(예: 관계 질문만 --only)이 더 최근이어도 표를 비우지 않는다."""
    files = sorted(eval_dir.glob("graphrag_vs_fulltext_*.json"), key=lambda p: p.stat().st_mtime, reverse=True) \
        if eval_dir.is_dir() else []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        summ = data.get("summary") or {}
        modes = [m for m, _ in COMPARE_MODES if m in summ]
        if modes and all(((summ[m].get("search") or {}).get("section") or {}).get("n")
                         and ((summ[m].get("search") or {}).get("relation") or {}).get("n")
                         and (summ[m].get("answer") or {}).get("n") for m in modes):
            return data, f.name
    return None, ""


def compare_rows(eval_dir: Path) -> dict:
    """scripts/188 결과(graphrag_vs_fulltext_*.json 가장 최근) → 화면 표와 한 줄 요약. 값은 파일의 summary 를 그대로 옮긴다."""
    data, name = _latest_complete(eval_dir)
    if not data or not data.get("summary"):
        return {}
    summ = data["summary"]
    modes = [(m, lab) for m, lab in COMPARE_MODES if m in summ]
    if not modes:
        return {}

    def get(m, *path):
        v = summ[m]
        for k in path:
            v = v.get(k) if isinstance(v, dict) else None
        return v
    spec = (
        ("검색", "절 질문 — 정답 문서가 근거 상위에", ("search", "section", "counts", "doc_hit"), "cnt"),
        ("검색", "절 질문 — 같은 사업·같은 해 쌍둥이 문서", ("search", "section", "counts", "twin_hit"), "cnt"),
        ("검색", "절 질문 — 같은 사업 문서", ("search", "section", "counts", "prog_hit"), "cnt"),
        ("검색", "관계 질문 — 관계 양쪽 근거", ("search", "relation", "counts"), "cnt"),
        ("검색", "  계획서·실적보고서 둘 다", ("search", "relation", "plan_report", "counts"), "cnt"),
        ("검색", "  앞·뒤 단계 사업", ("search", "relation", "lineage", "counts"), "cnt"),
        ("검색", "  평가 의견", ("search", "relation", "evaluates", "counts"), "cnt"),
        ("시간", "검색 한 번 중앙값", ("search", "sec_median"), "sec"),
        ("시간", "검색 한 번 p90", ("search", "sec_p90"), "sec"),
        ("시간", "답변까지 전체 중앙값", ("answer", "wall_sec_median"), "sec"),
        ("시간", "답변까지 전체 p90", ("answer", "wall_sec_p90"), "sec"),
        ("시간", "  그중 근거 찾기(규정+사업 문서) 중앙값", ("answer", "search_sec_median"), "sec"),
        ("시간", "  그중 27B 생성 중앙값", ("answer", "generate_sec_median"), "sec"),
        ("답변", "답의 근거에 정답 문서", ("answer", "evidence_has_gold_counts"), "cnt"),
        ("답변", "정답 절 숫자 사실 재현율(평균)", ("answer", "fact_recall_mean"), "ratio"),
        ("답변", "근거 밖 수치 비율", ("answer", "unsupported_number_ratio"), "ratio"),
        ("답변", "관계 질문 — 답의 근거에 양쪽 문서", ("answer", "relation_both_sides"), "cnt"),
        ("답변", "눈가림 판정 — 정답(2점) 비율", ("answer", "judge", "correct_full"), "cnt"),
        ("답변", "눈가림 판정 — 정답성 평균(0~2)", ("answer", "judge", "correct_mean"), "num"),
    )
    rows = []
    for group, label, path, kind in spec:
        vals = [get(m, *path) for m, _ in modes]
        if all(v is None for v in vals):
            continue
        rows.append({"group": group, "label": label, "vals": [_cell(v, kind) for v in vals]})
    ans_n = {lab: get(m, "answer", "n") for m, lab in modes}
    return {"file": name, "made": data.get("made", ""), "modes": [lab for _, lab in modes], "rows": rows,
            "n_sec": get(modes[0][0], "search", "section", "n"), "n_rel": get(modes[0][0], "search", "relation", "n"),
            "n_ans": max([v for v in ans_n.values() if v] or [0]) or None, "summary": _why_line(summ)}


def _why_line(summ: dict) -> str:
    """「왜 그래프를 쓰나」 한 줄 — 모드마다(풀텍스트 → 온톨로지+RAG → +27B 읽기) 질문 갈래별 차이와 지연 비용을 결과 파일 수치로 만든다."""
    modes = [(m, lab) for m, lab in COMPARE_MODES if m in summ]
    if len(modes) < 2 or modes[0][0] != "fulltext":
        return ""

    def r(s, *path):
        v = s
        for k in path:
            v = v.get(k) if isinstance(v, dict) else None
        return v

    def chain(path, fmt):
        vals = [r(summ[m], *path) for m, _ in modes]
        return " → ".join(fmt(v) for v in vals) if all(v is not None for v in vals) else ""
    pct = lambda v: f"{v * 100:.0f}%"                       # noqa: E731
    sec = lambda v: f"{v}초"                                 # noqa: E731
    parts = [f"{label} {c}" for label, path, f in (
        ("절 질문 정답 문서", ("search", "section", "doc_at_k"), pct),
        ("관계 양쪽 근거", ("search", "relation", "ok"), pct),
        ("앞·뒤 단계 사업", ("search", "relation", "lineage", "ok"), pct),
        ("평가 의견", ("search", "relation", "evaluates", "ok"), pct),
        ("계획·실적 둘 다", ("search", "relation", "plan_report", "ok"), pct),
        ("답변 판정 정답성(0~2)", ("answer", "judge", "correct_mean"), str),
        ("검색 한 번", ("search", "sec_median"), sec),
        ("답변까지", ("answer", "wall_sec_median"), sec)) if (c := chain(path, f))]
    return (" → ".join(lab for _, lab in modes) + ": " + " · ".join(parts)) if parts else ""
