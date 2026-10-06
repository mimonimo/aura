"""반입 연동 관리(/dev/intake) — 점검 결과(scripts/175)를 읽어 보이고, 목록을 내려받는다."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app


def _client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter())
    return TestClient(app)


def test_intake_page_shows_audit_and_lists(tmp_path):
    (tmp_path / "pipeline_audit.json").write_text(json.dumps({
        "at": "2026-10-06 10:00", "archive_files": 200, "targets": 100, "in_docbox": 90, "failed": 5, "waiting": 1, "missed": 4,
        "composition": {"사진·그림": 80, "처리 대상 문서": 100}, "missed_by_ext": {"pdf": 4}, "missed_by_dir": {"링크/업무": 4},
        "failed_by_reason": {"암호 문서": 5}, "stuck_docs": 0, "grant_chunks": 10, "embedding_index": 10, "lexical_index": 10,
        "grant_docs": 90, "graph_doc_nodes": 90, "alerts": ["원본 처리 놓침 4건"]}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "pipeline_missed.jsonl").write_text(json.dumps({"rel": "링크/업무/a.pdf", "ext": "pdf", "seen_at": "2026-10-05 01:00"},
                                                               ensure_ascii=False) + "\n", encoding="utf-8")
    c = _client(tmp_path)
    r = c.get("/dev/intake")
    assert r.status_code == 200
    for text in ("처리 대상 문서 100건", "문서함 90", "놓친 원본 4건", "암호 문서", "링크/업무/a.pdf", "원본 처리 놓침 4건"):
        assert text in r.text, text
    csv = c.get("/dev/intake/missed.csv")
    assert csv.status_code == 200 and "링크/업무/a.pdf" in csv.text
    assert c.get("/dev/intake/nope.csv").status_code == 404


def test_intake_page_without_audit(tmp_path):
    r = _client(tmp_path).get("/dev/intake")
    assert r.status_code == 200 and "점검 결과 없음" in r.text
