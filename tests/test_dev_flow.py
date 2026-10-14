"""처리 흐름 화면 — 단계·낱말·장비가 모두 그려지고, 산출물이 없으면 수치는 「—」 로 둔다(지어낸 수치 없음)."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import metrics, process_flow
from zzaimy.app.main import create_app


def _client(tmp_path):
    metrics._cache.update(at=0.0, path=None, data=None)
    app = create_app(db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter())
    return app, TestClient(app)


def test_flow_page_lists_every_stage_term_and_host(tmp_path):
    _, c = _client(tmp_path)
    page = c.get("/dev/flow").text
    for s in process_flow.STAGES:
        assert s["name"] in page and f'id="stage-{s["n"]}"' in page
    for t in process_flow.TERMS:
        assert t["name"] in page
    for h in process_flow.HOSTS:
        assert h["name"] in page
    assert [s["n"] for s in process_flow.STAGES] == list(range(1, len(process_flow.STAGES) + 1))


def test_flow_numbers_come_from_outputs(tmp_path):
    app, c = _client(tmp_path)
    data_dir = tmp_path
    snap = process_flow.snapshot(app.state.db)
    assert snap["vals"]["shacl_violations"] == "—" and snap["vals"]["reg_r1"] == "—"
    (data_dir / "ontology").mkdir()
    (data_dir / "ontology" / "shacl_report.json").write_text(json.dumps({"n_violations": 0, "n_warnings": 108}))
    snap = process_flow.snapshot(app.state.db)
    assert snap["vals"]["shacl_violations"] == "0" and snap["vals"]["shacl_warnings"] == "108"


def test_overview_links_to_flow(tmp_path):
    _, c = _client(tmp_path)
    assert 'href="/dev/flow"' in c.get("/dev").text


def test_compare_keeps_latest_complete_run(tmp_path):
    """관계 질문만 다시 잰 더 최근 측정이 있어도, 비교 표는 세 갈래를 다 잰 가장 최근 측정을 쓴다."""
    import os
    import time
    from zzaimy.app import answer_flow
    ev = tmp_path / "eval"
    ev.mkdir()
    full = {"summary": {"fulltext": {"search": {"section": {"n": 73, "counts": {"doc_hit": {"k": 13, "n": 73, "rate": 0.18}}},
                                                "relation": {"n": 75}}, "answer": {"n": 36}}}}
    part = {"summary": {"fulltext": {"search": {"section": {"n": 0}, "relation": {"n": 75}}}}}
    (ev / "graphrag_vs_fulltext_full.json").write_text(json.dumps(full))
    (ev / "graphrag_vs_fulltext_part.json").write_text(json.dumps(part))
    later = time.time() + 10
    os.utime(ev / "graphrag_vs_fulltext_part.json", (later, later))
    got = answer_flow.compare_rows(ev)
    assert got["file"] == "graphrag_vs_fulltext_full.json" and got["n_sec"] == 73
