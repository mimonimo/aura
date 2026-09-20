#!/usr/bin/env python3
"""사업 정보(문서 정체)를 아직 읽지 않은 문서를 한꺼번에 읽는다.

화면의 '사업 정보 읽기'와 같은 경로(doc_identity.extract — 본문에 있는 값만 남긴다)를
기본 LLM 연결로 돌린다. 대화 주제·같은 사업 문서 연결·그래프가 이 값을 쓴다.
이미 사업(program) 값이 있는 문서는 건너뛴다.

사용:  PYTHONPATH=src .venv/bin/python scripts/93_identify_all.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from zzaimy.app import doc_identity as di
    from zzaimy.app.db import Database
    from zzaimy.generate import llm_connections
    from zzaimy.generate.client import VllmClient

    db = Database(ROOT / "data" / "platform" / "platform.db")
    llm_connections.configure(ROOT / "data" / "platform" / "llm_connections.json")
    client = VllmClient()
    print(f"모델: {client.model}", flush=True)

    def call(prompt: str) -> str:
        r = client.client.chat.completions.create(
            model=client.model, temperature=0.0, max_tokens=1200,
            messages=[{"role": "user", "content": prompt}])
        return r.choices[0].message.content or ""

    docs = [d for d in db.list_documents() if d.get("status") == "reviewed"]
    todo = []
    for d in docs:
        try:
            ident = json.loads(d.get("identity") or "{}")
        except ValueError:
            ident = {}
        if not ident.get("program"):
            todo.append(d)
    if args.limit:
        todo = todo[: args.limit]
    print(f"대상 {len(todo)}건 (전체 검토 완료 {len(docs)}건)", flush=True)
    ok = 0
    for d in todo:
        t0 = time.time()
        parts = [c.get("content") or "" for c in db.list_doc_chunks(d["id"])]
        body = "\n".join(p for p in parts if p.strip()) or (d.get("masked_text") or "")
        r = di.extract(body, call)
        if r["ok"]:
            db.set_doc_identity(d["id"], r["identity"])
            ok += 1
        print(f"  [{d['id']}] {d['filename'][:34]} → "
              f"{r['identity'].get('program', '') if r['ok'] else r['error']} ({time.time() - t0:.0f}초)",
              flush=True)
    print(f"완료 — 읽음 {ok}/{len(todo)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
