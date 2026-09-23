"""개인정보 마스킹 감사 — 마스킹 기록·자가 점검·잔여 검사 (절대 규칙 3).

마스킹 자체는 `zzaimy.ingest.pii.PiiMasker`가 한다. 이 모듈은 그 결과를
확인 가능하게 만드는 세 가지를 맡는다.

1. 기록 — 마스킹이 돌 때마다 문서별·유형별 건수를 `mask_events`에 남긴다.
   원문 값은 절대 저장하지 않는다. 유형·건수와, 마스킹이 끝난 본문에서 뜬
   짧은 문맥(치환 토큰 주변 80자 이내)만 남긴다.
2. 자가 점검(known-answer) — 합성 표본을 실제 마스커에 넣어 지원 유형마다
   가려졌는지, 가리면 안 되는 것(날짜·문맥 없는 번호 등)은 남겼는지 본다.
   마스커가 지금 설정대로 동작한다는 근거는 이것 하나다.
3. 잔여 검사 — 실제로 저장·색인된 본문(documents.masked_text, doc_chunks,
   코퍼스 regulation_chunks)에 마스커와 같은 정규식 정의를 presidio 배관 없이
   독립 실행해 남아 있는 패턴을 찾는다. 마스킹이 빠진 경로를 잡는 용도이지
   탐지기 자체의 한계(라벨 없는 자유 문장 속 성명, 주소 등)는 보지 못한다.

자가 점검·잔여 검사 결과는 settings(JSON)에 남겨 /dev/pii 화면이 마지막
실행을 보여준다. 이 모듈은 presidio를 함수 안에서만 불러온다 — 로드에
실패해도 화면은 "마스커 로드 실패" 상태로 뜬다.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zzaimy.app.db import Database
    from zzaimy.ingest.pii import MaskEvent

SELFTEST_KEY = "pii_selftest"
SCAN_KEY = "pii_scan"
CONTEXT_CHARS = 80          # 기록·검사에 남기는 문맥 표본 최대 길이
NONE_ENTITY = "NONE"        # 마스킹은 돌았지만 0건이었음을 뜻하는 기록 행
SCAN_MAX_DOCS = 100         # 잔여 검사 결과에 남기는 문서 수 상한
SCAN_MAX_SAMPLES = 3        # 문서당 문맥 표본 상한

ENTITY_LABELS = {
    "KR_RRN": "주민등록번호",
    "KR_PHONE": "전화번호",
    "EMAIL": "이메일",
    "KR_BRN": "사업자등록번호",
    "KR_BANK_ACCOUNT": "계좌번호",
    "KR_NAME": "성명",
    "KR_STUDENT_ID": "학번·수험번호",
    "KR_BIRTHDATE": "생년월일",
    NONE_ENTITY: "없음",
}

# 문서 유형별 마스킹 정책 — 화면에 그대로 보여준다.
# (doc_type, 표시명, 마스킹 여부, 시점, 이유). 표시명은 화면의 문서 유형 이름과 같게.
MASK_POLICY: list[tuple[str, str, bool, str, str]] = [
    ("grant", "국고사업 문서", True,
     "접수 직후, 본문 전체를 가린 뒤 조각화·검토",
     "신청서·계획서에 담당자 연락처와 참여자 인적사항이 섞여 들어온다"),
    ("recruit", "채용 문서", True,
     "접수 직후",
     "지원 서류라 성명·연락처·주민등록번호·계좌 등 개인정보가 가장 많다"),
    ("admission", "입학 문서", True,
     "접수 직후",
     "지원자 인적사항이 들어온다"),
    ("auto", "행정 문서", True,
     "접수 직후",
     "유형을 정하지 않은 일반 접수분 — 무엇이 들어올지 몰라 전부 가린다"),
    ("ocr", "문서 추출", True,
     "구조화 조각을 저장하기 전에",
     "검토 없이 추출만 하는 경로라 저장되는 조각 자체가 안전해야 한다"),
    ("regulation", "기준 문서(규정·지침·공고)", False,
     "가리지 않음",
     "판단 근거 문서이지 개인 문서가 아니다(ADR-0006). 예외: 공개 국고 코퍼스 반입분"
     "(스크립트 74)은 적재 전에 가린다"),
]


def is_masking_subject(doc_type: str | None, owner: str | None) -> bool:
    """이 문서가 마스킹 대상인가 — 기준 문서만 제외(ADR-0006), 단 코퍼스 반입분은 포함."""
    return (doc_type or "auto") != "regulation" or (owner or "") == "corpus"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# --- 1. 마스킹 기록 ---


def _window(text: str, start: int, end: int, limit: int = CONTEXT_CHARS) -> str:
    """[start, end) 주변을 limit자 이내로 뜬다. 공백은 한 칸으로 접는다."""
    side = max(0, (limit - (end - start)) // 2)
    piece = text[max(0, start - side): end + side]
    return _squash(piece)[:limit]


def mask_event_rows(masked_text: str, events: Sequence[MaskEvent]) -> list[dict]:
    """유형별 (건수, 첫 발생 문맥) 행으로 접는다.

    MaskEvent의 좌표는 원문 기준이므로 앞선 치환들의 길이 차를 누적해 마스킹본
    좌표로 옮긴다. 문맥은 마스킹본에서만 뜨고, 좌표가 맞지 않으면(다른 본문을
    넘긴 경우) 문맥을 비운다 — 어느 쪽이든 원문 값이 들어갈 길은 없다.
    """
    rows: dict[str, dict] = {}
    shift = 0
    for ev in sorted(events, key=lambda e: (e.start, e.end)):
        token = f"[{ev.entity_type}]"
        m_start = ev.start + shift
        m_end = m_start + len(token)
        shift += len(token) - (ev.end - ev.start)
        row = rows.get(ev.entity_type)
        if row is None:
            context = None
            if masked_text[m_start:m_end] == token:
                context = _window(masked_text, m_start, m_end)
            rows[ev.entity_type] = {"entity_type": ev.entity_type, "n": 1, "context": context}
        else:
            row["n"] += 1
    return sorted(rows.values(), key=lambda r: (-r["n"], r["entity_type"]))


def record_mask_events(
    db: Database, doc_id: int, masked_text: str, events: Sequence[MaskEvent]
) -> list[dict]:
    """마스킹 직후 호출 — 문서의 기록을 교체 저장하고 저장한 행을 돌려준다."""
    rows = mask_event_rows(masked_text, events)
    db.replace_mask_events(doc_id, rows)
    return rows


# --- 2. 자가 점검 (known-answer) ---

# 전부 합성값이다. 주민등록번호·사업자등록번호는 체크섬만 맞춘 가공 번호
# (tests/test_pii.py와 같은 값). 지원 유형마다 최소 하나씩 둔다.
SELFTEST_CASES: list[dict] = [
    {"entity_type": "KR_RRN", "value": "990101-1234563",
     "text": "담당자 주민등록번호 {v} 기재"},
    {"entity_type": "KR_PHONE", "value": "010-0000-0000", "note": "휴대전화",
     "text": "연락처: {v}"},
    {"entity_type": "KR_PHONE", "value": "053-000-0000", "note": "유선전화",
     "text": "사무실 {v} 로 문의"},
    {"entity_type": "EMAIL", "value": "test@example.com",
     "text": "문의: {v} 로 발송"},
    {"entity_type": "KR_BRN", "value": "123-45-67891",
     "text": "사업자등록번호 {v} (주)합성상사"},
    {"entity_type": "KR_BANK_ACCOUNT", "value": "110-123-456789", "note": "라벨 문맥 있음",
     "text": "계좌번호: {v} (합성은행)"},
    {"entity_type": "KR_NAME", "value": "홍길동", "note": "라벨 문맥 있음",
     "text": "성명: {v}, 소속: 산학협력단"},
    {"entity_type": "KR_NAME", "value": "김영남", "note": "서식 표 — 띄어 쓴 라벨·콜론 없음",
     "text": "대학명 영남이공대학교 성 명 {v} 학 과 사이버보안과"},
    {"entity_type": "KR_STUDENT_ID", "value": "2437030", "note": "학번 라벨(띄어쓰기)",
     "text": "학 번 {v} 교육일자 2026.09.01"},
    {"entity_type": "KR_BIRTHDATE", "value": "1999.01.23", "note": "생년월일 라벨",
     "text": "생년월일: {v} 성별 남"},
]

# 오탐 방지 — 마스킹 뒤에도 그대로 남아야 하는 것
SELFTEST_KEEP: list[dict] = [
    {"label": "날짜는 전화번호로 잡지 않는다", "value": "2026-09-01",
     "text": "제출 기한은 {v} 이다."},
    {"label": "계좌 문맥 없는 번호는 남긴다", "value": "110-123-456789",
     "text": "과제 관리코드 {v} 로 등록됨"},
    {"label": "체크섬이 틀린 번호는 주민등록번호로 보지 않는다", "value": "990101-1234567",
     "text": "문서번호 {v} 참조"},
    {"label": "일반 문장은 손대지 않는다",
     "value": "본 사업은 교육과정 개편과 성과관리 지표 고도화를 목표로 한다.",
     "text": "{v}"},
    {"label": "라벨에 조사가 붙은 문장은 성명으로 잡지 않는다", "value": "이름으로",
     "text": "유추하기 어려운 {v} 변경함"},
    {"label": "라벨 뒤 서식 낱말은 성명으로 잡지 않는다", "value": "연락처",
     "text": "담당자 {v}: 사이버보안과 사무실"},
    {"label": "작성일자 같은 일반 날짜는 생년월일로 잡지 않는다", "value": "2026.09.01",
     "text": "작성일자 {v} 근로기관명 영남이공대학교"},
    {"label": "파일명·해시 속 숫자열은 전화번호로 잡지 않는다",
     "value": "7979a9c281745ecddbb09513eb98567c58f0351234567deaf556b5b6f925b.jpg",
     "text": "[그림] {v}"},
]


def _load_masker() -> tuple[Any, Any, list[str]]:
    """실제 마스커와 지원 유형 목록. presidio·spacy는 여기서만 불러온다."""
    from zzaimy.ingest.pii import PiiMasker, RawDocument, _build_recognizers

    supported = sorted({e for r in _build_recognizers() for e in r.supported_entities})
    return PiiMasker(), RawDocument, supported


def _save(db: Database | None, key: str, result: dict) -> None:
    if db is not None:
        db.set_setting(key, json.dumps(result, ensure_ascii=False))


def load_json(db: Database, key: str) -> dict | None:
    raw = db.get_setting(key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def run_selftest(db: Database | None = None) -> dict:
    """합성 표본으로 마스커를 검증하고 결과를 settings에 남긴다.

    유형별로 '값이 사라졌고 해당 토큰이 남았는가'를, 오탐 표본은 '그대로
    남았는가'를 본다. 마지막으로 표본 전부를 한 문서에 넣어 누락이 없는지
    한 번 더 본다. 마스커 로드 실패는 500이 아니라 결과 안의 error로 남긴다.
    """
    t0 = time.perf_counter()
    try:
        masker, raw_cls, supported = _load_masker()
    except Exception as exc:  # 의존성 부재·모델 로드 실패 등 — 화면에 그대로 보인다
        result = {
            "at": _now(), "ok": False,
            "error": f"마스커 로드 실패: {type(exc).__name__}: {exc}",
            "passed": 0, "total": 0, "elapsed_ms": 0,
            "supported": [], "uncovered": [], "checks": [],
        }
        _save(db, SELFTEST_KEY, result)
        return result

    checks: list[dict] = []
    for case in SELFTEST_CASES:
        text = case["text"].format(v=case["value"])
        out, _ = masker.mask(raw_cls(doc_id="selftest", text=text))
        token = f"[{case['entity_type']}]"
        leaked = case["value"] in out.text
        tagged = token in out.text
        if leaked:
            detail = "값이 그대로 남아 있음"
        elif not tagged:
            detail = f"가려졌으나 {token} 토큰이 아님 — 다른 유형으로 잡힘"
        else:
            detail = "가려짐"
        label = ENTITY_LABELS.get(case["entity_type"], case["entity_type"])
        if case.get("note"):
            label += f" · {case['note']}"
        checks.append({
            "kind": "mask", "entity_type": case["entity_type"], "label": label,
            "passed": (not leaked) and tagged, "detail": detail, "output": out.text,
        })
    for keep in SELFTEST_KEEP:
        text = keep["text"].format(v=keep["value"])
        out, _ = masker.mask(raw_cls(doc_id="selftest", text=text))
        kept = keep["value"] in out.text
        checks.append({
            "kind": "keep", "entity_type": "", "label": keep["label"],
            "passed": kept, "detail": "그대로 남음" if kept else "가려짐(오탐)",
            "output": out.text,
        })
    combined = " / ".join(c["text"].format(v=c["value"]) for c in SELFTEST_CASES)
    out, events = masker.mask(raw_cls(doc_id="selftest", text=combined))
    leaked_values = [c["value"] for c in SELFTEST_CASES if c["value"] in out.text]
    checks.append({
        "kind": "combined", "entity_type": "", "label": "표본 전부를 한 문서에",
        "passed": not leaked_values,
        "detail": (
            f"치환 {len(events)}건, 누락 없음" if not leaked_values
            else f"누락 {len(leaked_values)}건"
        ),
        "output": out.text,
    })

    covered = {c["entity_type"] for c in SELFTEST_CASES}
    uncovered = [e for e in supported if e not in covered]
    passed = sum(1 for c in checks if c["passed"])
    result = {
        "at": _now(),
        "ok": passed == len(checks) and not uncovered,
        "error": None,
        "passed": passed,
        "total": len(checks),
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "supported": supported,
        "uncovered": uncovered,   # 지원하지만 표본이 없는 유형 — 미확인으로 표시
        "checks": checks,
    }
    _save(db, SELFTEST_KEY, result)
    return result


# --- 3. 잔여 검사 ---


def redact_value(value: str) -> str:
    """앞 3자(한글은 1자)만 남기고 나머지 글자·숫자를 *로. 길이는 유지한다."""
    out: list[str] = []
    shown = 0
    for ch in value:
        if ch.isalnum():
            limit = 1 if "가" <= ch <= "힣" else 3
            if shown < limit:
                out.append(ch)
                shown += 1
            else:
                out.append("*")
        else:
            out.append(ch)
    return "".join(out)


def _load_detectors() -> list[tuple[str, Any, Any]]:
    """마스커의 recognizer 정의(정규식·체크섬)를 그대로 가져와 독립 컴파일한다.

    presidio 분석 엔진(레지스트리·점수·임계값)은 거치지 않는다 — 그 배관이
    잘못돼도 여기서 드러나게. 플래그는 presidio 기본값과 같게 맞춘다.
    """
    import regex

    from zzaimy.ingest.pii import _build_recognizers

    flags = regex.DOTALL | regex.MULTILINE | regex.IGNORECASE
    out = []
    for rec in _build_recognizers():
        for p in rec.patterns:
            out.append((rec.supported_entities[0], regex.compile(p.regex, flags), rec.validate_result))
    return out


def _already_masked(value: str) -> str | bool:
    """이미 가려진 자리인가.

    마스킹은 값을 별표로 덮는다(redact_value). 그 결과를 다시 탐지하면 점검이
    스스로 만든 결과를 위반으로 세게 된다. 실측(2026-09-20 운영 자료): 잔여로
    잡힌 표본 60건이 전부 "성*", "소***" 같은 이미 가린 자리였고 실제 노출은
    없었다. 별표나 자리표를 품은 구간은 세지 않는다.
    """
    if "*" in value:
        return True
    return "[KR_" in value or "[EMAIL]" in value


def find_spans(text: str, detectors: Sequence[tuple[str, Any, Any]]) -> list[tuple[int, int, str]]:
    """탐지 구간 (start, end, entity). 겹침은 앞선 것·긴 것 우선 — 마스커와 같은 규칙."""
    spans: list[tuple[int, int, str]] = []
    for entity, rx, validate in detectors:
        for m in rx.finditer(text):
            if validate(m.group()) is False:   # None은 검증 없음, False만 탈락
                continue
            if _already_masked(m.group()):     # 우리가 가려 놓은 자리는 잔여가 아니다
                continue
            spans.append((m.start(), m.end(), entity))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    merged: list[tuple[int, int, str]] = []
    for s in spans:
        if merged and s[0] < merged[-1][1]:
            continue
        merged.append(s)
    return merged


def _redacted_copy(text: str, spans: Sequence[tuple[int, int, str]]) -> str:
    """탐지 구간을 전부 가린 사본 — 길이가 같아 좌표를 그대로 쓸 수 있다."""
    parts: list[str] = []
    cursor = 0
    for s, e, _ in spans:
        parts.append(text[cursor:s])
        parts.append(redact_value(text[s:e]))
        cursor = e
    parts.append(text[cursor:])
    return "".join(parts)


def _doc_index(db: Database) -> list[dict]:
    """문서 대장(본문 제외) — 정책 판정과 표 구성용."""
    with db._conn() as conn:  # noqa: SLF001 — 본문 컬럼 없이 가볍게 읽는다 (/dev/corpus와 같은 방식)
        return [dict(r) for r in conn.execute(
            "SELECT id, filename, doc_type, owner, status, created_at FROM documents"
            " ORDER BY id DESC"
        ).fetchall()]


def _indexed_texts(db: Database, doc: dict) -> Iterator[tuple[str, str]]:
    """문서 하나의 저장·색인 본문을 (위치, 본문)으로 차례로 낸다."""
    with db._conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT masked_text FROM documents WHERE id = ?", (doc["id"],)
        ).fetchone()
        if row and row[0]:
            yield "masked_text", row[0]
        for r in conn.execute(
            "SELECT content FROM doc_chunks WHERE doc_id = ? ORDER BY seq", (doc["id"],)
        ):
            yield "doc_chunks", r[0]
        for r in conn.execute(
            "SELECT content FROM regulation_chunks WHERE doc_id = ? ORDER BY id", (doc["id"],)
        ):
            yield "regulation_chunks", r[0]


def run_scan(db: Database) -> dict:
    """마스킹 대상 문서의 저장 본문에 탐지 정규식을 독립 실행해 잔여를 센다."""
    t0 = time.perf_counter()
    try:
        detectors = _load_detectors()
    except Exception as exc:
        result = {
            "at": _now(), "error": f"탐지기 로드 실패: {type(exc).__name__}: {exc}",
            "detectors": [], "scanned": {}, "hits": 0, "by_type": {}, "docs": [],
            "excluded_docs": 0, "elapsed_ms": 0,
        }
        _save(db, SCAN_KEY, result)
        return result

    scanned = {"docs": 0, "masked_text": 0, "doc_chunks": 0, "regulation_chunks": 0, "chars": 0}
    by_type: dict[str, int] = {}
    docs_out: list[dict] = []
    excluded = 0
    for doc in _doc_index(db):
        if not is_masking_subject(doc.get("doc_type"), doc.get("owner")):
            excluded += 1
            continue
        scanned["docs"] += 1
        doc_hits: dict[str, int] = {}
        where: list[str] = []
        samples: list[dict] = []
        for place, text in _indexed_texts(db, doc):
            scanned[place] += 1
            scanned["chars"] += len(text)
            spans = find_spans(text, detectors)
            if not spans:
                continue
            if place not in where:
                where.append(place)
            safe = _redacted_copy(text, spans)
            for s, e, entity in spans:
                doc_hits[entity] = doc_hits.get(entity, 0) + 1
                by_type[entity] = by_type.get(entity, 0) + 1
                if len(samples) < SCAN_MAX_SAMPLES:
                    samples.append({
                        "entity_type": entity, "where": place,
                        "context": _window(safe, s, e),
                    })
        if doc_hits and len(docs_out) < SCAN_MAX_DOCS:
            docs_out.append({
                "doc_id": doc["id"], "filename": doc["filename"],
                "doc_type": doc["doc_type"], "owner": doc.get("owner"),
                "hits": sum(doc_hits.values()), "by_type": doc_hits,
                "where": where, "samples": samples,
            })
    result = {
        "at": _now(),
        "error": None,
        "detectors": sorted({d[0] for d in detectors}),
        "scanned": scanned,
        "hits": sum(by_type.values()),
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "docs": docs_out,
        "excluded_docs": excluded,   # 정책상 마스킹하지 않는 기준 문서 — 검사 제외
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    }
    _save(db, SCAN_KEY, result)
    return result


# --- 화면 조립 ---


def corpus_db_path(db: Database) -> Path | None:
    """플랫폼 DB 옆에 있는 코퍼스 파일럿 DB(스크립트 74 기본 대상). 없으면 None."""
    from zzaimy.app import paths as _paths

    p = _paths.corpus_db_existing(Path(db.path).parent)
    if p.exists() and p.resolve() != Path(db.path).resolve():
        return p
    return None


def source_view(db: Database, name: str, linkable: bool) -> dict:
    """한 DB의 마스킹 현황 — 요약·문서별 기록·마지막 잔여 검사."""
    stats = db.mask_event_stats()
    recorded = {d["doc_id"]: d for d in db.mask_events_by_doc(limit=100_000)}
    subjects: list[dict] = []
    known_types = {t for t, *_ in MASK_POLICY}
    unknown_types: set[str] = set()
    # 유형별 집계 — 정책표 옆에 '이 유형에서 실제로 얼마나 가렸나'를 붙인다
    by_doc_type: dict[str, dict[str, int]] = {}
    for doc in _doc_index(db):
        if doc.get("doc_type") not in known_types:
            unknown_types.add(doc.get("doc_type") or "")
        if not is_masking_subject(doc.get("doc_type"), doc.get("owner")):
            continue
        rec = recorded.get(doc["id"])
        agg = by_doc_type.setdefault(doc.get("doc_type") or "", {"docs": 0, "recorded": 0, "total": 0})
        agg["docs"] += 1
        if rec is not None:
            agg["recorded"] += 1
            agg["total"] += int(rec.get("total", 0))
        subjects.append({
            "doc_id": doc["id"], "filename": doc["filename"],
            "doc_type": doc["doc_type"], "owner": doc.get("owner"),
            "status": doc.get("status"),
            "recorded": rec is not None,
            "by_type": (rec or {}).get("by_type", {}),
            "total": (rec or {}).get("total", 0),
            "contexts": (rec or {}).get("contexts", []),
            "recorded_at": (rec or {}).get("created_at"),
        })
    unrecorded = sum(1 for s in subjects if not s["recorded"] and s["status"] == "reviewed")
    return {
        "name": name,
        "linkable": linkable,
        "stats": stats,
        "subject_docs": len(subjects),
        "unrecorded": unrecorded,   # 처리는 끝났는데 기록이 없는 문서 — 기능 도입 전 처리분
        "docs": subjects[:300],
        "docs_truncated": max(0, len(subjects) - 300),
        "unknown_types": sorted(unknown_types),
        "by_doc_type": by_doc_type,
        "scan": load_json(db, SCAN_KEY),
    }
