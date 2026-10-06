#!/usr/bin/env python3
"""DGX 에서 가볍게 처리한 문서(scripts/167 의 JSONL, 표준 입력)를 VM 문서함에 'DGX 보관 문서'로 들인다.

원본 파일은 옮기지 않는다 — stored_path 는 dgx://<원본 경로>. 글·조각·분류를 문서함에 넣어 RAG 색인·그래프·사업 분류가 쓴다.
사업마다 보관 묶음(보관 프로젝트, 사업 id 로 찾음)에 넣고, 원본 목록 장부(archive_files)와 원본 경로 장부(origins.jsonl)에 문서 번호를 잇는다.
이미 들인 원본(같은 rel)은 건너뛴다.

사용(운영 PC): ssh dgx 'cat ~/parsed/parsed-*.jsonl' | ssh vm 'cd ~/zzaimy-capstone && … 168_import_parsed.py'
      (VM): … 168_import_parsed.py data/inbox/parsed/parsed-*.jsonl     # 170 이 읽기 전용 키로 받아 둔 것
"""
from __future__ import annotations

import json
import re
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import archive  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402


def main() -> int:
    import fcntl
    # 두 번 동시에 돌면 같은 원본을 두 번 들인다 — 직접 실행해도 한 번에 하나만
    _lock = open("/tmp/zz_parsed_import168.lock", "w")
    try:
        fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("들임 0 · 새 판 갱신 0 · 이미 있음 0 · 처리 실패·빈 문서 0 · 다른 들이기가 도는 중")
        return 0
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    archive.ensure(db)
    led_path = ROOT / "data" / "platform" / "origins.jsonl"
    # 원본 경로 → (문서 번호, 판). 판(크기:수정 시각)이 다른 새 기록이 오면 같은 문서 번호로 갱신한다(C-183).
    # 판을 적지 않은 옛 장부 줄은 판을 모르므로 건너뛴다 — 바뀐 원본은 170 의 원본 장부 대조(changed)가 따로 다시 처리한다
    have: dict[str, tuple[int, str]] = {}
    if led_path.is_file():
        for line in led_path.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
                have[o["origin"]] = (int(o["doc_id"]), o.get("version") or "")
            except (ValueError, KeyError, TypeError):
                continue
    # DB 가 기준 — 장부 줄이 없더라도(문서를 만든 뒤 장부를 쓰기 전에 멈춘 경우) 같은 경로의 문서가 있으면 그 번호를 다시 쓴다(C-186)
    by_path: dict[str, int] = {}
    with db._conn() as conn:
        for sp, did0 in conn.execute("SELECT stored_path, id FROM documents WHERE stored_path LIKE 'dgx://%' ORDER BY id DESC").fetchall():
            by_path[str(sp)[len("dgx://"):]] = int(did0)
        # 경로마다 문서 하나 — DB 가 막는다(동시 들이기 사고 2026-10-02). 중복이 남아 있으면 만들지 못하고 알린다
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_dgx_path ON documents (stored_path) WHERE stored_path LIKE 'dgx://%'")
        except Exception as e:
            print("DGX 경로 고유 색인을 만들지 못함(중복 남음):", type(e).__name__, flush=True)
    projects: dict[str, int] = {}                     # 사업 id → 보관 묶음 번호

    n_ok = n_upd = n_skip = n_fail = n_link_later = n_stale = 0
    led = led_path.open("a", encoding="utf-8")
    fails = (ROOT / "data" / "platform" / "parse_failures.jsonl").open("a", encoding="utf-8")
    # 파일마다 읽은 자리를 기억한다(DGX 결과 파일은 덧붙기만 한다) — 5분 주기가 매번 처음부터 읽지 않게
    off_path = ROOT / "data" / "inbox" / "parsed" / ".offsets.json"
    offsets = json.loads(off_path.read_text(encoding="utf-8")) if off_path.is_file() else {}

    def save_offsets():
        if len(sys.argv) > 1 and only is None:
            if not led.closed:
                led.flush()                                # 장부가 먼저 — 읽은 자리만 앞서 저장되면 들인 기록을 잃는다
            off_path.parent.mkdir(parents=True, exist_ok=True)
            off_path.write_text(json.dumps(offsets), encoding="utf-8")

    # --rels <목록.json>: 그 원본들만 결과 파일 처음부터 다시 훑어 들인다(읽은 자리 무시·저장 안 함) — DGX 에 정상 기록이 있는데 문서함에
    # 없는 원본(연동 점검 175 --find-reimport)을 메운다. 10/6: 1,133건이 그랬다
    only: set[str] | None = None
    if len(sys.argv) > 2 and sys.argv[1] == "--rels":
        only = set(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
        del sys.argv[1:3]

    def lines():
        if len(sys.argv) <= 1:
            yield from sys.stdin
            return
        if only is not None:
            for f in sys.argv[1:]:
                with open(f, encoding="utf-8", newline="") as fh:
                    for raw in fh:
                        m = re.search(r'"rel": "((?:[^"\\]|\\.)*)"', raw[:2000])
                        if m and json.loads(f'"{m.group(1)}"') in only:
                            yield raw
            return
        for f in sys.argv[1:]:
            with open(f, "rb") as fh:
                pos = int(offsets.get(f, 0))
                if pos > Path(f).stat().st_size:      # 파일이 새로 시작됐다
                    pos = 0
                fh.seek(pos)
                for raw in fh:
                    if not raw.endswith(b"\n"):       # 아직 쓰는 중인 마지막 줄 — 다음 회차에
                        break
                    pos += len(raw)
                    yield raw.decode("utf-8", "replace")
                    offsets[f] = pos
    for line in lines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        rel = rec.get("rel")
        if not rel:
            continue
        ver = rec.get("version") or f"{int(rec.get('size') or 0)}:{int(float(rec.get('mtime') or 0))}"
        old = have.get(rel)
        if old and (not old[1] or old[1] == ver) and not rec.get("force"):
            n_skip += 1
            continue
        state = rec.get("state") or ("parsed" if rec.get("ok") else "failed")
        if state not in ("parsed", "partial") or not rec.get("chunks"):
            n_fail += 1
            # 실패도 남긴다 — 연동 점검(175)이 「결과 없음(놓침)」과 「실패(사유 있음)」를 가른다
            fails.write(json.dumps({"rel": rel, "version": ver, "state": state, "error": str(rec.get("error") or "")[:200],
                                    "at": time.strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False) + "\n")
            continue
        # 원본 장부의 현재 판과 같은 기록만 적용 — 여러 결과 파일에 옛 판이 뒤늦게 읽혀도 되돌리지 않는다(C-186).
        # 장부가 아직 새 판을 모르면(목록 갱신 전) 여기서는 건너뛰고, 바뀐 원본은 170 의 원본 장부 대조(changed)가 다시 처리한다
        with db._conn() as conn:
            row = conn.execute("SELECT program_name, size, mtime, program, year, round FROM archive_files WHERE rel = ?", (rel,)).fetchone()
        if row is not None and row[1] is not None and f"{int(row[1] or 0)}:{int(float(row[2] or 0))}" != ver:
            n_stale += 1
            continue
        # 처리 상태를 문서 기록에 남긴다 — 가벼운 처리 완료 ≠ OCR 품질 통과 ≠ 학습 승인(C-183)
        note = (rec.get("parse_note") or "") + " · DGX 보관(가벼운 처리: 검토 의견 없음, OCR 품질 미검사)"
        if state == "partial":
            note += " · 일부만 읽음"
        if rec.get("truncated"):
            note += f" · 본문 잘림(원래 {int(rec.get('text_len') or 0):,}자, 조각은 전부)"
        existing = old[0] if old else by_path.get(rel)
        if existing:
            did = existing                                 # 같은 원본 — 같은 문서 번호로 갱신(새 판, 또는 지난 회차가 중간에 멈춘 것)
            n_upd += 1
        else:
            # 사업 분류는 원본 장부의 현재 값(검토 판정 반영) — 기록의 값은 DGX 목록을 만들 때의 옛 분류일 수 있다
            prog_name = row[0] if row is not None else rec.get("program_name")
            prog_id = row[3] if row is not None and len(row) > 3 else rec.get("program")
            did = db.add_document(filename=rec.get("filename") or Path(rel).name, stored_path=f"dgx://{rel}", doc_type="grant",
                                  sector="grant", project_id=archive.program_project(
                                      db, prog_id or "", prog_name or "", projects,
                                      row[4] if row is not None else rec.get("year"), row[5] if row is not None else rec.get("round")),
                                  owner="zzdev")
            by_path[rel] = did
            n_ok += 1
        try:
            db.replace_doc_chunks(did, [c for c in rec["chunks"] if c.get("content") is not None])
            db.update_document(did, status="reviewed", masked_text=rec.get("masked_text") or "", parse_note=note)
        except Exception as e:
            # 한 문서의 오류로 들이기 전체가 멈추지 않게 — 그 문서만 실패 장부에 남기고 다음으로(멈추면 같은 자리에서 매번 다시 멈춰
            # 그 결과 파일의 뒤 문서들이 들어오지 못했다, 10/6)
            n_fail += 1
            fails.write(json.dumps({"rel": rel, "version": ver, "state": "import_error", "error": f"{type(e).__name__}: {e}"[:200],
                                    "at": time.strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False) + "\n")
            continue
        if rec.get("doc_kind"):
            db.set_document_kind(did, rec["doc_kind"])
        led.write(json.dumps({"doc_id": did, "origin": rel, "version": ver, "state": state, "at": time.strftime("%Y-%m-%d %H:%M"),
                              "via": "dgx-parse"}, ensure_ascii=False) + "\n")
        try:
            with db._conn() as conn:
                conn.execute("UPDATE archive_files SET doc_id = ?, analysis = ? WHERE rel = ?", (did, "가벼운 처리", rel))
        except Exception as e:  # 원본 장부를 동기화(170)가 크게 고치는 중이면 잠금 시간 초과 — 연결은 다음 동기화가 원본 경로 장부로 맞춘다
            n_link_later += 1
            if n_link_later == 1:
                print("원본 장부 연결은 다음 동기화로 미룸:", type(e).__name__, flush=True)
        if (n_ok + n_upd) % 200 == 0:
            save_offsets()                                 # 중간에 멈춰도 읽은 자리부터 이어서
        have[rel] = (did, ver)
    led.close()
    save_offsets()
    print(f"들임 {n_ok} · 새 판 갱신 {n_upd} · 이미 있음 {n_skip} · 처리 실패·빈 문서 {n_fail}"
          + (f" · 장부 판과 다름(건너뜀) {n_stale}" if n_stale else "")
          + (f" · 원본 장부 연결 미룸 {n_link_later}" if n_link_later else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
