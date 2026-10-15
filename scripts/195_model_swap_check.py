#!/usr/bin/env python3
"""생성 모델 교체 점검 — 한 번의 실행으로 검색 실측·답 표본·갈래 판정 표본을 돌리고 기준(data/eval/model_baseline.json)과 표로 견준다.

단계(각각 끌 수 있다 — --skip search,answer,kind):
- search : scripts/173 검색 실측 두 번(--reader: 질문을 모델로 읽기, --named: 문서 이름 넣은 질문 + 모델 읽기). 시험지는 고정 집합 그대로
- answer : scripts/188 의 운영 경로(graph_read) 답 표본 소수 — 시간·빈 답·오류·근거 밖 수치·정답 사실 재현율(채점은 하지 않는다)
- kind   : scripts/190 갈래 판정 표본을 review 용도의 연결로(규칙과 같은 비율·규칙이 비운 문서를 채운 비율·실패·시간)
--quick 이면 검색은 --reader 한 번, 답과 갈래는 아주 적게(배선·속도 확인용 — 수치 비교에는 기본 실행을 쓴다).

후보 모델(운영 용도 지정은 바꾸지 않는다): --candidate-conn <연결 id> 와/또는 --candidate-model <서빙 이름>.
하위 실행에 ZZAIMY_LLM_TRY_CONN·ZZAIMY_LLM_TRY_MODEL 을 넘겨 그 프로세스에서만 answer·review·기본 연결을 후보로 돌린다(llm_connections.trial_conn).
후보를 주면 같은 실행에서 운영도 함께 돈다(같은 데이터 상태에서 견주게) — 운영을 건너뛰려면 --no-current(기준 파일과만 견준다).

기준: data/eval/model_baseline.json 이 없으면 이번 운영 결과를 기준으로 저장한다. --save-baseline 이면 이번 운영 결과로 바꾼다.
후보 결과는 기준으로 저장하지 않는다 — 용도를 후보로 옮긴 뒤 운영으로 다시 돌려 저장한다.
결과: data/eval/model_swap_<tag>.json(단계 수치·모델 정보·연결 이름 — 서버 주소는 남기지 않는다)과 표(마크다운).

사용(VM — .env.local 을 이 스크립트가 읽는다, 없으면 경고):
  scripts/zz_run.sh 16G env PYTHONPATH=src .venv/bin/python scripts/195_model_swap_check.py --quick
  scripts/zz_run.sh 16G env PYTHONPATH=src .venv/bin/python scripts/195_model_swap_check.py --candidate-conn <연결 id> --candidate-model <서빙 이름>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

BASELINE = ROOT / "data" / "eval" / "model_baseline.json"
STEPS = ("search", "answer", "kind")
TRIAL_ENV = ("ZZAIMY_LLM_TRY_CONN", "ZZAIMY_LLM_TRY_MODEL")

# 표에 싣는 수치 — (열쇠, 이름, 높을수록 좋은가(None 이면 방향 없음))
METRICS: list[tuple[str, str, bool | None]] = [
    ("search_reader.doc_at_k", "검색(모델 읽기) 정답 문서@k", True),
    ("search_reader.twin_at_k", "검색(모델 읽기) 같은 판 문서@k", True),
    ("search_reader.prog_at_k", "검색(모델 읽기) 같은 사업@k", True),
    ("search_reader.search_sec_median", "검색(모델 읽기) 시간 중앙값(초)", False),
    ("search_reader.search_errors", "검색(모델 읽기) 오류", False),
    ("search_named.doc_at_k", "검색(이름 질문) 정답 문서@k", True),
    ("search_named.twin_at_k", "검색(이름 질문) 같은 판 문서@k", True),
    ("search_named.search_sec_median", "검색(이름 질문) 시간 중앙값(초)", False),
    ("answer.n", "답 표본 수", None),
    ("answer.errors", "답 오류", False),
    ("answer.empty", "빈 답", False),
    ("answer.with_unsupported_numbers", "근거 밖 수치가 있는 답", False),
    ("answer.unsupported_number_ratio", "근거 밖 수치 비율", False),
    ("answer.fact_recall_mean", "정답 사실 재현율(평균)", True),
    ("answer.evidence_has_gold", "근거에 정답 문서", True),
    ("answer.wall_sec_median", "답 전체 시간 중앙값(초)", False),
    ("answer.generate_sec_median", "답 생성 시간 중앙값(초)", False),
    ("answer.judge_correct_mean", "채점 정답성 평균(채점했을 때)", True),
    ("kind.n", "갈래 표본 수", None),
    ("kind.fail", "갈래 판정 실패", False),
    ("kind.ruled_same", "규칙 갈래와 같은 비율", True),
    ("kind.empty_filled", "규칙이 비운 문서를 채운 비율", None),
    ("kind.sec_median", "갈래 판정 시간 중앙값(초)", False),
]


# ---------------- 환경 ----------------

def load_env_file(path: Path) -> dict[str, str]:
    """.env.local 의 KEY=VALUE 줄(셸 따옴표·export 허용). 주석·빈 줄은 건너뛴다."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            out[k] = v
    return out


