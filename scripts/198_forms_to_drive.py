#!/usr/bin/env python3
"""양식을 드라이브에 맞춘다 — 원본 맞추기(164)가 새로 만든 공통 양식 docx 를 구글 독스로 바꿔 「ZZAIMY/공통 양식」에 올린다.

왜: 양식은 그래프가 바뀔 때마다 다시 만들어지는데 드라이브 사본은 한 번 손으로 올린 판이라 늘 낡아 있었다.
- 사업별 양식(162, data/generated/templates/*.docx) → 「ZZAIMY/공통 양식/사업별(참고)」
- 문서 갈래별 공통 양식(172, templates/common/*.docx) → 「ZZAIMY/공통 양식/문서 갈래별」
이름은 md 첫 제목(「○○ 계획서 공통 양식」). 내용이 그대로인 파일은 다시 올리지 않고(지문 표 drive_sync.json),
바뀐 파일은 옛 판을 휴지통으로(되돌릴 수 있음) 보낸 뒤 올린다. 지금 양식 목록에 없는 옛 양식도 휴지통으로.
계정은 --email 또는 ZZAIMY_FORMS_DRIVE_EMAIL(운영 .env.local) — 없으면 아무것도 하지 않는다. 이 앱이 만든 파일만 건드린다(drive.file).
사용(VM): env PYTHONPATH=src .venv/bin/python scripts/198_forms_to_drive.py [--email 계정] [--dry]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

TPL = ROOT / "data" / "generated" / "templates"
GROUPS = ((TPL, ["ZZAIMY", "공통 양식", "사업별(참고)"]), (TPL / "common", ["ZZAIMY", "공통 양식", "문서 갈래별"]))
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GDOC = "application/vnd.google-apps.document"


def title_of(docx: Path) -> str:
    md = docx.with_suffix(".md")
    try:
        for line in md.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()[:120]
    except OSError:
        pass
    return docx.stem


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default=os.environ.get("ZZAIMY_FORMS_DRIVE_EMAIL", ""))
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    if not args.email:
        print("FORMS_DRIVE 건너뜀 — 계정(ZZAIMY_FORMS_DRIVE_EMAIL) 없음", flush=True)
        return 0
    from zzaimy.ingest import gdrive, gdrive_files as gf

    marker = TPL / "drive_sync.json"
    try:
        seen = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    http = gdrive._http()
    up = same = gone = 0
    for src, parts in GROUPS:
        files = sorted(src.glob("*.docx"))
        if not files:
            continue
        folder = gf.ensure_folder(args.email, parts, http=http)
        names = {}
        for f in files:
            names[title_of(f)] = f
        for name, f in names.items():
            sha = hashlib.sha256(f.read_bytes()).hexdigest()
            key = f"{'/'.join(parts)}/{name}"
            if seen.get(key) == sha and gf.find_in_folder(args.email, name, folder, http):
                same += 1
                continue
            if args.dry:
                print(f"  올릴 것: {key}", flush=True)
                continue
            gf.trash_older(args.email, folder, name, http=http)
            gf.upload_file(args.email, f.read_bytes(), name + ".docx", DOCX, folder, convert_to=GDOC, reuse=False)
            seen[key] = sha
            up += 1
        # 지금 목록에 없는 옛 양식 — 휴지통으로(사업이 합쳐지거나 이름이 바뀐 것)
        h = gf._headers(args.email, http)
        r = http.get(f"{gdrive.API}/files", headers=h, params={"q": f"'{folder}' in parents and trashed = false",
                                                              "fields": "files(id,name)", "pageSize": 500})
        for x in (r.json().get("files") or []) if r.status_code == 200 else []:
            if x["name"] not in names:
                if not args.dry:
                    http.patch(f"{gdrive.API}/files/{x['id']}", headers=h, json={"trashed": True})
                    seen.pop(f"{'/'.join(parts)}/{x['name']}", None)
                gone += 1
    if not args.dry:
        marker.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"FORMS_DRIVE 올림 {up} · 그대로 {same} · 옛 양식 휴지통 {gone}" + (" (미리 보기)" if args.dry else ""), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
