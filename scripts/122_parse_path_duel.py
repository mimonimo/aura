#!/usr/bin/env python3
"""디지털 PDF 파싱 경로 비교 — 구조 추출(MinerU)과 글자층 직독을 같은 문서로 잰다.

왜: 지금은 모든 PDF 가 MinerU 를 거친다(표·2단·읽기 순서 보존). 디지털 PDF 는 그 위에 원본
글자를 좌표로 얹으므로 글자 자체는 같다. 남는 차이는 '구조'인데, 그 값어치가 시간에 비해
얼마인지 재 본 적이 없다. 재반입에서 큰 문서 한 건이 20~30분씩 걸려 그 값을 알아야 한다.

재는 것: 걸린 시간 · 글자 수 · 표 개수 · 표제 개수 · 원본 글자층 대비 글자 회수율.
바꾸지 않는다 — 수치만 낸다.

사용 (VM 에서):
  env PYTHONPATH=src .venv/bin/python scripts/122_parse_path_duel.py <문서id> [문서id ...]
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.pipeline import DocumentProcessor  # noqa: E402


def words(text: str) -> set[str]:
    return {w for w in (text or "").split() if len(w) > 1}


def main() -> int:
    ids = [int(x) for x in sys.argv[1:]]
    if not ids:
        print("문서 id 를 주십시오 (디지털 PDF 인 문서)")
        return 2
    db = Database(ROOT / "data" / "platform" / "platform.db")
    print(f"{'문서':<6}{'경로':<14}{'초':>7}{'글자':>9}{'표':>5}{'표제':>6}{'회수율':>8}")
    for doc_id in ids:
        doc = db.get_document(doc_id)
        if doc is None:
            print(f"{doc_id}: 없는 문서")
            continue
        path = Path(doc["stored_path"])
        if not path.exists():
            print(f"{doc_id}: 원본 파일 없음")
            continue
        # 기준: 원본 글자층(pypdfium 직독) — 회수율의 분모
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        base_words = words(" ".join(
            pdf[i].get_textpage().get_text_range() for i in range(min(len(pdf), 40))))
        for label, env in (("구조 추출", ""), ("글자층 직독", "1")):
            if env:
                os.environ["ZZAIMY_NO_MINERU_DEFAULT"] = env
            else:
                os.environ.pop("ZZAIMY_NO_MINERU_DEFAULT", None)
            proc = DocumentProcessor()
            t0 = time.time()
            try:
                text = proc._parse(path)
            except Exception as e:
                print(f"{doc_id:<6}{label:<14} 실패 {type(e).__name__}")
                continue
            spent = time.time() - t0
            got = words(text)
            recall = len(got & base_words) / max(1, len(base_words))
            tables = text.count("|") // 10          # 대략치 — 표 행의 셀 구분자
            heads = sum(1 for ln in text.splitlines() if ln.startswith("#"))
            print(f"{doc_id:<6}{label:<14}{spent:>7.1f}{len(text):>9}{tables:>5}{heads:>6}{recall:>8.3f}")
    os.environ.pop("ZZAIMY_NO_MINERU_DEFAULT", None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