def trial_env(conn: str = "", model: str = "") -> dict[str, str]:
    env = {}
    if conn:
        env["ZZAIMY_LLM_TRY_CONN"] = conn
    if model:
        env["ZZAIMY_LLM_TRY_MODEL"] = model
    return env


# ---------------- 모델·서버 정보(주소 없이) ----------------

def _server_version(client) -> str:
    """vLLM /version — 못 읽으면 빈 글."""
    import urllib.request

    base = str(getattr(getattr(client, "client", None), "base_url", "") or "").rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    if not root.startswith(("http://", "https://")) or getattr(client, "external", False):
        return ""
    try:
        with urllib.request.urlopen(root + "/version", timeout=3) as r:
            return str(json.loads(r.read()).get("version") or "")[:40]
    except Exception:
        return ""


def model_snapshot(extra_env: dict[str, str]) -> dict:
    """answer·review 용도가 지금(후보 설정을 얹어) 가리키는 모델과 연결 — 연결 이름·id·종류·엔진 판, 모델 정보. 주소는 넣지 않는다."""
    from zzaimy.generate import llm_connections, model_info
    from zzaimy.generate.client import VllmClient

    saved = {k: os.environ.get(k) for k in TRIAL_ENV}
    for k in TRIAL_ENV:
        os.environ.pop(k, None)
    os.environ.update(extra_env)
    out: dict = {}
    try:
        for role in ("answer", "review"):
            try:
                c = VllmClient(role=role)
                conn = llm_connections.get(c.connection_id) if c.connection_id else None
                out[role] = {"llm": model_info.stamp(c),
                             "server": {"conn": (conn or {}).get("name", "환경변수"), "conn_id": c.connection_id,
                                        "kind": c.kind, "version": _server_version(c)}}
            except Exception as e:
                out[role] = {"llm": {}, "server": {}, "error": f"{type(e).__name__}"}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return out


# ---------------- 단계 실행 ----------------

def plan_commands(tag: str, label: str, quick: bool, skip: set[str], py: str = sys.executable,
                  parsed: str = "") -> list[tuple[str, list[str], dict]]:
    """(단계 이름, 명령, 결과 위치) 목록 — 실행 없이 무엇을 돌릴지만(시험하기 쉽게)."""
    t = f"swap-{tag}-{label}"
    s = str(ROOT / "scripts")
    out: list[tuple[str, list[str], dict]] = []
    if "search" not in skip:
        out.append(("search_reader", [py, f"{s}/173_rag_probe.py", "--reader", "--tag", f"{t}-reader"],
                    {"json": str(ROOT / "data" / "eval" / f"rag_probe_{t}-reader.json")}))
        if not quick:
            out.append(("search_named", [py, f"{s}/173_rag_probe.py", "--reader", "--named", "--tag", f"{t}-named"],
                        {"json": str(ROOT / "data" / "eval" / f"rag_probe_{t}-named.json")}))
    if "answer" not in skip:
        n_sec, n_rel, limit = (2, 1, 3) if quick else (6, 4, 10)
        out.append(("answer", [py, f"{s}/188_graphrag_vs_fulltext.py", "--modes", "graph_read", "--answer-modes", "graph_read",
                               "--answer-sec", str(n_sec), "--answer-rel", str(n_rel), "--limit", str(limit), "--tag", t],
                    {"json": str(ROOT / "data" / "eval" / f"graphrag_vs_fulltext_{t}.json")}))
    if "kind" not in skip:
        kout = ROOT / "data" / "eval" / f"kind_read_{t}.jsonl"
        out.append(("kind", [py, f"{s}/190_kind_read_sample.py", "--role", "review", "--n", "3" if quick else "25",
                             "--parsed", parsed or str(ROOT / "data" / "inbox" / "parsed"), "--out", str(kout)],
                    {"line": "KIND_READ "}))
    return out


