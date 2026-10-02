#!/usr/bin/env python3
"""163 이 VM 받은 편지함에 옮겨 둔 새 뼈대 문서를 반입하고 그래프·양식을 다시 짓는다 — VM 에서 떨어져 돈다(몇 시간 걸려도 운영 PC 와 무관).

작업 목록: data/inbox/core/sync_manifest.json = [{program, project, inbox, files: [상대 경로]}]
순서: 사업마다 156 반입(원본 경로 장부·내용 해시로 중복 건너뜀, 분석은 ZZAIMY_ROLE_CONN 로 놀고 있는 토르) → 157 --full → 162 → docx.
진행 기록: /tmp/sync_apply.log (끝나면 SYNC_DONE)

사용(VM, 163 이 띄운다): setsid nohup env PYTHONPATH=src .venv/bin/python scripts/164_sync_apply.py > /tmp/sync_apply.log 2>&1 &
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
    jobs = json.loads(man.read_text(encoding="utf-8")) if man.is_file() else []
    env_conn = os.environ.get("ZZAIMY_ROLE_CONN", "")
    for job in jobs:
        files = [f"{job['inbox']}/{r}" for r in job["files"] if (ROOT / job["inbox"] / r).is_file()]
        if not files:
            continue
        print(f"== {job['project']} — {len(files)}건", flush=True)
        sh([PY, "scripts/156_intake_files.py", "--project", job["project"], "--sector", "grant", "--owner", "zzdev",
            "--origin-base", job["inbox"], "--jobs", "3", "--timeout", "30", "--apply", *files])
    sys.path.insert(0, str(ROOT / "src"))
    from zzaimy.app.db import Database
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    with db._conn() as c:
        lo, hi = c.execute("select min(id), max(id) from documents where id >= 557").fetchone()
    sh([PY, "scripts/157_build_kg.py", "--docs", f"{lo}-{hi}", "--apply", "--full"])
    sh([PY, "scripts/162_business_template.py"])
    from zzaimy.ingest import md_docx
    for f in sorted((ROOT / "data" / "generated" / "templates").glob("*.md")):
        data, rep = md_docx.convert(f.read_text(encoding="utf-8"))
        f.with_suffix(".docx").write_bytes(data)
        print(f"양식 {f.name}: 절 {rep['headings']} · 표 {rep['tables']}", flush=True)
    man.rename(man.with_suffix(".done.json"))
    print("SYNC_DONE", env_conn and "(분석 서버 지정)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
