"""새 표 열 너비 — 짧은 값 열(「목표 80%」)도 끊으면 안 되는 낱말이 한 줄에 들어갈 폭은 지킨다(11-09 결과보고서 실적 표)."""
import re

from zzaimy.ingest import hwpx_fill as hf


def _widths(xml: str) -> list[int]:
    first_row = xml.split("</hp:tr>")[0]
    return [int(w) for w in re.findall(r'<hp:cellSz width="(\d+)"', first_row)]


def test_short_value_column_keeps_room_for_its_token():
    rows = [["핵심성과지표", "목표", "실적", "비고"],
            ["취업률", "80%", "75%(자체조사)", "2023.2 졸업생 취업인원 157명(11월 통계) 기준으로 산출한 값이며 세부 내역은 붙임 참조"]]
    xml = hf.table_xml(rows, "0", "0", 42000, "1", "", 9, char_hu=1100)
    ws = _widths(xml)
    assert len(ws) == 4 and abs(sum(ws) - 42000) < 50
    assert ws[1] >= int(2 * 1100 * 1.05) + 1300          # 「목표」·「80%」 가 세로로 끊기지 않을 폭
    assert ws[0] >= int(6 * 1100 * 1.05) + 1300          # 「핵심성과지표」 한 줄
    assert ws[3] > ws[1]                                   # 긴 글 열은 여전히 넓다


def test_fit_widths_scales_when_minimums_exceed_table():
    ws = hf._fit_widths([10000, 10000], [9000, 9000], 12000)
    assert sum(ws) <= 12000 and ws[0] == ws[1]
