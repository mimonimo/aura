"""도구·모델 연결(/dev/tools)·개인정보 점검(/dev/pii)·품질(/dev/quality) 화면 정리의 재발 방지."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import pii_audit
from zzaimy.app.main import create_app
from zzaimy.eval import retrieval_eval as rev


@pytest.fixture
def client(tmp_path):
    app = create_app(
        db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(),
    )
    return TestClient(app)


def _active_nav(page: str) -> list[str]:
    return re.findall(r'<a[^>]*href="([^"]+)"[^>]*aria-current="page"', page)


# ---- 도구·모델 연결 ----

def test_tools_has_own_address_and_sidebar_link(client):
    r = client.get("/dev/tools")
    assert r.status_code == 200
    assert "<title>도구·모델 연결" in r.text
    assert _active_nav(r.text) == ["/dev/tools"]               # 사이드바에서 이 화면만 고른 상태
    assert 'href="/dev/train?tab=settings"' not in r.text
    assert _active_nav(client.get("/dev/train").text) == ["/dev/train"]
    # 옛 주소는 안내 문구를 들고 새 주소로
    old = client.get("/dev/train?tab=settings&ok=저장", follow_redirects=False)
    assert old.status_code == 303 and old.headers["location"].startswith("/dev/tools?ok=")
    assert client.get("/dev/train?tab=settings", follow_redirects=False).headers["location"] == "/dev/tools"


def test_tools_holds_external_connections(client, monkeypatch):
    from zzaimy.app import subscription_status

    page = client.get("/dev/tools").text
    assert "외부 연결" in page and "웹 검색" in page and "외부 참조 AI · 구독 연결 확인" in page
    monkeypatch.setattr(subscription_status, "probe",
                        lambda p: {"provider": p, "installed": False, "state": "missing"})
    r = client.post("/dev/pii/subscriptions/check", follow_redirects=False)
    assert r.headers["location"].startswith("/dev/tools")
    page = client.get("/dev/tools").text
    assert "CLI 미설치" in page
    # 시각은 원 ISO 문자열이 아니라 한국시간 「YYYY-MM-DD HH:MM」
    assert re.search(r"마지막 확인 \d{4}-\d{2}-\d{2} \d{2}:\d{2} ·", page)
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", page)
    # 옛 탭 주소도 새 화면으로
    assert client.get("/dev/pii?view=external", follow_redirects=False).headers["location"].startswith("/dev/tools")


def test_tools_folds_internal_addresses(client, monkeypatch, tmp_path):
    from zzaimy.app import main as app_main
    from zzaimy.generate import llm_connections as lc, model_config

    lc.configure(tmp_path / "llm.json"); model_config.set_override("", ""); model_config.reset_status_cache()
    lc.add("교내 DGX", "vllm", "http://10.9.8.7:11434/v1", "", "")
    monkeypatch.setattr(app_main, "_live_models_cached", lambda cid, ttl=0: {"ok": True, "models": [], "error": ""})
    page = client.get("/dev/tools").text
    row = page.split('class="srv-row"', 1)[1].split("</div>", 1)[0]
    assert 'data-addr hidden>http://10.9.8.7:11434/v1' in row          # 주소는 접힌 칸에만
    assert "data-addr-toggle" in page and "연결 주소 보기" in page
    assert "<details" not in page and "▶" not in page
    lc.configure(tmp_path / "none.json"); model_config.set_override("", ""); model_config.reset_status_cache()


# ---- 개인정보 점검 ----

def test_pii_tab_bar_comes_first_on_every_tab(client):
    for view in ("policy", "checks", "history"):
        page = client.get(f"/dev/pii?view={view}").text
        body = page.split('<div class="privacy-page">', 1)[1]
        assert body.lstrip().startswith('<nav class="privacy-nav"'), view
        assert "외부 연결" not in body.split("</nav>", 1)[0]


def test_pii_checks_use_korean_detector_labels_and_context(client):
    db = client.app.state.db
    db.set_setting(pii_audit.SCAN_KEY, json.dumps({
        "at": "2026-10-13T04:47:00+00:00", "detectors": ["KR_RRN", "KR_BRN", "KR_STUDENT_ID"],
        "scanned": {"docs": 1}, "hits": 2, "by_type": {"KR_BRN": 2}, "docs": [], "excluded_docs": 0,
        "elapsed_ms": 10,
    }))
    page = client.get("/dev/pii?view=checks").text
    assert "탐지 항목: 주민등록번호, 사업자등록번호, 학번·수험번호" in page
    assert ">KR_BRN" not in page and "탐지기:" not in page
    assert "2026-10-13 13:47" in page
    assert "scan-context" in page and "오탐" in page


def test_pii_history_empty_state_is_plain(client):
    page = client.get("/dev/pii?view=history").text
    assert "가리기 기록 없음" in page
    assert "보증하지 않습니다" not in page


# ---- 품질·성능 ----

def _write_eval(tmp_path, embedding: str, n_chunks: int):
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir(exist_ok=True)
    (eval_dir / "retrieval-latest.json").write_text(json.dumps({
        "schema": 1, "measured_at": "2026-09-29T10:00:00+09:00", "n_queries": 8, "n_chunks": n_chunks,
        "embedding_model": embedding,
        "rows": [{"method": "하이브리드", "production": True, "n": 8, "recall_at_1": 0.5,
                  "recall_at_5": 0.75, "recall_at_10": 1.0, "mrr_at_10": 0.625, "note": None}],
        "notes": [],
    }, ensure_ascii=False), encoding="utf-8")


def test_quality_folds_measurement_from_other_config(client, tmp_path, monkeypatch):
    from zzaimy.app import embed_search

    monkeypatch.setattr(rev, "QUERIES_PATH", tmp_path / "없음.jsonl")
    monkeypatch.setattr(rev, "LOG_PATH", tmp_path / "eval.log")
    monkeypatch.setattr(embed_search, "_index_model_name", lambda: "zzaimy-embed-v2")
    _write_eval(tmp_path, "KURE-v1", 539)
    page = client.get("/dev/quality").text
    assert "현재 구성 측정 없음" in page and "지난 측정(구성 다름)" in page
    top = page.split("현재 구성 측정 없음", 1)[0]
    assert "0.625" not in top                                   # 옛 수치는 위에 내걸지 않는다
    assert "0.625" in page.split("지난 측정(구성 다름)", 1)[1]   # 접힌 칸 안에만
    assert "문답 학습 데이터 품질" not in page


def test_quality_same_config_shows_numbers_plainly(client, tmp_path, monkeypatch):
    from zzaimy.app import embed_search

    monkeypatch.setattr(rev, "QUERIES_PATH", tmp_path / "없음.jsonl")
    monkeypatch.setattr(rev, "LOG_PATH", tmp_path / "eval.log")
    monkeypatch.setattr(embed_search, "_index_model_name", lambda: "KURE-v1")
    _write_eval(tmp_path, "KURE-v1", 539)
    page = client.get("/dev/quality").text
    assert "0.625" in page and "지난 측정(구성 다름)" not in page and "현재 구성 측정 없음" not in page
