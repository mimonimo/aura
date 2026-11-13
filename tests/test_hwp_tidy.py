"""한글 작업본 정돈(11-12 특성화 재현 — 「지저분하다」): 「〃」 세로 합치기, 값 글자 검정, 넓은 도식 접기, 새 자료 표 머리줄 모양."""
import io
import re
import zipfile

from zzaimy.app import webhwp_edit_ops as eo
from zzaimy.app import webhwp_style as ws
from zzaimy.app import webhwp_templates as wt
from zzaimy.export import house_style as hs
from zzaimy.ingest import hwpx_fill as hf


def _tbl(px: str) -> str:
    return re.search(r"<hp:tbl\b.*</hp:tbl>", px, re.S).group(0)


def test_table_xml_merges_ditto_cells_vertically():
    rows = [["구분", "학과", "교과목"], ["기초 공통", "기계", "CAD"], ["〃", "〃", "도면"], ["〃", "전기", "회로"], ["전공", "〃", "설계"]]
    t = _tbl(hf.table_xml(rows, "0", "0", 40000, "1", "", 9))
    assert "〃" not in t
    spans = re.findall(r'<hp:cellAddr colAddr="(\d+)" rowAddr="(\d+)"/><hp:cellSpan colSpan="1" rowSpan="(\d+)"/>', t)
    got = {(int(c), int(r)): int(s) for c, r, s in spans}
    assert got[(0, 1)] == 3 and got[(1, 1)] == 2 and got[(1, 3)] == 2       # 기초 공통 3행, 기계 2행, 전기 2행(전공 행의 〃 포함)
    assert (0, 2) not in got and (1, 2) not in got and (0, 4) in got           # 덮인 칸은 없고 「전공」 은 새 칸
    assert len(got) == 15 - 4


def test_set_cells_merges_ditto_into_cell_above():
    d, _ = eo.table_insert(wt.build(wt.GRANT["budget_change"]), {"head": ["구분", "교과목"], "values": [["", ""], ["", ""], ["", ""]]})
    t = max(x["no"] for x in eo.list_tables(eo._Doc(d)))
    new, msg = eo.table_edit(d, {"table": t, "op": "set_cells", "start": [2, 1], "values": [["기초 공통", "CAD"], ["〃", "도면"], ["〃", "회로"]]})
    assert "합침" in msg and eo.check_bytes(new) == []
    view = eo.table_view(new, {"table": t})
    assert "〃" not in view and "기초 공통[합침 3×1]" in view


def test_char_black_clones_colored_char_only():
    hdr = ('<hh:charProperties itemCnt="2"><hh:charPr id="0" height="1000" textColor="#000000"><hh:x/></hh:charPr>'
           '<hh:charPr id="1" height="1000" textColor="#0000FF"><hh:x/></hh:charPr></hh:charProperties>')
    H = ws._Header(hdr)
    assert H.char_black("0") == "0"
    b = H.char_black("1")
    assert b not in ("0", "1") or b == "0"                                     # 같은 몸통(검정)이 있으면 그 id
    assert H.char_black("1") == b and 'textColor="#0000FF"' in H.xml


def test_label_fill_value_is_black_not_sample_blue():
    d, _ = eo.table_insert(wt.build(wt.GRANT["budget_change"]), {"head": ["항목", "값"], "values": [["권역", "(우편번호)"]]})
    doc = eo._Doc(d)
    blue = doc.hdr.char_ex("0")
    doc.hdr.xml = re.sub(rf'(<hh:charPr\b[^>]*\bid="{blue}"[^>]*?)\btextColor="#[0-9A-Fa-f]{{6}}"', r'\1textColor="#0000FF"', doc.hdr.xml)
    n = doc.sec_names[0]
    i = doc.secs[n].index("(우편번호)")
    p = doc.secs[n].rfind('charPrIDRef="', 0, i)
    doc.secs[n] = doc.secs[n][:p] + re.sub(r'charPrIDRef="\d+"', f'charPrIDRef="{blue}"', doc.secs[n][p:i], count=1) + doc.secs[n][i:]
    d2 = doc.pack()
    new, done = eo.echo_labels(d2, {"칸:권역": "대구"})
    assert done
    z = zipfile.ZipFile(io.BytesIO(new))
    sec = z.read(n).decode()
    hdr = z.read("Contents/header.xml").decode()
    j = sec.index(">대구<")
    cp = re.findall(r'charPrIDRef="(\d+)"', sec[:j])[-1]
    color = re.search(rf'<hh:charPr\b[^>]*\bid="{cp}"[^>]*>', hdr).group(0)
    assert 'textColor="#0000FF"' not in color


def test_wide_diagram_row_wraps_without_arrows():
    lines = hs.diagram_lines("총장\n" + " → ".join(f"부서{i}" for i in range(12)))
    assert [len(x) for x in lines] == [1, 4, 4, 4]
    assert isinstance(lines[1], hs.Siblings) and lines[1].first and not lines[2].first
    hdr = ws._Header(zipfile.ZipFile(io.BytesIO(wt.build(wt.GRANT["budget_change"]))).read("Contents/header.xml").decode())
    t = ws.diagram_table(lines, hdr, "0", "0", 40000, 7)
    assert "→" not in t and t.count(">↓<") == 1                                # 총장 아래 한 번, 접은 줄 사이에는 없음
    g = hs.diagram_grid(lines)
    assert sum(1 for r in g["rows"] if r["kind"] == "arrow") == 1


def test_short_flow_still_has_arrows():
    lines = hs.diagram_lines("신청 → 심사 → 선정")
    t = ws.diagram_table(lines, ws._Header(zipfile.ZipFile(io.BytesIO(wt.build(wt.GRANT["budget_change"]))).read("Contents/header.xml").decode()),
                         "0", "0", 40000, 7)
    assert t.count(">→<") == 2


