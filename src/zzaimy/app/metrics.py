"""화면 지표 — 숫자마다 이름·한 줄 정의·기준(어디서 언제 읽었나)을 한 곳에 둔다.

왜: 같은 「문서 수」가 화면마다 다른 집합을 세면서 이름이 같아 서로 모순처럼 보였다
(원본 201,478 / 201,463, 문서함 36,365 / 41,770 / 41,572, 조각 1,727,414 / 1,973,998).
숫자는 대개 맞다 — 세는 집합이 다를 뿐이다. 그래서 집합마다 이름을 다르게 하고, 화면은
이름·정의를 여기서 꺼내 쓴다. 같은 이름이면 같은 숫자, 다른 숫자면 다른 이름이 된다.

기준(source)
  live   지금 DB 를 센 값(60초 재사용)
  index  색인 파일의 메타(색인을 마지막으로 만든 때)
  sync   원본 동기화(scripts/170)가 data/platform/sync_status.json 에 남긴 마지막 기록
  audit  반입 연동 점검(scripts/175)의 마지막 결과 data/platform/pipeline_audit.json
  scan   개인정보 잔여 검사의 마지막 결과
  eval   검색 품질 측정의 마지막 결과
  view   요청한 사람의 열람 권한·필터가 걸린 값(화면이 직접 센다 — 여기서는 정의만)

쓰는 법
  파이썬: metrics.get(db, "archive_linked") → {"key","label","value","definition","source","as_of","tip"}
          metrics.values(db, ["documents", "grant_chunks"]) → {key: value}
          metrics.tip("documents") → "정의 · 기준" 한 줄(툴팁)
  화면:   {{ metric_label("archive_linked") }}  → 이름에 정의 툴팁을 붙인 <abbr>
          {{ metric_tip("archive_linked") }}    → title 속성에 넣을 글
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from markupsafe import Markup

TTL_S = 60


@dataclass(frozen=True)
class Metric:
    key: str
    label: str          # 화면 이름 — 다른 집합이면 다른 이름
    definition: str     # 무엇을 셌는지 한 줄
    source: str         # live | index | audit | sync | scan | eval | view


SOURCE_LABEL = {
    "live": "지금 DB",
    "index": "색인 파일",
    "audit": "반입 연동 점검",
    "sync": "원본 동기화 기록",
    "scan": "개인정보 잔여 검사",
    "eval": "검색 품질 측정",
    "view": "보는 사람의 열람 범위",
}

_DEFS = [
    # 원본 보관소 — 원본 장부(archive_files)
    # 원본 장부에는 학교 원본 말고도 플랫폼 사본(반입·첨부 저장본)과 부산물 폴더(그림 추출·웹 페이지 저장 자료)가 함께 있다 — 같은
    # 「원본」이라도 장부 전체·학교 원본·DGX 목록·열람 범위는 다른 집합이라 이름을 따로 둔다(archive_state.original_sql)
    Metric("archive_files", "원본 장부 파일 수", "원본 장부의 파일 전부 — 학교 원본에 플랫폼 사본·부산물 폴더(그림 추출·웹 페이지 저장 자료)까지, 사진·압축·같은 파일 중복 포함, 지운 파일 제외", "live"),
    Metric("archive_originals", "원본 파일 수", "학교 원본 폴더의 파일 — 플랫폼 사본·부산물 폴더(그림 추출·웹 페이지 저장 자료) 제외, 사진·압축·같은 파일 중복 포함, 지운 파일 제외(원본 보관소 머리 숫자)", "live"),
    Metric("archive_visible", "열람 가능 원본 파일 수", "원본 파일 가운데 지금 계정이 볼 수 있는 것 — 볼 수 없는 문서에 이어진 파일 제외", "view"),
    Metric("archive_failed", "처리 실패 원본 수", "학교 원본 가운데 처리 상태가 실패인 것 — 판독·반입 실패가 원본 장부에 적힌 처리 대상(원본 보관소 「실패」)", "live"),
    Metric("dgx_listing", "DGX 원본 목록 수", "마지막 원본 동기화 때 DGX 원본 폴더 목록의 파일 — 변환 부산물 포함, 플랫폼 사본 제외", "sync"),
    Metric("archive_unique", "중복 제외 원본 수", "같은 파일 묶음마다 대표 하나만 센 원본 장부 파일", "live"),
    Metric("archive_linked", "원본 연결 수", "라이브러리 문서와 이어진 원본 파일 — 한 문서에 원본이 여럿이면 각각 셈", "live"),
    Metric("archive_removed", "지운 원본 수", "원본 장부에 남아 있지만 보관소에서 지워진 파일", "live"),
    # 라이브러리 — documents
    Metric("documents", "등록 문서 수", "라이브러리에 등록된 문서 전부 — 모든 업무·처리 상태, 열람 권한과 무관", "live"),
    Metric("documents_light", "DGX 보관 문서 수", "원본을 DGX 에 두고 처리 결과만 들인 등록 문서(가벼운 처리)", "live"),
    Metric("documents_full", "원본 반입·업로드 문서 수", "원본 파일까지 플랫폼에 들어온 등록 문서", "live"),
    Metric("documents_reviewed", "처리 완료 문서 수", "등록 문서 가운데 처리(판독·조각·검토)가 끝난 것", "live"),
    Metric("documents_visible", "열람 가능 등록 문서", "지금 계정이 볼 수 있는 등록 문서 — 고른 업무·조건 적용", "view"),
    Metric("documents_use_criteria", "기준 쓰임 문서", "열람 가능 등록 문서 가운데 갈래가 공고·지침·규정·심사 기준·기본계획이거나 규정 색인에 든 것 — 고른 업무·검색어 적용(라이브러리 「기준」 칩)", "view"),
    Metric("documents_use_work", "작성·결과 문서", "열람 가능 등록 문서 가운데 기준 쓰임이 아닌 것 — 계획서·보고서·양식·현황표·분류 전 등, 고른 업무·검색어 적용(라이브러리 「작성·결과」 칩)", "view"),
    Metric("grant_reviewed", "처리 완료 사업 문서 수", "사업 문서(국고) 가운데 처리 완료된 것 — 검색 범위(RAG 공간)가 세는 집합", "live"),
    Metric("grant_regulation_kind", "규정 갈래 사업 문서 수", "처리 완료 사업 문서 가운데 서류 갈래가 규정인 것 — 사업 문서 검색으로 찾음, 규정 색인과 별개", "live"),
    Metric("student_regulations", "학생 공개 규정 수", "처리 완료 규정 문서(기준 문서) 가운데 학생 공개로 지정된 것 — 학생 계정 검색의 대상", "live"),
    # 프로젝트 — 진행 중 프로젝트와 보관된 지난 사업(사업·연도 묶음)은 같은 표에 있지만 다른 집합이다
    Metric("projects_active", "프로젝트 수", "진행 중인 프로젝트(보관되지 않은 것) — 사이드바·업무 화면의 프로젝트", "live"),
    Metric("projects_archived", "지난 사업 묶음 수", "보관된 지난 사업 — 원본 보관소에서 사업·연도별로 묶어 둔 것", "live"),
    # 조각·색인
    Metric("grant_chunks", "사업 문서 조각 수", "처리 완료 사업 문서의 본문·표·그림 글 조각 — 검색 색인에 들어갈 대상", "live"),
    Metric("embedding_index", "임베딩 색인 조각 수", "사업 문서 임베딩 색인에 들어간 조각 — 색인을 마지막으로 만든 때 기준", "index"),
    Metric("embedding_pending", "색인 대기 조각 수", "사업 문서 조각 수에서 임베딩 색인 조각 수를 뺀 것", "live"),
    Metric("regulation_chunks", "규정 조각 수", "기준 문서(규정·지침)를 문단 단위로 자른 규정 검색 조각", "live"),
    Metric("regulation_docs", "규정 문서 수", "규정 조각이 하나라도 있는 기준 문서", "live"),
    # 그래프·개체
    Metric("entities", "문서에서 뽑은 개체 수", "문서에서 뽑은 사업·부서·연도 이름(entities 표) — 지식그래프와 별개", "live"),
    Metric("graph_nodes", "그래프 노드 수", "지식그래프 저장소(kg_nodes)의 노드 전부 — 사업·연차·문서·절·지표", "live"),
    Metric("graph_edges", "그래프 관계 수", "지식그래프 저장소(kg_edges)의 관계 전부", "live"),
    Metric("graph_doc_nodes", "그래프 문서 노드 수", "지식그래프에 들어간 문서 노드", "live"),
    # 학습 데이터
    Metric("dataset_pairs", "학습 데이터쌍", "데이터 묶음에 담긴 문답 쌍 — 검수 전 후보 포함, 학습 승인과 별개", "live"),
    # 반입 연동 점검(scripts/175)
    Metric("audit_targets", "처리 대상 문서 수", "원본 가운데 문서 형식·부산물 폴더 밖·범위 안(지출·증빙 제외)·중복 아님인 파일", "audit"),
    Metric("audit_in_library", "처리 대상 중 라이브러리 반영", "처리 대상 문서 가운데 라이브러리 문서로 들어온 것", "audit"),
    Metric("audit_failed", "처리 대상 중 실패", "처리 대상 문서 가운데 판독에 실패해 실패 장부에 사유가 남은 것 — 원본 보관소 「실패」와 같은 집합, 실패가 원본 장부에 아직 옮겨 적히지 않은 만큼 더 많을 수 있음", "audit"),
    Metric("audit_waiting", "처리 대상 중 대기", "들어오거나 바뀐 지 6시간이 안 돼 아직 결과가 없는 것", "audit"),
    Metric("audit_missed", "처리 대상 중 놓침", "들어오거나 바뀐 지 6시간이 넘었는데 결과가 없는 것", "audit"),
    Metric("audit_graph_docs", "그래프 대상 사업 문서 수", "처리 완료 사업 문서에서 범위 밖 갈래(지출·계약 증빙)를 뺀 것", "audit"),
    Metric("audit_graph_doc_nodes", "그래프 문서 노드 수", "지식그래프에 들어간 문서 노드", "audit"),
    Metric("audit_grant_chunks", "사업 문서 조각 수", "처리 완료 사업 문서의 본문·표·그림 글 조각", "audit"),
    Metric("audit_embedding_index", "임베딩 색인 조각 수", "사업 문서 임베딩 색인에 들어간 조각", "audit"),
    Metric("audit_lexical_index", "어휘 색인 조각 수", "사업 문서 어휘(낱말) 색인에 들어간 조각", "audit"),
    # 개인정보
    Metric("pii_subjects", "마스킹 대상 문서 수", "마스킹 정책 대상 유형의 등록 문서 — 처리 상태 무관", "live"),
    Metric("pii_recorded", "마스킹 기록 있는 문서", "마스킹 대상 가운데 가린 기록이 남은 문서", "live"),
    Metric("pii_unrecorded", "처리 완료·기록 없는 문서", "마스킹 대상 가운데 처리는 끝났는데 가린 기록이 없는 문서(기록 기능 도입 전 처리분)", "live"),
    Metric("pii_scan_chunks", "잔여 검사 조각 수", "잔여 검사가 훑은 조각 — 사업 문서의 모든 갈래 조각 + 규정 조각", "scan"),
    # 품질 측정
    Metric("eval_chunks", "측정 당시 규정 조각 수", "검색 품질을 잰 때의 규정 조각 수 — 지금 조각 수와 다를 수 있음", "eval"),
]

DEFS: dict[str, Metric] = {m.key: m for m in _DEFS}

_cache: dict = {"at": 0.0, "path": None, "data": None}
_lock = threading.Lock()


def definition(key: str) -> Metric:
    return DEFS[key]


def tip(key: str, as_of: str = "") -> str:
    """툴팁 한 줄 — 「정의 · 기준」."""
    m = DEFS[key]
    src = SOURCE_LABEL.get(m.source, m.source)
    return f"{m.definition} · 기준: {src}{(' ' + as_of) if as_of else ''}"


def label_html(key: str, as_of: str = "", text: str = "") -> Markup:
    """이름에 정의 툴팁을 붙인 <abbr> — 화면에서 이름만 보여도 마우스를 올리면 무엇을 셌는지 나온다."""
    m = DEFS[key]
    return Markup('<abbr class="metric" title="{}">{}</abbr>').format(tip(key, as_of), text or m.label)


def _one(conn, sql: str, args=()) -> int:
    try:
        return int(conn.execute(sql, args).fetchone()[0] or 0)
    except Exception:
        return 0


def _count_live(db) -> dict[str, int]:
    """live 지표를 한 번에 센다. 표가 없으면 0(새 설치·시험 DB)."""
    # 읽기 전용(자동 커밋) 연결 — PostgreSQL 에서 없는 표 하나가 트랜잭션 전체를 멈추지 않게
    from contextlib import closing

    from zzaimy.app.database_backend import connect
    from zzaimy.app import archive_state
    orig, orig_args = archive_state.original_sql()
    state, state_args = archive_state.state_sql()
    with closing(connect(str(db.path), readonly=True)) as conn:
        v = {
            "archive_originals": _one(conn, f"SELECT COUNT(*) FROM archive_files WHERE removed_at = '' AND {orig}", orig_args),
            "archive_failed": _one(conn, f"SELECT COUNT(*) FROM archive_files WHERE removed_at = '' AND {orig} AND {state} = 'failed'",
                                   orig_args + state_args),
            "archive_files": _one(conn, "SELECT COUNT(*) FROM archive_files WHERE removed_at = ''"),
            "archive_unique": _one(conn, "SELECT COUNT(*) FROM archive_files WHERE removed_at = '' AND dup_of = ''"),
            "archive_linked": _one(conn, "SELECT COUNT(*) FROM archive_files WHERE removed_at = '' AND doc_id IS NOT NULL"),
            "archive_removed": _one(conn, "SELECT COUNT(*) FROM archive_files WHERE removed_at <> ''"),
            "documents": _one(conn, "SELECT COUNT(*) FROM documents"),
            "documents_light": _one(conn, "SELECT COUNT(*) FROM documents WHERE stored_path LIKE 'dgx://%'"),
            "documents_reviewed": _one(conn, "SELECT COUNT(*) FROM documents WHERE status = 'reviewed'"),
            "grant_reviewed": _one(conn, "SELECT COUNT(*) FROM documents WHERE doc_type = 'grant' AND status = 'reviewed'"),
            "grant_chunks": _one(conn, "SELECT COUNT(*) FROM doc_chunks c JOIN documents d ON d.id = c.doc_id"
                                       " WHERE d.doc_type = 'grant' AND d.status = 'reviewed'"
                                       " AND c.kind IN ('text', 'table', 'image_text')"),
            "grant_regulation_kind": _one(conn, "SELECT COUNT(*) FROM documents WHERE doc_type = 'grant' AND status = 'reviewed'"
                                                " AND kind = 'regulation'"),
            "student_regulations": _one(conn, "SELECT COUNT(*) FROM documents WHERE doc_type = 'regulation'"
                                              " AND status = 'reviewed' AND audience = 'student'"),
            "regulation_chunks": _one(conn, "SELECT COUNT(*) FROM regulation_chunks"),
            "regulation_docs": _one(conn, "SELECT COUNT(DISTINCT doc_id) FROM regulation_chunks"),
            "entities": _one(conn, "SELECT COUNT(*) FROM entities"),
            "graph_nodes": _one(conn, "SELECT COUNT(*) FROM kg_nodes"),
            "graph_edges": _one(conn, "SELECT COUNT(*) FROM kg_edges"),
            "graph_doc_nodes": _one(conn, "SELECT COUNT(*) FROM kg_nodes WHERE type = 'doc'"),
            "dataset_pairs": _one(conn, "SELECT COALESCE(SUM(n_pairs), 0) FROM datasets"),
            "projects_active": _one(conn, "SELECT COUNT(*) FROM projects WHERE archived = 0"),
            "projects_archived": _one(conn, "SELECT COUNT(*) FROM projects WHERE archived <> 0"),
        }
    v["documents_full"] = v["documents"] - v["documents_light"]
    return v


def _index_meta() -> tuple[int, str]:
    """사업 문서 임베딩 색인의 조각 수와 만든 때(파일 시각)."""
    try:
        from zzaimy.app import grant_search
        meta = grant_search.INDEX.with_suffix(".json")
        got = json.loads(meta.read_text(encoding="utf-8"))
        return int(got.get("n_chunks") or 0), time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.stat().st_mtime))
    except Exception:
        return 0, ""


def live(db, force: bool = False) -> dict:
    """live·index 지표 전부 — {"at": 센 시각, "values": {key: int}, "as_of": {key: str}}. 60초 재사용."""
    path = str(getattr(db, "path", ""))
    with _lock:
        if (not force and _cache["data"] is not None and _cache["path"] == path
                and time.time() - _cache["at"] < TTL_S):
            return _cache["data"]
    values = _count_live(db)
    n_idx, idx_at = _index_meta()
    values["embedding_index"] = n_idx
    values["embedding_pending"] = max(0, values["grant_chunks"] - n_idx)
    at = time.strftime("%Y-%m-%d %H:%M")
    as_of = {k: at for k in values}
    as_of["embedding_index"] = idx_at
    data = {"at": at, "values": values, "as_of": as_of}
    with _lock:
        _cache.update(at=time.time(), path=path, data=data)
    return data


_AUDIT_FIELDS = {
    "audit_targets": "targets", "audit_in_library": "in_docbox", "audit_failed": "failed",
    "audit_waiting": "waiting", "audit_missed": "missed", "audit_graph_docs": "grant_docs",
    "audit_graph_doc_nodes": "graph_doc_nodes", "audit_grant_chunks": "grant_chunks",
    "audit_embedding_index": "embedding_index", "audit_lexical_index": "lexical_index",
}


def audit(data_dir: str | Path) -> dict | None:
    """반입 연동 점검의 마지막 결과를 지표 이름으로 — {"at", "values"}. 점검 결과가 없으면 None."""
    p = Path(data_dir) / "pipeline_audit.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {"at": str(raw.get("at") or ""),
            "values": {k: int(raw.get(f) or 0) for k, f in _AUDIT_FIELDS.items()}}


def get(db, key: str) -> dict:
    """지표 하나 — 이름·값·정의·기준·툴팁. view·scan·eval 지표는 값이 화면 쪽에 있어 value=None."""
    m = DEFS[key]
    value, as_of = None, ""
    if m.source in ("live", "index"):
        got = live(db)
        value, as_of = got["values"].get(key), got["as_of"].get(key, "")
    elif m.source == "audit":
        got = audit(Path(db.path).parent)
        if got:
            value, as_of = got["values"].get(key), got["at"]
    return {"key": key, "label": m.label, "value": value, "definition": m.definition,
            "source": m.source, "as_of": as_of, "tip": tip(key, as_of)}


def values(db, keys: list[str]) -> dict[str, int | None]:
    return {k: get(db, k)["value"] for k in keys}


# ---- 표시 — 지표 옆 시각·수·걸린 시간을 화면마다 같은 꼴로 ----

def kst(value) -> str:
    """ISO 시각(UTC·마이크로초·시간대 섞임) → 한국시간 「YYYY-MM-DD HH:MM」. 읽지 못하면 앞 16자를 그대로."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:16].replace("T", " ")
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%d %H:%M")       # 시간대 없는 값은 이미 현지 시각으로 적힌 것
    return dt.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")


def num(value) -> str:
    """천 단위 쉼표. 수가 아니면 그대로."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value if value is not None else "")


def duration_ms(value) -> str:
    """걸린 시간(ms) → 「3시간 35분」「12초」「850ms」."""
    try:
        ms = int(float(value))
    except (TypeError, ValueError):
        return ""
    if ms < 1000:
        return f"{ms}ms"
    sec = ms // 1000
    h, rem = divmod(sec, 3600)
    m, s_ = divmod(rem, 60)
    if h:
        return f"{h}시간 {m}분" if m else f"{h}시간"
    if m:
        return f"{m}분 {s_}초" if s_ else f"{m}분"
    return f"{s_}초"


def install(env) -> None:
    """Jinja 환경에 metric_label·metric_tip 과 표시 필터(kst·num·duration)를 단다(main.py 가 부른다)."""
    env.globals["metric_label"] = label_html
    env.globals["metric_tip"] = tip
    env.globals["metric_defs"] = DEFS
    env.filters["kst"] = kst
    env.filters["num"] = num
    env.filters["duration"] = duration_ms


__all__ = ["DEFS", "Metric", "SOURCE_LABEL", "audit", "definition", "duration_ms", "get", "install", "kst", "label_html", "live", "num",
           "tip", "values"]
