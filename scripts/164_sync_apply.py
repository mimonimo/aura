#!/usr/bin/env python3
"""170(VM 동기화)이 받은 편지함에 받아 둔 새 뼈대 문서를 반입하고 그래프·양식을 다시 짓는다.

작업 목록: data/inbox/core/sync_manifest.json = [{program, project, inbox, files: [상대 경로]}]
순서: 사업마다 156 반입(원본 경로 장부·내용 해시로 중복 건너뜀, 분석은 ZZAIMY_ROLE_CONN 로 놀고 있는 토르) → 157 --full → 162(사업별)·172(갈래별 공통) → docx.
진행 기록: /tmp/sync_apply.log (끝나면 SYNC_DONE)

사용(VM): scripts/170 이 부른다. 그래프·양식만 다시: PYTHONPATH=src .venv/bin/python scripts/164_sync_apply.py --post-only
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")


def sh(args: list[str]) -> int:
    print("$", " ".join(args[:6]), "…", flush=True)
    return subprocess.call(args, cwd=ROOT)


def main() -> int:
    man = ROOT / "data" / "inbox" / "core" / "sync_manifest.json"
    post_only = "--post-only" in sys.argv
    jobs = [] if post_only else (json.loads(man.read_text(encoding="utf-8")) if man.is_file() else [])
    env_conn = os.environ.get("ZZAIMY_ROLE_CONN", "")
    sys.path.insert(0, str(ROOT / "src"))
    def _intake_lines() -> int:
        """156 이 원본 경로 장부에 적은 줄 수(via 없음) — DGX 처리분 들이기(168, via dgx-parse)와 섞이지 않게 센다."""
        led = ROOT / "data" / "platform" / "origins.jsonl"
        if not led.is_file():
            return 0
        n = 0
        for line in led.read_text(encoding="utf-8").splitlines():
            try:
                if "via" not in json.loads(line):
                    n += 1
            except ValueError:
                continue
        return n

    before = _intake_lines() if jobs else 0
    for job in jobs:
        # 바뀐 원본 — 같은 문서 번호로 다시 처리. 문서함에 원본이 있으면 새 원본으로 바꾸고, DGX 보관 문서(dgx://)는 가볍게 처리
        for u in job.get("updates", []):
            src = ROOT / job["inbox"] / u["rel"]
            if not src.is_file():
                continue
            from zzaimy.app.db import Database as _DB
            from zzaimy.app.pipeline import DocumentProcessor
            _db = _DB(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
            d = _db.get_document(int(u["doc_id"])) or {}
            stored = d.get("stored_path") or ""
            if stored.startswith("dgx://"):
                os.environ["ZZAIMY_LIGHT_PROCESS"] = "1"
                DocumentProcessor().process(_db, int(u["doc_id"]), src)
                _db.update_document(int(u["doc_id"]), stored_path=stored)
                os.environ.pop("ZZAIMY_LIGHT_PROCESS", None)
            elif stored:
                import shutil
                shutil.copyfile(src, stored)
                DocumentProcessor().process(_db, int(u["doc_id"]), Path(stored))
            print(f"다시 처리 #{u['doc_id']} {u['rel'][-50:]}", flush=True)
        files = [f"{job['inbox']}/{r}" for r in job["files"] if (ROOT / job["inbox"] / r).is_file()]
        if not files:
            continue
        print(f"== {job['project']} — {len(files)}건", flush=True)
        # 과거 사업 묶음은 보관 상태로, 사업 id 로 찾는다(같은 사업이 이름만 달리 갈라지지 않게)
        sh([PY, "scripts/156_intake_files.py", "--project", job["project"], "--program", job["program"], "--archived",
            "--sector", "grant", "--owner", "zzdev",
            "--origin-base", job["inbox"], "--jobs", "3", "--timeout", "30", "--apply", *files])
    sys.path.insert(0, str(ROOT / "src"))
    # 이번 회차에 실제로 들어오거나 다시 처리한 문서가 없으면 그래프·양식을 다시 짓지 않는다(같은 결과를 회차마다 다시 짓던 것)
    if not post_only and jobs:
        n_upd = sum(len(j.get("updates", [])) for j in jobs)
        if _intake_lines() <= before and not n_upd:
            print("새로 들어온·다시 처리한 문서 없음 — 그래프·양식 다시 짓기 건너뜀", flush=True)
            if man.is_file():
                man.rename(man.with_suffix(".done.json"))
            print("SYNC_DONE", flush=True)
            return 0
    import fcntl
    _post = open("/tmp/zz_post.lock", "w")
    fcntl.flock(_post, fcntl.LOCK_EX)                     # 1분 주기 후속 처리와 겹치지 않게(기다렸다가)
    from zzaimy.app.db import Database
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    with db._conn() as c:
        lo, hi = c.execute("select min(id), max(id) from documents where id >= 557").fetchone()
    sh([PY, "scripts/157_build_kg.py", "--docs", f"{lo}-{hi}", "--apply", "--full"])
    sh([PY, "scripts/162_business_template.py"])
    sh([PY, "scripts/172_common_templates.py"])          # 문서 갈래별 공통 양식(사업 공통) — 드라이브 「ZZAIMY/공통 양식」
    from zzaimy.ingest import md_docx
    tpl = ROOT / "data" / "generated" / "templates"
    for f in sorted([*tpl.glob("*.md"), *(tpl / "common").glob("*.md")]):
        data, rep = md_docx.convert(f.read_text(encoding="utf-8"))
        f.with_suffix(".docx").write_bytes(data)
        print(f"양식 {f.name}: 절 {rep['headings']} · 표 {rep['tables']}", flush=True)
    if man.is_file() and not post_only:
        man.rename(man.with_suffix(".done.json"))
    print("SYNC_DONE", env_conn and "(분석 서버 지정)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