def _dig(d, path: str):
    for p in path.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def metrics_from(step: str, data: dict | None, stdout: str = "") -> dict:
    """하위 실행 결과 → 표에 싣는 수치(평평한 dict)."""
    if step.startswith("search_"):
        s = (data or {}).get("summary") or {}
        return {k: s.get(k) for k in ("n", "doc_at_k", "twin_at_k", "prog_at_k", "search_sec_median", "search_errors")}
    if step == "answer":
        a = _dig(data or {}, "summary.graph_read.answer") or {}
        out = {k: a.get(k) for k in ("n", "errors", "empty", "with_unsupported_numbers", "unsupported_number_ratio",
                                     "fact_recall_mean", "evidence_has_gold", "wall_sec_median", "generate_sec_median")}
        out["judge_correct_mean"] = _dig(a, "judge.correct_mean")
        return out
    if step == "kind":
        line = next((x for x in reversed(stdout.splitlines()) if x.startswith("KIND_READ ")), "")
        try:
            k = json.loads(line[len("KIND_READ "):]) if line else {}
        except ValueError:
            k = {}
        return {"n": k.get("n"), "fail": k.get("fail"), "ruled_same": _dig(k, "ruled.same_as_rule"),
                "empty_filled": _dig(k, "empty.filled"), "sec_median": k.get("sec_median")}
    return {}


def run_steps(tag: str, label: str, extra_env: dict[str, str], quick: bool, skip: set[str], parsed: str = "",
              runner=None) -> dict:
    """한 묶음(운영 또는 후보)의 단계를 차례로 돌린다. runner(cmd, env) → (종료 코드, 표준 출력) 는 시험용으로 바꿀 수 있다."""
    runner = runner or _subprocess_runner
    env = {k: v for k, v in os.environ.items() if k not in TRIAL_ENV}
    env.update(extra_env)
    env.setdefault("PYTHONPATH", str(ROOT / "src"))
    res: dict = {"metrics": {}, "steps": {}}
    for step, cmd, where in plan_commands(tag, label, quick, skip, parsed=parsed):
        t0 = time.time()
        print(f"[{label}] {step} …", flush=True)
        rc, stdout = runner(cmd, env)
        data = None
        if where.get("json") and Path(where["json"]).is_file():
            try:
                data = json.loads(Path(where["json"]).read_text(encoding="utf-8"))
            except ValueError:
                data = None
        group = "search_reader" if step == "search_reader" else ("search_named" if step == "search_named" else step)
        res["metrics"][group] = metrics_from(step, data, stdout)
        res["steps"][step] = {"rc": rc, "sec": round(time.time() - t0, 1),
                              "file": Path(where["json"]).name if where.get("json") else ""}
        print(f"[{label}] {step} 끝 · 종료 {rc} · {res['steps'][step]['sec']}초", flush=True)
    return res


def _subprocess_runner(cmd: list[str], env: dict) -> tuple[int, str]:
    p = subprocess.run(cmd, env=env, cwd=str(ROOT), capture_output=True, text=True)
    tail = (p.stdout or "")[-4000:]
    if p.returncode:
        print((p.stderr or "")[-2000:], file=sys.stderr, flush=True)
    return p.returncode, tail


# ---------------- 기준·표 ----------------

def flat(metrics: dict) -> dict:
    out = {}
    for group, vals in (metrics or {}).items():
        for k, v in (vals or {}).items():
            out[f"{group}.{k}"] = v
    return out


def ensure_baseline(path: Path, current: dict | None, force: bool = False) -> tuple[dict | None, bool]:
    """기준을 읽는다. 없거나(force 면 언제나) 운영 결과가 있으면 그것을 기준으로 저장한다. (기준, 이번에 저장했나)."""
    if path.is_file() and not force:
        try:
            return json.loads(path.read_text(encoding="utf-8")), False
        except ValueError:
            pass
    if current is None:
        return (json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None), False
    path.parent.mkdir(parents=True, exist_ok=True)
    base = {"saved": time.strftime("%Y-%m-%d %H:%M"), **current}
    path.write_text(json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")
    return base, True


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3f}".rstrip("0").rstrip(".") if abs(v) < 100 else f"{v:.0f}"
    return str(v)


def _mark(base, new, higher: bool | None) -> str:
    if higher is None or not isinstance(base, (int, float)) or not isinstance(new, (int, float)) or base == new:
        return ""
    better = (new > base) if higher else (new < base)
    return "나아짐" if better else "나빠짐"


def model_label(run: dict | None, role: str = "answer") -> str:
    from zzaimy.generate import model_info

    return model_info.label(_dig(run or {}, f"models.{role}.llm") or {})


