"""서식 견본 블록 늘리기(webhwp_blocks, 11-11)."""
import re

import pytest

from zzaimy.app import webhwp_blocks as wb
from zzaimy.app import webhwp_fields as wf


def _p(pid, text):
    return f'<hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p>'


def _fp(pid, fid, name, body=""):
    return (f'<hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="0">'
            f'<hp:ctrl><hp:fieldBegin id="{fid}" type="CLICK_HERE" name="{name}" editable="1"><hp:parameters/></hp:fieldBegin></hp:ctrl>'
            f'<hp:t>{body}</hp:t><hp:ctrl><hp:fieldEnd beginIDRef="{fid}" fieldid="{fid}"/></hp:ctrl></hp:run></hp:p>')


def _sec():
    parts = [_p(1, "2. 집중 육성 계획"), _fp(2, 100, "절:2. 집중 육성 계획"),
             _p(3, "2.1. OOO(과제명)"), _fp(4, 101, "절:2.1. OOO(과제명)", "과제 글"),
             _p(5, "2.1.1. 추진배경 및 목표"), _fp(6, 102, "절:2.1.1. 추진배경 및 목표"),
             _p(7, "1) OOO(프로그램명)"), _fp(8, 103, "절:1) OOO(프로그램명)"),
             _p(9, "(1) 5개년 추진계획"), _fp(10, 104, "절:(1) 5개년 추진계획"),
             _p(11, "3. 취업 지원 계획"), _fp(12, 105, "절:3. 취업 지원 계획")]
    return '<hs:sec xmlns:hs="s" xmlns:hp="p">' + "".join(parts) + "</hs:sec>"


def _names(xml):
    return [m.group(1) for m in re.finditer(r'type="CLICK_HERE" name="([^"]*)"', xml)]


def _heads(xml):
    return [h[3] for h in wb._heading_paras(xml)]


def test_level():
    assert wb.level("2. 계획") == (0, 1) and wb.level("2.1. 과제") == (0, 2) and wb.level("2.1.1 배경") == (0, 3)
    assert wb.level("1) 프로그램") == (1, 0) and wb.level("(1) 5개년") == (2, 0) and wb.level("추진 일정") is None


def test_repeat_task_block_renumbers_and_names():
    xml = _sec()
    ids = wf._Ids([xml])
    x2, tops = wb.repeat_xml(xml, "절:2.1. OOO(과제명)", ["교육모델 혁신", "지산학협력", "제도 혁신"], ids, set(_names(xml)))
    assert tops == ["2.1. 교육모델 혁신", "2.2. 지산학협력", "2.3. 제도 혁신"]
    hs = _heads(x2)
    assert hs[:2] == ["2. 집중 육성 계획", "2.1. 교육모델 혁신"]
    assert "2.2.1. 추진배경 및 목표" in hs and "2.3.1. 추진배경 및 목표" in hs and hs[-1] == "3. 취업 지원 계획"
    names = _names(x2)
    assert "절:2.2. 지산학협력" in names and "절:2.3.1. 추진배경 및 목표" in names
    assert len(names) == len(set(names))                                  # 필드 이름이 겹치지 않는다
    assert names.count("절:3. 취업 지원 계획") == 1
    # 복사본 본문 칸은 비고 원래 블록 글은 남는다
    assert x2.count("과제 글") == 1


def test_repeat_ids_unique_and_field_pairs_match():
    xml = _sec()
    x2, _ = wb.repeat_xml(xml, "절:2.1. OOO(과제명)", ["가", "나"], wf._Ids([xml]), set(_names(xml)))
    pids = re.findall(r'<hp:p id="(\d+)"', x2)
    assert len(pids) == len(set(pids))
    begins = re.findall(r'<hp:fieldBegin id="(\d+)"', x2)
    ends = re.findall(r'beginIDRef="(\d+)"', x2)
    assert sorted(begins) == sorted(ends) and len(begins) == len(set(begins))


def test_repeat_program_block_by_field_name():
    xml = _sec()
    taken = set(_names(xml))
    x2, _ = wb.repeat_xml(xml, "절:2.1. OOO(과제명)", ["가", "나"], wf._Ids([xml]), taken)
    prog = [n for n in _names(x2) if "OOO(프로그램명)" in n]
    assert len(prog) == 2
    x3, tops = wb.repeat_xml(x2, prog[1], ["현장실습", "공동 프로젝트"], wf._Ids([x2]), set(_names(x2)))
    assert tops == ["1) 현장실습", "2) 공동 프로젝트"]
    hs = _heads(x3)
    i = hs.index("2.2. 나")
    assert hs[i:i + 6] == ["2.2. 나", "2.2.1. 추진배경 및 목표", "1) 현장실습", "(1) 5개년 추진계획", "2) 공동 프로젝트", "(1) 5개년 추진계획"]
    assert "1) OOO(프로그램명)" in hs                                  # 다른 과제의 견본 프로그램은 그대로


