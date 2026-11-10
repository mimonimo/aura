#!/usr/bin/env python3
"""문서 이름을 한글 음절(NFC)로 맞추기 — 맥에서 올린 파일 이름은 자모로 풀린 꼴(NFD)로 남아, 글자 수로 자른 이름이
「육성사ᄋ」 처럼 갈려 에이전트가 문서를 고르지 못했다(11-10 대화 186·187). 새로 들어오는 이름은 반입에서 맞추고,
이 스크립트는 이미 들어온 이름(documents.filename, regulation_chunks.reg_title)만 고친다.

기본은 살펴보기(바꿀 목록만 보인다). --apply 를 주면 바꾼다 — 운영 DB 는 백업한 뒤에.
사용(VM, 운영 폴더에서 .env.local 과 함께):
  cd ~/zzaimy-capstone && set -a && . ./.env.local; set +a; PYTHONPATH=src .venv/bin/python scripts/224_nfc_titles.py [--apply]
"""
import os
import sys
import unicodedata
from pathlib import Path

from zzaimy.app.db import Database


def nfd_rows(db) -> list[tuple[int, str, str]]:
    out = []
    with db._conn() as conn:
        for r in conn.execute("SELECT id, filename FROM documents").fetchall():
            r = dict(r)
            name = r.get("filename") or ""
            fixed = unicodedata.normalize("NFC", name)
            if fixed != name:
                out.append((int(r["id"]), name, fixed))
    return out


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    db = Database(Path(os.environ.get("ZZAIMY_DB_PATH") or "data/platform/platform.db"))
    rows = nfd_rows(db)
    for did, _old, new in rows:
        print(f"문서 {did}: {new}")
    print(f"자모로 풀린 이름 {len(rows)}건" + (" — 바꿉니다" if apply and rows else "" if apply else " (살펴보기, 바꾸려면 --apply)"))
    if not apply or not rows:
        return 0
    with db._conn() as conn:
        for did, _old, new in rows:
            conn.execute("UPDATE documents SET filename = ? WHERE id = ?", (new, did))
            conn.execute("UPDATE regulation_chunks SET reg_title = ? WHERE doc_id = ?", (new, did))
    print("바꿨습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
