"""글을 써 넣는 표의 쪽 나눔(11-11) — 칸이 쪽보다 길어지면 넘친 글이 잘려 안 보이던 것."""
import io
import zipfile

from zzaimy.app import webhwp_write as ww


def _field(name):
    return (f'<hp:ctrl><hp:fieldBegin id="1" type="CLICK_HERE" name="{name}" editable="1"><hp:parameters/></hp:fieldBegin></hp:ctrl>'
            '<hp:t>x</hp:t><hp:ctrl><hp:fieldEnd beginIDRef="1" fieldid="1"/></hp:ctrl>')


def _tbl(pb, inner):
    return (f'<hp:tbl id="9" pageBreak="{pb}" repeatHeader="0" rowCnt="1" colCnt="1">'
            f'<hp:tr><hp:tc><hp:subList><hp:p id="2"><hp:run>{inner}</hp:run></hp:p></hp:subList></hp:tc></hp:tr></hp:tbl>')


def _hwpx(xml):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", f'<hs:sec xmlns:hs="s" xmlns:hp="p">{xml}</hs:sec>')
    return buf.getvalue()


def _sec(data):
    return zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()


def test_field_table_becomes_table_split():
    x, n = ww._split_tables_xml(_tbl("NONE", _field("1. 사업 개요")) + _tbl("CELL", _field("주관기관")))
    assert n == 2 and x.count('pageBreak="TABLE"') == 2


def test_para_only_short_table_untouched():
    x, n = ww._split_tables_xml(_tbl("CELL", _field("문단:3")))
    assert n == 0 and 'pageBreak="CELL"' in x


def test_long_cell_without_field_split():
    x, n = ww._split_tables_xml(_tbl("CELL", "<hp:t>" + "가" * 600 + "</hp:t>"))
    assert n == 1 and 'pageBreak="TABLE"' in x


def test_already_table_and_plain_table_untouched():
    x, n = ww._split_tables_xml(_tbl("TABLE", _field("칸")) + _tbl("NONE", "<hp:t>짧은 글</hp:t>"))
    assert n == 0


def test_nested_inner_field_marks_both():
    inner = _tbl("CELL", _field("칸"))
    x, n = ww._split_tables_xml(_tbl("NONE", inner))
    assert n == 2 and 'pageBreak="NONE"' not in x and 'pageBreak="CELL"' not in x


def test_split_tables_bytes_roundtrip():
    data = _hwpx(_tbl("NONE", _field("칸")))
    new, n = ww.split_tables(data)
    assert n == 1 and 'pageBreak="TABLE"' in _sec(new)
    same, k = ww.split_tables(new)
    assert k == 0 and same == new


def test_new_table_page_break_by_size():
    from zzaimy.ingest import hwpx_fill as hf
    short = hf.table_xml([["구분", "내용"], ["가", "짧은 글"]], "0", "0", 42000, "1", "", 901)
    long_rows = hf.table_xml([["구분", "내용"]] + [[str(i), "값"] for i in range(15)], "0", "0", 42000, "1", "", 902)
    long_cell = hf.table_xml([["구분", "내용"], ["가", "글" * 600]], "0", "0", 42000, "1", "", 903)
    assert 'pageBreak="NONE"' in short and 'pageBreak="CELL"' in long_rows and 'pageBreak="TABLE"' in long_cell
