#!/usr/bin/env python3
"""DGX 에서 가볍게 처리한 문서(scripts/167 의 JSONL, 표준 입력)를 VM 문서함에 'DGX 보관 문서'로 들인다.

원본 파일은 옮기지 않는다 — stored_path 는 dgx://<원본 경로>. 글·조각·분류를 문서함에 넣어 RAG 색인·그래프·사업 분류가 쓴다.
사업마다 프로젝트(「사업명 (DGX 보관)」)로 묶고, 원본 목록 장부(archive_files)와 원본 경로 장부(origins.jsonl)에 문서 번호를 잇는다.
이미 들인 원본(같은 rel)은 건너뛴다.

사용(운영 PC): ssh dgx 'cat ~/parsed/parsed-*.jsonl' | ssh vm 'cd ~/zzaimy-capstone && … 168_import_parsed.py'
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
    have = set()
    if led_path.is_file():
        for line in led_path.read_text(encoding="utf-8").splitlines():
            try:
                have.add(json.loads(line)["origin"])
            except (ValueError, KeyError):
                continue
    projects: dict[str, int] = {}

    def project_for(name: str) -> int:
        label = f"{(name or '사업 미분류')[:40]} (DGX 보관)"
        if label not in projects:
            proj = next((p for p in db.list_projects("grant") if p["name"] == label), None)
            projects[label] = int(proj["id"]) if proj else db.create_project("grant", label, owner="zzdev")
        return projects[label]
    n_ok = n_skip = n_fail = 0
    led = led_path.open("a", encoding="utf-8")
    for line in sys.stdin:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        rel = rec.get("rel")
        if not rel or rel in have:
            n_skip += 1
            continue
        if not rec.get("ok") or not rec.get("chunks"):
            n_fail += 1
            continue
        did = db.add_document(filename=rec.get("filename") or Path(rel).name, stored_path=f"dgx://{rel}", doc_type="grant",
                              sector="grant", project_id=project_for(rec.get("program_name") or ""), owner="zzdev")
        db.replace_doc_chunks(did, [c for c in rec["chunks"] if c.get("content") is not None])
        db.update_document(did, status="reviewed", masked_text=rec.get("masked_text") or "", parse_note=(rec.get("parse_note") or "") +
                           " · DGX 보관(가벼운 처리: 검토 의견 없음)", kind=rec.get("doc_kind") or rec.get("kind") or None)
        led.write(json.dumps({"doc_id": did, "origin": rel, "at": time.strftime("%Y-%m-%d %H:%M"), "via": "dgx-parse"}, ensure_ascii=False) + "\n")
        with db._conn() as conn:
            conn.execute("UPDATE archive_files SET doc_id = ?, analysis = ? WHERE rel = ?", (did, "가벼운 처리", rel))
        have.add(rel)
        n_ok += 1
    led.close()
    print(f"들임 {n_ok} · 이미 있음 {n_skip} · 처리 실패·빈 문서 {n_fail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
