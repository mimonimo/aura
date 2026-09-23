#!/usr/bin/env python3
"""저장 구조 이관 — inbox 에 임의 이름으로 쌓인 원본을 문서 폴더(ADR-0030)로 옮기고 DB(stored_path·doc_assets·files)를 맞춘다.

무엇을 하나. 문서마다 documents/반입/<연도>/<유형>/<접수번호> <제목>/원본.<확장자> 로 옮기고, 옆의 추출 그림 폴더(<stem>_imgs)는
원본_imgs 로 함께 옮긴다. 대화 첨부(inbox/chat_*)는 첨부/<연도>/미분류/ 로, 주간 보고(weekly/)는 보고/주간/ 으로 옮긴다.
모든 파일을 files 표에 적는다. DB 는 먼저 백업한다. 기본은 미리보기.

실행(VM): env PYTHONPATH=src .venv/bin/python scripts/138_layout_migrate.py [--db data/platform/platform.db] [--apply]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zzaimy.app import storage  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/platform/platform.db")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = Database(Path(args.db))
    base = Path(args.db).parent
    inbox = base / "inbox"
    plan: list[tuple[int, Path, Path]] = []
    missing: list[int] = []
    for d in db.list_documents():
        cur = Path(d.get("stored_path") or "")
        if not cur.is_absolute():
            cur = Path.cwd() / cur
        if storage._under(cur, storage.root(base)):
            continue                                    # 이미 새 구조
        if not cur.exists():
            missing.append(d["id"]); continue
        dest = storage.intake_dir(base, d) / f"{storage.ORIGINAL}{cur.suffix.lower()}"
        plan.append((d["id"], cur, dest))
    chats = sorted(inbox.glob("chat_*")) if inbox.exists() else []
    weekly_old = base / "weekly"
    weekly = sorted(weekly_old.glob("*.md")) if weekly_old.exists() else []
    print(f"문서 {len(plan)}건 옮김 예정 · 원본 없음 {len(missing)}건 · 대화 첨부 {len(chats)}건 · 주간 보고 {len(weekly)}건")
    for did, cur, dest in plan[:5]:
        print(f"  #{did}: {cur.name} → {dest.relative_to(base)}")
    if not args.apply:
        print("미리보기입니다. 적용하려면 --apply")
        return 0
    backup = base / "backup" / f"platform-{datetime.now():%Y%m%d-%H%M%S}-before138.db"
    backup.parent.mkdir(exist_ok=True)
    shutil.copy2(args.db, backup)
    print("백업:", backup)
    done = Counter()
    for did, cur, dest in plan:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_name(f"{storage.ORIGINAL}-{did}{dest.suffix}")
        shutil.move(str(cur), str(dest))
        imgs = cur.parent / f"{cur.stem}_imgs"
        if imgs.is_dir():
            new_imgs = dest.parent / f"{storage.ORIGINAL}_imgs"
            if new_imgs.exists():
                for f in imgs.iterdir():
                    shutil.move(str(f), str(new_imgs / f.name))
                shutil.rmtree(imgs, ignore_errors=True)
            else:
                shutil.move(str(imgs), str(new_imgs))
            db.repath_assets(did, str(imgs), str(new_imgs))
            done["그림 폴더"] += 1
        db.set_stored_path(did, str(dest))
        doc = db.get_document(did) or {}
        db.add_file("intake", str(dest), name=doc.get("filename") or dest.name, doc_id=did,
                    size=dest.stat().st_size)
        done["문서"] += 1
    year = f"{datetime.now():%Y}"
    for c in chats:
        d = storage.root(base) / storage.KIND_DIRS["attachment"] / year / "미분류"
        d.mkdir(parents=True, exist_ok=True)
        dest = d / c.name
        shutil.move(str(c), str(dest))
        db.add_file("attachment", str(dest), name=c.name, size=dest.stat().st_size)
        done["첨부"] += 1
    for w in weekly:
        dest = storage.report_dir(base, "주간") / w.name
        shutil.move(str(w), str(dest))
        db.add_file("report", str(dest), name=w.name, size=dest.stat().st_size)
        done["주간 보고"] += 1
    if weekly_old.exists() and not any(weekly_old.iterdir()):
        weekly_old.rmdir()
    left = [p.name for p in inbox.iterdir()] if inbox.exists() else []
    print("적용:", dict(done), "· inbox 에 남은 항목", len(left), (left[:5] if left else ""))
    print("장부(files):", db.file_counts())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
