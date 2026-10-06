"""작업 현황 「핵심 기술」 — RAG·지식그래프·온톨로지 부품과 판·라이선스가 보인다."""
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import tech_stack
from zzaimy.app.main import create_app


def test_snapshot_reads_installed_versions():
    s = tech_stack.snapshot()
    assert {"RAG", "지식그래프", "온톨로지", "판독"} <= set(s["areas"])
    kiwi = next(r for r in s["areas"]["RAG"] if r["name"].startswith("Kiwi"))
    assert kiwi["version"] and kiwi["source"] == "이 서버 설치본"
    cy = next(r for r in s["areas"]["지식그래프"] if r["name"] == "Cytoscape.js")
    assert cy["version"] == "3.30.4" and cy["license"] == "MIT"


def test_dev_page_has_tech_tab(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter())
    page = TestClient(app).get("/dev").text
    assert "핵심 기술" in page and "Cytoscape.js" in page and "kordoc" in page and "MinerU" in page
