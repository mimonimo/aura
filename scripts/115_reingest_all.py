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


SOURCES = [
    # (폴더, 문서 유형, 설명) — 앞에서부터 순서대로 올린다
    ("data/scraped/iacf", "regulation", "영남이공대학교 산학협력단 규정"),
    ("data/external", "recruit", "외부 기관 공고·안내"),
    ("data/scraped/files", "auto", "교내 내려받기 문서"),
]
EXTS = {".pdf", ".hwp", ".hwpx", ".docx", ".png", ".jpg", ".jpeg", ".xlsx"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="원본 폴더마다 최대 몇 건까지")
    args = ap.parse_args()

    db = Database(Path(args.db))
    plan: list[tuple[Path, str]] = []
    for rel, doc_type, label in SOURCES:
        folder = ROOT / rel
        files = sorted(p for p in folder.glob("*") if p.is_file() and p.suffix.lower() in EXTS)
        if args.limit:
            files = files[: args.limit]
        print(f"  {label}: {len(files)}건 ({rel})")
        plan += [(p, doc_type) for p in files]
    print(f"지금 문서함 {len(db.list_documents())}건 → 새로 올릴 것 {len(plan)}건")
    if not args.apply:
        print("미리보기입니다. 비우고 새로 올리려면 --apply 를 붙이십시오.")
        return 0

    import sqlite3

    conn = sqlite3.connect(args.db, timeout=30)
    with conn:
        for t in WIPE:
            try:
                conn.execute(f"DELETE FROM {t}")
            except sqlite3.OperationalError:
                pass
    conn.close()
    for name in INDEXES:
        f = Path(args.db).parent / name
        if f.exists():
            f.unlink()
    inbox = Path(args.db).parent / "inbox"
    if inbox.exists():
        for f in inbox.iterdir():
            if f.is_file():
                f.unlink()
    print("문서함을 비웠습니다 — 이제 원본에서 올립니다.", flush=True)

    from zzaimy.app.pipeline import DocumentProcessor

    proc = DocumentProcessor()
    ok = fail = 0
    t0 = time.time()
    for i, (path, doc_type) in enumerate(plan, 1):
        dest = inbox / f"{path.stem[:60]}{path.suffix.lower()}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(path.read_bytes())          # 올린 파일은 문서함으로 들어온다
        doc_id = db.add_document(filename=path.name, stored_path=str(dest), doc_type=doc_type)
        try:
            proc.process(db, doc_id, dest)
            ok += 1
        except Exception as e:
            fail += 1
            db.update_document(doc_id, status="failed", error=f"{type(e).__name__}: {e}")
        if i % 5 == 0 or i == len(plan):
            done = time.time() - t0
            print(f"  {i}/{len(plan)} · 성공 {ok} 실패 {fail} · {done / 60:.1f}분"
                  f" · 남은 시간 {(len(plan) - i) * done / max(i, 1) / 60:.0f}분", flush=True)
    print(f"반입 완료 — 성공 {ok} 실패 {fail} ({(time.time() - t0) / 60:.1f}분)")
    print("다음: 색인(scripts/96 --apply) · 개체 그래프 · 반입 점검")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
