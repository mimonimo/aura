#!/usr/bin/env python3
"""그래프 연결 정밀도 점검 — 이은 절 쌍과 버린 후보 쌍을 표본으로 27B 에 판정시킨다(평가에만, 그래프에는 쓰지 않는다).

반복 기록(docs/notes/2026-10-01-kg-iterations.md)의 '연결 정밀도'와 '놓친 짝' 어림:
  kept    = 지금 그래프의 plans_reports 절 관계
  dropped = 같은 사업·연차 계획서·실적보고서에서 제목만 같은데 지금 규칙이 잇지 않은 쌍(1차 반복 전 규칙이 잇던 것)
dropped 에서 '같다'가 많으면 맞는 짝을 놓친 것이다.

사용(VM): env PYTHONPATH=src .venv/bin/python scripts/158_kg_judge.py --n 20
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.graph import kg_store, sections  # noqa: E402

PROMPT_V1 = ("다음은 같은 사업·같은 연차의 사업계획서 절과 사업실적보고서 절이다. 두 절이 같은 과제·평가 항목에 대한 계획과 그 실적인가?\n"
          "제목이 같아도 다른 과제 아래의 절이면 아니다. 절 번호(20-2, 25-2 등)는 문서마다 원래 다르므로 번호로 판단하지 말고 내용과 상위 과제로 판단한다.\n"
          "JSON 한 줄로만 답한다: {{\"same\": true 또는 false, \"why\": \"한 문장\"}}\n\n"
          "[계획서 절] 「{pt}」 (상위: {pp})\n{pb}\n\n[실적보고서 절] 「{rt}」 (상위: {rp})\n{rb}")
# v2(11차 반복 뒤): 판정자가 상위 장 이름·사례 번호로 판단해 본문이 같은 사례(겹침 0.9)를 '다르다'고 했다. 보고서는 장을 다시 짜고
# (사례를 「컨설팅 결과 반영」 장에 모음) 사례 번호를 바꾼다 — 절 번호와 같은 이유로 내용으로 판단하게 한다. v1 수치와 함께 낸다.
PROMPT = ("다음은 같은 사업·같은 연차의 사업계획서 절과 사업실적보고서 절이다. 두 절이 같은 과제·평가 항목(또는 같은 사례)에 대한 계획과 그 실적인가?\n"
          "절 번호(20-2, 25-2 등), 사례 번호(우수사례 1·2), 상위 장의 이름과 위치는 보고서가 계획서와 다르게 짜는 일이 많으므로 그것만으로 판단하지 말고 "
          "본문이 같은 과제·사례를 다루는지로 판단한다. 본문이 다른 과제·사례를 다루면 제목이 같아도 아니다.\n"
          "JSON 한 줄로만 답한다: {{\"same\": true 또는 false, \"why\": \"한 문장\"}}\n\n"
          "[계획서 절] 「{pt}」 (상위: {pp})\n{pb}\n\n[실적보고서 절] 「{rt}」 (상위: {rp})\n{rb}")


def _text(db, doc_id: int, sec: dict, by_path: dict) -> tuple[str, str]:
    if ":sec:" not in sec["id"]:
        # 문서 전체(과제 코드로 이은 계획서) — 표지가 아니라 목차(장·절 제목)와 각 장 첫 문단으로 보여 준다
        heads = sorted((s for k, s in by_path.items() if k.startswith(sec["id"] + ":sec:") and k.split(":sec:")[1].count(".") <= 1),
                       key=lambda s: [int(x) if x.isdigit() else 0 for x in s["id"].split(":sec:")[1].split(".")])
        chunks = {c["seq"]: str(c["content"]) for c in db.list_doc_chunks(doc_id)}
        parts = []
        for h in heads[:14]:
            first = next((chunks.get(q, "") for q in (h["props"].get("chunks") or []) if chunks.get(q)), "")
            parts.append(f"- {h['label'][:50]}: {first[:120]}")
        return ("[문서 목차와 장 첫 문단]\n" + "\n".join(parts))[:1600], "문서 전체"
    seqs = set(sec["props"].get("chunks") or [])
    body = "\n".join(str(c["content"])[:400] for c in db.list_doc_chunks(doc_id) if c["seq"] in seqs)[:1200]
    tail = sec["id"].split(":sec:")[1] if ":sec:" in sec["id"] else ""
    parent = by_path.get(sec["id"].rsplit(".", 1)[0]) if "." in tail else None
    return body or "(본문 없음 — 하위 절만)", (parent or {}).get("label", "-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--all-kept", action="store_true", help="이은 쌍은 표본 대신 전부 판정")
    ap.add_argument("--sample-kept", type=int, default=0, help="이은 쌍을 사업마다 이만큼 표본(사업 여럿을 고르게 잴 때)")
    ap.add_argument("--out", default="kg_judge.json")
    ap.add_argument("--prompt", choices=("v1", "v2"), default="v2", help="판정 지시 판(v1=상위 과제로도 판단, v2=내용으로)")
    args = ap.parse_args()
    from zzaimy.generate import llm_connections
    from zzaimy.generate.client import VllmClient

    llm_connections.configure(ROOT / "data" / "platform" / "llm_connections.json")
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    secs = {n["id"]: n for n in kg_store.nodes(db, "section")}
    # 계획서 문서 전체 ↔ 보고서 절(과제 코드로 이은 RISE 꼴)도 판정한다 — 문서 노드는 절처럼 다룬다(본문 = 문서 앞 조각들)
    for n in kg_store.nodes(db, "doc"):
        secs[n["id"]] = {**n, "props": {**n["props"], "chunks": list(range(0, 12))}}
    kept = [(e["src"], e["dst"]) for e in kg_store.edges(db, "plans_reports") if ":sec:" in e["dst"]]
    basis_of = {(e["src"], e["dst"]): e["basis"] + ("·문서" if ":sec:" not in e["src"] else "")
                for e in kg_store.edges(db, "plans_reports") if ":sec:" in e["dst"]}
    kept_set = set(kept)
    docs = {n["doc_id"]: n for n in kg_store.nodes(db, "doc")}
    dropped = []
    for e in kg_store.edges(db, "plans_reports"):
        if ":sec:" in e["src"]:
            continue
        p, r = int(e["src"].split(":")[1]), int(e["dst"].split(":")[1])
        ps = [s for sid, s in secs.items() if sid.startswith(f"doc:{p}:sec:")]
        rs = [s for sid, s in secs.items() if sid.startswith(f"doc:{r}:sec:")]
        rk = {}
        for s in rs:
            rk.setdefault(sections.title_key(s["label"]), []).append(s["id"])
        for s in ps:
            k = sections.title_key(s["label"])
            if len(k) < 4:
                continue
            for rid in rk.get(k, []):
                if (s["id"], rid) not in kept_set:
                    dropped.append((s["id"], rid))
    # 문서 → 사업(사업별로 따로 잰다 — 한 사업에 맞춘 개선을 막는다, CLAUDE.md 절대 규칙 12)
    prog_of_node: dict[str, str] = {}
    for e in kg_store.edges(db, "contains"):
        if e["src"].startswith("program:"):
            prog_of_node[e["dst"]] = e["src"]
    for e in kg_store.edges(db, "contains"):
        if e["src"].startswith("year:") and e["src"] in prog_of_node:
            prog_of_node[e["dst"]] = prog_of_node[e["src"]]

    def prog_of(sec_id: str) -> str:
        return prog_of_node.get(sec_id.split(":sec:")[0], "(미분류)")
    # 사업 이름 머리글(「대구광역시 지역혁신중심 대학지원체계(RISE)」)끼리의 쌍은 놓친 짝 후보에서 뺀다 — 본문이 없어 판정이
    # 제목만 보고 '같다'를 주어 재현율 어림을 부풀렸다(all12). 157 의 is_program_heading 과 같은 규칙
    surf = {}
    for n in kg_store.nodes(db, "program"):
        pr = n.get("props") or {}
        surf[n["id"]] = sorted({re.sub(r"[\s.()·\-_]+", "", x).upper() for x in [n.get("label") or ""] + list(pr.get("names") or [])
                                + list(pr.get("acronyms") or []) if len(x) >= 3}, key=len, reverse=True)

    def heading(title: str, prog: str) -> bool:
        t = re.sub(r"[\s.()·\-_「」\[\]]+", "", title or "").upper()
        hit = False
        for su in surf.get(prog, []):
            if su and su in t:
                t = t.replace(su, "")
                hit = True
        return hit and len(re.sub(r"[^가-힣A-Z0-9]", "", t)) <= 6
    before = len(dropped)
    dropped = [(a, b) for a, b in dropped if not heading((secs.get(a) or {}).get("label") or "", prog_of(a))]
    print(f"놓친 짝 후보: 사업 이름 머리글 쌍 {before - len(dropped)}개 뺌", flush=True)
    rnd = random.Random(args.seed)
    client = VllmClient(role="review")
    out = {}
    for name, pool in (("kept", kept), ("dropped", dropped)):
        if name == "kept" and args.sample_kept:
            groups: dict[str, list] = {}
            for pr in pool:
                groups.setdefault(prog_of(pr[0]) + basis_of.get(pr, ""), []).append(pr)
            sample = [x for g in groups.values() for x in rnd.sample(g, min(args.sample_kept, len(g)))]
        elif name == "kept" and args.all_kept:
            sample = list(pool)
        else:
            sample = rnd.sample(pool, min(args.n, len(pool)))
        yes = 0
        rows = []
        for a, b in sample:
            pa, pb = _text(db, secs[a]["doc_id"], secs[a], secs), _text(db, secs[b]["doc_id"], secs[b], secs)
            msg = (PROMPT_V1 if args.prompt == "v1" else PROMPT).format(pt=secs[a]["label"], pp=pa[1], pb=pa[0], rt=secs[b]["label"], rp=pb[1], rb=pb[0])
            resp = client.client.chat.completions.create(
                model=client.model, messages=[{"role": "user", "content": msg}], temperature=0, max_tokens=400,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}})
            raw = re.sub(r"<think>.*?</think>", "", resp.choices[0].message.content or "", flags=re.S)
            m = re.search(r"\{.*\}", raw, re.S)
            try:
                v = json.loads(m.group(0)) if m else {}
            except json.JSONDecodeError:
                v = {}
            same = bool(v.get("same"))
            yes += same
            rows.append({"plan": secs[a]["label"], "report": secs[b]["label"], "same": same, "why": str(v.get("why", ""))[:120],
                         "program": prog_of(a), "plan_id": a, "report_id": b, "basis": basis_of.get((a, b), "")})
        out[name] = {"pool": len(pool), "n": len(sample), "same": yes, "rate": round(yes / max(len(sample), 1), 2), "rows": rows}
        print(f"{name}: 후보 {len(pool)} · 표본 {len(sample)} · 같다 {yes} ({out[name]['rate']:.0%})", flush=True)
        by_prog: dict[str, list[int]] = {}
        for r in rows:
            key = r["program"] + (f" [{r['basis']}]" if r.get("basis") else "")
            by_prog.setdefault(key, [0, 0])
            by_prog[key][0] += 1
            by_prog[key][1] += r["same"]
        out[name]["by_program"] = {k: {"n": n, "same": y, "rate": round(y / n, 2)} for k, (n, y) in by_prog.items()}
        for k, (n, y) in sorted(by_prog.items()):
            print(f"   {k}: {y}/{n} ({y / max(n, 1):.0%})", flush=True)
        for r in rows[:6]:
            print(f"   {'O' if r['same'] else 'X'} 「{r['plan'][:28]}」↔「{r['report'][:28]}」 {r['why'][:70]}", flush=True)
    dest = ROOT / "data" / "eval" / args.out
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("기록:", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
