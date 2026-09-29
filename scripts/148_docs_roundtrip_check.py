#!/usr/bin/env python3
"""한글 → docx → 구글 독스 왕복 품질 검사 — 어떤 한글 문서든 독스에서 깔끔한가를 쪽 단위 잣대로 잰다(사용자 지시 2026-09-28).

문서마다: 우리 변환기로 docx → 임시 독스로 올려 PDF 내보내기 → 같은 docx 의 LibreOffice PDF 와 견준다(둘 다 같은 docx 에서 나왔으니
차이는 독스가 docx 를 어떻게 이해했는가다). 잣대:
  쪽수 차이 · 빈 쪽(잉크 0.2% 미만) · 왼쪽 여백 밖 잉크(표가 밀려 잘리는 유형) · 글자 수 비율(독스 PDF 대 LibreOffice PDF)
가장 나쁜 쪽은 PNG 로 남겨 사람이 본다(/tmp/rt/<id>/). 임시 독스는 지운다.

  실행(VM): set -a; . .env.local; set +a; env PYTHONPATH=src .venv/bin/python scripts/148_docs_roundtrip_check.py --ids 557,564,562
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402

BLANK_INK = 0.002
LEFT_BAND = 0.045          # 쪽 너비의 4.5% 안쪽(여백)에 잉크가 있으면 밀린 것


def _page_stats(pdf: Path, out_dir: Path, tag: str, dpi: int = 40) -> list[dict]:
    """쪽마다 잉크 비율·왼쪽 띠 잉크 — pdftoppm 으로 낮은 해상도 PNG 를 만들어 잰다."""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(out_dir / tag)], check=False, capture_output=True)
    stats = []
    for png in sorted(out_dir.glob(f"{tag}-*.png"), key=lambda p: int(re.search(r"-(\d+)\.png$", p.name).group(1))):
        im = Image.open(png).convert("L")
        w, h = im.size
        px = im.load()
        dark = 0; left = 0
        band = int(w * LEFT_BAND)
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                if px[x, y] < 128:
                    dark += 1
                    if x < band:
                        left += 1
        total = (w // 2) * (h // 2)
        stats.append({"page": int(re.search(r"-(\d+)\.png$", png.name).group(1)), "ink": dark / max(total, 1), "left": left, "png": png})
    return stats


def _pdf_text(pdf: Path) -> str:
    from pypdf import PdfReader

    return "".join((p.extract_text() or "") for p in PdfReader(str(pdf)).pages)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--ids", default="", help="문서함 문서 번호(쉼표)")
    ap.add_argument("--files", default="", help="문서함 밖 파일 경로(쉼표) — 등록하지 않고 검사만")
    ap.add_argument("--out", default="/tmp/rt")
    args = ap.parse_args()
    if not args.ids and not args.files:
        ap.error("--ids 또는 --files")
    import httpx

    from zzaimy.app import office_pdf
    from zzaimy.ingest import gdrive, gdrive_files
    from zzaimy.ingest.gdrive_files import _headers

    db = Database(Path(args.db))
    acct = gdrive.list_accounts()[0]
    acct = acct.get("email") if isinstance(acct, dict) else acct
    h = httpx.Client(timeout=httpx.Timeout(600, connect=30))
    folder = gdrive_files.ensure_folder(acct, ["ZZAIMY", "_검증임시"], http=h)
    worst_total = 0
    targets: list[tuple[str, dict]] = []
    for x in [x for x in args.ids.split(",") if x.strip()]:
        doc = db.get_document(int(x))
        if doc:
            targets.append((str(doc["id"]), doc))
        else:
            print(x, "없음")
    for i, f in enumerate([f for f in args.files.split(",") if f.strip()], 1):
        fp = Path(f)
        targets.append((f"f{i}", {"stored_path": str(fp), "filename": fp.parent.name if fp.name.startswith("원본") else fp.name}))
    for did, doc in targets:
        src = Path(doc["stored_path"])
        conv = office_pdf.docx_for(src)                     # LibreOffice 용(줄 간격 '고정', ADR-0036)
        if conv is None or conv[1] != ".docx":
            print(did, "docx 변환 대상 아님", src.suffix); continue
        data = conv[0]
        # 독스에 올리는 것은 독스용 규칙(비율 ÷ 글꼴 자연 행 높이)으로 따로 변환한다 — 실제 열람 경로(gdrive_files.bytes_for_view)와 같게
        docs_data = gdrive_files.bytes_for_view(None, {"stored_path": str(src), "filename": src.name})[0]
        out = Path(args.out) / str(did)
        out.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "doc.docx"; stage.write_bytes(data)
            lo = office_pdf.to_pdf(stage, Path(tmp))
            if lo is None:
                print(did, "LibreOffice 렌더 실패"); continue
            lo_pdf = out / "lo.pdf"; lo_pdf.write_bytes(lo.read_bytes())
        up = gdrive_files.upload_file(acct, docs_data, f"왕복검사 {did}.docx", gdrive_files.CONVERT[".docx"][0], folder,
                                     convert_to="application/vnd.google-apps.document", http=h, reuse=False)
        try:
            r = h.get(f"{gdrive.API}/files/{up['id']}/export", headers=_headers(acct, h), params={"mimeType": "application/pdf"})
            docs_pdf = out / "docs.pdf"; docs_pdf.write_bytes(r.content)
        finally:
            h.delete(f"{gdrive.API}/files/{up['id']}", headers=_headers(acct, h))
        a = _page_stats(lo_pdf, out, "lo"); b = _page_stats(docs_pdf, out, "docs")
        blank_lo = sum(1 for s in a if s["ink"] < BLANK_INK); blank_docs = sum(1 for s in b if s["ink"] < BLANK_INK)
        left_pages = [s["page"] for s in b if s["left"] > 30]
        ta, tb = _pdf_text(lo_pdf), _pdf_text(docs_pdf)
        ratio = len(re.sub(r"\s+", "", tb)) / max(len(re.sub(r"\s+", "", ta)), 1)
        flags = []
        if abs(len(b) - len(a)) > max(2, len(a) * 0.15):
            flags.append(f"쪽수 {len(a)}→{len(b)}")
        if blank_docs > blank_lo + 1:
            flags.append(f"빈 쪽 {blank_lo}→{blank_docs}")
        if left_pages:
            flags.append(f"왼쪽 밖 잉크 {len(left_pages)}쪽 {left_pages[:6]}")
        if not 0.9 <= ratio <= 1.1:
            flags.append(f"글자 비율 {ratio:.2f}")
        worst_total += len(flags)
        print(f"{str(did):>4} {src.suffix[1:]:4} LO {len(a):>3}쪽 · 독스 {len(b):>3}쪽 · 빈쪽 {blank_lo}/{blank_docs} · 글자비 {ratio:.2f} · "
              f"{'OK' if not flags else 'WARN ' + ' / '.join(flags)}  {doc['filename'][:36]}")
        # 사람이 볼 쪽: 왼쪽 밖 잉크가 있는 쪽 + 독스 빈 쪽 앞 3개
        keep = set(left_pages[:3]) | {s["page"] for s in b if s["ink"] < BLANK_INK}
        for s in b:
            if s["page"] not in keep:
                s["png"].unlink(missing_ok=True)
        for s in a:
            if s["page"] not in keep:
                s["png"].unlink(missing_ok=True)
        if keep:
            print(f"      확인용 쪽 그림: {out} (쪽 {sorted(keep)[:8]})")
            # 원본 배치(kordoc 렌더 SVG → PNG)도 같은 쪽 번호로 남긴다 — 사람이 원본·독스를 나란히 본다(rsvg-convert 필요)
            try:
                from zzaimy.ingest.parsers import kordoc as _kd

                exe = _kd._bin()
                if exe and shutil.which("rsvg-convert") and src.suffix.lower() in (".hwpx", ".hwp"):
                    for pg in sorted(keep)[:6]:
                        svg = out / f"orig-{pg}.svg"
                        subprocess.run([str(exe), "render", str(src), "-p", str(pg), "-o", str(svg)], capture_output=True, env=_kd._env(), timeout=120)
                        if svg.exists():
                            subprocess.run(["rsvg-convert", "-w", "420", str(svg), "-o", str(out / f"orig-{pg}.png")], capture_output=True)
                            svg.unlink(missing_ok=True)
            except Exception as e:      # 원본 렌더는 보조 — 실패해도 검사는 유효
                print(f"      원본 렌더 생략({type(e).__name__})")
    return 1 if worst_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
