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
