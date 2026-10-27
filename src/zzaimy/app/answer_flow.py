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
    from zzaimy.graph import kg_store
    if _cache["edges"] is not None and time.time() - _cache["at"] < 600 and _cache.get("ver") == kg_store.version(db):
        return _cache["edges"]
    out: dict[str, int] = {}
    try:
        with db._conn() as conn:
            for _, kind, doc_only in RELATION_ROWS:
                sql = "SELECT COUNT(*) FROM kg_edges WHERE kind = ?" + (" AND src NOT LIKE '%:sec:%'" if doc_only else "")
                out[kind] = int(conn.execute(sql, (kind,)).fetchone()[0])
    except Exception:
        out = {}
    _cache.update(at=time.time(), edges=out, ver=kg_store.version(db))
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


# 세 방식 — ① 그래프 없이 전체에서 찾기 ② 그래프로 좁히되 질문 해석은 코드(이름·연도·갈래 낱말 맞추기) ③ 질문 해석을 27B 가(운영)
COMPARE_MODES = (("fulltext", "① 풀텍스트 RAG — 그래프 없이"), ("graph", "② GraphRAG — 질문 해석: 코드(낱말 맞추기)"),
                 ("graph_read", "③ GraphRAG — 질문 해석: 27B"))


def adopted_mode() -> str:
    """지금 운영이 쓰는 방식 — 27B 질문 읽기(ZZAIMY_GRAPH_READ)를 켰으면 ③, 아니면 ②(그래프 단계는 늘 켜짐)."""
    return "graph_read" if os.environ.get("ZZAIMY_GRAPH_READ", "0") == "1" else "graph"


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
        # 비교 표는 풀텍스트 RAG 와 GraphRAG 를 같은 시험지로 함께 잰 파일만 — 운영 방식 하나만 다시 잰 측정(모델 교체 점검 195 등)은
        # 아래 「최근 단독 측정」 줄로 따로 보인다(시험지가 다를 수 있어 같은 표에 섞지 않는다)
        if len(modes) >= 2 and "fulltext" in modes and all(((summ[m].get("search") or {}).get("section") or {}).get("n")
                         and ((summ[m].get("search") or {}).get("relation") or {}).get("n")
                         and (summ[m].get("answer") or {}).get("n") for m in modes):
            return data, f.name
    return None, ""


def _raw(v, kind):
    if v is None:
        return None
    if kind == "cnt":
        return v.get("rate") if isinstance(v, dict) and v.get("n") else None
    return v if isinstance(v, (int, float)) else None


def _diff(a, b, kind: str, lower_better: bool = False) -> dict | None:
    """첫 방식(풀텍스트) → 마지막 방식(운영 GraphRAG) 차이 — 비율은 %p, 시간은 초. good: 나아졌나."""
    x, y = _raw(a, kind), _raw(b, kind)
    if x is None or y is None:
        return None
    d = y - x
    if kind in ("cnt", "ratio"):
        text = f"{d * 100:+.0f}%p" if kind == "cnt" else f"{d * 100:+.1f}%p"
    elif kind == "sec":
        text = f"{d:+.1f}초"
    else:
        text = f"{d:+.2f}"
    return {"text": text, "good": (d < 0) if lower_better else (d > 0), "zero": abs(d) < 1e-9}


