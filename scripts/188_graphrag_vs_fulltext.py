#!/usr/bin/env python3
"""GraphRAG(온톨로지 + RAG) 대 풀텍스트 RAG — 같은 시험지로 검색 정확도·답변 시간·답변 품질을 견준다.

모드(grant_search.graph_mode, 운영 기본은 바꾸지 않는다):
- fulltext   : 그래프 단계를 끈다 — 사업 좁히기·그래프 절·관계 근거 없이 사업 문서 전체 조각에서 어휘+임베딩(RRF)만
- graph      : 운영 경로 — 그래프 단계(이름 맞추기)로 사업·연차·갈래를 좁히고 관계 근거를 담은 뒤 같은 RAG
- graph_read : graph + 27B 질문 읽기(graph.question_read, ZZAIMY_GRAPH_READ=1 과 같은 일)
재순위(ZZAIMY_GRANT_RERANK)는 운영 설정을 그대로 따른다 — 세 모드가 같다.

시험지는 사람이 고르지 않는다. 이미 고정해 둔 두 집합을 그대로 쓴다(측정끼리 같은 시험지).
- 절 질문: data/eval/rag_probe_set.json (scripts/173 — 계획서·실적보고서의 절을 무작위로 골라 만든 질문, 정답 문서·절 글)
- 관계 질문: data/eval/graph_relation_set.json (scripts/184 — 그래프의 계획↔실적·앞뒤 단계·평가 관계에서 뽑은 질문)
재는 것:
- 검색(모든 질문 × 모드): 절 질문 doc@k·twin@k·prog@k·정답 문서 순위, 관계 질문 ok(정답 문서 조건)·계획/실적 각각,
  계획↔실적 짝 성립(pair_any — 시험지 짝이 여럿 중 하나라 함께 본다, pair_sides), 걸린 시간
- 답변(앞에서 고른 일부 × --answer-modes): AgentResponder 를 그대로 — 단계별 시간(규정·사업 문서 검색·생성·전체),
  빈 답·오류, 근거 없음 답, 근거에 없는 숫자(지어낸 수치 의심), 정답 절 글과 겹치는 낱말 비율(절 질문), 근거에 정답 문서가 들었나
- 답 품질(quality): 정답 절 숫자 사실 재현율, 근거 밖 수치 비율, 근거에 정답 문서, 관계 양쪽 근거·계획과 실적을 다 다뤘나
- 눈가림 판정 묶음: 질문마다 모드별 답을 무작위로 「가·나·다」에 놓고 정답 자료와 함께 graphrag_blind_<tag>.json 에,
  열쇠는 graphrag_blind_key_<tag>.json 에 따로. 판정 결과는 --judge·--judge-key 로 다시 붙인다(--reuse 로 다시 돌지 않고)
같은 질문에서 모드를 번갈아 돌린다(서버 부하가 한 모드에만 몰리지 않게). 27B 질문 읽기 캐시는 매번 비운다(시간이 실제와 같게).
출력: data/eval/graphrag_vs_fulltext_<tag>.json 과 요약 한 줄(GRAPHRAG_VS), 표(마크다운).

답 생성 변형 축(--think-compare): 검색은 운영 모드(graph_read) 하나로 두고, 같은 질문의 답 쓰기를 생각 끔(think_off, 운영
현재)과 켬(think_on, ZZAIMY_ANSWER_THINK=1)으로 번갈아(질문마다 먼저 도는 쪽을 바꿔) 돌린다. 생성·전체 시간·출력 토큰·빈 답·
근거 밖 수치·정답 사실 재현율과 눈가림 묶음(가·나)을 낸다. 판정은 답마다 {"score": 0~2} — 붙이면 모드별 평균과 짝 비교(나아짐·
나빠짐·같음, 켬 기준)를 함께 낸다. 출력: data/eval/graphrag_think_<tag>.json · 요약 한 줄(GRAPHRAG_THINK)

사용(VM, .env.local 필수):
  scripts/zz_run.sh 16G env PYTHONPATH=src .venv/bin/python scripts/188_graphrag_vs_fulltext.py --tag v1 --answer-sec 12 --answer-rel 12
  scripts/zz_run.sh 16G env PYTHONPATH=src .venv/bin/python scripts/188_graphrag_vs_fulltext.py --think-compare --tag think1 \
      --answer-sec 12 --answer-rel 12 --limit 12      # 판정 뒤: --reuse <결과> --judge <판정> --judge-key <열쇠>
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402

MODES = ("fulltext", "graph", "graph_read")
LABEL = {"fulltext": "A 풀텍스트 RAG", "graph": "B 온톨로지+RAG", "graph_read": "C B+27B 읽기"}
THINK = ("think_off", "think_on")                 # --think-compare 의 답 생성 변형(검색은 graph_read)
THINK_LABEL = {"think_off": "생각 끔(운영)", "think_on": "생각 켬"}
_NUM = re.compile(r"(?<![\d.])\d{2,}(?:[,.]\d+)*")
_DATE = re.compile(r"((?:19|20)\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
_FACT = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d{2,})(?:\.\d+)?")   # 1,200 · 70 · 3.5 — 천 단위 쉼표를 한 수로
_WORD = re.compile(r"[가-힣A-Za-z0-9]{2,}")
_NO_EVID = re.compile(r"(?:근거|자료|내용|정보)[를을가이]?\s*(?:찾지\s*못|찾을\s*수\s*없|확인(?:되지|할\s*수)\s*(?:않|없))|관련\s*근거를\s*찾지")


def _date(g) -> tuple[int, int, int]:
    return int(g[0]), int(g[1]), int(g[2])


def unsupported_numbers(question: str, answer: str, ctx: str) -> list[str]:
    """답의 숫자 가운데 질문에도, 모델에 건넨 글에도 없는 것(scripts/173 과 같은 판정).
    답이 이미 「(근거 확인 필요)」를 붙여 알린 수치와 그 안내 줄은 세지 않는다 — 담당자에게 확인을 맡긴 수치라 몰래 섞인 수치가 아니다."""
    answer = re.sub(r"\n*※ 근거 문서에서 찾지 못한 수치[^\n]*", "", answer or "")
    answer = re.sub(r"[0-9][0-9.,]*\s*[%가-힣]?\(근거 확인 필요\)", "", answer)
    qn = set(_NUM.findall(question))
    ctx_digits = re.sub(r"\D", "", ctx)
    ctx_dates = {_date(m) for m in _DATE.findall(ctx)}
    return [x for x in _NUM.findall(answer) if x not in qn and x not in ctx
            and re.sub(r"\D", "", x) not in ctx_digits
            and not (_DATE.fullmatch(x) and _date(_DATE.fullmatch(x).groups()) in ctx_dates)]


def load_sets(sec_path: Path, rel_path: Path, named: bool = False) -> list[dict]:
    """named: 절 질문 가운데 같은 제목의 절을 가진 문서가 여럿인 것은 문서 이름을 넣은 질문(scripts/173 q_named)으로."""
    items: list[dict] = []
    for i, x in enumerate(json.loads(sec_path.read_text(encoding="utf-8"))["items"]):
        q = x.get("q_named") or x["q"] if named else x["q"]
        items.append({"id": f"sec{i:03d}", "set": "section", "type": "section", "q": q, "doc_id": x["doc_id"],
                      "twins": set(x["twins"]), "prog_docs": set(x["prog_docs"]), "gold_text": x.get("gold", "")})
    for i, x in enumerate(json.loads(rel_path.read_text(encoding="utf-8"))["items"]):
        items.append({"id": f"rel{i:03d}", "set": "relation", "type": x["type"], "q": x["q"], "gold": list(x["gold"]),
                      "any": bool(x.get("any"))})
    return items


def pick_answer_items(items: list[dict], n_sec: int, n_rel: int) -> list[dict]:
    """답변을 받을 질문 — 절 질문은 앞에서(집합이 이미 무작위 순서), 관계 질문은 갈래를 번갈아(갈래가 고루 들게)."""
    sec = [x for x in items if x["set"] == "section"][:n_sec]
    by_type: dict[str, list[dict]] = {}
    for x in items:
        if x["set"] == "relation":
            by_type.setdefault(x["type"], []).append(x)
    rel: list[dict] = []
    while len(rel) < n_rel and any(by_type.values()):
        for t in list(by_type):
            if by_type[t] and len(rel) < n_rel:
                rel.append(by_type[t].pop(0))
    return sec + rel


def pair_sides(db, it: dict) -> None:
    """계획↔실적 질문의 「짝 성립」 묶음을 붙인다(제자리) — plan_alts·report_alts.

    시험지의 짝은 그래프의 문서 짝(plans_reports) 가운데 하나를 무작위로 고른 것이다. 문서 짝은 같은 사업·연차의 계획서 ×
    실적보고서를 모두 잇기 때문에, 한 연차에 같은 절을 가진 계획서 판본이 여럿이고 실적보고서도 여럿이면 질문 글만으로는
    시험지가 고른 두 문서를 가릴 수 없다(엄격 지표 ok 는 운에 크게 기댄다). 그래서 함께 본다:
    계획 쪽 = 시험지 계획서, 또는 시험지 실적보고서와 그래프 짝이면서 질문에 묶어 적은 절 제목의 절을 가진 계획서.
    실적 쪽 = 시험지 실적보고서, 또는 시험지 계획서와 그래프 짝인 실적보고서."""
    if it.get("type") != "plan_report":
        return
    pa, pb = int(it["gold"][0]), int(it["gold"][1])
    m = re.findall(r"「([^」]{2,60})」", it["q"])
    sec = re.sub(r"\s+", "", m[-1]) if m else ""
    with db._conn() as conn:
        plans = {int(a.split(":")[1]) for (a,) in conn.execute(
            "SELECT src FROM kg_edges WHERE kind = 'plans_reports' AND dst = ? AND src NOT LIKE '%:sec:%'", (f"doc:{pb}",)).fetchall()}
        reports = {int(b.split(":")[1]) for (b,) in conn.execute(
            "SELECT dst FROM kg_edges WHERE kind = 'plans_reports' AND src = ? AND dst NOT LIKE '%:sec:%'", (f"doc:{pa}",)).fetchall()}
        with_sec: set[int] = set()
        if sec and plans:
            ids = sorted(plans)
            with_sec = {int(d) for (d,) in conn.execute(
                f"SELECT DISTINCT doc_id FROM kg_nodes WHERE type = 'section' AND doc_id IN ({','.join('?' * len(ids))})"
                " AND REPLACE(label, ' ', '') LIKE ?", (*ids, f"%{sec}%")).fetchall()}
    it["plan_alts"] = sorted(with_sec | {pa})
    it["report_alts"] = sorted(reports | {pb})


def judge_search(it: dict, got: list[int]) -> dict:
    if it["set"] == "section":
        rank = got.index(it["doc_id"]) + 1 if it["doc_id"] in got else None
        return {"doc_hit": it["doc_id"] in got, "twin_hit": bool(it["twins"] & set(got)),
                "prog_hit": any(h in it["prog_docs"] for h in got), "rank": rank}
    gold = set(it["gold"])
    ok = bool(gold & set(got)) if it["any"] else gold <= set(got)
    out = {"ok": ok}
    if it["type"] == "plan_report":
        out.update(plan_ok=it["gold"][0] in got, report_ok=it["gold"][1] in got)
        if "plan_alts" in it:
            pa_ok, rb_ok = bool(set(it["plan_alts"]) & set(got)), bool(set(it["report_alts"]) & set(got))
            out.update(plan_any=pa_ok, report_any=rb_ok, pair_any=pa_ok and rb_ok)
    return out


def _clear_reader_cache() -> None:
    try:
        from zzaimy.graph import question_read
        with question_read._lock:
            question_read._cache.clear()
    except Exception:
        pass


def run_search(db, it: dict, mode: str, k_sec: int, k_rel: int) -> dict:
    from zzaimy.app import grant_search
    _clear_reader_cache()
    k = k_sec if it["set"] == "section" else k_rel
    t0 = time.time()
    err = ""
    try:
        with grant_search.graph_mode(mode):
            g = grant_search.search(db, it["q"], k=k, user=None)
    except Exception as e:                                    # 검색 실패도 결과다
        g, err = {"hits": [], "steps": []}, f"{type(e).__name__}: {e}"[:200]
    got = [h["doc_id"] for h in g["hits"]]
    return {"got": got, "sec": round(time.time() - t0, 2), "error": err, "steps": g.get("steps", [])[:6],
            "relation_added": sum(1 for h in g["hits"] if h.get("relation")), **judge_search(it, got)}


def run_answer(db, it: dict, mode: str, think: bool | None = None) -> dict:
    """think: None 이면 환경 그대로, True·False 면 이 답 한 번만 ZZAIMY_ANSWER_THINK 를 그렇게 둔다(--think-compare)."""
    from zzaimy.app import grant_search
    from zzaimy.app.responder import AgentResponder
    _clear_reader_cache()
    ar = AgentResponder()
    old_think = os.environ.get("ZZAIMY_ANSWER_THINK")
    if think is not None:
        os.environ["ZZAIMY_ANSWER_THINK"] = "1" if think else "0"
    t0 = time.time()
    try:
        with grant_search.graph_mode(mode):
            ans = ar.answer(db, it["q"], scope={"user": None})
        err = ""
    except Exception as e:
        ans, err = "", f"{type(e).__name__}: {e}"[:200]
    finally:
        if think is not None:
            if old_think is None:
                os.environ.pop("ZZAIMY_ANSWER_THINK", None)
            else:
                os.environ["ZZAIMY_ANSWER_THINK"] = old_think
    wall = round(time.time() - t0, 2)
    ctx = getattr(ar, "last_context", "") or ""
    used = [d for d in (getattr(ar, "last_grant_docs", []) or []) if d is not None]
    row = {"answer": ans[:2500], "error": err, "wall_sec": wall, "timing": dict(getattr(ar, "last_timing", {}) or {}),
           "evidence_docs": used, "empty": len(ans.strip()) < 20, "no_evidence": bool(_NO_EVID.search(ans[:400])),
           "unsupported_numbers": unsupported_numbers(it["q"], ans, ctx)[:10] if ans else [],
           "flagged_numbers": len(re.findall(r"\(근거 확인 필요\)", ans or ""))}
    if it["set"] == "section":
        gw, aw = set(_WORD.findall(it["gold_text"])), set(_WORD.findall(ans))
        row["gold_overlap"] = round(len(gw & aw) / max(len(aw), 1), 3)
        row["evidence_has_gold"] = it["doc_id"] in used or bool(it["twins"] & set(used))
    else:
        gold = set(it["gold"])
        row["evidence_has_gold"] = bool(gold & set(used)) if it["any"] else gold <= set(used)
    return row


def _digits(x: str) -> str:
    return re.sub(r"\D", "", x)


def gold_facts(text: str, question: str, limit: int = 40) -> list[str]:
    """정답 절의 핵심 사실 — 숫자(2자리 이상, 날짜·금액·비율·건수).
    질문에 있던 숫자(연도 등)는 빼고, 같은 숫자는 한 번만. 이름은 낱말 겹침(gold_overlap)이 따로 본다."""
    qd = {_digits(x) for x in _NUM.findall(question)}
    out: list[str] = []
    for x in _FACT.findall(text or ""):
        d = _digits(x)
        if d and d not in qd and d not in out and not re.fullmatch(r"0+", d):
            out.append(d)
        if len(out) >= limit:
            break
    return out


def quality(it: dict, a: dict) -> dict:
    """답 하나의 품질 — 결과 파일의 답 글과 정답(시험지)에서 다시 계산한다(재사용한 답도 같은 잣대).
    fact_recall: 정답 절 숫자 사실 가운데 답에 나온 비율(사실 단위 재현율, 절 질문)
    num_total·num_unsupported: 답의 숫자 수와 그중 건넨 근거에 없던 수(근거 밖 수치 비율의 분자·분모)
    both_sides: 관계 질문에서 관계 양쪽 문서가 답의 근거에 다 들었나(계획↔실적은 계획서·실적보고서 둘 다)
    mentions_both: 계획↔실적 질문의 답이 계획과 실적을 둘 다 다뤘나(낱말)"""
    ans = a.get("answer") or ""
    qd = {_digits(x) for x in _NUM.findall(it["q"])}
    nums = [x for x in _NUM.findall(ans) if _digits(x) not in qd]
    out = {"num_total": len(nums), "num_unsupported": len(a.get("unsupported_numbers") or [])}
    used = set(a.get("evidence_docs") or [])
    if it["set"] == "section":
        facts = gold_facts(it.get("gold_text", ""), it["q"])
        got = {_digits(x) for x in _FACT.findall(ans)}
        hit = [f for f in facts if f in got]
        out.update(n_facts=len(facts), fact_hits=len(hit), fact_recall=round(len(hit) / len(facts), 3) if facts else None)
    else:
        gold = list(it["gold"])
        if it["type"] == "plan_report":
            out["both_sides"] = set(gold[:2]) <= used
            out["mentions_both"] = bool(re.search(r"계획|목표", ans)) and bool(re.search(r"실적|성과|달성|결과", ans))
        else:
            out["both_sides"] = bool(set(gold) & used)
    return out


def _wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """비율의 95% 윌슨 구간 — 표본이 작을 때 정규 근사보다 낫다."""
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)]


def _cnt(rows: list[dict], key: str) -> dict:
    vals = [bool(r[key]) for r in rows if key in r and r[key] is not None]
    k, n = sum(vals), len(vals)
    return {"k": k, "n": n, "rate": round(k / n, 3) if n else None, "ci95": _wilson(k, n)}


def _rate(rows: list[dict], key: str) -> float | None:
    vals = [bool(r[key]) for r in rows if key in r]
    return round(sum(vals) / len(vals), 3) if vals else None


def _med(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 2) if vals else None


def _p90(vals: list[float]) -> float | None:
    vals = sorted(v for v in vals if v is not None)
    return round(vals[min(len(vals) - 1, int(0.9 * len(vals)))], 2) if vals else None


def summarize(rows: list[dict], mode: str) -> dict:
    srch = [r["search"][mode] for r in rows if mode in r.get("search", {})]
    sec = [r["search"][mode] for r in rows if r["set"] == "section" and mode in r.get("search", {})]
    rel_rows = [r for r in rows if r["set"] == "relation" and mode in r.get("search", {})]
    out: dict = {"search": {
        "n": len(srch), "errors": sum(bool(s["error"]) for s in srch), "empty_hits": sum(not s["got"] for s in srch),
        "sec_median": _med([s["sec"] for s in srch]), "sec_p90": _p90([s["sec"] for s in srch]),
        "section": {"n": len(sec), "doc_at_k": _rate(sec, "doc_hit"), "twin_at_k": _rate(sec, "twin_hit"),
                    "prog_at_k": _rate(sec, "prog_hit"),
                    "counts": {k: _cnt(sec, k) for k in ("doc_hit", "twin_hit", "prog_hit")},
                    "mrr": round(sum(1 / s["rank"] for s in sec if s["rank"]) / len(sec), 3) if sec else None},
        "relation": {"n": len(rel_rows), "ok": _rate([r["search"][mode] for r in rel_rows], "ok"),
                     "counts": _cnt([r["search"][mode] for r in rel_rows], "ok")}}}
    for t in ("plan_report", "lineage", "evaluates"):
        part = [r["search"][mode] for r in rel_rows if r["type"] == t]
        if part:
            d = {"n": len(part), "ok": _rate(part, "ok"), "counts": _cnt(part, "ok")}
            if t == "plan_report":
                d.update(plan=_rate(part, "plan_ok"), report=_rate(part, "report_ok"))
                if any("pair_any" in x for x in part):
                    d.update(pair_any=_rate(part, "pair_any"), plan_any=_rate(part, "plan_any"), report_any=_rate(part, "report_any"),
                             pair_any_counts=_cnt(part, "pair_any"))
            out["search"]["relation"][t] = d
    ans = [r["answers"][mode] for r in rows if mode in r.get("answers", {})]
    if ans:
        ok = [a for a in ans if not a["error"]]
        tm = [a["timing"] for a in ok]
        out["answer"] = {
            "n": len(ans), "errors": sum(bool(a["error"]) for a in ans), "empty": sum(a["empty"] for a in ans),
            "no_evidence": sum(a["no_evidence"] for a in ok),
            "with_unsupported_numbers": sum(bool(a["unsupported_numbers"]) for a in ok),
            "unsupported_numbers_total": sum(len(a["unsupported_numbers"]) for a in ok),
            "evidence_has_gold": _rate(ok, "evidence_has_gold"),
            "gold_overlap_median": _med([a["gold_overlap"] for a in ok if "gold_overlap" in a]),
            "wall_sec_median": _med([a["wall_sec"] for a in ok]), "wall_sec_p90": _p90([a["wall_sec"] for a in ok]),
            "search_sec_median": _med([t.get("search_sec") for t in tm]),
            "grant_sec_median": _med([t.get("grant_sec") for t in tm]),
            "regulation_sec_median": _med([t.get("regulation_sec") for t in tm]),
            "generate_sec_median": _med([t.get("generate_sec") for t in tm]),
            "search_sec_p90": _p90([t.get("search_sec") for t in tm]),
            "generate_sec_p90": _p90([t.get("generate_sec") for t in tm]),
            "evidence_has_gold_counts": _cnt(ok, "evidence_has_gold")}
        qs = [a.get("quality") or {} for a in ok]
        nt, nu = sum(q.get("num_total", 0) for q in qs), sum(q.get("num_unsupported", 0) for q in qs)
        fr = [q for q in qs if q.get("fact_recall") is not None]
        out["answer"].update(
            numbers_total=nt, numbers_unsupported=nu, unsupported_number_ratio=round(nu / nt, 3) if nt else None,
            fact_recall_mean=round(sum(q["fact_recall"] for q in fr) / len(fr), 3) if fr else None,
            fact_recall_median=_med([q["fact_recall"] for q in fr]),
            fact_hits=sum(q.get("fact_hits", 0) for q in fr), facts_total=sum(q.get("n_facts", 0) for q in fr),
            relation_both_sides=_cnt([q for q in qs if "both_sides" in q], "both_sides"),
            plan_report_mentions_both=_cnt([q for q in qs if "mentions_both" in q], "mentions_both"))
        if any(a.get("judge") for a in ok):
            js = [a["judge"] for a in ok if a.get("judge")]
            out["answer"]["judge"] = {"n": len(js), "correct_mean": round(sum(j["correct"] for j in js) / len(js), 3),
                                      "correct_full": _cnt([{"x": j["correct"] == 2} for j in js], "x"),
                                      "complete_mean": round(sum(j.get("complete", 0) for j in js) / len(js), 3)}
        for part in ("section", "relation"):
            sub = [r["answers"][mode] for r in rows if mode in r.get("answers", {}) and r["set"] == part]
            sub = [a for a in sub if not a["error"]]
            if sub:
                out["answer"][part] = {"n": len(sub), "evidence_has_gold": _rate(sub, "evidence_has_gold"),
                                       "with_unsupported_numbers": sum(bool(a["unsupported_numbers"]) for a in sub),
                                       "wall_sec_median": _med([a["wall_sec"] for a in sub])}
    return out


def table(summary: dict) -> str:
    modes = [m for m in MODES if m in summary]
    pct = lambda v: "—" if v is None else f"{v * 100:.1f}%"   # noqa: E731
    num = lambda v: "—" if v is None else f"{v}"                # noqa: E731
    lines = ["| 지표 | " + " | ".join(LABEL[m] for m in modes) + " |", "|---|" + "---|" * len(modes)]

    def row(name, f):
        lines.append(f"| {name} | " + " | ".join(f(summary[m]) for m in modes) + " |")
    row("절 질문 doc@k", lambda s: pct(s["search"]["section"]["doc_at_k"]))
    row("절 질문 twin@k", lambda s: pct(s["search"]["section"]["twin_at_k"]))
    row("절 질문 prog@k", lambda s: pct(s["search"]["section"]["prog_at_k"]))
    row("절 질문 MRR", lambda s: num(s["search"]["section"]["mrr"]))
    row("관계 질문 전체", lambda s: pct(s["search"]["relation"]["ok"]))
    row("계획↔실적 둘 다", lambda s: pct((s["search"]["relation"].get("plan_report") or {}).get("ok")))
    row("  계획 · 실적 각각(시험지 문서)", lambda s: "{} · {}".format(*(pct((s["search"]["relation"].get("plan_report") or {}).get(k))
                                                                for k in ("plan", "report"))))
    row("계획↔실적 짝 성립(같은 절 계획서 + 짝 실적보고서)", lambda s: pct((s["search"]["relation"].get("plan_report") or {}).get("pair_any")))
    row("앞·뒤 단계 사업", lambda s: pct((s["search"]["relation"].get("lineage") or {}).get("ok")))
    row("평가 의견", lambda s: pct((s["search"]["relation"].get("evaluates") or {}).get("ok")))
    row("검색 시간 중앙값(초)", lambda s: num(s["search"]["sec_median"]))
    row("검색 시간 p90(초)", lambda s: num(s["search"]["sec_p90"]))
    if any("answer" in summary[m] for m in modes):
        a = lambda s, k: (s.get("answer") or {}).get(k)          # noqa: E731
        row("답변 수", lambda s: num(a(s, "n")))
        row("답변 전체 시간 중앙값(초)", lambda s: num(a(s, "wall_sec_median")))
        row("  그중 검색(규정+사업 문서)", lambda s: num(a(s, "search_sec_median")))
        row("  그중 생성(27B)", lambda s: num(a(s, "generate_sec_median")))
        row("근거에 정답 문서", lambda s: pct(a(s, "evidence_has_gold")))
        row("정답 절 낱말 겹침 중앙값", lambda s: num(a(s, "gold_overlap_median")))
        row("정답 절 숫자 사실 재현율(평균)", lambda s: num(a(s, "fact_recall_mean")))
        row("근거 밖 수치 비율", lambda s: f"{a(s, 'numbers_unsupported')}/{a(s, 'numbers_total')}" if a(s, "n") else "—")
        row("관계 양쪽 근거(답변)", lambda s: (lambda c: f"{c['k']}/{c['n']}" if c else "—")(a(s, "relation_both_sides")))
        row("판정 정답성 평균(0~2)", lambda s: num((a(s, "judge") or {}).get("correct_mean")))
        row("근거에 없는 숫자가 든 답", lambda s: num(a(s, "with_unsupported_numbers")))
        row("근거 없음 답", lambda s: num(a(s, "no_evidence")))
        row("빈 답·오류", lambda s: f"{a(s, 'empty')}·{a(s, 'errors')}" if a(s, "n") else "—")
    return "\n".join(lines)


def gold_reference(db, it: dict, limit: int = 1800) -> str:
    """판정자에게 줄 정답 자료 — 절 질문은 정답 절 글, 관계 질문은 정답 문서마다 질문에 묶어 적은 절 이름의 절 글(없으면 문서 앞부분)."""
    if it["set"] == "section":
        return (it.get("gold_text") or "")[:limit]
    m = re.search(r"「([^」]{2,60})」", it["q"])
    key = re.sub(r"\s+", "", m.group(1)) if m else ""
    parts = []
    per = limit // max(1, min(2, len(it["gold"])))
    for did in list(it["gold"])[:2]:
        doc = db.get_document(int(did)) or {}
        name = doc.get("filename") or f"문서 {did}"
        text = ""
        with db._conn() as conn:
            rows = conn.execute("SELECT label, props FROM kg_nodes WHERE type = 'section' AND doc_id = ?", (int(did),)).fetchall()
        seqs: list[int] = []
        for lab, props in rows:
            if key and key[:12] in re.sub(r"\s+", "", lab or ""):
                seqs = (json.loads(props or "{}").get("chunks") or [])[:4]
                break
        chunks = {c["seq"]: str(c.get("content") or "") for c in db.list_doc_chunks(int(did)) if c.get("kind") == "text"}
        text = "\n".join(chunks.get(q, "") for q in seqs) if seqs else "\n".join(chunks[q] for q in sorted(chunks)[:3])
        parts.append(f"《{name}》\n{text[:per]}")
    return "\n\n".join(parts)


def _mean(vals: list) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def summarize_think(rows: list[dict]) -> dict:
    """--think-compare 요약 — 변형별 시간·출력 토큰·빈 답·근거 밖 수치·재현율·판정 점수, 그리고 판정 짝 비교(켬 − 끔)."""
    out: dict = {}
    for v in THINK:
        ans = [r["answers"][v] for r in rows if v in r.get("answers", {})]
        if not ans:
            continue
        ok = [a for a in ans if not a["error"]]
        tm = [a.get("timing") or {} for a in ok]
        qs = [a.get("quality") or {} for a in ok]
        nt, nu = sum(q.get("num_total", 0) for q in qs), sum(q.get("num_unsupported", 0) for q in qs)
        fr = [q["fact_recall"] for q in qs if q.get("fact_recall") is not None]
        js = [a["judge"]["score"] for a in ok if (a.get("judge") or {}).get("score") is not None]
        out[v] = {"n": len(ans), "errors": sum(bool(a["error"]) for a in ans), "empty": sum(a["empty"] for a in ans),
                  "generate_sec_mean": _mean([t.get("generate_sec") for t in tm]), "generate_sec_median": _med([t.get("generate_sec") for t in tm]),
                  "generate_sec_p90": _p90([t.get("generate_sec") for t in tm]),
                  "wall_sec_mean": _mean([a["wall_sec"] for a in ok]), "wall_sec_median": _med([a["wall_sec"] for a in ok]),
                  "completion_tokens_mean": _mean([t.get("completion_tokens") for t in tm]),
                  "completion_tokens_median": _med([t.get("completion_tokens") for t in tm]),
                  "answer_chars_mean": _mean([len(a.get("answer") or "") for a in ok]),
                  "think_on_calls": sum(bool(t.get("think")) for t in tm),
                  "numbers_total": nt, "numbers_unsupported": nu, "unsupported_number_ratio": round(nu / nt, 3) if nt else None,
                  "with_unsupported_numbers": sum(bool(a["unsupported_numbers"]) for a in ok),
                  "fact_recall_mean": round(sum(fr) / len(fr), 3) if fr else None,
                  "fact_hits": sum(q.get("fact_hits", 0) for q in qs if q.get("fact_recall") is not None),
                  "facts_total": sum(q.get("n_facts", 0) for q in qs if q.get("fact_recall") is not None),
                  "judge_n": len(js), "judge_mean": round(sum(js) / len(js), 3) if js else None,
                  "judge_full": sum(1 for j in js if j == 2)}
        for part in ("section", "relation"):
            sub = [r["answers"][v] for r in rows if v in r.get("answers", {}) and r["set"] == part and not r["answers"][v]["error"]]
            pj = [a["judge"]["score"] for a in sub if (a.get("judge") or {}).get("score") is not None]
            if sub:
                out[v][part] = {"n": len(sub), "judge_mean": round(sum(pj) / len(pj), 3) if pj else None,
                                "generate_sec_mean": _mean([(a.get("timing") or {}).get("generate_sec") for a in sub])}
    pairs = [(r["answers"]["think_off"]["judge"]["score"], r["answers"]["think_on"]["judge"]["score"])
             for r in rows if all((r.get("answers", {}).get(v) or {}).get("judge") for v in THINK)]
    if pairs:
        d = [b - a for a, b in pairs]
        out["pairs"] = {"n": len(pairs), "better": sum(x > 0 for x in d), "worse": sum(x < 0 for x in d),
                        "same": sum(x == 0 for x in d), "mean_diff": round(sum(d) / len(d), 3)}
        # 부호 검정(같음 제외, 양측) — 표본이 작아 나아짐이 우연인지 가늠만
        k, n = out["pairs"]["better"], out["pairs"]["better"] + out["pairs"]["worse"]
        if n:
            from math import comb
            tail = sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
            out["pairs"]["sign_test_p"] = round(min(1.0, 2 * tail), 3)
    return out


def think_table(s: dict) -> str:
    vs = [v for v in THINK if v in s]
    num = lambda v: "—" if v is None else f"{v}"                # noqa: E731
    lines = ["| 지표 | " + " | ".join(THINK_LABEL[v] for v in vs) + " |", "|---|" + "---|" * len(vs)]

    def row(name, f):
        lines.append(f"| {name} | " + " | ".join(f(s[v]) for v in vs) + " |")
    row("답변 수", lambda x: num(x["n"]))
    row("생성 시간 평균·중앙값·p90(초)", lambda x: f"{num(x['generate_sec_mean'])} · {num(x['generate_sec_median'])} · {num(x['generate_sec_p90'])}")
    row("전체 시간 평균·중앙값(초)", lambda x: f"{num(x['wall_sec_mean'])} · {num(x['wall_sec_median'])}")
    row("출력 토큰 평균·중앙값", lambda x: f"{num(x['completion_tokens_mean'])} · {num(x['completion_tokens_median'])}")
    row("답 글자 수 평균", lambda x: num(x["answer_chars_mean"]))
    row("빈 답·오류", lambda x: f"{x['empty']}·{x['errors']}")
    row("근거 밖 수치", lambda x: f"{x['numbers_unsupported']}/{x['numbers_total']}")
    row("근거 밖 수치가 든 답", lambda x: num(x["with_unsupported_numbers"]))
    row("정답 사실 재현율(절 질문 평균)", lambda x: f"{num(x['fact_recall_mean'])} ({x['fact_hits']}/{x['facts_total']})")
    row("판정 점수 평균(0~2)", lambda x: num(x["judge_mean"]))
    row("  절 질문 · 관계 질문", lambda x: f"{num((x.get('section') or {}).get('judge_mean'))} · {num((x.get('relation') or {}).get('judge_mean'))}")
    row("판정 2점(만점) 답", lambda x: f"{x['judge_full']}/{x['judge_n']}")
    if s.get("pairs"):
        p = s["pairs"]
        lines.append(f"\n짝 비교(켬 기준, {p['n']}문): 나아짐 {p['better']} · 나빠짐 {p['worse']} · 같음 {p['same']} · "
                     f"평균 차 {p['mean_diff']:+} · 부호 검정 p={p.get('sign_test_p', '—')}")
    return "\n".join(lines)


LABELS = ("가", "나", "다")


def blind_pack(db, rows: list[dict], items: dict, seed: int, keys: tuple = MODES) -> tuple[list[dict], dict]:
    """눈가림 판정 묶음 — 질문마다 모드별 답을 무작위로 가·나·다에 놓는다. 열쇠는 따로(판정자에게 주지 않는다)."""
    rnd = random.Random(seed)
    out, key = [], {}
    for r in rows:
        a = r.get("answers", {})
        modes = [m for m in keys if m in a]
        if len(modes) < 2:
            continue
        rnd.shuffle(modes)
        labs = dict(zip(LABELS, modes))
        key[r["id"]] = labs
        out.append({"id": r["id"], "q": r["q"], "set": r["set"], "type": r["type"],
                    "reference": gold_reference(db, items[r["id"]]),
                    "answers": {lab: a[m]["answer"] for lab, m in labs.items()}})
    return out, key


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="fulltext,graph,graph_read")
    ap.add_argument("--answer-modes", default="fulltext,graph")
    ap.add_argument("--answer-sec", type=int, default=12, help="절 질문 앞에서 몇 개를 답변까지")
    ap.add_argument("--answer-rel", type=int, default=12, help="관계 질문 몇 개를 답변까지(갈래를 번갈아)")
    ap.add_argument("--k-sec", type=int, default=5)
    ap.add_argument("--k-rel", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="시험지 앞에서 몇 문만(시험 실행)")
    ap.add_argument("--only", default="", help="이 갈래의 질문만 — section · relation · plan_report · lineage · evaluates(쉼표로 여럿)."
                    " 27B 읽기(C) 측정을 필요한 질문으로만 줄일 때")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--tag", default=time.strftime("%Y%m%d%H%M"))
    ap.add_argument("--sec-set", default=str(ROOT / "data/eval/rag_probe_set.json"))
    ap.add_argument("--named", action="store_true", help="절 질문은 문서 이름을 넣은 질문(q_named, scripts/173 이 집합에 적어 둔 것)으로")
    ap.add_argument("--rel-set", default=str(ROOT / "data/eval/graph_relation_set.json"))
    ap.add_argument("--reuse", default="", help="앞선 결과 파일 — 같은 질문·모드의 검색·답을 그대로 쓰고 없는 것만 돈다(답변 표본 늘리기)")
    ap.add_argument("--judge", default="", help="눈가림 판정 결과(graphrag_judge_*.json) — 답마다 정답성·완결성 점수를 붙인다")
    ap.add_argument("--judge-key", default="", help="판정 묶음의 열쇠(graphrag_blind_key_*.json)")
    ap.add_argument("--think-compare", action="store_true",
                    help="답 생성 변형 축 — 검색은 graph_read 하나, 답 쓰기를 생각 끔/켬(ZZAIMY_ANSWER_THINK)으로 번갈아 견준다")
    ap.add_argument("--new-rel-set", default="", help="관계 질문 집합을 지금 그래프에서 새로 뽑아 이 경로에 쓰고 쓴다(scripts/184 make_set)"
                    " — 예: 40,30,20 = 계획↔실적 40·앞뒤 단계 30·평가 20")
    args = ap.parse_args()
    if args.new_rel_set:
        spec = importlib.util.spec_from_file_location("s184", ROOT / "scripts" / "184_graph_relation_probe.py")
        s184 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(s184)
        n_pair, n_lin, n_ev = (int(x) for x in args.new_rel_set.split(","))
        db0 = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
        made = s184.make_set(db0, args.seed, n_pair, n_lin, n_ev)
        Path(args.rel_set).write_text(json.dumps({"made": time.strftime("%Y-%m-%d %H:%M"), "seed": args.seed, "items": made},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"관계 질문 집합 새로 뽑음 {len(made)}문 → {Path(args.rel_set).name}", flush=True)
    import subprocess
    try:                                                    # 측정 시작 때 판 — 도는 동안 저장소가 바뀌어도 불러온 코드는 이것
        code_at_start = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                       timeout=10).stdout.strip()
    except Exception:
        code_at_start = ""
    modes = [m for m in args.modes.split(",") if m in MODES]
    amodes = [m for m in args.answer_modes.split(",") if m in MODES]
    if args.think_compare:
        modes, amodes = ["graph_read"], list(THINK)
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    items = load_sets(Path(args.sec_set), Path(args.rel_set), named=args.named)
    # 정답 문서가 지금 문서함에 없는 질문은 뺀다(반입 정리로 빠진 문서) — 뺀 수는 결과에 남긴다
    with db._conn() as conn:
        live = {int(r[0]) for r in conn.execute("SELECT id FROM documents WHERE doc_type = 'grant' AND status = 'reviewed'").fetchall()}
    dropped = [x["id"] for x in items if (x["doc_id"] not in live if x["set"] == "section"
                                          else not (set(x["gold"]) & live if x["any"] else set(x["gold"]) <= live))]
    items = [x for x in items if x["id"] not in set(dropped)]
    if args.only:
        only = set(args.only.split(","))
        items = [x for x in items if x["set"] in only or x["type"] in only]
    for x in items:
        pair_sides(db, x)
    if args.limit:
        items = [x for x in items if x["set"] == "section"][: args.limit] + [x for x in items if x["set"] == "relation"][: args.limit]
    ans_ids = {x["id"] for x in pick_answer_items(items, args.answer_sec, args.answer_rel)}
    if args.think_compare:                                      # 답 생성만 견준다 — 답할 질문만 검색(27B 서버에 헛걸음 없이)
        items = [x for x in items if x["id"] in ans_ids]
    print(f"시험지: 절 {sum(x['set'] == 'section' for x in items)}문 · 관계 {sum(x['set'] == 'relation' for x in items)}문 · "
          f"답변 {len(ans_ids)}문 × {','.join(amodes)} · 모드 {','.join(modes)}", flush=True)
    if dropped:
        print(f"정답 문서가 문서함에 없어 뺀 질문 {len(dropped)}문: {', '.join(dropped[:20])}", flush=True)
    for m in modes:
        run_search(db, items[0], m, args.k_sec, args.k_rel)             # 데우기(색인·DB 캐시) — 기록하지 않는다
    if ans_ids:
        from zzaimy.app.regulations import find_relevant
        find_relevant(db, items[0]["q"])                                 # 규정 검색(재순위 모델) 데우기 — 첫 답만 수십 초 느려지지 않게
    judged: dict = {}
    if args.judge:
        jd = json.loads(Path(args.judge).read_text(encoding="utf-8"))
        jkey = json.loads(Path(args.judge_key).read_text(encoding="utf-8"))
        for j in jd["items"]:
            for lab, mode in jkey.get(j["id"], {}).items():
                if lab in j.get("scores", {}):
                    judged[(j["id"], mode)] = j["scores"][lab]
    prev: dict = {}
    prev_meta: dict = {}
    if args.reuse:
        prev_meta = json.loads(Path(args.reuse).read_text(encoding="utf-8"))
        prev = {r["id"]: r for r in prev_meta["rows"]}
    rows: list[dict] = []
    started = time.time()
    for i, it in enumerate(items):
        row = {"id": it["id"], "set": it["set"], "type": it["type"], "q": it["q"], "search": {}, "answers": {}}
        old = prev.get(it["id"])
        if old and old.get("q") == it["q"]:
            row["search"] = {m: v for m, v in old.get("search", {}).items() if m in modes}
            row["answers"] = {m: v for m, v in old.get("answers", {}).items() if m in amodes}
        if it["set"] == "section":
            row.update(doc_id=it["doc_id"], gold_text=it["gold_text"][:3000])
        else:
            row["gold_docs"] = it["gold"]
        order = list(modes)
        random.Random(f"{args.seed}:{it['id']}").shuffle(order)          # 모드 차례를 질문마다 섞는다
        for m in order:
            if m not in row["search"]:
                row["search"][m] = run_search(db, it, m, args.k_sec, args.k_rel)
        if it["id"] in ans_ids and args.think_compare:
            # 끔·켬을 번갈아 — 답변 질문마다 먼저 도는 쪽을 바꾼다(서버 부하·캐시가 한쪽에 몰리지 않게)
            seq = THINK if sum(1 for r in rows if r["id"] in ans_ids) % 2 == 0 else THINK[::-1]
            for v in seq:
                if v not in row["answers"]:
                    row["answers"][v] = dict(run_answer(db, it, "graph_read", think=(v == "think_on")), first=(v == seq[0]))
        elif it["id"] in ans_ids:
            for m in [m for m in order if m in amodes]:
                if m not in row["answers"]:
                    row["answers"][m] = run_answer(db, it, m)
        for m, a in row["answers"].items():
            a["quality"] = quality(it, a)
            if judged.get((it["id"], m)):
                a["judge"] = judged[(it["id"], m)]
        rows.append(row)
        mark = " ".join(f"{m[:2]}:{'O' if (row['search'][m].get('doc_hit') if it['set'] == 'section' else row['search'][m]['ok']) else 'X'}"
                        f"/{row['search'][m]['sec']}s" for m in modes)
        extra = " ".join(f"ans {m[:2]} {row['answers'][m]['wall_sec']}s" for m in row["answers"])
        print(f"[{i + 1}/{len(items)}] {it['id']} {mark} {extra} {it['q'][:50]}", flush=True)
    summary = {m: summarize(rows, m) for m in modes}
    think = summarize_think(rows) if args.think_compare else None
    out = ROOT / "data" / "eval" / (f"graphrag_think_{args.tag}.json" if args.think_compare else f"graphrag_vs_fulltext_{args.tag}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    blind, bkey = blind_pack(db, rows, {x["id"]: x for x in items}, args.seed, keys=THINK if args.think_compare else MODES)
    code = code_at_start
    if prev_meta.get("code"):
        code = f"{prev_meta['code']}(재사용: {prev_meta.get('tag')})" + (f" · 이번 {code}" if code and code != prev_meta["code"] else "")
    out.write_text(json.dumps({"made": time.strftime("%Y-%m-%d %H:%M"), "tag": args.tag, "code": code, "modes": modes, "answer_modes": amodes,
                               "k": {"section": args.k_sec, "relation": args.k_rel},
                               "rerank_on": os.environ.get("ZZAIMY_GRANT_RERANK", "0") == "1",
                               "sets": {"section": Path(args.sec_set).name, "relation": Path(args.rel_set).name},
                               "elapsed_min": round((time.time() - started) / 60, 1), "dropped": dropped,
                               "summary": summary, "think": think, "rows": rows, "judged": len(judged)},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    bl = ROOT / "data" / "eval" / f"graphrag_blind_{args.tag}.json"
    bl.write_text(json.dumps(blind, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "data" / "eval" / f"graphrag_blind_key_{args.tag}.json").write_text(json.dumps(bkey, ensure_ascii=False), encoding="utf-8")
    print(table(summary))
    if think is not None:
        print(think_table(think))
        print("GRAPHRAG_THINK " + json.dumps(think, ensure_ascii=False))
    print("GRAPHRAG_VS " + json.dumps({m: {"section": summary[m]["search"]["section"], "relation_ok": summary[m]["search"]["relation"]["ok"],
                                            "search_sec_median": summary[m]["search"]["sec_median"],
                                            "answer": {k: v for k, v in (summary[m].get("answer") or {}).items()
                                                       if not isinstance(v, dict)}} for m in modes}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
