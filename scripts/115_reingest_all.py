#!/usr/bin/env python3
"""문서함을 비우고 원본 파일에서 반입을 처음부터 다시 돌린다.

왜: 반입 절차가 바뀌면(문서 이름을 본문 제목으로 바꾸는 등) 예전에 들어온 문서는 그 절차를
거치지 않은 상태로 남는다. 부분 보정으로 맞추면 무엇이 절차의 결과이고 무엇이 손질의 결과인지
구분되지 않는다. 그래서 통째로 다시 넣는다 — 지식 그래프·색인도 같은 절차에서 다시 만들어진다.

지우는 것: 문서·조각·자산·규정 조각·조각 질문·대화 근거·개체 그래프·임베딩 색인.
남기는 것: 계정·프로젝트·대화 본문·연결 설정.

사용 (VM 에서):
  env PYTHONPATH=src .venv/bin/python scripts/115_reingest_all.py            # 무엇을 할지만 보여 준다
  env PYTHONPATH=src .venv/bin/python scripts/115_reingest_all.py --apply
"""
from __future__ import annotations

import argparse
import hashlib
import multiprocessing as mp
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402

WIPE = ("regulation_chunks", "doc_chunks", "doc_assets", "chunk_questions",
        "chat_sources", "entities", "entity_links", "pii_records", "documents")
INDEXES = ("chunk_embeddings.npz", "chunk_embeddings.meta.json", "question_embeddings.npz")


def _run_one(db_path: str, doc_id: int, file_path: str) -> None:
    """문서 한 건을 따로 돌린다 — 굳으면 부모가 끊을 수 있게."""
    from zzaimy.app.db import Database as _DB
    from zzaimy.app.pipeline import DocumentProcessor as _P

    db = _DB(Path(db_path))
    try:
        _P().process(db, doc_id, Path(file_path))
    except Exception as e:
        db.update_document(doc_id, status="failed", error=f"{type(e).__name__}: {e}")
        raise SystemExit(1)


