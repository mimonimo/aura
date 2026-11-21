"""워드에서 바꾼 양식 — 앞 표가 다음 제목 문단에 붙어 들어간 꼴을 나눠 제목이 절로 잡히는지."""
import io
import zipfile

from zzaimy.app import webhwp_fields as F

TBL = ('<hp:tbl id="1" textWrap="TOP_AND_BOTTOM" rowCnt="1" colCnt="1">'
       '<hp:pos treatAsChar="0" vertRelTo="PARA" horzRelTo="COLUMN" vertOffset="0" horzOffset="0"/>'
       '<hp:tr><hp:tc><hp:subList><hp:p id="0" paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="1"><hp:t>값</hp:t></hp:run>'
       '<hp:linesegarray><hp:lineseg textpos="0"/></hp:linesegarray></hp:p></hp:subList></hp:tc></hp:tr></hp:tbl>')


def _doc(body: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", f'<hs:sec xmlns:hs="s" xmlns:hp="p">{body}</hs:sec>')
    return buf.getvalue()


def _sec(d: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(d)).read("Contents/section0.xml").decode()


def test_heading_after_anchored_table_is_split():
    body = ('<hp:p id="1" paraPrIDRef="2"><hp:run charPrIDRef="1"><hp:t>1. 개요</hp:t></hp:run></hp:p>'
            f'<hp:p id="2" paraPrIDRef="3" styleIDRef="5"><hp:run charPrIDRef="1">{TBL}</hp:run>'
            '<hp:run charPrIDRef="7"><hp:t>2. 실적</hp:t></hp:run><hp:linesegarray><hp:lineseg/></hp:linesegarray></hp:p>'
            '<hp:p id="3"><hp:run charPrIDRef="1"/></hp:p>')
    d, n = F.split_lead_tables(_doc(body))
    assert n == 1
    x = _sec(d)
    assert x.count("<hp:tbl") == 1 and "<hp:t>값</hp:t></hp:run><hp:linesegarray>" in x   # 칸 안 배치는 그대로
    assert '<hp:p id="2" paraPrIDRef="3" styleIDRef="5"><hp:run charPrIDRef="7"><hp:t>2. 실적</hp:t>' in x
    names = [f["name"] for f in F.fieldize(d)[1] if f["kind"] == "절"]
    assert "절:2. 실적" in names


def test_text_before_table_or_floating_table_untouched():
    before = f'<hp:p id="2"><hp:run charPrIDRef="1"><hp:t>앞 글</hp:t>{TBL}</hp:run><hp:run><hp:t>뒤</hp:t></hp:run></hp:p>'
    floating = f'<hp:p id="2"><hp:run charPrIDRef="1">{TBL.replace("vertOffset=\"0\"", "vertOffset=\"500\"")}</hp:run><hp:run><hp:t>뒤</hp:t></hp:run></hp:p>'
    only = f'<hp:p id="2"><hp:run charPrIDRef="1">{TBL}</hp:run></hp:p>'
    for b in (before, floating, only):
        d0 = _doc(b)
        assert F.split_lead_tables(d0) == (d0, 0)
