"""화면 점검(좁은 칸·긴 이름·초점 고리·초기 설정 줄) — 공통 CSS·틀이 그 꼴을 지키는지 본다. 화면 값은 모두 합성."""
from __future__ import annotations

import re
from pathlib import Path

from tests.test_dev_pages import client  # noqa: F401  (고정물)
from zzaimy.app import setup_check

STATIC = Path(__file__).resolve().parents[1] / "src" / "zzaimy" / "app" / "static"
TEMPLATES = STATIC.parent / "templates"


def test_library_filters_follow_pane_width_not_window():
    """라이브러리 거르기 칸은 창 폭이 아니라 문서 칸 폭으로 두 칸 격자가 된다(노트북 폭·도우미 패널을 열어도 칸이 낱낱이 꺾이지 않게)."""
    page = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    assert "container:libmain / inline-size" in page
    block = page.split("@container libmain (max-width: 600px)", 1)[1].split("\n  }\n", 1)[0]
    assert ".intake-toolbar.lib-filters { display:grid;" in block
    assert ".lib-filters .lib-more > *" in block            # 더 거르기 고르기도 칸을 꽉 채운다(max-width 상한을 이긴다)
    assert "@media (max-width: 600px) {\n    .lib-uses" not in page   # 창 폭 규칙과 두 벌로 갈리지 않는다


def test_org_tree_names_wrap_two_lines():
    css = (STATIC / "org-chart.css").read_text(encoding="utf-8")
    rule = re.search(r"^\.om-name \{([^}]*)\}", css, re.M).group(1)
    assert "-webkit-line-clamp: 2" in rule and "white-space: nowrap" not in rule


def test_narrow_control_bar_selects_share_rows():
    shell = (STATIC / "workspace-shell.css").read_text(encoding="utf-8")
    assert re.search(r"@media \(max-width:600px\) \{\s*\.control-bar > :is\(input\[type=text\], input\[type=search\]\) \{ flex-basis:100%; \}\s*"
                     r"\.control-bar > select \{ flex:1 1 calc\(50% - 4px\); \}", shell)
    assert "@media (max-width: 600px) { .om .control-bar > select { flex: 1 1 calc(50% - 4px); } }" in (STATIC / "org-chart.css").read_text(encoding="utf-8")


def test_finder_name_field_has_one_focus_ring_and_quiet_note():
    css = (STATIC / "aura-finder.css").read_text(encoding="utf-8")
    assert ".aura-finder .af-nm input:focus-visible" in css and "{outline:0}" in css
    assert ".af-msg{font-size:12px;color:#6b778a}" in css      # 안내는 회색, 잘못만 빨강(.af-msg.bad)
    assert "grid-template-rows:auto minmax(0,1fr)" in css       # 좁은 폭에서 위치 목록 아래 빈 띠가 생기지 않게


def test_setup_rows_keep_link_at_end_and_no_em_dash(client):
    page = client.get("/dev/tools").text
    rows = page.split('id="setupBox"', 1)[1].split("</div></div>", 1)[0]
    assert rows.count('class="setup-txt"') == rows.count('class="setup-row"') > 0
    assert "—" not in rows
    assert all("—" not in str(r["detail"]) for r in setup_check.items(client.app.state))
