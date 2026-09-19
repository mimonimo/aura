"""개인정보 마스킹 감사 테스트 — 기록·자가 점검·잔여 검사 (절대 규칙 3).

★ 데이터는 전부 합성이다. 실제 개인정보를 절대 쓰지 않는다.
마스커(presidio)를 못 불러오는 환경에서는 실제 마스킹이 필요한 테스트를
건너뛰고, '로드 실패' 상태가 500 없이 화면에 남는지만 본다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zzaimy.app import pii_audit
from zzaimy.app.db import Database
from zzaimy.app.main import create_app
from zzaimy.ingest.pii import MaskEvent

SYN_PHONE = "010-0000-0000"
SYN_EMAIL = "test@example.com"
SYN_RRN = "990101-1234563"   # 체크섬만 맞춘 가공 번호 (tests/test_pii.py와 동일)


class FakeProcessor:
    """실제 파이프라인 대신 즉시 완료 처리 (tests/test_app.py와 같은 골격)."""

    def process(self, db: Database, doc_id: int, file_path: Path) -> None:
        db.update_document(doc_id, status="reviewed", masked_text="합성 마스킹 본문",
                           series="proposal", ai_review="합성 검토 의견")

    def reprocess(self, db: Database, doc_id: int) -> None:
        db.update_document(doc_id, status="reviewed")

    def extract_text(self, file_path: Path) -> str:
        return "합성 첨부 본문"

    def analyze(self, db: Database, doc_id: int) -> None:
        db.update_document(doc_id, ai_review="합성 맥락 분석")


class FakeDrafter:
    def generate(self, db: Database, doc_id: int) -> None:
        db.update_document(doc_id, draft="합성 초안")


@pytest.fixture()
def client(tmp_path):
    app = create_app(
        db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(),
    )
    return TestClient(app)


@pytest.fixture(scope="module")
def masker():
    """실제 마스커. 못 불러오면 이 픽스처를 쓰는 테스트는 건너뛴다."""
    try:
        m, raw_cls, supported = pii_audit._load_masker()
    except Exception as exc:  # noqa: BLE001 — 의존성 부재는 스킵 사유
        pytest.skip(f"마스커 로드 불가: {exc}")
    return m, raw_cls, supported


def _dump(db: Database) -> str:
    """DB에 실제로 적힌 것 전부를 문자열로 — 원문 값 유출 검사용."""
    with db._conn() as conn:  # noqa: SLF001
        return "\n".join(
            json.dumps(dict(r), ensure_ascii=False)
            for t in ("mask_events", "settings")
            for r in conn.execute(f"SELECT * FROM {t}").fetchall()
        )


# --- 1. 기록 ---


def test_mask_event_rows_fold_counts_and_take_context_from_masked_text():
    text = f"연락처: {SYN_PHONE}, 메일 {SYN_EMAIL}, 다시 {SYN_PHONE} 끝"
    events = [
        MaskEvent("d", "KR_PHONE", text.find(SYN_PHONE), text.find(SYN_PHONE) + len(SYN_PHONE)),
        MaskEvent("d", "EMAIL", text.find(SYN_EMAIL), text.find(SYN_EMAIL) + len(SYN_EMAIL)),
        MaskEvent("d", "KR_PHONE", text.rfind(SYN_PHONE), text.rfind(SYN_PHONE) + len(SYN_PHONE)),
    ]
    masked = text.replace(SYN_PHONE, "[KR_PHONE]").replace(SYN_EMAIL, "[EMAIL]")

    rows = pii_audit.mask_event_rows(masked, events)

    assert [(r["entity_type"], r["n"]) for r in rows] == [("KR_PHONE", 2), ("EMAIL", 1)]
    for r in rows:
        assert f"[{r['entity_type']}]" in r["context"]
        assert len(r["context"]) <= pii_audit.CONTEXT_CHARS
        assert SYN_PHONE not in r["context"] and SYN_EMAIL not in r["context"]


def test_mask_event_rows_drop_context_when_coordinates_do_not_match():
    events = [MaskEvent("d", "KR_PHONE", 0, len(SYN_PHONE))]
    rows = pii_audit.mask_event_rows("전혀 다른 본문", events)
    assert rows == [{"entity_type": "KR_PHONE", "n": 1, "context": None}]


def test_replace_mask_events_persists_counts_only(tmp_path):
    db = Database(tmp_path / "t.db")
    d1 = db.add_document("a.pdf", "/tmp/a.pdf", doc_type="recruit")
    d2 = db.add_document("b.pdf", "/tmp/b.pdf", doc_type="grant")
    db.replace_mask_events(d1, [
        {"entity_type": "KR_PHONE", "n": 2, "context": "연락처: [KR_PHONE], 메일"},
        {"entity_type": "EMAIL", "n": 1, "context": None},
    ])
    db.replace_mask_events(d2, [])   # 돌았지만 0건

    stats = db.mask_event_stats()
    assert stats["docs"] == 2 and stats["total"] == 3
    assert stats["by_type"] == {"KR_PHONE": 2, "EMAIL": 1}
    assert stats["last_at"]

    assert [(e["entity_type"], e["n"]) for e in db.list_mask_events(d2)] == [("NONE", 0)]
    by_doc = {d["doc_id"]: d for d in db.mask_events_by_doc()}
    assert by_doc[d1]["by_type"] == {"KR_PHONE": 2, "EMAIL": 1}
    assert by_doc[d1]["contexts"] == [{"entity_type": "KR_PHONE", "context": "연락처: [KR_PHONE], 메일"}]
    assert by_doc[d2]["total"] == 0 and by_doc[d2]["by_type"] == {}

    # 재처리 시 교체 — 누적되지 않는다
    db.replace_mask_events(d1, [{"entity_type": "KR_RRN", "n": 1, "context": None}])
    assert db.mask_event_stats()["by_type"] == {"KR_RRN": 1}


def test_record_mask_events_with_real_masker_stores_no_original_values(tmp_path, masker):
    m, raw_cls, _ = masker
    db = Database(tmp_path / "t.db")
    doc_id = db.add_document("c.pdf", "/tmp/c.pdf", doc_type="recruit")
    text = f"성명: 홍길동 / 연락처 {SYN_PHONE} / 주민등록번호 {SYN_RRN} / {SYN_EMAIL}"
    out, events = m.mask(raw_cls(doc_id=str(doc_id), text=text))

    rows = pii_audit.record_mask_events(db, doc_id, out.text, events)

    assert {r["entity_type"] for r in rows} == {"KR_NAME", "KR_PHONE", "KR_RRN", "EMAIL"}
    stored = _dump(db)
    for secret in ("홍길동", SYN_PHONE, SYN_RRN, SYN_EMAIL):
        assert secret not in stored
    assert "[KR_PHONE]" in stored


# --- 2. 자가 점검 ---


def test_selftest_passes_for_every_supported_type(tmp_path, masker):
    _, _, supported = masker
    db = Database(tmp_path / "t.db")

    result = pii_audit.run_selftest(db)

    assert result["error"] is None
    assert result["ok"] is True
    assert result["passed"] == result["total"] == len(result["checks"])
    assert result["uncovered"] == []
    covered = {c["entity_type"] for c in result["checks"] if c["kind"] == "mask"}
    assert covered == set(supported)
    assert {c["kind"] for c in result["checks"]} == {"mask", "keep", "combined"}
    for c in result["checks"]:
        assert set(c) >= {"kind", "entity_type", "label", "passed", "detail", "output"}
    # settings에 같은 결과가 남는다
    assert pii_audit.load_json(db, pii_audit.SELFTEST_KEY)["at"] == result["at"]


def test_selftest_reports_masker_load_failure_instead_of_raising(tmp_path, monkeypatch):
    def boom():
        raise ImportError("presidio_analyzer 없음")

    monkeypatch.setattr(pii_audit, "_load_masker", boom)
    db = Database(tmp_path / "t.db")

    result = pii_audit.run_selftest(db)

    assert result["error"].startswith("마스커 로드 실패: ImportError")
    assert result["ok"] is False and result["checks"] == []
    assert pii_audit.load_json(db, pii_audit.SELFTEST_KEY)["error"] == result["error"]


# --- 3. 잔여 검사 ---


def test_scan_finds_planted_patterns_and_redacts_them(tmp_path, masker):
    db = Database(tmp_path / "t.db")
    doc_id = db.add_document("leak.pdf", "/tmp/leak.pdf", doc_type="recruit")
    db.update_document(doc_id, status="reviewed",
                       masked_text=f"연락처 {SYN_PHONE} 메일 {SYN_EMAIL}")
    db.replace_doc_chunks(doc_id, [{"kind": "text", "content": f"주민등록번호 {SYN_RRN}"}])

    result = pii_audit.run_scan(db)

    assert result["error"] is None
    assert result["hits"] == 3
    assert result["by_type"] == {"KR_PHONE": 1, "EMAIL": 1, "KR_RRN": 1}
    assert result["scanned"]["docs"] == 1 and result["scanned"]["doc_chunks"] == 1
    (d,) = result["docs"]
    assert d["doc_id"] == doc_id and sorted(d["where"]) == ["doc_chunks", "masked_text"]
    assert d["by_type"] == {"KR_PHONE": 1, "EMAIL": 1, "KR_RRN": 1}
    contexts = " ".join(s["context"] for s in d["samples"])
    assert "010-****-****" in contexts
    serialized = json.dumps(result, ensure_ascii=False)
    for secret in (SYN_PHONE, SYN_EMAIL, SYN_RRN):
        assert secret not in serialized
    assert SYN_PHONE not in _dump(db)


def test_scan_reports_zero_when_text_is_masked(tmp_path, masker):
    db = Database(tmp_path / "t.db")
    doc_id = db.add_document("clean.pdf", "/tmp/clean.pdf", doc_type="grant")
    db.update_document(doc_id, status="reviewed",
                       masked_text="연락처 [KR_PHONE] 메일 [EMAIL] 기한 2026-09-01")

    result = pii_audit.run_scan(db)

    assert result["hits"] == 0 and result["docs"] == []
    assert result["scanned"] == {"docs": 1, "masked_text": 1, "doc_chunks": 0,
                                 "regulation_chunks": 0, "chars": len("연락처 [KR_PHONE] 메일 [EMAIL] 기한 2026-09-01")}


def test_scan_follows_masking_policy_for_regulation_docs(tmp_path, masker):
    """기준 문서는 원문 유지(ADR-0006)라 검사에서 빼고, 코퍼스 반입분은 본다."""
    from zzaimy.app.regulations import RegulationChunk

    db = Database(tmp_path / "t.db")
    reg = db.add_document("규정.pdf", "/tmp/r.pdf", doc_type="regulation")
    db.add_regulation_chunks(
        reg, "규정", [RegulationChunk(heading="제1조", content=f"문의 {SYN_PHONE}")]
    )
    corpus = db.add_document("공고.pdf", "/tmp/c.pdf", doc_type="regulation", owner="corpus")
    db.add_regulation_chunks(
        corpus, "공고", [RegulationChunk(heading="1.", content=f"담당 {SYN_PHONE}")]
    )

    result = pii_audit.run_scan(db)

    assert result["excluded_docs"] == 1
    assert result["hits"] == 1
    assert [d["doc_id"] for d in result["docs"]] == [corpus]
    assert pii_audit.is_masking_subject("regulation", "zzaimy") is False
    assert pii_audit.is_masking_subject("regulation", "corpus") is True
    assert pii_audit.is_masking_subject("recruit", "zzaimy") is True


def test_scan_reports_detector_load_failure(tmp_path, monkeypatch):
    def boom():
        raise ImportError("regex 없음")

    monkeypatch.setattr(pii_audit, "_load_detectors", boom)
    result = pii_audit.run_scan(Database(tmp_path / "t.db"))
    assert result["error"].startswith("탐지기 로드 실패")
    assert result["hits"] == 0


def test_redact_value_keeps_length_and_hides_most():
    assert pii_audit.redact_value("010-1234-5678") == "010-****-****"
    assert pii_audit.redact_value("홍길동") == "홍**"
    assert pii_audit.redact_value("gil@example.com") == "gil@*******.***"
    assert len(pii_audit.redact_value(SYN_RRN)) == len(SYN_RRN)


# --- 4. 화면 ---


def test_dev_pii_page_actions_and_shortcut(client):
    r = client.get("/dev/pii")
    assert r.status_code == 200
    assert "자가 점검" in r.text and "잔여 검사" in r.text
    assert "원문 유지" in r.text and "ADR-0006" in r.text
    assert "아직 실행하지 않음" in r.text

    r = client.post("/dev/pii/selftest", follow_redirects=False)
    assert r.status_code == 303
    r = client.post("/dev/pii/scan", follow_redirects=False)
    assert r.status_code == 303

    page = client.get("/dev/pii").text
    selftest = pii_audit.load_json(client.app.state.db, pii_audit.SELFTEST_KEY)
    if selftest["error"]:
        assert "마스커 로드 실패" in page
    else:
        assert "마지막 자가 점검" in page
        assert f"통과 {selftest['passed']}/{selftest['total']}" in page
    scan = pii_audit.load_json(client.app.state.db, pii_audit.SCAN_KEY)
    if scan["error"]:
        assert "탐지기 로드 실패" in page
    else:
        assert "잔여 개인정보 없음(정규식 기준)" in page


def test_dev_dashboard_has_pii_shortcut(client):
    assert 'href="/dev/pii"' in client.get("/dev").text


def test_dev_pii_page_shows_load_failure_without_500(client, monkeypatch):
    def boom():
        raise RuntimeError("spacy 모델 없음")

    monkeypatch.setattr(pii_audit, "_load_masker", boom)
    r = client.post("/dev/pii/selftest", follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/dev/pii")
    assert page.status_code == 200
    assert "마스커 로드 실패: RuntimeError: spacy 모델 없음" in page.text


def test_dev_pii_page_lists_recorded_documents_and_corpus_db(client, tmp_path):
    db = client.app.state.db
    doc_id = db.add_document("접수.pdf", "/tmp/x.pdf", doc_type="recruit")
    db.update_document(doc_id, status="reviewed", masked_text="연락처 [KR_PHONE]")
    db.replace_mask_events(doc_id, [
        {"entity_type": "KR_PHONE", "n": 3, "context": "연락처 [KR_PHONE]"},
    ])
    old = db.add_document("옛문서.pdf", "/tmp/y.pdf", doc_type="grant")
    db.update_document(old, status="reviewed", masked_text="기록 없는 옛 문서")
    reg = db.add_document("규정.pdf", "/tmp/r.pdf", doc_type="regulation")
    db.update_document(reg, status="reviewed", masked_text="원문 유지")

    # 플랫폼 DB 옆의 코퍼스 파일럿 DB는 두 번째 원천으로 뜬다
    cdb = Database(tmp_path / "corpus_pilot.db")
    c_id = cdb.add_document("공고.pdf", "/tmp/c.pdf", doc_type="regulation", owner="corpus")
    cdb.replace_mask_events(c_id, [{"entity_type": "EMAIL", "n": 1, "context": "[EMAIL]"}])

    page = client.get("/dev/pii").text
    assert f'href="/doc/{doc_id}"' in page and "전화번호 3" in page
    assert "기록이 없는 문서 1건" in page
    assert "규정.pdf" not in page          # 기준 문서는 마스킹 대상 표에 없다
    assert "국고 코퍼스 (별도 DB)" in page and "공고.pdf" in page
    assert "이메일 1" in page


# --- 5. 파이프라인 연결 — 접수·문서 추출 경로가 실제로 기록을 남기는가 ---


def _processor(monkeypatch):
    from zzaimy.app import regulations
    from zzaimy.app.pipeline import DocumentProcessor

    monkeypatch.setattr(DocumentProcessor, "_review", lambda self, text, doc_type: "합성 의견")
    monkeypatch.setattr(DocumentProcessor, "_correct_texts", lambda self, texts: None)
    monkeypatch.setattr(DocumentProcessor, "_pdf_to_images", lambda self, p, max_pages=4: [])
    monkeypatch.setattr(regulations, "compose_review_context", lambda *a, **k: "")
    monkeypatch.setattr(regulations, "suggest_criteria_docs", lambda *a, **k: [])
    return DocumentProcessor()


@pytest.mark.parametrize("doc_type", ["recruit", "ocr"])
def test_pipeline_records_mask_events(tmp_path, monkeypatch, masker, doc_type):
    db = Database(tmp_path / "t.db")
    src = tmp_path / "입력.txt"
    src.write_text(f"지원자 연락처 {SYN_PHONE}\n메일 {SYN_EMAIL}\n", encoding="utf-8")
    doc_id = db.add_document("입력.txt", str(src), doc_type=doc_type)

    _processor(monkeypatch).process(db, doc_id, src)

    doc = db.get_document(doc_id)
    assert doc["status"] == "reviewed", doc["error"]
    assert SYN_PHONE not in doc["masked_text"] and "[KR_PHONE]" in doc["masked_text"]
    events = {e["entity_type"]: e["n"] for e in db.list_mask_events(doc_id)}
    assert events == {"KR_PHONE": 1, "EMAIL": 1}
    assert SYN_PHONE not in _dump(db) and SYN_EMAIL not in _dump(db)
