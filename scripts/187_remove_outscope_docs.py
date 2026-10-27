#!/usr/bin/env python3
"""필요 없는 문서를 라이브러리에서 뺀다 — 지출·계약 증빙(절대 규칙 11)·같은 본문 여분·빈 문서·도면·개인 제출물(--what).
원본 파일은 그대로, 백업으로 되돌릴 수 있게.

- 대상: 서류 갈래가 증빙(excluded_kinds)인 사업 문서. 검색·그래프에서는 이미 빠져 있고 라이브러리에만 남아 있었다.
  경로 규칙(archive_state 「범위 밖 경로」)만으로는 고르지 않는다 — 정산 폴더 속 계획서·보고서까지 걸린다.
- 지우기 전에 문서 행과 그 문서에 딸린 행(doc_id 칸이 있는 표 전부 — 조각·그림·개체·검토·어휘 색인 등)과
  원본·파일 장부·대화 출처·판본 이음을 한 파일(data/platform/backups/outscope-<시각>.jsonl.gz)에 남긴다.
- 지우기는 db.delete_document 와 같은 길(파생물까지). 원본 장부의 이음(doc_id)은 비우고 그 원본은 정리함 「지출·계약 증빙」으로
  옮긴다(증빙은 「지출·계약 증빙」, 나머지는 「라이브러리에서 뺀 문서」. 파일은 그대로) — 보관소 건수에서 빠지고, 맞추기가 다시 들이지 않는다.

사용(VM): env PYTHONPATH=src .venv/bin/python scripts/187_remove_outscope_docs.py            # 미리 보기
          ... --what dup,tiny --apply
          ... --restore data/platform/backups/outscope-<시각>.jsonl.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database, table_names  # noqa: E402

DOC_TABLES = ("doc_chunks", "doc_assets", "doc_entities", "mask_events", "reviews", "quality_reports", "draft_history",
              "grant_lex")
# 벡터 표(grant_vec)는 백업하지 않는다 — 되돌린 뒤 색인 따라잡기(grant_search, ADR-0057)가 빠진 조각을 다시 임베딩한다
LINKS = (("archive_files", "rel", "doc_id"), ("files", "id", "doc_id"), ("chat_sources", "id", "doc_id"),
         ("documents", "id", "version_of"), ("documents", "id", "related_criteria_id"))


WHAT = {
    "evidence": "지출·계약 증빙 갈래",
    "evidence_title": "제목이 지출·계약 증빙인 문서(갈래는 본문으로 달리 붙었어도)",
    "dup": "같은 본문 여분(사업마다 한 건만 남김)",
    "near": "제목이 같고 본문이 거의 같은 여분(같은 사업 안, 겹침 doc_family.NEAR_DUP 이상 — 최신 판 하나만 남김)",
    "tiny": "본문 100자 미만(양식 제외)",
    "drawing": "도면 갈래",
    "personal": "개인 제출물 갈래(수기·응모작 등)",
}
CLEANUP = {"evidence": "evidence", "evidence_title": "evidence"}                    # 원본을 옮길 정리함 갈래 — 나머지는 pruned
TINY = 100
# 같은 본문 묶음에서 남길 쪽을 고를 때 뒤로 미는 경로 낱말 — 사람이 만든 사본·넘겨받은 묶음
_COPYISH = re.compile(r"인수인계|사본|복사본|백업|backup|copy|(^|/)새 폴더", re.I)


def _school(conn) -> set[int]:
    """학교 원본과 이어진 문서 — 사람이 직접 올린 문서·프로젝트 첨부는 건드리지 않는다."""
    return {int(r[0]) for r in conn.execute("SELECT DISTINCT doc_id FROM archive_files WHERE doc_id IS NOT NULL")}


def targets(db, what: str = "evidence") -> list[int]:
    """고른 갈래의 라이브러리 문서(학교 원본에서 온 사업 문서만).
    evidence — 지출·계약 증빙 갈래(doc_routing.excluded_kinds, 절대 규칙 11). 경로만으로 고르지 않는다: 「정산·집행」 폴더에도
               사업계획서·실적보고서·집행 요령이 있다(그것은 남긴다)
    dup      — 본문이 글자 하나 다르지 않은 문서 묶음에서, 같은 사업 안의 여분. 남기는 쪽은 사본·인수인계 경로가 아닌 것 → 최신
    near     — 같은 사업 안에서 판 꼬리를 뗀 제목(library.version_key)이 같고 본문 겹침이 doc_family.NEAR_DUP 이상인 여분.
               반입의 같은 내용 판정(doc_family.judge)과 같은 잣대 — 남기는 쪽은 dup 과 같다. 겹침이 그보다 낮은 옛 판은 남긴다
               (라이브러리는 판본으로 묶어 최신 판을 보이고, 검색은 계열의 최종본을 쓴다)
    tiny     — 처리는 끝났는데 본문이 TINY 자 미만(시안·빈 표·그림만 있는 파일). 양식은 빈 칸이 본문이라 남긴다
    drawing·personal — 서류 갈래(guess_kind)가 도면·개인 제출물"""
    with db._conn() as conn:
        school = _school(conn)
        if what == "evidence":
            from zzaimy.app.doc_routing import excluded_kinds
            kinds = excluded_kinds()
            ids = [int(r[0]) for r in conn.execute(
                f"SELECT id FROM documents WHERE doc_type = 'grant' AND kind IN ({','.join('?' * len(kinds))})", list(kinds))] if kinds else []
            return sorted(ids)                    # 증빙은 직접 올린 것도 같은 규칙(처음 정리 때와 같게)
        if what == "evidence_title":
            # 사람도 서류를 제목으로 안다 — 제목의 머리 낱말이 증빙(세금계산서·거래명세서·견적서·청구서·계약서 …)이면 지출·계약 계열
            from zzaimy.app.doc_routing import excluded_kinds, guess_kind
            kinds = set(excluded_kinds())
            return sorted(int(r[0]) for r in conn.execute("SELECT id, filename, kind FROM documents WHERE doc_type = 'grant'")
                          if (r[2] or "") not in kinds and guess_kind(r[1] or "", "")[0] in kinds)
        if what in ("drawing", "personal"):
            return sorted(int(r[0]) for r in conn.execute(
                "SELECT id FROM documents WHERE doc_type = 'grant' AND kind = ?", (what,)) if int(r[0]) in school)
        if what == "tiny":
            return sorted(int(r[0]) for r in conn.execute(
                "SELECT id FROM documents WHERE doc_type = 'grant' AND status = 'reviewed' AND COALESCE(kind, '') <> 'form'"
                " AND LENGTH(COALESCE(masked_text, '')) < ?", (TINY,)) if int(r[0]) in school)
        if what == "dup":
            import hashlib
            rel = {int(r[0]): r[1] for r in conn.execute("SELECT doc_id, MIN(rel) FROM archive_files WHERE doc_id IS NOT NULL GROUP BY doc_id")}
            prog = {int(r[0]): (r[1] or "").split("|", 1)[0] for r in conn.execute(
                "SELECT d.id, p.program FROM documents d LEFT JOIN projects p ON p.id = d.project_id WHERE d.doc_type = 'grant'")}
            groups: dict[tuple, list[int]] = {}
            top = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM documents").fetchone()[0])
            for lo in range(0, top + 1, 2000):                # 본문을 한꺼번에 읽지 않게 번호 구간마다
                for did, text in conn.execute("SELECT id, masked_text FROM documents WHERE doc_type = 'grant' AND status = 'reviewed'"
                                              " AND id >= ? AND id < ? AND LENGTH(COALESCE(masked_text, '')) >= ?", (lo, lo + 2000, TINY)):
                    did = int(did)
                    if did in school:
                        h = hashlib.sha1((text or "").encode("utf-8")).hexdigest()
                        groups.setdefault((h, prog.get(did, "")), []).append(did)
            out = []
            for ids in groups.values():
                if len(ids) > 1:
                    keep = min(ids, key=lambda d: (bool(_COPYISH.search(rel.get(d, ""))), -d))
                    out += [d for d in ids if d != keep]
            return sorted(out)
        if what == "near":
            return _near_dups(conn, school)
    raise SystemExit(f"모르는 갈래: {what} — {', '.join(WHAT)}")


NEAR_GROUP_MAX = 60          # 한 제목 묶음에서 견줄 문서 수 상한 — 흔한 제목(「붙임」 등)이 비교를 키우지 않게, 넘는 것은 남긴다


def _near_dups(conn, school: set[int]) -> list[int]:
    from zzaimy.app.doc_family import NEAR_DUP, similarity
    from zzaimy.app.library import version_key
    rel = {int(r[0]): r[1] for r in conn.execute("SELECT doc_id, MIN(rel) FROM archive_files WHERE doc_id IS NOT NULL GROUP BY doc_id")}
    groups: dict[tuple, list[int]] = {}
    for did, name, prog in conn.execute(
            "SELECT d.id, d.filename, p.program FROM documents d LEFT JOIN projects p ON p.id = d.project_id"
            " WHERE d.doc_type = 'grant' AND d.status = 'reviewed' AND LENGTH(COALESCE(d.masked_text, '')) >= ?", (TINY,)):
        did = int(did)
        if did in school:
            groups.setdefault((version_key(name or ""), (prog or "").split("|", 1)[0]), []).append(did)
    out: list[int] = []
    for ids in groups.values():
        if len(ids) < 2:
            continue
        ids = sorted(ids, key=lambda d: (bool(_COPYISH.search(rel.get(d, ""))), -d))[:NEAR_GROUP_MAX]   # 남길 쪽이 앞
        texts = {int(a): (b or "") for a, b in conn.execute(
            f"SELECT id, masked_text FROM documents WHERE id IN ({','.join('?' * len(ids))})", ids)}
        kept: list[int] = []
        for d in ids:
            if any(similarity(texts.get(d, ""), texts.get(k, "")) >= NEAR_DUP for k in kept):
                out.append(d)
            else:
                kept.append(d)
    return sorted(out)


def _cols(conn, table: str) -> list[str]:
    cur = conn.execute(f"SELECT * FROM {table} LIMIT 0")
    return [c[0] for c in cur.description or []]


def backup(db, ids: list[int], out: Path) -> int:
    n = 0
    with db._conn() as conn, gzip.open(out, "wt", encoding="utf-8") as f:
        have = set(table_names(conn))
        for t in ("documents", *DOC_TABLES):
            if t not in have:
                continue
            cols = _cols(conn, t)
            key = "id" if t == "documents" else "doc_id"
            if key not in cols:
                continue
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                for r in conn.execute(f"SELECT * FROM {t} WHERE {key} IN ({','.join('?' * len(part))})", part):
                    f.write(json.dumps({"t": t, "row": dict(zip(cols, r))}, ensure_ascii=False, default=str) + "\n")
                    n += 1
        for t, key, col in LINKS:                   # 지우면 비워지는 이음 — 되돌릴 때 다시 잇는다
            if t not in have:
                continue
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                for k, v in conn.execute(f"SELECT {key}, {col} FROM {t} WHERE {col} IN ({','.join('?' * len(part))})", part):
                    f.write(json.dumps({"t": "link", "row": {"table": t, "key": key, "col": col, "k": k, "v": v}},
                                       ensure_ascii=False) + "\n")
    return n


def _retry(fn, tries: int = 8):
    """동기화(170)가 원본 장부를 잡고 있으면 잠금 시간 초과가 난다 — 잠시 뒤 다시."""
    for k in range(tries):
        try:
            return fn()
        except Exception as e:                    # psycopg LockNotAvailable 등
            if "lock" not in str(e).lower() or k == tries - 1:
                raise
            time.sleep(5 * (k + 1))


def remove(db, ids: list[int], what: str = "evidence") -> int:
    """문서를 빼고, 이어져 있던 학교 원본은 정리함 「지출·계약 증빙」으로 — 맞추기(175 --find-reimport)가 다시 들이지 않게.
    문서 한 건씩 끝맺는다(중간에 멈춰도 뺀 문서의 원본은 정리함에 있다)."""
    from zzaimy.app import archive_cleanup
    with db._conn() as conn:
        have = set(table_names(conn))
    moved = 0

    def one(did: int) -> int:
        with db._conn() as conn:
            rels = [r[0] for r in conn.execute("SELECT rel FROM archive_files WHERE doc_id = ?", (did,))]
            for t in ("grant_lex", "grant_vec"):
                if t in have:
                    conn.execute(f"DELETE FROM {t} WHERE doc_id = ?", (did,))
            conn.execute("UPDATE archive_files SET doc_id = NULL WHERE doc_id = ?", (did,))
        archive_cleanup.put(db, rels, CLEANUP.get(what, "pruned"), by="scripts/187")
        db.delete_document(did)
        return len(rels)

    for i, did in enumerate(ids, 1):
        moved += _retry(lambda: one(did))
        if i % 200 == 0:
            print(f"  {i}/{len(ids)}", flush=True)
    return moved


def put_from_backup(db, path: Path) -> int:
    """앞선 실행이 멈췄을 때 — 백업에 적힌 원본 이음 가운데 이미 끊긴 것을 정리함으로."""
    from zzaimy.app import archive_cleanup
    rels = [r["row"]["k"] for r in map(json.loads, gzip.open(path, "rt", encoding="utf-8"))
            if r["t"] == "link" and r["row"]["table"] == "archive_files"]
    with db._conn() as conn:
        loose = [rel for rel in rels if (conn.execute("SELECT doc_id FROM archive_files WHERE rel = ?", (rel,)).fetchone() or [1])[0] is None]
    return archive_cleanup.put(db, loose, "evidence", by="scripts/187")


def restore(db, path: Path) -> None:
    rows = [json.loads(line) for line in gzip.open(path, "rt", encoding="utf-8")]
    order = ["documents", *DOC_TABLES]
    with db._conn() as conn:
        have = set(table_names(conn))
        for t in (t for t in order if t in have):
            for rec in (r for r in rows if r["t"] == t):
                row = rec["row"]
                cols = list(row)
                conn.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) ON CONFLICT DO NOTHING",
                             [row[c] for c in cols])
        # 정리함에서도 되돌린다 — 이 백업이 옮긴 원본만
        for rec in (r["row"] for r in rows if r["t"] == "link" and r["row"]["table"] == "archive_files"):
            conn.execute("DELETE FROM archive_cleanup WHERE rel = ? AND category IN ('evidence', 'pruned')", (rec["k"],))
        ok = {(t, k, c) for t, k, c in LINKS}
        for rec in (r["row"] for r in rows if r["t"] == "link"):
            if (rec["table"], rec["key"], rec["col"]) in ok:
                conn.execute(f"UPDATE {rec['table']} SET {rec['col']} = ? WHERE {rec['key']} = ?", (rec["v"], rec["k"]))
    print(f"되돌림 — {Counter(r['t'] for r in rows)}")


def prune_new(db, whats=("evidence", "evidence_title")) -> int:
    """동기화가 부르는 상시 정리 — 학교 원본에서 새로 들어온 지출·계약 증빙을 처음 정리와 같은 규칙으로 정리함에.
    사람이 직접 올린 문서는 건드리지 않는다(올린 사람이 둔 것). 백업을 남기므로 --restore 로 되돌린다."""
    with db._conn() as conn:
        school = _school(conn)
    total = 0
    for what in whats:
        ids = [i for i in targets(db, what) if i in school]
        if not ids:
            continue
        out = ROOT / "data/platform/backups" / f"prune-{what}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl.gz"
        out.parent.mkdir(parents=True, exist_ok=True)
        backup(db, ids, out)
        moved = remove(db, ids, what)
        print(f"범위 밖 정리 [{what}] {len(ids)}건 · 원본 {moved}건 정리함으로 · 되돌리기 --restore {out.name}", flush=True)
        total += len(ids)
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--what", default="evidence", help="뺄 갈래(쉼표): " + ", ".join(f"{k}={v}" for k, v in WHAT.items()))
    ap.add_argument("--restore", default="")
    ap.add_argument("--settle", default="", help="멈춘 실행의 백업 파일 — 이미 끊긴 원본을 정리함으로")
    args = ap.parse_args()
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    if args.restore:
        restore(db, Path(args.restore))
        return 0
    if args.settle:
        print(f"정리함으로 {put_from_backup(db, Path(args.settle))}건")
    for what in [w.strip() for w in args.what.split(",") if w.strip()]:
        ids = targets(db, what)
        with db._conn() as conn:
            sample = [r[0] for r in conn.execute(f"SELECT filename FROM documents WHERE id IN ({','.join('?' * min(8, len(ids)))})", ids[:8])] if ids else []
        print(f"[{what}] {WHAT.get(what, what)} — 라이브러리 문서 {len(ids)}건 · 예: {sample}")
        if not args.apply or not ids:
            continue
        out = ROOT / "data/platform/backups" / f"prune-{what}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl.gz"
        out.parent.mkdir(parents=True, exist_ok=True)
        n = backup(db, ids, out)
        print(f"  백업 {n}줄 → {out}")
        moved = remove(db, ids, what)
        print(f"  뺐다 — {len(ids)}건 · 원본 {moved}건 정리함으로. 되돌리기: scripts/187_remove_outscope_docs.py --restore {out}", flush=True)
    if not args.apply:
        print("미리 보기입니다 — --apply 로 백업 뒤 뺍니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
