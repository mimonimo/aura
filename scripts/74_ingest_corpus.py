"""수집 코퍼스 → RAG 기준문서(regulation chunk) 인제스트.

파이프라인(ADR-0014·브리프 절대규칙 3): 파싱 → **PII 마스킹(인덱싱 前)** →
조각화(split_regulation) → 문서·조각 DB 적재. 임베딩·재색인은 VM에서
scripts/66_reindex.sh로 별도 실행(KURE-v1, 오프라인). 이 스크립트는 무인
반복 가능(파일명 기준 idempotent).

사용:
  python scripts/74_ingest_corpus.py data/samples/국고공고 data/samples/국고계획서 \
      --db data/platform/corpus_pilot.db --sector common --dept 공통
  # 운영 반영: --db data/platform/platform.db 로 지정 후 VM에서 66_reindex.sh
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from zzaimy.app.db import Database              # noqa: E402
from zzaimy.app.pii_audit import record_mask_events  # noqa: E402
from zzaimy.app.regulations import split_regulation  # noqa: E402
from zzaimy.ingest.hwp_text import extract_text  # noqa: E402
from zzaimy.ingest.pii import PiiMasker, RawDocument  # noqa: E402


def parse_file(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    if ext in ("hwp", "hwpx"):
        return extract_text(path)
    raise ValueError(f"미지원 형식: {ext}")


def existing_filenames(db: Database) -> set[str]:
    with db._conn() as conn:  # noqa: SLF001 (내부 헬퍼 재사용)
        return {r[0] for r in conn.execute("SELECT filename FROM documents")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+", help="수집 코퍼스 디렉터리들")
    ap.add_argument("--db", default="data/platform/knowledge/corpus_pilot/corpus_pilot.db")
    ap.add_argument("--sector", default="common")
    ap.add_argument("--dept", default="공통")
    ap.add_argument("--min-chars", type=int, default=80,
                    help="이보다 짧으면 스킵(빈 양식 서식 등)")
    args = ap.parse_args()

    db = Database(args.db)
    masker = PiiMasker()
    have = existing_filenames(db)

    files = []
    for d in args.dirs:
        for p in sorted(glob.glob(os.path.join(d, "*"))):
            if os.path.isfile(p) and p.rsplit(".", 1)[-1].lower() in ("pdf", "hwp", "hwpx"):
                files.append(p)

    n_ing = n_skip = n_pii = n_chunks = n_err = 0
    for path in files:
        name = os.path.basename(path)
        if name in have:
            n_skip += 1
            continue
        try:
            text = (parse_file(path) or "").strip()
        except Exception as e:
            print(f"  [파싱ERR] {name[:44]} :: {str(e)[:40]}")
            n_err += 1
            continue
        if len(text) < args.min_chars:
            print(f"  [스킵:빈약 {len(text)}자] {name[:44]}")
            n_skip += 1
            continue
        # PII 마스킹 — 반드시 조각화·적재 前 (절대규칙 3)
        try:
            masked, events = masker.mask(RawDocument(doc_id=name, text=text))
        except Exception as e:
            print(f"  [마스킹ERR] {name[:40]} :: {str(e)[:40]}")
            n_err += 1
            continue
        chunks = split_regulation(masked.text)
        if not chunks:
            n_skip += 1
            continue
        doc_id = db.add_document(
            filename=name, stored_path=path, doc_type="regulation",
            sector=args.sector, owner="corpus")
        # 인용 이름 — 'www.ync.ac.kr__UPLOAD_…' 같은 파일 이름 대신 첫 쪽의 문서 이름(doc_title.py)
        from zzaimy.app.doc_title import resolved_title

        found, _date = resolved_title({"filename": name, "stored_path": str(path),
                                       "masked_text": masked.text[:3000], "identity": None})
        db.add_regulation_chunks(doc_id, reg_title=found or name, chunks=chunks,
                                 sector=args.sector, dept=args.dept)
        # 마스킹 기록 — 유형·건수·마스킹본 문맥만 남긴다 (/dev/pii에서 확인)
        record_mask_events(db, doc_id, masked.text, events)
        n_ing += 1
        n_pii += len(events)
        n_chunks += len(chunks)
        print(f"  [OK] {name[:46]}  조각 {len(chunks)} · PII {len(events)}")

    print(f"\n=== 인제스트 요약 (db={args.db}) ===")
    print(f"  대상 {len(files)} · 적재 {n_ing} · 스킵 {n_skip} · 오류 {n_err}")
    print(f"  총 조각 {n_chunks} · PII 마스킹 {n_pii}건")
    print(f"  → 다음: VM에서 66_reindex.sh (KURE-v1 임베딩·재색인)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