def _blue_italic_doc():
    """본문 문단 하나가 파란 기울임(견본 글 꼴)인 문서 — (바이트, 그 문단 id, 그 글자 모양 id)."""
    d = wt.build(wt.GRANT["budget_change"])
    doc = eo._Doc(d)
    cp = doc.hdr.char_ex("0", pt=11)
    doc.hdr.xml = re.sub(rf'(<hh:charPr\b[^>]*\bid="{cp}"[^>]*?)\btextColor="#[0-9A-Fa-f]{{6}}"', r'\1textColor="#0000FF"', doc.hdr.xml)
    doc.hdr.xml = re.sub(rf'(<hh:charPr\b[^>]*\bid="{cp}"[^>]*>)', r'\1<hh:italic/>', doc.hdr.xml, count=1)
    n = doc.sec_names[0]
    pid = "987654"
    para = f'<hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="{cp}"><hp:t>견본 꼴 글</hp:t></hp:run></hp:p>'
    doc.secs[n] = doc.secs[n].replace("</hs:sec>", para + "</hs:sec>")
    return doc.pack(), pid, cp


def test_plain_runs_and_off_profile():
    from zzaimy.app import webhwp_lint as lint
    from zzaimy.app import webhwp_profile as wp
    d, pid, cp = _blue_italic_doc()
    assert wp.off_profile(d, [pid]) and lint.issues(d)["off_style"]
    d2 = wp.plain_runs(d, [pid])
    assert wp.off_profile(d2, [pid]) == []
    z = zipfile.ZipFile(io.BytesIO(d2))
    hdr = z.read("Contents/header.xml").decode()
    new_cp = re.search(rf'<hp:p id="{pid}"[^>]*><hp:run charPrIDRef="(\d+)"', z.read("Contents/section0.xml").decode()).group(1)
    tag = re.search(rf'<hh:charPr\b[^>]*\bid="{new_cp}"[^>]*>(?:(?!</hh:charPr>).)*</hh:charPr>', hdr, re.S).group(0)
    assert new_cp != cp and "#0000FF" not in tag and "<hh:italic" not in tag and 'height="1100"' in tag     # 크기는 그대로


def test_profile_summary_falls_back_to_house():
    from zzaimy.app import webhwp_profile as wp
    p = wp.profile(wt.build(wt.GRANT["budget_change"]))
    assert p["table"]["head_fill"] and p["body"]["pt"]
    assert "서식 규격" in wp.summary(p) and "검정" in wp.summary(p)


def test_last_folded_single_box_is_one_box_wide():
    lines = hs.diagram_lines("총장\n" + " → ".join(f"부서{i}" for i in range(5)))
    assert [len(x) for x in lines] == [1, 4, 1]
    hdr = ws._Header(zipfile.ZipFile(io.BytesIO(wt.build(wt.GRANT["budget_change"]))).read("Contents/header.xml").decode())
    t = ws.diagram_table(lines, hdr, "0", "0", 40000, 7)
    last = t.split("<hp:tr>")[-1]
    assert 'colSpan="7"' not in last.split(">부서4<")[0][-600:]                 # 「부서4」 칸은 온 폭이 아니다


def test_quiet_hints_only_for_filled_sections():
    """다 채운 절(표·그림·칸 값)의 빨간 안내 글만 지운다 — 아직 안 쓴 절은 서식 견본 표가 있어도 안내를 남긴다(11-13 4차)."""
    import io
    import zipfile
    from zzaimy.app import webhwp_write as ww

    def fld(name, fid):
        return (f'<hp:p id="{fid}"><hp:run><hp:ctrl><hp:fieldBegin id="{fid}" type="CLICK_HERE" name="{name}" editable="1"><hp:parameters cnt="3" name="">'
                '<hp:integerParam name="Prop">9</hp:integerParam><hp:stringParam name="Command" xml:space="preserve">Clickhere:set:47:Direction:wstring:5:본문 입력 HelpState:wstring:0:  </hp:stringParam>'
                f'<hp:stringParam name="Direction">본문 입력</hp:stringParam></hp:parameters></hp:fieldBegin></hp:ctrl><hp:t/><hp:ctrl><hp:fieldEnd beginIDRef="{fid}" fieldid="{fid}"/></hp:ctrl></hp:run></hp:p>')
    tbl = '<hp:p id="9"><hp:run><hp:tbl id="1"><hp:tr><hp:tc><hp:subList><hp:p id="8"><hp:run><hp:t>{}</hp:t></hp:run></hp:p></hp:subList></hp:tc></hp:tr></hp:tbl></hp:run></hp:p>'
    xml = '<hs:sec xmlns:hs="s" xmlns:hp="p">' + fld("절:1. 현황", 1) + tbl.format("4,971명") + fld("절:2. 계획", 2) + tbl.format("구분") + "</hs:sec>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", xml)
    new, done = ww.quiet_hints(buf.getvalue(), {"절:1. 현황"})
    assert done == ["절:1. 현황"]
    out = zipfile.ZipFile(io.BytesIO(new)).read("Contents/section0.xml").decode()
    assert out.count('<hp:stringParam name="Direction">본문 입력</hp:stringParam>') == 1        # 안 쓴 절은 그대로
    rest = "Direction:wstring:0: HelpState:wstring:0:  "
    assert f"Clickhere:set:{len(rest)}:{rest}" in out
    assert ww.section_before(buf.getvalue(), "Contents/section0.xml", xml.index("구분")) == "절:2. 계획"
