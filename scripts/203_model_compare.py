#!/usr/bin/env python3
"""생성 모델 비교 묶음 — 「지금 운영 모델 vs 후보(파인튜닝 등)」 를 같은 묶음으로 한 번에 돌리고 한 표로 합친다.

묶음(각각 --skip 으로 끌 수 있다):
- 195 답변 품질·검색·갈래 판정(scripts/195_model_swap_check.py — 운영·후보를 한 실행에서 함께 돈다)
- 201 문서 작성(한글 기안기 에이전트, scripts/201) — 운영 한 번, 후보 한 번
- 202 시나리오(여러 턴: 목표 이해·양식 고르기/만들기·절 쓰기·고쳐 쓰기·디자인) + 양식 만들기 묶음(scripts/202 --forms) — 운영 한 번, 후보 한 번
후보는 운영 용도 지정을 바꾸지 않고 하위 실행에서만 돈다(--candidate-conn·--candidate-model·--candidate-url → ZZAIMY_LLM_TRY_CONN·ZZAIMY_LLM_TRY_MODEL·ZZAIMY_LLM_TRY_URL,
llm_connections.trial_conn). 채점자(202 의 review 27B)는 두 판 모두 운영 연결로 고정이다. 결과에는 모델 이름·연결 이름만, 서버 주소는 남기지 않는다.

결과: data/eval/model_compare/<tag>/ 아래 하위 실행 결과와 합친 표 compare.md·compare.json.
사용(VM 운영 폴더에서 — 195 는 운영 색인을 읽기만 한다. .env.local 을 이 스크립트가 읽는다):
  scripts/zz_run.sh 16G env PYTHONPATH=src .venv/bin/python scripts/203_model_compare.py --candidate-conn <연결 id> [--candidate-model <서빙 이름>]
  배선 확인: ... scripts/203_model_compare.py --candidate-model <이름> --quick   (195 --quick, 201 은 기안문 하나, 202 는 양식 묶음 둘)
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
SUITES = ("195", "201", "202")


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
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


def run(cmd: list[str], log: Path) -> int:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    t0 = time.time()
    with log.open("w", encoding="utf-8") as f:
        f.write("$ " + " ".join(cmd) + "\n")
        f.flush()
        rc = subprocess.call(cmd, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT)
    print(f"  rc={rc} {round(time.time() - t0)}초 — {log.name}", flush=True)
    return rc


def newest(folder: Path, pattern: str) -> Path | None:
    hits = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


def model_of(models: dict | None) -> str:
    m = (models or {}).get("answer") or {}
    return f"{m.get('label') or '모름'} · 연결 {m.get('conn') or '모름'}"


def table_201(runs: dict[str, list[dict]]) -> str:
    keys = [("sections", "쓴 절"), ("facts", "사실 반영"), ("invented", "지어낸 숫자"), ("tables", "표"), ("diagrams", "도식"),
            ("needs_met", "작성방법 요구"), ("sec", "시간(초)"), ("peak", "가장 큰 호출")]
    labels = list(runs)
    lines = ["| 요청 | 지표 | " + " | ".join(labels) + " |", "|---|---|" + "---|" * len(labels)]
    names = list(dict.fromkeys(r["name"] for rs in runs.values() for r in rs))
    for n in names:
        for k, title in keys:
            vals = []
            for lb in labels:
                r = next((x for x in runs[lb] if x["name"] == n), {})
                v = r.get(k, r.get("error", "-") if k == "sections" else "-")
                vals.append(str(len(v)) if isinstance(v, list) else str(v)[:40])
            lines.append(f"| {n} | {title} | " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate-conn", default="")
    ap.add_argument("--candidate-url", default="", help="장부에 없는 교내 서버 주소(/v1 까지) — llm_connections.json 을 고치지 않고 잰다(ZZAIMY_LLM_TRY_URL)")
    ap.add_argument("--candidate-model", default="")
    ap.add_argument("--skip", default="", help="건너뛸 묶음(쉼표): 195·201·202")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-current", action="store_true", help="후보만(운영 결과가 이미 있을 때)")
    ap.add_argument("--env-file", default=str(ROOT / ".env.local"))
    ap.add_argument("--tag", default=time.strftime("%Y%m%d%H%M"))
    a = ap.parse_args(argv)
    cand = [x for x in (["--candidate-conn", a.candidate_conn.strip()] if a.candidate_conn.strip() else [])
            + (["--candidate-model", a.candidate_model.strip()] if a.candidate_model.strip() else [])
            + (["--candidate-url", a.candidate_url.strip()] if a.candidate_url.strip() else [])]
    if not cand:
        ap.error("--candidate-conn·--candidate-model·--candidate-url 가운데 하나는 준다")
    skip = {s.strip() for s in a.skip.split(",") if s.strip()}
    if skip - set(SUITES):
        ap.error("모르는 묶음: " + ", ".join(sorted(skip - set(SUITES))))
    loaded = load_env_file(Path(a.env_file))
    if not loaded:
        print(f"경고: {Path(a.env_file).name} 을 읽지 못함 — 측정이 CPU 대체로 돌 수 있다", flush=True)
    for k, v in loaded.items():
        os.environ.setdefault(k, v)
    os.environ.pop("ZZAIMY_LLM_TRY_CONN", None)
    os.environ.pop("ZZAIMY_LLM_TRY_MODEL", None)
    os.environ.pop("ZZAIMY_LLM_TRY_URL", None)
    out = ROOT / "data" / "eval" / "model_compare" / a.tag
    out.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    sides = ([] if a.no_current else [("운영", [])]) + [("후보", cand)]
    summary: dict = {"tag": a.tag, "candidate": {"conn": a.candidate_conn or ("장부 밖 주소" if a.candidate_url.strip() else ""),
                                                 "model": a.candidate_model}, "quick": a.quick}   # 주소는 남기지 않는다
    md = [f"# 모델 비교 {a.tag}", "", f"후보: 연결 {a.candidate_conn or ('장부 밖 주소' if a.candidate_url.strip() else '(운영 연결)')} · 서빙 이름 {a.candidate_model or '(연결 기본)'}"
          + (" · 빠른 판(배선 확인용, 수치 비교에 쓰지 않는다)" if a.quick else ""), ""]
    if "195" not in skip:
        print("[195] 답변 품질·검색·갈래", flush=True)
        cmd = [py, "scripts/195_model_swap_check.py", "--tag", f"cmp{a.tag}", *cand] + (["--quick"] if a.quick else []) \
            + (["--no-current"] if a.no_current else [])
        rc = run(cmd, out / "195.log")
        j = ROOT / "data" / "eval" / f"model_swap_cmp{a.tag}.json"
        summary["195"] = {"rc": rc, "file": j.name if j.exists() else ""}
        md += ["## 답변 품질·검색·갈래(195)", "", json.loads(j.read_text(encoding="utf-8")).get("table", "") if j.exists() else f"(결과 없음, rc={rc})", ""]
    if "201" not in skip:
        runs = {}
        for lb, extra in sides:
            print(f"[201] 문서 작성 — {lb}", flush=True)
            cmd = [py, "scripts/201_webhwp_agent_eval.py", "--label", lb, "--out", str(out / "201"), *extra] + (["--only", "기안문"] if a.quick else [])
            rc = run(cmd, out / f"201-{lb}.log")
            j = newest(out / "201", f"*-{lb}.json")
            runs[lb] = json.loads(j.read_text(encoding="utf-8")) if j else [{"name": "(없음)", "error": f"rc={rc}"}]
        summary["201"] = runs
        md += ["## 문서 작성(201)", "", " · ".join(f"{lb}: {model_of((rs[0] if rs else {}).get('models'))}" for lb, rs in runs.items()), "",
               table_201(runs), ""]
    if "202" not in skip:
        rows, frows, scen_md = {}, {}, []
        for lb, extra in sides:
            print(f"[202] 시나리오·양식 만들기 — {lb}", flush=True)
            cmd = [py, "scripts/202_webhwp_scenario_eval.py", "--label", lb, "--out", str(out / "202"), "--forms", "--forms-agent", *extra] \
                + (["--variant", "none", "--only-form", "회의록"] if a.quick else [])
            rc = run(cmd, out / f"202-{lb}.log")
            m = newest(out / "202", f"*-{lb}.md")
            scen_md.append(m.read_text(encoding="utf-8") if m else f"(결과 없음 {lb}, rc={rc})")
            rows[lb] = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((out / "202").glob(f"*-*forms-{lb}.json"))]
            f = newest(out / "202", f"*-formsset-{lb}.json")
            frows[lb] = json.loads(f.read_text(encoding="utf-8")) if f else []
        summary["202"] = {"scenario": rows, "forms": frows}
        md += ["## 시나리오·양식 만들기(202)", "", " · ".join(f"{lb}: {model_of(((frows.get(lb) or [{}])[0]).get('models'))}" for lb in frows), ""]
        md += scen_md
    (out / "compare.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "compare.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md), flush=True)
    print(f"COMPARE_DONE {out / 'compare.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
