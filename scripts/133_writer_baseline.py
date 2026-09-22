#!/usr/bin/env python3
"""③ZZAIMY-Writer 베이스라인 측정 — 학습 전 베이스 모델로 초안을 만들어 검증 결과를 기록으로 남긴다.

절대 규칙 2: 베이스라인 측정 전에 파인튜닝을 시작하지 않는다. 학습 스크립트(83)는 이 기록이 없으면 돌지 않는다.
무엇을 재나: 공고 문서마다 초안을 만들고(운영 초안 생성기 그대로), 배점 반영률(covered/total), 근거 없는 수치 수,
예산 검산 불일치 수, 섹션 수를 모은다. 사람 평가(문체·논리)는 따로 한다.
지금은 공개 국고 공고·기본계획으로 잰다(실적 자료가 없어 재료 0 — 배점 반영률의 하한선). 교내 실물 문서가 오면
같은 명령으로 다시 재서 그 값을 정본으로 삼는다.

실행(VM, 서빙 설정 필요):
  set -a; . ./.env.local; set +a
  env PYTHONPATH=src .venv/bin/python scripts/133_writer_baseline.py [--docs 517,504,527] [--limit 3]
산출: data/eval/baseline.json (+ 날짜본). DGX 에서 학습할 때는 이 파일을 DGX 의 같은 자리에 복사한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.drafter import SliceDrafter  # noqa: E402
from zzaimy.generate import llm_connections  # noqa: E402


def pick_docs(db: Database, limit: int) -> list[int]:
    """공개 수집 공고·기본계획 가운데 본문이 긴 것부터 — 실물 공고가 오면 그것을 --docs 로 준다."""
    with db._conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT id FROM documents WHERE owner='corpus' AND doc_type='regulation'"
            " AND (filename LIKE '%공고%' OR filename LIKE '%기본계획%') AND masked_text IS NOT NULL"
            " ORDER BY length(masked_text) DESC LIMIT ?", (limit,)).fetchall()
    return [int(r[0]) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--docs", default="", help="문서 id 를 쉼표로")
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--out", default=str(ROOT / "data" / "eval" / "baseline.json"))
    args = ap.parse_args()
    llm_connections.configure(Path(args.db).parent / "llm_connections.json")
    db = Database(Path(args.db))
    ids = [int(x) for x in args.docs.split(",") if x.strip()] or pick_docs(db, args.limit)
    if not ids:
        print("측정할 공고 문서가 없습니다", file=sys.stderr)
        return 2
    drafter = SliceDrafter()
    results = []
    t0 = time.time()
    for i, did in enumerate(ids, 1):
        doc = db.get_document(did)
        if not doc:
            continue
        t = time.time()
        print(f"[{i}/{len(ids)}] {did} {doc['filename'][:40]} …", flush=True)
        drafter.generate(db, did)
        doc = db.get_document(did)
        try:
            audit = json.loads(doc.get("draft_audit") or "{}")
        except ValueError:
            audit = {}
        if not audit:
            print(f"   실패 — {doc.get('coverage')}", flush=True)
            continue
        audit.update({"doc_id": did, "filename": doc["filename"], "seconds": round(time.time() - t, 1)})
        results.append(audit)
        print(f"   배점 {audit['covered_points']}/{audit['total_points']} · 근거 없는 수치 {audit['number_violations']}"
              f" · 섹션 {audit['sections']} · {audit['seconds']}초", flush=True)
    if not results:
        print("측정 결과가 없습니다 — 모델 연결을 확인하십시오", file=sys.stderr)
        return 1
    tot = sum(r["total_points"] for r in results)
    cov = sum(r["covered_points"] for r in results)
    out = {
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": results[0].get("model"),
        "n_docs": len(results),
        "coverage_rate": round(cov / tot, 4) if tot else None,
        "number_violations_per_doc": round(sum(r["number_violations"] for r in results) / len(results), 2),
        "budget_issues_per_doc": round(sum(r["budget_issues"] for r in results) / len(results), 2),
        "seconds_per_doc": round(sum(r["seconds"] for r in results) / len(results), 1),
        "note": "공개 공고·기본계획으로 잰 학습 전 베이스라인(재료 없음). 실물 문서로 재측정하면 그 값이 정본",
        "docs": results,
        "elapsed_s": round(time.time() - t0, 1),
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(p, p.parent / f"baseline-{stamp}.json")
    print(f"산출물: {p} · 배점 반영률 {out['coverage_rate']} · 근거 없는 수치 {out['number_violations_per_doc']}/문서")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
