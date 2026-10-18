#!/usr/bin/env python3
"""기준 문서(규정) 정리 — 잘린 이름 고치기, 다른 기관 규정 되돌리기, 같은 규정의 겹친 판 되돌리기.

왜: 반입 자료에서 규정을 기준 문서로 올릴 때(app/regulation_register) 판 묶음을 원래 파일 이름(family)으로 지어서,
규정집 번호·제정일이 다른 같은 규정이 판 수만큼 기준 문서가 됐다. 참고로 보관한 다른 학교·재단의 규정도 제목이
규정 갈래로 끝나 함께 올라갔다. 파일 이름이 첫 글자를 잃은 문서('남이공대학교 …')는 이름 겹침을 피하려는 규칙이
본문 제목 대신 그 잘린 파일 이름을 골랐다. 규칙은 고쳤고(rule_key·issuer·doc_naming.clipped_title), 이 스크립트는
이미 들어온 문서에 같은 규칙을 적용한다.

하는 일 (순서대로):
  1. 이름 — 파일 이름으로 이름을 정한 문서 가운데 파일 이름이 본문 제목의 잘린 꼴인 것을 반입과 같은 함수
     (db.rename_from_text)로 다시 매긴다(규정 조각의 인용 이름도 함께 바뀐다).
  2. 다른 기관 — 제목 첫머리가 우리 학교가 아닌 대학·재단인 기준 문서를 사업 문서로 되돌린다.
  3. 겹친 판 — 같은 규정(rule_key)의 기준 문서가 여럿이면 가장 새 판(잘린 판독은 건너뜀) 하나만 두고 나머지를 되돌린다.
되돌리기는 등록 해제와 같은 길(regulation_register.demote: 규정 조각 삭제, doc_type grant, 기록 한 줄, .reindex-needed)이다.
적용 전에 바뀌는 문서의 이름·정체·갈래와 지울 규정 조각을 data/platform/backups/regcleanup-<시각>.jsonl.gz 에 남긴다.
규정 임베딩 재색인은 하지 않는다(.reindex-needed 표시만) — 서빙 상자에서 따로 돌린다.

사용 (VM, .env.local 을 읽은 셸):
  scripts/zz_run.sh 8G env PYTHONPATH=src .venv/bin/python scripts/189_regulation_cleanup.py           # 미리보기
  scripts/zz_run.sh 8G env PYTHONPATH=src .venv/bin/python scripts/189_regulation_cleanup.py --apply
  scripts/zz_run.sh 8G env PYTHONPATH=src .venv/bin/python scripts/189_regulation_cleanup.py --restore data/platform/backups/regcleanup-<시각>.jsonl.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import regulation_register as rr  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.doc_naming import USER_SOURCE, body_title, clean_filename, clipped_title  # noqa: E402

BATCH = 500
BY = "scripts/189"


def clipped_names(db) -> list[dict]:
    """파일 이름으로 이름을 정했는데 그 파일 이름이 본문 제목의 잘린 꼴인 문서."""
    out, last = [], 0
    while True:
        with db._conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, filename, identity, SUBSTR(COALESCE(masked_text, ''), 1, 3000) AS head FROM documents"
                " WHERE id > ? AND identity LIKE '%\"name_source\": \"file\"%' ORDER BY id LIMIT ?", (last, BATCH)).fetchall()]
        if not rows:
            return out
        last = rows[-1]["id"]
        for r in rows:
            try:
                ident = json.loads(r["identity"] or "{}")
            except ValueError:
                continue
            if ident.get("name_source") == USER_SOURCE or not r["head"]:
                continue
            title, _ = body_title(r["head"])
            fname = clean_filename(ident.get("original_filename") or r["filename"] or "", r["head"])
            if title and clipped_title(fname, title):
                out.append({"id": r["id"], "old": r["filename"], "title": title})


def plan(db, renamed: dict[int, str]) -> tuple[list[dict], list[dict], list[dict]]:
    """(다른 기관, 겹친 판으로 되돌릴 것, 남길 판) — 이름 고치기 뒤의 이름으로 판단한다."""
    groups = rr.current_groups(db)
    docs = [d for g in groups.values() for d in g]
    for d in docs:
        d["title"] = renamed.get(d["id"], d["title"])
    other = [d for d in docs if rr.issuer(d["title"] or "")]
    gone = {d["id"] for d in other}
    regroup: dict[str, list[dict]] = {}
    for d in docs:
        if d["id"] not in gone:
            regroup.setdefault(rr.rule_key(d["title"] or "") or f"#{d['id']}", []).append(d)
    dup, keep = [], []
    for k, g in sorted(regroup.items()):
        if len(g) < 2:
            continue
        cur = rr.pick_current(g)
        keep.append({**cur, "group": k, "n": len(g)})
        dup += [{**d, "kept": cur["id"]} for d in g if d["id"] != cur["id"]]
    return other, dup, keep


def backup(db, ids_meta: list[int], ids_chunks: list[int]) -> Path:
    out = ROOT / "data/platform/backups" / f"regcleanup-{time.strftime('%Y%m%d-%H%M%S')}.jsonl.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f, db._conn() as conn:
        for i in sorted(set(ids_meta)):
            r = conn.execute("SELECT id, filename, identity, doc_type FROM documents WHERE id = ?", (i,)).fetchone()
            if r:
                f.write(json.dumps({"doc": dict(r)}, ensure_ascii=False) + "\n")
        for i in sorted(set(ids_chunks)):
            for r in conn.execute("SELECT * FROM regulation_chunks WHERE doc_id = ? ORDER BY id", (i,)).fetchall():
                f.write(json.dumps({"chunk": dict(r)}, ensure_ascii=False) + "\n")
    os.chmod(out, 0o600)
    return out


def restore(db, path: Path) -> None:
    docs = chunks = 0
    with gzip.open(path, "rt", encoding="utf-8") as f, db._conn() as conn:
        for line in f:
            rec = json.loads(line)
            if "doc" in rec:
                d = rec["doc"]
                conn.execute("UPDATE documents SET filename = ?, identity = ?, doc_type = ? WHERE id = ?",
                             (d["filename"], d["identity"], d["doc_type"], d["id"]))
                conn.execute("UPDATE regulation_chunks SET reg_title = ? WHERE doc_id = ?", (d["filename"], d["id"]))
                docs += 1
            else:
                c = rec["chunk"]
                if conn.execute("SELECT 1 FROM regulation_chunks WHERE id = ?", (c["id"],)).fetchone():
                    continue
                cols = list(c)
                conn.execute(f"INSERT INTO regulation_chunks ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                             [c[k] for k in cols])
                chunks += 1
    (ROOT / "data/platform/.reindex-needed").touch()
    print(f"되살림: 문서 {docs}건 · 규정 조각 {chunks}개 — 규정 재색인 필요(.reindex-needed)")


def counts(db) -> str:
    with db._conn() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM documents WHERE doc_type = 'regulation'").fetchone()["n"]
        c = conn.execute("SELECT COUNT(*) AS n FROM regulation_chunks").fetchone()["n"]
    return f"기준 문서 {n}건 · 규정 조각 {c}개"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data/platform/platform.db"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore")
    args = ap.parse_args()
    db = Database(Path(args.db))
    if args.restore:
        restore(db, Path(args.restore))
        return 0
    print("지금:", counts(db))
    clip = clipped_names(db)
    print(f"\n[1] 잘린 이름 {len(clip)}건")
    for c in clip:
        print(f"  {c['id']:>6} {c['old']}  →  {c['title']}")
    other, dup, keep = plan(db, {c["id"]: c["title"] for c in clip})
    print(f"\n[2] 다른 기관 규정 {len(other)}건 (조각 {sum(d['chunks'] for d in other)})")
    for d in sorted(other, key=lambda d: d["title"]):
        print(f"  {d['id']:>6} [{rr.issuer(d['title'])}] {d['title']} ({d['chunks']}조각)")
    print(f"\n[3] 겹친 판 {len(dup)}건 (조각 {sum(d['chunks'] for d in dup)}) — 규정 {len(keep)}개에 한 판씩 남김")
    by_keep: dict[int, list[dict]] = {}
    for d in dup:
        by_keep.setdefault(d["kept"], []).append(d)
    for k in keep:
        print(f"  남김 {k['id']:>6} {k['title']} ({k['chunks']}조각, 판 {rr.revision(k['title'], k.get('family') or '', k['text'] or '')})")
        for d in by_keep.get(k["id"], []):
            print(f"   └ 되돌림 {d['id']:>6} {d['title']} ({d['chunks']}조각, 판 {rr.revision(d['title'], d.get('family') or '', d['text'] or '')})")
    if not args.apply:
        print("\n미리보기입니다. 적용하려면 --apply")
        return 0
    demote_ids = [d["id"] for d in other] + [d["id"] for d in dup]
    bk = backup(db, [c["id"] for c in clip] + demote_ids, demote_ids)
    print(f"\n백업: {bk}")
    for c in clip:
        db.rename_from_text(c["id"], overwrite=True)
    data_dir = ROOT / "data/platform"
    a = rr.demote(db, [d["id"] for d in other], data_dir, by=BY, reason="다른 기관의 규정 — 참고 자료(제목 첫머리 기관)")
    b = rr.demote(db, [d["id"] for d in dup], data_dir, by=BY, reason="같은 규정의 겹친 판 — 가장 새 판 하나만 기준 문서")
    left = rr.superseded(db)
    print(f"적용: 이름 {len(clip)}건 · 다른 기관 {len(a)}건 · 겹친 판 {len(b)}건 되돌림 · 남은 겹침 {len(left)}건")
    print("뒤:", counts(db), "— 규정 임베딩 재색인 필요(.reindex-needed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