def _latest_single(eval_dir: Path, compare_file: str) -> dict | None:
    """비교 표보다 나중에 운영 방식 하나만 잰 측정 — 한 줄 요약(시험지·주요 값)."""
    files = sorted(eval_dir.glob("graphrag_vs_fulltext_*.json"), key=lambda p: p.stat().st_mtime, reverse=True) \
        if eval_dir.is_dir() else []
    for f in files:
        if f.name == compare_file:
            return None
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        summ = data.get("summary") or {}
        m = next((m for m, _ in COMPARE_MODES if m in summ), None)
        if not m:
            continue
        s = summ[m]
        sec = (((s.get("search") or {}).get("section") or {}).get("counts") or {}).get("doc_hit")
        ans = s.get("answer") or {}
        return {"made": data.get("made", ""), "file": f.name, "mode": dict(COMPARE_MODES)[m],
                "set": ", ".join(str(v) for v in (data.get("sets") or {}).values()),
                "vals": [("절 질문 정답 문서 상위 5", _cell(sec, "cnt")),
                         ("답이 정답 문서를 근거로", _cell(ans.get("evidence_has_gold_counts"), "cnt")),
                         ("답 시간(중앙값)", _cell(ans.get("wall_sec_median"), "sec")),
                         ("근거 밖 숫자 비율", _cell(ans.get("unsupported_number_ratio"), "ratio"))]}
    return None


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
        ("검색", "특정 문서의 한 부분을 묻는 질문 — 그 문서가 상위 5건에", ("search", "section", "counts", "doc_hit"), "cnt"),
        ("검색", "  같은 질문 — 같은 사업·같은 해의 거의 같은 문서까지 인정", ("search", "section", "counts", "twin_hit"), "cnt"),
        ("검색", "  같은 질문 — 같은 사업 문서면 인정", ("search", "section", "counts", "prog_hit"), "cnt"),
        ("검색", "관계를 묻는 질문 — 관계 양쪽 문서가 모두 상위에", ("search", "relation", "counts"), "cnt"),
        ("검색", "  계획서와 그 결과보고서", ("search", "relation", "plan_report", "counts"), "cnt"),
        ("검색", "  전신·후속 사업", ("search", "relation", "lineage", "counts"), "cnt"),
        ("검색", "  평가 결과와 평가받은 문서", ("search", "relation", "evaluates", "counts"), "cnt"),
        ("시간", "검색 한 번 — 보통(중앙값)", ("search", "sec_median"), "sec"),
        ("시간", "검색 한 번 — 느린 쪽(10건 중 9번째)", ("search", "sec_p90"), "sec"),
        ("시간", "답이 나오기까지 — 보통(중앙값)", ("answer", "wall_sec_median"), "sec"),
        ("시간", "답이 나오기까지 — 느린 쪽(10건 중 9번째)", ("answer", "wall_sec_p90"), "sec"),
        ("시간", "  그중 근거 문서 찾기(규정·사업 문서)", ("answer", "search_sec_median"), "sec"),
        ("시간", "  그중 27B 가 답을 쓰는 시간", ("answer", "generate_sec_median"), "sec"),
        ("답변", "답이 정답 문서를 근거로 썼나", ("answer", "evidence_has_gold_counts"), "cnt"),
        ("답변", "정답 문서의 숫자가 답에 들어간 비율(평균)", ("answer", "fact_recall_mean"), "ratio"),
        ("답변", "근거 문서에 없는 숫자가 답에 나온 비율(낮을수록 좋음)", ("answer", "unsupported_number_ratio"), "ratio"),
        ("답변", "관계를 묻는 질문 — 답이 양쪽 문서를 근거로 썼나", ("answer", "relation_both_sides"), "cnt"),
        ("답변", "답 채점 — 완전히 맞은 답(2점) 비율", ("answer", "judge", "correct_full"), "cnt"),
        ("답변", "답 채점 — 평균 점수(0~2점, 어느 방식 답인지 가리고 매김)", ("answer", "judge", "correct_mean"), "num"),
    )
    rows = []
    better_low = ("시간", "근거 문서에 없는 숫자")
    for group, label, path, kind in spec:
        vals = [get(m, *path) for m, _ in modes]
        if all(v is None for v in vals):
            continue
        rows.append({"group": group, "label": label, "vals": [_cell(v, kind) for v in vals], "path": path, "kind": kind,
                     "raw": vals,
                     "diff": _diff(vals[0], vals[-1], kind, lower_better=group == "시간" or label.startswith(better_low[1]))})
    ans_n = {lab: get(m, "answer", "n") for m, lab in modes}
    return {"file": name, "made": data.get("made", ""), "modes": [lab for _, lab in modes], "rows": rows,
            "diff_label": f"차이 ({'①→' + '②③'[len(modes) - 2]})", "latest": _latest_single(eval_dir, name),
            "adopted": [m == adopted_mode() for m, _ in modes],
            "n_sec": get(modes[0][0], "search", "section", "n"), "n_rel": get(modes[0][0], "search", "relation", "n"),
            "n_ans": max([v for v in ans_n.values() if v] or [0]) or None, "summary": _why_line(summ),
            "keys": [m for m, _ in modes]}


