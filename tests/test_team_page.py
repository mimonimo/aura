"""조직·구성원 화면(/team) — 이름(조직·구성원 / 조직도·구성원 탭), 구성원 탭의 부서 고르기(처·단 묶음·내 부서·찾기),
조직도 탭의 다이어그램 틀과 「구성원 보기」 범위. 사람·부서는 모두 가짜 값."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.test_org_chart import _no_google, org  # noqa: F401  (픽스처 재사용)
from tests.test_staff_grades import _add, _client
from zzaimy.app import dept_dictionary, rag_spaces

STATIC = Path(__file__).resolve().parents[1] / "src" / "zzaimy" / "app" / "static"


def test_labels_renamed(org):
    app, dev, _ = org
    page = dev.get("/team").text
    assert "<title>" in page and "조직·구성원" in page and ">구성원</a>" in page and ">조직도</a>" in page
    assert 'aria-label="조직·구성원 화면"' in page and ">부서 사람</a>" not in page   # 탭·사이드바 이름이 바뀌었다
    side = re.sub(r"\s*<iconify-icon[^>]*></iconify-icon>", ">", _client(app, "boss").get("/chat").text)
    assert ">조직·구성원</a>" in side and "조직도와 구성원 등급·권한" in side          # 사이드바(책임자)
    kim = _client(app, "kim")                                                   # 일반 — 조직도만
    org_page = kim.get("/team?tab=org").text
    assert ">구성원</a>" not in org_page and "조직도" in org_page


def test_dept_picker_grouped_not_select(org):
    app, dev, _ = org
    page = dev.get("/team", params={"dept": "예산팀"}).text
    assert '<select name="dept" aria-label="부서"' not in page and "data-tm-dp" in page and "부서 바꾸기" in page
    # 처·단 묶음 — 기획처(고를 수 있음) 아래 기획팀·예산팀이 들여 쓰기로, 위가 없는 부서는 마지막 묶음
    groups = page.split("data-tm-dp-group>")[1:]
    first = groups[0]
    assert 'class="top"' in first and ">기획처<" in first
    assert first.index("기획팀") < first.index("예산팀") and first.count('class="kid"') == 2
    assert "상위 없는 부서" in groups[-1] and "천마빌리지센터" in groups[-1]
    assert 'aria-current="true"' in page.split("data-tm-dp-group>")[1] and "<b>예산팀</b><small>기획처</small>" in page
    assert '/team?dept=%EC%98%88%EC%82%B0%ED%8C%80' in page                    # 링크는 주소 꼴로
    assert "data-tm-dp-recent" in page and "localStorage" in page               # 최근에 본 부서(브라우저에만)


def test_dept_picker_my_depts_and_scope(org, tmp_path):
    app, _, data_dir = org
    _add(app, "multi", dept="기획팀", grade="head")
    app.state.accounts["multi"]["dept_grades"] = {"학생지원팀": "head"}
    app.state.save_accounts()
    page = _client(app, "multi").get("/team").text
    quick = page.split("data-tm-dp-quick>")[1].split("</div>")[0]
    assert "내 부서" in quick and "기획팀" in quick and "학생지원팀" in quick
    assert "천마빌리지센터" not in page                                          # 맡지 않은 부서는 목록에 없다


def test_dept_picker_escapes_names(org):
    app, dev, data_dir = org
    cfg = rag_spaces.config(data_dir)
    cfg, err = rag_spaces.create_dept_rag(cfg, "따옴\"표'팀")
    if err:
        pytest.skip("부서 이름 규칙이 따옴표를 받지 않음")
    rag_spaces.save_config(cfg, data_dir)
    page = dev.get("/team").text
    assert "따옴\"표'팀" not in page and "따옴&#34;표&#39;팀" in page


def test_org_tab_diagram_shell_and_team_scope(org):
    app, dev, _ = org
    page = dev.get("/team?tab=org").text
    for hook in ("data-od-view", "data-od-stage", 'data-oc-zoom="fit"', "data-oc-foldall"):
        assert hook in page
    scope = json.loads(re.search(r"data-team='([^']*)'", page).group(1))
    assert scope == {"all": True, "depts": []}
    boss = _client(app, "boss").get("/team?tab=org").text
    s2 = json.loads(re.search(r"data-team='([^']*)'", boss).group(1))
    assert not s2["all"] and "기획처" in s2["depts"] and "학생지원팀" not in s2["depts"]
    kim = _client(app, "kim").get("/team?tab=org").text
    assert json.loads(re.search(r"data-team='([^']*)'", kim).group(1)) == {"all": False, "depts": []}


def test_org_chart_js_safe_and_complete():
    src = (STATIC / "org-chart.js").read_text(encoding="utf-8")
    assert "innerHTML" not in src and "insertAdjacentHTML" not in src         # 글자는 textContent 로만
    assert "CSS.escape" in src and "encodeURIComponent(n.name)" in src
    for path in ("/org/move", "/org/rename", "/org/delete", "/org/create", "/org/head", "/api/org"):
        assert path in src
    for word in ("펼치기 · ", "모두 펼치기", "구성원 보기", "이름 바꾸기", "옮기기", "지우기", "총괄 책임자 정하기"):
        assert word in src
    assert "preventScroll" in src and "view.scrollLeft = keep.l" in src       # 고친 뒤 보던 자리 그대로
    assert "location.reload" not in src
