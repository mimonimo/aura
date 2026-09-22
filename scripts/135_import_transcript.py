#!/usr/bin/env python3
"""전사본 반입 — 쪽별 마크다운(제목 '## ', 표는 파이프 표 또는 <table>, 문단)을 문서의 조각으로 넣는다.

data/vision/<doc_id>/p<N>.md 가 있으면 그것을(쪽 순서대로), 없으면 transcript.md 하나를 읽는다.
비전 판독 경로와 같은 변환기(_md_to_chunks)를 써서 doc_chunks(제목·문단·표 JSON)를 만들고, 본문(masked_text)과
기준 조각(regulation_chunks: chunk_document → index_ready)도 다시 만든다. 공개 수집분이 아니면 본문을 마스킹한다.
parse_note 에 '외부 전사' 표시를 남긴다. 끝나면 재색인 표시(.reindex-needed) — 96 을 이어서.

실행(VM): env PYTHONPATH=src .venv/bin/python scripts/135_import_transcript.py --docs 429,451 [--by claude]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.pipeline import DocumentProcessor  # noqa: E402
from zzaimy.app.regulations import chunk_document, index_ready  # noqa: E402


def load_pages(folder: Path) -> list[tuple[int, str]]:
    pages = sorted(folder.glob("p*.md"), key=lambda p: int(p.stem[1:]) if p.stem[1:].isdigit() else 0)
    if pages:
        return [(int(p.stem[1:]), p.read_text(encoding="utf-8")) for p in pages]
    one = folder / "transcript.md"
    if one.exists():
        return [(1, one.read_text(encoding="utf-8"))]
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--docs", required=True)
    ap.add_argument("--by", default="claude", help="전사 주체 표시(parse_note)")
    args = ap.parse_args()
    db = Database(Path(args.db))
    proc = DocumentProcessor.__new__(DocumentProcessor)     # 마스커만 쓴다 — 파서 초기화는 생략
    proc._masker = None
    base = ROOT / "data" / "vision"
    done = 0
    for did in [int(x) for x in args.docs.split(",") if x.strip()]:
        doc = db.get_document(did)
        pages = load_pages(base / str(did))
        if not doc or not pages:
            print(f"[{did}] 전사본 없음"); continue
        from zzaimy.app.pii_audit import is_masking_subject

        mask = is_masking_subject(doc.get("doc_type") or "", doc.get("owner"))
        mk = (lambda s: proc._mask_str(s)) if mask else (lambda s: s)
        chunks: list[dict] = []
        texts: list[str] = []
        for no, md in pages:
            chunks += DocumentProcessor._md_to_chunks(md, mk, page_no=no)
            texts.append(md)
        full = mk("\n\n".join(texts))
        db.replace_doc_chunks(did, chunks)
        db.update_document(did, masked_text=full, parse_note=f"외부 전사({args.by}) · {len(pages)}쪽 · 표 "
                           f"{sum(1 for c in chunks if c['kind'] == 'table')}개 · 제목 {sum(1 for c in chunks if c['kind'] == 'heading')}개")
        if doc.get("doc_type") == "regulation":
            reg, dropped = index_ready(did, chunk_document(full))
            title = (db.chunks_for_docs([did]) or [{}])[0].get("reg_title") or doc["filename"]
            db.add_regulation_chunks(did, title, reg, sector=doc.get("sector") or "common")
            print(f"[{did}] {doc['filename'][:36]} — {len(pages)}쪽 · 구조 조각 {len(chunks)} · 기준 조각 {len(reg)}(잡음 {dropped} 제외)")
        else:
            print(f"[{did}] {doc['filename'][:36]} — {len(pages)}쪽 · 구조 조각 {len(chunks)}")
        done += 1
    if done:
        (Path(args.db).parent / ".reindex-needed").touch()
        print(f"반입 {done}건 — 재색인 필요(scripts/96_embed_on_thor.sh --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