# 품질·성능 첫 화면의 짧은 표 — 결과 파일의 같은 칸을 짧은 이름으로 고르기만 한다(수치는 새로 만들지 않는다)
MODE_SHORT = {"fulltext": "풀텍스트", "graph": "GraphRAG(코드)", "graph_read": "GraphRAG(27B)"}
KEY_ROWS = (
    (("search", "section", "counts", "doc_hit"), "문서 한 부분 질문 — 정답 문서가 상위 5건에"),
    (("search", "relation", "counts"), "관계 질문 — 양쪽 문서가 모두 상위에"),
    (("answer", "evidence_has_gold_counts"), "답이 정답 문서를 근거로"),
    (("answer", "judge", "correct_mean"), "답 채점 평균(0~2점)"),
    (("answer", "wall_sec_median"), "답이 나오기까지(보통)"),
)


def _short(v, kind: str) -> str:
    if kind == "cnt":
        return f"{v['rate'] * 100:.0f}%" if isinstance(v, dict) and v.get("n") else "—"
    return _cell(v, kind)


def overview(compare: dict) -> dict:
    """첫 화면 카드·타일 — 풀텍스트와 지금 운영 방식 두 칸만, 핵심 줄 다섯까지. 비교 측정이 없으면 빈 dict."""
    if not compare or "fulltext" not in compare.get("keys", []):
        return {}
    keys = compare["keys"]
    op = keys.index(adopted_mode()) if adopted_mode() in keys else len(keys) - 1
    by_path = {r["path"]: r for r in compare["rows"]}
    rows = []
    for path, label in KEY_ROWS:
        r = by_path.get(path)
        if not r:
            continue
        a, b = r["raw"][0], r["raw"][op]
        rows.append({"label": label, "full": _short(a, r["kind"]), "op": _short(b, r["kind"]),
                     "diff": _diff(a, b, r["kind"], lower_better=r["group"] == "시간")})
    return {"made": compare.get("made", ""), "op_label": MODE_SHORT.get(keys[op], compare["modes"][op]),
            "full_label": MODE_SHORT["fulltext"], "rows": rows, "head": rows[0] if rows else None}


def _why_line(summ: dict) -> list[str]:
    """비교 요약 — 항목마다 세 방식의 값을 나란히(①·②·③). 화살표로 잇지 않는다(읽기 어렵다). 값은 결과 파일 그대로."""
    modes = [(m, lab) for m, lab in COMPARE_MODES if m in summ]
    if len(modes) < 2 or modes[0][0] != "fulltext":
        return []

    def r(s, *path):
        v = s
        for k in path:
            v = v.get(k) if isinstance(v, dict) else None
        return v

    marks = "①②③"
    pct = lambda v: f"{v * 100:.0f}%"                       # noqa: E731
    sec = lambda v: f"{v}초"                                 # noqa: E731
    lines = []
    for label, path, f in (
            ("특정 문서의 한 부분을 묻는 질문 — 그 문서가 상위 5건에", ("search", "section", "doc_at_k"), pct),
            ("관계를 묻는 질문 — 관계 양쪽 문서가 모두 상위에", ("search", "relation", "ok"), pct),
            ("답 채점 평균(0~2점, 어느 방식 답인지 가리고 매김)", ("answer", "judge", "correct_mean"), str),
            ("답이 나오기까지(보통)", ("answer", "wall_sec_median"), sec)):
        vals = [r(summ[m], *path) for m, _ in modes]
        if all(v is not None for v in vals):
            lines.append(f"{label}: " + " · ".join(f"{marks[i]} {f(v)}" for i, v in enumerate(vals)))
    return lines
