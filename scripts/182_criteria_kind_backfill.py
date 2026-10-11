"""공고 갈래 기준 문서 소급 연결 — 기존 프로젝트의 공고·지침·평가 기준·규정·기본계획 갈래 문서를 프로젝트 기준 문서로 잇는다.

새로 들어오는 문서는 반입 뒤 분류에서 바로 이어진다(project_documents.link_criteria_kind). 그 규칙이 생기기 전에
프로젝트에 올렸거나 프로젝트 대화에 첨부한 문서는 첨부 문서로만 남아 있어, 같은 함수로 한 번 잇는다.
대상은 담당자가 만든 진행 중 프로젝트(보관 상태도, 원본 보관소 자동 묶음 archive_source 도 아닌 것)에 담당자가 직접
올렸거나 그 대화에 첨부한 문서뿐이다(project_documents.auto_criteria_project·auto_criteria_doc). 원본 보관소에서 옮겨 온
문서(dgx://)는 잇지 않는다. 프로젝트 소유자가 열람할 수 없는 문서도 잇지 않는다. 연결을 푼 기록은 남지 않으므로, 담당자가 일부러 푼 연결도
다시 이어질 수 있다 — 미리보기 목록을 보고 필요하면 --project 로 범위를 좁힌다.

실행: env PYTHONPATH=src .venv/bin/python scripts/182_criteria_kind_backfill.py              # 미리보기
      env PYTHONPATH=src .venv/bin/python scripts/182_criteria_kind_backfill.py --apply      # 적용
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import project_documents  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.doc_routing import KINDS  # noqa: E402


def load_accounts(path: Path) -> dict[str, dict]:
    """계정별 역할·부서만 읽는다(비밀번호 해시는 버린다). 파일이 없으면 빈 값 — 모두 담당자로 본다."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {u: {"role": a.get("role", "staff"), "dept": a.get("dept", "")} for u, a in raw.items() if isinstance(a, dict)}


def run(db: Database, accounts: dict[str, dict], apply: bool = False,
        project_ids: set[int] | None = None, show: int = 60) -> dict:
    plan = project_documents.criteria_kind_backlog(db, accounts, project_ids)
    added: list[tuple[int, int]] = []
    for it in plan["link"]:
        if apply:
            if project_documents.link_criteria_kind(db, it["doc_id"], only={it["project_id"]}):
                added.append((it["project_id"], it["doc_id"]))
    rows = plan["link"]
    print(f"{'적용' if apply else '미리보기'} · 이을 연결 {len(rows):,} · 프로젝트 {len({r['project_id'] for r in rows}):,}"
          f" · 열람 범위 밖이라 건너뜀 {len(plan['skipped']):,}")
    for r in rows[:show]:
        p, d = db.get_project(r["project_id"]) or {}, db.get_document(r["doc_id"]) or {}
        kind = KINDS.get(d.get("kind") or "", d.get("kind") or "")
        print(f"  프로젝트 {r['project_id']} 「{p.get('name', '')}」 ← 문서 {r['doc_id']} 「{d.get('title') or d.get('filename', '')}」"
              f" ({kind}, {r['via']})")
    if len(rows) > show:
        print(f"  … 외 {len(rows) - show:,}")
    if apply:
        print(f"연결 추가 {len(added):,} · 프로젝트 {len({a[0] for a in added}):,}")
    return {"planned": len(rows), "added": len(added), "projects": len({a[0] for a in added}),
            "skipped": len(plan["skipped"])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="연결을 실제로 쓴다(없으면 미리보기)")
    ap.add_argument("--project", type=int, action="append", help="이 프로젝트만(여러 번 줄 수 있다)")
    ap.add_argument("--show", type=int, default=60, help="미리보기에 보일 줄 수")
    args = ap.parse_args()
    db_path = Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db")
    db = Database(db_path)
    run(db, load_accounts(db_path.parent / "accounts.json"), apply=args.apply,
        project_ids=set(args.project) if args.project else None, show=args.show)


if __name__ == "__main__":
    main()
