"""원본 처리 상태 — 원본 장부(archive_files)의 파일마다 등록됨 · 처리 제외 · 실패 · 대기.

반입 연동 점검(scripts/175)과 원본 보관소 화면이 같은 기준을 쓴다.
- 등록됨: 문서함 문서에 이어짐(doc_id)
- 처리 제외: 글 문서 형식이 아님 · 같은 파일(이름·크기·내용 지문이 같음, zzaimy/archive_dups)의 중복 · 범위 밖 경로(지출·증빙 등, 절대 규칙 11) · 처리 상한보다 큼
  · 같은 파일 묶음의 대표인데 묶음의 다른 사본이 문서에 이어짐(DGX 와 VM 이 대표를 달리 골라 처리된 쪽이 사본 — 연동 점검이
    「라이브러리 반영」으로 세는 것과 같은 판단. 이것을 대기로 두면 보관소 「대기」가 점검의 「대기」보다 부풀었다)
- 실패: DGX 처리·반입이 실패로 적힌 것(analysis 칸이 「처리 실패」로 시작 — scripts/168 이 적고, 지난 실패는 이 모듈로 채운다)
- 대기: 나머지(처리 대상인데 아직 결과가 없음)

지난 실패 채우기(VM): env PYTHONPATH=src .venv/bin/python -m zzaimy.app.archive_state --mark-failures
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from zzaimy.doc_formats import DOC_EXT  # noqa: E402,F401  처리 대상 문서 형식은 한 곳(zzaimy/doc_formats)

OUT_OF_SCOPE_WORDS = ("지출", "증빙", "스캔", "영수", "정산", "집행")      # 절대 규칙 11 — DGX 주기 처리와 같은 기준
OUT_OF_SCOPE = re.compile("|".join(OUT_OF_SCOPE_WORDS))
MAX_PARSE_BYTES = 300 * 1024 * 1024                                     # scripts/167 MAX_BYTES 와 같다
FAILED_MARK = "처리 실패"

# 학교 원본이 아닌 것 — 원본 보관소 목록·건수에서 뺀다
# 1. 플랫폼이 보관한 사본(archive.UPLOAD_PREFIX 아래: 반입·첨부 문서의 플랫폼 저장본). 원본은 학교 폴더에 있다
# 2. 부산물 폴더(「<문서 이름><접미>/」 아래) — 우리 변환기 출력과 웹 페이지 저장 자료 폴더. 접미 목록은 한 곳(zzaimy/doc_formats)
from zzaimy.doc_formats import BYPRODUCT_DIR_SUFFIXES, empty_reason, is_byproduct  # noqa: E402,F401


def original_sql(alias: str = "") -> tuple[str, list]:
    """학교 원본만 남기는 SQL 조건과 인자(SQLite·PostgreSQL 공통)."""
    from zzaimy.app.archive import UPLOAD_PREFIX

    def esc(t: str) -> str:
        return t.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    conds = [f"{alias}rel NOT LIKE ? ESCAPE '!'"] + [f"{alias}rel NOT LIKE ? ESCAPE '!'" for _ in BYPRODUCT_DIR_SUFFIXES]
    args = [esc(UPLOAD_PREFIX) + "%"] + [f"%{esc(sfx)}/%" for sfx in BYPRODUCT_DIR_SUFFIXES]
    # 정리함으로 옮긴 원본(사진·압축·범위 밖 등, archive_cleanup)은 보관소 목록·건수에서 뺀다 — 되돌리면 다시 보인다
    # NOT IN 하위 질의는 PostgreSQL 에서 정리함이 커지면 행마다 다시 훑는다 — 표 이름을 붙인 NOT EXISTS 로
    conds.append(f"NOT EXISTS (SELECT 1 FROM archive_cleanup ac WHERE ac.rel = {alias or 'archive_files.'}rel)")
    return "(" + " AND ".join(conds) + ")", args


def is_original(rel: str) -> bool:
    from zzaimy.app.archive import UPLOAD_PREFIX
    return not (rel or "").startswith(UPLOAD_PREFIX) and not is_byproduct(rel)


from zzaimy.app.vocab import ARCHIVE_STATE_LABELS as STATES  # noqa: E402  화면 용어는 한 곳(app/vocab)


def state_sql(alias: str = "") -> tuple[str, list]:
    """처리 상태를 내는 SQL 식과 인자 — SELECT·WHERE 에 쓴다(SQLite·PostgreSQL 공통). alias 는 표 별칭(「a.」).
    GROUP BY 에는 식을 다시 쓰지 말고 열 번호로 묶는다 — PostgreSQL 은 자리표시자가 다르면 다른 식으로 본다."""
    a = alias
    exts = ", ".join("?" * len(DOC_EXT))
    scope = " OR ".join(f"{a}rel LIKE ?" for _ in OUT_OF_SCOPE_WORDS)
    # 같은 파일 묶음의 다른 사본이 문서에 이어진 대표 — 묶음이 이미 처리됐다. 상관 없는 하위 질의(IN)라 한 번만 훑는다
    covered = (f"{a}rel IN (SELECT g.dup_of FROM archive_files g WHERE g.dup_of <> '' AND g.doc_id IS NOT NULL"
               f" AND g.removed_at = '')")
    expr = (f"(CASE WHEN {a}doc_id IS NOT NULL THEN 'registered'"
            f" WHEN {covered} OR {a}dup_of <> '' OR LOWER(REPLACE({a}ext, '.', '')) NOT IN ({exts}) OR {a}size > ? OR {scope} THEN 'excluded'"
            f" WHEN {a}analysis LIKE ? THEN 'failed' ELSE 'waiting' END)")
    return expr, [*DOC_EXT, MAX_PARSE_BYTES, *(f"%{w}%" for w in OUT_OF_SCOPE_WORDS), FAILED_MARK + "%"]


def exclusion_reason(row: dict) -> str:
    """처리 제외의 까닭 한 마디(화면 표시용). 아래 까닭이 다 아니면 같은 파일 묶음의 다른 사본이 처리된 대표다(state_sql)."""
    ext = (row.get("ext") or "").lower().lstrip(".")
    if ext not in DOC_EXT:
        return "문서 형식 아님"
    if row.get("dup_of"):
        return "같은 파일 중복"
    if int(row.get("size") or 0) > MAX_PARSE_BYTES:
        return "처리 상한 초과"
    if OUT_OF_SCOPE.search(row.get("rel") or ""):
        return "범위 밖 경로"
    return COVERED_REASON


COVERED_REASON = "같은 파일 사본이 처리됨"


def mark_failures(db, failures: dict[str, str]) -> int:
    """실패한 원본(rel → 사유)을 장부에 적는다 — 문서함에 이어진 것(나중에 성공)과 부산물 폴더(처리 대상 아님)는 건드리지 않는다.
    적은 수를 돌려준다."""
    from zzaimy.app import archive

    archive.ensure(db)
    n = 0
    with db._conn() as conn:
        for rel, why in failures.items():
            if is_byproduct(rel):
                continue
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
            why = r.get("error") or (empty_reason(r["rel"]) if r.get("state") == "empty" else r.get("state"))
            out[r["rel"]] = str(why or "")[:160]     # 오류 글 없이 빈 채로 끝난 옛 기록도 같은 사유 글(doc_formats.empty_reason)
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
