"""표 칸 글 정렬 — 긴 글 칸도 왼쪽(양쪽·배분이면 좁은 칸에서 줄이 꺾일 때 「N C S 기 반」처럼 벌어진다, 11-09 결과보고서)."""
import io
import re
import zipfile

from zzaimy.app import webhwp_style as st
from zzaimy.ingest import hwpx_fill as hf


def _doc_with_table(rows):
    from zzaimy.app import webhwp_templates as t
    data = t.build(t.GRANT["budget_change"])
    z = zipfile.ZipFile(io.BytesIO(data))
    sec = z.read("Contents/section0.xml").decode()
    hdr = z.read("Contents/header.xml").decode()
    jpp = re.search(r'<hh:paraPr\b[^>]*\bid="(\d+)"[^>]*>(?:(?!</hh:paraPr>).)*<hh:align horizontal="JUSTIFY"', hdr, re.S).group(1)
    px = hf.table_xml(rows, jpp, "0", 42000, "1", "", 777).replace('<hp:p id="0"', '<hp:p id="424242"', 1)
    sec = sec.replace("</hs:sec>", px + "</hs:sec>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for info in z.infolist():
            body = sec.encode() if info.filename == "Contents/section0.xml" else z.read(info.filename)
            out.writestr(info, body)
    return buf.getvalue()


def _cell_aligns(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    hdr = z.read("Contents/header.xml").decode()
    sec = z.read("Contents/section0.xml").decode()
    align = {m.group(1): m.group(2) for m in re.finditer(
        r'<hh:paraPr\b[^>]*\bid="(\d+)"[^>]*>(?:(?!</hh:paraPr>).)*?<hh:align horizontal="([A-Z_]+)"', hdr, re.S)}
    tbl = sec[sec.index('<hp:p id="424242"'):]
    out = {}
    for tc in re.findall(r"<hp:tc\b.*?</hp:tc>", tbl, re.S):
        text = "".join(re.findall(r"<hp:t>([^<]*)</hp:t>", tc))
        pp = re.search(r'paraPrIDRef="(\d+)"', tc).group(1)
        out[text] = align.get(pp)
    return out


def test_long_text_cells_are_left_not_justified():
    rows = [["프로그램명", "운영 일시", "장소", "주요 내용"],
            ["NCS기반 2급소방안전관리자 특강", "2018. 11. 03.(토) ~ 11. 11.(일)", "[확인 필요]", "소방개론, 소방관계법령 등 NCS 기반 교육"]]
    data = st.apply(_doc_with_table(rows), only_paras={"424242"})
    al = _cell_aligns(data)
    assert al["NCS기반 2급소방안전관리자 특강"] == "LEFT"
    assert al["소방개론, 소방관계법령 등 NCS 기반 교육"] == "LEFT"
    assert al["프로그램명"] == "CENTER"                         # 머리 행은 가운데
    assert not any(v in ("JUSTIFY", "DISTRIBUTE") for v in al.values())
