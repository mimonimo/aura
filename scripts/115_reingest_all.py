#!/usr/bin/env python3
"""문서함을 비우고 원본 파일에서 반입을 처음부터 다시 돌린다.

왜: 반입 절차가 바뀌면(문서 이름을 본문 제목으로 바꾸는 등) 예전에 들어온 문서는 그 절차를
거치지 않은 상태로 남는다. 부분 보정으로 맞추면 무엇이 절차의 결과이고 무엇이 손질의 결과인지
구분되지 않는다. 그래서 통째로 다시 넣는다 — 지식 그래프·색인도 같은 절차에서 다시 만들어진다.

지우는 것: 문서·조각·자산·규정 조각·조각 질문·대화 근거·개체 그래프·임베딩 색인.
남기는 것: 계정·프로젝트·대화 본문·연결 설정.

사용 (VM 에서):
  env PYTHONPATH=src .venv/bin/python scripts/115_reingest_all.py            # 무엇을 할지만 보여 준다
  env PYTHONPATH=src .venv/bin/python scripts/115_reingest_all.py --apply
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402

WIPE = ("regulation_chunks", "doc_chunks", "doc_assets", "chunk_questions",
        "chat_sources", "entities", "entity_links", "pii_records", "documents")
INDEXES = ("chunk_embeddings.npz", "chunk_embeddings.meta.json", "question_embeddings.npz")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = Database(Path(args.db))
    docs = db.list_documents()
    plan = [{"path": d.get("stored_path"), "doc_type": d.get("doc_type"),
             "owner": d.get("owner"), "sector": d.get("sector"), "dept": d.get("dept"),
             "project_id": d.get("project_id")} for d in docs if d.get("stored_path")]
    missing = [p for p in plan if not Path(p["path"]).exists()]
    print(f"지금 문서 {len(docs)}건 · 다시 넣을 원본 {len(plan) - len(missing)}건"
          f"{f' · 원본이 사라진 것 {len(missing)}건' if missing else ''}")
    kinds: dict[str, int] = {}
    for p in plan:
        kinds[p["doc_type"]] = kinds.get(p["doc_type"], 0) + 1
    print("  유형별:", kinds)
    if not args.apply:
        print("미리보기입니다. 실제로 비우고 다시 넣으려면 --apply 를 붙이십시오.")
        return 0

    import sqlite3

    conn = sqlite3.connect(args.db, timeout=30)
    with conn:
        for t in WIPE:
            try:
                conn.execute(f"DELETE FROM {t}")
            except sqlite3.OperationalError:
                pass                      # 없는 표는 건너뛴다
    conn.close()
    for name in INDEXES:
        p = Path(args.db).parent / name
        if p.exists():
            p.unlink()
    print("문서함을 비웠습니다.")

    from zzaimy.app.pipeline import Processor

    proc = Processor()
    ok = fail = 0
    t0 = time.time()
    for i, item in enumerate(plan, 1):
        path = Path(item["path"])
        if not path.exists():
            continue
        doc_id = db.add_document(
            filename=path.name, stored_path=str(path), doc_type=item["doc_type"] or "auto",
            owner=item["owner"] or "zzaimy", sector=item["sector"] or "common",
            dept=item["dept"] or "공통", project_id=item["project_id"])
        try:
            proc.process(db, doc_id, path)
            ok += 1
        except Exception as e:
            fail += 1
            db.update_document(doc_id, status="failed", error=f"{type(e).__name__}: {e}")
        if i % 10 == 0:
            print(f"  {i}/{len(plan)} · 성공 {ok} 실패 {fail} · {time.time() - t0:.0f}초", flush=True)
    print(f"반입 완료 — 성공 {ok} 실패 {fail} ({time.time() - t0:.0f}초)")
    print("다음: 색인(scripts/96 --apply) · 개체 그래프 재구성 · 반입 점검")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
