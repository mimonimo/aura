"""OCR 구조 점검 — 문서 1건을 파이프라인으로 다시 읽고 표·그림·캡션 구조를 보고한다 (운영 VM).

무엇을 보나: 조각 종류별 개수(text/heading/table/image/image_text), 표 평문(text)
표본, 그림 속 글자(OCR) 표본, 찾은 캡션·각주, 표 안의 수치 개수, 파싱 경로(parse_note),
소요 시간. ADR-0016(표 평문·그림 글자·캡션 계약)이 실문서에서 성립하는지 확인하는 용도.

기본은 DB에 쓰지 않는다 — 파싱 → 마스킹 → 구조 조각까지 메모리에서만 만든다.
`--write`를 주면 process()로 실제 재처리해 doc_chunks/doc_assets를 교체한다(원본은 그대로).
CPU 무거운 작업이라 코어 절반(OMP 4)·nice 15로 돈다 — 웹을 굶기지 않는다.

실행 (VM, 저장소 루트에서):
  env PYTHONPATH=src HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \\
    .venv/bin/python scripts/79_ocr_structure_check.py <doc_id> [--write] \\
    [--db data/platform/platform.db]
마지막 줄 STRUCTURE_RESULT 로 요약을 낸다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")
os.environ.setdefault("OMP_THREAD_LIMIT", "4")
sys.path.insert(0, "src")

_CAPTION_HINT = re.compile(
    r"^\s*[\[<(〈《「【]?\s*(?:표|그림|사진|도표|그래프|Table|Figure|Fig)", re.I
)


def _report(chunks: list[dict], assets: list[dict], parse_note: str | None, elapsed: float) -> dict:
    from zzaimy.app.render import table_context, table_text
    from zzaimy.verify.numbers import extract_numbers

    kinds = Counter(c["kind"] for c in chunks)
    with_bbox = sum(1 for c in chunks if c.get("bbox"))
    tables = [c for c in chunks if c["kind"] == "table"]
    fig_texts = [c for c in chunks if c["kind"] == "image_text"]
    captions: list[str] = []
    notes: list[str] = []
    table_numbers = 0
    stored_text = 0
    for c in tables:
        cap, note = table_context(c["content"])
        if cap:
            captions.append(cap)
        if note:
            notes.append(note)
        try:
            if json.loads(c["content"]).get("text"):
                stored_text += 1
        except (ValueError, TypeError, AttributeError):
            pass
        table_numbers += len(extract_numbers(table_text(c["content"])))
    fig_captions = [
        ft["content"].splitlines()[0] for ft in fig_texts
        if ft["content"].strip() and _CAPTION_HINT.match(ft["content"])
    ]

    print(f"파싱 경로: {parse_note or '일반'} · {elapsed:.1f}s")
    print(f"조각: {dict(kinds)} · bbox 있음 {with_bbox} · 그림 자산 {len(assets)}")
    print(f"표: {len(tables)}개 · 평문(text) 저장 {stored_text} · 캡션 {len(captions)}"
          f" · 각주 {len(notes)} · 표 안 수치 토큰 {table_numbers}")
    for cap in captions[:8]:
        print(f"  표 캡션: {cap[:80]}")
    for note in notes[:4]:
        print(f"  표 각주: {note[:80]}")
    if tables:
        sample = table_text(tables[0]["content"]).splitlines()
        print(f"표 평문 표본 (첫 표, {len(sample)}줄):")
        for ln in sample[:12]:
            print(f"  | {ln[:110]}")
    print(f"그림 글자(image_text): {len(fig_texts)}조각 · 그림 캡션 {len(fig_captions)}")
    for cap in fig_captions[:8]:
        print(f"  그림 캡션: {cap[:80]}")
    if fig_texts:
        body = fig_texts[0]["content"]
        print("그림 글자 표본 (첫 조각):")
        for ln in body.splitlines()[:8]:
            print(f"  > {ln[:110]}")
    return {
        "tables": len(tables), "table_text": stored_text, "captions": len(captions),
        "notes": len(notes), "image_text": len(fig_texts), "fig_captions": len(fig_captions),
        "images": len(assets), "table_numbers": table_numbers,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("doc_id", type=int)
    ap.add_argument("--db", default="data/platform/platform.db")
    ap.add_argument("--write", action="store_true", help="process()로 실제 재처리해 DB에 반영")
    args = ap.parse_args()

    try:
        os.nice(15)
    except (OSError, AttributeError):
        pass

    from zzaimy.app.db import Database
    from zzaimy.app.pipeline import DocumentProcessor

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB가 없다: {db_path}")
        return 2
    db = Database(db_path)
    doc = db.get_document(args.doc_id)
    if doc is None:
        print(f"문서 {args.doc_id} 없음")
        return 2
    path = Path(doc["stored_path"])
    print(f"문서 #{doc['id']} {doc['filename']} · 유형 {doc.get('doc_type')} · 원본 {path}"
          f" ({'있음' if path.exists() else '없음'})")
    if not path.exists():
        return 2
    cmd = DocumentProcessor._tesseract_cmd()
    tess = f"{cmd[0]} ({cmd[1] or 'default'})" if cmd else "없음 — 그림 글자 OCR 생략"
    print(f"tesseract: {tess}")

    proc = DocumentProcessor()
    t0 = time.time()
    if args.write:
        proc.process(db, args.doc_id, path)
        elapsed = time.time() - t0
        doc = db.get_document(args.doc_id) or {}
        print(f"재처리 상태: {doc.get('status')} · {doc.get('error') or ''}")
        chunks = db.list_doc_chunks(args.doc_id)
        assets = [a for a in db.list_doc_assets(args.doc_id) if a["kind"] != "scan"]
        summary = _report(chunks, assets, doc.get("parse_note"), elapsed)
    else:
        raw = proc._parse(path)
        do_mask = doc.get("doc_type") != "regulation"
        chunks = proc._structured_chunks(do_mask=do_mask) or []
        if not chunks:
            from zzaimy.app.pipeline import _split_chunks

            text = proc._mask_str(raw) if do_mask else raw
            chunks = _split_chunks(text)
            print("구조 조각 없음 — 문단 분할 폴백")
        elapsed = time.time() - t0
        assets = [{"kind": "image", "page_no": pg, "path": str(p)} for pg, p in proc._last_images]
        # 접수 문서는 마스킹 전 원문이다 — 로그에 개인정보가 남지 않게 미리보기도 마스커를 거친다
        preview = raw[:400]
        if (doc.get("doc_type") or "auto") != "regulation":
            try:
                from zzaimy.ingest.pii import PiiMasker, RawDocument
                preview = PiiMasker().mask(RawDocument(doc_id=int(doc["id"]), text=preview))[0].text
            except Exception as e:  # noqa: BLE001
                preview = f"(마스커 로드 실패 {type(e).__name__} — 미리보기 생략)"
        print(f"본문 {len(raw)}자 · 앞부분(마스킹 후): {preview[:160]!r}")
        summary = _report(chunks, assets, proc._last_parse_note, elapsed)
        print("(DB에 쓰지 않음 — 반영하려면 --write)")
    flags = " ".join(f"{k}={v}" for k, v in summary.items())
    print(f"STRUCTURE_RESULT doc={args.doc_id} {flags} elapsed={elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
