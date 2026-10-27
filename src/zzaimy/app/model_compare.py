"""모델 비교(/dev/models/compare) — VM 의 비교 실행이 남긴 파일을 읽어 보여 준다.
품질·성능(/dev/quality)의 「모델 비교」 카드가 마지막 실행의 요약(headline)을 보이고, 이 화면은 그 「자세히」다.

결과 자리: data/eval/model_compare/<run-id>/ (ZZAIMY_MODEL_COMPARE_DIR 로 바꿀 수 있다)
  summary.json  실행 정보·갈래(arms)·묶음(blocks: 지표·쌍대 승패·차원별 평균·서류 갈래별 점수)·메모
  samples.json  표본마다 입력·갈래별 출력·점수·판정 이유
  docs.json     갈래별로 만든 hwpx 목록(docs/<arm>/*.hwpx). 없으면 docs/ 폴더를 직접 훑는다
  analysis.md   사람이 쓴 분석(문제·까닭·다음 방향) — 있으면 화면 「분석」 절에 문서 열람과 같은 안전한 렌더로

실행이 도는 중이면 파일이 덜 차 있다 — 없는 부분은 화면이 「진행 중」 으로 보인다(summary 에 created 가 생기면 끝난 것 —
그 뒤로는 재지 않은 칸만 「—」). 지표는 {갈래: {지표: 값}}·{지표: {갈래: 값}} 둘 다 읽는다. 파일은 읽기만 한다.
- 개발자(관리자)만 — /dev 경로는 main.check_auth 가 막는다.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter()

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FILES = ("summary.json", "samples.json", "docs.json")
_ANALYSIS_MAX = 200_000      # analysis.md 를 읽는 최대 글자 수

KIND_LABELS = {"open": "오픈 27B", "sft-lora": "SFT 어댑터", "sft-merged": "SFT 합친 판"}
DIM_LABELS = {
    "instruction": "지시 충실", "instruction_following": "지시 충실",
    "context": "맥락 이해", "context_understanding": "맥락 이해",
    "format": "형식", "structure": "형식",
    "grounding": "근거 충실", "faithfulness": "근거 충실", "groundedness": "근거 충실",
    "style": "문체", "tone": "문체",
    "completeness": "완결성",
}
DIM_ORDER = ("지시 충실", "맥락 이해", "형식", "근거 충실", "문체", "완결성")
SPEED_LABELS = {
    "ttft": "첫 글자까지(초)", "ttft_s": "첫 글자까지(초)", "ttft_ms": "첫 글자까지(ms)", "first_token_s": "첫 글자까지(초)",
    "tok_per_sec": "초당 토큰", "tok_per_s": "초당 토큰", "tokens_per_s": "초당 토큰", "tps": "초당 토큰", "tok_s": "초당 토큰",
    "sec": "문항당 시간(초)", "total_s": "전체 시간", "elapsed_s": "전체 시간", "latency_s": "전체 시간", "total_time_s": "전체 시간",
}


def root(db_path) -> Path:
    env = os.environ.get("ZZAIMY_MODEL_COMPARE_DIR", "").strip()
    return Path(env) if env else Path(db_path).parent.parent / "eval" / "model_compare"


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _run_dir(base: Path, run_id: str) -> Path | None:
    if not _RUN_ID.match(run_id or ""):
        return None
    d = base / run_id
    return d if d.is_dir() and any((d / f).exists() for f in _FILES) else None


def list_runs(base: Path) -> list[dict]:
    """결과 파일이 하나라도 있는 실행 — 새것이 앞(summary.created, 없으면 폴더 시각)."""
    if not base.is_dir():
        return []
    out = []
    for d in base.iterdir():
        if not d.is_dir() or not _RUN_ID.match(d.name) or not any((d / f).exists() for f in _FILES):
            continue
        s = _read(d / "summary.json") if (d / "summary.json").exists() else None
        created = str((s or {}).get("created") or "") if isinstance(s, dict) else ""
        out.append({"id": d.name, "created": created, "mtime": d.stat().st_mtime})
    out.sort(key=lambda r: (r["created"] or "", r["mtime"]), reverse=True)
    return out


def _label(table: dict, key: str) -> str:
    return table.get(str(key), table.get(str(key).lower(), str(key)))


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _pair(p: dict) -> dict:
    win, tie, loss = (_num(p.get(k)) or 0 for k in ("win", "tie", "loss"))
    tot = win + tie + loss
    pct = (lambda x: round(100 * x / tot, 1)) if tot else (lambda x: 0)
    return {"a": str(p.get("a", "")), "b": str(p.get("b", "")), "win": win, "tie": tie, "loss": loss,
            "n": p.get("n", tot), "win_pct": pct(win), "tie_pct": pct(tie), "loss_pct": pct(loss),
            "score": _num(p.get("score")), "ci_low": _num(p.get("ci_low")), "ci_high": _num(p.get("ci_high")),
            "control": bool(p.get("control")), "flip_rate": _num(p.get("flip_rate"))}


def _by_metric(metrics, arm_keys: list[str]) -> dict[str, dict]:
    """지표 표를 {지표: {갈래: 값}} 꼴로 — summary 는 {갈래: {지표: 값}}(212 내보내기) 과 {지표: {갈래: 값}} 둘 다 온다."""
    if not isinstance(metrics, dict):
        return {}
    arm_major = bool(metrics) and all(k in arm_keys for k in metrics) and all(isinstance(v, dict) for v in metrics.values())
    out: dict[str, dict] = {}
    if arm_major:
        for arm, vals in metrics.items():
            for name, v in vals.items():
                out.setdefault(str(name), {})[arm] = v
    else:
        for name, vals in metrics.items():
            if isinstance(vals, dict):
                out[str(name)] = dict(vals)
    return out


def _is_pct(name: str, pct: set) -> bool:
    return name in pct or name == "rate" or name.endswith("_rate")


def _rows(by_metric: dict, arm_keys: list[str], labels: dict, pct: set, skip=()) -> list[dict]:
    return [{"name": labels.get(n) or SPEED_LABELS.get(n) or {"rate": "통과율", "ok": "통과", "n": "문항 수"}.get(n, n),
             "vals": {a: _num(v.get(a)) for a in arm_keys}, "pct": _is_pct(n, pct)}
            for n, v in by_metric.items() if n not in skip and any(_num(v.get(a)) is not None for a in arm_keys)]


def _block(b: dict, arm_keys: list[str], labels: dict | None = None, pct: set | None = None) -> dict:
    labels, pct = labels or {}, pct or set()
    metrics = b.get("metrics") if isinstance(b.get("metrics"), dict) else None
    bm = _by_metric(metrics, arm_keys)
    speed = _rows({n: v for n, v in bm.items() if n in SPEED_LABELS}, arm_keys, labels, pct)
    other = _rows({n: v for n, v in bm.items() if n not in SPEED_LABELS}, arm_keys, labels, pct)
    dims = b.get("per_dimension") if isinstance(b.get("per_dimension"), dict) else None
    dim_rows = None
    if dims is not None:
        dim_rows = [{"name": _label(DIM_LABELS, k), "vals": {a: _num((v or {}).get(a)) for a in arm_keys}}
                    for k, v in dims.items() if isinstance(v, dict) and any(_num(v.get(a)) is not None for a in arm_keys)]
        order = {n: i for i, n in enumerate(DIM_ORDER)}
        dim_rows.sort(key=lambda r: order.get(r["name"], len(order)))
    per_type_raw = b.get("per_type") if isinstance(b.get("per_type"), list) else None
    per_type, runs = None, None
    if per_type_raw is not None:
        per_type, run_list = [], []
        for r in per_type_raw:
            if not isinstance(r, dict):
                continue
            if r.get("arm") and isinstance(r.get("metrics"), dict) and "type" not in r:
                run_list.append({"label": str(r.get("label") or r["arm"]), "arm": str(r["arm"]), "metrics": r["metrics"]})
                continue
            m = r.get("metrics")
            rows = _rows(_by_metric(m, arm_keys), arm_keys, labels, pct, skip=("n",)) if isinstance(m, dict) else \
                [{"name": "", "vals": {a: _num(r.get(a)) for a in arm_keys}, "pct": False}]
            per_type.append({"type": str(r.get("type", "")), "n": r.get("n", ""), "rows": rows})
        if run_list:   # 회차별(문서 작성처럼 한 판씩 돈 것): 줄 = 지표, 칸 = 회차
            names: list[str] = []
            for x in run_list:
                for n in x["metrics"]:
                    if n not in names:
                        names.append(n)
            runs = {"cols": [{"label": x["label"], "arm": x["arm"]} for x in run_list],
                    "rows": [{"name": labels.get(n) or n, "pct": _is_pct(n, pct),
                              "vals": [x["metrics"].get(n) for x in run_list]} for n in names]}
    pairwise = b.get("pairwise") if isinstance(b.get("pairwise"), list) else None
    used = set()
    for r in speed + other + (dim_rows or []) + [row for t in (per_type or []) for row in t["rows"]]:
        used |= {a for a, v in r["vals"].items() if v is not None}
    return {
        "key": str(b.get("key", "")), "title": str(b.get("title") or b.get("key") or "묶음"),
        "pairwise": [_pair(p) for p in pairwise if isinstance(p, dict)] if pairwise is not None else None,
        "dims": dim_rows, "speed": speed if metrics is not None else None, "other": other,
        "per_type": per_type, "runs": runs,
        "arms": [a for a in arm_keys if a in used] or list(arm_keys),   # 이 묶음에서 잰 갈래만 칸으로
    }


def _docs(d: Path, listed) -> dict[str, list[dict]]:
    """갈래별 문서 — docs.json 이 있으면 그것, 없으면 docs/<arm>/*.hwpx 를 훑는다. 실제로 있는 파일만."""
    out: dict[str, list[dict]] = {}
    if isinstance(listed, list):
        for r in listed:
            if isinstance(r, dict) and r.get("file") and safe_doc(d, str(r["file"])):
                out.setdefault(str(r.get("arm", "")), []).append(
                    {"title": str(r.get("title") or Path(str(r["file"])).name), "file": str(r["file"]),
                     "created": str(r.get("created") or "")})
        return out
    docs = d / "docs"
    if docs.is_dir():
        for arm in sorted(p for p in docs.iterdir() if p.is_dir()):
            for f in sorted(arm.glob("*.hwpx")):
                out.setdefault(arm.name, []).append({"title": f.stem, "file": f"{arm.name}/{f.name}", "created": ""})
    return out


def _analysis(d: Path) -> str:
    f = d / "analysis.md"
    if not f.is_file() or f.is_symlink():
        return ""
    try:
        return f.read_text(encoding="utf-8")[:_ANALYSIS_MAX].strip()
    except (OSError, UnicodeDecodeError):
        return ""


def load_run(base: Path, run_id: str) -> dict | None:
    d = _run_dir(base, run_id)
    if d is None:
        return None
    s = _read(d / "summary.json")
    s = s if isinstance(s, dict) else {}
    arms = [a for a in (s.get("arms") or []) if isinstance(a, dict) and a.get("key")]
    arm_keys = [str(a["key"]) for a in arms]
    samples = _read(d / "samples.json")
    samples = [x for x in samples if isinstance(x, dict)] if isinstance(samples, list) else None
    if not arm_keys and samples:   # 요약 전 — 표본에 나온 갈래로
        for x in samples:
            for k in (x.get("outputs") or {}):
                if k not in arm_keys:
                    arm_keys.append(str(k))
        arms = [{"key": k} for k in arm_keys]
    arms = [{"key": str(a["key"]), "label": str(a.get("label") or a["key"]), "model": str(a.get("model") or ""),
             "kind": str(a.get("kind") or ""), "kind_label": KIND_LABELS.get(str(a.get("kind") or ""), ""),
             "short": str(a.get("short") or "")}
            for a in arms]
    _short_names(arms, s.get("labels") if isinstance(s.get("labels"), dict) else {})
    blocks = s.get("blocks")
    docs_listed = _read(d / "docs.json") if (d / "docs.json").exists() else None
    return {
        "id": d.name, "created": str(s.get("created") or ""), "has_summary": bool(s),
        "finished": bool(s.get("created")) and isinstance(s.get("blocks"), list),
        "arms": arms, "arm_label": {a["key"]: a["label"] for a in arms},
        "blocks": [_block(b, arm_keys, s.get("labels") if isinstance(s.get("labels"), dict) else None,
                          set(s.get("percent_keys") or []) if isinstance(s.get("percent_keys"), list) else None)
                   for b in blocks if isinstance(b, dict)] if isinstance(blocks, list) else None,
        "notes": [str(n) for n in (s.get("notes") or []) if n] if isinstance(s.get("notes"), list) else [],
        # 결론 한 줄 — 내보내기가 적어 둔 경우만(지어내지 않는다)
        "verdict": str(s.get("verdict") or s.get("conclusion") or "").strip(),
        "analysis": _analysis(d),
        "samples": [{"id": str(x.get("id", i)), "type": str(x.get("type") or ""), "input": x.get("input") or "",
                     "gold": x.get("gold") or "", "outputs": x.get("outputs") if isinstance(x.get("outputs"), dict) else {},
                     "scores": x.get("scores") if isinstance(x.get("scores"), dict) else {},
                     "judge_reason": x.get("judge_reason") or ""} for i, x in enumerate(samples)]
                   if samples is not None else None,
        "docs": _docs(d, docs_listed) if (docs_listed is not None or (d / "docs").is_dir()) else None,
    }


def _short_names(arms: list[dict], labels: dict) -> None:
    """화면에 쓸 짧은 이름 — 결과 파일이 적어 둔 짧은 이름(갈래의 short, labels 표의 갈래 키)이 먼저,
    없으면 갈래 종류 이름(오픈 27B·SFT 합친 판…)이 그 실행에서 하나뿐일 때 그것, 그래도 없으면 이름의 괄호 부분을 뗀 것.
    모델마다 따로 적지 않는다. 짧은 이름끼리 겹치면 원래 이름으로 둔다."""
    kinds = [a["kind_label"] for a in arms if a["kind_label"]]
    for a in arms:
        given = str(a.get("short") or labels.get(a["key"]) or "").strip()
        trimmed = re.sub(r"\s*[(（][^()（）]*[)）]", "", a["label"]).strip() or a["label"]
        a["short"] = given or (a["kind_label"] if a["kind_label"] and kinds.count(a["kind_label"]) == 1 else trimmed)
    shorts = [a["short"] for a in arms]
    for a in arms:
        if shorts.count(a["short"]) > 1:
            a["short"] = a["label"]


def headline(run: dict) -> dict:
    """품질·성능 카드용 짧은 요약 — load_run 결과에서 고르기만 한다(수치를 새로 만들지 않는다).
    pairs: 대조군이 아닌 쌍대 점수(95% 구간), docs: 회차별로 돈 묶음(문서 작성)의 비율 지표, speed: 속도 지표."""
    blocks = run.get("blocks") or []
    pairs = [{"block": b["title"], **p} for b in blocks for p in (b["pairwise"] or [])
             if not p["control"] and p["score"] is not None]
    doc = next((b for b in blocks if b["runs"] and b["other"]), None)
    doc_rows = sorted(doc["other"], key=lambda r: not r["pct"])[:4] if doc else []
    speed_blocks = [b for b in blocks if b["speed"]]
    sp = next((b for b in speed_blocks if not b["other"] and not b["pairwise"]), speed_blocks[0] if speed_blocks else None)
    used = {a for r in doc_rows + (sp["speed"] if sp else []) for a, v in r["vals"].items() if v is not None}
    # 첫 화면 카드의 짧은 표 — 문서 작성 두 줄 + 속도 두 줄까지
    key_rows = doc_rows[:2] + (sp["speed"][:2] if sp else [])
    key_used = {a for r in key_rows for a, v in r["vals"].items() if v is not None}
    verdict = run.get("verdict") or ""
    return {"pairs": pairs, "doc_title": doc["title"] if doc else "", "doc_rows": doc_rows,
            "speed_title": sp["title"] if sp else "", "speed_rows": sp["speed"] if sp else [],
            "arms": [a for a in run["arms"] if a["key"] in used],
            "key_rows": key_rows, "key_arms": [a for a in run["arms"] if a["key"] in key_used],
            # 타일 한 줄 — 결론의 앞부분(「 — 」·「 · 」 앞). 결론이 없으면 비운다(지어내지 않는다)
            "verdict_short": re.split(r"\s+[—·]\s+|[.。]\s", verdict, maxsplit=1)[0].strip() if verdict else ""}


def quality_card(db_path) -> dict:
    """품질·성능 「모델 비교」 카드 — 마지막 실행 요약과 실행 목록."""
    base = root(db_path)
    runs = list_runs(base)
    run = load_run(base, runs[0]["id"]) if runs else None
    return {"runs": runs, "run": run, "head": headline(run) if run else None}


def safe_doc(run_dir: Path, rel: str) -> Path | None:
    """실행의 docs/ 안에 있는 hwpx 만 — 절대 경로·.. ·링크로 밖을 가리키면 None."""
    if not rel or "\x00" in rel or rel.startswith(("/", "\\")) or ":" in rel:
        return None
    docs = (run_dir / "docs").resolve()
    try:
        p = (docs / rel).resolve()
    except (OSError, RuntimeError):
        return None
    if not p.is_relative_to(docs) or p.suffix.lower() != ".hwpx" or not p.is_file():
        return None
    return p


def _fmt(v, digits: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}" if isinstance(v, float) else str(v)


def install(env) -> None:
    env.filters["mc_num"] = _fmt


@router.get("/dev/models/compare", response_class=HTMLResponse)
def compare_latest(request: Request):
    return _page(request, None)


@router.get("/dev/models/compare/{run_id}", response_class=HTMLResponse)
def compare_run(request: Request, run_id: str):
    return _page(request, run_id)


def _page(request: Request, run_id: str | None):
    st = request.app.state
    base = root(st.db_path)
    runs = list_runs(base)
    if run_id is None:
        run = load_run(base, runs[0]["id"]) if runs else None
    else:
        run = load_run(base, run_id)
        if run is None:
            raise HTTPException(404, "비교 실행을 찾을 수 없습니다")
    # 분석은 /dev/doc 문서 열람과 같은 렌더(이스케이프 뒤 제목·목록·표·굵게만 되살림 — 원문 HTML 은 글자 그대로)
    analysis = st.docs.dev_doc_view(run["analysis"], "분석") if run and run["analysis"] else None
    return st.templates.TemplateResponse(request, "dev_model_compare.html", st.page_ctx(request, {
        "runs": runs, "run": run, "base_shown": str(base), "analysis": analysis,
    }))


@router.get("/dev/models/compare/{run_id}/doc")
def compare_doc(request: Request, run_id: str, f: str = ""):
    d = _run_dir(root(request.app.state.db_path), run_id)
    p = safe_doc(d, f) if d is not None else None
    if p is None:
        raise HTTPException(404, "파일을 찾을 수 없습니다")
    return FileResponse(p, filename=p.name, media_type="application/hwp+zip")
