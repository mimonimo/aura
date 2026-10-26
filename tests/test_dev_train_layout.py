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
    return page.split('class="review-status"', 1)[1].split("</p>", 1)[0]


def test_unconfigured_review_status_says_what_and_where(client):
    line = _status_line(client.get("/dev/train").text)
    assert "검수 현황 미확인" not in line and "Label Studio 미연결(" in line      # 뜻 없는 「미확인」 칩은 뺐다(10-26)
    assert 'href="/dev/tools"' in line                          # 확인하는 곳
    assert "recheck=1" in line                                   # 다시 묻기


def test_connected_without_project_points_to_project_list(client, monkeypatch):
    from zzaimy.dataset.ls_client import LabelStudioClient

    db = client.app.state.db
    db.set_setting("labelstudio_url", LS_URL + "/")
    db.set_setting("labelstudio_token", "test-placeholder")
    calls = []

    def status(self, project):
        calls.append(project)
        return {"ok": True, "error": None, "project": project, "project_id": None,
                "total": 0, "done": 0, "pending": 0}

    monkeypatch.setattr(LabelStudioClient, "status", status)
    line = _status_line(client.get("/dev/train").text)
    assert "Label Studio 연결됨" in line and "프로젝트 없음" in line
    assert f'href="{LS_URL}/projects"' in line and 'target="_blank"' in line
    client.get("/dev/train")
    assert len(calls) == 1                                       # 평소엔 캐시
    client.get("/dev/train?tab=data&recheck=1")
    assert len(calls) == 2                                       # 다시 확인은 바로 묻는다


def test_guide_is_folded_by_default(client):
    page = client.get("/dev/train").text
    toggle = re.search(r"<button[^>]*data-guide-toggle[^>]*>(.*?)</button>", page, re.S)
    assert toggle and 'aria-expanded="false"' in toggle.group(0) and "학습 문답 구성 안내" in toggle.group(1)
    body = page.split('<div id="training-guide-body" hidden>', 1)[1]   # 기본 접힘
    assert body.index('id="training-guide"') < body.index("</section>")


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
    assert tools.count('class="tool-card"') == 3
    assert "training-tool-row" not in tools
