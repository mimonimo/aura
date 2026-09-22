#!/usr/bin/env python3
"""판본·갈래 소급 — 새 열(family·version_of·kind)이 생긴 뒤 기존 문서에 반입 규칙을 적용한다.

무엇을 하나. 문서마다 제목 열쇠(family)와 서류 갈래(kind)를 적고, 같은 제목의 기준 문서끼리 본문을
견줘 같은 내용(겹침 ≥ doc_family.NEAR_DUP)이면 뒤에 온 것을 '중복'으로 실패 처리하고, 판본이면 첫 판본에
잇는다(version_of). 붙임 묶음은 머리 문서(공고·계획)에 related_criteria_id 로 잇는다.
중복으로 실패 처리한 문서의 기준 조각은 지운다(검색에서 빠진다). 원본 파일은 그대로 둔다.

실행(VM): env PYTHONPATH=src .venv/bin/python scripts/136_family_backfill.py [--db data/platform/platform.db] [--apply]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.doc_family import (NEAR_DUP, family_key, judge, link_attachments,  # noqa: E402
                                   similarity)
from zzaimy.app.doc_routing import KINDS, guess_kind  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/platform/platform.db")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = Database(Path(args.db))
    docs = [d for d in db.list_documents() if d.get("status") != "failed"]
    kinds: Counter = Counter()
    fam_plan: list[tuple[int, str]] = []
    kind_plan: list[tuple[int, str]] = []
    for d in docs:
        fam = family_key(d["filename"]) or ""
        if fam != (d.get("family") or ""):
            fam_plan.append((d["id"], fam))
        kind, _ = guess_kind(d["filename"], (d.get("masked_text") or "")[:4000], d.get("doc_type"))
        kinds[KINDS.get(kind, "(미정)")] += 1
        if (kind or "") != (d.get("kind") or ""):
            kind_plan.append((d["id"], kind))
    print(f"문서 {len(docs)}건 · 제목 열쇠 적을 것 {len(fam_plan)}건 · 갈래 적을 것 {len(kind_plan)}건")
    print("갈래 분포:", dict(kinds))

    groups: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        if d.get("doc_type") == "regulation":
            groups[family_key(d["filename"])].append(d)
    dups: list[tuple[int, int, float]] = []
    versions: list[tuple[int, int, float]] = []
    for fam, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda x: x["id"])
        kept: list[dict] = [members[0]]
        for m in members[1:]:
            j = judge(m.get("masked_text") or "", kept)
            if j["duplicate_of"]:
                dups.append((m["id"], j["duplicate_of"], j["score"]))
            else:
                versions.append((m["id"], kept[0]["id"], j["score"]))
                kept.append(m)
    print(f"같은 제목 묶음 {sum(1 for v in groups.values() if len(v) > 1)}개 · 중복(겹침 ≥ {NEAR_DUP:.2f}) {len(dups)}건 · 판본 {len(versions)}건")
    for a, b, sc in dups:
        print(f"  중복  #{a} ← #{b} 겹침 {sc * 100:.0f}%")
    for a, b, sc in versions:
        print(f"  판본  #{a} → 첫 판본 #{b} 겹침 {sc * 100:.0f}%")
    if not args.apply:
        print("미리보기입니다. 적용하려면 --apply")
        return 0

    backup = Path(args.db).with_name(f"platform-{datetime.now():%Y%m%d-%H%M%S}-before136.db")
    shutil.copy2(args.db, backup)
    print("백업:", backup)
    for did, fam in fam_plan:
        db.set_document_family(did, fam or None)
    for did, kind in kind_plan:
        db.set_document_kind(did, kind or None)
    for did, head, _ in versions:
        db.set_document_family(did, family_key(db.get_document(did)["filename"]), version_of=head)
    for did, twin, sc in dups:
        t = db.get_document(twin)
        db.update_document(did, status="failed",
                           error=f"같은 내용의 문서가 이미 있습니다 — #{twin} {t['filename']} (본문 겹침 {sc * 100:.0f}%)")
        db.add_regulation_chunks(did, t["filename"], [])       # 기준 조각을 비운다 — 검색에서 빠진다
    linked = 0
    for d in docs:
        if d.get("doc_type") == "regulation" and link_attachments(db, d["id"]):
            linked += 1
    print(f"적용: 제목 열쇠 {len(fam_plan)} · 갈래 {len(kind_plan)} · 판본 {len(versions)} · 중복 제외 {len(dups)} · 붙임 잇기 {linked}")
    if dups:
        (Path(args.db).parent / ".reindex-needed").touch()
        print("기준 조각이 줄었습니다 — 재색인 필요(scripts/96_embed_on_thor.sh --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