SOURCES = [
    # (폴더, 문서 유형, 설명, 소유) — 앞에서부터 순서대로 올린다.
    # 소유 corpus = 공개 수집 문서: 마스킹 대상이며, 공개 자료 판독(외부 모델)을 쓸 수 있다.
    ("data/scraped/iacf", "regulation", "영남이공대학교 산학협력단 규정", "zzaimy"),
    ("data/external", "recruit", "외부 기관 공고·안내", "corpus"),
    ("data/scraped/files", "regulation", "교내 내려받기 문서", "zzaimy"),
    ("data/scraped/uniall", "regulation", "국고사업 공개 문서(기본계획·서식)", "corpus"),
]
EXTS = {".pdf", ".hwp", ".hwpx", ".docx", ".png", ".jpg", ".jpeg", ".xlsx"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="원본 폴더마다 최대 몇 건까지")
    ap.add_argument("--timeout", type=int, default=10, help="문서 한 건의 제한 시간(분)")
    ap.add_argument("--jobs", type=int, default=2, help="동시에 처리할 문서 수")
    ap.add_argument("--resume", action="store_true",
                    help="비우지 않고 아직 안 들어간 것만 올린다(중단됐을 때)")
    args = ap.parse_args()

    db = Database(Path(args.db))
    plan: list[tuple[Path, str]] = []
    for rel, doc_type, label, owner in SOURCES:
        folder = ROOT / rel
        files = sorted(p for p in folder.glob("*") if p.is_file() and p.suffix.lower() in EXTS)
        if args.limit:
            files = files[: args.limit]
        print(f"  {label}: {len(files)}건 ({rel})")
        plan += [(p, doc_type, owner) for p in files]
    if args.resume:
        # 이미 들어간 원본은 건너뛴다 — 문서에 올라온 파일 이름을 남겨 두므로 그것으로 맞춘다
        done = set()
        for d in db.list_documents():
            ident = db.get_doc_identity(d["id"])
            done.add(ident.get("original_filename") or d["filename"])
            done.add(Path(d.get("stored_path") or "").name)
        plan = [(p, t, o) for p, t, o in plan if p.name not in done
                and f"{p.stem[:60]}{p.suffix.lower()}" not in done]
    print(f"지금 문서함 {len(db.list_documents())}건 → 새로 올릴 것 {len(plan)}건")
    if not args.apply:
        print("미리보기입니다. 비우고 새로 올리려면 --apply 를 붙이십시오.")
        return 0

    inbox = Path(args.db).parent / "inbox"
    if not args.resume:
        import sqlite3

        conn = sqlite3.connect(args.db, timeout=30)
        with conn:
            for t in WIPE:
                try:
                    conn.execute(f"DELETE FROM {t}")
                except sqlite3.OperationalError:
                    pass
        conn.close()
        for name in INDEXES:
            f = Path(args.db).parent / name
            if f.exists():
                f.unlink()
        if inbox.exists():
            for f in inbox.iterdir():
                if f.is_file():
                    f.unlink()
        print("문서함을 비웠습니다 — 이제 원본에서 올립니다.", flush=True)
    else:
        print("이어서 올립니다(문서함은 그대로).", flush=True)

    from zzaimy.app.pipeline import DocumentProcessor

    proc = DocumentProcessor()
    ok = fail = 0
    limit_s = args.timeout * 60
    t0 = time.time()
    def start(path: Path, doc_type: str, owner: str = "zzaimy"):
        tag = hashlib.sha1(str(path).encode()).hexdigest()[:8]
        dest = inbox / f"{path.stem[:40]}-{tag}{path.suffix.lower()}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(path.read_bytes())          # 올린 파일은 문서함으로 들어온다
        doc_id = db.add_document(filename=path.name, stored_path=str(dest), doc_type=doc_type, owner=owner)
        child = mp.Process(target=_run_one, args=(str(Path(args.db)), doc_id, str(dest)))
        child.start()
        return {"doc_id": doc_id, "proc": child, "t0": time.time(), "name": path.name}

    # 문서 하나가 배치를 멈추지 못하게 제한 시간을 둔다(실측: 같은 스캔 PDF 가 두 번 반입을 세웠다).
    # 여러 건을 동시에 돌린다 — 파싱은 CPU, 검토는 서빙 장비라 겹쳐 돌리면 전체가 빨라진다.
    pending = list(plan)
    running: list[dict] = []
    while pending or running:
        while pending and len(running) < max(1, args.jobs):
            running.append(start(*pending.pop(0)))
        time.sleep(2)
        for job in list(running):
            child = job["proc"]
            over = time.time() - job["t0"] > limit_s
            if child.is_alive() and not over:
                continue
            if child.is_alive():
                child.terminate()
                child.join(10)
                fail += 1
                db.update_document(job["doc_id"], status="failed",
                                   error=f"시간 초과 — {args.timeout}분 안에 끝나지 않았습니다")
            elif child.exitcode == 0:
                ok += 1
            else:
                fail += 1
                if (db.get_document(job["doc_id"]) or {}).get("status") != "failed":
                    db.update_document(job["doc_id"], status="failed", error="처리 중 중단되었습니다")
            running.remove(job)
            done = ok + fail
            if done % 5 == 0 or not (pending or running):
                spent = time.time() - t0
                print(f"  {done}/{len(plan)} · 성공 {ok} 실패 {fail} · {spent / 60:.1f}분"
                      f" · 남은 시간 {(len(plan) - done) * spent / max(done, 1) / 60:.0f}분", flush=True)

    print(f"반입 완료 — 성공 {ok} 실패 {fail} ({(time.time() - t0) / 60:.1f}분)")
    print("다음: 색인(scripts/96 --apply) · 개체 그래프 · 반입 점검")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
