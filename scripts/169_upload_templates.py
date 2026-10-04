#!/usr/bin/env python3
"""사업별 공통 양식(scripts/162 → docx)을 구글 드라이브 「ZZAIMY/사업별 공통 양식」에 구글 문서로 올린다.

같은 이름의 옛 판이 있으면 휴지통으로 보내고 새로 올린다(개선은 쓰는 문서까지). 제목이 바뀌어 이번 판에 없는
「… 공통 양식」 문서도 휴지통으로 보낸다 — 폴더에 옛 이름 판이 남지 않게. 계정은 플랫폼에 연결된 학교 구글 계정.

사용(VM): env PYTHONPATH=src .venv/bin/python scripts/169_upload_templates.py --email security02@ync.ac.kr [--common]
  --common: 문서 갈래별 공통 양식(172, 사업 공통) → 「ZZAIMY/공통 양식」. 없으면 사업별 양식(162) → 「ZZAIMY/공통 양식/사업별(참고)」
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


def _list_folder(email: str, folder: str, http) -> list[dict]:
    out, token = [], None
    while True:
        params = {"q": f"'{folder}' in parents and trashed = false", "fields": "nextPageToken,files(id,name)",
                  "pageSize": 200, "supportsAllDrives": "true"}
        if token:
            params["pageToken"] = token
        r = http.get(f"{gdrive.API}/files", headers=gdrive_files._headers(email, http), params=params)
        if r.status_code != 200:
            return out
        body = r.json()
        out += body.get("files", [])
        token = body.get("nextPageToken")
        if not token:
            return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--common", action="store_true", help="문서 갈래별 공통 양식(172)")
    ap.add_argument("--dir", default="")
    ap.add_argument("--folder", default="")
    args = ap.parse_args()
    tpl = ROOT / "data" / "generated" / "templates"
    args.dir = args.dir or str(tpl / "common" if args.common else tpl)
    args.folder = args.folder or ("ZZAIMY/공통 양식" if args.common else "ZZAIMY/공통 양식/사업별(참고)")
    http = gdrive._http()
    folder = gdrive_files.ensure_folder(args.email, args.folder.split("/"), http=http)
    current: set[str] = set()
    for md in sorted(Path(args.dir).glob("*.md")):
        docx = md.with_suffix(".docx")
        if not docx.is_file():
            continue
        first = md.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip() or md.stem
        current.add(first)
        old = gdrive_files.find_in_folder(args.email, first, folder, http)
        if old:
            http.patch(f"{gdrive.API}/files/{old['id']}", headers=gdrive_files._headers(args.email, http),
                       params={"supportsAllDrives": "true"}, json={"trashed": True})
        got = gdrive_files.upload_file(args.email, docx.read_bytes(), first + ".docx", DOCX, folder_id=folder,
                                       convert_to=GDOC, http=http, reuse=False)
        print(f"{first} → {got['url']}" + ("(옛 판 휴지통)" if old else ""))
    if current:
        stale = [f for f in _list_folder(args.email, folder, http)
                 if f["name"].endswith("공통 양식") and f["name"] not in current]
        for f in stale:
            http.patch(f"{gdrive.API}/files/{f['id']}", headers=gdrive_files._headers(args.email, http),
                       params={"supportsAllDrives": "true"}, json={"trashed": True})
            print(f"{f['name']} → 이번 판에 없어 휴지통")
    print("폴더: https://drive.google.com/drive/folders/" + folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
