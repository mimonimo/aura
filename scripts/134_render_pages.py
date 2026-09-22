#!/usr/bin/env python3
"""전사(판독) 대상 문서의 쪽을 그림으로 뽑는다 — 사람이나 외부 판독 모델이 원본을 보고 옮겨 적기 위한 준비.

공개 문서만 대상으로 한다(owner=corpus 또는 --force). 접수 서류처럼 개인정보가 든 문서는 밖으로 내지 않는다(ADR-0024).
PDF 는 pypdfium2 로 쪽마다 PNG, 이미지 문서는 그대로 복사. 산출: data/vision/<doc_id>/p<N>.png + manifest.json

실행(VM): env PYTHONPATH=src .venv/bin/python scripts/134_render_pages.py --docs 429,451,320 [--dpi 130] [--force]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402

PUBLIC_OWNERS = {"corpus"}


def render(doc: dict, out: Path, dpi: int) -> list[str]:
    src = Path(doc["stored_path"])
    if not src.is_absolute():
        src = ROOT / src
    out.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png"):
        dst = out / f"p1{suffix if suffix != '.jpeg' else '.jpg'}"
        shutil.copy2(src, dst)
        return [dst.name]
    if suffix == ".pdf":
        import pypdfium2 as pdfium

        names = []
        pdf = pdfium.PdfDocument(str(src))
        for i in range(len(pdf)):
            page = pdf[i]
            img = page.render(scale=dpi / 72).to_pil()
            name = f"p{i + 1}.png"
            img.save(out / name)
            names.append(name)
        return names
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--docs", required=True)
    ap.add_argument("--dpi", type=int, default=130)
    ap.add_argument("--force", action="store_true", help="공개 수집분이 아니어도 뽑는다(교내 공개 게시물 등)")
    args = ap.parse_args()
    db = Database(Path(args.db))
    base = ROOT / "data" / "vision"
    manifest = []
    for did in [int(x) for x in args.docs.split(",") if x.strip()]:
        doc = db.get_document(did)
        if not doc:
            print(f"[{did}] 없음"); continue
        if doc.get("owner") not in PUBLIC_OWNERS and not args.force:
            print(f"[{did}] 공개 수집분이 아님 — 건너뜀 (--force 로 강제)"); continue
        names = render(doc, base / str(did), args.dpi)
        manifest.append({"doc_id": did, "filename": doc["filename"], "pages": names, "owner": doc.get("owner"),
                         "doc_type": doc.get("doc_type"), "parse_note": doc.get("parse_note")})
        print(f"[{did}] {doc['filename'][:40]} → {len(names)}쪽")
    (base / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"manifest: {base / 'manifest.json'} · 문서 {len(manifest)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
