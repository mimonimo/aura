"""46차 결과보고서 시험(11-29)에서 드러난 것 — 끝 다시 읽기에서만 보이던 표끼리 합 어긋남을 쓴 자리에서."""
from zzaimy.app import doc_review as d


def _tbl(rows):
    tr = "".join("<hp:tr>" + "".join(f"<hp:tc><hp:subList><hp:p><hp:run><hp:t>{c}</hp:t></hp:run></hp:p></hp:subList></hp:tc>" for c in r) + "</hp:tr>"
                 for r in rows)
    return f"<hp:tbl>{tr}</hp:tbl>"


def test_table_total_issue_names_written_table(monkeypatch):
    """B 기본·짧게: 분기 표에 운영위원회 참석자 15명이 섞여 315 — 과제별 표 300 과 다른 것을 분기 표를 쓴 걸음에 알린다."""
    a = [["추진 과제", "운영 횟수", "참석자(명)"], ["강좌", "5", "190"], ["네트워크", "4", "83"], ["지원", "3", "27"]]
    b = [["분기", "주요 활동", "참석자(명)"], ["2분기", "x", "88"], ["3분기", "y", "90"], ["4분기", "z", "137"]]
    c = [["세목", "집행액(천원)"], ["홍보비", "9,800"], ["운영비", "22,700"]]
    monkeypatch.setattr(d, "_sections", lambda data: [("절:Ⅱ. 과제별", _tbl(a)), ("절:Ⅲ. 분기별", _tbl(b) + _tbl(c))])
    assert d._rows(d._TBL.findall(_tbl(a))[0])[1] == ["강좌", "5", "190"]
    out = d.table_total_issues(b"", 2)
    assert len(out) == 1 and "300" in out[0] and "315" in out[0] and "표1" in out[0]
    out_a = d.table_total_issues(b"", 1)
    assert len(out_a) == 1 and "표1(" in out_a[0]
    assert d.table_total_issues(b"", 3) == [] and d.table_total_issues(b"", 9) == []