def test_missing_heading_raises():
    xml = _sec()
    with pytest.raises(wb.BlockError):
        wb.repeat_xml(xml, "절:9.9. 없는 과제", ["가"], wf._Ids([xml]), set())


def test_materials_excludes_dynamic_form_sources():
    from zzaimy.app import drafting
    box = {"ids": set()}
    m = drafting.Materials(None, None, {1}, None, None, [], form_sources=lambda: box["ids"])
    assert m.form_source_ids == {1}
    box["ids"] = {7, 8}                                   # 요청 도중 작업본이 생기면 그 원본도 뺀다
    assert m.form_source_ids == {1, 7, 8}


def test_repeat_tree_tasks_and_programs_in_one_call():
    xml = _sec()
    x2, made = wb.repeat_tree(xml, "절:2.1. OOO(과제명)",
                              [{"name": "가 과제", "children": ["가1", "가2"]}, {"name": "나 과제", "children": ["나1", "나2", "나3"]}],
                              wf._Ids([xml]), set(_names(xml)))
    hs = _heads(x2)
    assert hs == ["2. 집중 육성 계획", "2.1. 가 과제", "2.1.1. 추진배경 및 목표", "1) 가1", "(1) 5개년 추진계획", "2) 가2", "(1) 5개년 추진계획",
                  "2.2. 나 과제", "2.2.1. 추진배경 및 목표", "1) 나1", "(1) 5개년 추진계획", "2) 나2", "(1) 5개년 추진계획",
                  "3) 나3", "(1) 5개년 추진계획", "3. 취업 지원 계획"]
    names = _names(x2)
    assert len(names) == len(set(names)) and "절:1) 나1" in names and "절:3) 나3" in names
    assert len(made) == 2 + 5


def _label_doc():
    import io, zipfile
    def tc(r, c, text, cs=1, rs=1):
        return (f'<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="1">'
                f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
                f'<hp:p id="{r * 10 + c + 100}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList>'
                f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/><hp:cellSpan colSpan="{cs}" rowSpan="{rs}"/><hp:cellSz width="1000" height="500"/>'
                f'<hp:cellMargin left="0" right="0" top="0" bottom="0"/></hp:tc>')
    rows = [[tc(0, 0, "대 학 명", 2), tc(0, 2, "OO대학교", 2)],
            [tc(1, 0, "총괄책임자", 1, 2), tc(1, 1, "성 명"), tc(1, 2, ""), tc(1, 3, "")],
            [tc(2, 1, "소속(직위)"), tc(2, 2, ""), tc(2, 3, "")],
            [tc(3, 0, "실무담당자"), tc(3, 1, "성 명"), tc(3, 2, ""), tc(3, 3, "")],
            [tc(4, 0, "URL", 2), tc(4, 2, "http://www.", 2)]]
    tbl = ('<hp:tbl id="9" zOrder="0" numberingType="TABLE" pageBreak="CELL" repeatHeader="0" rowCnt="5" colCnt="4" cellSpacing="0" borderFillIDRef="1" noAdjust="0">'
           '<hp:sz width="4000" widthRelTo="ABSOLUTE" height="2500" heightRelTo="ABSOLUTE" protect="0"/>'
           + "".join("<hp:tr>" + "".join(r) + "</hp:tr>" for r in rows) + "</hp:tbl>")
    sec = ('<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
           f'<hp:p id="1" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0">{tbl}<hp:t/></hp:run></hp:p></hs:sec>')
    hdr = '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"><hh:refList><hh:borderFills itemCnt="1"><hh:borderFill id="1"/></hh:borderFills></hh:refList></hh:head>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/header.xml", hdr)
        z.writestr("Contents/section0.xml", sec)
    return buf.getvalue()


def test_fill_labels_next_to_label_cells():
    from zzaimy.app import webhwp_edit_ops as eo
    new, done, why = eo.fill_labels(_label_doc(), {"대학명": "영남이공대학교", "총괄책임자 성명": "박민규", "실무담당자 성명": "박은호",
                                                    "URL": "http://www.ync.ac.kr", "성명": "모호함", "없는 칸": "x"})
    assert set(done) == {"대학명", "총괄책임자 성명", "실무담당자 성명", "URL"}
    assert "성명" in why and "곳" in why["성명"] and "없는 칸" in why
    view = eo.table_view(new, {"table": 1})
    assert "영남이공대학교" in view and "박민규" in view and "박은호" in view and "ync.ac.kr" in view and "OO대학교" not in view
    assert view.index("박민규") < view.index("박은호")
