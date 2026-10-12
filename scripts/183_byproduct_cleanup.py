"""부산물 폴더 문서 정리(소급) — 규칙이 생기기 전에 처리·반입된 부산물 폴더 속 파일을 문서함·장부에서 걷는다.

부산물 폴더(zzaimy/doc_formats.is_byproduct): 우리 변환기 출력(<문서>_imgs/ 등)과 웹 페이지를 「다른 이름으로 저장」할 때
생기는 자료 폴더(<이름>_files/, 엑셀·워드를 웹 페이지로 저장한 <이름>.files/ — sheet001.htm, 프레임 outer·channel·inner 등).
이 안의 파일은 본문 문서의 부속이라 DGX 처리(167)·반입(168)·연동 점검(175)이 이제 고르지 않는다. 그 전에 들어온 것을 정리한다.

하는 일(--apply 때만)
1. 부산물 폴더 원본에 이어진 문서함 문서를 라이브러리 삭제와 같은 함수로 지운다 — db.delete_document(문서·조각·검토·개체 등)
   + storage.remove_intake_dir(파일 장부). 학생 명단처럼 개인정보가 든 부속 표가 있어 숨김이 아니라 지운다.
   사업 문서 색인·어휘 색인은 다음 주기(170 --quick·--index)가 지워진 조각을 빼고, 그래프는 문서 수가 바뀌어 다시 짓는다(.kg-dirty).
   같은 문서가 부산물이 아닌 원본에도 이어져 있으면 지우지 않고 「건너뜀」으로 보인다.
2. 원본 장부(archive_files)의 부산물 줄은 남기되 문서 연결을 풀고 처리 기록을 「부산물」로 바꾼다(처리 실패·가벼운 처리 → 부산물).
3. 원본 경로 장부(origins.jsonl)에서 지운 문서를 가리키는 부산물 줄을 뺀다 — 남기면 다음 동기화(170)가 장부에 지운 문서 번호를 다시 잇는다.
적용 전에 지울 문서·조각·장부 줄·원본 경로 장부를 data/platform/backups/byproduct-<시각>/ 에 백업한다(개인정보 포함 — 700 권한).
동기화·반입 잠금(zz_sync·zz_parsed·168)을 잡고 돈다. 하나라도 돌고 있으면 적용하지 않고 끝낸다.

실행(VM): set -a; . ./.env.local; set +a
          env PYTHONPATH=src .venv/bin/python scripts/183_byproduct_cleanup.py            # 미리 보기(건수·목록, 쓰지 않음)
          env PYTHONPATH=src .venv/bin/python scripts/183_byproduct_cleanup.py --apply    # 백업 뒤 적용
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.doc_formats import is_byproduct  # noqa: E402

BYPRODUCT_MARK = "부산물"                     # 원본 장부 analysis — 처리 대상이 아닌 부속 파일
DGX = "dgx://"
LOCKS = ("/tmp/zz_sync.lock", "/tmp/zz_parsed.lock", "/tmp/zz_parsed_import.lock", "/tmp/zz_parsed_import168.lock")


def read_origins(path: Path) -> list[tuple[str, dict | None]]:
    """원본 경로 장부의 줄 — (원래 줄, 풀린 값). 깨진 줄도 그대로 다시 쓰도록 남긴다."""
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        try:
            out.append((line, json.loads(line)))
        except ValueError:
            out.append((line, None))
    return out


def _doc_id(o: dict | None) -> int | None:
    try:
        return int(o["doc_id"]) if o and o.get("doc_id") is not None else None
    except (TypeError, ValueError):
        return None


def plan(db, origins_path: Path) -> dict:
    """무엇을 지우고 바꿀지 — 쓰지 않는다."""
    from zzaimy.app import archive

    archive.ensure(db)
    with db._conn() as conn:
        ledger = [tuple(r) for r in conn.execute("SELECT rel, doc_id, COALESCE(analysis, '') FROM archive_files").fetchall()]
        dgx_docs = [tuple(r) for r in conn.execute(
            "SELECT id, stored_path FROM documents WHERE stored_path LIKE 'dgx://%'").fetchall()]
    origins = read_origins(origins_path)
    # 문서 → 이어진 원본 경로(부산물인지)
    links: dict[int, set[str]] = {}
    for did, sp in dgx_docs:
        links.setdefault(int(did), set()).add(str(sp)[len(DGX):])
    for rel, did, _a in ledger:
        if did is not None:
            links.setdefault(int(did), set()).add(rel)
    for _line, o in origins:
        did = _doc_id(o)
        if did is not None and o.get("origin"):
            links.setdefault(did, set()).add(str(o["origin"]))
    candidates = {did for did, rels in links.items() if any(is_byproduct(r) for r in rels)}
    with db._conn() as conn:
        exist = set()
        ids = sorted(candidates)
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            exist |= {int(r[0]) for r in conn.execute(
                f"SELECT id FROM documents WHERE id IN ({','.join('?' * len(part))})", part).fetchall()}
    delete, kept = [], []
    for did in sorted(candidates & exist):
        (kept if any(not is_byproduct(r) for r in links[did]) else delete).append(did)
    gone = set(delete) | (candidates - exist)        # 이미 지워진 문서를 가리키는 부산물 연결도 함께 푼다
    ledger_rows = [(rel, did, a) for rel, did, a in ledger if is_byproduct(rel)
                   and ((did is not None and int(did) in gone) or a.startswith("처리 실패") or a == "가벼운 처리")]
    origin_drop = [line for line, o in origins if o and is_byproduct(str(o.get("origin") or "")) and _doc_id(o) in gone]
    return {"delete": delete, "kept": kept, "gone": sorted(gone), "ledger": ledger_rows, "origin_drop": origin_drop,
            "byproduct_rows": sum(1 for r in ledger if is_byproduct(r[0])), "links": {d: sorted(links[d]) for d in delete + kept}}


def _grab_locks():
    import fcntl
    held = []
    for p in LOCKS:
        fh = open(p, "a")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            for h in held:
                h.close()
            return None, p
        held.append(fh)
    return held, ""


def backup(db, p: dict, origins_path: Path, out_dir: Path) -> Path:
    """지울 문서(행·조각)·바꿀 장부 줄·원본 경로 장부를 백업한다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    docs = []
    for did in p["delete"]:
        d = db.get_document(did) or {}
        d["chunks"] = db.list_doc_chunks(did)
        docs.append(d)
    (out_dir / "documents.json").write_text(json.dumps(docs, ensure_ascii=False, default=str), encoding="utf-8")
    (out_dir / "archive_files.json").write_text(json.dumps(
        [{"rel": r, "doc_id": d, "analysis": a} for r, d, a in p["ledger"]], ensure_ascii=False), encoding="utf-8")
    if origins_path.is_file():
        (out_dir / "origins.jsonl").write_bytes(origins_path.read_bytes())
    for f in out_dir.iterdir():
        os.chmod(f, 0o600)
    return out_dir


