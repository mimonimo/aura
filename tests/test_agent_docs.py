"""에이전트 문서와 코드가 어긋나지 않는지 — 도구 표와 지시문 위치 목록을 코드에서 다시 센다."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW = (ROOT / "docs" / "document-work-flow.md").read_text(encoding="utf-8")


def _code_tools() -> set[str]:
    main = (ROOT / "src" / "zzaimy" / "app" / "main.py").read_text(encoding="utf-8")
    names = set(re.findall(r'doc_agent\.Tool\(\s*"([a-z_]+)"', main))
    if "_weo.doc_tools(" in main:                    # 기안기 편집 도구는 목록(webhwp_edit_ops.TOOLS)으로 한 번에 잇는다
        from zzaimy.app import webhwp_edit_ops
        names |= {t["name"] for t in webhwp_edit_ops.TOOLS}
    return names


def _doc_tools() -> set[str]:
    """「| 도구 | 하는 일 | 실행 |」 표 첫 칸의 도구 이름."""
    table = FLOW.split("| 도구 | 하는 일 |", 1)[1].split("\n\n", 1)[0]
    names: set[str] = set()
    for line in table.splitlines():
        m = re.match(r"\|\s*([a-z_ /·]+?)\s*\|", line)
        if m:
            names.update(n for n in re.split(r"[\s/·]+", m.group(1)) if n)
    return names


def test_tool_table_matches_code():
    code, doc = _code_tools(), _doc_tools()
    assert code, "main.py 에서 도구 등록을 찾지 못함"
    assert code - doc == set(), f"문서 도구 표에 없는 도구: {sorted(code - doc)}"
    assert doc - code == set(), f"코드에 없는 도구가 문서 표에 있음: {sorted(doc - code)}"


def test_prompt_locations_exist():
    sec = FLOW.split("에이전트 지시문 위치", 1)
    assert len(sec) == 2, "문서에 「에이전트 지시문 위치」 절이 없음"
    paths = re.findall(r"`((?:app|generate)/[a-z_]+\.py)`", sec[1].split("\n## ", 1)[0])
    assert paths
    for p in paths:
        assert (ROOT / "src" / "zzaimy" / p).exists(), p
