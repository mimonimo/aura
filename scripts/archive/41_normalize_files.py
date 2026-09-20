"""수집 파일 확장자 정규화 — downloadRun류 URL 이름에는 확장자가 없다.

매직 바이트로 형식을 판별해 확장자를 붙인다. zip은 내부에 HWPX 섹션이 있으면
.hwpx, 아니면 건드리지 않는다(압축 첨부는 등록 대상 아님).

실행(맥): python scripts/41_normalize_files.py
"""

from __future__ import annotations

import zipfile
from pathlib import Path

FILES = Path("data/scraped/files")


def detect(p: Path) -> str | None:
    head = p.read_bytes()[:8]
    if head.startswith(b"%PDF"):
        return ".pdf"
    if head.startswith(b"\xd0\xcf\x11\xe0"):  # OLE — HWP 5.x
        return ".hwp"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head.startswith(b"GIF8"):
        return ".gif"
    if head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(p) as zf:
                names = zf.namelist()
            if any(n.startswith("Contents/section") for n in names):
                return ".hwpx"
            if any(n.startswith("word/") for n in names):
                return ".docx"
            if any(n.startswith("xl/") for n in names):
                return ".xlsx"
        except zipfile.BadZipFile:
            return None
    return None


KNOWN = {".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".xls", ".xlsx",
         ".jpg", ".jpeg", ".png", ".gif", ".zip", ".txt"}


def main() -> None:
    renamed = skipped = 0
    for p in sorted(FILES.iterdir()):
        # URL 조각(.do_...)이 확장자로 오인되므로 알려진 확장자만 인정한다
        if not p.is_file() or p.suffix.lower() in KNOWN:
            continue
        ext = detect(p)
        if ext is None:
            skipped += 1
            continue
        p.rename(p.with_name(p.name.rstrip("_") + ext))
        renamed += 1
    print(f"확장자 부여 {renamed}건 / 판별 불가·압축 {skipped}건")


if __name__ == "__main__":
    main()