def apply(db, p: dict, origins_path: Path) -> dict:
    """적용 — 문서 삭제(라이브러리 삭제와 같은 함수)·장부 줄 「부산물」·원본 경로 장부 줄 빼기."""
    from zzaimy.app import storage

    n_del = 0
    for did in p["delete"]:
        doc = db.get_document(did)
        if doc is None:
            continue
        db.delete_document(did)
        storage.remove_intake_dir(db, doc)
        n_del += 1
    with db._conn() as conn:
        for rel, _d, _a in p["ledger"]:
            conn.execute("UPDATE archive_files SET doc_id = NULL, analysis = ? WHERE rel = ?", (BYPRODUCT_MARK, rel))
    drop = set(p["origin_drop"])
    if drop and origins_path.is_file():
        keep = [line for line, _o in read_origins(origins_path) if line not in drop]
        tmp = origins_path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(line + "\n" for line in keep), encoding="utf-8")
        tmp.replace(origins_path)
    if n_del:
        (Path(db.path).parent / ".kg-dirty").touch()   # 1분 주기가 그래프·색인을 맞춘다
    return {"deleted": n_del, "ledger": len(p["ledger"]), "origins": len(drop)}


def main() -> int:
    ap = argparse.ArgumentParser(description="부산물 폴더 문서 정리(소급)")
    ap.add_argument("--apply", action="store_true", help="백업 뒤 적용(없으면 미리 보기)")
    args = ap.parse_args()
    from zzaimy.app.db import Database

    plat = ROOT / "data" / "platform"
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or plat / "platform.db"))
    origins_path = plat / "origins.jsonl"
    held = None
    if args.apply:
        held, busy = _grab_locks()
        if held is None:
            print(f"동기화·반입이 도는 중({busy}) — 적용하지 않음. 끝난 뒤 다시", flush=True)
            return 1
    p = plan(db, origins_path)
    exts = Counter(Path(r).suffix.lower() for d in p["delete"] for r in p["links"][d])
    print(f"원본 장부의 부산물 폴더 줄 {p['byproduct_rows']:,} · 지울 문서 {len(p['delete'])} · 건너뜀(부산물 아닌 원본에도 이어짐) "
          f"{len(p['kept'])} · 「부산물」로 바꿀 장부 줄 {len(p['ledger'])} · 원본 경로 장부에서 뺄 줄 {len(p['origin_drop'])}", flush=True)
    print("  지울 문서 형식", dict(exts), flush=True)
    for did in p["delete"][:30]:
        print(f"  삭제 {did}  {p['links'][did][0][-90:]}", flush=True)
    for did in p["kept"][:10]:
        print(f"  건너뜀 {did}  {', '.join(r[-60:] for r in p['links'][did])}", flush=True)
    print("  장부 처리 기록", dict(Counter((a.split(":")[0] or "(빈 값)") for _r, _d, a in p["ledger"])), flush=True)
    if not args.apply:
        print("미리 보기 — 적용은 --apply", flush=True)
        return 0
    bk = backup(db, p, origins_path, plat / "backups" / f"byproduct-{time.strftime('%Y%m%d%H%M%S')}")
    got = apply(db, p, origins_path)
    print(f"적용: 문서 삭제 {got['deleted']} · 장부 「부산물」 {got['ledger']} · 원본 경로 장부 뺌 {got['origins']} · 백업 {bk}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
