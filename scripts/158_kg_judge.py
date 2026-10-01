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

PROMPT = ("다음은 같은 사업·같은 연차의 사업계획서 절과 사업실적보고서 절이다. 두 절이 같은 과제·평가 항목에 대한 계획과 그 실적인가?\n"
          "제목이 같아도 다른 과제 아래의 절이면 아니다. JSON 한 줄로만 답한다: {{\"same\": true 또는 false, \"why\": \"한 문장\"}}\n\n"
          "[계획서 절] 「{pt}」 (상위: {pp})\n{pb}\n\n[실적보고서 절] 「{rt}」 (상위: {rp})\n{rb}")


def _text(db, doc_id: int, sec: dict, by_path: dict) -> tuple[str, str]:
    seqs = set(sec["props"].get("chunks") or [])
    body = "\n".join(str(c["content"])[:400] for c in db.list_doc_chunks(doc_id) if c["seq"] in seqs)[:1200]
    parent = by_path.get(sec["id"].rsplit(".", 1)[0]) if "." in sec["id"].split(":sec:")[1] else None
    return body or "(본문 없음 — 하위 절만)", (parent or {}).get("label", "-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    from zzaimy.generate import llm_connections
    from zzaimy.generate.client import VllmClient

    llm_connections.configure(ROOT / "data" / "platform" / "llm_connections.json")
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    secs = {n["id"]: n for n in kg_store.nodes(db, "section")}
    kept = [(e["src"], e["dst"]) for e in kg_store.edges(db, "plans_reports") if ":sec:" in e["src"]]
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
    rnd = random.Random(args.seed)
    client = VllmClient(role="review")
    out = {}
    for name, pool in (("kept", kept), ("dropped", dropped)):
        sample = rnd.sample(pool, min(args.n, len(pool)))
        yes = 0
        rows = []
        for a, b in sample:
            pa, pb = _text(db, secs[a]["doc_id"], secs[a], secs), _text(db, secs[b]["doc_id"], secs[b], secs)
            msg = PROMPT.format(pt=secs[a]["label"], pp=pa[1], pb=pa[0], rt=secs[b]["label"], rp=pb[1], rb=pb[0])
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
            rows.append({"plan": secs[a]["label"], "report": secs[b]["label"], "same": same, "why": str(v.get("why", ""))[:120]})
        out[name] = {"pool": len(pool), "n": len(sample), "same": yes, "rate": round(yes / max(len(sample), 1), 2), "rows": rows}
        print(f"{name}: 후보 {len(pool)} · 표본 {len(sample)} · 같다 {yes} ({out[name]['rate']:.0%})", flush=True)
        for r in rows[:6]:
            print(f"   {'O' if r['same'] else 'X'} 「{r['plan'][:28]}」↔「{r['report'][:28]}」 {r['why'][:70]}", flush=True)
    dest = ROOT / "data" / "eval" / "kg_judge.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("기록:", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
