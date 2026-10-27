"""/dev/train 데이터·학습 화면 — 일하는 칸 우선 배치·검수 현황 사유·산출물 선택(2026-10 전수 점검 3차)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app

LS_URL = "http://ls.example.test:8080"


@pytest.fixture
def client(tmp_path):
    app = create_app(
        db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(),
    )
    return TestClient(app)


def _status_line(page: str) -> str:
    return page.split('class="review-status', 1)[1].split("</p>", 1)[0]


def test_unconfigured_review_status_says_what_and_where(client):
    line = _status_line(client.get("/dev/train").text)
    assert "Label Studio 미연결(" in line and 'href="/dev/tools"' in line
    assert "recheck" not in line and "다시 확인" not in line          # 단추 대신 화면이 1분마다 다시 읽는다(10-27)


def test_review_card_lists_projects_and_refreshes_from_cache(client, monkeypatch):
    from zzaimy.dataset.ls_client import LabelStudioClient

    db = client.app.state.db
    db.set_setting("labelstudio_url", LS_URL + "/")
    db.set_setting("labelstudio_token", "test-placeholder")
    calls = []

    def progress(self, timeout=2):
        calls.append(1)
        return {"ok": True, "error": None, "projects": [{"id": 8, "title": "ZZAIMY SFT 5000 검수", "total": 5000, "done": 250}]}

    monkeypatch.setattr(LabelStudioClient, "projects_progress", progress)
    page = client.get("/dev/train").text
    assert 'data-ls-project="8"' in page and f'href="{LS_URL}/projects/8/data"' in page and ">5%<" in page
    assert "사람 검수 시작 때 생성" not in page and 'action="/dev/train/grounded-pull"' not in page
    st = client.get("/dev/train/review-status").json()
    assert st["ok"] and st["projects"][0]["pct"] == 5
    assert len(calls) == 1                                       # 30초 캐시 — 화면과 1분 갱신이 LS 를 두드리지 않게


def test_empty_project_list_is_one_line(client, monkeypatch):
    from zzaimy.dataset.ls_client import LabelStudioClient
    client.app.state.db.set_setting("labelstudio_url", LS_URL)
    client.app.state.db.set_setting("labelstudio_token", "test-placeholder")
    monkeypatch.setattr(LabelStudioClient, "projects_progress", lambda self, timeout=2: {"ok": True, "error": None, "projects": []})
    page = client.get("/dev/train").text
    assert "검수 프로젝트가 없습니다" in page and 'id="reviewProjects"' not in page


def test_outputs_name_regulation_index_and_selection_controls(client):
    page = client.get("/dev/train").text
    outputs = page.split('id="approved-data"', 1)[1].split('id="training-tools"', 1)[0]
    assert "규정 색인" in outputs and "RAG 인덱스" not in outputs
    assert "사업 문서 색인은 이 서버 DB에 있어 반출 대상 아님" in outputs
    # 체크 상자는 라벨 안 — 이름·파일 수까지 누름 영역
    for label in re.findall(r'<label class="output-selection">(.*?)</label>', outputs, re.S):
        assert "data-output-pick" in label and "output-count" in label
    assert "data-output-picked" in outputs and "data-output-submit" in outputs


def test_training_tool_cards_share_one_shape(client):
    tools = client.get("/dev/train").text.split('id="training-tools"', 1)[1].split("</section>", 1)[0]
    assert tools.count('class="tool-card"') == 2            # 품질·성능 바로가기는 사이드바와 겹쳐 뺐다(10-27)
    assert 'href="/dev/quality"' not in tools
    assert "training-tool-row" not in tools
