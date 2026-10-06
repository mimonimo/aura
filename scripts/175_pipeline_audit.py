#!/usr/bin/env python3
"""반입 연동 점검 — 원본 → 문서함 → 색인 → 그래프까지 단계마다 다음 단계로 넘어가지 못한 수를 센다(읽기만, 고치지 않는다).

10/5: DGX 원본 목록이 10/3 것 그대로라 그 뒤 원본 2만여 건이 처리 대상에 오르지 않았고, pptx·xls 는 처리 형식에서 빠져 있었다 —
사람이 우연히 숫자를 보고서야 알았다. 이 점검이 주기마다 같은 종류의 빈틈을 기계로 찾는다.

단계와 판정
1. 원본 장부(archive_files) 의 처리 대상 = 문서 형식 · 범위 안(지출·증빙 등 경로 제외) · 중복 아님 · 있음
   → 결과가 있는가: 문서함 연결(doc_id·origins.jsonl) 또는 실패 사유(parse_failures.jsonl)
   → 없으면 「대기」, 들어오거나 바뀐 지 LAG_H 시간이 넘으면 「놓침」(형식별·폴더별로 센다)
2. 문서함: 처리 중(received·processing)으로 1시간 넘게 멈춘 문서
3. 색인: 사업 문서 조각 수 대 임베딩 색인·어휘 색인 수
4. 그래프: 문서함의 사업 문서 가운데 그래프 문서 노드가 없는 것
출력: data/platform/pipeline_audit.json · 요약 줄(PIPELINE_AUDIT) · sync_status 의 audit.

사용(VM): env PYTHONPATH=src .venv/bin/python scripts/175_pipeline_audit.py [--backfill-failures]
  --backfill-failures: VM 이 가진 DGX 처리 결과 사본(data/inbox/parsed)에서 지난 실패를 parse_failures.jsonl 로 한 번 채운다
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402

DOC_EXT = {"hwp", "hwpx", "pdf", "docx", "xlsx", "pptx", "xls", "doc", "ppt"}
IMG_EXT = {"jpg", "jpeg", "png", "bmp", "gif", "tif", "tiff", "heic", "webp"}
ARC_EXT = {"zip", "7z", "rar", "egg", "alz"}
OUT_OF_SCOPE = re.compile(r"지출|증빙|스캔|영수|정산|집행")      # 절대 규칙 11 — DGX 주기 처리(zz_dgx_cycle.sh)와 같은 기준
LAG_H = 6
MAX_PARSE_BYTES = 300 * 1024 * 1024        # scripts/167 MAX_BYTES 와 같다
PLAT = ROOT / "data" / "platform"


def backfill_failures() -> int:
    """DGX 처리 결과 사본에서 rel 마다 마지막 기록이 실패이고 성공 기록이 없는 것을 실패 장부에 — 한 번만."""
    last: dict[str, dict] = {}
    for f in sorted((ROOT / "data" / "inbox" / "parsed").glob("parsed-*.jsonl")):
        with open(f, encoding="utf-8", newline="") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                rel = r.get("rel")
                if not rel:
                    continue
                ok = bool(r.get("ok")) and r.get("state") in (None, "parsed", "partial")
                cur = last.get(rel)
                if cur is None or not cur["ok"]:
                    last[rel] = {"ok": ok, "error": str(r.get("error") or "")[:200], "state": r.get("state") or ("parsed" if ok else "failed")}
    n = 0
    with (PLAT / "parse_failures.jsonl").open("a", encoding="utf-8") as out:
        for rel, r in last.items():
            if not r["ok"]:
                out.write(json.dumps({"rel": rel, "state": r["state"], "error": r["error"], "at": "backfill"}, ensure_ascii=False) + "\n")
                n += 1
    return n


def find_reimport(db, missing: dict[str, str]) -> list[str]:
    """결과 없는 대상 가운데 VM 의 DGX 결과 사본에 같은 판 정상 기록이 있는 원본 — 168 --rels 로 다시 들일 목록."""
    hit: set[str] = set()
    for f in sorted((ROOT / "data" / "inbox" / "parsed").glob("parsed-*.jsonl")):
        with open(f, encoding="utf-8", newline="") as fh:
            for line in fh:
                if '"ok": true' not in line[:4000] and '"ok": true' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                rel = r.get("rel")
                # 판이 비어 있는 옛 기록은 크기·수정 시각으로(168 과 같은 계산) — 빈 판끼리 견주어 다시 들임에서 빠지던 것
                ver = r.get("version") or f"{int(r.get('size') or 0)}:{int(float(r.get('mtime') or 0))}"
                if rel in missing and r.get("chunks") and r.get("state") in (None, "parsed", "partial") and ver == missing[rel]:
                    hit.add(rel)
    return sorted(hit)


def _jsonl_rels(path: Path, key: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if path.is_file():
        with open(path, encoding="utf-8", newline="") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get(key):
                    out[r[key]] = r
    return out


def reason(err: str) -> str:
    e = err or ""
    for k, label in (("암호", "암호 문서"), ("빈 파일", "빈 파일"), ("형식이 아닙니다", "형식 불일치"), ("시간 초과", "시간 초과"),
                     ("같은 내용", "같은 내용 중복"), ("스캔 PDF", "스캔 판독 없음"), ("ModuleNotFound", "판독 환경 오류")):
        if k in e:
            return label
    return "기타 판독 실패"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill-failures", action="store_true")
    ap.add_argument("--find-reimport", action="store_true", help="결과 없는 대상 중 DGX 정상 기록이 있는 것을 data/platform/reimport_rels.json 으로")
    args = ap.parse_args()
    if args.backfill_failures:
        print(f"지난 실패 {backfill_failures()}건을 실패 장부에 채움", flush=True)
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or PLAT / "platform.db"))
    now = datetime.now()
    origins = _jsonl_rels(PLAT / "origins.jsonl", "origin")
    fails = _jsonl_rels(PLAT / "parse_failures.jsonl", "rel")
    with db._conn() as conn:
        rows = conn.execute("SELECT rel, ext, dup_of, doc_id, seen_at, COALESCE(removed_at, ''), size, mtime FROM archive_files").fetchall()
    total = len(rows)
    # 같은 파일(이름·크기) 묶음 — DGX(165)와 VM(170)이 중복 묶음의 대표를 서로 다르게 골라, 처리된 쪽이 VM 에선 「중복」,
    # 처리 안 된 쪽이 「대상」이 되어 놓침으로 잡혔다(2026-10-06, 155건 중 100건). 묶음 중 하나라도 들어왔으면 처리된 것으로 본다
    covered_groups = set()
    for rel, ext, dup, doc_id, seen_at, removed, size, mtime in rows:
        if not removed and (doc_id or rel in origins):
            covered_groups.add(((rel or "").rsplit("/", 1)[-1], int(size or 0)))
    too_big = 0
    target = done = failed = waiting = 0
    missed: Counter = Counter()
    missed_dirs: Counter = Counter()
    fail_why: Counter = Counter()
    examples: list[str] = []
    no_result: dict[str, str] = {}
    comp: Counter = Counter()
    missed_rows: list[dict] = []
    failed_rows: list[dict] = []
    for rel, ext, dup, doc_id, seen_at, removed, size, mtime in rows:
        e = (ext or "").lower().lstrip(".")
        if removed:
            continue
        if e in IMG_EXT:
            comp["사진·그림"] += 1
            continue
        if e in ARC_EXT:
            comp["압축 파일"] += 1
            continue
        if e not in DOC_EXT:
            comp["기타 비문서(회계 서식·동영상 등)"] += 1
            continue
        if dup:
            comp["같은 파일 중복"] += 1
            continue
        if OUT_OF_SCOPE.search(rel or ""):
            comp["범위 밖(지출·증빙·정산 등)"] += 1
            continue
        comp["처리 대상 문서"] += 1
        target += 1
        if doc_id or rel in origins or ((rel or "").rsplit("/", 1)[-1], int(size or 0)) in covered_groups:
            done += 1
            continue
        if int(size or 0) > MAX_PARSE_BYTES:          # DGX 처리기(167)가 정책상 건너뛰는 크기 — 놓침이 아니다
            too_big += 1
            continue
        no_result[rel] = f"{int(size or 0)}:{int(float(mtime or 0))}"
        if rel in fails:
            failed += 1
            why = reason(fails[rel].get("error", ""))
            fail_why[why] += 1
            failed_rows.append({"rel": rel, "reason": why, "error": fails[rel].get("error", "")[:160]})
            continue
        try:
            age = now - datetime.fromisoformat(str(seen_at)[:19].replace(" ", "T"))
        except ValueError:
            age = timedelta(hours=LAG_H + 1)
        if age < timedelta(hours=LAG_H):
            waiting += 1
            continue
        missed[e] += 1
        missed_dirs["/".join(rel.split("/")[:2])] += 1
        missed_rows.append({"rel": rel, "ext": e, "seen_at": str(seen_at)[:16]})
        if len(examples) < 10:
            examples.append(rel)
    with db._conn() as conn:
        stuck = conn.execute("SELECT COUNT(*) FROM documents WHERE status IN ('received', 'processing') AND created_at < ?",
                             ((now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0]
        n_chunks = conn.execute("SELECT COUNT(*) FROM doc_chunks c JOIN documents d ON d.id = c.doc_id WHERE d.doc_type = 'grant'"
                                " AND d.status = 'reviewed' AND c.kind IN ('text', 'table', 'image_text')").fetchone()[0]
        n_lex = conn.execute("SELECT COUNT(*) FROM grant_lex").fetchone()[0] if _has(conn, "grant_lex") else 0
        n_docs = conn.execute("SELECT COUNT(*) FROM documents WHERE doc_type = 'grant' AND status = 'reviewed'").fetchone()[0]
        n_graph = conn.execute("SELECT COUNT(*) FROM kg_nodes WHERE type = 'doc'").fetchone()[0] if _has(conn, "kg_nodes") else 0
    emb = {}
    try:
        emb = json.loads((PLAT / "knowledge" / "index" / "grant_embeddings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    n_emb = int(emb.get("n_chunks") or 0)
    alerts = []
    if sum(missed.values()):
        alerts.append(f"원본 처리 놓침 {sum(missed.values())}건(들어오거나 바뀐 지 {LAG_H}시간 넘게 결과 없음) — 형식 {dict(missed.most_common(5))}")
    if stuck:
        alerts.append(f"문서함 처리 멈춤 {stuck}건(1시간 넘게 처리 중)")
    if n_chunks and n_emb < 0.97 * n_chunks:
        alerts.append(f"임베딩 색인 덜 참 {n_emb:,}/{n_chunks:,}")
    if n_chunks and n_lex < 0.97 * n_chunks:
        alerts.append(f"어휘 색인 덜 참 {n_lex:,}/{n_chunks:,}")
    if n_docs and n_graph < 0.95 * n_docs:
        alerts.append(f"그래프에 없는 사업 문서 {n_docs - n_graph:,}건")
    reimport = []
    if args.find_reimport:
        # 실패 장부에 있어도 같은 판의 정상 기록이 있으면 다시 들인다 — 환경 오류로 한 번 실패한 뒤 성공한 원본이 반입 중단(NUL 문자)으로
        # 못 들어오고 실패 기록만 남았던 688건(10/6)
        reimport = find_reimport(db, no_result)
        (PLAT / "reimport_rels.json").write_text(json.dumps(reimport, ensure_ascii=False), encoding="utf-8")
        if reimport:
            alerts.append(f"DGX 에서 정상 처리됐는데 문서함에 없는 원본 {len(reimport)}건 — 다시 들임 목록(reimport_rels.json)")
    for name, rows_ in (("pipeline_missed.jsonl", missed_rows), ("pipeline_failed.jsonl", failed_rows)):
        tmpf = PLAT / f".{name}.tmp"
        tmpf.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows_), encoding="utf-8")
        tmpf.replace(PLAT / name)                         # 화면의 목록 내려받기(/dev/intake)가 읽는다
    out = {"at": now.strftime("%Y-%m-%d %H:%M"), "reimport": len(reimport), "composition": dict(comp), "archive_files": total, "targets": target, "in_docbox": done, "failed": failed,
           "waiting": waiting, "too_big": too_big, "missed": sum(missed.values()), "missed_by_ext": dict(missed), "missed_by_dir": dict(missed_dirs.most_common(15)),
           "missed_examples": examples, "failed_by_reason": dict(fail_why), "stuck_docs": stuck, "grant_chunks": n_chunks,
           "embedding_index": n_emb, "lexical_index": n_lex, "grant_docs": n_docs, "graph_doc_nodes": n_graph, "alerts": alerts}
    (PLAT / "pipeline_audit.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        st = json.loads((PLAT / "sync_status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    st["audit"] = {"at": out["at"], "summary": f"대상 {target:,} · 문서함 {done:,} · 실패 {failed:,} · 대기 {waiting:,} · 놓침 {out['missed']:,}"
                   + (f" · 경고 {len(alerts)}" if alerts else "")}
    tmp = PLAT / ".sync_status.audit.tmp"
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PLAT / "sync_status.json")
    print("PIPELINE_AUDIT", json.dumps({k: v for k, v in out.items() if k not in ("missed_examples", "missed_by_dir")}, ensure_ascii=False))
    for a in alerts:
        print("경고:", a)
    return 0


def _has(conn, table: str) -> bool:
    from zzaimy.app.database_backend import table_names
    return table in table_names(conn)


if __name__ == "__main__":
    raise SystemExit(main())
