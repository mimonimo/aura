"""OCR 글자 오류율(CER) 자동 벤치 — 디지털 PDF의 원문을 정답으로 쓴다.

원리: 텍스트 레이어가 있는 PDF를 이미지로 렌더해 OCR에 통과시키고,
원문 텍스트와 글자 단위로 대조한다. 사람 채점도, 단어 목록도 없다
(하드코딩 금지 원칙) — 어떤 OCR 구성이든 같은 잣대로 공정 비교.

무겁지 않게: 문서 1건·앞 N쪽만. 한 번에 하나(flock)로 실행.
실행: PYTHONPATH=src .venv/bin/python scripts/68_ocr_cer_bench.py <doc_id> [pages]
"""

from __future__ import annotations

import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

DB = Path("data/platform/platform.db")
REPORT = Path("docs/ocr-cer-bench.md")


def _norm(text: str) -> str:
    """대조 전 정규화 — 공백·개행 차이는 오류로 치지 않는다."""
    return re.sub(r"\s+", "", text)


def word_f1(ref: str, hyp: str) -> float:
    """어절 단위 일치 F1 — 순서 무관. 인식 오류는 어절을 깨므로 점수가 깎이고,
    읽기 순서·표 직렬화 차이는 점수에 영향을 주지 않는다."""
    from collections import Counter

    r = Counter(re.findall(r"[가-힣A-Za-z0-9]{2,}", ref))
    h = Counter(re.findall(r"[가-힣A-Za-z0-9]{2,}", hyp))
    if not r or not h:
        return 0.0
    overlap = sum((r & h).values())
    prec = overlap / max(sum(h.values()), 1)
    rec = overlap / max(sum(r.values()), 1)
    return 2 * prec * rec / max(prec + rec, 1e-9)


def cer(ref: str, hyp: str) -> float:
    """글자 오류율 = 편집거리 / 정답 길이 (밴드 제한 없는 표준 레벤슈타인)."""
    ref, hyp = _norm(ref), _norm(hyp)
    if not ref:
        return 0.0
    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, hc in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (rc != hc))
        prev = cur
    return prev[-1] / len(ref)


def page_ground_truth(pdf: Path, page_idx: int) -> str:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        tp = doc[page_idx].get_textpage()
        try:
            return tp.get_text_bounded() or ""
        finally:
            tp.close()
    finally:
        doc.close()


def render_pages(pdf: Path, n_pages: int, out_dir: Path) -> list[Path]:
    """텍스트 레이어를 지운 순수 이미지로 렌더 — OCR이 원문을 못 훔쳐보게."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    paths = []
    try:
        for i in range(min(n_pages, len(doc))):
            img = doc[i].render(scale=2.0).to_pil()
            p = out_dir / f"p{i + 1}.png"
            img.save(p)
            paths.append(p)
    finally:
        doc.close()
    return paths


def images_to_pdf(images: list[Path], out: Path) -> None:
    from PIL import Image

    ims = [Image.open(p).convert("RGB") for p in images]
    ims[0].save(out, save_all=True, append_images=ims[1:])


def main() -> None:
    doc_id = int(sys.argv[1]) if len(sys.argv) > 1 else 172
    n_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT filename, stored_path FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    conn.close()
    if row is None:
        raise SystemExit(f"문서 {doc_id} 없음")
    pdf = Path(row["stored_path"])

    import tempfile

    from zzaimy.app.pipeline import DocumentProcessor

    proc = DocumentProcessor()
    with tempfile.TemporaryDirectory(prefix="zz-cer-") as tmp:
        tmp_p = Path(tmp)
        pages = render_pages(pdf, n_pages, tmp_p)
        scan_pdf = tmp_p / "scan.pdf"
        images_to_pdf(pages, scan_pdf)

        # 원시 OCR
        text = proc._parse(scan_pdf)
        # 표준 경로의 LLM 교정까지 적용한 변형 — 문단 단위 일반 교정
        paras = [p for p in text.split("\n\n") if p.strip()]
        fixed = proc._correct_texts(paras)
        text_corrected = "\n\n".join(fixed) if fixed else text
        # 페이지 경계가 없으므로 문서 단위로 합산 대조
        ref = "".join(page_ground_truth(pdf, i) for i in range(len(pages)))
        rate = cer(ref, text)
        f1 = word_f1(ref, text)
        f1c = word_f1(ref, text_corrected)
        note = proc._last_parse_note or ""

    line = (
        f"| {date.today().isoformat()} | {row['filename'][:28]} (#{doc_id})"
        f" | {len(pages)}쪽 | {f1:.4f} | {f1c:.4f} | {rate:.4f} | {note[:40]} |"
    )
    if REPORT.exists():
        content = REPORT.read_text(encoding="utf-8").rstrip() + "\n" + line + "\n"
    else:
        content = "\n".join([
            "# OCR 글자 오류율(CER) 벤치",
            "",
            "디지털 PDF 원문을 정답으로, 렌더 이미지를 스캔처럼 OCR한 결과를",
            "글자 단위 대조. 공백 차이는 오류로 치지 않음. 낮을수록 좋다.",
            "",
            "주 지표는 어절 F1(순서 무관, 높을수록 좋음). CER은 순서 차이에",
            "민감해 참고용.",
            "",
            "| 측정일 | 문서 | 쪽수 | 어절F1(원시) | 어절F1(교정후) | CER(참고) | 처리 경로 |",
            "|---|---|---|---|---|---|---|",
            line,
        ]) + "\n"
    REPORT.write_text(content, encoding="utf-8")
    print(f"어절F1 원시={f1:.4f} 교정후={f1c:.4f} CER={rate:.4f}"
          f" ({row['filename'][:30]}, {len(pages)}쪽, {note})")
    print("CER_BENCH_DONE")


if __name__ == "__main__":
    main()
