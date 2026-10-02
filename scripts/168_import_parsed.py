#!/usr/bin/env python3
"""DGX 에서 가볍게 처리한 문서(scripts/167 의 JSONL, 표준 입력)를 VM 문서함에 'DGX 보관 문서'로 들인다.

원본 파일은 옮기지 않는다 — stored_path 는 dgx://<원본 경로>. 글·조각·분류를 문서함에 넣어 RAG 색인·그래프·사업 분류가 쓴다.
사업마다 프로젝트(「사업명 (DGX 보관)」)로 묶고, 원본 목록 장부(archive_files)와 원본 경로 장부(origins.jsonl)에 문서 번호를 잇는다.
이미 들인 원본(같은 rel)은 건너뛴다.

사용(운영 PC): ssh dgx 'cat ~/parsed/parsed-*.jsonl' | ssh vm 'cd ~/zzaimy-capstone && … 168_import_parsed.py'
      (VM): … 168_import_parsed.py data/inbox/parsed/parsed-*.jsonl     # 170 이 읽기 전용 키로 받아 둔 것
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import archive  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402


def main() -> int:
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
    projects: dict[str, int] = {}

    def project_for(name: str) -> int:
        label = f"{(name or '사업 미분류')[:40]} (DGX 보관)"
        if label not in projects:
            proj = next((p for p in db.list_projects("grant") if p["name"] == label), None)
            projects[label] = int(proj["id"]) if proj else db.create_project("grant", label, owner="zzdev")
        return projects[label]
    n_ok = n_upd = n_skip = n_fail = n_link_later = 0
    led = led_path.open("a", encoding="utf-8")
    # 파일마다 읽은 자리를 기억한다(DGX 결과 파일은 덧붙기만 한다) — 5분 주기가 매번 처음부터 읽지 않게
    off_path = ROOT / "data" / "inbox" / "parsed" / ".offsets.json"
    offsets = json.loads(off_path.read_text(encoding="utf-8")) if off_path.is_file() else {}

    def save_offsets():
        if len(sys.argv) > 1:
            if not led.closed:
                led.flush()                                # 장부가 먼저 — 읽은 자리만 앞서 저장되면 들인 기록을 잃는다
            off_path.parent.mkdir(parents=True, exist_ok=True)
            off_path.write_text(json.dumps(offsets), encoding="utf-8")

    def lines():
        if len(sys.argv) <= 1:
            yield from sys.stdin
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
        if old and (not old[1] or old[1] == ver):
            n_skip += 1
            continue
        state = rec.get("state") or ("parsed" if rec.get("ok") else "failed")
        if state not in ("parsed", "partial") or not rec.get("chunks"):
            n_fail += 1
            continue
        # 처리 상태를 문서 기록에 남긴다 — 가벼운 처리 완료 ≠ OCR 품질 통과 ≠ 학습 승인(C-183)
        note = (rec.get("parse_note") or "") + " · DGX 보관(가벼운 처리: 검토 의견 없음, OCR 품질 미검사)"
        if state == "partial":
            note += " · 일부만 읽음"
        if rec.get("truncated"):
            note += f" · 본문 잘림(원래 {int(rec.get('text_len') or 0):,}자, 조각은 전부)"
        if old:
            did = old[0]                                   # 같은 원본의 새 판 — 같은 문서 번호로 갱신
            n_upd += 1
        else:
            did = db.add_document(filename=rec.get("filename") or Path(rel).name, stored_path=f"dgx://{rel}", doc_type="grant",
                                  sector="grant", project_id=project_for(rec.get("program_name") or ""), owner="zzdev")
            n_ok += 1
        db.replace_doc_chunks(did, [c for c in rec["chunks"] if c.get("content") is not None])
        db.update_document(did, status="reviewed", masked_text=rec.get("masked_text") or "", parse_note=note)
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
          + (f" · 원본 장부 연결 미룸 {n_link_later}" if n_link_later else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
