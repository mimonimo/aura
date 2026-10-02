"""반입 현황 — 개발 현황(/dev) 카드와 /dev/api/intake 의 원천. 수치는 DB·장부·색인 파일에서 바로 읽는다(기계 산출물만).

무엇을 내는가
  원본 보관소(DGX): 원본 수, 중복 제외, 분류 상태(규칙·에이전트 판정·미확정), 문서함 연결 수, 최상위 폴더별
  문서함: 전체, DGX 보관(가벼운 처리)·원본 반입·업로드, 처리 상태, 판독 경로(글자층·MinerU·비전), 잘림·일부·OCR 미검사 표시
  사업별: DGX 보관 프로젝트 상위
  색인: 사업 문서 색인의 들어간 조각·남은 조각
  그래프: 노드 갈래별 수(사업·연차·문서·절·단위·성과지표)
  동기화: 주기마다 마지막 실행 시각과 결과(scripts/170 이 data/platform/sync_status.json 에 적는다)
조회가 무거운 표(원본 16만 줄·조각 80만 줄)를 매번 세지 않도록 60초 동안 결과를 재사용한다.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_cache: dict = {"at": 0.0, "data": None}
_lock = threading.Lock()
TTL_S = 60

NOTE_KINDS = (("글자층 직독", "글자층 직독"), ("MinerU", "MinerU 구조·OCR"), ("AI 비전 판독", "Writer 비전 판독"),
              ("한글", "한글 변환"))


def _one(conn, sql: str, args=()) -> int:
    try:
        return int(conn.execute(sql, args).fetchone()[0] or 0)
    except Exception:
        return 0


def _rows(conn, sql: str, args=()) -> list[tuple]:
    try:
        return [tuple(r) for r in conn.execute(sql, args).fetchall()]
    except Exception:
        return []


def _index_state(db_path: str) -> dict:
    from zzaimy.app import grant_search
    meta = grant_search.INDEX.with_suffix(".json")
    try:
        got = json.loads(meta.read_text(encoding="utf-8"))
        return {"chunks": int(got.get("n_chunks") or 0), "updated": time.strftime("%m-%d %H:%M", time.localtime(meta.stat().st_mtime))}
    except (OSError, ValueError):
        return {"chunks": 0, "updated": ""}


def _sync_state(db_path: str) -> dict:
    p = Path(db_path).parent / "sync_status.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def snapshot(db, force: bool = False) -> dict:
    with _lock:
        if not force and _cache["data"] is not None and time.time() - _cache["at"] < TTL_S:
            return _cache["data"]
    from zzaimy.app import archive
    try:
        archive.ensure(db)
    except Exception:
        pass
    with db._conn() as conn:
        a_total = _one(conn, "SELECT COUNT(*) FROM archive_files WHERE removed_at = ''")
        a_unique = _one(conn, "SELECT COUNT(*) FROM archive_files WHERE removed_at = '' AND dup_of = ''")
        a_linked = _one(conn, "SELECT COUNT(*) FROM archive_files WHERE removed_at = '' AND doc_id IS NOT NULL")
        a_removed = _one(conn, "SELECT COUNT(*) FROM archive_files WHERE removed_at <> ''")
        a_status = _rows(conn, "SELECT status, COUNT(*) FROM archive_files WHERE removed_at = '' AND dup_of = '' GROUP BY status ORDER BY 2 DESC")
        a_area = _rows(conn, "SELECT area, COUNT(*), SUM(CASE WHEN doc_id IS NOT NULL THEN 1 ELSE 0 END) FROM archive_files"
                             " WHERE removed_at = '' GROUP BY area ORDER BY 2 DESC")
        d_total = _one(conn, "SELECT COUNT(*) FROM documents")
        d_dgx = _one(conn, "SELECT COUNT(*) FROM documents WHERE stored_path LIKE 'dgx://%'")
        d_status = _rows(conn, "SELECT status, COUNT(*) FROM documents GROUP BY status ORDER BY 2 DESC")
        notes = {label: _one(conn, "SELECT COUNT(*) FROM documents WHERE parse_note LIKE ?", (f"%{key}%",)) for key, label in NOTE_KINDS}
        flags = {
            "OCR 품질 미검사": _one(conn, "SELECT COUNT(*) FROM documents WHERE parse_note LIKE '%OCR 품질 미검사%'"),
            "일부만 읽음": _one(conn, "SELECT COUNT(*) FROM documents WHERE parse_note LIKE '%일부만 읽음%'"),
            "본문 잘림": _one(conn, "SELECT COUNT(*) FROM documents WHERE parse_note LIKE '%본문 잘림%'"),
            "원본 없음": _one(conn, "SELECT COUNT(*) FROM documents WHERE parse_note LIKE '%원본 없음%'"),
        }
        progs = _rows(conn, "SELECT p.name, COUNT(d.id) FROM projects p JOIN documents d ON d.project_id = p.id"
                            " WHERE p.name LIKE ? GROUP BY p.name ORDER BY 2 DESC", (f"%{archive.DGX_PROJECT_SUFFIX}",))
        chunks_live = _one(conn, "SELECT COUNT(*) FROM doc_chunks c JOIN documents d ON d.id = c.doc_id"
                                 " WHERE d.doc_type = 'grant' AND d.status = 'reviewed' AND c.kind IN ('text', 'table', 'image_text')")
        kg = _rows(conn, "SELECT type, COUNT(*) FROM kg_nodes GROUP BY type ORDER BY 2 DESC")
    idx = _index_state(db.path)
    data = {
        "at": time.strftime("%Y-%m-%d %H:%M"),
        "archive": {"total": a_total, "unique": a_unique, "linked": a_linked, "removed": a_removed,
                    "status": [{"label": s or "(없음)", "n": n} for s, n in a_status],
                    "areas": [{"label": a or "(없음)", "n": n, "linked": int(k or 0)} for a, n, k in a_area]},
        "store": {"total": d_total, "dgx": d_dgx, "full": d_total - d_dgx,
                  "status": [{"label": s or "(없음)", "n": n} for s, n in d_status],
                  "paths": [{"label": k, "n": v} for k, v in notes.items()],
                  "flags": [{"label": k, "n": v} for k, v in flags.items()]},
        "programs": [{"label": name[: -len(archive.DGX_PROJECT_SUFFIX)], "n": n} for name, n in progs[:12]],
        "index": {"chunks": idx["chunks"], "pending": max(0, chunks_live - idx["chunks"]), "live": chunks_live, "updated": idx["updated"]},
        "graph": [{"label": t, "n": n} for t, n in kg],
        "sync": _sync_state(db.path),
    }
    with _lock:
        _cache.update(at=time.time(), data=data)
    return data
