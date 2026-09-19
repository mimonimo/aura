#!/usr/bin/env python3
"""OpenAI 호환 추론 서버를 LLM 연결로 등록한다.

화면에서도 등록할 수 있지만, 서버에서 바로 넣어야 할 때(초기 구성·복구) 쓴다.
같은 주소가 이미 있으면 새로 만들지 않고 모델 목록만 다시 받는다.
API 키는 인자로 받지 않는다 — 키가 필요한 연결은 화면에서 넣는다.

사용:
  python scripts/79_register_llm_connection.py --name "교내 DGX" \
      --base-url http://211.170.162.110:11434/v1 [--model qwen3.6:35b] [--kind vllm]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.generate import llm_connections as lc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", default="")
    ap.add_argument("--kind", default="vllm", choices=sorted(lc.KINDS))
    ap.add_argument("--store", default=str(ROOT / "data" / "platform" / "llm_connections.json"))
    args = ap.parse_args()

    lc.configure(Path(args.store))
    base = args.base_url.rstrip("/")
    found = [c for c in lc.list_public() if c.get("base_url", "").rstrip("/") == base]
    if found:
        conn = lc.get(found[0]["id"])
        print(f"이미 등록된 연결입니다 — {conn['name']} ({conn['id']})")
    else:
        conn = lc.add(name=args.name, kind=args.kind, base_url=args.base_url,
                      model=args.model, api_key="")
        print(f"연결을 등록했습니다 — {conn['name']} ({conn['id']})")

    r = lc.fetch_catalog(conn)
    if r["ok"]:
        lc.set_catalog(conn["id"], r["models"], r["source"])
        print(f"모델 목록 {len(r['models'])}개 ({r['source']})")
        for m in r["models"]:
            print(f"   {m['id']}")
    else:
        print(f"모델 목록을 받지 못했습니다 — {r['error']}")

    p = lc.probe(conn)
    print("연결 확인:", "정상" if p["ok"] else f"실패 — {p['error']}")
    return 0 if p["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
