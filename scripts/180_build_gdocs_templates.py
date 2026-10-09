"""구글 독스 공통 양식 4종을 독스 API 로 직접 만든다(K-20261008-01, src/zzaimy/ingest/gdocs_templates.py).

실행(VM): env PYTHONPATH=src .venv/bin/python scripts/180_build_gdocs_templates.py --email security02@ync.ac.kr [--only plan]
  폴더: 내 드라이브 ZZAIMY/공통 양식(구글 독스) — 같은 이름의 옛 문서는 휴지통으로.
  사양은 data/generated/templates/gdocs/{id}.json·.md 로도 남긴다(27B 프롬프트·검토용).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.ingest import gdocs_templates as gt  # noqa: E402
from zzaimy.ingest import gdrive_files  # noqa: E402

FOLDER = ["ZZAIMY", "공통 양식(구글 독스)"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    gt.export_specs(ROOT / "data/generated/templates/gdocs")
    folder = gdrive_files.ensure_folder(args.email, FOLDER)
    out = []
    for sid, spec in gt.SPECS.items():
        if args.only and sid not in args.only.split(","):
            continue
        got = gt.build(args.email, spec, folder)
        out.append(got)
        print(f"{spec['title']} → {got['url']}", flush=True)
    print("폴더: https://drive.google.com/drive/folders/" + folder)
    # 바로가기 목록은 폴더에 지금 있는 양식 전체로 — --only 로 하나만 다시 만들면 목록이 그 하나로 줄던 것(10/10)
    made = {g["title"]: g for g in out}
    pub = []
    for spec in gt.SPECS.values():
        g = made.get(spec["title"])
        if g is None:
            f = gdrive_files.find_in_folder(args.email, spec["title"], folder)
            g = {"id": f["id"], "url": f"https://docs.google.com/document/d/{f['id']}/edit", "title": spec["title"]} if f else None
        if g:
            pub.append(g)
    (ROOT / "data/generated/templates/gdocs/published.json").write_text(json.dumps(pub, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"바로가기 {len(pub)}종")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
