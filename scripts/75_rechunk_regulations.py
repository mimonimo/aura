"""규정 조각 재분할 — 조각화 규칙이 바뀐 뒤 기존 문서에 소급 적용한다.

규칙은 반입 경로와 같다: chunk_document(조문형·서술형 자동 판별) → index_ready(잡음 관문).
2026-09-14: 문장 속 조 참조에서 끊지 않음 · 본문 없는 조각 이웃과 병합 · 중복 제거 · 1,400자 상한.
2026-09-22: 크기 분할 뒤에도 얇은 조각 병합 · 표 블록은 행 단위 · 본문 없는 번호 줄은 표제여도 제외.

원문 선택: 기본은 documents.masked_text. 비전 판독·오타 교정을 거친 문서는
조각 본문이 교정본이라(masked_text는 교정 전) 기존 조각을 이어 붙여 쓴다.
reg_title·sector·dept는 기존 조각에서 그대로 가져온다.

실행 전 DB 파일과 조각 표를 백업한다(되돌리기: 백업 DB를 제자리에 복사).
조각 id가 바뀌므로 임베딩·합성 질의 세트가 낡는다 → 66_reindex.sh를 이어서.

실행(VM): env PYTHONPATH=src .venv/bin/python scripts/75_rechunk_regulations.py [--dry-run]
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path("data/platform/platform.db")
BACKUP_DIR = Path("data/platform/backup")


def _source_text(row: dict, old_chunks: list[dict]) -> tuple[str, str]:
    note = row.get("parse_note") or ""
    corrected = ("오타 교정" in note) or ("비전 판독" in note)
    mt = (row.get("masked_text") or "").strip()
    old_len = sum(len(c["content"]) for c in old_chunks)
    if corrected or not mt or len(mt) < 0.8 * old_len:
        return "\n".join(c["content"] for c in old_chunks), "기존 조각 결합"
    return mt, "masked_text"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import sys
    sys.path.insert(0, "src")
    from zzaimy.app.db import Database
    from zzaimy.app.regulations import chunk_document, index_ready

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    docs = [dict(r) for r in conn.execute(
        "SELECT id, filename, parse_note, masked_text FROM documents"
        " WHERE doc_type='regulation' ORDER BY id")]
    old_all = [dict(r) for r in conn.execute(
        "SELECT id, doc_id, reg_title, heading, content, sector, dept, access_level"
        " FROM regulation_chunks ORDER BY id")]
    conn.close()
    by_doc: dict[int, list[dict]] = {}
    for c in old_all:
        by_doc.setdefault(c["doc_id"], []).append(c)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if not args.dry_run:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DB, BACKUP_DIR / f"platform-{stamp}.db")
        with gzip.open(BACKUP_DIR / f"regulation_chunks-{stamp}.json.gz", "wt",
                       encoding="utf-8") as f:
            json.dump(old_all, f, ensure_ascii=False)
        print(f"백업: {BACKUP_DIR}/platform-{stamp}.db · regulation_chunks-{stamp}.json.gz")

    db = Database(DB)
    tot_old = tot_new = 0
    for d in docs:
        old = by_doc.get(d["id"], [])
        if not old:
            continue
        text, src = _source_text(d, old)
        chunks, dropped = index_ready(d["id"], chunk_document(text))
        tot_old += len(old)
        tot_new += len(chunks)
        print(f"{d['id']:4d} {d['filename'][:34]:<34} {src:<10} {len(old):4d} → {len(chunks):4d}"
              + (f" (잡음 {dropped} 제외)" if dropped else ""))
        if args.dry_run or not chunks:
            continue
        db.add_regulation_chunks(
            d["id"], old[0]["reg_title"], chunks,
            sector=old[0].get("sector") or "common", dept=old[0].get("dept") or "공통",
            access_level=old[0].get("access_level") or "public",
        )
    print(f"합계 {tot_old} → {tot_new} 조각" + (" (dry-run)" if args.dry_run else ""))
    if not args.dry_run:
        (DB.parent / ".reindex-needed").touch()
        print("재색인 필요 표시 — scripts/66_reindex.sh 를 이어서 실행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
