#!/usr/bin/env python3
"""바꿔 말한 질의 세트 — 규정 문구를 모르는 담당자가 상황으로 묻는 질문을 흉내 낸다.

왜: 합성 질의(51)는 정답 조각의 낱말을 그대로 써 어휘 검색에 유리하다. 실제 담당자는
'임용권자' 대신 '계약직 뽑으면 누가 최종 결재해?'처럼 묻는다. 같은 정답을 두고 핵심 명사를
피해 바꿔 쓴 질문으로 검색·리랭커의 의미 이해를 따로 잰다.

입력: data/interim/synth_queries.jsonl (정답 본문 gold_* 포함)
출력: data/interim/synth_queries_paraphrase.jsonl — 같은 정답 필드, practical 칸에 바꿔 쓴 질문만 둔다.
사용:  PYTHONPATH=src .venv/bin/python scripts/97_paraphrase_queries.py [--n 200]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

SRC = ROOT / "data" / "interim" / "synth_queries.jsonl"
OUT = ROOT / "data" / "interim" / "synth_queries_paraphrase.jsonl"
PROMPT = """다음은 교내 규정을 찾는 검색 질문이다. 규정 원문을 읽어 보지 않은 행정 담당자가
실제 업무 상황에서 물을 법한 말로 바꿔 써라.
- 원래 질문의 핵심 명사(규정 용어)는 되도록 쓰지 말고 상황·목적으로 풀어 말한다
- 묻는 내용(정답)은 바꾸지 않는다
- 한 문장, 질문 하나만 출력한다

원래 질문: {q}
참고한 규정 조각의 제목: {title} / {heading}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    args = ap.parse_args()

    from zzaimy.generate import llm_connections
    from zzaimy.generate.client import VllmClient

    llm_connections.configure(ROOT / "data" / "platform" / "llm_connections.json")
    client = VllmClient()
    rows = [json.loads(x) for x in SRC.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if (r.get("practical") or "").strip() and r.get("gold_text")]
    rows = random.Random(20260920).sample(rows, min(args.n, len(rows)))
    print(f"모델 {client.model} · 대상 {len(rows)}행", flush=True)

    def one(r: dict) -> dict | None:
        try:
            resp = client.client.chat.completions.create(
                model=client.model, temperature=0.7, max_tokens=120,
                messages=[{"role": "user", "content": PROMPT.format(
                    q=r["practical"], title=r.get("gold_title") or "", heading=r.get("gold_heading") or "")}])
            text = (resp.choices[0].message.content or "").strip().split("\n")[0].strip(' "')
        except Exception:
            return None
        if not text or text == r["practical"]:
            return None
        out = {k: v for k, v in r.items() if k.startswith("gold") or k == "chunk_id"}
        out.update({"practical": text, "requirement": "", "keyword": "", "original": r["practical"]})
        return out

    with ThreadPoolExecutor(max_workers=6) as pool:
        made = [x for x in pool.map(one, rows) if x]
    OUT.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in made) + "\n", encoding="utf-8")
    print(f"저장 {OUT.relative_to(ROOT)} — {len(made)}행", flush=True)
    for x in made[:5]:
        print(f"  {x['original']}\n   → {x['practical']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
