"""조직 관리 화면(/team, ADR-0073) — 이름(사이드바·제목), 옛 주소(?tab=org)도 같은 화면, 트리 자료의 구성원 범위(team),
이름에 든 따옴표. 사람·부서는 모두 가짜 값."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_org_chart import _no_google, org  # noqa: F401  (픽스처 재사용)
from tests.test_staff_grades import _client
from zzaimy.app import rag_spaces

STATIC = Path(__file__).resolve().parents[1] / "src" / "zzaimy" / "app" / "static"


def test_labels_and_old_addresses(org):
    app, dev, _ = org
    page = dev.get("/team").text
    assert "<title>조직 관리" in page and 'id="orgAdmin"' in page and "org-chart.js" in page
    assert ">구성원</a>" not in page and "data-od-view" not in page                 # 옛 탭·다이어그램은 없다
    for who in ("boss", "kim"):
        side = re.sub(r"\s*<iconify-icon[^>]*></iconify-icon>", ">", _client(app, who).get("/chat").text)
        assert ">조직 관리</a>" in side and "조직·구성원" not in side
    kim = _client(app, "kim")
    assert 'id="orgAdmin"' in kim.get("/team?tab=org").text                      # 옛 주소도 같은 화면
    assert 'data-start-dept="예산팀"' in dev.get("/team", params={"dept": "예산팀"}).text


def test_member_scope_in_tree(org):
    app, dev, _ = org
    nodes = {n["name"]: n for n in _client(app, "boss").get("/api/org").json()["nodes"]}
    assert nodes["기획처"]["team"] and nodes["기획팀"]["team"] and not nodes["학생지원팀"]["team"]
    lee = {n["name"]: n for n in _client(app, "lee").get("/api/org").json()["nodes"]}
    assert lee["학생지원팀"]["team"] and not lee["기획처"]["team"]                   # 사업 책임자는 자기 부서만
    kim = {n["name"]: n for n in _client(app, "kim").get("/api/org").json()["nodes"]}
    assert not any(n["team"] for n in kim.values())


def test_names_with_quotes_stay_data(org):
    app, dev, data_dir = org
    cfg = rag_spaces.config(data_dir)
    cfg, err = rag_spaces.create_dept_rag(cfg, "따옴\"표'팀")
    if err:
        pytest.skip("부서 이름 규칙이 따옴표를 받지 않음")
    rag_spaces.save_config(cfg, data_dir)
    page = dev.get("/team", params={"dept": "따옴\"표'팀"}).text
    assert "따옴\"표'팀" not in page and "따옴&#34;표&#39;팀" in page


def test_org_chart_js_safe_and_complete():
    src = (STATIC / "org-chart.js").read_text(encoding="utf-8")
    assert "innerHTML" not in src and "insertAdjacentHTML" not in src         # 글자는 textContent 로만
    assert "CSS.escape" in src and "encodeURIComponent(n.name)" in src
    for word in ("전체 사용자", "구성원", "부서 설정", "이름 바꾸기", "상위 부서 옮기기", "하위 부서 추가", "지우기", "총괄 책임자 정하기",
                 "사람 추가", "등급 바꾸기", "계정 추가", "선택 해제"):
        assert word in src
    assert "location.reload" not in src and "draggable" in src
