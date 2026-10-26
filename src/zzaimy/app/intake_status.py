"""반입 현황 — 처리 흐름 「반입 점검」(/dev/flow#intake)·데이터 현황(/dev/db 원본 장부) 화면의 원천. 수치는 DB·장부·점검 파일에서 바로 읽는다(기계 산출물만).

무엇을 내는가
  연동 점검(scripts/175) 결과: 원본 장부 구성·처리 대상 상태·놓침·실패·동기화 기록(pipeline·listing)
  원본 장부 나눔: 학교 원본·정리함·부산물·플랫폼 사본(ledger_breakdown, 60초 재사용)
  DGX 처리기의 다시 하기 횟수(retry_limit)와 원본 보관소 분류 상태 이름(ARCHIVE_STATUS)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

TTL_S = 60

# 화면에 내부 코드 대신 쓰는 말
ARCHIVE_STATUS = {"auto": "규칙으로 확정", "agent": "에이전트 검토 판정", "period": "사업 기간으로 보정", "review": "검토 대기",
                  "folder": "폴더로 추론", "": "분류 없음"}


def _one(conn, sql: str, args=()) -> int:
    try:
        return int(conn.execute(sql, args).fetchone()[0] or 0)
    except Exception:
        return 0


def _sync_state(db_path: str) -> dict:
    p = Path(db_path).parent / "sync_status.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_PARSE_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "167_dgx_parse.py"


def retry_limit(script: Path = _PARSE_SCRIPT) -> int | None:
    """DGX 처리기(scripts/167)가 새 형식 변환 실패를 같은 판에서 다시 하는 횟수(ENV_RETRY_MAX) — 화면 안내가 처리기 규칙과
    어긋나지 않게 코드에서 읽는다. 스크립트를 실행하지 않고 상수만 읽는다. 못 읽으면 None."""
    import ast
    try:
        tree = ast.parse(script.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ENV_RETRY_MAX" for t in node.targets)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, int)):
            return node.value.value
    return None


def dgx_listing(sync: dict) -> int | None:
    """마지막 원본 동기화(scripts/170 의 full 기록 「원본 N · 새로 …」)가 본 DGX 원본 목록 수. 기록이 없으면 None."""
    import re
    m = re.match(r"\s*원본\s+([\d,]+)", str((sync.get("full") or {}).get("summary") or ""))
    return int(m.group(1).replace(",", "")) if m else None


def pipeline(db_path: str) -> dict | None:
    """반입 연동 점검(scripts/175) 결과 — 원본 구성·처리 대상 상태·놓침·실패·색인·그래프·경고. 없으면 None."""
    p = Path(db_path).parent / "pipeline_audit.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    t = max(int(data.get("targets") or 0), 1)
    data["pct"] = {k: round(100 * int(data.get(k) or 0) / t, 1) for k in ("in_docbox", "failed", "waiting", "missed")}
    data["sync"] = _sync_state(db_path)
    data["dgx_listing"] = dgx_listing(data["sync"])
    data["retry_limit"] = retry_limit()
    data["rows"] = composition_rows(data.get("composition"))
    data["school_rows_total"] = sum(r["n"] for r in data["rows"] if r["school"])
    data["sync_text"] = {k: with_commas((v or {}).get("summary", "")) for k, v in data["sync"].items() if isinstance(v, dict)}
    return data


_ledger_cache: dict = {"at": 0.0, "v": None, "db": ""}


def ledger_breakdown(db, force: bool = False) -> dict:
    """원본 장부 전체가 무엇으로 이루어졌는지 — 학교 원본(원본 보관소 머리 숫자) · 정리함 · 부산물 폴더 · 플랫폼 사본.
    「원본」이라는 말이 두 수(학교 원본, 원본 장부 전체)에 함께 쓰여 헷갈리지 않게 화면이 이 나눔을 한 줄로 보인다.
    학교 원본은 지표 모듈(archive_originals)과 같은 조건(archive_state.original_sql)으로 센다. 60초 재사용."""
    if (not force and _ledger_cache["v"] is not None and _ledger_cache["db"] == str(db.path)
            and time.time() - _ledger_cache["at"] < TTL_S):
        return _ledger_cache["v"]
    from contextlib import closing

    from zzaimy.app import archive_state
    from zzaimy.app.archive import UPLOAD_PREFIX
    from zzaimy.app.database_backend import connect
    orig, oa = archive_state.original_sql()
    cleaned = "EXISTS (SELECT 1 FROM archive_cleanup ac WHERE ac.rel = archive_files.rel)"
    up = UPLOAD_PREFIX.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
    with closing(connect(str(db.path), readonly=True)) as conn:
        total = _one(conn, "SELECT COUNT(*) FROM archive_files WHERE removed_at = ''")
        originals = _one(conn, f"SELECT COUNT(*) FROM archive_files WHERE removed_at = '' AND {orig}", oa)
        n_clean = _one(conn, f"SELECT COUNT(*) FROM archive_files WHERE removed_at = '' AND {cleaned}")
        platform = _one(conn, f"SELECT COUNT(*) FROM archive_files WHERE removed_at = '' AND NOT {cleaned} AND rel LIKE ? ESCAPE '!'", (up,))
    v = {"total": total, "originals": originals, "cleaned": n_clean, "platform": platform,
         "byproduct": max(total - originals - n_clean - platform, 0)}
    _ledger_cache.update(at=time.time(), v=v, db=str(db.path))
    return v


# 연동 점검(scripts/175)의 원본 장부 구성 줄 — 학교 원본(원본 보관소가 세는 것)에 드는 줄인지와 화면 이름
SCHOOL_ROWS = ("처리 대상 문서", "같은 파일 중복", "기타 비문서(회계 서식·동영상 등)", "사진·그림", "압축 파일",
               "범위 밖(지출·증빙·정산 등)")
ROW_LABELS = {"같은 파일 중복": "같은 파일 중복(정리함 밖, 문서에 이어진 사본 포함)"}


def composition_rows(comp: dict | None) -> list[dict]:
    """원본 장부 구성 → [{label, n, school}] (많은 순). school=학교 원본에 드는 줄."""
    return [{"label": ROW_LABELS.get(k, k), "n": int(v), "school": k in SCHOOL_ROWS}
            for k, v in sorted((comp or {}).items(), key=lambda kv: -int(kv[1]))]


def with_commas(text: str) -> str:
    """주기 작업 요약 글 속 네 자리 넘는 수에 천 단위 쉼표(「문서 38573 · 조각 1980742」 → 「문서 38,573 · 조각 1,980,742」)."""
    import re
    return re.sub(r"(?<![\d.,:-])\d{4,}(?![\d.:-]|년)", lambda m: f"{int(m.group(0)):,}", str(text or ""))


def listing(db_path: str, name: str) -> list[dict]:
    """놓친 원본(missed)·실패 원본(failed) 전체 목록 — 175 가 쓴 줄 파일."""
    p = Path(db_path).parent / f"pipeline_{name}.jsonl"
    out = []
    try:
        with open(p, encoding="utf-8", newline="") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out
