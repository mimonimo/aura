#!/usr/bin/env python3
"""사업별 공통 양식(scripts/162 → docx)을 구글 드라이브 「ZZAIMY/사업별 공통 양식」에 구글 문서로 올린다.

같은 이름의 옛 판이 있으면 휴지통으로 보내고 새로 올린다(개선은 쓰는 문서까지). 계정은 플랫폼에 연결된 학교 구글 계정.

사용(VM): env PYTHONPATH=src .venv/bin/python scripts/169_upload_templates.py --email security02@ync.ac.kr
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.ingest import gdrive, gdrive_files  # noqa: E402

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GDOC = "application/vnd.google-apps.document"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--dir", default=str(ROOT / "data" / "generated" / "templates"))
    ap.add_argument("--folder", default="ZZAIMY/사업별 공통 양식")
    args = ap.parse_args()
    http = gdrive._http()
    folder = gdrive_files.ensure_folder(args.email, args.folder.split("/"), http=http)
    for md in sorted(Path(args.dir).glob("*.md")):
        docx = md.with_suffix(".docx")
        if not docx.is_file():
            continue
        first = md.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip() or md.stem
        old = gdrive_files.find_in_folder(args.email, first, folder, http)
        if old:
            http.patch(f"{gdrive.API}/files/{old['id']}", headers=gdrive_files._headers(args.email, http),
                       params={"supportsAllDrives": "true"}, json={"trashed": True})
        got = gdrive_files.upload_file(args.email, docx.read_bytes(), first + ".docx", DOCX, folder_id=folder,
                                       convert_to=GDOC, http=http, reuse=False)
        print(f"{first} → {got['url']}" + ("(옛 판 휴지통)" if old else ""))
    print("폴더: https://drive.google.com/drive/folders/" + folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
