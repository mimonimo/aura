"""서식 대조(11-15) — 절을 장·번호로 짝짓고, 표 행·열·열 폭 비율과 본문 글자 크기를 잰다."""
from __future__ import annotations


def test_compare_same_and_changed_tables(tmp_path):
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.app import webhwp_templates as wt
    from zzaimy.eval import style_compare as sc
    base = wt.build(wt.GRANT["budget_change"])
    a, _ = eo.table_insert(base, {"head": ["구분", "내용"], "values": [["가", "나"], ["다", "라"]]})
    b, _ = eo.table_insert(base, {"head": ["구분", "내용", "비고"], "values": [["가", "나", ""]]})
    pa, pb = tmp_path / "a.hwpx", tmp_path / "b.hwpx"
    pa.write_bytes(a)
    pb.write_bytes(b)
    same = sc.compare(sc.read(pa), sc.read(pa))["summary"]
    assert same["table_shape_match"] == 1.0 and same["col_ratio_l1_mean"] == 0 and same["table_count_diff"] == 0
    diff = sc.compare(sc.read(pa), sc.read(pb))
    assert diff["summary"]["table_shape_match"] < 1.0
    assert "서식 대조" in sc.report_md(diff)


def test_section_keys_carry_chapter():
    from zzaimy.eval import style_compare as sc
    from xml.etree import ElementTree as ET
    from zzaimy.ingest.hwpx_docx import Styles
    mk = lambda t: ET.fromstring(f'<p><run charPrIDRef="0"><t>{t}</t></run></p>')
    secs = sc.sections([mk("1. 대학 현황"), mk("본문"), mk("Ⅰ. 사업추진 목표"), mk("1. 대내·외 여건"), mk("본문 둘")], Styles())
    assert [s.key for s in secs] == ["앞", "현황 1", "Ⅰ", "Ⅰ 1"]
