"""원본 처리 상태 — 원본 장부(archive_files)의 파일마다 등록됨 · 처리 제외 · 실패 · 대기.

반입 연동 점검(scripts/175)과 원본 보관소 화면이 같은 기준을 쓴다.
- 등록됨: 문서함 문서에 이어짐(doc_id)
- 처리 제외: 글 문서 형식이 아님 · 이름·크기가 같은 파일의 중복 · 범위 밖 경로(지출·증빙 등, 절대 규칙 11) · 처리 상한보다 큼
- 실패: DGX 처리·반입이 실패로 적힌 것(analysis 칸이 「처리 실패」로 시작 — scripts/168 이 적고, 지난 실패는 이 모듈로 채운다)
- 대기: 나머지(처리 대상인데 아직 결과가 없음)

지난 실패 채우기(VM): env PYTHONPATH=src .venv/bin/python -m zzaimy.app.archive_state --mark-failures
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DOC_EXT = ("hwp", "hwpx", "pdf", "docx", "xlsx", "pptx", "xls", "doc", "ppt")
OUT_OF_SCOPE_WORDS = ("지출", "증빙", "스캔", "영수", "정산", "집행")      # 절대 규칙 11 — DGX 주기 처리와 같은 기준
OUT_OF_SCOPE = re.compile("|".join(OUT_OF_SCOPE_WORDS))
MAX_PARSE_BYTES = 300 * 1024 * 1024                                     # scripts/167 MAX_BYTES 와 같다
FAILED_MARK = "처리 실패"

STATES = {"registered": "등록됨", "excluded": "처리 제외(범위 밖)", "failed": "실패", "waiting": "대기"}


def state_sql(alias: str = "") -> tuple[str, list]:
    """처리 상태를 내는 SQL 식과 인자 — SELECT·WHERE 에 쓴다(SQLite·PostgreSQL 공통). alias 는 표 별칭(「a.」).
    GROUP BY 에는 식을 다시 쓰지 말고 열 번호로 묶는다 — PostgreSQL 은 자리표시자가 다르면 다른 식으로 본다."""
    a = alias
    exts = ", ".join("?" * len(DOC_EXT))
    scope = " OR ".join(f"{a}rel LIKE ?" for _ in OUT_OF_SCOPE_WORDS)
    expr = (f"(CASE WHEN {a}doc_id IS NOT NULL THEN 'registered'"
            f" WHEN {a}dup_of <> '' OR LOWER(REPLACE({a}ext, '.', '')) NOT IN ({exts}) OR {a}size > ? OR {scope} THEN 'excluded'"
            f" WHEN {a}analysis LIKE ? THEN 'failed' ELSE 'waiting' END)")
    return expr, [*DOC_EXT, MAX_PARSE_BYTES, *(f"%{w}%" for w in OUT_OF_SCOPE_WORDS), FAILED_MARK + "%"]


def exclusion_reason(row: dict) -> str:
    """처리 제외의 까닭 한 마디(화면 표시용)."""
    ext = (row.get("ext") or "").lower().lstrip(".")
    if ext not in DOC_EXT:
        return "문서 형식 아님"
    if row.get("dup_of"):
        return "같은 파일 중복"
    if int(row.get("size") or 0) > MAX_PARSE_BYTES:
        return "처리 상한 초과"
    if OUT_OF_SCOPE.search(row.get("rel") or ""):
        return "범위 밖 경로"
    return ""


def mark_failures(db, failures: dict[str, str]) -> int:
    """실패한 원본(rel → 사유)을 장부에 적는다 — 문서함에 이어진 것(나중에 성공)은 건드리지 않는다. 적은 수를 돌려준다."""
    from zzaimy.app import archive

    archive.ensure(db)
    n = 0
    with db._conn() as conn:
        for rel, why in failures.items():
            cur = conn.execute("UPDATE archive_files SET analysis = ? WHERE rel = ? AND doc_id IS NULL",
                               (f"{FAILED_MARK}: {why}"[:200], rel))
            n += max(int(getattr(cur, "rowcount", 0) or 0), 0)
    return n


def read_failures(path: Path) -> dict[str, str]:
    """실패 장부(parse_failures.jsonl) → {rel: 사유}. 같은 rel 은 마지막 줄."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").split("\n"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("rel"):
            out[r["rel"]] = str(r.get("error") or r.get("state") or "")[:160]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="원본 장부에 지난 처리 실패를 적는다")
    ap.add_argument("--mark-failures", action="store_true")
    args = ap.parse_args()
    if not args.mark_failures:
        ap.print_help()
        return 1
    import os

    from zzaimy.app import paths
    from zzaimy.app.db import Database

    plat = paths.platform_dir()
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or plat / "platform.db"))
    fails = read_failures(plat / "parse_failures.jsonl")
    print(f"실패 장부 {len(fails)}건 중 장부에 적음 {mark_failures(db, fails)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