def table(columns: list[tuple[str, dict | None]]) -> str:
    """columns: [(열 이름, 실행 결과 {models, metrics})] — 첫 열이 기준. 뒤 열마다 기준 대비 나아짐·나빠짐을 붙인다."""
    head = ["항목"] + [name for name, _ in columns]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines.append("| 답 모델 | " + " | ".join(model_label(r) for _, r in columns) + " |")
    lines.append("| 판정 모델(review) | " + " | ".join(model_label(r, "review") for _, r in columns) + " |")
    lines.append("| 연결 | " + " | ".join(str(_dig(r or {}, "models.answer.server.conn") or "-") for _, r in columns) + " |")
    flats = [flat((r or {}).get("metrics") or {}) for _, r in columns]
    for key, name, higher in METRICS:
        if all(f.get(key) is None for f in flats):
            continue
        cells = []
        for i, f in enumerate(flats):
            cell = _fmt(f.get(key))
            if i:
                m = _mark(flats[0].get(key), f.get(key), higher)
                cell += f" ({m})" if m else ""
            cells.append(cell)
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ---------------- 실행 ----------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="검색 한 번·답 3문·갈래 6건 — 배선·속도 확인용")
    ap.add_argument("--skip", default="", help="건너뛸 단계(쉼표): search·answer·kind")
    ap.add_argument("--candidate-conn", default="", help="후보 연결 id(등록된 교내 연결) — 운영 용도 지정은 바꾸지 않는다")
    ap.add_argument("--candidate-model", default="", help="후보 서빙 이름(같은 연결의 LoRA 어댑터 이름 등)")
    ap.add_argument("--no-current", action="store_true", help="후보만 돌리고 기준 파일과만 견준다")
    ap.add_argument("--save-baseline", action="store_true", help="이번 운영 결과를 기준으로 바꿔 저장한다")
    ap.add_argument("--baseline", default=str(BASELINE))
    ap.add_argument("--parsed", default="", help="갈래 표본의 처리 결과 폴더(기본 data/inbox/parsed)")
    ap.add_argument("--env-file", default=str(ROOT / ".env.local"))
    ap.add_argument("--tag", default=time.strftime("%Y%m%d%H%M"))
    args = ap.parse_args(argv)
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    if skip - set(STEPS):
        ap.error(f"모르는 단계: {', '.join(sorted(skip - set(STEPS)))}")
    loaded = load_env_file(Path(args.env_file))
    if not loaded:
        print(f"경고: {Path(args.env_file).name} 을 읽지 못함 — 검색 임베딩·재순위가 CPU 대체로 돌아 수치가 운영과 다를 수 있다", flush=True)
    for k, v in loaded.items():
        os.environ.setdefault(k, v)                     # 셸에서 이미 준 값이 먼저
    candidate = trial_env(args.candidate_conn.strip(), args.candidate_model.strip())
    runs: list[tuple[str, dict]] = []
    if not (candidate and args.no_current):
        runs.append(("운영", {}))
    if candidate:
        runs.append(("후보", candidate))
    results: dict[str, dict] = {}
    started = time.time()
    for label, extra in runs:
        snap = model_snapshot(extra)
        print(f"[{label}] 답 모델 {model_label({'models': snap})} · 판정 모델 {model_label({'models': snap}, 'review')}", flush=True)
        r = run_steps(args.tag, "cur" if label == "운영" else "cand", extra, args.quick, skip, parsed=args.parsed)
        results[label] = {"models": snap, "quick": args.quick, "skip": sorted(skip), **r}
    current = results.get("운영")
    baseline, saved = ensure_baseline(Path(args.baseline), current, force=args.save_baseline)
    cols: list[tuple[str, dict | None]] = []
    if baseline is not None and not saved:
        cols.append((f"기준({baseline.get('saved', '')})", baseline))
    for label, r in results.items():
        cols.append((label + (" — 기준으로 저장" if saved and label == "운영" else ""), r))
    tbl = table(cols)
    out = ROOT / "data" / "eval" / f"model_swap_{args.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"made": time.strftime("%Y-%m-%d %H:%M"), "tag": args.tag, "quick": args.quick,
                               "candidate": candidate, "baseline_saved": saved,
                               "baseline": (baseline or {}).get("saved"), "runs": results, "table": tbl,
                               "elapsed_min": round((time.time() - started) / 60, 1)}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    if (baseline or {}).get("quick") is not None and current is not None and baseline.get("quick") != args.quick and not saved:
        print("주의: 기준과 이번 실행의 규모(--quick)가 달라 수치를 그대로 견줄 수 없다", flush=True)
    print(tbl)
    print("MODEL_SWAP " + json.dumps({"out": out.name, "baseline_saved": saved,
                                       "runs": {k: flat(v["metrics"]) for k, v in results.items()}}, ensure_ascii=False))
    return 0 if all(s["rc"] == 0 for r in results.values() for s in r["steps"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
