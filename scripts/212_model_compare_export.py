#!/usr/bin/env python3
"""모델 비교 결과 모으기 — 210(보류분)·211(맥락)·202(문서 작성) 결과를 관리자 화면이 읽는 꼴로 모은다.

쓰는 곳: <dest>/<run-id>/ (기본 data/eval/model_compare — scripts/203 과 같은 자리)
- summary.json  {run_id, created, arms[{key,label,model,endpoint,kind}], blocks[{key,title,metrics,pairwise,per_dimension,per_type}], notes}
- samples.json  대표 문항 10~20 — 입력(이름·연락처 가림), 정답(있으면), 모델마다 답·점수, 채점 까닭
- docs.json + docs/<비교 대상>/*.hwpx   202 회차마다 만든 한글 파일
- index.html    같은 내용을 한 쪽으로(크롬으로 열어 캡처)
파일은 임시 파일에 쓰고 이름을 바꿔(원자적) 화면이 반쯤 쓴 파일을 읽지 않게 한다. 주소(IP)는 남기지 않는다 — endpoint 는 「토르 02 :8001」 같은 이름.

사용(VM): .venv/bin/python scripts/212_model_compare_export.py --run-id sft-20261024 --work ~/ops/sft_eval \\
   --arm "open-nvfp4|오픈 모델 NVFP4|zzaimy-writer|토르 02 :8001|open" --arm "sft-merged|SFT 병합 NVFP4|writer-sft-merged|토르 02 :8002|sft-merged"
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
DIMS = ["지시 충실", "맥락 이해", "형식", "근거 충실", "문체", "완결성"]
LABELS = {
    "chars": "답 글자 수", "chrf": "정답 겹침(chrF)", "rougeL": "정답 겹침(ROUGE-L)", "gold_frame": "정답 형식 일치",
    "invented_any": "질문에 없는 숫자가 든 답 비율", "len_ratio": "정답 대비 길이", "out_tokens": "출력 토큰", "in_tokens": "입력 토큰",
    "truncated": "잘린 답 비율", "sec": "문항당 시간(초)", "ttft": "첫 글자까지(초)", "tok_per_sec": "초당 토큰", "n": "문항 수",
    "table": "표를 쓴 답 비율", "unknown": "「모름·확인 필요」 표시 비율",
    "multiturn_rate": "여러 번 주고받은 값 이어 쓰기 통과율", "longctx_rate": "긴 자료 사실 찾기 통과율", "conflict_rate": "「없으면 확인 필요」 통과율",
    "overall_rate": "맥락 이해 전체 통과율", "회차": "회차 수", "절 채움": "절 채움 비율", "목표 유지": "목표 유지 비율",
    "고쳐 쓰기 반영": "고쳐 쓰기 반영(0~2)", "시간(초)": "대화 전체 시간(초)",
    "frame_prefix": "학습셋 틀(「답변:」 줄)이 든 답 비율", "frame_answers": "학습셋 틀이 든 채팅 답 비율",
    "frame_memo": "학습셋 틀이 든 작업 메모 비율", "format_stop": "단계 형식 오류로 멈춘 턴 비율", "ask_only": "묻기만 하고 끝난 턴 비율",
    "문서 만듦": "문서(작업본)를 만든 회차 비율",
    "frame_no_system": "틀 비율 — 지시 없는 채팅", "frame_platform": "틀 비율 — 플랫폼 답변 지시", "frame_train": "틀 비율 — 학습셋 지시",
    "gold_lp": "정답 익숙함(토큰당 평균 로그확률, 0에 가까울수록 학습 꼴)", "gold_first_lp": "정답 첫 토큰 로그확률", "gold_lp_n": "정답 익숙함 잰 문항 수",
}
PERCENT = {"gold_frame", "frame_prefix", "frame_answers", "frame_memo", "format_stop", "ask_only", "문서 만듦", "frame_no_system", "frame_platform", "frame_train", "invented_any", "truncated", "table", "unknown", "multiturn_rate", "longctx_rate", "conflict_rate", "overall_rate",
           "절 채움", "목표 유지"}


def fmt(key: str, v) -> str:
    if v is None or v == "":
        return ""
    if key in PERCENT and isinstance(v, (int, float)):
        return f"{v * 100:.1f}%"
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:,.0f}"
    return str(v)


KIND_TITLE = {"multiturn": "여러 번 주고받은 값 이어 쓰기", "longctx": "긴 자료(약 1.2만 토큰) 속 사실 찾기", "conflict": "「자료에 없으면 확인 필요」 지키기"}


def _load(name: str, file: str):
    """같은 폴더 → 운영 저장소 scripts(홑 파일 사본을 ~/ops 에서 돌릴 때) 차례로 찾는다."""
    p = next((d / file for d in (HERE, Path.home() / "zzaimy-capstone" / "scripts") if (d / file).exists()), None)
    if p is None:
        return None
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


FRAME = re.compile(r"(?:^|\n)\s*(?:\*\*)?답변(?:\*\*)?\s*:")   # 210 FRAME 과 같다(학습셋 틀의 줄머리 「답변:」)


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


PHONE = re.compile(r"\d{2,3}[-.\s]\d{3,4}[-.\s]\d{4}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
TITLED = re.compile(r"([가-힣]{2,4})(\s?)(교수|팀장|과장|처장|단장|센터장|원장|총장|부총장|주무관|담당자|연구원|조교|학과장|소장|실장|님)")
LABELED = re.compile(r"(성명|이름|담당자|작성자|책임자|대표자)(\s*[:：]\s*)([가-힣]{2,4})")


def mask(text: str) -> str:
    """사람 이름·연락처 가림 — 직함 앞 이름, 「성명: 」 뒤 이름, 전화, 전자우편."""
    t = PHONE.sub("○○○-○○○○-○○○○", text or "")
    t = EMAIL.sub("○○○@○○○", t)
    t = LABELED.sub(lambda m: m.group(1) + m.group(2) + "○" * len(m.group(3)), t)
    t = TITLED.sub(lambda m: "○" * len(m.group(1)) + m.group(2) + m.group(3), t)
    return t


# ── 보류분(210) ──
def heldout_block(work: Path, arms: list[dict], s210) -> tuple[dict | None, dict]:
    """본 비교 대상(open·sft-merged)은 모두 생성한 문항으로, 대조군(sft-lora)은 오픈 모델과 짝지은 문항으로 따로 센다 —
    대조군이 일부 문항만 있어도 본 비교의 문항 수가 줄지 않게."""
    run = work / "test-run"
    have = [a["key"] for a in arms if (run / f"gen-{a['key']}.jsonl").exists()]
    kind = {a["key"]: a["kind"] for a in arms}
    main = [k for k in have if kind[k] != "sft-lora"]
    ctl = [k for k in have if kind[k] == "sft-lora"]
    if not main or s210 is None:
        return None, {}
    data = work / "test.jsonl"
    items = s210.load_items(data, work / "test-index.jsonl")
    import contextlib
    import io

    def report(models, pairs):
        class A:  # 210 phase_report 가 읽는 인자
            pass
        a = A()
        a.models, a.pairs, a.judge_model, a.temperature, a.max_tokens, a.data = models, pairs, "zzaimy-writer(토르 03)", 0.2, 2048, str(data)
        with contextlib.redirect_stdout(io.StringIO()):
            return s210.phase_report(items, a, run)

    mech_keys = ["chrf", "rougeL", "len_ratio", "invented_any", "table", "gold_frame", "frame_prefix", "unknown", "truncated", "sec", "out_tokens", "chars"]
    ctl_reps = {c: report([main[0], c], [(main[0], c)]) for c in ctl}
    pairs = [(main[0], b) for b in main[1:]] + [(main[i], main[j]) for i in range(1, len(main)) for j in range(i + 1, len(main))]
    rep = report(main, pairs)                                   # 마지막에 돌려 report.json 이 본 비교가 되게
    full = json.loads((run / "report.json").read_text(encoding="utf-8"))
    metrics = {arm: {**{k: rep["mech"]["전체"][arm][k] for k in mech_keys}, "n": rep["n"]} for arm in main}
    for c, cr in ctl_reps.items():
        metrics[c] = {**{k: cr["mech"]["전체"][c][k] for k in mech_keys}, "n": cr["n"]}
    for r in [rep, *ctl_reps.values()]:
        for arm, g in (r.get("goldlp") or {}).items():          # 학습이 실제로 얹혔는지(정답 로그확률, 앞 40문항)
            if arm in metrics:
                metrics[arm].update({"gold_lp": g["mean_lp"], "gold_first_lp": g["first_lp"], "gold_lp_n": g["n"]})
    pairwise = []
    for r, control in [(rep, False)] + [(cr, True) for cr in ctl_reps.values()]:
        for name, p in r["pairs"].items():
            s = p["groups"]["전체"]["종합"]
            pairwise.append({"a": p["candidate"], "b": p["baseline"], "win": s["win"], "tie": s["tie"], "loss": s["loss"], "n": s["n"],
                             "score": s["score"], "ci_low": s["ci95"][0], "ci_high": s["ci95"][1], "control": control,
                             "by_dimension": {k: {kk: p["groups"]["전체"][k][kk] for kk in ("win", "tie", "loss", "score")} for k in DIMS if k in p["groups"]["전체"]},
                             "position_bias_A_share": p["position_bias_A_share"], "flip_rate": p["flip_rate"]})
    have = main
    def sc(g, arm, d):
        x = rep.get("scores", {}).get(g, {}).get(arm) or {}
        return x.get(d) if x.get("n") else None
    per_dim = {d: {arm: sc("전체", arm, d) for arm in have} for d in DIMS}
    per_type = []
    for g in [x for x in rep["mech"] if x != "전체"]:
        row = {"type": g, "n": rep["mech"][g]["n"],
               "metrics": {arm: {k: rep["mech"][g][arm][k] for k in ("chrf", "rougeL", "invented_any", "len_ratio")} for arm in have},
               "per_dimension": {arm: {d: sc(g, arm, d) for d in DIMS} for arm in have},
               "pairwise": [{"a": p["candidate"], "b": p["baseline"], **{k: p["groups"][g]["종합"][k] for k in ("win", "tie", "loss", "n", "score")},
                             "ci_low": p["groups"][g]["종합"]["ci95"][0], "ci_high": p["groups"][g]["종합"]["ci95"][1]}
                            for p in rep["pairs"].values() if g in p["groups"]]}
        per_type.append(row)
    block = {"key": "heldout220", "title": f"시험 문항 {rep['n']}개(학습에 쓰지 않은 것)", "n": rep["n"], "metrics": metrics,
             "pairwise": pairwise, "per_dimension": per_dim, "per_type": per_type}
    speed = {arm: rep["speed"][arm] for arm in have if arm in rep.get("speed", {})}
    return block, {"rows": full["rows"], "items": {it["id"]: it for it in items}, "speed": speed,
                   "gens": {arm: s210.read_cache(run / f"gen-{arm}.jsonl") for arm in have}}


def context_block(work: Path, arms: list[dict]) -> tuple[dict | None, dict]:
    p = work / "context-run" / "report.json"
    if not p.exists():
        return None, {}
    rep = json.loads(p.read_text(encoding="utf-8"))
    keys = [a["key"] for a in arms if a["key"] in rep["arms"]]
    if not keys:
        return None, {}
    metrics = {}
    for arm in keys:
        d = rep["arms"][arm]
        tot = sum(x["n"] for x in d.values())
        metrics[arm] = {**{f"{k}_rate": x["rate"] for k, x in d.items()}, "overall_rate": round(sum(x["ok"] for x in d.values()) / max(1, tot), 3),
                        "invented_any": round(sum(x["invented_any"] * x["n"] for x in d.values()) / max(1, tot), 3)}
    per_type = [{"type": KIND_TITLE.get(k, k), "key": k, "n": rep["arms"][keys[0]].get(k, {}).get("n", 0),
                 "metrics": {arm: rep["arms"][arm].get(k, {}) for arm in keys}} for k in KIND_TITLE]
    return ({"key": "context", "title": "맥락 이해(가상 값·공개 자료 30문항)", "metrics": metrics, "pairwise": [], "per_dimension": {},
             "per_type": per_type, "longctx_in_tokens": rep.get("longctx_in_tokens")},
            {"rows": rep["rows"], "cases": json.loads((work / "context-run" / "cases.json").read_text(encoding="utf-8")),
             "gens": {arm: _read_jsonl(work / "context-run" / f"gen-{arm}.jsonl") for arm in keys}})


def _read_jsonl(p: Path) -> dict:
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                if not d.get("error"):
                    out[d["key"]] = d
    return out


def doc_block(work: Path, arms: list[dict], dest: Path, s202) -> tuple[dict | None, list[dict]]:
    metrics, per_run, docs = {}, [], []
    for a in arms:
        folder = work / "202" / a["key"]
        runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob(f"*-noforms-{a['key']}-*.json"))]
        # 작업본을 못 만든 회차(에이전트가 묻기만 되풀이 등)도 빼지 않는다 — 실패가 결과다
        if not runs:
            continue
        ms = [s202.metrics(r) if s202 else {} for r in runs]
        for r, m in zip(runs, ms):                        # 채팅 답·작업 메모에 학습셋 틀이 새어 나왔나, 에이전트 단계가 형식에서 멈췄나(턴마다)
            turns = r.get("turns") or []
            m["문서 만듦"] = 0 if r.get("error") and not r.get("form") else 1
            if m["문서 만듦"] == 0:
                m.setdefault("쓴 절", 0)
                m.setdefault("절 채움", 0.0)
            if turns:
                n = len(turns)
                m["frame_answers"] = round(sum(bool(FRAME.search(t.get("answer") or "")) for t in turns) / n, 3)
                m["frame_memo"] = round(sum(bool(FRAME.search(t.get("memo") or "")) for t in turns) / n, 3)
                m["format_stop"] = round(sum(t.get("stop") == "format" for t in turns) / n, 3)
                m["ask_only"] = round(sum(bool(t.get("actions")) and set(t["actions"]) <= {"ask_user", "search", "finish"}
                                          and "ask_user" in t["actions"] for t in turns) / n, 3)
            if r.get("error"):
                m["오류"] = str(r["error"])[:60]
        keys = sorted({k for m in ms for k in m})
        avg = {}
        for k in keys:
            xs = [m[k] for m in ms if isinstance(m.get(k), (int, float))]
            if xs:
                avg[k] = round(sum(xs) / len(xs), 2)
        metrics[a["key"]] = {**avg, "회차": len(runs)}
        for r, m in zip(runs, ms):
            per_run.append({"arm": a["key"], "label": r.get("label"), "metrics": m,
                            "model": ((r.get("models") or {}).get("answer") or {}).get("label")})
            for tag, title in (("hwpx_text", "글(고쳐 쓰기까지)"), ("hwpx", "디자인까지")):
                src = Path(r.get(tag) or "")
                if src.is_file():
                    rel = Path("docs") / a["key"] / src.name
                    (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest / rel)
                    docs.append({"arm": a["key"], "run": r.get("label"), "title": f"{a['label']} · {r.get('label')} · 사업계획서 {title}",
                                 "path": str(rel), "bytes": src.stat().st_size})
    if not metrics:
        return None, docs
    return ({"key": "doc202", "title": "문서 작성(양식 없이 시작해 11번 주고받기, 모델마다 2회)", "metrics": metrics, "pairwise": [],
             "per_dimension": {k: {arm: metrics[arm].get(k) for arm in metrics} for k in ("구체성", "논리", "문체", "근거")},
             "per_type": per_run}, docs)


# ── 대표 문항 ──
def pick_samples(hctx: dict, cctx: dict, arms: list[dict], n_heldout: int = 12) -> list[dict]:
    out = []
    if hctx:
        rows = [r for r in hctx["rows"] if r.get("scores")]
        keys = [a["key"] for a in arms if a["key"] in hctx["gens"]]
        by_group: dict[str, list] = {}
        for r in rows:
            by_group.setdefault(r["group"], []).append(r)

        def gap(r):  # 모델끼리 점수 차가 큰 것과 비슷한 것을 섞어 고른다
            tot = [sum((r["scores"].get(k) or {}).get(d, 0) for d in DIMS) for k in keys if k in r["scores"]]
            return (max(tot) - min(tot)) if len(tot) > 1 else 0

        chosen = []
        groups = sorted(by_group)
        per = max(1, n_heldout // max(1, len(groups)))
        for g in groups:
            rs = sorted(by_group[g], key=lambda r: (-gap(r), r["id"]))
            chosen += rs[:max(1, per // 2)] + rs[len(rs) // 2: len(rs) // 2 + (per - per // 2)]
        for r in chosen[:n_heldout]:
            it = hctx["items"][r["id"]]
            last = it["messages"][-1]["content"]
            prior = sum(1 for m in it["messages"] if m["role"] == "user") - 1
            reason = ""
            for pr in r.get("pairs", {}).values():
                reason = (pr.get("reasons") or [""])[0]
                break
            out.append({"id": r["id"], "type": r["group"], "block": "heldout220",
                        "input": mask(last[:2500]) + ("…" if len(last) > 2500 else ""), "prior_turns": prior,
                        "gold": mask(it["gold"][:2500]),
                        "outputs": {k: mask(hctx["gens"][k][r["id"]]["text"][:4000]) for k in keys if r["id"] in hctx["gens"][k]},
                        "scores": {k: {d: (r["scores"].get(k) or {}).get(d) for d in DIMS} for k in keys if k in r.get("scores", {})},
                        "pairwise": {name: pr["result"]["종합"] for name, pr in r.get("pairs", {}).items()},
                        "judge_reason": mask(reason) or mask(next(((r["scores"].get(k) or {}).get("까닭", "") for k in keys if k in r["scores"]), ""))})
    if cctx:
        keys = list(cctx["gens"])
        for kind in KIND_TITLE:
            c = next((c for c in cctx["cases"] if c["kind"] == kind and all(c["id"] in cctx["gens"][k] for k in keys)), None)
            if not c:
                continue
            res = {row["arm"]: {"통과": row["ok"]} for row in cctx["rows"] if row["id"] == c["id"]}
            txt = c["messages"][-1]["content"]
            if kind == "longctx":
                txt = "[자료 약 2만 자 — 공개 공고·기본계획, 깊이 %d%% 에 지침 한 문장]\n" % int(c["depth"] * 100) + txt.split("[질문]")[-1]
            elif kind == "multiturn":
                txt = "\n".join(f"[{ {'user': '사용자', 'assistant': '도우미'}[m['role']] }] {m['content']}" for m in c["messages"][1:])
            out.append({"id": c["id"], "type": KIND_TITLE[kind], "block": "context", "input": txt, "gold": json.dumps(c["expect"], ensure_ascii=False),
                        "outputs": {k: cctx["gens"][k][c["id"]]["text"][:4000] for k in keys}, "scores": res, "judge_reason": "코드 채점(값 포함·표시 여부)"})
    return out


# ── 한 쪽 보고 ──
CSS = """:root{--bg:#fbfbfa;--fg:#1d1d1f;--mut:#6b6b70;--line:#e3e3e0;--card:#fff;--a0:#5b6b8c;--a1:#c2703d;--a2:#3d8c6b;--a3:#8c3d7a}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#17181a;--fg:#ececec;--mut:#a0a0a6;--line:#2c2d30;--card:#1f2023}}
:root[data-theme="dark"]{--bg:#17181a;--fg:#ececec;--mut:#a0a0a6;--line:#2c2d30;--card:#1f2023}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 "Pretendard","Noto Sans KR",system-ui,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 10px;padding-top:8px;border-top:1px solid var(--line)}
h3{font-size:16px;margin:20px 0 8px}.mut{color:var(--mut);font-size:13px}table{border-collapse:collapse;width:100%;margin:8px 0;font-size:14px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{font-weight:600;background:var(--card)}
td.n{text-align:right;font-variant-numeric:tabular-nums}.wrap{overflow-x:auto}
.bar{display:flex;height:18px;border-radius:3px;overflow:hidden;min-width:220px}.bar span{display:block;height:100%}
.w{background:var(--a2)}.t{background:var(--line)}.l{background:var(--a1)}
.sample{border:1px solid var(--line);border-radius:8px;background:var(--card);padding:14px;margin:14px 0}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px}.cols pre,.inp{white-space:pre-wrap;word-break:break-word;font:13px/1.55 inherit;margin:0;
background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;max-height:420px;overflow:auto}
.tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;background:var(--line);margin-right:6px}
.hb{display:flex;align-items:center;gap:6px}.hb i{display:inline-block;height:10px;border-radius:2px}"""


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def render_html(summary: dict, samples: list[dict], docs: list[dict]) -> str:
    arms = {a["key"]: a for a in summary["arms"]}
    color = {k: f"var(--a{i % 4})" for i, k in enumerate(arms)}
    L = [f"<main><h1>모델 비교 — 오픈 모델과 SFT 학습 모델</h1><p class='mut'>{esc(summary['run_id'])} · {esc(summary['created'])} · "
         "바뀌는 것은 모델뿐(같은 질문·같은 생성 설정·같은 채점자)</p>",
         "<table><tr><th>비교 대상</th><th>이름</th><th>모델 이름</th><th>서버</th><th>종류</th></tr>"]
    for a in summary["arms"]:
        L.append(f"<tr><td><span class='tag' style='background:{color[a['key']]};color:#fff'>{esc(a['key'])}</span></td><td>{esc(a['label'])}</td>"
                 f"<td>{esc(a['model'])}</td><td>{esc(a['endpoint'])}</td><td>{esc(a['kind'])}</td></tr>")
    L.append("</table>")
    for b in summary["blocks"]:
        L.append(f"<h2>{esc(b['title'])}</h2>")
        if b.get("pairwise"):
            L.append("<h3>둘 중 나은 답 고르기(채점 모델이 두 답의 순서를 바꿔 두 번 고름)</h3><div class='wrap'><table><tr><th>비교</th><th>문항</th><th>이김·비김·짐</th>"
                     "<th></th><th>점수(95% 구간)</th></tr>")
            for p in b["pairwise"]:
                n = max(1, p["n"])
                L.append(f"<tr><td>{esc(p['a'])} 대 {esc(p['b'])}{' (대조군: 같은 모델 두 번)' if p.get('control') else ''}</td><td class='n'>{p['n']}</td><td class='n'>{p['win']} · {p['tie']} · {p['loss']}</td>"
                         f"<td><div class='bar'><span class='w' style='width:{100 * p['win'] / n:.1f}%'></span><span class='t' style='width:{100 * p['tie'] / n:.1f}%'>"
                         f"</span><span class='l' style='width:{100 * p['loss'] / n:.1f}%'></span></div></td>"
                         f"<td class='n'>{p.get('score', '')} ({p['ci_low']}~{p['ci_high']})</td></tr>")
            L.append("</table></div><p class='mut'>점수 = (이김 + 비김/2)/n, 0.5 가 같음. 초록 = 앞 모델이 나음, 주황 = 뒤 모델이 나음.</p>")
        pd = {d: v for d, v in (b.get("per_dimension") or {}).items() if any(x is not None for x in v.values())}
        if pd:
            L.append("<h3>항목 점수(1~5, 모델마다 따로 채점)</h3><div class='wrap'><table><tr><th>항목</th>" + "".join(f"<th>{esc(k)}</th>" for k in arms) + "</tr>")
            for d, v in pd.items():
                cells = []
                for k in arms:
                    x = v.get(k)
                    cells.append(f"<td><div class='hb'><i style='width:{(x or 0) * 24:.0f}px;background:{color[k]}'></i>{'' if x is None else x}</div></td>")
                L.append(f"<tr><td>{esc(d)}</td>{''.join(cells)}</tr>")
            L.append("</table></div>")
        if b.get("metrics"):
            ks = sorted({k for m in b["metrics"].values() for k in m})
            L.append("<h3>지표</h3><div class='wrap'><table><tr><th>지표</th>" + "".join(f"<th>{esc(k)}</th>" for k in b["metrics"]) + "</tr>")
            for k in ks:
                L.append(f"<tr><td>{esc(LABELS.get(k, k))}</td>" + "".join(f"<td class='n'>{esc(fmt(k, b['metrics'][a].get(k)))}</td>" for a in b["metrics"]) + "</tr>")
            L.append("</table></div>")
        if b["key"] == "heldout220" and b.get("per_type"):
            L.append("<h3>종류별</h3><div class='wrap'><table><tr><th>종류</th><th>문항</th><th>나은 답 점수</th><th>정답 겹침(chrF)</th><th>항목 점수 합(30점)</th></tr>")
            for t in b["per_type"]:
                pw = " / ".join(f"{p['a']} 대 {p['b']} {p['score']}" for p in t["pairwise"])
                ch = " / ".join(f"{k} {fmt('chrf', v['chrf'])}" for k, v in t["metrics"].items())
                sc = " / ".join(f"{k} {round(sum(x for x in v.values() if x), 1)}" for k, v in t["per_dimension"].items() if any(v.values()))
                L.append(f"<tr><td>{esc(t['type'])}</td><td class='n'>{t['n']}</td><td>{esc(pw)}</td><td>{esc(ch)}</td><td>{esc(sc)}</td></tr>")
            L.append("</table></div>")
        if b["key"] == "context" and b.get("per_type"):
            L.append("<h3>종류별 통과</h3><table><tr><th>종류</th>" + "".join(f"<th>{esc(k)}</th>" for k in b["metrics"]) + "</tr>")
            for t in b["per_type"]:
                L.append(f"<tr><td>{esc(t['type'])}</td>" + "".join(f"<td class='n'>{v.get('ok', '-')}/{v.get('n', '-')}</td>" for v in t["metrics"].values()) + "</tr>")
            L.append("</table>")
    if docs:
        L.append("<h2>문서 작성 결과 파일</h2><ul>" + "".join(f"<li>{esc(d['title'])} — {esc(d['path'])}</li>" for d in docs) + "</ul>")
    if summary.get("notes"):
        L.append("<h2>읽을 때 주의</h2><ul>" + "".join(f"<li>{esc(n)}</li>" for n in summary["notes"]) + "</ul>")
    L.append("<h2>대표 문항</h2><p class='mut'>사람 이름·연락처는 가렸다. 입력은 마지막 질문만(앞 대화는 횟수만).</p>")
    for s in samples:
        L.append(f"<div class='sample'><div><span class='tag'>{esc(s['id'])}</span><span class='tag'>{esc(s['type'])}</span>"
                 + (f"<span class='mut'>앞서 주고받은 대화 {s['prior_turns']}번</span>" if s.get("prior_turns") else "") + "</div>"
                 f"<h3>입력</h3><div class='inp'>{esc(s['input'])}</div><h3>답</h3><div class='cols'>")
        for k, txt in s["outputs"].items():
            sc = s.get("scores", {}).get(k) or {}
            scs = " · ".join(f"{d} {v}" for d, v in sc.items() if v is not None)
            L.append(f"<div><div><span class='tag' style='background:{color.get(k, 'var(--line)')};color:#fff'>{esc(k)}</span>"
                     f"<span class='mut'>{esc(scs)}</span></div><pre>{esc(txt)}</pre></div>")
        L.append("</div>")
        if s.get("pairwise"):
            L.append("<p class='mut'>나은 답: " + esc(" · ".join(f"{k} → {v}" for k, v in s["pairwise"].items())) + "</p>")
        if s.get("judge_reason"):
            L.append(f"<p class='mut'>채점 까닭: {esc(s['judge_reason'])}</p>")
        if s.get("gold"):
            L.append(f"<details><summary class='mut'>정답(학습셋)</summary><div class='inp'>{esc(s['gold'])}</div></details>")
        L.append("</div>")
    L.append("</main>")
    return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>모델 비교 보고</title><style>{CSS}</style></head><body>" + "".join(L) + "</body></html>")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="모델 비교 결과 모으기(관리자 화면·한 쪽 보고)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--work", default=str(Path.home() / "ops" / "sft_eval"))
    ap.add_argument("--dest", default=str(ROOT / "data" / "eval" / "model_compare"))
    ap.add_argument("--arm", action="append", required=True, help="「키|이름|서빙 이름|서빙 자리|open·sft-lora·sft-merged」")
    ap.add_argument("--note", action="append", default=[])
    ap.add_argument("--html-out", default="", help="index.html 을 여기에도")
    a = ap.parse_args(argv)
    arms = []
    for s in a.arm:
        k, label, model, endpoint, kind = (x.strip() for x in s.split("|"))
        if kind not in ("open", "sft-lora", "sft-merged"):
            ap.error(f"모르는 종류: {kind}")
        arms.append({"key": k, "label": label, "model": model, "endpoint": endpoint, "kind": kind})
    work, dest = Path(a.work).expanduser(), Path(a.dest).expanduser() / a.run_id
    dest.mkdir(parents=True, exist_ok=True)
    s210 = _load("s210", "210_sft_heldout_compare.py")
    if s210 is None and (work / "210_sft_heldout_compare.py").exists():
        spec = importlib.util.spec_from_file_location("s210", work / "210_sft_heldout_compare.py")
        s210 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(s210)
    try:
        s202 = _load("s202", "202_webhwp_scenario_eval.py")
    except Exception:
        s202 = None
    blocks, notes = [], list(a.note)
    hb, hctx = heldout_block(work, arms, s210)
    if hb:
        blocks.append(hb)
    cb, cctx = context_block(work, arms)
    if cb:
        blocks.append(cb)
    db, docs = doc_block(work, arms, dest, s202)
    if db:
        blocks.append(db)
    fr = work / "format-run" / "report.json"
    if fr.exists():
        frep = json.loads(fr.read_text(encoding="utf-8"))
        keymap = {"지시 없음": "frame_no_system", "플랫폼 답변 지시": "frame_platform", "학습셋 지시": "frame_train"}
        fm = {arm: {keymap[k]: v["rate"] for k, v in d.items() if k in keymap} for arm, d in frep["arms"].items()
              if arm in {x["key"] for x in arms}}
        if fm:
            blocks.append({"key": "format", "title": f"답 꼴(평범한 업무 질문 {frep['prompts']}개 × 지시 세 가지)", "metrics": fm, "pairwise": [],
                           "per_dimension": {}, "per_type": []})
    if hctx.get("speed"):
        blocks.append({"key": "speed", "title": "속도(시험 문항 20개, 한 번에 하나씩)", "metrics": hctx["speed"], "pairwise": [],
                       "per_dimension": {}, "per_type": []})
    summary = {"run_id": a.run_id, "created": time.strftime("%Y-%m-%d %H:%M"), "arms": arms, "blocks": blocks, "notes": notes,
               "labels": LABELS, "percent_keys": sorted(PERCENT)}
    samples = pick_samples(hctx, cctx, arms)
    write_atomic(dest / "summary.json", json.dumps(summary, ensure_ascii=False, indent=1))
    write_atomic(dest / "samples.json", json.dumps(samples, ensure_ascii=False, indent=1))
    write_atomic(dest / "docs.json", json.dumps(docs, ensure_ascii=False, indent=1))
    page = render_html(summary, samples, docs)
    write_atomic(dest / "index.html", page)
    if a.html_out:
        write_atomic(Path(a.html_out).expanduser(), page)
    print(f"EXPORT_DONE {dest} 묶음 {len(blocks)} · 대표 {len(samples)} · 파일 {len(docs)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
