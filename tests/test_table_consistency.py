"""표 사이 같은 값 맞추기 — 한 표에만 「확인 필요」로 남은 같은 행·열 칸을 문서 안 값으로(11-09 결과보고서)."""
import io
import re
import zipfile

from zzaimy.app import table_consistency as tc
from zzaimy.ingest import hwpx_fill as hf


def _doc(*tables):
    from zzaimy.app import webhwp_templates as t
    data = t.build(t.GRANT["budget_change"])
    z = zipfile.ZipFile(io.BytesIO(data))
    sec = z.read("Contents/section0.xml").decode()
    # 시험할 표만 남긴다 — 양식에 원래 든 표는 빼고 새 표만 붙인다
    sec = re.sub(r"<hp:tbl\b.*?</hp:tbl>", "", sec, flags=re.S)
    px = "".join(hf.table_xml(rows, "0", "0", 42000, "1", "", 900 + i) for i, rows in enumerate(tables))
    sec = sec.replace("</hs:sec>", px + "</hs:sec>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for info in z.infolist():
            out.writestr(info, sec.encode() if info.filename == "Contents/section0.xml" else z.read(info.filename))
    return buf.getvalue()


def _texts(data):
    sec = zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()
    return "".join(re.findall(r"<hp:t>([^<]*)</hp:t>", sec))


SCHEDULE = [["프로그램명", "운영 일시", "장소", "참여 인원"],
            ["고객만족 서비스 관리사 특강", "2018. 10. 06.(토)", "[확인 필요]", "29명(이수)"],
            ["NCS기반 2급소방안전관리자 특강", "2018. 11. 03.(토)", "미래관 251호", "66명(수료)"]]
RESULT = [["프로그램", "운영 장소", "주요 성과", "비고"],
          ["고객만족 서비스 관리사 특강", "협동관 컨벤션홀", "자격증 취득 교육", "-"],
          ["2급 소방안전관리자 특강", "[확인 필요]", "NCS 기반 교육", "-"]]


def test_fills_placeholder_from_same_row_and_column_elsewhere():
    data, filled, left = tc.cross_fill(_doc(SCHEDULE, RESULT))
    t = _texts(data)
    assert "협동관 컨벤션홀" in t and t.count("협동관 컨벤션홀") == 2       # 일정 표의 빈 장소가 채워졌다
    assert t.count("미래관 251호") == 2                                   # 「2급 소방안전관리자 특강」 은 「NCS기반 2급소방…」 과 같은 행
    assert "확인 필요" not in t
    assert len(filled) == 2 and not left


def test_leaves_cell_when_values_disagree():
    other = [["프로그램", "장소", "성과", "비고"], ["고객만족 서비스 관리사 특강", "본관 101호", "-", "-"]]
    data, filled, left = tc.cross_fill(_doc(SCHEDULE[:2], RESULT[:2], other))
    assert not filled and left and "여럿" in left[0]
    assert "확인 필요" in _texts(data)


def test_no_change_without_placeholders():
    raw = _doc(RESULT[:2])
    data, filled, left = tc.cross_fill(raw)
    assert data == raw and not filled and not left
