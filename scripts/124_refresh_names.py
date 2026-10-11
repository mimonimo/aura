#!/usr/bin/env python3
"""문서 이름을 지금의 규칙(app/doc_naming)으로 다시 매긴다.

이름 규칙을 고치면 이미 들어온 문서는 옛 이름을 그대로 쓴다. 올라온 파일 이름(identity.original_filename,
없으면 지금 이름)과 본문으로 반입과 같은 함수(db.rename_from_text)를 다시 부른다 — 화면·인용·검색이 같은 이름을 본다.

두 번 훑는다. 첫 번째는 문서마다 이름을 고르고, 문서함 전체에서 겹침을 찾는다 — 본문 제목으로 고른 이름이 다른 문서가
고른 이름과 같은데 두 문서의 걷어 낸 파일 이름이 다르면(다른 문서가 한 이름으로 겹침) 그 문서들은 파일 이름으로 돌린다.
미리보기는 여기서 멈추고 통계·층별 표본·전체 대응표(TSV)를 남긴다. --apply 는 백업을 남긴 뒤 같은 판단으로 적용한다.

지키는 것: 접수번호·원본 파일·저장 경로는 건드리지 않는다. 담당자가 직접 붙인 이름(identity.name_source = user)은 건너뛴다.
본문은 묶음으로 나눠 읽는다(문서 수만 건의 본문을 한 번에 올리지 않는다). 미리보기의 이름에는 같은 이름 구분 말
(' · 부제' / ' · 접수번호')이 아직 붙지 않는다 — 적용 때 db._unique_name 이 붙인다.

사용 (VM, 저장소 위치에서 .env.local 을 읽은 셸 — set -a; . ./.env.local; set +a):
  scripts/zz_run.sh 12G env PYTHONPATH=src .venv/bin/python scripts/124_refresh_names.py --seed 7        # 미리보기 + 대응표
  scripts/zz_run.sh 12G env PYTHONPATH=src .venv/bin/python scripts/124_refresh_names.py --apply        # 백업 뒤 적용
  scripts/zz_run.sh 12G env PYTHONPATH=src .venv/bin/python scripts/124_refresh_names.py --like '국고'  # 이름에 그 글자가 든 문서만
  scripts/zz_run.sh 12G env PYTHONPATH=src .venv/bin/python scripts/124_refresh_names.py --restore data/platform/backups/names-<시각>.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.doc_naming import USER_SOURCE, choose_name, clean_filename, naming_meta  # noqa: E402

BATCH = 300


def _ident(raw) -> dict:
    try:
        return json.loads(raw or "{}") or {}
    except (ValueError, TypeError):
        return {}


def _original(row: dict) -> str:
    ident = _ident(row.get("identity"))
    sp = str(row.get("stored_path") or "")
    return (ident.get("original_filename") or (sp[6:].rsplit("/", 1)[-1] if sp.startswith("dgx://") else "")
            or row.get("filename") or "").strip()


def _name(got: dict) -> str:
    name = got["name"] or ""
    if got.get("title") and got.get("date"):
        name = f"{got['title']} · {got['date']}"
    return name


def _rows(db, ids: list[int]):
    """본문을 묶음으로 읽어 한 건씩 내준다."""
    for i in range(0, len(ids), BATCH):
        part = ids[i:i + BATCH]
        q = ("SELECT id, filename, identity, masked_text, stored_path, kind, receipt_no FROM documents WHERE id IN (%s) ORDER BY id"
             % ",".join("?" * len(part)))
        with db._conn() as conn:
            rows = [dict(r) for r in conn.execute(q, tuple(part)).fetchall()]
        yield from rows
        print(f"  … {min(i + BATCH, len(ids))}/{len(ids)}", file=sys.stderr, flush=True)


def plan(db, ids: list[int]) -> tuple[list[dict], int]:
    """첫 번째 훑기 — 문서마다 고른 이름과 겹침 판정. 돌려주는 것: (계획 목록, 담당자 이름이라 건너뛴 수)."""
    out: list[dict] = []
    skipped = 0
    for r in _rows(db, ids):
        if _ident(r["identity"]).get("name_source") == USER_SOURCE:
            skipped += 1
            continue
        orig = _original(r)
        text = db.naming_text(r["id"], r["masked_text"] or "")
        got = choose_name(orig, text, naming_meta(db, r))
        out.append({"id": r["id"], "receipt": r["receipt_no"] or "", "old": r["filename"] or "", "orig": orig,
                    "clean": clean_filename(orig), "new": _name(got), "source": got["source"], "collision": False})
    # 겹침 — 같은 이름을 고른 문서들의 걷어 낸 파일 이름이 서로 다르면 본문 제목으로 고른 것을 파일 이름으로 돌린다
    by_name: dict[str, list[dict]] = defaultdict(list)
    for p in out:
        by_name[p["new"]].append(p)
    flagged = [p for group in by_name.values() if len({g["clean"] for g in group}) > 1
               for p in group if p["source"] == "body"]
    if flagged:
        want = {p["id"]: p for p in flagged}
        for r in _rows(db, sorted(want)):
            p = want[r["id"]]
            got = choose_name(p["orig"], db.naming_text(r["id"], r["masked_text"] or ""), naming_meta(db, r), prefer_file=True)
            p.update(new=_name(got), source=got["source"], collision=True)
    return out, skipped


def restore(db, path: Path) -> int:
    n = 0
    with db._conn() as conn:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
            except ValueError:
                continue
            conn.execute("UPDATE documents SET filename = ?, identity = ? WHERE id = ?", (o["filename"], o["identity"], o["id"]))
            conn.execute("UPDATE regulation_chunks SET reg_title = ? WHERE doc_id = ?", (o["filename"], o["id"]))
            n += 1
    print(f"되돌림 {n}건 ← {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--like", default="", help="이 글자가 이름에 든 문서만")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ask", action="store_true", help="적용 때 본문 규칙이 못 찾고 파일 이름도 빈약하면 모델에게 제목 줄을 짚게 한다(느림)")
    ap.add_argument("--restore", default="", help="--apply 가 남긴 백업 파일에서 이름·정체를 되돌린다")
    ap.add_argument("--sample", type=int, default=200, help="출처별로 고르게 뽑아 보일 바뀐 이름 수")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tsv", default="", help="전체 대응표 경로(기본 data/platform/backups/names-preview-<시각>.tsv)")
    args = ap.parse_args()

    db = Database(Path(args.db))
    if args.restore:
        return restore(db, Path(args.restore))

    sql, params = "SELECT id FROM documents", ()
    if args.like:
        sql += " WHERE filename LIKE ?"
        params = (f"%{args.like}%",)
    with db._conn() as conn:
        ids = [int(r[0]) for r in conn.execute(sql + " ORDER BY id", params).fetchall()]

    stamp = time.strftime("%Y%m%d-%H%M%S")
    bdir = ROOT / "data" / "platform" / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    plans, skipped = plan(db, ids)

    # 전체 대응표
    tsv = Path(args.tsv) if args.tsv else bdir / f"names-preview-{stamp}.tsv"
    with tsv.open("w", encoding="utf-8") as fh:
        fh.write("id\treceipt_no\tsource\tcollision\tchanged\told\tnew\toriginal\n")
        for p in plans:
            cells = [str(p["id"]), p["receipt"], p["source"], "1" if p["collision"] else "0", "1" if p["new"] != p["old"] else "0",
                     p["old"], p["new"], p["orig"]]
            fh.write("\t".join(c.replace("\t", " ").replace("\n", " ") for c in cells) + "\n")

    changed = [p for p in plans if p["new"] and p["new"] != p["old"]]
    src_all = Counter(p["source"] for p in plans)
    src_changed = Counter(p["source"] for p in changed)
    print(f"대상 {len(ids)}건 · 바뀜 {len(changed)}건 · 그대로 {len(plans) - len(changed)}건 · 담당자 이름이라 건너뜀 {skipped}건")
    print(f"겹침을 피해 파일 이름으로 돌린 것 {sum(p['collision'] for p in plans)}건")
    print("출처(전체): " + ", ".join(f"{k} {v}" for k, v in src_all.most_common()))
    print("출처(바뀐 것): " + ", ".join(f"{k} {v}" for k, v in src_changed.most_common()))
    left = Counter(p["new"] for p in plans)
    print(f"이름이 겹치는 문서(적용 때 구분 말이 붙음) {sum(v for v in left.values() if v > 1)}건 / {sum(1 for v in left.values() if v > 1)}개 이름")

    # 출처별 층화 표본
    rng = random.Random(args.seed)
    groups: dict[str, list[dict]] = defaultdict(list)
    for p in changed:
        groups[p["source"] + (":겹침" if p["collision"] else "")].append(p)
    picks: list[dict] = []
    if groups:
        per = max(1, args.sample // len(groups))
        for key in sorted(groups):
            g = groups[key]
            picks += [(key, p) for p in rng.sample(g, min(per, len(g)))]
    print(f"\n표본 {len(picks)}건 (seed={args.seed}, 출처별 고르게) — 출처 | 접수번호 | 원래 파일 이름 → 새 이름")
    for key, p in picks[: args.sample]:
        print(f"  {key:9} {p['receipt']:16} {p['orig'][:70]}  →  {p['new'][:90]}")
    print(f"\n전체 대응표: {tsv.relative_to(ROOT) if tsv.is_relative_to(ROOT) else tsv}")

    if not args.apply:
        print("미리보기입니다. 적용하려면 --apply 를 붙이십시오(먼저 백업을 남깁니다).")
        return 0

    bpath = bdir / f"names-{stamp}.jsonl"
    with db._conn() as conn, bpath.open("w", encoding="utf-8") as fh:
        for i in range(0, len(ids), 2000):
            part = ids[i:i + 2000]
            q = "SELECT id, receipt_no, filename, identity FROM documents WHERE id IN (%s)" % ",".join("?" * len(part))
            for r in conn.execute(q, tuple(part)).fetchall():
                fh.write(json.dumps({"id": r[0], "receipt_no": r[1], "filename": r[2], "identity": r[3]}, ensure_ascii=False) + "\n")
    print(f"백업 {len(ids)}건 → {bpath.relative_to(ROOT)}", flush=True)
    ask = None
    if args.ask:
        from zzaimy.app.pipeline import DocumentProcessor

        ask = DocumentProcessor().ask_review
    flagged = {p["id"] for p in plans if p["collision"]}
    done = 0
    for r in _rows(db, [p["id"] for p in plans]):
        db.rename_from_text(r["id"], r["masked_text"] or "", overwrite=True, ask=ask, prefer_file=r["id"] in flagged)
        done += 1
    print(f"적용 {done}건")
    print(f"되돌리기: scripts/zz_run.sh 12G env PYTHONPATH=src .venv/bin/python scripts/124_refresh_names.py --restore {bpath.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
