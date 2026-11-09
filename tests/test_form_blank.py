"""지난 문서를 양식으로 쓸 때 옛 값 비우기(form_blank) — 결과보고서 초안에 지난 해 예결산 수치가 남던 것(11-09)."""
from __future__ import annotations

import io
import re
import zipfile

from zzaimy.app import form_blank as fb


def _cell(row: int, text: str) -> str:
    return (f'<hp:tc><hp:cellAddr colAddr="0" rowAddr="{row}"/><hp:subList><hp:p id="1"><hp:run charPrIDRef="0">'
            f'<hp:t>{text}</hp:t></hp:run><hp:linesegarray><hp:lineseg/></hp:linesegarray></hp:p></hp:subList></hp:tc>')


def _doc(rows: list[list[str]]) -> bytes:
    body = "".join("<hp:tr>" + "".join(_cell(r, t) for t in row) + "</hp:tr>" for r, row in enumerate(rows))
    sec = f'<hs:sec><hp:p id="0"><hp:run><hp:tbl>{body}</hp:tbl></hp:run></hp:p><hp:p id="9"><hp:run><hp:t>18. 예산 운영의 적절성</hp:t></hp:run></hp:p></hs:sec>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/header.xml", "<hh:head/>")
        z.writestr("Contents/section0.xml", sec)
    return out.getvalue()


def _texts(data: bytes) -> list[str]:
    x = zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()
    return re.findall(r"<hp:t>([^<]*)</hp:t>", x)


def test_is_value_rules():
    assert fb.is_value("2,100") and fb.is_value("60.8%") and fb.is_value("’22.4.1.") and fb.is_value("157,159천원")
    assert fb.is_value("◦ 전담직원 인건비: 157,159\n◦ 시간외근무수당 : 3,566")
    assert not fb.is_value("인건비(25% 이내)") and not fb.is_value("구 분") and not fb.is_value("2차년도\n(’22.3～’23.2)")
    assert not fb.is_value("00%") and not fb.is_value("0,000") and not fb.is_value("")       # 빈 양식의 예시 글자


def test_filled_report_values_are_blanked_but_structure_stays():
    rows = [["구 분", "예산(A)", "집행(B)"]] + [[f"항목{i}", str(100 + i), f"{90 + i}.5"] for i in range(25)]
    data = _doc(rows)
    assert fb.looks_filled(data)
    out, n = fb.blank_values(data)
    texts = _texts(out)
    assert n == 50
    assert "구 분" in texts and "예산(A)" in texts and "항목3" in texts and "18. 예산 운영의 적절성" in texts   # 머리·라벨·표 밖 글은 그대로
    assert "103" not in texts and "93.5" not in texts                                                        # 옛 값은 비움
    seg = lambda d: zipfile.ZipFile(io.BytesIO(d)).read("Contents/section0.xml").decode().count("<hp:linesegarray>")
    assert seg(out) == seg(data) - 50                                     # 비운 칸의 옛 줄 배치 정보는 떼어 한글이 다시 잡게
    zipfile.ZipFile(io.BytesIO(out)).testzip()


def test_blank_form_with_placeholders_is_not_treated_as_filled():
    rows = [["지표명", "목표", "실적"]] + [[f"지표{i}", "00%", "00건 / 00명"] for i in range(30)]
    data = _doc(rows)
    assert not fb.looks_filled(data)
    out, n = fb.blank_values(data)
    assert n == 0 and out == data


def test_past_doc_form_detects_past_folder(tmp_path):
    from zzaimy.app.webhwp_agent import past_doc_form

    class _Db:
        def get_setting(self, key, default=""):
            return default
    assert past_doc_form(_Db(), {"id": 1, "path": "/x/기안기/공통/지난 사업 문서/LINC/결과보고서/a.hwpx"})
    assert not past_doc_form(_Db(), {"id": 2, "path": "/x/기안기/공통/공통 양식/사업계획서 공통 양식.hwpx"})
