#!/usr/bin/env python3
"""못 채운 절 모아 보기 — 절 작성이 비우거나 덜 채운 절을 원인 갈래별로 센다(app/fill_gaps).

어디가 약한지 본다: no_material·material_mismatch 가 많은 절은 검색·짝 절(PLAN_COUNTERPART)을, needs_user_document 는
담당자에게 미리 받을 자료 목록(프로젝트 만들기 안내)을, partial 은 값 입력 흐름을 손볼 곳이다.
--episodes 면 지난 절 작성 기록(drafting_episodes.jsonl — 재료·초안·답글)에 같은 규칙을 소급해 센다(기록 이전 분석용, 쓰지 않음).
실행(VM): env PYTHONPATH=src .venv/bin/python scripts/181_fill_gap_report.py [--data data/platform] [--top 30] [--episodes]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zzaimy.app import fill_gaps  # noqa: E402

REASONS = ("needs_user_document", "no_material", "material_mismatch", "partial")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/platform")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--episodes", action="store_true")
    args = ap.parse_args()
    if args.episodes:
        import json
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        n = 0
        src = Path(args.data) / "drafting_episodes.jsonl"
        for line in (src.read_text(encoding="utf-8").splitlines() if src.is_file() else []):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            n += 1
            rec = fill_gaps.analyze(e.get("section") or "", e.get("draft") or [], e.get("materials") or "", e.get("reply") or "")
            if rec:
                fill_gaps.record(tmp, rec)
        print(f"지난 절 작성 {n}회에 소급")
        args.data = str(tmp)
    rows = fill_gaps.summary(Path(args.data))
    print(f"기록 {sum(r['total'] for r in rows)}건 · 절 {len(rows)}개")
    print("절 | 합계 | " + " | ".join(REASONS) + " | 필요한 자료")
    for r in rows[: args.top]:
        print(f"{r['heading'][:30]} | {r['total']} | " + " | ".join(str(r.get(k, 0)) for k in REASONS) + f" | {r['needs'][:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
