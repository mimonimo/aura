"""반입 점검(처리 흐름 /dev/flow#intake) — 점검 결과(scripts/175)를 읽어 보이고, 빠진 원본 목록을 내려받는다(/dev/flow/intake/missed.csv)."""
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
    r = c.get("/dev/flow")
    assert r.status_code == 200
    for text in ("처리 대상 문서 100건", "라이브러리에 들어감 90", "빠진 원본 4건", "링크/업무/a.pdf", "원본 처리 놓침 4건"):
        assert text in r.text, text
    assert 'href="/dev/flow/intake/missed.csv"' in r.text
    csv = c.get("/dev/flow/intake/missed.csv")
    assert csv.status_code == 200 and "링크/업무/a.pdf" in csv.text
    for gone in ("/dev/intake/missed.csv", "/dev/intake/failed.csv", "/dev/flow/intake/failed.csv"):   # 옛 주소·쓰지 않던 실패 목록은 지웠다(10-27)
        assert c.get(gone).status_code == 404, gone


def test_intake_page_without_audit(tmp_path):
    r = _client(tmp_path).get("/dev/flow")
    assert r.status_code == 200 and "점검 결과 없음" in r.text


def _audit(tmp_path, **over):
    base = {"at": "2031-02-03 04:05", "archive_files": 50, "targets": 20, "in_docbox": 20, "failed": 0, "waiting": 0, "missed": 0,
            "composition": {"처리 대상 문서": 20, "사진·그림": 30}, "missed_by_ext": {}, "missed_by_dir": {}, "failed_by_reason": {},
            "stuck_docs": 0, "grant_chunks": 1, "embedding_index": 1, "lexical_index": 1, "grant_docs": 1, "graph_doc_nodes": 1,
            "alerts": []}
    base.update(over)
    (tmp_path / "pipeline_audit.json").write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")


def _metric_names(page: str) -> str:
    """지표 이름 마크업(이름 + 정보 아이콘 + 숨긴 툴팁)을 「[이름]」 으로 접는다 — 문구 확인용."""
    import re
    return re.sub(r'<span class="metric"[^>]*>(.*?)<iconify-icon class="metric-i"[^>]*></iconify-icon>'
                  r'<span class="metric-tip"[^>]*>.*?</span></span>', r"[\1]", page)


def test_empty_missed_and_failed_are_one_line_not_cards(tmp_path):
    _audit(tmp_path)
    page = _client(tmp_path).get("/dev/flow").text
    assert "/intake/missed.csv" not in page and "/intake/failed.csv" not in page   # 빈 표 카드 없음
    assert "빠진 원본 예시" not in page
    names = _metric_names(page)
    assert "[빠짐 0]" in names and "[실패 0]" in names                  # 0 은 처리 대상 범례에서 한 번만
    assert "[빠진 원본]" not in names


def test_no_failed_table_on_flow_page(tmp_path):
    """실패 원본은 동기화마다 정리함 「처리 실패」 로 옮긴다(scripts/217) — 처리 흐름에 실패 표를 두지 않는다(10-26)."""
    _audit(tmp_path, failed=7, failed_by_reason={"글 없는 문서": 7})
    page = _client(tmp_path).get("/dev/flow").text
    assert "/intake/failed.csv" not in page and "글 없는 문서" not in page


def test_retry_limit_reads_constant_without_running(tmp_path):
    from zzaimy.app import intake_status
    script = tmp_path / "parse.py"
    script.write_text("import sys\nENV_RETRY_MAX = 5\nraise SystemExit('실행되면 안 됨')\n", encoding="utf-8")
    assert intake_status.retry_limit(script) == 5
    script.write_text("X = 1\n", encoding="utf-8")
    assert intake_status.retry_limit(script) is None
    assert intake_status.retry_limit(tmp_path / "없음.py") is None


def test_sync_listing_number_gets_its_own_name(tmp_path):
    from zzaimy.app import intake_status
    assert intake_status.dgx_listing({"full": {"summary": "원본 1234 · 새로 2 · 바뀜 0"}}) == 1234
    assert intake_status.dgx_listing({"full": {"summary": "목록 실패"}}) is None and intake_status.dgx_listing({}) is None
    _audit(tmp_path)
    (tmp_path / "sync_status.json").write_text(json.dumps({"full": {"at": "2031-02-03 04:00", "summary": "원본 4321 · 새로 1"}},
                                                          ensure_ascii=False), encoding="utf-8")
    page = _client(tmp_path).get("/dev/flow").text
    assert "DGX 목록 4,321" in page and "원본 4321" not in page            # 반입 점검 머리 한 줄(「최근 실행」 표는 뺐다, 10-26)
    assert "원본 장부의 파일 전부" in page and "학교 원본 폴더의 파일" in page     # 장부 전체와 학교 원본은 이름이 다르다


def test_audit_reason_treats_empty_state_as_no_text():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "scripts" / "175_pipeline_audit.py"
    spec = importlib.util.spec_from_file_location("audit175", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.reason("", "empty") == "글 없는 문서"
    assert mod.reason("", "failed") == "기타 판독 실패"
    assert mod.reason("암호가 걸린 문서", "empty") == "암호 문서"
